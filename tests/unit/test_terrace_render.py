"""P1-A terrace 渲染单测：版面偏移数学 / 崖壁出现条件 / y 排序权重 / HUD ELEV / CLI 开关。

V1.2（用户反馈「高度看不出来」）：STEP 16px + 顶面分级提亮（ADD）+ 北缘亮脚线 + 东/西缘暗条——
顶面颜色断言改用同款 BLEND_RGB_ADD 复刻（_tinted），新增脚线/侧缘/提亮用例。
"""

from __future__ import annotations

import pygame
import pytest

from pymc2.domain.elevation import ELEVATION_MAX, ElevationGrid
from pymc2.domain.entities.mech import MAP_SIZE_PX, MAP_TILES, TILE_SIZE_PX, Faction, Mech
from pymc2.main import _apply_terrain, parse_args
from pymc2.presentation.renderer import (
    _CLIFF_COLOR,
    _RIM_COLOR,
    _SIDE_SHADE_WIDTH,
    _TINT_PER_LEVEL,
    ELEVATION_STEP_PX,
    Renderer,
    _tile_color,
)
from pymc2.services.battle_service import BattleService

SCREEN_SIZE = (1280, 720)
TOP_OFFSET = ELEVATION_MAX * ELEVATION_STEP_PX  # terrace 版面顶部预留（48px）


def _tinted(color: tuple[int, int, int], level: int) -> tuple[int, int, int]:
    """顶面分级提亮复刻（与 renderer._terraced_terrain_surface 同款 BLEND_RGB_ADD）。"""
    probe = pygame.Surface((1, 1))
    probe.fill(color)
    lift = _TINT_PER_LEVEL * level
    probe.fill((lift, lift, lift), special_flags=pygame.BLEND_RGB_ADD)
    return probe.get_at((0, 0))[:3]


def _grid(elev: dict[tuple[int, int], int]) -> ElevationGrid:
    rows = [[0] * MAP_TILES for _ in range(MAP_TILES)]
    for (x, y), value in elev.items():
        rows[y][x] = value
    return ElevationGrid(rows)


def _mech_at(tile: tuple[int, int], mech_id: str = "px-01") -> Mech:
    return Mech(
        mech_id,
        Faction.PLAYER,
        tile[0] * TILE_SIZE_PX + TILE_SIZE_PX / 2,
        tile[1] * TILE_SIZE_PX + TILE_SIZE_PX / 2,
    )


def _distinct_color_tile() -> tuple[int, int]:
    """找颜色与南北邻都不同的 tile（哈希微扰动下几乎必有，确定性检索）。"""
    for tile_y in range(1, MAP_TILES - 1):
        for tile_x in range(MAP_TILES):
            if _tile_color(tile_x, tile_y) != _tile_color(tile_x, tile_y - 1) and _tile_color(
                tile_x, tile_y
            ) != _tile_color(tile_x, tile_y + 1):
                return (tile_x, tile_y)
    raise AssertionError("no distinct-color tile for bake assertions")


class _RecordingFont:
    """记录 render 文本的字体包装（委托真字体；断言 HUD 状态行内容）。"""

    def __init__(self, real: pygame.font.Font, texts: list[str]) -> None:
        self._real = real
        self._texts = texts

    def render(self, text: str, *args: object, **kwargs: object) -> pygame.Surface:
        self._texts.append(text)
        return self._real.render(text, *args, **kwargs)  # type: ignore[arg-type]


@pytest.fixture()
def screen() -> pygame.Surface:
    pygame.init()
    try:
        yield pygame.display.set_mode(SCREEN_SIZE)
    finally:
        pygame.quit()


