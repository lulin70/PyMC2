"""Wave V3 buttonlayout 解析与 UI 面板渲染单测：全合成 fixture（真实资产零入库）。

覆盖：fit 段解析（注释/类型前缀/十六进制/引号剥离）、ButtonLayout 构建（UV 裁切、
零尺寸与缺图集部件跳过、FileName 大小写与 .tga 后缀归一化、负坐标 bbox、三文本
槽位锚点与 dropteam stride）、pack 级 button_layout lazy 缓存与降级（无 fit/空静态段）、
panel_base_offset 画布偏移数学、Renderer 面板预合成与回退（无布局 → HUD 路径，
渲染输出与无资产包逐字节一致）、面板槽位文字落位与超额选中 +N 计数。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pygame
import pytest
from PIL import Image

from pymc2.domain.entities.mech import Faction, Mech, Weapon
from pymc2.infrastructure.mc2_assets import (
    BUTTON_LAYOUT_FIT_RELPATH,
    ButtonLayout,
    MC2AssetPack,
    _build_button_layout,
    parse_fit_sections,
)
from pymc2.presentation.renderer import Renderer, panel_base_offset
from pymc2.services.battle_service import BattleService

# 合成布局 fixture 色板（单色 UV 区让像素断言无需推算）
PALETTE_COLOR = (70, 90, 140, 255)
COMP_TOP_COLOR = (150, 120, 60, 255)
SHEET_BASE_COLOR = (30, 34, 40, 255)

# 与官方 buttonlayout1280.fit [Statics] 全量 23 部件同构的合成布局：段号/坐标/UV
# 对照官方表（docs/V3_DESIGN_20261120.md §2），FileName 映射到三张合成图集；
# 覆盖零尺寸跳过（Static2/18/20/21，其中 20/21 官方语义会盖住断言区）、缺图集
# 跳过（Static3）、.tga 后缀大写引用（Static8）、负坐标补边（Static22）四类容错。
SYNTHETIC_FIT = """FITini
// synthetic in-game UI layout fixture (mirrors official buttonlayout1280.fit)
[HiresOffsets]
l xOffset = 128
l yOffset = 256

[ResourcePoints]
l RPTextLeft = 9
l RPTextTop = 746

[HelpText]
l HelpTextLeft = 250
l HelpTextBottom = 601

[Statics]
l staticCount = 23

[Static0]
l XLocation = 331
l YLocation = 603
l Width = 256
l Height = 16
st FileName = "MCUI_TestA"
l UNormal = 0
l VNormal = 0
l UWidth = 256
l VHeight = 16

[Static1]
l XLocation = 867
l YLocation = 561
l Width = 15
l Height = 203
st FileName = "MCUI_TestA"
l UNormal = 0
l VNormal = 16
l UWidth = 15
l VHeight = 203

[Static2]
l XLocation = 908
l YLocation = 410
l Width = 0
l Height = 0
st FileName = "MCUI_Compass"
l UNormal = 0
l VNormal = 0
l UWidth = 0
l VHeight = 0

[Static3]
l XLocation = 800
l YLocation = 600
l Width = 40
l Height = 40
st FileName = "MCUI_Missing"
l UNormal = 0
l VNormal = 0
l UWidth = 40
l VHeight = 40

[Static4]
l XLocation = 227
l YLocation = 556
l Width = 18
l Height = 24
st FileName = "MCUI_TestA"
l UNormal = 196
l VNormal = 16
l UWidth = 18
l VHeight = 24

[Static5]
l XLocation = 848
l YLocation = 561
l Width = 19
l Height = 19
st FileName = "MCUI_TestA"
l UNormal = 214
l VNormal = 16
l UWidth = 19
l VHeight = 19

[Static6]
l XLocation = 212
l YLocation = 556
l Width = 15
l Height = 208
st FileName = "MCUI_TestA"
l UNormal = 233
l VNormal = 16
l UWidth = 15
l VHeight = 208

[Static7]
l XLocation = 0
l YLocation = 556
l Width = 4
l Height = 208
st FileName = "MCUI_TestA"
l UNormal = 248
l VNormal = 16
l UWidth = 4
l VHeight = 208

[Static8]
l XLocation = 855
l YLocation = 546
l Width = 169
l Height = 15
st FileName = "MCUI_TestB.TGA"
l UNormal = 21
l VNormal = 32
l UWidth = 169
l VHeight = 15

[Static9]
l XLocation = 867
l YLocation = 764
l Width = 157
l Height = 4
st FileName = "MCUI_TestA"
l UNormal = 21
l VNormal = 47
l UWidth = 157
l VHeight = 4

