"""W3 斜投影 main 接线单测：CLI 参数 / create_game 注入 / 拾取委托 / 相机钳制。

设计（docs/W3_DESIGN_20261128.md §5）：top 缺省且钳制恒等存量；isometric 分支仅
拾取入口与相机 y 上界两处。
"""

from __future__ import annotations

import pygame
import pytest

from pymc2.domain.elevation import ElevationGrid
from pymc2.domain.entities.mech import MAP_SIZE_PX, MAP_TILES, TILE_SIZE_PX, Faction, Mech
from pymc2.main import _world_pos, create_game, parse_args, update_camera
from pymc2.services.battle_service import BattleService

SCREEN_SIZE = (1280, 720)


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


@pytest.fixture()
def screen() -> pygame.Surface:
    pygame.init()
    try:
        yield pygame.display.set_mode(SCREEN_SIZE)
    finally:
        pygame.quit()


def _game(screen: pygame.Surface, projection: str, elevation: ElevationGrid | None = None):
    service = BattleService(elevation=elevation) if elevation is not None else BattleService()
    mech = _mech_at((10, 10))
    service.add_mech(mech)
    service.selected_ids = [mech.id]
    return create_game(screen, service=service, projection=projection)


class TestCli:
    def test_default_top(self) -> None:
        """缺省 projection="top"（FR-4.1）。"""
        assert parse_args([]).projection == "top"

    def test_isometric_flag(self) -> None:
        """--projection isometric 生效。"""
        assert parse_args(["--projection", "isometric"]).projection == "isometric"

    def test_create_game_injects_renderer(self, screen: pygame.Surface) -> None:
        """create_game projection 透传 Renderer（e2e 注入路径）。"""
        game = _game(screen, "isometric")
        assert game.renderer.is_isometric
        game_top = _game(screen, "top")
        assert not game_top.renderer.is_isometric


class TestWorldPos:
    def test_top_identity(self, screen: pygame.Surface) -> None:
        """top 拾取恒等存量公式（screen + camera）。"""
        game = _game(screen, "top")
        game.camera.x, game.camera.y = 100.0, 50.0
        assert _world_pos(game, (33, 77)) == (133.0, 127.0)

    def test_iso_two_pass(self, screen: pygame.Surface) -> None:
        """isometric 拾取经 Renderer 两段式（与 renderer.screen_to_world 输出一致）。"""
        game = _game(screen, "isometric")
        game.camera.x, game.camera.y = 100.0, 50.0
        assert _world_pos(game, (33, 77)) == game.renderer.screen_to_world(33, 77, 100.0, 50.0)


class TestCameraClamp:
    def test_top_clamp_unchanged(self, screen: pygame.Surface) -> None:
        """top 相机钳制与存量公式逐值相等（FR-4.2 组成部分）。"""
        game = _game(screen, "top")
        update_camera(game)
        mech = game.service.selected
        assert mech is not None
        assert game.camera.x == max(0.0, min(mech.x - SCREEN_SIZE[0] / 2, MAP_SIZE_PX - SCREEN_SIZE[0]))
        assert game.camera.y == max(0.0, min(mech.y - SCREEN_SIZE[1] / 2, MAP_SIZE_PX - SCREEN_SIZE[1]))

    def test_iso_clamp_uses_terrain_screen_height(self, screen: pygame.Surface) -> None:
        """isometric 相机 y 上界 = terrain_screen_height − 屏高（缩放跨度）。"""
        hills = _grid({(5, 5): 2})
        game = _game(screen, "isometric", hills)
        update_camera(game)
        max_y = game.renderer.terrain_screen_height() - SCREEN_SIZE[1]
        assert game.camera.y <= max(0.0, max_y)
        # 缩放跨度严格小于 top 跨度（纵向压缩生效的程序证明）
        assert game.renderer.terrain_screen_height() < MAP_SIZE_PX + game.renderer._terrain_y_offset