class TestFlatZeroChange:
    def test_flat_grid_byte_identical_to_no_grid(self, screen: pygame.Surface) -> None:
        """flat 网格渲染与未注入高程逐字节一致（硬约束：默认战局输出零改动）。"""
        renderer_plain = Renderer(screen)
        renderer_flat = Renderer(screen, elevation=ElevationGrid.flat())
        assert renderer_plain._terrain_y_offset == 0
        assert renderer_flat._terrain_y_offset == 0
        service = BattleService()
        mech = _mech_at((6, 10))
        service.add_mech(mech)
        service.selected_ids = [mech.id]  # 选中态含 HUD ELEV 追加路径
        renderer_plain.render(service, 0, 0)
        plain = pygame.image.tobytes(screen, "RGB")
        renderer_flat.render(service, 0, 0)
        assert pygame.image.tobytes(screen, "RGB") == plain

    def test_no_grid_hud_elev_zero(self, screen: pygame.Surface) -> None:
        renderer = Renderer(screen)
        assert renderer._font is not None
        texts: list[str] = []
        renderer._font = _RecordingFont(renderer._font, texts)  # type: ignore[assignment]
        service = BattleService()
        mech = _mech_at((6, 10))
        service.add_mech(mech)
        service.selected_ids = [mech.id]
        renderer.render(service, 0, 0)
        assert any("ELEV: 0" in t for t in texts)

    def test_no_selection_no_elev_segment(self, screen: pygame.Surface) -> None:
        renderer = Renderer(screen)
        assert renderer._font is not None
        texts: list[str] = []
        renderer._font = _RecordingFont(renderer._font, texts)  # type: ignore[assignment]
        service = BattleService()
        service.add_mech(_mech_at((6, 10)))
        renderer.render(service, 0, 0)
        assert all("ELEV" not in t for t in texts)


