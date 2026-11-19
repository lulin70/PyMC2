"""交互模式（main 默认）e2e：真人可玩全链路——框选 → 右键移动 → 自动交战击毁 → 掉血。

main() 默认接线 = 玩家侧无 AiCommander、敌方保留 AiCommander + BattleService 自动交战
（FREE 机甲对射程内最近敌机甲自动锁定开火，origin=SELF 经 dispatcher 下发，无需玩家
攻击指令）。右键语义：一律 MOVE_TO_POINT（点到敌方机甲也移动过去，攻击交给自动交战）。
本测试按同款接线（仅敌方指挥官注入 create_game）+ pygame.event.post 走真实输入路径
（与 M2 uat 同模式），并断言 AI 边界：玩家机甲只收 PLAYER/SELF 源指令、敌方指挥官
只控敌方（COMMANDER 不越界）。截图存 docs/assets/uat_interactive/。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pygame
import pytest

import pymc2.main as main_module
from pymc2.domain.entities.mech import Faction
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType
from pymc2.presentation.renderer import WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX
from pymc2.services.ai_commander import AiCommander
from pymc2.services.battle_service import BattleService, create_m2_battle

SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "assets" / "uat_interactive"
PLAYER_UNIT_IDS = {"px-01", "px-02"}


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


@pytest.fixture()
def game() -> Iterator[main_module.Game]:
    """dummy 驱动下的交互模式战局：仅敌方指挥官（main 默认接线的最小复刻）。

    决策间隔取 1（main 默认 30）：自动交战下敌方机甲在首个默认决策边界前即可能被
    击毁，提速决策以保留「敌方指挥官正常产出指令」的验收语义。
    """
    pygame.init()
    screen = pygame.display.set_mode((WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX))
    service: BattleService = create_m2_battle(rng=StaticRng(0.0))
    commander = AiCommander(Faction.ENEMY, decision_interval_ticks=1)
    yield main_module.create_game(screen, service=service, commander=commander)
    pygame.quit()


def assert_commander_boundary(game: main_module.Game) -> None:
    """AI 边界：触及玩家机甲的指令只允许 PLAYER/SELF 源（敌方 AI 不越界）。"""
    for order in game.dispatcher.order_log:
        if PLAYER_UNIT_IDS & set(order.unit_ids):
            assert order.origin in (OrderOrigin.PLAYER, OrderOrigin.SELF), order


def assert_no_player_attack_order(game: main_module.Game) -> None:
    """本链路全程零玩家攻击指令：右键不再承担攻击语义，开火全部来自自动交战。"""
    for order in game.dispatcher.order_log:
        if order.origin is OrderOrigin.PLAYER:
            assert order.order_type is not OrderType.ATTACK_OBJECT, order


def test_interactive_select_move_and_auto_engagement(game: main_module.Game) -> None:
    player_a = game.service.get("px-01")
    player_b = game.service.get("px-02")
    enemy = game.service.get("ex-01")
    assert player_a is not None and player_b is not None and enemy is not None
    saved: list[Path] = []

    # --- 步骤 0：开局帧——敌机甲自 Spawn 起在双机射程内，零玩家输入即自动交战 ---
    main_module.run_frame(game, 1 / 60)
    assert player_a.attack_target_id == "ex-01"
    assert player_b.attack_target_id == "ex-01"
    # 双机各自射出一发；敌机甲亦还击 px-02（COMMANDER 或 SELF 源，取决于决策边界先后）
    player_shots = [p for p in game.service.projectiles if p.attacker_id in PLAYER_UNIT_IDS]
    assert len(player_shots) == 2
    assert all(p.target_id == "ex-01" for p in player_shots)
    enemy_shots = [p for p in game.service.projectiles if p.attacker_id == "ex-01"]
    assert len(enemy_shots) == 1 and enemy_shots[0].target_id in PLAYER_UNIT_IDS
    # 玩家双机开火全部来自自动交战（origin=SELF）；敌机甲开火源随决策时序为 COMMANDER/SELF
    self_orders = [o for o in game.dispatcher.order_log if o.origin is OrderOrigin.SELF]
    assert {o.unit_ids[0] for o in self_orders} == PLAYER_UNIT_IDS
    assert all(o.order_type is OrderType.ATTACK_OBJECT for o in self_orders)
    assert all(o.target_unit_id == "ex-01" for o in self_orders)
    assert_no_player_attack_order(game)

    # --- 步骤 1：左键拖拽框选两台己方机甲 ---
    post_mouse_down(185, 320, button=1)
    main_module.run_frame(game, 1 / 60)
    post_mouse_motion(295, 352)
    main_module.run_frame(game, 1 / 60)
    post_mouse_up(295, 352, button=1)
    main_module.run_frame(game, 1 / 60)
    assert game.service.selected_ids == ["px-01", "px-02"]
    saved.append(save_screenshot(game.screen, "01_box_select.png"))

    # --- 步骤 2：右键敌方机甲（存活）→ MOVE_TO_POINT（新语义：移动过去，不开火）---
    assert enemy.alive  # 首轮弹丸尚未落地（t≈0.82s 击毁），此刻仍存活可被点中
    post_mouse_down(592, 336, button=3)
    post_mouse_up(592, 336, button=3)
    main_module.run_frame(game, 1 / 60)
    assert player_a.move_target == (592.0, 336.0)  # tile(18,10) 中心
    assert player_b.move_target == (592.0, 336.0)
    assert_no_player_attack_order(game)
    saved.append(save_screenshot(game.screen, "02_move_toward_enemy.png"))

    # --- 步骤 3：右键地面改派 → 组移动指令（tile(12,10) 中心 = (400, 336)）---
    post_mouse_down(400, 336, button=3)
    post_mouse_up(400, 336, button=3)
    main_module.run_frame(game, 1 / 60)
    assert player_a.move_target == (400.0, 336.0)
    assert player_b.move_target == (400.0, 336.0)
    assert_commander_boundary(game)
    saved.append(save_screenshot(game.screen, "03_move_order.png"))
    # 让路协商（MC2-05）：双机同目标 tile，后到者经超时阀（>90 帧）强制通行后到位
    # → 循环上限 80→200
    for _ in range(200):
        main_module.run_frame(game, 0.05)
        if player_a.move_target is None and player_b.move_target is None:
            break
    assert player_a.position == (400.0, 336.0)
    assert player_b.position == (400.0, 336.0)

    # --- 步骤 4：自动交战击毁验收——全程零玩家攻击指令，敌机甲已阵亡 ---
    assert not enemy.alive
    assert enemy.hull == 60  # 恒命中恒 CT：px-02 15（t≈0.68 落地）+ px-01 25（t≈0.82 CT 摧毁致死）
    assert any(
        o.origin is OrderOrigin.SELF and o.order_type is OrderType.ATTACK_OBJECT for o in game.dispatcher.order_log
    )
    # 目标死亡 → 锁定清除（渲染层攻击高亮同步消失）
    assert player_a.attack_target_id is None
    assert player_b.attack_target_id is None
    assert_no_player_attack_order(game)
    saved.append(save_screenshot(game.screen, "04_auto_engagement_kill.png"))

    # --- 边界验收：玩家指令边界 + 敌方指挥官只产出己方（COMMANDER）源指令 ---
    assert_commander_boundary(game)
    assert_no_player_attack_order(game)
    commander_orders = [o for o in game.dispatcher.order_log if o.origin is OrderOrigin.COMMANDER]
    assert commander_orders, "交互模式下敌方 AI 应持续产出指令"
    for order in commander_orders:
        assert PLAYER_UNIT_IDS.isdisjoint(set(order.unit_ids))

    # --- 验收留痕：≥3 张截图全部落盘 ---
    assert len(saved) >= 3
    assert all(p.exists() and p.stat().st_size > 0 for p in saved)
