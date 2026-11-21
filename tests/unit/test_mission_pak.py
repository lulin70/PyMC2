"""P3 内容提取单测（docs/P3_DESIGN_20261121.md §1.2-1.5/§4）：地形/对象表/.fit 布置/类型表。

合成 .pak fixture 覆盖全部分支（CI 无资产）；真实官方 .pak 解析为本地可选测试
（资产存在才跑，CI 恒 skip）——解析结果作为验收证据（设计文档 §2）。
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from helpers.pak_fixture import (
    make_mission_fit_text,
    make_object_type_text,
    make_object_types_pak,
    make_objects_blob,
    make_pak,
    make_terrain_blob,
)
from pymc2.infrastructure.mission_pak import (
    OBJECT_CLASS_BUILDING,
    OBJECT_CLASS_MECH,
    OBJECT_CLASS_TURRET,
    OBJECT_CLASS_VEHICLE,
    MissionPakError,
    load_mission_fit,
    load_mission_pak,
    load_object_types,
)
from pymc2.infrastructure.pak_parser import PacketStorageType

Z = PacketStorageType.ZLIB


# ---------------------------------------------------------------- 合成数据工厂


def _terrain_pak_bytes(elevations: list[list[float]]) -> bytes:
    """地形 + 空对象表双 packet（load_mission_pak 最小形态）。"""
    return make_pak([(Z, make_terrain_blob(elevations)), (Z, make_objects_blob([]))])


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def _side_elevations(side: int, value: float = 100.0) -> list[list[float]]:
    return [[value for _ in range(side)] for _ in range(side)]


# ---------------------------------------------------------------- 地形提取


def test_terrain_vertex_to_tile(tmp_path: Path) -> None:
    """60 顶点/边 → 59 tile/边（tiles = vertices − 1），tile 取左上顶点高程。"""
    elevations = [[float(r * 60 + c) for c in range(60)] for r in range(60)]
    pak = _write(tmp_path / "t.pak", _terrain_pak_bytes(elevations))
    mission = load_mission_pak(pak)
    assert mission.terrain.vertices_per_side == 60
    assert mission.terrain.tiles_per_side == 59
    assert len(mission.terrain.rows) == 59
    assert mission.terrain.rows[0][0] == 0.0  # 顶点 (0,0)
    assert mission.terrain.rows[1][2] == float(1 * 60 + 2)  # tile (1,2) = 顶点 (1,2)


def test_world_to_tile_mapping(tmp_path: Path) -> None:
    """世界坐标 ↔ tile：Terrain.cpp:265-268 逆映射（y 向南递减行号、钳制图内）。"""
    pak = _write(tmp_path / "t.pak", _terrain_pak_bytes(_side_elevations(60)))
    mission = load_mission_pak(pak)
    half = 60 * 128 / 2  # worldUnitsMapSide / 2 = 3840
    assert mission.terrain.world_to_tile(0.0, 0.0) == (30, 30)  # 地图中心
    assert mission.terrain.world_to_tile(0.0, half) == (0, 30)  # 北缘 row=0
    assert mission.terrain.world_to_tile(0.0, -half) == (58, 30)  # 南缘越界 → 钳到末行 58
    assert mission.terrain.world_to_tile(half - 1.0, 0.0) == (30, 58)  # 东缘越界 → 钳到末列 58
    assert mission.terrain.world_to_tile(-99999.0, 99999.0) == (0, 0)  # 越界钳制


def test_terrain_bad_size_rejected(tmp_path: Path) -> None:
    """非平方顶点数 / 非整 32 字节 → MissionPakError（不硬编）。"""
    truncated = make_terrain_blob(_side_elevations(60))[:-8]  # 破坏 32 字节对齐
    bad_square = _write(tmp_path / "bad.pak", make_pak([(Z, truncated), (Z, make_objects_blob([]))]))
    with pytest.raises(MissionPakError, match="not a multiple"):
        load_mission_pak(bad_square)


# ---------------------------------------------------------------- 对象表提取


def test_objects_record_layout(tmp_path: Path) -> None:
    """40 字节记录步长真值表：8 字段 + 2 padding long 全部落位（f32 用精确可表示值）。"""
    records = [(290, -1216.0, 192.0, 138.25, -65.5, 0, -1, -1), (135, 448.0, 768.0, 145.5, 45.0, 0, 2, -1)]
    pak_bytes = make_pak([(Z, make_terrain_blob(_side_elevations(60))), (Z, make_objects_blob(records))])
    mission = load_mission_pak(_write(tmp_path / "t.pak", pak_bytes))
    assert len(mission.objects) == 2
    first = mission.objects[0]
    assert (first.obj_type_num, first.x, first.y, first.z) == (290, -1216.0, 192.0, 138.25)
    assert (first.rotation_deg, first.damage, first.team_id, first.parent_id) == (-65.5, 0, -1, -1)
    assert mission.objects[1].team_id == 2 and mission.objects[1].rotation_deg == 45.0


def test_objects_count_mismatch_padding_bytes(tmp_path: Path) -> None:
    """count 与实际记录区不符：截断取完整记录（原版顺序读语义），不抛错不硬编。"""
    blob = make_objects_blob([(1, 2.0, 3.0, 4.0, 5.0, 0, 0, 0)]) + b"\xff\xff\xff\xff"  # 4 字节尾残
    pak_bytes = make_pak([(Z, make_terrain_blob(_side_elevations(60))), (Z, blob)])
    mission = load_mission_pak(_write(tmp_path / "t.pak", pak_bytes))
    assert len(mission.objects) == 1  # 尾残字节不构成完整记录，忽略


def test_objects_negative_count_rejected(tmp_path: Path) -> None:
    """count 为负 → MissionPakError。"""
    pak_bytes = make_pak([(Z, make_terrain_blob(_side_elevations(60))), (Z, struct.pack("<i", -5) + b"\x00" * 40)])
    with pytest.raises(MissionPakError, match="negative"):
        load_mission_pak(_write(tmp_path / "t.pak", pak_bytes))


# ---------------------------------------------------------------- 任务 .fit 布置


def test_fit_parts_parse(tmp_path: Path) -> None:
    """[PartN] 枚举：objNum/team/position/rotation/player/active 全字段。"""
    fit = _write(
        tmp_path / "m.fit",
        make_mission_fit_text(
            [
                (1, 45, 2496.0, -1941.3, 180.0, 0, True, True),
                (2, 328, 2880.0, 2112.0, 0.0, 1, False, True),
                (3, 246, 0.0, 0.0, 90.0, 1, False, False),  # inactive → 保留记录（builder 过滤）
            ]
        ).encode("utf-8"),
    )
    parts = load_mission_fit(fit)
    assert [p.part_index for p in parts] == [1, 2, 3]
    assert parts[0].object_number == 45 and parts[0].team_id == 0 and parts[0].player is True
    assert parts[0].x == 2496.0 and parts[0].y == -1941.3
    assert parts[1].object_number == 328 and parts[1].team_id == 1
    assert parts[2].active is False


def test_fit_garbage_lines_skipped(tmp_path: Path) -> None:
    """坏行/注释/缺键逐条跳过（缺键取缺省值，不抛错）。"""
    text = (
        "FITini\n"
        "[Part1]\n"
        "ul ObjectNumber = 45 // 注释\n"
        "garbage line without equals\n"
        "c TeamID = 0\n"
        "f PositionX = 128.0\n"
        "f PositionY = -256.0\n"
        "[Other]\n"
        "l Junk = 1\n"
        "FITEnd\n"
    )
    fit = _write(tmp_path / "m.fit", text.encode("utf-8"))
    parts = load_mission_fit(fit)
    assert len(parts) == 1
    assert parts[0].object_number == 45
    assert parts[0].rotation_deg == 0.0  # 缺键缺省
    assert parts[0].active is False  # Active/Exists 缺失 → False（builder 跳过）


# ---------------------------------------------------------------- object2.pak 类型表


def test_object_types_scan(tmp_path: Path) -> None:
    """全表扫描：packet N = 类型 N；空洞 NUL 跳过；类号/名称解析。"""
    pak = _write(
        tmp_path / "object2.pak",
        make_object_types_pak(
            [
                (2, (OBJECT_CLASS_MECH, "werewolf")),
                (3, (OBJECT_CLASS_VEHICLE, "centipede")),
                (7, (OBJECT_CLASS_TURRET, "gauss")),
                (9, (OBJECT_CLASS_BUILDING, "nav1")),
            ]
        ),
    )
    types = load_object_types(pak)
    assert types[2].object_class == OBJECT_CLASS_MECH and types[2].name == "werewolf"
    assert types[3].object_class == OBJECT_CLASS_VEHICLE
    assert types[7].object_class == OBJECT_CLASS_TURRET
    assert types[9].object_class == OBJECT_CLASS_BUILDING
    assert types[2].appearance_name == "werewolf"  # 未传 appearance → 回退基名
    assert 1 not in types  # NUL 空洞不入表（类型 0 恒 NULL，objtype.cpp:324）


def test_object_types_bad_packet_skipped(tmp_path: Path) -> None:
    """个别坏 packet（unpackedSize 谎报）→ 跳过不拖垮全表。"""
    good = make_object_type_text(OBJECT_CLASS_MECH, "thor").encode("ascii")
    data = make_pak([(Z, good), (Z, good)])
    # 篡改 packet 0 的 unpackedSize 头（数据区起点 = 8 + 2*4）
    broken = bytearray(data)
    struct.pack_into("<I", broken, 16, 12345)
    pak = _write(tmp_path / "object2.pak", bytes(broken))
    types = load_object_types(pak)
    assert types[1].object_class == OBJECT_CLASS_MECH  # packet 0 坏 → 跳过；packet 1 正常
    assert 0 not in types


# ---------------------------------------------------------------- 真实 .pak 本地可选测试


MISSIONS_DIR = Path.home() / ".pymc2" / "mc2-assets" / "Data" / "missions"
OBJECTS_PAK = Path.home() / ".pymc2" / "mc2-assets" / "Data" / "Objects" / "object2.pak"
_real_available = pytest.mark.skipif(
    not (MISSIONS_DIR / "tut_01.pak").exists() or not (MISSIONS_DIR / "mc2_01.pak").exists(),
    reason="本地 MC2 缓存无官方任务 .pak（~/.pymc2/mc2-assets），CI 恒跳过",
)


@_real_available
def test_real_tut_01_pak() -> None:
    """真实官方 tut_01.pak：60×60 顶点 / 高程 0..440.6 / 4 静态对象（设计文档 §2 证据）。"""
    mission = load_mission_pak(MISSIONS_DIR / "tut_01.pak")
    assert mission.terrain.vertices_per_side == 60
    elevations = [value for row in mission.terrain.rows for value in row]
    assert 0.0 < max(elevations) < 500.0  # 实测 0..440.6（米级原始值）
    assert len(mission.objects) == 4
    assert all(obj.team_id == -1 for obj in mission.objects)  # 中立静态物


@_real_available
def test_real_mc2_01_pak() -> None:
    """真实官方 mc2_01.pak：100×100 顶点 / 1000 静态对象（4+1000×40=40004 精确吻合）。"""
    mission = load_mission_pak(MISSIONS_DIR / "mc2_01.pak")
    assert mission.terrain.vertices_per_side == 100
    assert len(mission.objects) == 1000
    assert len(mission.terrain.rows) == 99
    elevations = [value for row in mission.terrain.rows for value in row]
    assert min(elevations) > 150.0 and max(elevations) < 750.0  # 实测 200..710


@_real_available
def test_real_mc2_01_fit_parts() -> None:
    """真实 mc2_01.fit：29 parts = 6 机甲 + 23 载具（object2.pak 类号解析）。"""
    parts = load_mission_fit(MISSIONS_DIR / "mc2_01.fit")
    assert len(parts) == 29
    assert all(part.active for part in parts)
    types = load_object_types(OBJECTS_PAK)
    classes = [types[part.object_number].object_class for part in parts]
    assert classes.count(OBJECT_CLASS_MECH) == 6
    assert classes.count(OBJECT_CLASS_VEHICLE) == 23
    player_parts = [part for part in parts if part.player]
    assert len(player_parts) == 3 and all(part.team_id == 0 for part in player_parts)


@_real_available
def test_real_object_types_table() -> None:
    """真实 object2.pak：1188 packets，类分布含机甲 34 / 载具 62 / 炮塔 34（设计文档 §1.4）。"""
    types = load_object_types(OBJECTS_PAK)
    distribution: dict[int, int] = {}
    for definition in types.values():
        distribution[definition.object_class] = distribution.get(definition.object_class, 0) + 1
    assert distribution.get(OBJECT_CLASS_MECH) == 34
    assert distribution.get(OBJECT_CLASS_VEHICLE) == 62
    assert distribution.get(OBJECT_CLASS_TURRET) == 34
    assert 45 in types and types[45].object_class == OBJECT_CLASS_MECH  # mc2_01 Part1 = werewolf