class TestTerraceBake:
    def test_hills_surface_size_and_offset(self, screen: pygame.Surface) -> None:
        renderer = Renderer(screen, elevation=_grid({(5, 5): 2}))
        assert renderer._terrain.get_size() == (MAP_SIZE_PX, MAP_SIZE_PX + TOP_OFFSET)
        assert renderer._terrain_y_offset == TOP_OFFSET

    def test_top_face_shifts_up_into_north_neighbor(self, screen: pygame.Surface) -> None:
        """顶面 = tile 纹理上移 e*STEP 并按级提亮：上移区颜色 = 本 tile 纹理 × tint(e)。"""
        hx, hy = _distinct_color_tile()
        renderer = Renderer(screen, elevation=_grid({(hx, hy): 2}))
        sample_surface_y = hy * TILE_SIZE_PX + 20  # 顶面内（避开北缘脚线 2px）
        assert renderer._terrain.get_at((hx * TILE_SIZE_PX + 16, sample_surface_y))[:3] == _tinted(
            _tile_color(hx, hy), 2
        )
        flat = Renderer(screen)._terrain
        assert flat.get_at((hx * TILE_SIZE_PX + 16, sample_surface_y - TOP_OFFSET))[:3] == _tile_color(hx, hy - 1)

    def test_top_face_brighter_than_untinted(self, screen: pygame.Surface) -> None:
        """分级提亮可感知：e=2 顶面亮度严格高于原纹理（用户反馈「高度看不出来」回归钉）。"""
        hx, hy = _distinct_color_tile()
        renderer = Renderer(screen, elevation=_grid({(hx, hy): 2}))
        sample_surface_y = hy * TILE_SIZE_PX + 20
        got = renderer._terrain.get_at((hx * TILE_SIZE_PX + 16, sample_surface_y))[:3]
        raw = _tile_color(hx, hy)
        assert sum(got) > sum(raw)

    def test_north_rim_line_when_north_lower(self, screen: pygame.Surface) -> None:
        """北邻更低 → 顶面北缘 2px 亮脚线（等高线语义）；北邻同高 → 无脚线。"""
        hx, hy = _distinct_color_tile()
        rim_grid = _grid({(hx, hy): 2})  # 北邻缺省 0 < 2 → 画脚线
        renderer = Renderer(screen, elevation=rim_grid)
        rim_y = hy * TILE_SIZE_PX - 2 * ELEVATION_STEP_PX + TOP_OFFSET + 1  # 顶面北缘内 1px
        assert renderer._terrain.get_at((hx * TILE_SIZE_PX + 16, rim_y))[:3] == _RIM_COLOR
        no_rim_grid = _grid({(hx, hy): 2, (hx, hy - 1): 2})  # 北邻同高 → 不画脚线
        renderer_no_rim = Renderer(screen, elevation=no_rim_grid)
        north_y = hy * TILE_SIZE_PX - 2 * ELEVATION_STEP_PX + TOP_OFFSET + 0
        assert renderer_no_rim._terrain.get_at((hx * TILE_SIZE_PX + 16, north_y))[:3] != _RIM_COLOR

    def test_east_side_shade_when_east_lower(self, screen: pygame.Surface) -> None:
        """东邻更低 → 顶面东缘 _SIDE_SHADE_WIDTH 暗条（零宽侧面近似）。"""
        hx, hy = _distinct_color_tile()
        renderer = Renderer(screen, elevation=_grid({(hx, hy): 2, (hx + 1, hy): 0}))
        top_surface_y = hy * TILE_SIZE_PX - 2 * ELEVATION_STEP_PX + TOP_OFFSET + 16  # 顶面中部
        strip_x = hx * TILE_SIZE_PX + TILE_SIZE_PX - _SIDE_SHADE_WIDTH + 1
        assert renderer._terrain.get_at((strip_x, top_surface_y))[:3] == _CLIFF_COLOR

    def test_south_cliff_band_drawn_when_south_lower(self, screen: pygame.Surface) -> None:
        """南邻更低 → 顶面下缘与南邻顶面上缘之间填深色崖壁（高差×STEP）。"""
        hx, hy = _distinct_color_tile()
        renderer = Renderer(screen, elevation=_grid({(hx, hy): 2}))
        band_surface_y = hy * TILE_SIZE_PX + 32 - 2 * ELEVATION_STEP_PX + TOP_OFFSET + 5  # 崖壁条带中部
        assert renderer._terrain.get_at((hx * TILE_SIZE_PX + 16, band_surface_y))[:3] == _CLIFF_COLOR

    def test_no_band_when_south_same_elevation(self, screen: pygame.Surface) -> None:
        """南邻同高 → 无崖壁：条带区被南邻上移顶面覆盖（颜色 = 南邻纹理×tint(2)）。"""
        hx, hy = _distinct_color_tile()
        renderer = Renderer(screen, elevation=_grid({(hx, hy): 2, (hx, hy + 1): 2}))
        band_surface_y = hy * TILE_SIZE_PX + 32 - 2 * ELEVATION_STEP_PX + TOP_OFFSET + 5
        assert renderer._terrain.get_at((hx * TILE_SIZE_PX + 16, band_surface_y))[:3] == _tinted(
            _tile_color(hx, hy + 1), 2
        )

    def test_no_band_when_south_higher(self, screen: pygame.Surface) -> None:
        """南邻更高 → 无崖壁：残条带被南邻（抬得更高）顶面覆盖。"""
        hx, hy = _distinct_color_tile()
        renderer = Renderer(screen, elevation=_grid({(hx, hy): 1, (hx, hy + 1): 3}))
        # 南邻（e=3）顶面版面区间 [(hy+1)*32-3*STEP+TOP_OFFSET, +32)：取条带内中点
        strip_surface_y = (hy + 1) * TILE_SIZE_PX - 3 * ELEVATION_STEP_PX + TOP_OFFSET + 8
        assert renderer._terrain.get_at((hx * TILE_SIZE_PX + 16, strip_surface_y))[:3] == _tinted(
            _tile_color(hx, hy + 1), 3
        )

    def test_map_south_edge_counts_as_ground(self, screen: pygame.Surface) -> None:
        """地图南缘（越界视为 0）→ 边缘崖壁照画。"""
        renderer = Renderer(screen, elevation=_grid({(7, MAP_TILES - 1): 2}))
        band_surface_y = (MAP_TILES - 1) * TILE_SIZE_PX + 32 - 2 * ELEVATION_STEP_PX + TOP_OFFSET + 5
        assert renderer._terrain.get_at((7 * TILE_SIZE_PX + 16, band_surface_y))[:3] == _CLIFF_COLOR


