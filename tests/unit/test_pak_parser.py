"""P3 容器解析单测（docs/P3_DESIGN_20261121.md §1.1/§4）：PacketFile 真值表 + 容错。

真值来源：Shared Source packet.h/Packet.cpp 字段对照；fixture 全部合成字节（CI 无资产）。
"""

from __future__ import annotations

import struct

import pytest

from helpers.pak_fixture import make_pak
from pymc2.infrastructure.pak_parser import (
    PACKET_FILE_VERSION,
    PacketStorageType,
    PakFile,
    PakParseError,
)

Z = PacketStorageType.ZLIB
R = PacketStorageType.RAW
N = PacketStorageType.NUL


def test_magic_and_count() -> None:
    """头部真值表：version=0xFEEDFACE、numPackets = firstPacketOffset/4−2。"""
    data = make_pak([(Z, b"alpha"), (R, b"beta")])
    version, first_offset = struct.unpack_from("<II", data, 0)
    assert version == PACKET_FILE_VERSION == 0xFEEDFACE
    assert first_offset == 8 + 2 * 4
    pak = PakFile(data)
    assert pak.packet_count == 2


def test_raw_packet_passthrough() -> None:
    """RAW packet 原样读出（透传不加工）。"""
    pak = PakFile(make_pak([(R, b"\x01\x02\x03"), (R, b"tail")]))
    assert pak.read_packet(0) == b"\x01\x02\x03"
    assert pak.read_packet(1) == b"tail"


def test_zlib_roundtrip() -> None:
    """ZLIB packet：首 u32 = unpackedSize，压缩体 zlib 解压 → 原载荷。"""
    payload = bytes(range(256)) * 8  # 可压缩重复数据
    pak = PakFile(make_pak([(Z, payload), (R, b"x")]))
    packet = pak.packet(0)
    assert packet.storage_type is PacketStorageType.ZLIB
    assert packet.unpacked_size == len(payload)
    assert pak.read_packet(0) == payload
    # 原始存储体含 4 字节 unpackedSize 头（read_raw_packet 直取不剥）
    raw = pak.read_raw_packet(0)
    assert struct.unpack_from("<I", raw, 0)[0] == len(payload)


def test_fwf_read_as_raw() -> None:
    """FWF（file-within-file）按 RAW 透传（Packet.cpp readPacket 同口径）。"""
    pak = PakFile(make_pak([(PacketStorageType.FWF, b"inner-file")]))
    assert pak.packet(0).storage_type is PacketStorageType.FWF
    assert pak.read_packet(0) == b"inner-file"


def test_nul_packet_empty() -> None:
    """NUL packet：packetSize=0、读取为空。"""
    pak = PakFile(make_pak([(N, b""), (R, b"after")]))
    packet = pak.packet(0)
    assert packet.storage_type is PacketStorageType.NUL
    assert packet.packet_size == 0
    assert pak.read_packet(0) == b""
    assert pak.read_packet(1) == b"after"


def test_last_packet_extends_to_eof() -> None:
    """最后 packet 的 packetSize 到文件尾（seekPacket next=getLength 口径）。"""
    payload = b"A" * 50
    pak = PakFile(make_pak([(R, payload)]))
    assert pak.packet(0).packet_size == len(payload)


def test_unpacked_size_mismatch_rejected() -> None:
    """ZLIB 头部 unpackedSize 与实际解压长度不符 → PakParseError（readPacket 校验口径）。"""
    data = bytearray(make_pak([(Z, b"payload")]))
    # 篡改 unpackedSize 头（数据区起点 = 8 + 1×4）
    struct.pack_into("<I", data, 12, 999)
    with pytest.raises(PakParseError, match="unpacked size mismatch"):
        PakFile(bytes(data)).read_packet(0)


def test_zlib_corrupt_body_rejected() -> None:
    """ZLIB 压缩体损坏 → PakParseError（不静默）。"""
    data = bytearray(make_pak([(Z, b"payload"), (R, b"pad-pad-pad-pad")]))
    body_start = 8 + 2 * 4 + 4  # 头 + seek 表 + unpackedSize 头 → zlib 体
    data[body_start : body_start + 4] = b"\xff\xff\xff\xff"  # 破坏 zlib 流
    with pytest.raises(PakParseError, match="zlib decompress failed"):
        PakFile(bytes(data)).read_packet(0)


def test_lzd_and_hf_unsupported() -> None:
    """LZD/HF 显式报错（原版 HF 即 STOP；LZD 实测官方任务无此类型，裁剪 §6.5）。"""
    pak = PakFile(make_pak([(PacketStorageType.LZD, b"legacy"), (PacketStorageType.HF, b"old")]))
    for index in (0, 1):
        with pytest.raises(PakParseError, match="not supported"):
            pak.read_packet(index)


def test_bad_magic_rejected() -> None:
    """首位非 0xFEEDFACE 且 checksum 不符 → PakParseError（afterOpen 校验口径）。"""
    data = bytearray(make_pak([(R, b"payload")]))
    struct.pack_into("<I", data, 0, 0xDEADBEEF)
    with pytest.raises(PakParseError, match="bad checksum"):
        PakFile(bytes(data))


def test_checksum_variant_accepted() -> None:
    """checksum 变体：首位 = sum(bytes[4:]) mod 2^32 时通过（checkSumFile 口径）。"""
    data = bytearray(make_pak([(R, b"payload")]))
    checksum = sum(data[4:]) & 0xFFFFFFFF
    struct.pack_into("<I", data, 0, checksum)
    pak = PakFile(bytes(data))
    assert pak.read_packet(0) == b"payload"


def test_checksum_wrong_value_rejected() -> None:
    """checksum 变体但数值不符 → PakParseError。"""
    data = bytearray(make_pak([(R, b"payload")]))
    struct.pack_into("<I", data, 0, (sum(data[4:]) + 1) & 0xFFFFFFFF)
    with pytest.raises(PakParseError, match="bad checksum"):
        PakFile(bytes(data))


def test_too_short_rejected() -> None:
    """短文件（< 8 字节）→ PakParseError。"""
    with pytest.raises(PakParseError, match="too short"):
        PakFile(b"\x00" * 4)


def test_seek_table_truncated_rejected() -> None:
    """seek 表截断 → PakParseError（表声明 4 个 packet 但文件只有头）。"""
    data = struct.pack("<II", PACKET_FILE_VERSION, 8 + 4 * 4)
    with pytest.raises(PakParseError, match="seek table truncated"):
        PakFile(data)


def test_packet_index_out_of_range() -> None:
    """越界 packet 枚举/读取 → PakParseError。"""
    pak = PakFile(make_pak([(R, b"only")]))
    with pytest.raises(PakParseError, match="out of range"):
        pak.packet(5)
    with pytest.raises(PakParseError, match="out of range"):
        pak.read_packet(-1)


def test_packets_enumeration_snapshot() -> None:
    """packets() 快照：类型/偏移/尺寸逐 packet 对齐（数据区起点 = 8 + n*4）。"""
    payload = b"0123456789"
    pak = PakFile(make_pak([(Z, payload), (N, b""), (R, payload)]))
    packets = pak.packets()
    assert [p.storage_type for p in packets] == [Z, N, R]
    assert packets[0].offset == 8 + 3 * 4
    assert packets[1].packet_size == 0  # NUL 紧随其后
    assert packets[2].packet_size == len(payload)
