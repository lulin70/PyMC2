"""plan_decorations 单测（Wave V1 环境点缀）：确定性撒点纯函数。

覆盖：同 seed 同结果、非道路 tile 候选、已占用格排除、数量钳制、kind 循环取用。
纯装饰语义：产物只进渲染层，不阻挡移动不参与命中（无战斗逻辑依赖）。
"""

from __future__ import annotations

from pymc2.presentation.renderer import (
    _DECORATION_COUNT,
    _DECORATION_KINDS,
    _ROAD_COL,
    _ROAD_ROW,
    MAP_TILES,
    plan_decorations,
)


def test_same_seed_same_result() -> None:
    """同 seed 同结果（确定性），不同 seed 结果不同（随机性）。"""
    first = plan_decorations(seed=42)
    assert plan_decorations(seed=42) == first
    assert plan_decorations(seed=43) != first


def test_excludes_road_tiles() -> None:
    """撒点避开道路行/列（十字路网）。"""
    for tile_x, _tile_y, _kind in plan_decorations():
        assert tile_x != _ROAD_COL
        assert _tile_y != _ROAD_ROW


def test_excludes_occupied_tiles() -> None:
    """已占用格（机甲出生格等）不撒点。"""
    occupied = {(0, 0), (1, 1), (2, 2)}
    decorations = plan_decorations(occupied_tiles=occupied)
    assert all((tile_x, tile_y) not in occupied for tile_x, tile_y, _kind in decorations)


def test_default_count_is_fifteen() -> None:
    assert len(plan_decorations()) == _DECORATION_COUNT


def test_count_clamped_to_candidates() -> None:
    """候选不足时钳制：占满全部非道路格后只返回剩余空格数。"""
    occupied = {
        (x, y)
        for y in range(MAP_TILES)
        for x in range(MAP_TILES)
        if y != _ROAD_ROW and x != _ROAD_COL
    }  # 占满非道路格 → 候选为空
    assert plan_decorations(occupied_tiles=occupied) == ()


def test_kinds_cycle_through_palette() -> None:
    """kind 按调色板循环取用：15 个点缀对 5 种 kind 各 3 个。"""
    kinds = [kind for _x, _y, kind in plan_decorations()]
    assert set(kinds) == set(_DECORATION_KINDS)
    assert kinds == [_DECORATION_KINDS[i % len(_DECORATION_KINDS)] for i in range(len(kinds))]
