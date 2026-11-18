"""M6 全链路 e2e：示例任务脚本（m1_river_crossing.json）驱动真战斗——
增援空投（tick 360）→ 玩家 dispatcher 指令交战 → RP 奖励入账 → victory 结局。

ROADMAP M6 验收：示例脚本全事件触发覆盖。截图存 docs/assets/uat_m6/
（dummy 驱动 + 固定 seed，与 M1-M4 uat 同模式）。触发器评估由
battle_service.tick 经 tick_hook 驱动（与 main.run_frame 同一渲染管线）。
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TypeVar

import pygame
import pytest

import pymc2.main as main_module
from pymc2.domain.entities.mech import Faction, Mech, Weapon
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType, TacticalOrder
from pymc2.domain.scripting.mission_script import ScriptContext, ScriptEngine, load_mission_script
from pymc2.presentation.renderer import WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX
from pymc2.services.battle_service import BattleService

SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "assets" / "uat_m6"
EXAMPLE_SCRIPT = Path(__file__).resolve().parents[2] / "data" / "missions" / "m1_river_crossing.json"
MAX_POLL_FRAMES = 6000
UAT_SEED = 7

T = TypeVar("T")


def save_screenshot(screen: pygame.Surface, name: str) -> Path:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / name
    pygame.image.save(screen, str(path))
    assert path.stat().st_size > 0
    return path


def run_until[T](game: main_module.Game, predicate: Callable[[], T], max_frames: int = MAX_POLL_FRAMES) -> T | None:
    """逐帧推进（玩家持续重发 ATTACK 指令）直至谓词满足；超帧返回 None。"""
    for _ in range(max_frames):
        for unit_id in ("px-01", "px-02"):
            mech = game.service.get(unit_id)
            if mech is None or not mech.alive:
                continue
            target = game.service.nearest_enemy(mech)
            if target is not None:
                order = TacticalOrder(
                    order_type=OrderType.ATTACK_OBJECT,
                    unit_ids=(unit_id,),
                    origin=OrderOrigin.PLAYER,
                    target_tile=None,
                    target_unit_id=target.id,
                )
                game.dispatcher.dispatch(order, game.service)
        main_module.run_frame(game, 1 / 60)
        result = predicate()
        if result:
            return result
    return None


@pytest.fixture()
def screen() -> Iterator[pygame.Surface]:
    pygame.init()
    yield pygame.display.set_mode((WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX))
    pygame.quit()


def test_m6_scripted_battle_slice(screen: pygame.Surface) -> None:
    script = load_mission_script(EXAMPLE_SCRIPT)
    assert script.script_id == "m1_river_crossing"
    saved: list[Path] = []

    # --- 战局搭建：玩家双远程炮 vs 敌桥头单机，脚本引擎经 tick_hook 挂载 ---
    battle = BattleService(rng=random.Random(UAT_SEED))
    battle.add_mech(Mech("px-01", Faction.PLAYER, 208.0, 336.0, weapon=Weapon("ppc", 25, 420.0, 2.0)))
    battle.add_mech(Mech("px-02", Faction.PLAYER, 272.0, 336.0, weapon=Weapon("ppc", 25, 420.0, 2.0)))
    battle.add_mech(Mech("ex-01", Faction.ENEMY, 592.0, 336.0))
    context = ScriptContext(battle=battle)
    engine = ScriptEngine(script, context)
    engine.attach(battle)  # battle_service.tick 驱动触发器评估
    game = main_module.create_game(screen, service=battle)
    for unit_id, tile in (("px-01", (15, 10)), ("px-02", (15, 11))):
        game.dispatcher.dispatch(
            TacticalOrder(OrderType.MOVE_TO_POINT, (unit_id,), OrderOrigin.PLAYER, tile, None), battle
        )  # 先机动至渡口（增援 tile 超出出生点射程，必须推进后才能全歼）

    # --- 开局帧：开场简报已触发（tick_reached），画面可见 ---
    main_module.run_frame(game, 1 / 60)
    assert "任务开始：渡河突破口——清除桥头敌军。" in context.messages
    assert engine.fire_count("intro_briefing") == 1
    saved.append(save_screenshot(game.screen, "01_briefing_start.png"))

    # --- 增援空投（tick 360）：条件 tick_reached → 动作 spawn_reinforcement ---
    reinforced = run_until(game, lambda: "ex-r1" in context.spawned_unit_ids)
    assert reinforced is not None
    ex_r1 = battle.get("ex-r1")
    assert ex_r1 is not None and ex_r1.alive
    assert "敌军增援从东侧抵达！" in context.messages
    saved.append(save_screenshot(game.screen, "02_reinforcement_arrived.png"))

    # --- 玩家交战至敌全灭：首杀 grant_rp + victory 结局（enemy_destroyed 全灭）---
    won = run_until(
        game,
        lambda: (
            context.outcome == "victory"
            and not any(m.alive for m in battle.mechs.values() if m.faction is Faction.ENEMY)
        ),
    )
    assert won is True
    assert not any(m.alive for m in battle.mechs.values() if m.faction is Faction.ENEMY)
    assert context.granted_rp >= 15  # 首杀奖励入账（enemy_destroyed count=1）
    assert "首杀入账：回收残骸折算 15 RP。" in context.messages
    assert engine.fire_count("mission_victory") == 1  # once 门控：恰好结算一次
    saved.append(save_screenshot(game.screen, "03_victory_settlement.png"))

    # --- 脚本事件覆盖留痕：≥3 张截图全部落盘 ---
    assert len(saved) >= 3
    assert all(p.exists() and p.stat().st_size > 0 for p in saved)
