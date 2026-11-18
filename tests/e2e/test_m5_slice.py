"""M5 全链路 e2e：任务 1 战斗 → 敌全灭结算（salvage 入账）→ 整备（repair+hire）→
任务 2 开局携带验证（pilot rank / 装甲状态 / 新机甲在列）。

ROADMAP M5 验收：「整备变更带入下一任务」。截图 ≥3 张存 docs/assets/uat_m5/
（dummy 驱动 + 固定 seed，与 M1-M4 uat 同模式）。玩家指令走 OrderDispatcher
（origin=PLAYER，模拟右键开火），敌方走 AiCommander 自动攻防。
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TypeVar

import pygame
import pytest

import pymc2.main as main_module
from pymc2.domain.entities.mech import Faction
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType, TacticalOrder
from pymc2.presentation.renderer import WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX
from pymc2.services.ai_commander import AiCommander
from pymc2.services.campaign_state import start_campaign
from pymc2.services.mission_chain import MissionChain
from pymc2.services.refit_service import RefitService

SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "assets" / "uat_m5"
MAX_POLL_FRAMES = 3600
UAT_SEED = 7  # 探测值：双机存活、px-02 受损（hull 70）、RP 恰够 repair+hire 全链

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
        for unit_id in ("px-01", "px-02", "px-03"):
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


def test_m5_campaign_refit_carryover_slice(screen: pygame.Surface) -> None:
    state = start_campaign("uat-m5")
    chain = MissionChain()
    refit = RefitService()
    saved: list[Path] = []

    # --- 任务 1 开局：名册 2 台 vs 敌 1 台（AI 指挥官自动反击）---
    battle1 = chain.start_mission(state, rng=random.Random(UAT_SEED))
    game = main_module.create_game(screen, service=battle1, commander=AiCommander(Faction.ENEMY))
    main_module.run_frame(game, 1 / 60)
    assert sorted(battle1.mechs) == ["ex-01", "px-01", "px-02"]
    saved.append(save_screenshot(game.screen, "01_mission1_start.png"))

    # --- 玩家经 dispatcher 指令交战 → 敌全灭（真战斗、固定 seed）---
    defeated = run_until(game, lambda: chain.is_victory(battle1))
    assert defeated is not None
    ex01 = battle1.get("ex-01")
    assert ex01 is not None and not ex01.alive
    saved.append(save_screenshot(game.screen, "02_mission1_enemy_destroyed.png"))

    # --- 结算：salvage 入账 + 任务奖励 ---
    result1 = chain.complete_mission(state, battle1)
    assert result1.victory is True
    assert result1.rp_reward == 25
    assert result1.rp_salvage >= 0
    assert state.requisition_points == 30 + 25 + result1.rp_salvage
    assert result1.survivor_ids  # 至少一台幸存（结转验证前提）

    # --- 整备：repair 受损机甲 + hire 新机甲 ---
    wounded = next((m for m in state.roster if m.hull < m.max_hull), None)
    if wounded is None:  # 极端情况（敌方未命中任何人）：人为打伤一台供整备验证
        wounded = state.roster[0]
        wounded.take_damage(40.0)
    assert refit.repair(state, wounded.id) is True
    repaired = next(m for m in state.roster if m.id == wounded.id)
    assert repaired.hull == repaired.max_hull
    hired = refit.hire(state)
    assert hired is not None
    new_unit_id = hired.id
    roster_ids = [m.id for m in state.roster]
    assert new_unit_id not in roster_ids[:-1]  # 新 id 不与既有名册冲突
    assert len(state.roster) == len(result1.survivor_ids) + 1

    # --- 任务 2 开局：pilot rank / 装甲状态 / 新机甲三项携带断言 ---
    roster_before = {m.id: m for m in state.roster}
    battle2 = chain.start_mission(state, rng=random.Random(UAT_SEED))
    assert sorted(battle2.mechs) == sorted(roster_ids + ["ex-01", "ex-02"])
    assert chain.current_def(state).mission_id == "m2_ridge_ambush"
    for unit_id, roster_mech in roster_before.items():
        combat = battle2.get(unit_id)
        assert combat is not None
        # 装甲状态携带：任务 2 参战副本逐台等于名册整备后状态（含 repair 生效）
        assert combat.hull == roster_mech.hull
        for location, part in combat.chassis.parts.items():
            assert (part.armor, part.structure) == (
                roster_mech.chassis.parts[location].armor,
                roster_mech.chassis.parts[location].structure,
            )
        # pilot rank 携带：kills 与双计数逐位还原 → rank 一致
        if combat.pilot is not None and roster_mech.pilot is not None:
            assert (combat.pilot.kills, combat.pilot.gunnery_rank) == (
                roster_mech.pilot.kills,
                roster_mech.pilot.gunnery_rank,
            )
    b2_hired = battle2.get(new_unit_id)
    assert b2_hired is not None and b2_hired.alive  # 新机甲在列

    # --- 任务 2 开局渲染留痕 ---
    game2 = main_module.create_game(screen, service=battle2)
    main_module.run_frame(game2, 1 / 60)
    saved.append(save_screenshot(game2.screen, "03_mission2_carryover_start.png"))

    # --- 验收留痕：≥3 张截图全部落盘 ---
    assert len(saved) >= 3
    assert all(p.exists() and p.stat().st_size > 0 for p in saved)
