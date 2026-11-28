"""W3 斜投影 Renderer 接入单测：双模式分支 / 地形缩放 / 高程偏移 / 两段式逆变换。

设计（docs/W3_DESIGN_20261128.md §3/§4）：top 逐字节恒等存量；isometric 分支集中
于 Renderer 3 方法；拾取两段式回补误差 ≤1 tile。
"""

from __future__ import annotations

import math

import pygame
import pytest

from pymc2.domain.elevation import ElevationGrid
from pymc2.domain.entities.mech import MAP_SIZE_PX, MAP_TILES, TILE_SIZE_PX
from pymc2.presentation.projection import ISO_Y_SCALE
from pymc2.presentation.renderer import ELEVATION_STEP_PX, Renderer

SCREEN_SIZE = (1280, 720)


def _grid(elev: dict[tuple[int, int], int]) -> ElevationGrid:
    rows = [[0] * MAP_TILES for _ in range(MAP_TILES)]
    for (x, y), value in elev.items():
        rows[y][x] = value
    return ElevationGrid(rows)


@pytest.fixture()
def screen() -> pygame.Surface:
    pygame.init()
    try:
        yield pygame.display.set_mode(SCREEN_SIZE)
    finally:
        pygame.quit()


class TestTopIdentity:
    def test_world_to_screen_top_matches_legacy(self, screen: pygame.Surface) -> None:
        """top 模式 world_to_screen 逐点等于存量公式（FR-1.3 回归钉）。"""
        renderer = Renderer(screen)
        assert not renderer.is_isometric
        for x, y in [(0.0, 0.0), (100.0, 200.0), (640.5, 33.25)]:
            assert renderer.world_to_screen(x, y, 10.0, 20.0) == (
                int(x - 10.0),
                int(y - 20.0),
            )

    def test_screen_to_world_top_matches_legacy(self, screen: pygame.Surface) -> None:
        """top 模式 screen_to_world 恒等存量 _world_pos 公式（screen + camera）。"""
        renderer = Renderer(screen)
        assert renderer.screen_to_world(100, 200, 10.0, 20.0) == (110.0, 220.0)

    def test_terrain_top_untouched(self, screen: pygame.Surface) -> None:
        """top 模式地形表面尺寸/偏移与存量一致（MAP_SIZE_PX 方形，flat 偏移 0）。"""
        renderer = Renderer(screen)
        assert renderer._terrain.get_size() == (MAP_SIZE_PX, MAP_SIZE_PX)
        assert renderer._terrain_y_offset == 0
        assert renderer.terrain_screen_height() == MAP_SIZE_PX


class TestIsoTerrain:
    def test_terrain_scaled(self, screen: pygame.Surface) -> None:
        """isometric 地形表面纵向压缩，偏移同比缩放（设计 §3）。"""
        renderer = Renderer(screen, projection="isometric")
        assert renderer.is_isometric
        assert renderer._terrain.get_size() == (MAP_SIZE_PX, int(MAP_SIZE_PX * ISO_Y_SCALE))
        assert renderer._terrain_y_offset == 0  # flat 无 terrace 预留

    def test_terrain_hills_offset_scaled(self, screen: pygame.Surface) -> None:
        """丘陵 terrace 版面偏移同比缩放（ELEVATION_MAX × STEP × K 取整）。"""
        top = Renderer(screen, elevation=_grid({(5, 5): 2}))
        iso = Renderer(screen, elevation=_grid({(5, 5): 2}), projection="isometric")
        assert top._terrain_y_offset > 0
        assert iso._terrain_y_offset == int(top._terrain_y_offset * ISO_Y_SCALE)

    def test_terrain_screen_height_dual_mode(self, screen: pygame.Surface) -> None:
        """isometric 有效纵向跨度 = 缩放高 + 缩放偏移（相机钳制边界）。"""
        hills = _grid({(5, 5): 2})
        top = Renderer(screen, elevation=hills)
        iso = Renderer(screen, elevation=hills, projection="isometric")
        assert top.terrain_screen_height() == top._terrain.get_height() + top._terrain_y_offset
        assert iso.terrain_screen_height() == iso._terrain.get_height() + iso._terrain_y_offset
        assert iso.terrain_screen_height() < top.terrain_screen_height()


class TestElevOffset:
    def test_elev_offset_dual_mode(self, screen: pygame.Surface) -> None:
        """高程偏移：top = level×STEP（存量恒等）；isometric 同比压缩。"""
        tile = (5, 5)
        top = Renderer(screen, elevation=_grid({tile: 2}))
        iso = Renderer(screen, elevation=_grid({tile: 2}), projection="isometric")
        assert top._elev_offset(tile) == 2 * ELEVATION_STEP_PX
        assert iso._elev_offset(tile) == int(2 * ELEVATION_STEP_PX * ISO_Y_SCALE)


class TestInverseTwoPass:
    def test_two_pass_inverse_hill_within_one_tile(self, screen: pygame.Surface) -> None:
        """两段式逆变换：2 级高差 tile 回补后落点 ≤1 tile（FR-3.1）。"""
        hill = (8, 6)
        renderer = Renderer(screen, elevation=_grid({hill: 2}), projection="isometric")
        cam_x, cam_y = 100.0, 50.0
        wx = hill[0] * TILE_SIZE_PX + TILE_SIZE_PX / 2
        wy = hill[1] * TILE_SIZE_PX + TILE_SIZE_PX / 2
        # 正向（实体全链）：压缩 + 高程偏移
        sx, sy = renderer.world_to_screen(wx, wy, cam_x, cam_y)
        sy -= renderer._elev_offset(hill)
        # 逆变换（拾取全链）
        rx, ry = renderer.screen_to_world(sx, sy, cam_x, cam_y)
        assert math.hypot(rx - wx, ry - wy) <= TILE_SIZE_PX

    def test_two_pass_inverse_without_correction_would_drift(self, screen: pygame.Surface) -> None:
        """无回补时高差产生系统性漂移 >1 tile（回补必要性的量化证明）。"""
        hill = (8, 6)
        renderer = Renderer(screen, elevation=_grid({hill: 3}), projection="isometric")
        cam_x, cam_y = 100.0, 50.0
        wx = hill[0] * TILE_SIZE_PX + TILE_SIZE_PX / 2
        wy = hill[1] * TILE_SIZE_PX + TILE_SIZE_PX / 2
        sx, sy = renderer.world_to_screen(wx, wy, cam_x, cam_y)
        sy -= renderer._elev_offset(hill)
        from pymc2.presentation.projection import screen_to_world_iso

        _, wy_uncorrected = screen_to_world_iso(sx, sy, cam_x, cam_y)
        drift = abs(wy_uncorrected - wy)
        assert drift > TILE_SIZE_PX  # 3 级 × 16px × 0.866 ≈ 41.6px > 32px（1 tile）

    def test_flat_iso_inverse_accurate(self, screen: pygame.Surface) -> None:
        """平坦地形 isometric 逆变换精确（回补项为 0）。"""
        renderer = Renderer(screen, projection="isometric")
        rx, ry = renderer.screen_to_world(300, 200, 100.0, 50.0)
        assert math.isclose(rx, 400.0, abs_tol=1e-9)
        assert abs(ry - (200 / ISO_Y_SCALE + 50.0)) < 1e-9