[Static10]
l XLocation = 227
l YLocation = 619
l Width = 160
l Height = 149
st FileName = "MCUI_TestA"
l UNormal = 21
l VNormal = 51
l UWidth = 160
l VHeight = 149

[Static11]
l XLocation = 387
l YLocation = 619
l Width = 160
l Height = 149
st FileName = "MCUI_TestA"
l UNormal = 21
l VNormal = 51
l UWidth = 160
l VHeight = 149

[Static12]
l XLocation = 547
l YLocation = 619
l Width = 160
l Height = 149
st FileName = "MCUI_TestA"
l UNormal = 21
l VNormal = 51
l UWidth = 160
l VHeight = 149

[Static13]
l XLocation = 707
l YLocation = 619
l Width = 160
l Height = 149
st FileName = "MCUI_TestA"
l UNormal = 21
l VNormal = 51
l UWidth = 160
l VHeight = 149

[Static14]
l XLocation = 227
l YLocation = 580
l Width = 104
l Height = 39
st FileName = "MCUI_TestA"
l UNormal = 21
l VNormal = 200
l UWidth = 104
l VHeight = 39

[Static15]
l XLocation = 762
l YLocation = 580
l Width = 105
l Height = 39
st FileName = "MCUI_TestA"
l UNormal = 125
l VNormal = 200
l UWidth = 105
l VHeight = 39

[Static16]
l XLocation = 0
l YLocation = 764
l Width = 227
l Height = 4
st FileName = "MCUI_TestA"
l UNormal = 0
l VNormal = 239
l UWidth = 227
l VHeight = 4

[Static17]
l XLocation = 0
l YLocation = 545
l Width = 234
l Height = 11
st FileName = "MCUI_TestA"
l UNormal = 0
l VNormal = 243
l UWidth = 234
l VHeight = 11

[Static18]
l XLocation = 908
l YLocation = 410
l Width = 0
l Height = 0
st FileName = "MCUI_Compass"
l UNormal = 0
l VNormal = 0
l UWidth = 0
l VHeight = 0

[Static19]
l XLocation = 973
l YLocation = 726
l Width = 42
l Height = 36
st FileName = "MCUI_TestB"
l UNormal = 126
l VNormal = 36
l UWidth = 42
l VHeight = 36

[Static20]
l XLocation = 229
l YLocation = 624
l Width = 0
l Height = 0
st FileName = "MCUI_TestC"
l UNormal = 42
l VNormal = 36
l UWidth = 0
l VHeight = 0

[Static21]
l XLocation = 1018
l YLocation = 546
l Width = 0
l Height = 0
st FileName = "MCUI_TestC"
l UNormal = 83
l VNormal = 0
l UWidth = 0
l VHeight = 0

[Static22]
l XLocation = -53
l YLocation = 545
l Width = 57
l Height = 223
st FileName = "mcui_testc"
l UNormal = 0
l VNormal = 0
l UWidth = 57
l VHeight = 223

