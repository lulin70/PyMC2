"""MC2 资产渲染集成测试：命中精灵按格子尺寸绘制；未命中/未加载时与现渲染逐像素一致。

门禁要求「不加载资产时渲染输出与现状完全一致」：以同一战局分别用带包（仅未命中）
与无包渲染器出帧，全图字节比对。
Wave V1 地形合成：带地面/覆盖资产时铺 MC2 纹理（fixture 色块可断言像素），道路
tile 叠 overlay（蒙版内外像素可断言），装饰点缀 blit 在地形之上；无 pack /
env-only pack 地形保持程序化（字节一致），纹理出帧确定性（两帧全图字节相同）。
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
# Wave V1 fixture 色板
GRASS_COLOR = (90, 120, 70, 255)  # 唯一 Detail 变体（全图草地同色，断言无需算 seed）
ROAD_OPAQUE_COLOR = (170, 60, 50, 255)  # overlay 左半幅不透明红
TREE_COLOR = (30, 110, 40, 255)


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


def write_tga(path: Path, image: Image.Image) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def make_terrain_pack(tmp_path: Path, *, with_overlay: bool = True) -> MC2AssetPack:
    """地形 mini pack：单张 Detail（全图草地同色）+ 半幅蒙版道路 overlay + 树精灵。
    fixture 尺寸取 32x32（=TILE_SIZE_PX）避免缩放差值干扰像素断言（真资产零入库）。"""
    root = tmp_path / "terrain"
    write_tga(root / "Data" / "Textures" / "64" / "Detail_10000.tga", Image.new("RGBA", (32, 32), GRASS_COLOR))
    if with_overlay:
        overlay = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        for x in range(16):
            for y in range(32):
                overlay.putpixel((x, y), ROAD_OPAQUE_COLOR)
        write_tga(root / "Data" / "Textures" / "64Overlays" / "2lanedirt0000.TGA", overlay)
    write_tga(root / "Data" / "TGL" / "128" / "A_Pine&Palm.tga", Image.new("RGBA", (32, 32), TREE_COLOR))
    pack = MC2AssetPack.load(root)
    assert pack is not None
    return pack


def pick_grass_pixel() -> tuple[int, int]:
    """远离道路网且在视口内的草地点位：tile(35, 20) 中心（相机 0,0 时屏幕=世界）。"""
    return (35 * 32 + 16, 20 * 32 + 16)


class TestTexturedTerrainRender:
    """Wave V1：带地面/覆盖资产时地形改铺 MC2 纹理，像素可按 fixture 断言。"""

    def test_grass_tile_blits_ground_texture(self, screen: pygame.Surface, tmp_path: Path) -> None:
        service = make_battle_with_unknown_kinds()
        renderer = Renderer(screen, asset_pack=make_terrain_pack(tmp_path, with_overlay=False))
        renderer.render(service, 0.0, 0.0)
        assert screen.get_at(pick_grass_pixel())[:3] == GRASS_COLOR[:3]

    def test_road_tile_composites_overlay_on_grass(self, screen: pygame.Surface, tmp_path: Path) -> None:
        """道路 tile：overlay 蒙版内 = overlay 色（不透明段），蒙版外 = 草皮底色。
        选用 tile(30, 10)（远离机甲出生点 (6,10)/(8,10)/(18,10)）。"""
        service = make_battle_with_unknown_kinds()
        renderer = Renderer(screen, asset_pack=make_terrain_pack(tmp_path))
        renderer.render(service, 0.0, 0.0)
        base_y = 10 * 32 + 16
        assert screen.get_at((30 * 32 + 4, base_y))[:3] == ROAD_OPAQUE_COLOR[:3]  # 左半幅：overlay 覆盖
        assert screen.get_at((30 * 32 + 24, base_y))[:3] == GRASS_COLOR[:3]  # 右半幅：草皮底透出

    def test_textured_output_differs_from_procedural(self, screen: pygame.Surface, tmp_path: Path) -> None:
        service = make_battle_with_unknown_kinds()
        plain = pygame.Surface(SCREEN_SIZE)
        textured = pygame.Surface(SCREEN_SIZE)
        Renderer(plain).render(service, 0.0, 0.0)
        Renderer(textured, asset_pack=make_terrain_pack(tmp_path)).render(service, 0.0, 0.0)
        assert pygame.image.tobytes(plain, "RGB") != pygame.image.tobytes(textured, "RGB")

    def test_textured_render_is_deterministic(self, screen: pygame.Surface, tmp_path: Path) -> None:
        """纹理出帧确定性：同一战局渲染两帧，全图字节相同（同 seed 同结果）。"""
        service = make_battle_with_unknown_kinds()
        pack = make_terrain_pack(tmp_path)
        first = pygame.Surface(SCREEN_SIZE)
        second = pygame.Surface(SCREEN_SIZE)
        Renderer(first, asset_pack=pack).render(service, 0.0, 0.0)
        Renderer(second, asset_pack=pack).render(service, 0.0, 0.0)
        assert pygame.image.tobytes(first, "RGB") == pygame.image.tobytes(second, "RGB")


class TestDecorationRender:
    """环境点缀：set_decorations 后树精灵 blit 在地形之上、机甲之下；逐项降级。"""

    def test_decoration_blits_tree_sprite_at_tile_center(self, screen: pygame.Surface, tmp_path: Path) -> None:
        service = make_battle_with_unknown_kinds()
        renderer = Renderer(screen, asset_pack=make_terrain_pack(tmp_path))
        renderer.set_decorations(((35, 20, "tree"),))
        renderer.render(service, 0.0, 0.0)
        assert screen.get_at(pick_grass_pixel())[:3] == TREE_COLOR[:3]

    def test_unknown_kind_degrades_to_terrain(self, screen: pygame.Surface, tmp_path: Path) -> None:
        """kind 未命中逐项跳过：该 tile 保持草皮底色（不出错不画块）。"""
        service = make_battle_with_unknown_kinds()
        renderer = Renderer(screen, asset_pack=make_terrain_pack(tmp_path))
        renderer.set_decorations(((35, 20, "zzz"),))
        renderer.render(service, 0.0, 0.0)
        assert screen.get_at(pick_grass_pixel())[:3] == GRASS_COLOR[:3]

    def test_no_pack_decorations_ignored(self, screen: pygame.Surface) -> None:
        """无资产包时点缀不绘制：输出与纯程序化渲染逐字节一致（现状保持）。"""
        service = make_battle_with_unknown_kinds()
        plain = pygame.Surface(SCREEN_SIZE)
        decorated = pygame.Surface(SCREEN_SIZE)
        plain_renderer = Renderer(plain)
        decorated_renderer = Renderer(decorated)
        decorated_renderer.set_decorations(((35, 20, "tree"),))
        plain_renderer.render(service, 0.0, 0.0)
        decorated_renderer.render(service, 0.0, 0.0)
        assert pygame.image.tobytes(plain, "RGB") == pygame.image.tobytes(decorated, "RGB")

    def test_decoration_drawn_under_mech(self, screen: pygame.Surface, tmp_path: Path) -> None:
        """纯装饰画在地形之上、机甲之下：机甲走到点缀 tile 上时覆盖树精灵。"""
        service = make_battle_with_unknown_kinds()
        service.mechs["zz-01"].x = float(35 * 32 + 16)  # 机甲移到点缀 tile 中心
        service.mechs["zz-01"].y = float(20 * 32 + 16)
        renderer = Renderer(screen, asset_pack=make_terrain_pack(tmp_path))
        renderer.set_decorations(((35, 20, "tree"),))
        renderer.render(service, 0.0, 0.0)
        assert screen.get_at(pick_grass_pixel())[:3] == PLAYER_COLOR[:3]  # 机甲色块盖住树
