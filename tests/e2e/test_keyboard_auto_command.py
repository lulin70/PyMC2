"""键盘操控 + Q 键托管 e2e（dummy 驱动 + event.post 走真实输入路径）。

用户反馈两项验收链路：框选 → 方向键/WASD 编队移动（KEYBOARD_MOVE_TILES=5）→
Q 托管（玩家侧 AiCommander 接管：COMMANDER 源指令下发、自主交战）→ 再按 Q 解除
（无新 COMMANDER 指令，已下发指令执行完自然停止）。敌方机甲出生点外移至 tile
(24,10)（武器射程外）：先按 F 转 HOLD 冻结玩家火力，保证托管验收时敌机甲存活、
AI 的 ATTACK_OBJECT 指令真实入账（恢复 FREE 后自主交战落地）。截图存
docs/assets/uat_keyboard/。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pygame
import pytest

import pymc2.main as main_module
from pymc2.domain.entities.mech import TILE_SIZE_PX, Faction
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType
from pymc2.presentation.renderer import WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX
from pymc2.services.ai_commander import AiCommander
from pymc2.services.battle_service import BattleService, create_m2_battle

SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "assets" / "uat_keyboard"
PLAYER_UNIT_IDS = {"px-01", "px-02"}
ENEMY_FAR_TILE = (24, 10)  # 距玩家出生点 576px：双机射程外（420/380），开局零自动交战


class StaticRng:
    """固定随机源（恒命中、恒 CT），保证 e2e 结果确定可回归。"""

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
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=key))


def _far_enemy_battle() -> BattleService:
    """交互模式战局：敌方机甲外移到玩家双机射程外（托管验收期间保持存活）。"""
    service = create_m2_battle(rng=StaticRng(0.0))
    enemy = service.get("ex-01")
    assert enemy is not None
    enemy.x = ENEMY_FAR_TILE[0] * TILE_SIZE_PX + TILE_SIZE_PX / 2
    enemy.y = ENEMY_FAR_TILE[1] * TILE_SIZE_PX + TILE_SIZE_PX / 2
    return service


@pytest.fixture()
def game() -> Iterator[main_module.Game]:
    """dummy 驱动下的交互模式战局：仅敌方指挥官（main 默认接线 + 敌机外移）。"""
    pygame.init()
    screen = pygame.display.set_mode((WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX))
    commander = AiCommander(Faction.ENEMY, decision_interval_ticks=1)
    yield main_module.create_game(screen, service=_far_enemy_battle(), commander=commander)
    pygame.quit()


def _box_select_both(game: main_module.Game) -> None:
    """左键拖拽框选双机（世界坐标 = 屏幕坐标：选中前相机停 0）。"""
    post_mouse_down(185, 320, button=1)
    main_module.run_frame(game, 1 / 60)
    post_mouse_motion(295, 352)
    main_module.run_frame(game, 1 / 60)
    post_mouse_up(295, 352, button=1)
    main_module.run_frame(game, 1 / 60)
    assert game.service.selected_ids == ["px-01", "px-02"]


def _player_commander_orders_after(game: main_module.Game, index: int) -> list[object]:
    """托管生效证据：index 之后触及玩家机甲的 COMMANDER 源指令（敌方 AI 永不越界）。"""
    return [
        order
        for order in game.dispatcher.order_log[index:]
        if order.origin is OrderOrigin.COMMANDER and not PLAYER_UNIT_IDS.isdisjoint(set(order.unit_ids))
    ]


def test_keyboard_move_and_auto_command_chain(game: main_module.Game) -> None:
    player_a = game.service.get("px-01")
    player_b = game.service.get("px-02")
    enemy = game.service.get("ex-01")
    assert player_a is not None and player_b is not None and enemy is not None
    saved: list[Path] = []

    # --- 步骤 1：框选双机 + F 转 HOLD（冻结玩家火力，敌机甲在托管验收前保持存活）---
    _box_select_both(game)
    post_key(pygame.K_f)
    main_module.run_frame(game, 1 / 60)
    assert player_a.fire_mode.value == "hold" and player_b.fire_mode.value == "hold"
    assert enemy.alive and player_a.move_target is None  # 射程外：零自动交战、零移动

    # --- 步骤 2：方向键右（K_d）编队移动 5 格：每机自身 tile+Δ，目标互异平行 ---
    log_before = len(game.dispatcher.order_log)
    post_key(pygame.K_d)
    main_module.run_frame(game, 1 / 60)
    moves = [
        order
        for order in game.dispatcher.order_log[log_before:]
        if order.origin is OrderOrigin.PLAYER and order.order_type is OrderType.MOVE_TO_POINT
    ]
    assert {order.target_tile for order in moves} == {(11, 10), (13, 10)}
    assert player_a.move_target == (11 * TILE_SIZE_PX + TILE_SIZE_PX / 2, 336.0)
    assert player_b.move_target == (13 * TILE_SIZE_PX + TILE_SIZE_PX / 2, 336.0)
    saved.append(save_screenshot(game.screen, "01_keyboard_formation_move.png"))
    for _ in range(400):
        main_module.run_frame(game, 0.05)
        if player_a.move_target is None and player_b.move_target is None:
            break
    assert player_a.tile == (11, 10) and player_b.tile == (13, 10)

    # --- 步骤 3：Q 托管 ON——玩家侧 AiCommander 接管，COMMANDER 源指令下发 ---
    toggle_on_index = len(game.dispatcher.order_log)
    post_key(pygame.K_q)
    main_module.run_frame(game, 1 / 60)
    assert game.player_commander is not None
    assert game.player_commander.faction is Faction.PLAYER
    for _ in range(45):
        main_module.run_frame(game, 1 / 60)
    commander_orders = _player_commander_orders_after(game, toggle_on_index)
    assert commander_orders, "托管开启后玩家侧 AI 应下发 COMMANDER 源指令"
    assert any(order.order_type is OrderType.ATTACK_OBJECT for order in commander_orders)
    assert all(order.target_unit_id == "ex-01" for order in commander_orders if order.target_unit_id)
    assert enemy.alive  # HOLD 纪律下 AI 攻击指令入账但不击发
    saved.append(save_screenshot(game.screen, "02_auto_command_on.png"))

    # --- 步骤 4：F 恢复 FREE → 托管 AI 的攻击指令真实执行（自主交战落地）---
    post_key(pygame.K_f)
    main_module.run_frame(game, 1 / 60)
    for _ in range(30):
        main_module.run_frame(game, 1 / 60)
        if any(p.attacker_id in PLAYER_UNIT_IDS for p in game.service.projectiles):
            break
    assert any(p.attacker_id in PLAYER_UNIT_IDS for p in game.service.projectiles)
    assert player_a.attack_target_id == "ex-01" or player_b.attack_target_id == "ex-01"

    # --- 步骤 5：A 托管 OFF——此后零玩家侧 COMMANDER 指令（敌方 AI 仍不越界）---
    toggle_off_index = len(game.dispatcher.order_log)
    post_key(pygame.K_q)
    main_module.run_frame(game, 1 / 60)
    assert game.player_commander is None
    for _ in range(45):
        main_module.run_frame(game, 1 / 60)
    assert _player_commander_orders_after(game, toggle_off_index) == []
    saved.append(save_screenshot(game.screen, "03_auto_command_off.png"))

    # --- 全程边界：托管 ON 段外玩家机甲只收 PLAYER/SELF 源指令 ---
    for index, order in enumerate(game.dispatcher.order_log):
        touches_player = not PLAYER_UNIT_IDS.isdisjoint(set(order.unit_ids))
        if touches_player and order.origin is OrderOrigin.COMMANDER:
            assert toggle_on_index <= index < toggle_off_index, order
    assert len(saved) >= 3
    assert all(path.exists() and path.stat().st_size > 0 for path in saved)


def test_auto_command_key_noop_in_demo_mode(game: main_module.Game) -> None:
    """--demo 双侧 AI 已就位：A 键托管无效（不挂玩家侧指挥官、不改写既有决策回路）。"""
    game.demo_mode = True
    post_key(pygame.K_q)
    main_module.run_frame(game, 1 / 60)
    assert game.player_commander is None
    for _ in range(35):
        main_module.run_frame(game, 1 / 60)
    assert game.player_commander is None
    assert _player_commander_orders_after(game, 0) == []  # 玩家机甲零 COMMANDER 指令