FITEnd
"""


@pytest.fixture()
def pygame_context() -> Iterator[pygame.Surface]:
    pygame.init()
    screen = pygame.display.set_mode((1280, 720))
    yield screen
    pygame.quit()


def write_sheet(path: Path, size: tuple[int, int], color: tuple[int, int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, color).save(path)


def make_layout_pack(tmp_path: Path, *, with_fit: bool = True) -> MC2AssetPack:
    """合成布局 pack：三张图集（图集 A 的 UV 区涂 palette 色）+ 可选布局文件。

    with_fit 两种取值用不同子目录（同一 tmp_path 双 pack 互不串目录）。"""
    root = tmp_path / ("layout" if with_fit else "layout_plain")
    art = root / "Data" / "Art"
    sheet_a = Image.new("RGBA", (256, 256), SHEET_BASE_COLOR)
    for x in range(21, 181):
        for y in range(51, 200):
            sheet_a.putpixel((x, y), PALETTE_COLOR)
    art.mkdir(parents=True)
    sheet_a.save(art / "MCUI_TestA.tga")
    write_sheet(art / "MCUI_TestB.tga", (256, 256), COMP_TOP_COLOR)
    write_sheet(art / "MCUI_TestC.tga", (128, 256), (90, 90, 90, 255))
    if with_fit:
        (art / "buttonlayout1280.fit").write_text(SYNTHETIC_FIT, encoding="ascii")
    pack = MC2AssetPack.load(root)
    assert pack is not None
    return pack


def layout_from_fit(text: str, sheets: dict[str, pygame.Surface]) -> ButtonLayout | None:
    """单测辅助：绕过文件级入口直接走构建函数（pack.button_layout 的内部路径）。"""
    return _build_button_layout(parse_fit_sections(text), sheets)


def default_sheets() -> dict[str, pygame.Surface]:
    return {
        "mcui_testa": pygame.Surface((256, 256)),
        "mcui_testb": pygame.Surface((256, 256)),
        "mcui_testc": pygame.Surface((128, 256)),
    }


def _six_mech_service() -> BattleService:
    """六台玩家机甲战局（覆盖 dropteam 4 槽上限与 +N 超额计数）。"""
    service = BattleService()
    for index in range(6):
        service.add_mech(
            Mech(
                f"px-{index + 1:02d}",
                Faction.PLAYER,
                200.0 + index * 40,
                300.0,
                weapon=Weapon("mlaser", 10, 380.0, 1.5),
            )
        )
    return service


def _has_pixel_other_than(
    screen: pygame.Surface, x: int, y: int, width: int, height: int, color: tuple[int, int, int]
) -> bool:
    """矩形条带内存在异于底色的像素（面板文字落位断言）。"""
    for offset_y in range(height):
        for offset_x in range(width):
            if screen.get_at((x + offset_x, y + offset_y))[:3] != color:
                return True
    return False


def _has_bright_pixel(screen: pygame.Surface, x: int, y: int, width: int, height: int) -> bool:
    """矩形条带内存在亮色像素（r>200：槽位文字亮色，战场草地/路面远低于该亮度）。"""
    for offset_y in range(height):
        for offset_x in range(width):
            if screen.get_at((x + offset_x, y + offset_y))[0] > 200:
                return True
    return False


class TestParseFitSections:
    def test_sections_and_types(self) -> None:
        sections = parse_fit_sections(SYNTHETIC_FIT)
        assert sections["ResourcePoints"] == {"RPTextLeft": 9, "RPTextTop": 746}
        assert sections["HiresOffsets"] == {"xOffset": 128, "yOffset": 256}
        assert sections["Static0"]["XLocation"] == 331  # 顶边框左段（官方表同位）
        assert sections["Static0"]["FileName"] == "MCUI_TestA"  # 引号剥离

    def test_comments_and_non_l_st_lines_ignored(self) -> None:
        text = "FITini\n[Sec] // comment\nl Color = 0xff000000 // hex\nb outline = FALSE\nstray line\n"
        assert parse_fit_sections(text) == {"Sec": {"Color": 0xFF000000}}  # 十六进制 int、b 前缀/裸行忽略

    def test_empty_and_garbage_input(self) -> None:
        assert parse_fit_sections("") == {}
        assert parse_fit_sections("no sections here\nl orphan = 1\n") == {}


class TestBuildButtonLayout:
    def test_pieces_uv_crop_and_skips(self) -> None:
        layout = layout_from_fit(SYNTHETIC_FIT, default_sheets())
        assert layout is not None
        # 零尺寸 Static2/18/20/21 与缺图集 Static3 跳过 → 18 部件，命名保留原始段序号
        assert {piece.name for piece in layout.pieces} == {
            "Static0",
            "Static1",
            "Static4",
            "Static5",
            "Static6",
            "Static7",
            "Static8",
            "Static9",
            "Static10",
            "Static11",
            "Static12",
            "Static13",
            "Static14",
            "Static15",
            "Static16",
            "Static17",
            "Static19",
            "Static22",
        }
        palette = next(piece for piece in layout.pieces if piece.name == "Static10")
        assert palette.dest == (227, 619)
        assert palette.surface.get_size() == (160, 149)  # UV 裁切尺寸

    def test_canvas_bbox_includes_negative_dest(self) -> None:
        layout = layout_from_fit(SYNTHETIC_FIT, default_sheets())
        assert layout is not None
        assert layout.canvas_origin == (-53, 545)  # 负坐标补边进入 bbox
        assert layout.canvas_size == (1077, 223)

    def test_slots_and_stride(self) -> None:
        layout = layout_from_fit(SYNTHETIC_FIT, default_sheets())
        assert layout is not None
        assert layout.rp_text_pos == (9, 746)
        assert layout.help_text_pos == (250, 601)
        assert layout.dropteam_origin == (227, 619)
        assert layout.dropteam_stride == 160  # Static11.x − Static10.x
        assert layout.compalette_top_pos == (855, 546)

    def test_no_statics_returns_none(self) -> None:
        text = "FITini\n[ResourcePoints]\nl RPTextLeft = 9\n"
        assert layout_from_fit(text, {"mcui_testa": pygame.Surface((8, 8))}) is None

    def test_uv_out_of_bounds_clipped(self) -> None:
        text = (
            "FITini\n[Static0]\nl XLocation = 0\nl YLocation = 0\nl Width = 64\nl Height = 64\n"
            'st FileName = "Sheet"\nl UNormal = 200\nl VNormal = 200\nl UWidth = 128\nl VHeight = 128\n'
        )
        layout = layout_from_fit(text, {"sheet": pygame.Surface((256, 256))})
        assert layout is not None
        assert layout.pieces[0].surface.get_size() == (56, 56)  # 越界 UV 裁到图集边缘


class TestPackButtonLayout:
    def test_pack_with_fit_resolves_layout(self, pygame_context: pygame.Surface, tmp_path: Path) -> None:
        pack = make_layout_pack(tmp_path)
        layout = pack.button_layout
        assert isinstance(layout, ButtonLayout)
        assert len(layout.pieces) == 18
        assert pack.button_layout is layout  # lazy 缓存（同一对象）

    def test_pack_without_fit_returns_none(self, pygame_context: pygame.Surface, tmp_path: Path) -> None:
        pack = make_layout_pack(tmp_path, with_fit=False)
        assert pack.button_layout is None
        assert pack.button_layout is None  # 缓存不重复探测


class TestPanelBaseOffset:
    def test_offsets_match_hires_semantics(self) -> None:
        assert panel_base_offset((1280, 720)) == (128, -48)  # 水平居中 + 底边锚定
        assert panel_base_offset((1024, 768)) == (0, 0)  # 设计画布原尺寸
        assert panel_base_offset((800, 600)) == (-112, -168)  # 小于画布同样成立

    def test_fit_repath_points_at_art_dir(self) -> None:
        assert BUTTON_LAYOUT_FIT_RELPATH.as_posix() == "Art/buttonlayout1280.fit"


class TestRendererPanel:
    def test_renderer_builds_panel_surface(self, pygame_context: pygame.Surface, tmp_path: Path) -> None:
        renderer = Renderer(pygame_context, asset_pack=make_layout_pack(tmp_path))
        assert renderer._panel is not None
        assert renderer._panel.get_size() == (1077, 223)  # 布局 bbox 预合成

    def test_renderer_fallback_without_layout_is_byte_identical_to_no_pack(
        self, pygame_context: pygame.Surface, tmp_path: Path
    ) -> None:
        pack = make_layout_pack(tmp_path, with_fit=False)
        renderer_plain = Renderer(pygame_context)
        renderer_fallback = Renderer(pygame_context, asset_pack=pack)
        assert renderer_fallback._panel is None
        renderer_plain.render(_six_mech_service(), 0, 0)
        plain = pygame.image.tobytes(pygame_context, "RGB")
        renderer_fallback.render(_six_mech_service(), 0, 0)
        # 无布局 pack → 回退现状 HUD：渲染输出与无资产包逐字节一致
        assert pygame.image.tobytes(pygame_context, "RGB") == plain

    def test_panel_pixels_slots_and_over_count(self, pygame_context: pygame.Surface, tmp_path: Path) -> None:
        renderer = Renderer(pygame_context, asset_pack=make_layout_pack(tmp_path))
        service = _six_mech_service()
        service.selected_ids = list(service.mechs.keys())
        renderer.render(service, 0, 0, auto_command=True, mission_id="m9", requisition_points=77)
        # 面板部件落位：palette 槽 0 内部（避开文字行 y 577..623）= UV 区 palette 色
        assert pygame_context.get_at((415, 651))[:3] == PALETTE_COLOR[:3]
        # 任务/托管条：M + AUTO + 超额 +2 → compalette top 条带出现非底色像素
        assert _has_pixel_other_than(pygame_context, 991, 499, 160, 14, COMP_TOP_COLOR[:3])
        # RP 槽位：透出战场区上的亮色文字（草地/路面基色远低于文字亮度）
        assert _has_bright_pixel(pygame_context, 137, 698, 90, 16)
        # dropteam 槽 0 第 1 行（机甲 id）文字出现
        assert _has_pixel_other_than(pygame_context, 363, 577, 140, 14, PALETTE_COLOR[:3])
        # 槽 4（第 5 台选中）不绘制（4 dropteam 上限）→ 条带无文字亮像素（透出战场）
        assert not _has_bright_pixel(pygame_context, 975, 577, 140, 14)

    def test_slots_skip_when_content_absent(self, pygame_context: pygame.Surface, tmp_path: Path) -> None:
        renderer = Renderer(pygame_context, asset_pack=make_layout_pack(tmp_path))
        renderer.render(_six_mech_service(), 0, 0)  # 无选中/无任务/RP → 槽位不画
        assert not _has_pixel_other_than(pygame_context, 991, 499, 160, 14, COMP_TOP_COLOR[:3])
        assert not _has_bright_pixel(pygame_context, 137, 698, 90, 16)
        assert not _has_pixel_other_than(pygame_context, 363, 577, 140, 14, PALETTE_COLOR[:3])
