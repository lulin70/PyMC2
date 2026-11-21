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

from helpers.ase_fixture import STATIC_CARD_COLOR, write_arrow_ase, write_arrow_texture, write_static_card
from pymc2.domain.entities.mech import Faction, Mech, Weapon
from pymc2.infrastructure.mc2_assets import MC2AssetPack
from pymc2.presentation.renderer import MECH_DAMAGED_HULL_RATIO, WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX, Renderer
from pymc2.services.battle_service import BattleService

MADCAT_COLOR = (200, 40, 40, 255)
MADCAT_DAM_COLOR = (120, 40, 30, 255)
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


def make_damaged_pack(tmp_path: Path, *, with_dam_variant: bool = True) -> MC2AssetPack:
    """受损变体 mini pack：madcat 基名 + 可选 MadCatDam 变体（真资产零入库）。"""
    root = tmp_path / "mc2-damaged"
    base = root / "Data" / "TGL" / "128" / "madcat.tga"
    base.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (32, 32), MADCAT_COLOR).save(base)
    if with_dam_variant:
        Image.new("RGBA", (32, 32), MADCAT_DAM_COLOR).save(root / "Data" / "TGL" / "128" / "madcatdam.tga")
    pack = MC2AssetPack.load(root)
    assert pack is not None
    return pack


def make_single_mech_battle() -> BattleService:
    """单台玩家机甲战局（px-01 → madcat，默认 hull=100 便于按阈值压血）。"""
    service = BattleService()
    service.add_mech(Mech("px-01", Faction.PLAYER, 300.0, 300.0, weapon=Weapon("ppc", 25, 420.0, 2.0)))
    return service


class TestDamagedSpriteSwitch:
    """Wave V2：hull_ratio ≤ MECH_DAMAGED_HULL_RATIO 且存活 → damaged 变体精灵。"""

    def test_full_hull_uses_base_sprite(self, screen: pygame.Surface, tmp_path: Path) -> None:
        service = make_single_mech_battle()
        renderer = Renderer(screen, asset_pack=make_damaged_pack(tmp_path))
        renderer.render(service, 0.0, 0.0)
        assert screen.get_at((300, 300))[:3] == MADCAT_COLOR[:3]

    def test_ratio_at_threshold_uses_damaged_sprite(self, screen: pygame.Surface, tmp_path: Path) -> None:
        """边界：hull_ratio == 阈值（≤0.5）即切受损变体。"""
        service = make_single_mech_battle()
        mech = service.mechs["px-01"]
        mech.take_damage((1 - MECH_DAMAGED_HULL_RATIO) * mech.max_hull)
        assert mech.hull_ratio == MECH_DAMAGED_HULL_RATIO
        renderer = Renderer(screen, asset_pack=make_damaged_pack(tmp_path))
        renderer.render(service, 0.0, 0.0)
        assert screen.get_at((300, 300))[:3] == MADCAT_DAM_COLOR[:3]

    def test_ratio_above_threshold_uses_base_sprite(self, screen: pygame.Surface, tmp_path: Path) -> None:
        service = make_single_mech_battle()
        mech = service.mechs["px-01"]
        mech.take_damage(mech.max_hull * 0.49)  # ratio 0.51 > 0.5
        assert mech.hull_ratio > MECH_DAMAGED_HULL_RATIO
        renderer = Renderer(screen, asset_pack=make_damaged_pack(tmp_path))
        renderer.render(service, 0.0, 0.0)
        assert screen.get_at((300, 300))[:3] == MADCAT_COLOR[:3]

    def test_damaged_without_variant_falls_back_to_base(self, screen: pygame.Surface, tmp_path: Path) -> None:
        """包内无变体文件：受损机甲回退基名精灵（输出与现状一致）。"""
        service = make_single_mech_battle()
        service.mechs["px-01"].take_damage(60)
        renderer = Renderer(screen, asset_pack=make_damaged_pack(tmp_path, with_dam_variant=False))
        renderer.render(service, 0.0, 0.0)
        assert screen.get_at((300, 300))[:3] == MADCAT_COLOR[:3]

    def test_no_pack_damaged_mech_stays_procedural(self, screen: pygame.Surface) -> None:
        """无资产包：受损机甲保持阵营色块（V2 阈值路径不触碰程序化降级）。"""
        service = make_single_mech_battle()
        service.mechs["px-01"].take_damage(60)
        renderer = Renderer(screen)
        renderer.render(service, 0.0, 0.0)
        assert screen.get_at((300, 300))[:3] == PLAYER_COLOR


def make_heading_pack(tmp_path: Path) -> MC2AssetPack:
    """朝向帧 mini pack：TestMech 箭头 .ase + 配套纹理 + madcat 静态卡（真资产零入库）。"""
    root = tmp_path / "mc2-heading"
    write_arrow_ase(root / "Data" / "TGL" / "MadCat.ase")
    write_arrow_texture(root / "Data" / "TGL" / "128" / "MadcatRGB.tga")
    write_static_card(root / "Data" / "TGL" / "128" / "MadCat.tga", STATIC_CARD_COLOR)
    pack = MC2AssetPack.load(root)
    assert pack is not None
    return pack


