"""M1 全链路 e2e：选中 → 移动 → 自动交战开火 → 掉血，全程截图留痕。

ROADMAP M1 验收：e2e 脚本「选中 → 移动 → 开火 → 掉血」全链路截图存
docs/assets/uat_m1/。dummy 视频驱动 + pygame.event.post 模拟真实输入。
注：M2 起 F 键改为火力姿态循环；M7 起开火由自动交战接管（射程内自动锁定，
origin=SELF 经 dispatcher 下发），右键不再承担攻击语义（一律移动）。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pygame
import pytest

import pymc2.main as main_module
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType
from pymc2.presentation.renderer import WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX
from pymc2.services.battle_service import BattleService, create_m1_battle

SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "assets" / "uat_m1"


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


def post_click(x: int, y: int, button: int) -> None:
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(x, y), button=button))
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(x, y), button=button))


@pytest.fixture()
def game() -> Iterator[main_module.Game]:
    """dummy 驱动下的完整 M1 战局（固定随机源，恒命中）。"""
    pygame.init()
    screen = pygame.display.set_mode((WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX))
    service: BattleService = create_m1_battle(rng=StaticRng(0.0))
    yield main_module.create_game(screen, service=service)
    pygame.quit()


def test_m1_full_chain_selected_moved_auto_fired_damaged(game: main_module.Game) -> None:
    player = game.service.get("px-01")
    enemy = game.service.get("ex-01")
    assert player is not None and enemy is not None
    saved: list[Path] = []

    # --- 初始帧：渲染管线产出可见画面（机甲方块颜色校验）+ 自动交战已开火 ---
    main_module.run_frame(game, 1 / 60)
    assert game.screen.get_at((208, 336))[:3] == (70, 130, 230)
    assert game.screen.get_at((592, 336))[:3] == (210, 70, 60)
    # 零输入自动交战：敌机甲在射程内（384px < 420px）→ SELF 源锁定开火（敌射程 360 不及，无还击）
    assert player.attack_target_id == "ex-01"
    assert len(game.service.projectiles) == 1
    self_orders = [o for o in game.dispatcher.order_log if o.origin is OrderOrigin.SELF]
    assert [(o.unit_ids, o.target_unit_id) for o in self_orders] == [(("px-01",), "ex-01")]
    assert all(o.order_type is OrderType.ATTACK_OBJECT for o in self_orders)

    # --- 步骤 1：左键选中己方机甲 ---
    post_click(208, 336, button=1)
    main_module.run_frame(game, 1 / 60)
    assert game.service.selected_id == "px-01"
    saved.append(save_screenshot(game.screen, "01_selected.png"))

    # --- 步骤 2：右键下达移动指令（目标 256px 外；右键一律移动，不再攻击）---
    post_click(464, 336, button=3)
    main_module.run_frame(game, 1 / 60)
    assert player.move_target == (464.0, 336.0)
    assert not any(
        o.origin is OrderOrigin.PLAYER and o.order_type is OrderType.ATTACK_OBJECT for o in game.dispatcher.order_log
    )
    saved.append(save_screenshot(game.screen, "02_move_order.png"))

    # --- 步骤 3：tick 推进至到位（256px / 96px/s ≈ 2.67s；期间自动开火弹丸落地）---
    for _ in range(60):
        main_module.run_frame(game, 0.05)
        if player.move_target is None:
            break
    assert player.move_target is None
    assert player.position == (464.0, 336.0)
    saved.append(save_screenshot(game.screen, "03_arrived.png"))

    # --- 步骤 4：自动交战击毁验收——零玩家攻击指令，敌机甲被击毁、锁定清除 ---
    assert not enemy.alive  # 两发 CT：首发破甲（hull 75），次发（接近后）CT 结构击穿 → 全毁
    assert enemy.hull == 50  # 恒命中恒 CT：总量精确扣 2 × 25
    assert player.attack_target_id is None  # 目标死亡 → 自动清除锁定
    assert any(
        o.origin is OrderOrigin.SELF and o.order_type is OrderType.ATTACK_OBJECT for o in game.dispatcher.order_log
    )
    assert not any(
        o.origin is OrderOrigin.PLAYER and o.order_type is OrderType.ATTACK_OBJECT for o in game.dispatcher.order_log
    )
    saved.append(save_screenshot(game.screen, "04_auto_engagement_kill.png"))

    # --- 验收留痕：≥3 张截图全部落盘 ---
    assert len(saved) >= 3
    assert all(p.exists() and p.stat().st_size > 0 for p in saved)
