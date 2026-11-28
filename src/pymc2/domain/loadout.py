"""装配配置数据模型（W2 装配编辑器，设计 docs/W2_DESIGN_20261128.md §2）。

纯数据层：零 pygame 依赖（可测试性 NFR）；吨位常量单点集中（风险缓解）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pymc2.domain.entities.mech import Mech, Weapon, WeaponType
from pymc2.domain.entities.mech_chassis import MechLocation

# ---------------------------------------------------------------------------
# 吨位常量（单点调参，风险缓解 §7）
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
ARMOR_TON_PER_POINT = 0.5  # 1 装甲点 = 0.5t（MC2 常规口径简化）

# hull（总余量）→ 分档（PRD §4.1：轻型 20-35t / 中型 40-55t / 重型 60-75t / 突击 80-100t）
TIER_TABLE: tuple[tuple[int, str, float], ...] = (
    (120, "light", 20.0),  # (hull 上限, 分档名, 底盘基准吨位=分档下限)
    (180, "medium", 40.0),
    (240, "heavy", 60.0),
    (10**9, "assault", 80.0),
)
TONNAGE_MAX: dict[str, float] = {
    "light": 35.0,
    "medium": 55.0,
    "heavy": 75.0,
    "assault": 100.0,
}

# 部位装甲上限（中型模板 ×2，单点常量；ARMOR_CAP 校验依据）
ARMOR_CAPS: dict[str, int] = {
    MechLocation.HEAD.value: 10,
    MechLocation.CT.value: 24,
    MechLocation.LT.value: 12,
    MechLocation.RT.value: 12,
    MechLocation.LA.value: 8,
    MechLocation.RA.value: 8,
    MechLocation.LL.value: 10,
    MechLocation.RL.value: 10,
}


def tier_for_hull(hull: int) -> str:
    """hull 总余量 → 底盘分档名。"""
    for cap, tier, _base in TIER_TABLE:
        if hull <= cap:
            return tier
    return "assault"


# ---------------------------------------------------------------------------
# 武器目录（PRD §5.2 定标；tonnage 为 W2 新增字段）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WeaponSpec:
    """武器目录条目（静态表）。"""

    name: str
    wtype: str  # WeaponType.value："ballistic"|"energy"|"missile"
    damage: int
    range_px: float
    cooldown_s: float
    heat_per_shot: float
    tonnage: float


WEAPON_LIBRARY: tuple[WeaponSpec, ...] = (
    WeaponSpec("Standard", "ballistic", 10, 400.0, 2.0, 5.0, 5.0),
    WeaponSpec("PPC", "energy", 18, 480.0, 4.0, 12.0, 6.0),
    WeaponSpec("AC-10", "ballistic", 14, 420.0, 3.0, 4.0, 10.0),
    WeaponSpec("LRM-20", "missile", 6, 560.0, 5.0, 9.0, 5.0),
    WeaponSpec("Small Laser", "energy", 5, 220.0, 1.0, 3.0, 2.0),
    WeaponSpec("Flamer", "energy", 3, 180.0, 0.8, 6.0, 1.5),
)

WEAPON_INDEX: dict[str, WeaponSpec] = {spec.name: spec for spec in WEAPON_LIBRARY}


def build_weapon(spec: WeaponSpec, location: MechLocation) -> Weapon:
    """目录条目 → Weapon 实例（每实例独立 CD，2.1 多槽位核销路径）。"""
    return Weapon(
        name=spec.name,
        damage=spec.damage,
        range_px=spec.range_px,
        cooldown_s=spec.cooldown_s,
        mounted_location=location,
        heat_per_shot=spec.heat_per_shot,
        wtype=WeaponType(spec.wtype),
    )


# ---------------------------------------------------------------------------
# 装配配置
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadoutError:
    """校验/IO 错误对象（FR-2.2：返回值非 raise，GUI 零异常泄漏）。"""

    code: str  # OVERWEIGHT|UNKNOWN_WEAPON|BAD_LOCATION|ARMOR_CAP|BAD_JSON|FILE_ERROR
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class WeaponSlotConfig:
    """单槽位：武器目录外键 + 挂载部位。"""

    spec_name: str
    location: str  # MechLocation.value


@dataclass
class LoadoutConfig:
    """装配配置（FR-1）：底盘分档 + 8 部位装甲 + 多武器槽。"""

    chassis_kind: str  # tier_for_hull 分档名
    armor_allocation: dict[str, int]
    weapons: list[WeaponSlotConfig]

    # -- 吨位（FR-2.1）--

    def base_tonnage(self) -> float:
        return dict((t, b) for _cap, t, b in TIER_TABLE).get(self.chassis_kind, TIER_TABLE[1][2])

    def total_tonnage(self) -> float:
        armor_tons = sum(self.armor_allocation.values()) * ARMOR_TON_PER_POINT
        weapon_tons = sum(WEAPON_INDEX[w.spec_name].tonnage for w in self.weapons if w.spec_name in WEAPON_INDEX)
        return self.base_tonnage() + armor_tons + weapon_tons

    # -- 校验（FR-2.2）--

    def validate(self) -> LoadoutError | None:
        """合法返回 None；非法返回错误对象（不 raise）。具体错误优先于超重。"""
        if self.chassis_kind not in TONNAGE_MAX:
            return LoadoutError("BAD_TIER", f"unknown chassis_kind: {self.chassis_kind}")
        for w in self.weapons:
            if w.spec_name not in WEAPON_INDEX:
                return LoadoutError("UNKNOWN_WEAPON", f"unknown weapon: {w.spec_name}")
            if w.location not in ARMOR_CAPS:
                return LoadoutError("BAD_LOCATION", f"unknown location: {w.location}")
        for loc, points in self.armor_allocation.items():
            if loc not in ARMOR_CAPS:
                return LoadoutError("BAD_LOCATION", f"unknown location: {loc}")
            if points > ARMOR_CAPS[loc]:
                return LoadoutError(
                    "ARMOR_CAP",
                    f"{loc} armor {points} > cap {ARMOR_CAPS[loc]}",
                    {"location": loc, "cap": ARMOR_CAPS[loc]},
                )
        total = self.total_tonnage()
        max_t = TONNAGE_MAX[self.chassis_kind]
        if total > max_t:
            return LoadoutError(
                "OVERWEIGHT",
                f"total {total:.1f}t exceeds {max_t:.1f}t",
                {"total": total, "max": max_t},
            )
        return None

    # -- 序列化（FR-1.2）--

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "chassis_kind": self.chassis_kind,
            "armor_allocation": dict(self.armor_allocation),
            "weapons": [{"spec_name": w.spec_name, "location": w.location} for w in self.weapons],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> LoadoutConfig:
        """严格类型校验反序列化（FR-3.3：类型错 → TypeError → BAD_JSON，不静默强转）。"""
        if not isinstance(data, dict):
            raise TypeError("loadout must be a JSON object")
        kind = data["chassis_kind"]
        if not isinstance(kind, str):
            raise TypeError("chassis_kind must be str")
        armor_raw = data["armor_allocation"]
        if not isinstance(armor_raw, dict):
            raise TypeError("armor_allocation must be object")
        allocation = {str(k): v for k, v in armor_raw.items()}
        if not all(isinstance(v, int) for v in allocation.values()):
            raise TypeError("armor points must be int")
        weapons_raw = data["weapons"]
        if not isinstance(weapons_raw, list):
            raise TypeError("weapons must be array")
        weapons: list[WeaponSlotConfig] = []
        for w in weapons_raw:
            if not isinstance(w, dict):
                raise TypeError("weapon slot must be object")
            name, loc = w["spec_name"], w["location"]
            if not isinstance(name, str) or not isinstance(loc, str):
                raise TypeError("weapon fields must be str")
            weapons.append(WeaponSlotConfig(spec_name=name, location=loc))
        return cls(
            chassis_kind=kind,
            armor_allocation=allocation,
            weapons=weapons,
        )

    # -- 战斗应用（FR-5 / 设计 §5.3）--

    def apply(self, mech: Mech) -> None:
        """装甲覆盖 + 重建武器槽 + max_hull 重算。structure 不动。"""
        for loc_str, points in self.armor_allocation.items():
            part = mech.chassis.parts.get(MechLocation(loc_str))
            if part is not None:
                part.armor = float(min(points, ARMOR_CAPS[loc_str]))
        for slot_list in mech.chassis.slots.values():
            slot_list.clear()
        for w in self.weapons:
            if w.spec_name in WEAPON_INDEX and w.location in ARMOR_CAPS:
                mech.chassis.mount_weapon(build_weapon(WEAPON_INDEX[w.spec_name], MechLocation(w.location)))
        if mech.weapons:
            mech.weapon = mech.weapons[0]
        mech.max_hull = int(round(mech.chassis.total_integrity))

    # -- 默认配置（FR-5.1 零破坏路径）--

    @classmethod
    def default_config(cls, chassis_kind: str = "medium") -> LoadoutConfig:
        """底盘工厂对齐默认配载（单 Standard + 模板装甲均摊）。"""
        base = dict((t, b) for _cap, t, b in TIER_TABLE).get(chassis_kind, TIER_TABLE[1][2])
        budget = int((TONNAGE_MAX.get(chassis_kind, 55.0) - base) / ARMOR_TON_PER_POINT / 2)
        allocation = {loc: min(budget // 8 + 1, cap) for loc, cap in ARMOR_CAPS.items()}
        return cls(
            chassis_kind=chassis_kind,
            armor_allocation=allocation,
            weapons=[WeaponSlotConfig("Standard", MechLocation.CT.value)],
        )
