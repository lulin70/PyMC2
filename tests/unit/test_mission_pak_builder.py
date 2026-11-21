"""P3 战局构建单测（docs/P3_DESIGN_20261121.md §3/§4）：类号分派/阵营/裁剪窗/高程量化/降级。

全部走合成 .pak fixture（CI 无资产）；坐标/高程断言按逆向上游公式推演（Terrain.cpp
worldUnitsPerVertex=128 + worldToTile 逆映射；tile (row,col) 中心世界坐标
x=(col+0.5)×128−half、y=half−(row+0.5)×128，half=vertices×128/2）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers.pak_fixture import (
    make_mission_fit_text,
    make_object_types_pak,
    make_objects_blob,
    make_pak,
    make_terrain_blob,
)
from helpers.pak_fixture import (
    tile_world as _tile_world,
)
from pymc2.domain.entities.mech import MAP_TILES, Faction, Mech
from pymc2.domain.entities.turret import Turret
from pymc2.domain.entities.vehicle import Vehicle
from pymc2.infrastructure.mission_pak import MissionTerrain, TerrainGrid
from pymc2.services.mission_pak_builder import (
    MissionPakBuildError,
    build_battle_from_mission_pak,
    quantize_window,
)

Z = 4  # PacketStorageType.ZLIB int 值（fixture 规格用）

MECH, VEHICLE, TURRET_CLASS = 2, 3, 21


def _side_elevations(side: int, value: float = 100.0) -> list[list[float]]:
    return [[value for _ in range(side)] for _ in range(side)]


def _ramp_elevations(side: int = 60) -> list[list[float]]:
    """西低东高线性坡（每列 +10m）：量化真值表用确定性坡面。"""
    return [[float(c * 10) for c in range(side)] for _ in range(side)]


class _MissionDirs:
    """合成任务目录布局：Missions/<name>.pak(+.fit) + Objects/object2.pak（CLI 推导约定）。"""

    def __init__(self, tmp_path: Path, object_types: list[tuple[int, tuple[int, str]]] | None) -> None:
        self.missions = tmp_path / "Data" / "Missions"
        self.objects_dir = tmp_path / "Data" / "Objects"
        self.missions.mkdir(parents=True)
        self.object_types_path: Path | None = None
        if object_types is not None:
            self.objects_dir.mkdir(parents=True)
            self.object_types_path = self.objects_dir / "object2.pak"
            self.object_types_path.write_bytes(make_object_types_pak(object_types))

    def write_mission(
        self,
        name: str,
        elevations: list[list[float]],
        fit_parts: list[tuple[int, int, float, float, float, int, bool, bool]],
        objects: list[tuple[int, float, float, float, float, int, int, int]] | None = None,
    ) -> Path:
        pak = make_pak(
            [
                (Z, make_terrain_blob(elevations)),
                (Z, make_objects_blob(objects or [])),
            ]
        )
        (self.missions / f"{name}.pak").write_bytes(pak)
        (self.missions / f"{name}.fit").write_text(make_mission_fit_text(fit_parts), encoding="utf-8")
        return self.missions / f"{name}.pak"


def test_class_dispatch_and_factions(tmp_path: Path) -> None:
    """类号分派真值表：2→Mech / 3→Vehicle / 21→Turret；team0/PlayerPart→PLAYER 其余→ENEMY。"""
    dirs = _MissionDirs(
        tmp_path,
        [
            (10, (MECH, "Werewolf")),
            (11, (VEHICLE, "Centipede")),
            (12, (TURRET_CLASS, "GaussTurret")),
            (13, (0, "PalmTree")),
        ],
    )
    pak = dirs.write_mission(
        "m",
        _side_elevations(60),
        [
            (1, 10, *_tile_world(30, 30), 180.0, 0, True, True),  # 玩家机甲
            (2, 11, *_tile_world(31, 30), 0.0, 1, False, True),  # 敌方载具
            (3, 12, *_tile_world(29, 30), 0.0, 1, False, True),  # 敌方炮塔
            (4, 13, *_tile_world(32, 30), 0.0, 1, False, True),  # 树 → 跳过
            (5, 10, *_tile_world(33, 30), 0.0, 1, False, False),  # inactive → 跳过
        ],
    )
    result = build_battle_from_mission_pak(pak, dirs.missions / "m.fit", dirs.object_types_path)
    service = result.service
    werewolf = service.get("werewolf-01")
    centipede = service.get("centipede-02")
    gauss = service.get("gaussturret-03")
    assert isinstance(werewolf, Mech) and werewolf.faction is Faction.PLAYER
    assert isinstance(centipede, Vehicle) and centipede.faction is Faction.ENEMY
    assert isinstance(gauss, Turret) and gauss.faction is Faction.ENEMY and gauss.mobile is False
    assert len(service.mechs) == 3 and result.units_placed == 3
    assert any("unsupported object class 0" in note for note in result.skipped_units)
    assert any("inactive" in note for note in result.skipped_units)
    assert result.degraded is False


def test_part_positions_map_to_crop_window(tmp_path: Path) -> None:
    """MC2 tile (row,col) → PyMC2 tile (col−c0, row−r0)，单位落 tile 中心（128wu=32px）。"""
    dirs = _MissionDirs(tmp_path, [(10, (MECH, "thor")), (11, (VEHICLE, "storm"))])
    pak = dirs.write_mission(
        "m",
        _side_elevations(60),
        [
            (1, 10, *_tile_world(31, 29), 0.0, 0, True, True),  # MC2 tile (29, 31)
            (2, 11, *_tile_world(29, 31), 0.0, 1, False, True),  # MC2 tile (31, 29)
        ],
    )
    result = build_battle_from_mission_pak(pak, dirs.missions / "m.fit", dirs.object_types_path)
    thor = result.service.get("thor-01")
    storm = result.service.get("storm-02")
    assert thor is not None and storm is not None
    # 两台可同窗（rows/cols 29..31 跨度 3 < 40）：最大覆盖穷举 tie-break 取西北角 (0, 0)
    assert result.crop_origin == (0, 0)
    assert thor.tile == (31, 29)  # (tile_x, tile_y) = (col−c0, row−r0)
    assert storm.tile == (29, 31)
    assert (thor.x, thor.y) == (31 * 32 + 16.0, 29 * 32 + 16.0)


def test_crop_window_clamped_to_map_edge(tmp_path: Path) -> None:
    """单位靠图缘（tile 55/59）：含它的最早窗位 (16,16)（55−39），单位落窗内。"""
    dirs = _MissionDirs(tmp_path, [(10, (MECH, "thor"))])
    pak = dirs.write_mission("m", _side_elevations(60), [(1, 10, *_tile_world(55, 55), 0.0, 1, False, True)])
    result = build_battle_from_mission_pak(pak, dirs.missions / "m.fit", dirs.object_types_path)
    assert result.crop_origin == (16, 16)
    thor = result.service.get("thor-01")
    assert thor is not None and thor.tile == (55 - 16, 55 - 16)


def test_out_of_window_units_skipped(tmp_path: Path) -> None:
    """单位分布超过 40 tile 跨度 → 窗外跳过留痕（§6.2），窗内照常布置。"""
    dirs = _MissionDirs(tmp_path, [(10, (MECH, "thor"))])
    pak = dirs.write_mission(
        "m",
        _side_elevations(60),
        [
            (1, 10, *_tile_world(5, 5), 0.0, 0, True, True),  # 西北角（玩家）
            (2, 10, *_tile_world(55, 55), 0.0, 1, False, True),  # 东南角
            (3, 10, *_tile_world(30, 30), 0.0, 1, False, True),  # 中央
        ],
    )
    result = build_battle_from_mission_pak(pak, dirs.missions / "m.fit", dirs.object_types_path)
    # 最大覆盖 = 2（任一 40×40 窗无法同时含 (5,5) 与 (55,55)）；tie-break 玩家方多者
    # → 窗 (0,0) 含 (5,5)+(30,30)，(55,55) 出窗跳过
    assert result.crop_origin == (0, 0)
    assert result.units_placed == 2
    assert result.service.get("thor-01") is not None and result.service.get("thor-03") is not None
    assert result.service.get("thor-02") is None
    outside = [note for note in result.skipped_units if "outside 40x40 crop window" in note]
    assert len(outside) == 1


def test_pak_static_turrets_placed(tmp_path: Path) -> None:
    """pak packet 1 静态炮塔（类 21）入局；team<0 中立 → ENEMY 守卫、team0 → PLAYER（§6.7）。"""
    dirs = _MissionDirs(
        tmp_path,
        [(10, (MECH, "thor")), (11, (TURRET_CLASS, "SRMTurret")), (12, (1, "Nav1")), (13, (0, "Tree"))],
    )
    objects = [
        (11, *_tile_world(31, 30), 100.0, 45.0, 0, -1, -1),  # 中立炮塔
        (12, *_tile_world(32, 30), 100.0, 0.0, 0, -1, -1),  # 建筑 → 跳过
        (13, *_tile_world(33, 30), 100.0, 0.0, 0, -1, -1),  # 树 → 跳过
        (11, *_tile_world(29, 30), 100.0, 0.0, 0, 0, -1),  # 玩家方炮塔
    ]
    pak = dirs.write_mission("m", _side_elevations(60), [(1, 10, *_tile_world(30, 30), 0.0, 0, True, True)], objects)
    result = build_battle_from_mission_pak(pak, dirs.missions / "m.fit", dirs.object_types_path)
    units = result.service.mechs
    enemy_turret = units.get("turret-00")
    friendly_turret = units.get("turret-03")
    assert isinstance(enemy_turret, Turret) and enemy_turret.faction is Faction.ENEMY
    assert isinstance(friendly_turret, Turret) and friendly_turret.faction is Faction.PLAYER
    assert len(units) == 3  # 建筑/树不入局
    # 候选全在 rows 30 / cols 29..31：滑窗 tie-break 取 (0, 0)，炮塔 PyMC2 tile = MC2 tile
    assert enemy_turret.tile == (31, 30)
    assert friendly_turret.tile == (29, 30)


def test_elevation_quantization(tmp_path: Path) -> None:
    """高程量化真值表：西低东高坡 → 窗内 min-max 线性 0..3；全平地形 → 全 0。"""
    dirs = _MissionDirs(tmp_path, [(10, (MECH, "thor"))])
    pak = dirs.write_mission("m", _ramp_elevations(60), [(1, 10, *_tile_world(30, 30), 0.0, 0, True, True)])
    result = build_battle_from_mission_pak(pak, dirs.missions / "m.fit", dirs.object_types_path)
    rows = result.service.elevation.rows
    assert len(rows) == MAP_TILES and all(len(row) == MAP_TILES for row in rows)
    # 单台单位 → 滑窗 (0, 0) → 列 0..39 → 原始 0..390m；首列 0、末列 3、单调不减
    first_row = rows[0]
    assert first_row[0] == 0 and first_row[-1] == 3
    assert all(later >= earlier for earlier, later in zip(first_row, first_row[1:], strict=False))
    assert result.elevation_min == 0.0 and result.elevation_max == 390.0
    assert not result.service.elevation.is_flat()

    dirs2 = _MissionDirs(tmp_path / "b", [(10, (MECH, "thor"))])
    pak2 = dirs2.write_mission("m2", _side_elevations(60, 250.0), [(1, 10, *_tile_world(30, 30), 0.0, 0, True, True)])
    result2 = build_battle_from_mission_pak(pak2, dirs2.missions / "m2.fit", dirs2.object_types_path)
    assert result2.service.elevation.is_flat()  # span==0 → 全 0
    assert result2.elevation_min == 250.0 and result2.elevation_max == 250.0


def test_quantize_window_values() -> None:
    """quantize_window 线性真值：round((v−min)/span×3)；span==0 → 0。"""
    grid = TerrainGrid(vertices_per_side=41, rows=tuple(tuple(float(c) for c in range(40)) for _ in range(40)))
    mission = MissionTerrain(terrain=grid, objects=())
    rows = quantize_window(mission, (0, 0)).rows
    assert rows[0][0] == 0 and rows[0][13] == 1 and rows[0][26] == 2 and rows[0][39] == 3
    flat = TerrainGrid(vertices_per_side=41, rows=tuple((7.5,) * 40 for _ in range(40)))
    assert all(
        value == 0 for row in quantize_window(MissionTerrain(terrain=flat, objects=()), (0, 0)).rows for value in row
    )


def test_degraded_without_object_types(tmp_path: Path) -> None:
    """类型表缺失降级（§6.9）：parts 全按 Mech + 静态物全跳过 + degraded 留痕。"""
    dirs = _MissionDirs(tmp_path, object_types=None)
    pak = dirs.write_mission(
        "m",
        _side_elevations(60),
        [(1, 45, *_tile_world(30, 30), 0.0, 0, True, True), (2, 328, *_tile_world(31, 30), 0.0, 1, False, True)],
        objects=[(11, *_tile_world(32, 30), 100.0, 0.0, 0, -1, -1)],
    )
    result = build_battle_from_mission_pak(pak, dirs.missions / "m.fit", None)
    assert result.degraded is True
    assert all(isinstance(unit, Mech) for unit in result.service.mechs.values())  # 载具 part 也降级
    assert len(result.service.mechs) == 2
    assert result.skipped_units == ()  # 静态物静默跳过（无类型表无法逐条判类）


def test_no_units_placed_raises(tmp_path: Path) -> None:
    """全部单位 inactive → 硬失败（不返回空战局）。"""
    dirs = _MissionDirs(tmp_path, [(10, (MECH, "thor"))])
    pak = dirs.write_mission("m", _side_elevations(60), [(1, 10, *_tile_world(30, 30), 0.0, 0, True, False)])
    with pytest.raises(MissionPakBuildError, match="no units placed"):
        build_battle_from_mission_pak(pak, dirs.missions / "m.fit", dirs.object_types_path)


def test_map_smaller_than_window_raises(tmp_path: Path) -> None:
    """地图 tile < 40 → 硬失败（官方任务 ≥59 tile，合成数据守门）。"""
    dirs = _MissionDirs(tmp_path, [(10, (MECH, "thor"))])
    pak = dirs.write_mission("m", _side_elevations(30), [(1, 10, 0.0, 0.0, 0.0, 0, True, True)])
    with pytest.raises(MissionPakBuildError, match="too small"):
        build_battle_from_mission_pak(pak, dirs.missions / "m.fit", dirs.object_types_path)
