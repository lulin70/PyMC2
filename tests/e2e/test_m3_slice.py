"""M3 全链路 e2e：选中 → 开火 → 弹着部位伤害 → CT 摧毁爆炸（残骸），截图留痕。

ROADMAP M3 验收：一场小战斗打到一台机甲 CT 摧毁，截图 ≥3 张存 docs/assets/uat_m3/。
dummy 视频驱动 + pygame.event.post 模拟真实输入（与 M1/M2 uat 同模式）。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pygame
import pytest

import pymc2.main as main_module
from pymc2.domain.entities.mech_chassis import MechLocation
from pymc2.presentation.renderer import _WRECK_COLOR, WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX
from pymc2.services.battle_service import BattleService, create_m3_battle

SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "assets" / "uat_m3"


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
    """dummy 驱动下的完整 M3 战局（中型底盘，固定随机源，恒命中 CT）。"""
    pygame.init()
    screen = pygame.display.set_mode((WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX))
    service: BattleService = create_m3_battle(rng=StaticRng(0.0))
    yield main_module.create_game(screen, service=service)
    pygame.quit()


def test_m3_full_chain_selected_fired_ct_destroyed(game: main_module.Game) -> None:
    player = game.service.get("px-01")
    enemy = game.service.get("ex-01")
    assert player is not None and enemy is not None
    saved: list[Path] = []

    # --- 初始帧：渲染管线产出可见画面（机甲方块颜色校验）---
    main_module.run_frame(game, 1 / 60)
    assert game.screen.get_at((208, 336))[:3] == (70, 130, 230)
    assert game.screen.get_at((592, 336))[:3] == (210, 70, 60)

    # --- 步骤 1：左键选中己方机甲 ---
    post_click(208, 336, button=1)
    main_module.run_frame(game, 1 / 60)
    assert game.service.selected_id == "px-01"
    saved.append(save_screenshot(game.screen, "01_selected.png"))

    # --- 步骤 2：右键敌机甲开火 → 弹丸飞行（部位命中在开火瞬间判定为 CT）---
    hull_before = enemy.hull
    post_click(592, 336, button=3)
    main_module.run_frame(game, 1 / 60)
    assert len(game.service.projectiles) == 1
    assert game.service.projectiles[0].location is MechLocation.CT
    assert player.heat.heat == pytest.approx(5.0, abs=0.05)  # 开火产热（HUD 可见）
    saved.append(save_screenshot(game.screen, "02_projectile_in_flight.png"))

    # --- 步骤 3：弹丸落地（384px / 480px/s ≈ 0.8s）→ 部位结算 → CT 摧毁 → 机甲全毁 ---
    for _ in range(20):
        main_module.run_frame(game, 0.05)
        if not game.service.projectiles:
            break
    assert not game.service.projectiles
    assert enemy.chassis.is_destroyed(MechLocation.CT) is True
    assert enemy.chassis.destroyed_locations == frozenset({MechLocation.CT})
    assert enemy.alive is False
    assert enemy.hull == hull_before - 25  # 总量精确扣减（CT 21 + 溢出 4 → LT 装甲）
    assert game.screen.get_at((588, 330))[:3] == _WRECK_COLOR  # 爆炸残骸色块（避开 X 裂纹线）
    saved.append(save_screenshot(game.screen, "03_ct_destroyed_wreck.png"))

    # --- 验收留痕：≥3 张截图全部落盘 ---
    assert len(saved) >= 3
    assert all(p.exists() and p.stat().st_size > 0 for p in saved)
