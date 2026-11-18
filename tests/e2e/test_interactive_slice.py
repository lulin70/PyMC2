"""交互模式（main 默认）e2e：真人可玩全链路——框选 → 右键移动 → 右键开火 → 掉血。

main() 默认接线 = 玩家侧无 AiCommander、敌方保留 AiCommander。本测试按同款接线
（仅敌方指挥官注入 create_game）+ pygame.event.post 走真实输入路径（与 M2 uat
同模式），并断言 AI 边界：指令日志中玩家机甲只收 PLAYER 源指令、敌方指挥官正常
产出 COMMANDER 源指令。截图存 docs/assets/uat_interactive/。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pygame
import pytest

import pymc2.main as main_module
from pymc2.domain.entities.mech import Faction
from pymc2.domain.orders.tactical_order import OrderOrigin
from pymc2.presentation.renderer import WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX
from pymc2.services.ai_commander import AiCommander
from pymc2.services.battle_service import BattleService, create_m2_battle

SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "assets" / "uat_interactive"
PLAYER_UNIT_IDS = {"px-01", "px-02"}


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


@pytest.fixture()
def game() -> Iterator[main_module.Game]:
    """dummy 驱动下的交互模式战局：仅敌方指挥官（main 默认接线的最小复刻）。"""
    pygame.init()
    screen = pygame.display.set_mode((WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX))
    service: BattleService = create_m2_battle(rng=StaticRng(0.0))
    yield main_module.create_game(screen, service=service, commander=AiCommander(Faction.ENEMY))
    pygame.quit()


def assert_player_orders_all_player_origin(game: main_module.Game) -> None:
    """AI 边界：触及玩家机甲的指令必须全部来自 PLAYER 源（敌方 AI 不越界）。"""
    for order in game.dispatcher.order_log:
        if PLAYER_UNIT_IDS & set(order.unit_ids):
            assert order.origin is OrderOrigin.PLAYER, order


def test_interactive_select_move_fire_and_impact(game: main_module.Game) -> None:
    player_a = game.service.get("px-01")
    player_b = game.service.get("px-02")
    enemy = game.service.get("ex-01")
    assert player_a is not None and player_b is not None and enemy is not None
    saved: list[Path] = []

    # --- 初始帧：交互模式下敌方 AI 已在决策循环内（run_frame 驱动），玩家机甲待命 ---
    main_module.run_frame(game, 1 / 60)
    assert game.service.selected_ids == []
    assert player_a.move_target is None and player_a.attack_target_id is None

    # --- 步骤 1：左键拖拽框选两台己方机甲 ---
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
    assert_player_orders_all_player_origin(game)
    saved.append(save_screenshot(game.screen, "02_move_order.png"))
    for _ in range(80):
        main_module.run_frame(game, 0.05)
        if player_a.move_target is None and player_b.move_target is None:
            break
    assert player_a.position == (400.0, 336.0)
    assert player_b.position == (400.0, 336.0)
    saved.append(save_screenshot(game.screen, "03_arrived.png"))

    # --- 步骤 3：右键敌机甲 → ATTACK_OBJECT → 双弹丸飞行（含攻击目标高亮）---
    post_mouse_down(592, 336, button=3)
    post_mouse_up(592, 336, button=3)
    main_module.run_frame(game, 1 / 60)
    assert len(game.service.projectiles) == 2
    assert player_a.attack_target_id == "ex-01"
    assert player_b.attack_target_id == "ex-01"
    saved.append(save_screenshot(game.screen, "04_attack_order.png"))

    # --- 步骤 4：弹丸落地 → 掉血（恒命中：ppc 25 + mlaser 15 → 60）---
    for _ in range(10):
        main_module.run_frame(game, 0.05)
        if not game.service.projectiles:
            break
    assert not game.service.projectiles
    assert enemy.hull == 60
    saved.append(save_screenshot(game.screen, "05_impact.png"))

    # --- 边界验收：玩家指令全部 PLAYER 源；敌方指挥官已产出 COMMANDER 源指令 ---
    assert_player_orders_all_player_origin(game)
    commander_orders = [o for o in game.dispatcher.order_log if o.origin is OrderOrigin.COMMANDER]
    assert commander_orders, "交互模式下敌方 AI 应持续产出指令"
    for order in commander_orders:
        assert PLAYER_UNIT_IDS.isdisjoint(set(order.unit_ids))

    # --- 验收留痕：≥3 张截图全部落盘 ---
    assert len(saved) >= 3
    assert all(p.exists() and p.stat().st_size > 0 for p in saved)
