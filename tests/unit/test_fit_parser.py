"""fit_parser 单测：合成 FITini 文本（自造值，官方 .fit 文本零入库）。

覆盖：全字段解析（ObjectTypeNum/Name/AppearanceName 小写化/ExtentRadius/
DestroyedObject/ExplosionObject）、行注释与 CRLF 容错、缺 FITini 头报错、缺渲染
必需键报错、未知键与杂散行忽略、非机甲对象类号解析、OBJECT_TYPE_MECH 常量。
格式依据 docs/V2_DESIGN_20261119.md §1.3（远端四机甲 .fit 实测：ASCII + CRLF、
``//`` 行注释、l/f/st 类型前缀、FITini/FITEnd 哨兵）。
"""

from __future__ import annotations

import pytest

from pymc2.infrastructure.fit_parser import (
    OBJECT_TYPE_MECH,
    FitDefinition,
    FitParseError,
    parse_fit,
)

# 合成 fixture：结构与官方 .fit 同构，值全部自造（TestMech），不复制官方文本。
FIT_FIXTURE = """\
FITini

[ObjectClass]
l ObjectTypeNum = 2

[ObjectType]
st Name               = "TestMech"      // Text string giving a descriptive name
l ExplosionObject     = 41              // Object Class Number of explosion
l DestroyedObject     = 101             // Object Class Number of destroyed shape
f ExtentRadius        = 12              // Extent Radius in Meters
st AppearanceName     = "TestMech"

[MechProfile]
st ProfileName        = "TestMech"
f destructdamage      = 35              // 战斗侧字段：解析器应忽略

FITEnd
"""


class TestParseFit:
    def test_parses_all_render_fields(self) -> None:
        fit = parse_fit(FIT_FIXTURE)
        assert fit == FitDefinition(
            object_type_num=2,
            name="TestMech",
            appearance_name="testmech",  # 小写化：可直接喂资产匹配链
            extent_radius=12.0,
            destroyed_object=101,
            explosion_object=41,
        )

    def test_mech_profile_section_ignored(self) -> None:
        """[MechProfile] 战斗侧字段不进结果（裁剪决策，设计文档 §2）。"""
        fit = parse_fit(FIT_FIXTURE)
        assert not hasattr(fit, "destructdamage")
        assert not hasattr(fit, "profile_name")

    def test_crlf_line_endings_tolerated(self) -> None:
        """官方实测 CRLF：\\r\\n 与 \\n 结果一致。"""
        assert parse_fit(FIT_FIXTURE.replace("\n", "\r\n")) == parse_fit(FIT_FIXTURE)

    def test_object_type_num_preserved_for_non_mech(self) -> None:
        """非机甲类号原样携带（2=机甲由消费方判断，解析器不硬编码类别）。"""
        text = FIT_FIXTURE.replace("l ObjectTypeNum = 2", "l ObjectTypeNum = 5")
        fit = parse_fit(text)
        assert fit.object_type_num == 5

    def test_optional_object_refs_default_none(self) -> None:
        """ExplosionObject/DestroyedObject 缺省 → None（部分物体定义没有）。"""
        text = "\n".join(
            line for line in FIT_FIXTURE.splitlines() if "ExplosionObject" not in line and "DestroyedObject" not in line
        )
        fit = parse_fit(text)
        assert fit.destroyed_object is None
        assert fit.explosion_object is None

    def test_unknown_keys_and_stray_lines_ignored(self) -> None:
        text = FIT_FIXTURE.replace("f ExtentRadius", "x UnknownKey = junk\nf ExtentRadius")
        text = text.replace("[ObjectType]", "[ObjectType]\ngarbage line without separator")
        fit = parse_fit(text)
        assert fit.extent_radius == 12.0  # 单键损坏/杂散行不致命

    def test_missing_fitini_header_raises(self) -> None:
        text = FIT_FIXTURE.replace("FITini", "NOTFIT")
        with pytest.raises(FitParseError, match="FITini"):
            parse_fit(text)

    def test_missing_appearance_name_raises(self) -> None:
        text = "\n".join(line for line in FIT_FIXTURE.splitlines() if "AppearanceName" not in line)
        with pytest.raises(FitParseError, match="appearancename"):
            parse_fit(text)

    def test_missing_name_raises(self) -> None:
        text = "\n".join(line for line in FIT_FIXTURE.splitlines() if "st Name " not in line)
        with pytest.raises(FitParseError, match="name"):
            parse_fit(text)

    def test_empty_string_raises(self) -> None:
        with pytest.raises(FitParseError):
            parse_fit("")

    def test_object_type_mech_constant(self) -> None:
        """官方机甲类号语义：.fit ObjectTypeNum=2（设计文档 §1.3 实测）。"""
        assert OBJECT_TYPE_MECH == 2
