"""MC2 任务 .pak 容器解析（P3 内容驱动）：PacketFile 二进制格式 → packet 枚举与解压。

MC2 参照（Shared Source `MCLib/packet.h` + `Packet.cpp`，docs/P3_DESIGN_20261121.md §1.1）：

- 头部（小端）：u32 @0 = 0xFEEDFACE（否则视为 checksum：``sum(bytes[4:]) mod 2^32``
  校验）；u32 @4 = firstPacketOffset = ``(2 + numPackets) << 2``；
  ``numPackets = firstPacketOffset // 4 - 2``。
- seek 表 @8：numPackets × u32——bit29-31 = storage type，bit0-28 = packet 起始偏移
  （OFFSET_MASK=0x1FFFFFFF，TYPE_SHIFT=29）。
- packetSize = 下一 entry 偏移 − 本 entry 偏移（最后一个 packet 到文件尾）。
- 类型：RAW=0 / FWF=1（file-within-file，按 RAW 读）/ LZD=2 / HF=3 / ZLIB=4 / NUL=7。
  LZD/ZLIB 的 packet 首 u32 = unpackedSize，其后为压缩体（packetSize 含该 4 字节头）；
  ZLIB = 标准 zlib 流。HF 原版即不支持（readPacket STOP）；LZD 为旧 LZ 算法，
  实测官方任务两关（tut_01/mc2_01）全部 packet 仅 RAW/ZLIB → 本实现 LZD/HF 显式报错
  （裁剪记录 docs/P3_DESIGN_20261121.md §6.5）。

注意：`MCLib/ffile.cpp`（FastFile，.ff 资产库，elfHash 索引）是另一种容器，官方任务
.pak 不使用它，本模块不实现（docs/P3_DESIGN_20261121.md §6.1）。
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

PACKET_FILE_VERSION = 0xFEEDFACE
TYPE_SHIFT = 29
OFFSET_MASK = (1 << 29) - 1
_HEADER_SIZE = 8  # version + firstPacketOffset


class PacketStorageType(IntEnum):
    """packet 存储类型（packet.h STORAGE_TYPE_*，bit29-31 编码）。"""

    RAW = 0
    FWF = 1
    LZD = 2
    HF = 3
    ZLIB = 4
    NUL = 7


class PakParseError(ValueError):
    """.pak 容器解析失败（magic/checksum/seek 表/解压长度不符/不支持的存储类型）。"""


@dataclass(frozen=True)
class PakPacket:
    """单个 packet 的枚举信息（偏移与尺寸为容器层原始值）。"""

    index: int
    offset: int  # 数据区起始偏移（已剥类型位）
    storage_type: PacketStorageType
    packet_size: int  # 下一 packet 起点与本起点的差（ZLIB 含 4 字节 unpackedSize 头）
    unpacked_size: int  # RAW = packet_size；ZLIB/LZD = packet 首 u32；NUL = 0


class PakFile:
    """PacketFile 只读容器：packet 枚举 + 解包（RAW 透传 / ZLIB 解压）。"""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._entries: tuple[PakPacket, ...] = tuple(self._parse(data))

    @classmethod
    def open(cls, path: Path) -> PakFile:
        """从文件加载 .pak（只读）。"""
        return cls(path.read_bytes())

    @staticmethod
    def _parse(data: bytes) -> list[PakPacket]:
        if len(data) < _HEADER_SIZE:
            raise PakParseError(f".pak too short: {len(data)} bytes (< {_HEADER_SIZE})")
        first_check, first_packet_offset = struct.unpack_from("<II", data, 0)
        if first_check != PACKET_FILE_VERSION:
            # 首位不是版本 → 是 checksum（Packet.cpp afterOpen:156-167）：sum(bytes[4:])。
            checksum = sum(data[4:]) & 0xFFFFFFFF
            if checksum != first_check:
                raise PakParseError(f"bad checksum: expected 0x{first_check:08X}, computed 0x{checksum:08X}")
        if first_packet_offset < _HEADER_SIZE or first_packet_offset % 4 != 0:
            raise PakParseError(f"bad first packet offset {first_packet_offset}")
        num_packets = first_packet_offset // 4 - 2
        if num_packets < 0:
            raise PakParseError(f"bad packet count {num_packets}")
        table_end = _HEADER_SIZE + num_packets * 4
        if len(data) < table_end:
            raise PakParseError(f"seek table truncated: need {table_end} bytes, have {len(data)}")
        entries = struct.unpack_from(f"<{num_packets}I", data, _HEADER_SIZE)
        packets: list[PakPacket] = []
        for index, entry in enumerate(entries):
            storage_type = PacketStorageType((entry >> TYPE_SHIFT) & 7)
            offset = entry & OFFSET_MASK
            next_offset = entries[index + 1] & OFFSET_MASK if index + 1 < num_packets else len(data)
            packet_size = next_offset - offset
            if packet_size < 0:
                raise PakParseError(f"packet {index}: negative size {packet_size}")
            unpacked = packet_size
            if storage_type in (PacketStorageType.LZD, PacketStorageType.ZLIB):
                if packet_size < 4:
                    raise PakParseError(f"packet {index}: compressed packet too small {packet_size}")
                if offset + packet_size > len(data):
                    raise PakParseError(f"packet {index}: data out of range")
                (unpacked,) = struct.unpack_from("<I", data, offset)
            elif storage_type is PacketStorageType.NUL:
                unpacked = 0
            packets.append(
                PakPacket(
                    index=index,
                    offset=offset,
                    storage_type=storage_type,
                    packet_size=packet_size,
                    unpacked_size=unpacked,
                )
            )
        return packets

    @property
    def packet_count(self) -> int:
        """packet 总数（含 NUL）。"""
        return len(self._entries)

    def packet(self, index: int) -> PakPacket:
        """packet 枚举信息（越界抛 PakParseError）。"""
        if not 0 <= index < len(self._entries):
            raise PakParseError(f"packet index out of range: {index} (count {len(self._entries)})")
        return self._entries[index]

    def packets(self) -> tuple[PakPacket, ...]:
        """全部 packet 枚举快照。"""
        return self._entries

    def read_raw_packet(self, index: int) -> bytes:
        """不解压直取 packet 存储体（ZLIB/LZD 含首 4 字节 unpackedSize 头；NUL 为空）。"""
        packet = self.packet(index)
        start = packet.offset
        end = packet.offset + packet.packet_size
        return self._data[start:end]

    def read_packet(self, index: int) -> bytes:
        """读取并解包 packet：RAW/FWF 透传，ZLIB 解压校验 unpackedSize，NUL 为空。

        ZLIB 压缩体不含 unpackedSize 头（Packet.cpp readPacket:331-360
        ``seek(packetBase+sizeof(long))`` 跳头后解压的口径）。
        """
        packet = self.packet(index)
        if packet.storage_type is PacketStorageType.NUL:
            return b""
        if packet.storage_type in (PacketStorageType.LZD, PacketStorageType.HF):
            raise PakParseError(
                f"packet {index}: storage type {packet.storage_type.name} not supported "
                "(legacy LZ/HF, absent from shipped mission data)"
            )
        body = self._data[
            packet.offset + (4 if packet.storage_type is PacketStorageType.ZLIB else 0) : packet.offset
            + packet.packet_size
        ]
        if packet.storage_type is PacketStorageType.ZLIB:
            try:
                blob = zlib.decompress(body)
            except zlib.error as exc:
                raise PakParseError(f"packet {index}: zlib decompress failed: {exc}") from exc
            if len(blob) != packet.unpacked_size:
                raise PakParseError(
                    f"packet {index}: unpacked size mismatch (expected {packet.unpacked_size}, got {len(blob)})"
                )
            return blob
        return body
