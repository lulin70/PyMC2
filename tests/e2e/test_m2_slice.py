"""M2 全链路 e2e：框选两台 → 右键移动 → F 切火力姿态 → 自动交战 → 击毁。

ROADMAP M2 验收：指令层点击链路截图留痕，存 docs/assets/uat_m2/。
dummy 视频驱动 + pygame.event.post 模拟真实输入（与 M1 uat 同模式）。
注：M7 起开火由自动交战接管（射程内自动锁定开火，origin=SELF），右键一律
MOVE_TO_POINT（点到敌方机甲也移动过去），存量「右键敌机甲开火」步骤改写。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pygame
import pytest

import pymc2.main as main_module
from pymc2.domain.entities.mech import FireMode
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType
from pymc2.presentation.renderer import WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX
from pymc2.services.battle_service import BattleService, create_m2_battle

SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "assets" / "uat_m2"


class StaticRng:
    """固定随机源（恒命中），保证 e2e 结果确定可回归。"""

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


def post_mouse_down(x: int, y: int, button: int) -> None:
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(x, y), button=button))


def post_mouse_motion(x: int, y: int) -> None:
    pygame.event.post(pygame.event.Event(pygame.MOUSEMOTION, pos=(x, y)))


def post_mouse_up(x: int, y: int, button: int) -> None:
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(x, y), button=button))


def post_key(key: int) -> None:
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=key, mod=0))
    pygame.event.post(pygame.event.Event(pygame.KEYUP, key=key, mod=0))


@pytest.fixture()
def game() -> Iterator[main_module.Game]:
    """dummy 驱动下的完整 M2 战局（固定随机源，恒命中）。"""
    pygame.init()
    screen = pygame.display.set_mode((WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX))
    service: BattleService = create_m2_battle(rng=StaticRng(0.0))
    yield main_module.create_game(screen, service=service)
    pygame.quit()


def test_m2_full_chain_box_select_group_move_fire_mode_and_attack(game: main_module.Game) -> None:
    player_a = game.service.get("px-01")
    player_b = game.service.get("px-02")
    enemy = game.service.get("ex-01")
    assert player_a is not None and player_b is not None and enemy is not None
    saved: list[Path] = []

    # --- 初始帧：渲染管线产出可见画面（机甲方块颜色校验）---
    main_module.run_frame(game, 1 / 60)
    assert game.screen.get_at((208, 336))[:3] == (70, 130, 230)
    assert game.screen.get_at((272, 336))[:3] == (70, 130, 230)
    assert game.screen.get_at((592, 336))[:3] == (210, 70, 60)

    # --- 步骤 1：左键拖拽框选两台己方机甲（多选）---
    post_mouse_down(185, 320, button=1)
    main_module.run_frame(game, 1 / 60)
    post_mouse_motion(295, 352)
    main_module.run_frame(game, 1 / 60)
    post_mouse_up(295, 352, button=1)
    main_module.run_frame(game, 1 / 60)
    assert game.service.selected_ids == ["px-01", "px-02"]
    saved.append(save_screenshot(game.screen, "01_box_select.png"))

    # --- 步骤 2：右键地面 → 组移动指令（tile(12,10) 中心 = (400, 336)）---
    post_mouse_down(400, 336, button=3)
    post_mouse_up(400, 336, button=3)
    main_module.run_frame(game, 1 / 60)
    assert player_a.move_target == (400.0, 336.0)
    assert player_b.move_target == (400.0, 336.0)
    saved.append(save_screenshot(game.screen, "02_group_move.png"))
    for _ in range(80):
        main_module.run_frame(game, 0.05)
        if player_a.move_target is None and player_b.move_target is None:
            break
    assert player_a.position == (400.0, 336.0)
    assert player_b.position == (400.0, 336.0)

    # --- 步骤 3：F 键切火力姿态 FREE→HOLD（HUD 状态行可见），再切回 FREE ---
    post_key(pygame.K_f)
    main_module.run_frame(game, 1 / 60)
    assert player_a.fire_mode is FireMode.HOLD
    assert player_b.fire_mode is FireMode.HOLD
    saved.append(save_screenshot(game.screen, "03_fire_hold.png"))
    post_key(pygame.K_f)
    main_module.run_frame(game, 1 / 60)
    assert player_a.fire_mode is FireMode.FREE
    assert player_b.fire_mode is FireMode.FREE

    # --- 步骤 4：右键敌方机甲（已被自动交战击毁）→ MOVE_TO_POINT（新语义：右键不再攻击）---
    post_mouse_down(592, 336, button=3)
    post_mouse_up(592, 336, button=3)
    main_module.run_frame(game, 1 / 60)
    assert player_a.move_target == (592.0, 336.0)  # tile(18,10) 中心
    assert player_b.move_target == (592.0, 336.0)
    assert not any(
        o.origin is OrderOrigin.PLAYER and o.order_type is OrderType.ATTACK_OBJECT for o in game.dispatcher.order_log
    )
    saved.append(save_screenshot(game.screen, "04_auto_engagement.png"))

    # --- 步骤 5：自动交战击毁验收——零玩家攻击指令，敌机甲阵亡、锁定清除 ---
    assert not enemy.alive
    assert enemy.hull == 60  # 首轮齐射即击毁：15（t≈0.68 破 CT 装甲）+ 25（t≈0.82 CT 结构击穿 → 全毁）
    assert player_a.attack_target_id is None
    assert player_b.attack_target_id is None
    assert any(
        o.origin is OrderOrigin.SELF and o.order_type is OrderType.ATTACK_OBJECT for o in game.dispatcher.order_log
    )
    saved.append(save_screenshot(game.screen, "05_enemy_destroyed.png"))

    # --- 验收留痕：≥4 张截图全部落盘 ---
    assert len(saved) >= 4
    assert all(p.exists() and p.stat().st_size > 0 for p in saved)
