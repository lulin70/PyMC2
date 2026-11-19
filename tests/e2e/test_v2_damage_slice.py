"""Wave V2 受损变体 e2e：带 fixture 资产包跑真实战局，验证 HP 阈值切换受损精灵。

程序化 fixture（PIL 现场生成，真资产零入库）验证端到端管线：create_game 接线
（madcat 基名 + MadCatDam 受损变体）→ run_frame 真实渲染 → 开局满血像素=基名色 →
hull_ratio 压到阈值下（服务级 take_damage 确定性诱导，渲染链路全真实）→ 像素切
受损变体色且机甲仍存活 → 自动交战照常击毁敌机甲（战斗链路与 M2 语义一致）。
无变体/无资产包回退由集成测试覆盖；真实资产观感由用户真机确认。
截图存 docs/assets/uat_v2/。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pygame
import pytest
from PIL import Image

import pymc2.main as main_module
from pymc2.domain.entities.mech import Faction
from pymc2.infrastructure.mc2_assets import MC2AssetPack
from pymc2.presentation.renderer import MECH_DAMAGED_HULL_RATIO, WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX
from pymc2.services.ai_commander import AiCommander
from pymc2.services.battle_service import BattleService, create_m2_battle

SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "assets" / "uat_v2"
SCREEN_SIZE = (WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX)
# fixture 色板（单色精灵让像素断言无需推算缩放差值）
MADCAT_COLOR = (200, 40, 40, 255)
MADCAT_DAM_COLOR = (120, 40, 30, 255)
WRECK_COLOR = (60, 60, 66)  # 与 renderer._WRECK_COLOR 一致（私有常量不 import）
# px-01 出生 tile (6,10) 中心 (208,336)；精灵 32px 覆盖 (192,320)-(224,352)。
# 断言点取中线下方 8px：避开水平弹道（双方机甲同排，弹丸沿 y≈336 飞行）。
PX01_PIXEL = (6 * 32 + 16, 10 * 32 + 16 + 8)
DAMAGE_TARGET_RATIO = 0.4  # 压到阈值（0.5）以下的服务级诱导点


class StaticRng:
    """固定随机源（恒命中、恒 CT），保证 e2e 结果确定可回归。"""

    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


def make_damage_pack(tmp_path: Path) -> MC2AssetPack:
    """受损变体 fixture pack：madcat 基名 + MadCatDam 变体（32x32 = 格子尺寸）。"""
    root = tmp_path / "damage"
    tgl = root / "Data" / "TGL" / "128"
    tgl.mkdir(parents=True)
    Image.new("RGBA", (32, 32), MADCAT_COLOR).save(tgl / "MadCat.tga")
    Image.new("RGBA", (32, 32), MADCAT_DAM_COLOR).save(tgl / "MadCatDam.tga")
    pack = MC2AssetPack.load(root)
    assert pack is not None
    return pack


@pytest.fixture()
def game(tmp_path: Path) -> Iterator[main_module.Game]:
    """dummy 驱动下的交互模式战局 + 受损变体 fixture pack（main 默认接线最小复刻）。"""
    pygame.init()
    screen = pygame.display.set_mode(SCREEN_SIZE)
    service: BattleService = create_m2_battle(rng=StaticRng(0.0))
    commander = AiCommander(Faction.ENEMY, decision_interval_ticks=1)
    yield main_module.create_game(
        screen,
        service=service,
        commander=commander,
        asset_pack=make_damage_pack(tmp_path),
    )
    pygame.quit()


def save_screenshot(screen: pygame.Surface, name: str) -> Path:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / name
    pygame.image.save(screen, str(path))
    assert path.stat().st_size > 0
    return path


def test_damaged_variant_switch_in_battle(game: main_module.Game) -> None:
    px = game.service.get("px-01")
    enemy = game.service.get("ex-01")
    assert px is not None and enemy is not None
    saved: list[Path] = []

    # --- 步骤 0：开局帧——满血机甲用基名精灵，自动交战同帧生效 ---
    main_module.run_frame(game, 1 / 60)
    assert px.alive and px.hull_ratio > MECH_DAMAGED_HULL_RATIO
    assert game.screen.get_at(PX01_PIXEL)[:3] == MADCAT_COLOR[:3]
    assert px.attack_target_id == "ex-01"  # 精灵切换不影响自动交战链路
    saved.append(save_screenshot(game.screen, "01_full_hull_base_sprite.png"))

    # --- 步骤 1：hull_ratio 压到阈值下——像素切受损变体色且机甲仍存活 ---
    px.take_damage((1 - DAMAGE_TARGET_RATIO) * px.max_hull)
    assert px.alive and px.hull_ratio <= MECH_DAMAGED_HULL_RATIO
    main_module.run_frame(game, 1 / 60)
    assert game.screen.get_at(PX01_PIXEL)[:3] == MADCAT_DAM_COLOR[:3]
    saved.append(save_screenshot(game.screen, "02_damaged_variant_sprite.png"))

    # --- 步骤 2：自动交战照常击毁敌机甲（受损渲染不拦截战斗链路）---
    for _ in range(60):
        main_module.run_frame(game, 0.05)
        if not enemy.alive:
            break
    assert not enemy.alive
    assert px.alive  # 步骤 1 诱导点 40% + 敌方零星命中，仍在存活窗口内
    # 残骸像素：敌机甲最终位置中心右偏 6px（避开两条交叉裂纹对角线）
    wx, wy = int(enemy.x) + 6, int(enemy.y)
    assert game.screen.get_at((wx, wy))[:3] == WRECK_COLOR[:3]
    saved.append(save_screenshot(game.screen, "03_enemy_destroyed_wreck.png"))

    # --- 验收留痕：3 张截图全部落盘 ---
    assert len(saved) == 3
    assert all(p.exists() and p.stat().st_size > 0 for p in saved)
