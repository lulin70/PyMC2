"""P1-A 高程系统 e2e：hills 模式跑真实战局 + flat 回归切片。

hills 切片（程序化地形 + 确定性丘陵，真资产零入库）：create_game 接线 → run_frame
真实渲染 → 像素断言（顶面上移进北邻区 + 南侧深色崖壁）→ 机甲爬 1 级 tile 落位 →
爬升 >1 的 MOVE_TO 被拒（回执 = dispatcher 失败惯例：指令入 order_log）。
flat 回归切片：无高程注入 vs flat 网格渲染逐字节一致 + 移动公式逐帧不变 + 道路像素。
截图存 docs/assets/uat_p1a/。StaticRng(0.5) 使全部开火未命中（确定性、无人阵亡）。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pygame
import pytest

import pymc2.main as main_module
from pymc2.domain.elevation import ELEVATION_MAX, ElevationGrid, generate_hills
from pymc2.domain.entities.mech import TILE_SIZE_PX
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType, TacticalOrder
from pymc2.presentation.renderer import (
    _CLIFF_COLOR,
    _ROAD_COLOR,
    _TINT_PER_LEVEL,
    ELEVATION_STEP_PX,
    WINDOW_HEIGHT_PX,
    WINDOW_WIDTH_PX,
    Renderer,
    _tile_color,
)
from pymc2.services.battle_service import create_m2_battle

SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "assets" / "uat_p1a"
SCREEN_SIZE = (WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX)
PLAYER_SPAWN_TILE = (6, 10)
PLAYER_BODY_COLOR = (70, 130, 230)


class StaticRng:
    """固定随机源（0.5 → 全部开火未命中）：战局确定性、无人阵亡、移动断言不受战斗干扰。"""

    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


@pytest.fixture()
def hills_game() -> Iterator[main_module.Game]:
    """dummy 驱动下的交互战局 + 确定性丘陵（main 默认接线复刻：service.elevation → Renderer）。"""
    pygame.init()
    try:
        screen = pygame.display.set_mode(SCREEN_SIZE)
        service = create_m2_battle(rng=StaticRng(0.5))
        service.elevation = generate_hills()
        yield main_module.create_game(screen, service=service)
    finally:
        pygame.quit()


def save_screenshot(screen: pygame.Surface, name: str) -> Path:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / name
    pygame.image.save(screen, str(path))
    assert path.stat().st_size > 0
    return path


def _tile_center(tile: tuple[int, int]) -> tuple[float, float]:
    return (tile[0] * TILE_SIZE_PX + TILE_SIZE_PX / 2, tile[1] * TILE_SIZE_PX + TILE_SIZE_PX / 2)


def _cliff_tile_in_view(grid: ElevationGrid) -> tuple[int, int, int]:
    """开局视口内（row 1..7，避开机甲行）南邻更低的高程 tile（顶面色异于北邻证位移）。"""
    for tile_y in range(1, 8):
        for tile_x in range(40):
            if tile_x == 25:
                continue
            e = grid.elevation_at((tile_x, tile_y))
            if e < 1 or grid.elevation_at((tile_x, tile_y + 1)) >= e:
                continue
            if _tile_color(tile_x, tile_y) == _tile_color(tile_x, tile_y - 1):
                continue
            return (tile_x, tile_y, e)
    raise AssertionError("no visible cliff tile in opening viewport (hills seed changed?)")


def _climbable_tile_near_spawn(grid: ElevationGrid) -> tuple[int, int]:
    """玩家出生点 Chebyshev ≤8 内高程恰为 1 的 tile（Δ+1 可爬目标）。"""
    for tile_y in range(2, 19):
        for tile_x in range(40):
            if (
                grid.elevation_at((tile_x, tile_y)) == 1
                and max(abs(tile_x - PLAYER_SPAWN_TILE[0]), abs(tile_y - PLAYER_SPAWN_TILE[1])) <= 8
            ):
                return (tile_x, tile_y)
    raise AssertionError("no climbable tile near spawn (hills seed changed?)")


def _unclimbable_tile(grid: ElevationGrid) -> tuple[int, int]:
    """任意高程 = ELEVATION_MAX 的 tile（相对出生 0 级 Δ=3 > 1 → 必拒）。"""
    for tile_y in range(40):
        for tile_x in range(40):
            if grid.elevation_at((tile_x, tile_y)) == ELEVATION_MAX:
                return (tile_x, tile_y)
    raise AssertionError("no max-elevation tile (hills seed changed?)")


def test_hills_terrain_slice(hills_game: main_module.Game) -> None:
    game = hills_game
    grid = game.renderer._elevation
    assert grid is not None and not grid.is_flat()
    px = game.service.get("px-01")
    assert px is not None
    saved: list[Path] = []

    # --- 步骤 0：开局帧——terrace 像素断言（顶面上移进北邻区 + 南侧深色崖壁）---
    assert game.service.select_at(*_tile_center(PLAYER_SPAWN_TILE))  # 选中：HUD ELEV + 选中框
    main_module.run_frame(game, 1 / 60)
    cliff_x, cliff_y, cliff_e = _cliff_tile_in_view(grid)
    # 顶面内避开脚线（屏幕 y 可能被视口上缘裁剪 → 钳制），证顶面上移
    top_face_y = max(2, cliff_y * TILE_SIZE_PX - cliff_e * ELEVATION_STEP_PX + 8)
    lift = _TINT_PER_LEVEL * cliff_e  # V1.2：顶面按级提亮（ADD）
    expected_top = tuple(min(255, c + lift) for c in _tile_color(cliff_x, cliff_y))
    assert game.screen.get_at((cliff_x * TILE_SIZE_PX + 16, top_face_y))[:3] == expected_top
    e_south = grid.elevation_at((cliff_x, cliff_y + 1))
    band_y = cliff_y * TILE_SIZE_PX + TILE_SIZE_PX - ((cliff_e + e_south) * ELEVATION_STEP_PX) // 2  # 崖壁条带中部
    assert game.screen.get_at((cliff_x * TILE_SIZE_PX + 16, band_y))[:3] == _CLIFF_COLOR
    saved.append(save_screenshot(game.screen, "01_hills_opening.png"))

    # --- 步骤 1：爬 1 级 tile 落位（Δ+1 放行；上坡减速帧不拦截到达）---
    target = _climbable_tile_near_spawn(grid)
    order = TacticalOrder(
        order_type=OrderType.MOVE_TO_POINT,
        unit_ids=("px-01",),
        origin=OrderOrigin.PLAYER,
        target_tile=target,
        target_unit_id=None,
    )
    assert game.dispatcher.dispatch(order, game.service)
    for _ in range(600):
        main_module.run_frame(game, 0.05)
        if px.move_target is None:
            break
    assert px.move_target is None
    assert px.position == _tile_center(target)
    assert grid.elevation_at(px.tile) == 1  # 爬 1 级落位
    # 机甲随 tile 高程同步上移（e=1 → 上移 STEP px，程序化方块阵营色）
    mx, my = int(px.x), int(px.y)
    assert game.screen.get_at((mx, my - ELEVATION_STEP_PX))[:3] == PLAYER_BODY_COLOR
    saved.append(save_screenshot(game.screen, "02_climbed_hill.png"))

    # --- 步骤 2：爬升 >1 目标被拒（回执 = dispatcher 失败惯例：指令入 order_log）---
    reject_tile = _unclimbable_tile(grid)
    assert grid.elevation_at(px.tile) == 1  # 当前 1 级 → 目标 3 级 Δ=2 > 1
    reject_order = TacticalOrder(
        order_type=OrderType.MOVE_TO_POINT,
        unit_ids=("px-01",),
        origin=OrderOrigin.PLAYER,
        target_tile=reject_tile,
        target_unit_id=None,
    )
    orders_before = len(game.dispatcher.order_log)
    assert not game.dispatcher.dispatch(reject_order, game.service)
    assert px.move_target is None  # 未落任何移动意图
    assert len(game.dispatcher.order_log) == orders_before + 1  # 指令仍入日志
    main_module.run_frame(game, 1 / 60)  # 重渲一帧反映拒绝后状态
    saved.append(save_screenshot(game.screen, "03_climb_rejected.png"))

    # --- 验收留痕：3 张截图全部落盘 ---
    assert len(saved) == 3
    assert all(p.exists() and p.stat().st_size > 0 for p in saved)


def test_flat_terrain_regression() -> None:
    """flat 回归切片：渲染输出与未注入高程逐字节一致 + 移动公式逐帧不变 + 关键像素。"""
    pygame.init()
    try:
        screen = pygame.display.set_mode(SCREEN_SIZE)
        service = create_m2_battle(rng=StaticRng(0.5))
        game = main_module.create_game(screen, service=service)
        assert game.renderer._elevation.is_flat()
        assert game.renderer._terrain_y_offset == 0
        assert game.renderer._terrain.get_size() == (1280, 1280)  # 无 terrace 加高

        # 渲染字节一致：flat 网格 Renderer vs 未注入高程 Renderer（同一 service 同一帧）
        game.renderer.render(service, 0, 0)
        flat_bytes = pygame.image.tobytes(screen, "RGB")
        Renderer(screen).render(service, 0, 0)
        assert pygame.image.tobytes(screen, "RGB") == flat_bytes

        # 移动公式逐帧不变：flat 下 96px/s × dt，无高程加成（纵列移动避开静态 px-02）
        px = service.get("px-01")
        assert px is not None
        assert service.select_at(*_tile_center(PLAYER_SPAWN_TILE))
        dispatcher = game.dispatcher
        assert dispatcher.dispatch(
            TacticalOrder(
                order_type=OrderType.MOVE_TO_POINT,
                unit_ids=("px-01",),
                origin=OrderOrigin.PLAYER,
                target_tile=(6, 5),
                target_unit_id=None,
            ),
            service,
        )
        for _ in range(10):
            main_module.run_frame(game, 0.05)
        assert px.y == pytest.approx(336 - 10 * 96 * 0.05)  # 每帧恰 4.8px（存量公式）
        # 关键像素：程序化道路色不变（tile (30,10) 非虚线区）
        assert screen.get_at((30 * TILE_SIZE_PX + 4, 10 * TILE_SIZE_PX + 16))[:3] == _ROAD_COLOR
        save_screenshot(screen, "04_flat_regression.png")
    finally:
        pygame.quit()
