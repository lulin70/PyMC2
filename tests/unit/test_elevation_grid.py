"""ElevationGrid（P1-A）单测：数据模型边界 / 确定性丘陵生成 / climb_allowed 真值表 / JSON 容错。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pymc2.domain.elevation import (
    ELEVATION_MAX,
    ElevationGrid,
    generate_hills,
)
from pymc2.domain.entities.mech import MAP_TILES
from pymc2.domain.scripting.mission_script import mission_script_from_dict


def _grid(elev: dict[tuple[int, int], int]) -> ElevationGrid:
    """按 {(x, y): elev} 稀疏表构造网格（未列 tile 为 0）。"""
    rows = [[0] * MAP_TILES for _ in range(MAP_TILES)]
    for (x, y), value in elev.items():
        rows[y][x] = value
    return ElevationGrid(rows)


def _minimal_script(elevation: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"script_id": "s", "triggers": []}
    if elevation is not ...:  # ... = 不带键（缺失用例）
        data["elevation"] = elevation
    return data


class TestElevationGridModel:
    def test_flat_default_all_zero(self) -> None:
        grid = ElevationGrid()
        assert grid.is_flat()
        assert grid.elevation_at((0, 0)) == 0
        assert grid.elevation_at((MAP_TILES - 1, MAP_TILES - 1)) == 0
        assert ElevationGrid.flat().rows == grid.rows

    def test_elevation_at_out_of_bounds_zero(self) -> None:
        grid = _grid({(0, 0): 3, (MAP_TILES - 1, MAP_TILES - 1): 2})
        for tile in ((-1, 0), (0, -1), (MAP_TILES, 0), (0, MAP_TILES), (100, 100)):
            assert grid.elevation_at(tile) == 0

    def test_explicit_rows_values_clamped(self) -> None:
        rows = [[0] * MAP_TILES for _ in range(MAP_TILES)]
        rows[3][4] = ELEVATION_MAX + 2  # 5 → 钳到 3
        rows[5][6] = -1  # → 钳到 0
        grid = ElevationGrid(rows)
        assert grid.elevation_at((4, 3)) == ELEVATION_MAX
        assert grid.elevation_at((6, 5)) == 0
        assert not grid.is_flat()

    @pytest.mark.parametrize("rows", [[[0, 0]], [[0] * MAP_TILES for _ in range(MAP_TILES - 1)]])
    def test_explicit_rows_shape_error(self, rows: list[list[int]]) -> None:
        with pytest.raises(ValueError, match="rows"):
            ElevationGrid(rows)


class TestClimbAllowed:
    """climb_allowed 真值表：高出 1 级以上不可爬；下坡/平地不限；非相邻沿用同一公式。"""

    def test_truth_table(self) -> None:
        grid = _grid({(0, 0): 1, (1, 0): 2, (2, 0): 3, (3, 0): 0})
        assert grid.climb_allowed((0, 0), (1, 0))  # Δ+1 可爬
        assert grid.climb_allowed((1, 0), (2, 0))  # Δ+1 可爬
        assert grid.climb_allowed((0, 0), (0, 0))  # Δ0
        assert not grid.climb_allowed((0, 0), (2, 0))  # Δ+2 不可爬
        assert grid.climb_allowed((2, 0), (3, 0))  # Δ-3 下坡不限
        assert grid.climb_allowed((2, 0), (0, 0))  # Δ-2 下坡不限

    def test_non_adjacent_uses_same_formula(self) -> None:
        grid = _grid({(1, 0): 2})
        assert not grid.climb_allowed((39, 39), (1, 0))  # 非相邻 Δ+2 → 不可达
        assert grid.climb_allowed((1, 0), (39, 39))  # 非相邻 Δ-2 → 可达
        assert grid.climb_allowed((0, 0), (-1, 0))  # 地图外视为 0：Δ-1 可达

    def test_climb_cost_constants(self) -> None:
        assert ELEVATION_MAX == 3


class TestGenerateHills:
    def test_deterministic_same_seed(self) -> None:
        assert generate_hills().rows == generate_hills().rows

    def test_different_seed_differs(self) -> None:
        assert generate_hills(seed=1).rows != generate_hills(seed=2).rows

    def test_values_in_range_and_terrain_present(self) -> None:
        grid = generate_hills()
        values = {v for row in grid.rows for v in row}
        assert values <= {0, 1, 2, 3}
        assert values == {0, 1, 2, 3}  # 四档高程都有（terrace 各形态可见）
        assert not grid.is_flat()

    def test_road_and_spawn_protection_zero(self) -> None:
        grid = generate_hills()
        assert all(grid.elevation_at((x, 10)) == 0 for x in range(MAP_TILES))  # 道路行
        assert all(grid.elevation_at((25, y)) == 0 for y in range(MAP_TILES))  # 道路列
        spawns = ((6, 10), (8, 10), (10, 10), (18, 10), (20, 13), (26, 12))
        for sx, sy in spawns:
            assert all(grid.elevation_at((sx + dx, sy + dy)) == 0 for dx in (-2, 0, 2) for dy in (-2, 0, 2))


class TestMissionScriptElevationKey:
    """任务脚本可选 elevation 键：显式行数组 / hills 关键字 / 容错。"""

    def test_missing_key_no_override(self) -> None:
        assert mission_script_from_dict(_minimal_script(...)).elevation is None

    def test_hills_keyword(self) -> None:
        script = mission_script_from_dict(_minimal_script("hills"))
        assert script.elevation is not None
        assert not script.elevation.is_flat()
        assert script.elevation.rows == generate_hills().rows

    def test_explicit_rows(self) -> None:
        rows = [[0] * MAP_TILES for _ in range(MAP_TILES)]
        rows[2][3] = 2
        script = mission_script_from_dict(_minimal_script(rows))
        assert script.elevation is not None
        assert script.elevation.elevation_at((3, 2)) == 2
        assert script.elevation.elevation_at((0, 0)) == 0

    @pytest.mark.parametrize(
        "raw",
        [
            42,
            "flat",
            [[0, 0]],
            [[0] * MAP_TILES for _ in range(MAP_TILES - 1)],
            [["x"] * MAP_TILES for _ in range(MAP_TILES)],
        ],
    )
    def test_invalid_tolerated_as_no_override(self, raw: Any) -> None:
        assert mission_script_from_dict(_minimal_script(raw)).elevation is None

    def test_load_from_file_with_hills(self, tmp_path: Path) -> None:
        from pymc2.domain.scripting.mission_script import load_mission_script

        path = tmp_path / "mission.json"
        payload = _minimal_script("hills")
        payload["triggers"] = [{"id": "t", "condition": {"kind": "tick_reached", "params": {"tick": 1}}, "actions": []}]
        path.write_text(json.dumps(payload), encoding="utf-8")
        script = load_mission_script(path)
        assert script.elevation is not None
        assert not script.elevation.is_flat()
