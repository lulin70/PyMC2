"""P1-A terrace 渲染单测：版面偏移数学 / 崖壁出现条件 / y 排序权重 / HUD ELEV / CLI 开关。"""

from __future__ import annotations

import pygame
import pytest

from pymc2.domain.elevation import ELEVATION_MAX, ElevationGrid
from pymc2.domain.entities.mech import MAP_SIZE_PX, MAP_TILES, TILE_SIZE_PX, Faction, Mech
from pymc2.main import _apply_terrain, parse_args
from pymc2.presentation.renderer import (
    _CLIFF_COLOR,
    ELEVATION_STEP_PX,
    Renderer,
    _tile_color,
)
from pymc2.services.battle_service import BattleService

SCREEN_SIZE = (1280, 720)
TOP_OFFSET = ELEVATION_MAX * ELEVATION_STEP_PX  # terrace 版面顶部预留（30px）


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
        """顶面 = tile 纹理上移 e*STEP：上移区在 flat 版面属北邻 → 颜色取本 tile。"""
        hx, hy = _distinct_color_tile()
        renderer = Renderer(screen, elevation=_grid({(hx, hy): 2}))
        sample_surface_y = hy * TILE_SIZE_PX + 12  # 版面坐标：flat 语义下属北邻行
        assert renderer._terrain.get_at((hx * TILE_SIZE_PX + 16, sample_surface_y))[:3] == _tile_color(hx, hy)
        flat = Renderer(screen)._terrain
        assert flat.get_at((hx * TILE_SIZE_PX + 16, sample_surface_y - TOP_OFFSET))[:3] == _tile_color(hx, hy - 1)

    def test_south_cliff_band_drawn_when_south_lower(self, screen: pygame.Surface) -> None:
        """南邻更低 → 顶面下缘与南邻顶面上缘之间填深色崖壁（高差×STEP）。"""
        hx, hy = _distinct_color_tile()
        renderer = Renderer(screen, elevation=_grid({(hx, hy): 2}))
        band_surface_y = hy * TILE_SIZE_PX + 32 - 2 * ELEVATION_STEP_PX + TOP_OFFSET + 5  # 崖壁条带中部
        assert renderer._terrain.get_at((hx * TILE_SIZE_PX + 16, band_surface_y))[:3] == _CLIFF_COLOR

    def test_no_band_when_south_same_elevation(self, screen: pygame.Surface) -> None:
        """南邻同高 → 无崖壁：条带区被南邻上移顶面覆盖（颜色 = 南邻纹理）。"""
        hx, hy = _distinct_color_tile()
        renderer = Renderer(screen, elevation=_grid({(hx, hy): 2, (hx, hy + 1): 2}))
        band_surface_y = hy * TILE_SIZE_PX + 32 - 2 * ELEVATION_STEP_PX + TOP_OFFSET + 5
        assert renderer._terrain.get_at((hx * TILE_SIZE_PX + 16, band_surface_y))[:3] == _tile_color(hx, hy + 1)

    def test_no_band_when_south_higher(self, screen: pygame.Surface) -> None:
        """南邻更高 → 无崖壁：残条带被南邻（抬得更高）顶面覆盖。"""
        hx, hy = _distinct_color_tile()
        renderer = Renderer(screen, elevation=_grid({(hx, hy): 1, (hx, hy + 1): 3}))
        strip_surface_y = hy * TILE_SIZE_PX + 32 - ELEVATION_STEP_PX + TOP_OFFSET + 5  # 南邻更高 → 无崖壁
        assert renderer._terrain.get_at((hx * TILE_SIZE_PX + 16, strip_surface_y))[:3] == _tile_color(hx, hy + 1)

    def test_map_south_edge_counts_as_ground(self, screen: pygame.Surface) -> None:
        """地图南缘（越界视为 0）→ 边缘崖壁照画。"""
        renderer = Renderer(screen, elevation=_grid({(7, MAP_TILES - 1): 2}))
        band_surface_y = (MAP_TILES - 1) * TILE_SIZE_PX + 32 - 2 * ELEVATION_STEP_PX + TOP_OFFSET + 5
        assert renderer._terrain.get_at((7 * TILE_SIZE_PX + 16, band_surface_y))[:3] == _CLIFF_COLOR


class TestMechYSortAndOffset:
    def test_y_sort_elevation_weight_first(self, screen: pygame.Surface, monkeypatch: pytest.MonkeyPatch) -> None:
        """排序键 (elevation, y)：低层在前、高层后画（避免高层被低层前景遮挡错序）。"""
        order: list[str] = []
        original = Renderer._draw_mech

        def spy(renderer: Renderer, mech: Mech, cam_x: float, cam_y: float, *, selected: bool) -> None:
            order.append(mech.id)
            original(renderer, mech, cam_x, cam_y, selected=selected)

        monkeypatch.setattr(Renderer, "_draw_mech", spy)
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

        monkeypatch.setattr(Renderer, "_draw_mech", spy)
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