class TestMechYSortAndOffset:
    def test_y_sort_elevation_weight_first(self, screen: pygame.Surface, monkeypatch: pytest.MonkeyPatch) -> None:
        """排序键 (elevation, y)：低层在前、高层后画（避免高层被低层前景遮挡错序）。"""
        order: list[str] = []
        original = Renderer._draw_unit

        def spy(renderer: Renderer, mech: Mech, cam_x: float, cam_y: float, *, selected: bool) -> None:
            order.append(mech.id)
            original(renderer, mech, cam_x, cam_y, selected=selected)

        monkeypatch.setattr(Renderer, "_draw_unit", spy)
        renderer = Renderer(screen, elevation=_grid({(10, 8): 2}))
        service = BattleService(elevation=renderer._elevation)
        service.add_mech(_mech_at((6, 10), "low-y-large"))  # elev 0, y=336
        service.add_mech(_mech_at((10, 8), "high-elev-small-y"))  # elev 2, y=272
        renderer.render(service, 0, 0)
        assert order == ["low-y-large", "high-elev-small-y"]  # 高层后画（旧纯 y 排序相反）

    def test_same_elevation_keeps_y_order(self, screen: pygame.Surface, monkeypatch: pytest.MonkeyPatch) -> None:
        order: list[str] = []

        def spy(renderer: Renderer, mech: Mech, cam_x: float, cam_y: float, *, selected: bool) -> None:
            order.append(mech.id)

        monkeypatch.setattr(Renderer, "_draw_unit", spy)
        renderer = Renderer(screen, elevation=ElevationGrid.flat())
        service = BattleService()
        service.add_mech(_mech_at((6, 5), "north"))  # y=176
        service.add_mech(_mech_at((6, 8), "south"))  # y=272
        renderer.render(service, 0, 0)
        assert order == ["north", "south"]

    def test_mech_drawn_shifted_up_on_hill(self, screen: pygame.Surface) -> None:
        """机甲按所在 tile 高程同步上移（e=2 → 上移 20px；血条/标签随锚点联动）。"""
        renderer = Renderer(screen, elevation=_grid({(6, 5): 2}))
        service = BattleService(elevation=renderer._elevation)
        mech = _mech_at((6, 5))
        service.add_mech(mech)
        renderer.render(service, 0, 0)
        mx = int(mech.x)
        body_color = (70, 130, 230)  # PLAYER 阵营色（程序化方块填充）
        assert screen.get_at((mx, int(mech.y) - 2 * ELEVATION_STEP_PX))[:3] == body_color
        flat_renderer = Renderer(screen)
        flat_renderer.render(service, 0, 0)
        assert screen.get_at((mx, int(mech.y)))[:3] == body_color  # flat 语义落点在原位


class TestCliTerrainFlag:
    def test_default_flat(self) -> None:
        assert parse_args([]).terrain == "flat"

    def test_hills_flag(self) -> None:
        assert parse_args(["--terrain", "hills"]).terrain == "hills"

    def test_invalid_choice_rejected(self) -> None:
        with pytest.raises(SystemExit):
            parse_args(["--terrain", "bogus"])

    def test_apply_terrain_wiring(self) -> None:
        service = BattleService()
        _apply_terrain(service, "flat", None)
        assert service.elevation.is_flat()
        _apply_terrain(service, "hills", None)
        assert not service.elevation.is_flat()
        explicit = _grid({(3, 4): 1})
        _apply_terrain(service, "hills", explicit)
        assert service.elevation is explicit  # 脚本显式声明优先于 CLI

    def test_apply_terrain_script_override_on_flat(self) -> None:
        service = BattleService()
        explicit = _grid({(3, 4): 1})
        _apply_terrain(service, "flat", explicit)
        assert service.elevation is explicit
