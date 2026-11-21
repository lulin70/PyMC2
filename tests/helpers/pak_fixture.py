"""合成 .pak fixture（P3 双轨测试，docs/P3_DESIGN_20261121.md §4）：按 PacketFile 格式手写字节。

不读真实 MC2 资产（CI 无资产可跑）；字节布局与 Shared Source packet.h/Packet.cpp 一致：
头部（version + firstPacketOffset）→ seek 表（bit29-31 类型 + bit0-28 偏移）→ 数据区。
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Sequence

from pymc2.infrastructure.pak_parser import PACKET_FILE_VERSION, TYPE_SHIFT, PacketStorageType


def make_pak(packet_specs: Sequence[tuple[PacketStorageType, bytes]], *, version: int = PACKET_FILE_VERSION) -> bytes:
    """packet 规格表（类型 + 未压缩载荷）→ .pak 字节（ZLIB/LZD 自动加 unpackedSize 头并压缩）。"""
    table_size = 8 + len(packet_specs) * 4
    chunks: list[bytes] = []
    entries: list[int] = []
    for storage_type, payload in packet_specs:
        if storage_type in (PacketStorageType.LZD, PacketStorageType.ZLIB):
            body = struct.pack("<I", len(payload)) + zlib.compress(payload)
        elif storage_type is PacketStorageType.NUL:
            body = b""
        else:  # RAW / FWF 原样存储
            body = payload
        entries.append((int(storage_type) << TYPE_SHIFT) | (table_size + sum(len(c) for c in chunks)))
        chunks.append(body)
    out = bytearray()
    out += struct.pack("<II", version, table_size)
    for entry in entries:
        out += struct.pack("<I", entry)
    for chunk in chunks:
        out += chunk
    return bytes(out)


def make_terrain_blob(elevations: Sequence[Sequence[float]]) -> bytes:
    """顶点高程二维数组（side×side）→ PostcompVertex 数组（32B/顶点，normal 等字段置零）。"""
    side = len(elevations)
    out = bytearray()
    for row in elevations:
        if len(row) != side:
            raise ValueError(f"terrain rows must be square, got {side}x{len(row)}")
        for value in row:
            out += struct.pack(
                "<3f f III 4B",
                0.0,
                0.0,
                0.0,  # vertexNormal
                float(value),  # elevation @12
                0,  # textureData
                0,  # localRGBLight
                0,  # terrainType
                0,  # selected
                0,  # water
                0,  # shadow
                0,  # highlighted
            )
    return bytes(out)


def make_objects_blob(
    records: Sequence[tuple[int, float, float, float, float, int, int, int]],
) -> bytes:
    """对象记录 → packet 1 字节（首 i32 count + 每记录 40B = objTypeNum(i)+x/y/z/rotation(4f)
    + damage/teamId/parentId/2 padding(5i)）。"""
    out = bytearray(struct.pack("<i", len(records)))
    for obj_type_num, x, y, z, rotation, damage, team_id, parent_id in records:
        out += struct.pack("<i4f5i", obj_type_num, x, y, z, rotation, damage, team_id, parent_id, 0, 0)
    return bytes(out)


def make_object_type_text(object_class: int, name: str, appearance: str = "") -> str:
    """object2.pak 单 packet 的 FITini 文本（[ObjectClass] ObjectTypeNum + [ObjectType]）。"""
    return (
        "FITini\n\n"
        "[ObjectClass]\n"
        f"l ObjectTypeNum = {object_class}\n"
        "\n[ObjectType]\n"
        f'st Name = "{name}"\n'
        f'st AppearanceName = "{appearance or name}"\n'
        "f ExtentRadius = 12\n"
        "FITEnd\n"
    )


def make_object_types_pak(types: Sequence[tuple[int, tuple[int, str]]]) -> bytes:
    """(index, (object_class, name)) 表 → object2.pak 字节（packet N = 类型 N，空洞用 NUL）。"""
    count = max(index for index, _ in types) + 1
    specs: list[tuple[PacketStorageType, bytes]] = [(PacketStorageType.NUL, b"") for _ in range(count)]
    for index, (object_class, name) in types:
        specs[index] = (PacketStorageType.ZLIB, make_object_type_text(object_class, name).encode("ascii"))
    return make_pak(specs)


def make_mission_fit_text(
    parts: Sequence[tuple[int, int, float, float, float, int, bool, bool]],
) -> str:
    """part 记录 (index, objNum, x, y, rot, team, player, active) → 任务 .fit 文本。"""
    sections = [
        "FITini\n",
        "\n[Warriors]\nul NumWarriors = 0\n",
        f"\n[Parts]\nul NumParts = {len(parts)}\n",
    ]
    for index, obj_num, x, y, rotation, team, player, active in parts:
        sections.append(
            f"\n[Part{index}]\n"
            f"ul ObjectNumber = {obj_num}\n"
            f"c TeamID = {team}\n"
            f"f PositionX = {x:.6f}\n"
            f"f PositionY = {y:.6f}\n"
            f"f Rotation = {rotation:.6f}\n"
            f"b PlayerPart = {'TRUE' if player else 'FALSE'}\n"
            f"l Active = {1 if active else 0}\n"
            f"l Exists = {1 if active else 0}\n"
        )
    sections.append("FITEnd\n")
    return "".join(sections)


def tile_world(col: int, row: int, *, vertices_per_side: int = 60) -> tuple[float, float]:
    """MC2 tile (row, col) 中心的世界坐标（Terrain.cpp 逆映射：
    x=(col+0.5)×128−half、y=half−(row+0.5)×128，half=vertices×128/2）。"""
    half = vertices_per_side * 128.0 / 2.0
    return ((col + 0.5) * 128.0 - half, half - (row + 0.5) * 128.0)
