"""MC2 任务 .pak 内容提取（P3 内容驱动）：地形高程 / 静态对象表 / 任务 .fit 布置 / 类型表。

MC2 参照（docs/P3_DESIGN_20261121.md §1）：

- packet 0 = 地形（Terrain.cpp:293 + Vertex.h PostcompVertex）：整包为 PostcompVertex
  数组（32 字节/顶点），``vertices_per_side = sqrt(packetSize/32)``；高程为每顶点 f32
  （米级原始值），tile 取其左上顶点（tiles/边 = vertices − 1）；
  ``worldUnitsPerVertex = 128.0``（Terrain.cpp:76），世界坐标 ↔ tile 见 Terrain.cpp:265-268
  （y 轴向南递减行号：tileRow = (half − y)/128，tileCol = (x + half)/128）。
- packet 1 = 静态对象布置表（objmgr.cpp countTerrainObjects:781-856）：首 i32 = count，
  每记录 40 字节 = 10×i32（objTypeNum + x/y/z + rotation + damage + teamId + parentId
  + 2 个 padding long）。实测 mc2_01 = 4 + 1000×40 = 40004 字节精确吻合（设计文档 §2）。
- objTypeNum 解析表 = Data/Objects/object2.pak（mission.cpp:2139 + objtype.cpp:221-373）：
  PacketFile，packet N = 类型 N 的 FITini，``[ObjectClass] l ObjectTypeNum`` = 对象类号
  （2=机甲 / 3=载具 / 21=炮塔 / 0=树 / 1=建筑…）。
- 单位（机甲/载具）布置在任务同名 .fit 的 ``[PartN]`` 段（mission.cpp createPartObject）：
  ul ObjectNumber / c TeamID / f PositionX·PositionY / f Rotation / b PlayerPart /
  l Active·l Exists。

容错：坏 fit 行逐条跳过（对齐 fit_parser 惯例）；缺 packet/短记录抛 MissionPakError。
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

from pymc2.infrastructure.pak_parser import PacketStorageType, PakFile, PakParseError

WORLD_UNITS_PER_VERTEX = 128.0  # Terrain.cpp:76（两顶点间距 = 一 tile 的世界尺寸）
_POSTCOMP_VERTEX_SIZE = 32  # Vertex.h PostcompVertex（16 字节对齐）
_ELEVATION_OFFSET = 12  # vertexNormal 3×f32 之后的 elevation f32
_TERRAIN_PACKET = 0
_OBJECTS_PACKET = 1
_OBJECT_RECORD_SIZE = 40  # 10×i32（含 2 个 padding long，objmgr.cpp:811-824）

# 对象类号（object2.pak [ObjectClass] ObjectTypeNum，与 Data/Objects/*.fit 主表同编号；
# dobjclass.h：BATTLEMECH=2 / GROUNDVEHICLE=3；炮塔 21 为类型主表编号，见 P2 §1）。
OBJECT_CLASS_TREE = 0
OBJECT_CLASS_BUILDING = 1
OBJECT_CLASS_MECH = 2
OBJECT_CLASS_VEHICLE = 3
OBJECT_CLASS_TURRET = 21

_OBJECT_CLASS_RE = re.compile(r"\[ObjectClass\](.*?)(?=\n\[|\Z)", re.S)
_OBJECT_TYPE_NUM_RE = re.compile(r"l\s+ObjectTypeNum\s*=\s*(-?\d+)")
_NAME_RE = re.compile(r"st\s+Name\s*=\s*\"([^\"]*)\"")
_APPEARANCE_RE = re.compile(r"st\s+AppearanceName\s*=\s*\"([^\"]*)\"")
_PART_SECTION_RE = re.compile(r"^part(\d+)$")


class MissionPakError(ValueError):
    """任务 .pak 内容提取失败（缺 terrain/objects packet、记录短读、地形边长不合法）。"""


@dataclass(frozen=True)
class TerrainGrid:
    """任务地形：每 tile 高程（米级 float，tile = 左上顶点值）+ 世界坐标换算。

    rows[r][c] 为 MC2 tile (row=r, col=c) 的高程；tiles/边 = vertices_per_side − 1。
    """

    vertices_per_side: int
    rows: tuple[tuple[float, ...], ...]

    @property
    def tiles_per_side(self) -> int:
        """tile 边长（顶点数 − 1）。"""
        return self.vertices_per_side - 1

    def world_to_tile(self, x: float, y: float) -> tuple[int, int]:
        """世界坐标 → MC2 tile (row, col)（Terrain.cpp:265-268 逆映射，钳制在图内）。"""
        half = self.vertices_per_side * WORLD_UNITS_PER_VERTEX / 2.0
        col = int((x + half) // WORLD_UNITS_PER_VERTEX)
        row = int((half - y) // WORLD_UNITS_PER_VERTEX)
        limit = self.tiles_per_side - 1
        return (max(0, min(limit, row)), max(0, min(limit, col)))


@dataclass(frozen=True)
class ObjPlacement:
    """pak packet 1 静态对象记录（40 字节，objmgr.cpp ObjDataLoader）。"""

    obj_type_num: int
    x: float
    y: float
    z: float
    rotation_deg: float
    damage: int
    team_id: int
    parent_id: int


@dataclass(frozen=True)
class MissionTerrain:
    """一关的任务数据：地形高程 + 静态对象布置表。"""

    terrain: TerrainGrid
    objects: tuple[ObjPlacement, ...]


@dataclass(frozen=True)
class FitPart:
    """任务 .fit [PartN] 单位布置记录（mission.cpp MoverSpec 子集）。"""

    part_index: int
    object_number: int  # → object2.pak packet 解析对象类
    team_id: int
    x: float
    y: float
    rotation_deg: float
    player: bool
    active: bool


@dataclass(frozen=True)
class ObjectTypeDef:
    """object2.pak 单个对象类型（packet N = objTypeNum N 的 FITini 子集）。"""

    type_index: int
    object_class: int  # [ObjectClass] ObjectTypeNum（2=机甲/3=载具/21=炮塔/0=树/1=建筑）
    name: str
    appearance_name: str


def load_mission_pak(path: Path) -> MissionTerrain:
    """官方任务 .pak → MissionTerrain（packet 0 地形 + packet 1 静态对象表）。"""
    pak = PakFile.open(path)
    if pak.packet_count <= _OBJECTS_PACKET:
        raise MissionPakError(f"{path}: expected terrain+objects packets, has {pak.packet_count}")
    terrain = _parse_terrain(pak.read_packet(_TERRAIN_PACKET))
    objects = _parse_objects(pak.read_packet(_OBJECTS_PACKET))
    return MissionTerrain(terrain=terrain, objects=objects)


def _parse_terrain(blob: bytes) -> TerrainGrid:
    """PostcompVertex 数组 → TerrainGrid（每 tile 取左上顶点 elevation）。"""
    if len(blob) % _POSTCOMP_VERTEX_SIZE != 0:
        raise MissionPakError(f"terrain packet size {len(blob)} not a multiple of {_POSTCOMP_VERTEX_SIZE}")
    vertex_count = len(blob) // _POSTCOMP_VERTEX_SIZE
    side = int(round(vertex_count**0.5))
    if side * side != vertex_count or side < 2:
        raise MissionPakError(f"terrain vertex count {vertex_count} is not a square >= 2x2")
    elevations = [
        struct.unpack_from("<f", blob, index * _POSTCOMP_VERTEX_SIZE + _ELEVATION_OFFSET)[0]
        for index in range(vertex_count)
    ]
    # tile = 左上顶点：tiles/边 = side − 1（dterrain.h TILESPERSIDE）。
    rows = tuple(tuple(elevations[row * side + col] for col in range(side - 1)) for row in range(side - 1))
    return TerrainGrid(vertices_per_side=side, rows=rows)


def _parse_objects(blob: bytes) -> tuple[ObjPlacement, ...]:
    """对象布置表 → ObjPlacement 元组（40 字节步长；尾部残字节忽略——原版顺序读同理）。"""
    if len(blob) < 4:
        raise MissionPakError(f"objects packet too small: {len(blob)} bytes")
    (count,) = struct.unpack_from("<i", blob, 0)
    if count < 0:
        raise MissionPakError(f"objects count negative: {count}")
    placements: list[ObjPlacement] = []
    offset = 4
    for _ in range(count):
        if offset + _OBJECT_RECORD_SIZE > len(blob):
            break  # 记录区截断：按原版顺序读语义取已完整记录，不硬编补齐
        obj_type_num, x, y, z, rotation, damage, team_id, parent_id = struct.unpack_from("<i4f3i", blob, offset)
        placements.append(
            ObjPlacement(
                obj_type_num=obj_type_num,
                x=x,
                y=y,
                z=z,
                rotation_deg=rotation,
                damage=damage,
                team_id=team_id,
                parent_id=parent_id,
            )
        )
        offset += _OBJECT_RECORD_SIZE
    return tuple(placements)


def load_mission_fit(path: Path) -> tuple[FitPart, ...]:
    """任务 .fit [PartN] → FitPart 元组（section 序号排序；坏行跳过）。"""
    sections = _parse_fit_sections(path.read_text(encoding="utf-8", errors="replace"))
    parts: list[FitPart] = []
    for key, values in sections.items():
        match = _PART_SECTION_RE.match(key.lower())
        if match is None:
            continue
        part_index = int(match.group(1))
        parts.append(
            FitPart(
                part_index=part_index,
                object_number=_as_int(values.get("objectnumber"), -1),
                team_id=_as_int(values.get("teamid"), -1),
                x=_as_float(values.get("positionx"), 0.0),
                y=_as_float(values.get("positiony"), 0.0),
                rotation_deg=_as_float(values.get("rotation"), 0.0),
                player=_as_bool(values.get("playerpart")),
                active=_as_bool(values.get("active")) and _as_bool(values.get("exists")),
            )
        )
    parts.sort(key=lambda part: part.part_index)
    return tuple(parts)


def load_object_types(path: Path) -> dict[int, ObjectTypeDef]:
    """object2.pak 全表扫描 → {objTypeNum: ObjectTypeDef}（packet N = 类型 N）。"""
    pak = PakFile.open(path)
    types: dict[int, ObjectTypeDef] = {}
    for index in range(pak.packet_count):
        packet = pak.packet(index)
        if packet.storage_type is PacketStorageType.NUL or packet.unpacked_size == 0:
            continue
        try:
            text = pak.read_packet(index).decode("ascii", "replace")
        except PakParseError:
            continue  # 个别坏 packet 不拖垮全表（原版 load 亦按需才读）
        class_match = _OBJECT_CLASS_RE.search(text)
        num_match = _OBJECT_TYPE_NUM_RE.search(class_match.group(1)) if class_match else None
        name_match = _NAME_RE.search(text)
        appearance_match = _APPEARANCE_RE.search(text)
        types[index] = ObjectTypeDef(
            type_index=index,
            object_class=int(num_match.group(1)) if num_match else -1,
            name=name_match.group(1) if name_match else "",
            appearance_name=appearance_match.group(1) if appearance_match else "",
        )
    return types


def _parse_fit_sections(text: str) -> dict[str, dict[str, str]]:
    """FITini 文本 → {段名: {小写键: 原始字符串值}}（值转交调用方按字段解释）。"""
    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.split("//", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current = {}
            sections[line[1:-1]] = current
            continue
        if current is None or "=" not in line:
            continue
        head, _, value = line.partition("=")
        key = head.strip().split(" ")[-1].strip().lower()  # 剥类型前缀（l/f/st/ul/uc/b/c…）
        current[key] = value.strip().strip('"')
    return sections


def _as_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value, 0)
    except ValueError:
        return default


def _as_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _as_bool(value: str | None) -> bool:
    """FITini 布尔（b 键）：TRUE/true/非零数 → True；缺失按 False（存在性由调用方判断）。"""
    if value is None:
        return False
    if value.upper() == "TRUE":
        return True
    return _as_int(value, 0) != 0
