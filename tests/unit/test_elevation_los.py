"""P1-B 视线判定单测：LOS 真值表 + is_flat 快路径恒通（docs/P1B_DESIGN_20261121.md §1）。

遮挡模型：眼高 EYE_HEIGHT_LEVELS=1 级——视线高度在两端点间线性插值（射手高程+1 →
目标高程+1），Bresenham 途经 tile（不含端点）高程 ≥ 插值视线高度 → 遮挡。
"""

from __future__ import annotations

from pymc2.domain.elevation import ELEVATION_MAX, ElevationGrid, generate_hills
from pymc2.domain.entities.mech import MAP_TILES


def _grid(elev: dict[tuple[int, int], int]) -> ElevationGrid:
    rows = [[0] * MAP_TILES for _ in range(MAP_TILES)]
    for (x, y), value in elev.items():
        rows[y][x] = value
    return ElevationGrid(rows)


class TestLosTruthTable:
    def test_flat_ground_clears(self) -> None:
        """平地通：全零网格任意两点视线恒通。"""
        grid = _grid({})
        assert grid.has_line_of_sight((2, 10), (20, 10))
        assert grid.has_line_of_sight((0, 0), (39, 39))

    def test_same_level_clears(self) -> None:
        """同级通：两端同处 1 级高地，途经 0 级谷地通（视线 2.0 高于谷地 0）。"""
        grid = _grid({(2, 5): 1, (20, 5): 1})
        assert grid.has_line_of_sight((2, 5), (20, 5))

    def test_ridge_blocks_ground_to_ground(self) -> None:
        """山背挡：0 级对 0 级，途经 1 级山脊遮挡（脊 1 ≥ 视线 1.0）。"""
        grid = _grid({(11, 10): 1})
        assert not grid.has_line_of_sight((2, 10), (20, 10))

    def test_high_ground_overlooks_low_ridge(self) -> None:
        """占据高点俯射通：射手 2 级（眼高 3），途经 1 级山脊不遮挡（脊 1 < 视线 2.0）。"""
        grid = _grid({(2, 5): 2, (11, 5): 1})
        assert grid.has_line_of_sight((2, 5), (20, 5))

    def test_max_cliff_blocks_even_from_height(self) -> None:
        """ELEVATION_MAX 峭壁隔断：1 级射手（眼高 2）对 0 级目标，3 级峭壁恒挡
        （视线 ≤ 2.0 < 峭壁 3）。"""
        grid = _grid({(2, 5): 1, (11, 5): ELEVATION_MAX})
        assert not grid.has_line_of_sight((2, 5), (20, 5))

    def test_endpoints_do_not_block(self) -> None:
        """端点不算遮挡：射手/目标所在 tile 高程不参与遮挡。"""
        grid = _grid({(2, 5): 3, (10, 5): 3})
        assert grid.has_line_of_sight((2, 5), (10, 5))

    def test_diagonal_ridge_blocks(self) -> None:
        """斜线遮挡：对角线途经 (5,5) 1 级脊遮挡 0→0 视线。"""
        grid = _grid({(5, 5): 1})
        assert not grid.has_line_of_sight((0, 0), (10, 10))

    def test_adjacent_and_same_tile_always_clear(self) -> None:
        """相邻/同格无途经 tile → 恒通（四周皆 ELEVATION_MAX 也不影响）。"""
        grid = _grid({(5, 4): 3, (6, 5): 3, (4, 5): 3, (5, 6): 3})
        assert grid.has_line_of_sight((5, 5), (6, 5))
        assert grid.has_line_of_sight((5, 5), (5, 5))

    def test_uphill_target_sight_rises(self) -> None:
        """仰射高地目标：视线沿程抬升，中段 2 级丘不挡（t=0.5 视线 2.5 > 丘 2）。"""
        grid = _grid({(11, 5): 2, (20, 5): 3})
        assert grid.has_line_of_sight((2, 5), (20, 5))

    def test_eye_height_boundary_is_ge(self) -> None:
        """边界口径（≥）：1 级射手对 1 级目标（视线 2.0）——途经 2 级脊挡（2 ≥ 2.0）、
        1 级脊不挡（1 < 2.0）。"""
        two_level_ridge = _grid({(2, 5): 1, (11, 5): 2, (20, 5): 1})
        assert not two_level_ridge.has_line_of_sight((2, 5), (20, 5))
        one_level_ridge = _grid({(2, 5): 1, (11, 5): 1, (20, 5): 1})
        assert one_level_ridge.has_line_of_sight((2, 5), (20, 5))

    def test_direction_symmetric(self) -> None:
        """视线判定对称：A→B 与 B→A 同结果（Bresenham 步进对称）。"""
        grid = _grid({(11, 10): 1})
        assert grid.has_line_of_sight((2, 10), (20, 10)) == grid.has_line_of_sight((20, 10), (2, 10))
        assert not grid.has_line_of_sight((20, 10), (2, 10))

    def test_offmap_tiles_extend_as_zero(self) -> None:
        """地图外 tile 视为 0（平面延伸）：跨界视线不崩、界外途经不遮挡；界内墙仍挡。"""
        assert _grid({}).has_line_of_sight((-2, 0), (2, 0))
        assert not _grid({(0, 0): 1}).has_line_of_sight((-2, 0), (2, 0))


class TestFlatFastPath:
    def test_flat_grid_always_clears(self) -> None:
        """is_flat 快路径恒通：全零平面任意两点（含越界）恒真（存量零行为改动保证）。"""
        grid = ElevationGrid.flat()
        assert grid.has_line_of_sight((0, 0), (39, 39))
        assert grid.has_line_of_sight((6, 10), (18, 10))
        assert grid.has_line_of_sight((-5, -5), (50, 50))

    def test_hills_spawn_corridor_clear(self) -> None:
        """丘陵网格走遮挡模型：默认战局走廊（道路行 row 10 保护区归零）通视。"""
        grid = generate_hills()
        assert not grid.is_flat()
        assert grid.has_line_of_sight((6, 10), (18, 10))
        assert grid.has_line_of_sight((6, 10), (24, 10))