def mech_sprite_pixels(screen: pygame.Surface, center: tuple[int, int], size: int = 32) -> list[pygame.Color]:
    """精灵包围盒内全部像素（朝向帧/脚影断言用）。"""
    cx, cy = center
    half = size // 2
    return [screen.get_at((x, y)) for x in range(cx - half, cx + half) for y in range(cy - half, cy + half)]


class TestHeadingFrameRender:
    """P4 朝向帧渲染链：.ase 命中按 heading 选帧 + 脚影；未命中回退静态卡。"""

    def test_mech_with_ase_uses_heading_frame(self, screen: pygame.Surface, tmp_path: Path) -> None:
        """px-01 命中 .ase 朝向帧：精灵区出现箭头纹理色（非静态卡色）。"""
        service = make_single_mech_battle()
        renderer = Renderer(screen, asset_pack=make_heading_pack(tmp_path))
        renderer.render(service, 0.0, 0.0)
        pixels = mech_sprite_pixels(screen, (300, 300))
        arrow_hits = sum(1 for p in pixels if p.r > 150 and p.b > 150 and p.g < 120)
        assert arrow_hits > 0  # 箭头纹理色（紫）在帧内
        assert all(p[:3] != STATIC_CARD_COLOR[:3] for p in pixels)  # 未走静态卡

    def test_heading_change_swaps_frame(self, screen: pygame.Surface, tmp_path: Path) -> None:
        """heading 0 → 2：帧切换（朝向帧像素分布互异）。"""
        service = make_single_mech_battle()
        pack = make_heading_pack(tmp_path)
        renderer = Renderer(screen, asset_pack=pack)
        service.mechs["px-01"].heading = 0
        renderer.render(service, 0.0, 0.0)
        north = pygame.image.tobytes(screen, "RGB")
        screen.fill((0, 0, 0))
        service.mechs["px-01"].heading = 2
        renderer.render(service, 0.0, 0.0)
        east = pygame.image.tobytes(screen, "RGB")
        assert north != east

    def test_heading_frame_draws_shadow(self, screen: pygame.Surface, tmp_path: Path) -> None:
        """朝向帧命中时铺半透明椭圆脚影：精灵下缘混合像素暗于纯草地且非遮罩全色。"""
        service = make_single_mech_battle()
        renderer = Renderer(screen, asset_pack=make_heading_pack(tmp_path))
        renderer.render(service, 0.0, 0.0)
        # 脚影中心 ≈ (300, 300 + 32//4) = (300, 308)：精灵内部偏下，草地上混合
        pixel = screen.get_at((300, 308))
        assert pixel[:3] != (58, 96, 44)  # 非纯程序化草底
        assert pixel.a == 255

    def test_static_card_path_has_no_shadow(self, screen: pygame.Surface, tmp_path: Path) -> None:
        """静态卡路径（包内无 .ase）：无脚影——存量像素输出不变。"""
        service = make_single_mech_battle()
        renderer = Renderer(screen, asset_pack=make_damaged_pack(tmp_path))  # 仅静态卡
        renderer.render(service, 0.0, 0.0)
        pixels = mech_sprite_pixels(screen, (300, 308), 8)
        assert all(p[:3] == MADCAT_COLOR[:3] for p in pixels)  # 精灵下方仍是静态卡色（无暗化）

    def test_fallback_without_ase_keeps_static_card(self, screen: pygame.Surface, tmp_path: Path) -> None:
        """包内无 .ase：_unit_sprite 回退静态卡（V2 口径不变），朝向帧未激活。"""
        service = make_single_mech_battle()
        pack = make_damaged_pack(tmp_path)
        renderer = Renderer(screen, asset_pack=pack)
        mech = service.mechs["px-01"]
        sprite = renderer._unit_sprite(mech)
        assert sprite is not None
        assert sprite.get_at((16, 16))[:3] == MADCAT_COLOR[:3]
        assert renderer._heading_frames_active(mech) is False

    def test_damaged_variant_not_used_on_heading_path(self, screen: pygame.Surface, tmp_path: Path) -> None:
        """ase 帧路径不接 damaged 变体（P4 §8 偏离 2）：压血后仍为朝向帧（箭头色）。"""
        service = make_single_mech_battle()
        service.mechs["px-01"].take_damage(60)  # hull_ratio 0.4 ≤ 0.5
        renderer = Renderer(screen, asset_pack=make_heading_pack(tmp_path))
        renderer.render(service, 0.0, 0.0)
        pixels = mech_sprite_pixels(screen, (300, 300))
        assert any(p.r > 150 and p.b > 150 and p.g < 120 for p in pixels)  # 仍箭头帧
