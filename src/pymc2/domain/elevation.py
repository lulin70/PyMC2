"""ElevationGrid（P1-A）：per-tile 高程数据模型 + 确定性丘陵生成。

MC2 参照：任务地形高度图（per-tile elevation），移动代价与视线遮挡的数据源
（视线遮挡属 P1-B，本切片只落数据与移动侧语义）。

约定：
- 高程为 int 0..ELEVATION_MAX（构造时钳制）；地图外 tile 视为 0（平面延伸）。
- climb_allowed：目标 tile 比出发 tile 高出 CLIMB_MAX_DELTA 以上 → 不可达（下坡/平地
  不限）。相邻 tile 为定义口径；非相邻（MOVE_TO 端点直判，无寻路简化）沿用同一公式，
  见 docs/P1A_DESIGN_20261121.md §5。
- generate_hills：value noise（md5 哈希确定性，风格对齐 renderer 装饰撒点的 md5 确定性），
  道路 tile 与出生保护区强制归零，保证默认战局走廊可用。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from pymc2.domain.entities.mech import MAP_TILES

ELEVATION_MAX = 3
CLIMB_MAX_DELTA = 1
CLIMB_COST_PER_LEVEL = 1.5  # 进入 tile 每高出 1 级的步进耗时乘数（Δ 级复利）

# 确定性丘陵生成参数（seed 风格对齐 renderer._DECORATION_SEED 的确定性撒点）。
HILLS_SEED = 20261121
_HILLS_LATTICE_CELL = 8  # value noise 粗网格边长（tile 数）：越小丘陵越碎
_SPAWN_PROTECT_RADIUS = 2  # 出生保护区 Chebyshev 半径
# 与 renderer._ROAD_ROW/_ROAD_COL 同值（渲染层私有常量不上提避免跨层依赖，此处对齐声明）。
ROAD_TILE_ROW = 10
ROAD_TILE_COL = 25
# 全模式出生 tile 汇总（battle_service spawn + mission_chain 各关编制位置）。
_PROTECTED_SPAWN_TILES: tuple[tuple[int, int], ...] = (
    (6, 10),
    (8, 10),
    (10, 10),
    (18, 10),
    (20, 10),
    (24, 10),
    (20, 13),
    (22, 13),
    (26, 12),
    (26, 8),
)


class ElevationGrid:
    """40x40 per-tile 高程网格（int 0..ELEVATION_MAX，越界访问视为 0）。"""

    def __init__(self, rows: Sequence[Sequence[int]] | None = None) -> None:
        if rows is None:
            self._rows: tuple[tuple[int, ...], ...] = tuple((0,) * MAP_TILES for _ in range(MAP_TILES))
        else:
            if len(rows) != MAP_TILES:
                raise ValueError(f"elevation rows must have {MAP_TILES} rows, got {len(rows)}")
            normalized: list[tuple[int, ...]] = []
            for row in rows:
                if len(row) != MAP_TILES:
                    raise ValueError(f"elevation row must have {MAP_TILES} tiles, got {len(row)}")
                normalized.append(tuple(max(0, min(ELEVATION_MAX, int(v))) for v in row))
            self._rows = tuple(normalized)
        self._max_elevation = max(max(row) for row in self._rows)

    @classmethod
    def flat(cls) -> ElevationGrid:
        """全零平面网格（默认战局）。"""
        return cls()

    @property
    def rows(self) -> tuple[tuple[int, ...], ...]:
        """行优先快照（观察/断言用）。"""
        return self._rows

    def elevation_at(self, tile: tuple[int, int]) -> int:
        """tile 高程；地图外视为 0（平面延伸）。"""
        tile_x, tile_y = tile
        if 0 <= tile_x < MAP_TILES and 0 <= tile_y < MAP_TILES:
            return self._rows[tile_y][tile_x]
        return 0

    def climb_allowed(self, from_tile: tuple[int, int], to_tile: tuple[int, int]) -> bool:
        """to_tile 相对 from_tile 是否可达：高出 CLIMB_MAX_DELTA 以上不可爬（下坡/平地不限）。

        相邻 tile 为定义口径；非相邻（MOVE_TO 端点直判）沿用同一公式（无寻路简化）。
        """
        return self.elevation_at(to_tile) - self.elevation_at(from_tile) <= CLIMB_MAX_DELTA

    def is_flat(self) -> bool:
        """全零平面（O(1)，构造时预计算）：flat 快路径下移动/渲染零改动。"""
        return self._max_elevation == 0


def generate_hills(*, seed: int = HILLS_SEED) -> ElevationGrid:
    """确定性丘陵网格：value noise 双线性插值（同 seed 同结果），道路与出生保护区归零。"""
    rows: list[list[int]] = []
    for tile_y in range(MAP_TILES):
        rows.append([_noise_height(tile_x, tile_y, seed) for tile_x in range(MAP_TILES)])
    _flatten_protected(rows)
    return ElevationGrid(rows)


def _lattice_height(ix: int, iy: int, seed: int) -> int:
    """value noise 角点高度：md5 tile 哈希（与 renderer _grass_color 同款确定性风格）。"""
    digest = hashlib.md5(f"pymc2-hills:{seed}:{ix}:{iy}".encode()).digest()
    return digest[0] % (ELEVATION_MAX + 1)


def _noise_height(tile_x: int, tile_y: int, seed: int) -> int:
    """粗网格角点双线性插值 → 0..ELEVATION_MAX（相邻 tile 高差以 1 为主，成片成坡）。"""
    fx = tile_x / _HILLS_LATTICE_CELL
    fy = tile_y / _HILLS_LATTICE_CELL
    ix, iy = int(fx), int(fy)
    tx, ty = fx - ix, fy - iy
    h00 = float(_lattice_height(ix, iy, seed))
    h10 = float(_lattice_height(ix + 1, iy, seed))
    h01 = float(_lattice_height(ix, iy + 1, seed))
    h11 = float(_lattice_height(ix + 1, iy + 1, seed))
    value = h00 * (1 - tx) * (1 - ty) + h10 * tx * (1 - ty) + h01 * (1 - tx) * ty + h11 * tx * ty
    return int(round(value))


def _flatten_protected(rows: list[list[int]]) -> None:
    """保护区归零：道路 tile（默认战局走廊）+ 出生 tile Chebyshev 邻域。"""
    for tile_y in range(MAP_TILES):
        for tile_x in range(MAP_TILES):
            if tile_y == ROAD_TILE_ROW or tile_x == ROAD_TILE_COL:
                rows[tile_y][tile_x] = 0
                continue
            for spawn_x, spawn_y in _PROTECTED_SPAWN_TILES:
                if max(abs(tile_x - spawn_x), abs(tile_y - spawn_y)) <= _SPAWN_PROTECT_RADIUS:
                    rows[tile_y][tile_x] = 0
                    break
