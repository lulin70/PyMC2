"""M4 全链路 e2e：AI 指挥官自动进攻 → 玩家机甲被摧毁；PRESERVE_FORCE 后撤场景留痕。

ROADMAP M4 验收：AI 指挥 vs 玩家单机（玩家全程不动），AI 自动接近 → 开火 →
摧毁玩家机甲；截图 ≥3 张存 docs/assets/uat_m4/（含后撤场景触发）。
dummy 视频驱动 + 固定随机源，与 M1/M2/M3 uat 同模式。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TypeVar

import pygame
import pytest

import pymc2.main as main_module
from pymc2.domain.entities.mech import Faction
from pymc2.presentation.renderer import WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX
from pymc2.services.ai_commander import AiCommander, GoalType
from pymc2.services.battle_service import BattleService, create_m4_battle

SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "assets" / "uat_m4"
MAX_POLL_FRAMES = 1200

T = TypeVar("T")


class StaticRng:
    """固定随机源（恒命中、部位恒 CT），保证 e2e 结果确定可回归。"""

    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


def save_screenshot(screen: pygame.Surface, name: str) -> Path:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / name
    pygame.image.save(screen, str(path))
    assert path.stat().st_size > 0
    return path


def run_until[T](game: main_module.Game, predicate: Callable[[], T], max_frames: int = MAX_POLL_FRAMES) -> T | None:
    """逐帧推进决策循环直至谓词满足；超帧返回 None（由断言判败）。"""
    for _ in range(max_frames):
        main_module.run_frame(game, 1 / 60)
        result = predicate()
        if result:
            return result
    return None


@pytest.fixture()
def game() -> Iterator[main_module.Game]:
    """dummy 驱动下的完整 M4 战局（AI 指挥官，固定随机源，决策间隔 30 帧）。

    本切片编排玩家全程静置、由脚本伤害触发后撤场景——解除自动交战接线
    （battle.dispatcher 置 None）以保持 M4 验收语义；自动交战行为由
    interactive/m1-m3 uat 与 test_auto_engage 单测覆盖。
    """
    pygame.init()
    screen = pygame.display.set_mode((WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX))
    commander = AiCommander(Faction.ENEMY)
    service: BattleService = create_m4_battle(rng=StaticRng(0.0))
    game = main_module.create_game(screen, service=service, commander=commander)
    service.dispatcher = None  # M4 编排依赖玩家静置不还击（脚本伤害驱动后撤场景）
    yield game
    pygame.quit()


def test_m4_ai_commander_destroys_stationary_player(game: main_module.Game) -> None:
    service = game.service
    player = service.get("px-01")
    ex1 = service.get("ex-01")
    ex2 = service.get("ex-02")
    assert player is not None and ex1 is not None and ex2 is not None
    saved: list[Path] = []

    # --- 初始帧：三方对阵（玩家不动，AI 待命），渲染管线产出可见画面 ---
    main_module.run_frame(game, 1 / 60)
    assert ex1.position == (592.0, 336.0)
    assert ex2.position == (656.0, 432.0)  # tile (20,13) 中心
    saved.append(save_screenshot(game.screen, "01_battle_start.png"))

    # --- 步骤 1：AI 前出（30 帧首次决策 → 接近指令），随后重创 ex-01 触发后撤 ---
    for _ in range(40):
        main_module.run_frame(game, 1 / 60)
    ex1.take_damage(85.0)  # hull 15% < 30% → PRESERVE_FORCE
    for _ in range(60):  # 跨两次决策边界，后撤指令必然下发
        main_module.run_frame(game, 1 / 60)
    assert ex1.alive is True
    assert ex1.hull_ratio < 0.30
    assert ex1.move_target is not None
    assert ex1.move_target[0] > ex1.x  # 向东远离西侧玩家
    saved.append(save_screenshot(game.screen, "02_retreat_triggered.png"))

    # --- 步骤 2：AI 自动进攻：健康僚机 ex-02 接近玩家并开火（弹丸在飞）---
    projectile_seen = run_until(game, lambda: len(service.projectiles) > 0)
    assert projectile_seen
    assert ex2.x < 656.0  # ex-02 已从出生点前出接近
    saved.append(save_screenshot(game.screen, "03_ai_engaged_projectile.png"))

    # --- 步骤 3：玩家机甲被 AI 摧毁（恒命中 rng：两发 CT → 全毁）---
    wreck = run_until(game, lambda: not player.alive)
    assert wreck is not None
    total_kills = sum(m.pilot.kills for m in (ex1, ex2) if m.pilot is not None)
    assert total_kills >= 1  # 击杀入账（驾驶员 kills 计数）
    # ex-01 仍重伤 → force goal 保持 PRESERVE_FORCE（后撤优先于守区）
    assert game.commander is not None
    assert game.commander.current_goal is not None
    assert game.commander.current_goal.goal_type is GoalType.PRESERVE_FORCE
    saved.append(save_screenshot(game.screen, "04_player_destroyed.png"))

    # --- 验收留痕：≥3 张截图全部落盘 ---
    assert len(saved) >= 3
    assert all(p.exists() and p.stat().st_size > 0 for p in saved)
