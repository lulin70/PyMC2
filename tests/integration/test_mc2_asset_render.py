"""MC2 资产渲染集成测试：命中精灵按格子尺寸绘制；未命中/未加载时与现渲染逐像素一致。

门禁要求「不加载资产时渲染输出与现状完全一致」：以同一战局分别用带包（仅未命中）
与无包渲染器出帧，全图字节比对。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pygame
import pytest
from PIL import Image

from pymc2.domain.entities.mech import Faction, Mech, Weapon
from pymc2.infrastructure.mc2_assets import MC2AssetPack
from pymc2.presentation.renderer import WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX, Renderer
from pymc2.services.battle_service import BattleService

MADCAT_COLOR = (200, 40, 40, 255)
PLAYER_COLOR = (70, 130, 230)
SCREEN_SIZE = (WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX)


@pytest.fixture()
def screen() -> Iterator[pygame.Surface]:
    pygame.init()
    yield pygame.display.set_mode(SCREEN_SIZE)
    pygame.quit()


def make_pack(tmp_path: Path) -> MC2AssetPack:
    """mini pack：仅收录 madcat（真资产零入库，PIL 现场生成）。"""
    root = tmp_path / "mc2"
    path = root / "Data" / "TGL" / "256" / "madcat.tga"
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (32, 32), MADCAT_COLOR).save(path)
    pack = MC2AssetPack.load(root)
    assert pack is not None
    return pack


def make_battle_with_unknown_kinds() -> BattleService:
    """双机甲模板未登记（zz-*）→ 资产必未命中 → 走程序化降级路径。"""
    service = BattleService()
    service.add_mech(Mech("zz-01", Faction.PLAYER, 300.0, 300.0, weapon=Weapon("ppc", 25, 420.0, 2.0)))
    service.add_mech(Mech("zz-02", Faction.ENEMY, 600.0, 300.0, weapon=Weapon("mlaser", 15, 360.0, 2.5)))
    return service


class TestSpriteHitRender:
    def test_hit_mech_blits_scaled_sprite(self, screen: pygame.Surface, tmp_path: Path) -> None:
        """px-01 命中 madcat：中心像素为精灵色（非阵营色块），格子尺寸 32x32。"""
        service = BattleService()
        service.add_mech(Mech("px-01", Faction.PLAYER, 300.0, 300.0, weapon=Weapon("ppc", 25, 420.0, 2.0)))
        renderer = Renderer(screen, asset_pack=make_pack(tmp_path))
        renderer.render(service, 0.0, 0.0)
        assert screen.get_at((300, 300))[:3] == MADCAT_COLOR[:3]

    def test_procedural_overlays_survive_sprite_hit(self, screen: pygame.Surface, tmp_path: Path) -> None:
        """选中框仍程序化绘制：精灵外扩 4px 处出现黄色选中描边。"""
        service = BattleService()
        service.add_mech(Mech("px-01", Faction.PLAYER, 300.0, 300.0, weapon=Weapon("ppc", 25, 420.0, 2.0)))
        service.selected_ids = ["px-01"]
        renderer = Renderer(screen, asset_pack=make_pack(tmp_path))
        renderer.render(service, 0.0, 0.0)
        # 精灵 32px 居中 (300,300) → 选中框为 inflate(8,8) 外框 (280,280)-(320,320)；
        # 取左侧边框中点（顶边中点被血条覆盖）
        assert screen.get_at((280, 300))[:3] == (250, 210, 60)


class TestDegradedRenderEquivalence:
    def test_miss_render_is_pixel_identical_to_no_pack(self, screen: pygame.Surface, tmp_path: Path) -> None:
        """未命中（无包 / 包内无匹配）渲染必须与现状逐像素一致。"""
        service = make_battle_with_unknown_kinds()
        pack = make_pack(tmp_path)
        plain = pygame.Surface(SCREEN_SIZE)
        with_pack = pygame.Surface(SCREEN_SIZE)
        Renderer(plain).render(service, 0.0, 0.0)
        Renderer(with_pack, asset_pack=pack).render(service, 0.0, 0.0)
        assert pygame.image.tobytes(plain, "RGB") == pygame.image.tobytes(with_pack, "RGB")

    def test_no_pack_mech_stays_procedural_color_block(self, screen: pygame.Surface) -> None:
        """不注入资产包：机甲保持阵营色块（存量 e2e 语义不变）。"""
        service = make_battle_with_unknown_kinds()
        renderer = Renderer(screen)
        renderer.render(service, 0.0, 0.0)
        assert screen.get_at((300, 300))[:3] == PLAYER_COLOR
