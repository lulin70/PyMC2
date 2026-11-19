"""MC2 .fit 对象定义解析（Wave V2）：Data/Objects/*.fit 文本 → 渲染相关字段。

.fit 是 MC2 的 FITini 文本（实测 ASCII + CRLF、``//`` 行注释、``FITini``/``FITEnd``
哨兵行），本模块只解析渲染管线消费得上的字段（最小化原则，[MechProfile] 战斗侧
数值不解析——PyMC2 有自己的伤害体系，详见 docs/V2_DESIGN_20261119.md §2）：

- ``l ObjectTypeNum``：对象类号（2 = 机甲）；
- ``st Name``：显示名；
- ``st AppearanceName``：TGL 外观引用（贴图/精灵名，小写化存储可直接喂资产匹配链）；
- ``f ExtentRadius``：物理半径（米，渲染尺寸参考，本 Wave 不消费）；
- ``l DestroyedObject`` / ``l ExplosionObject``：摧毁/爆炸形态对象类号（V3+ 消费）。

容错：非 FITini 文本或缺渲染必需键（Name/AppearanceName）抛 ``FitParseError``；
未知键忽略；类型前缀（l/f/st）决定值的解析方式，解析失败的键忽略（单键损坏不致命）。
与 MC2AssetPack 零耦合：不自动扫描 Data/Objects（项目机甲 id 体系与 .fit Name 不同名）。
"""

from __future__ import annotations

from dataclasses import dataclass

# 对象类号（[ObjectClass] ObjectTypeNum，官方 fitconv/mcobj 常量语义）。
OBJECT_TYPE_MECH = 2

_FIT_HEADER = "FITini"
_KEY_VALUE_SEP = "="


class FitParseError(ValueError):
    """.fit 文本解析失败（缺 FITini 头 / 缺渲染必需键）。"""


@dataclass(frozen=True)
class FitDefinition:
    """一个 .fit 对象定义的渲染相关字段（appearance_name 小写化）。"""

    object_type_num: int
    name: str
    appearance_name: str
    extent_radius: float
    destroyed_object: int | None = None
    explosion_object: int | None = None


def _strip_comment(line: str) -> str:
    """剥 ``//`` 行注释（官方文件把注释写在行尾）。"""
    return line.split("//", 1)[0].strip()


def _parse_entry(line: str) -> tuple[str, int | float | str] | None:
    """解析一行键值（``type Key = Value``）：类型前缀在行首（l/f/st）；失败 → None。"""
    kind, _, rest = line.partition(" ")
    key, sep, raw = rest.partition(_KEY_VALUE_SEP)
    if not sep or kind not in ("l", "f", "st"):
        return None
    raw = raw.strip()
    key = key.strip().lower()
    try:
        if kind == "l":
            return key, int(raw)
        if kind == "f":
            return key, float(raw)
    except ValueError:
        return None  # 单键类型损坏不致命
    return key, raw.strip('"')  # st：去引号字符串


def parse_fit(text: str) -> FitDefinition:
    """FITini 文本 → FitDefinition；缺 FITini 头或渲染必需键 → FitParseError。"""
    if _FIT_HEADER not in text:
        raise FitParseError("缺少 FITini 头（不是 MC2 .fit 文本）")
    values: dict[str, int | float | str] = {}
    for raw_line in text.splitlines():
        line = _strip_comment(raw_line)
        if not line or line.startswith("[") or line.lower() in (_FIT_HEADER.lower(), "fitend"):
            continue  # 段头/哨兵/空行不携带键值
        entry = _parse_entry(line)
        if entry is not None:
            values[entry[0]] = entry[1]
    try:
        name = values["name"]
        appearance = values["appearancename"]
    except KeyError as exc:
        raise FitParseError(f"缺少渲染必需键：{exc.args[0]}") from exc
    if not isinstance(name, str) or not isinstance(appearance, str):
        raise FitParseError("Name/AppearanceName 必须是字符串（st 键）")
    object_type = values.get("objecttypenum", -1)
    if not isinstance(object_type, int):
        raise FitParseError("ObjectTypeNum 必须是整数（l 键）")
    extent = values.get("extentradius", 0.0)
    if not isinstance(extent, (int, float)):
        raise FitParseError("ExtentRadius 必须是数值（f 键）")
    destroyed = values.get("destroyedobject")
    explosion = values.get("explosionobject")
    return FitDefinition(
        object_type_num=object_type,
        name=name,
        appearance_name=appearance.lower(),
        extent_radius=float(extent),
        destroyed_object=destroyed if isinstance(destroyed, int) else None,
        explosion_object=explosion if isinstance(explosion, int) else None,
    )
