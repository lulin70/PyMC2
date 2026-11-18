"""机甲底盘：八部位装甲/内部结构、武器槽与摧毁规则（M3）。

BattleTech/MC2 经典部位模型的最小可测版：
- 八部位 HEAD/CT/LT/RT/LA/RA/LL/RL；伤害先扣装甲（armor）再透进内部结构（structure）。
- 摧毁规则：structure 归零 → 部位摧毁；CT/HEAD 摧毁 → 机甲全毁（alive=False）；
  LA/RA 摧毁 → 挂载该臂的武器失效；LL/RL/侧躯摧毁 → 不全毁但登记 mobility 惩罚。
- 溢出传导：单部位吸收上限为 armor+structure，超额沿 DAMAGE_CHAIN 向后续部位
  传导，保证「总扣减量 == min(伤害, 总余量)」，与 M1/M2 聚合血量语义兼容。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pymc2.domain.entities.mech import Weapon


class MechLocation(Enum):
    """机甲八部位（BattleTech 命名）：头/中枢躯/左右侧躯/左右臂/左右腿。"""

    HEAD = "head"
    CT = "ct"
    LT = "lt"
    RT = "rt"
    LA = "la"
    RA = "ra"
    LL = "ll"
    RL = "rl"


@dataclass
class BodyPart:
    """单一部位：外部装甲（armor）+ 内部结构（structure）。

    摧毁判定为派生语义：structure <= 0 即摧毁（含出生即 0 的退化情形）。
    """

    armor: float
    structure: float

    @property
    def destroyed(self) -> bool:
        """structure 归零 → 部位摧毁。"""
        return self.structure <= 0.0

    def damage(self, amount: float) -> float:
        """结算伤害：先扣装甲再透进结构；返回未被该部位吸收的溢出量。"""
        if amount <= 0.0:
            return 0.0
        if self.destroyed:
            return amount  # 已摧毁部位不再提供防护，全额下溢
        absorbed_armor = min(self.armor, amount)
        self.armor -= absorbed_armor
        remain = amount - absorbed_armor
        if remain > 0.0:
            absorbed_structure = min(self.structure, remain)
            self.structure -= absorbed_structure
            remain -= absorbed_structure
        return remain


# 顺序即溢出传导链；权重为弹着部位分布（CT/侧躯高、四肢低、HEAD 最低）。
_LOCATION_SPECS: tuple[tuple[MechLocation, float], ...] = (
    (MechLocation.CT, 0.25),
    (MechLocation.LT, 0.15),
    (MechLocation.RT, 0.15),
    (MechLocation.LA, 0.10),
    (MechLocation.RA, 0.10),
    (MechLocation.LL, 0.09),
    (MechLocation.RL, 0.09),
    (MechLocation.HEAD, 0.07),
)
DAMAGE_CHAIN: tuple[MechLocation, ...] = tuple(location for location, _ in _LOCATION_SPECS)
HIT_WEIGHTS: dict[MechLocation, float] = dict(_LOCATION_SPECS)
TOTAL_HIT_WEIGHT = sum(HIT_WEIGHTS.values())

LEG_MOBILITY_PENALTY = 0.25
SIDE_TORSO_MOBILITY_PENALTY = 0.15

# 中型机甲模板（armor, structure）：总装甲 45 / 结构 36 / 总余量 81。
_MEDIUM_TEMPLATE: dict[MechLocation, tuple[int, int]] = {
    MechLocation.HEAD: (5, 3),
    MechLocation.CT: (12, 9),
    MechLocation.LT: (6, 6),
    MechLocation.RT: (6, 6),
    MechLocation.LA: (4, 3),
    MechLocation.RA: (4, 3),
    MechLocation.LL: (4, 3),
    MechLocation.RL: (4, 3),
}


class RollRng(Protocol):
    """部位命中判定所需随机源协议（random.Random 天然满足）。"""

    def random(self) -> float: ...


def roll_location(rng: RollRng) -> MechLocation:
    """按弹着部位权重随机（累计分布；CT 桶居首保证 r=0 落 CT）。"""
    roll = rng.random() * TOTAL_HIT_WEIGHT
    cumulative = 0.0
    for location, weight in HIT_WEIGHTS.items():
        cumulative += weight
        if roll < cumulative:
            return location
    return MechLocation.HEAD  # 浮点兜底（权重和恰为 1.0 时不可达）


def _allocate_largest_remainder(total: int, quotas: dict[MechLocation, float]) -> dict[MechLocation, int]:
    """最大余数法整数化配额：余数大者优先补 1，同分按枚举序（确定性）。"""
    caps = {location: math.floor(quota) for location, quota in quotas.items()}
    remaining = total - sum(caps.values())
    by_remainder = sorted(quotas, key=lambda location: quotas[location] - caps[location], reverse=True)
    for location in by_remainder[:remaining]:
        caps[location] += 1
    return caps


class MechChassis:
    """八部位底盘：部位状态、武器槽与部位级伤害结算。"""

    def __init__(self, parts: dict[MechLocation, BodyPart]) -> None:
        self.parts: dict[MechLocation, BodyPart] = parts
        self.slots: dict[MechLocation, list[Weapon]] = {location: [] for location in MechLocation}

    @classmethod
    def from_total(cls, total: int) -> MechChassis:
        """按总余量（armor+structure）缩放中型模板构建底盘。

        最大余数法取整保证总余量精确等于 total；用于 Mech 默认构造
        （hull 参数 → 底盘），使派生 hull 与 M1/M2 聚合血量口径一致。
        """
        template_total = sum(armor + structure for armor, structure in _MEDIUM_TEMPLATE.values())
        quotas = {
            location: (armor + structure) * total / template_total
            for location, (armor, structure) in _MEDIUM_TEMPLATE.items()
        }
        caps = _allocate_largest_remainder(total, quotas)
        parts: dict[MechLocation, BodyPart] = {}
        for location, cap in caps.items():
            armor_share = _MEDIUM_TEMPLATE[location][0] / sum(_MEDIUM_TEMPLATE[location])
            armor = min(round(cap * armor_share), max(0, cap - 1)) if cap >= 2 else 0
            parts[location] = BodyPart(float(armor), float(max(0, cap - armor)))
        return cls(parts)

    @property
    def total_integrity(self) -> float:
        """总余量 = 全部位装甲 + 内部结构（hull 派生视图的数据源）。"""
        return sum(part.armor + part.structure for part in self.parts.values())

    @property
    def destroyed_locations(self) -> frozenset[MechLocation]:
        """已摧毁部位集合。"""
        return frozenset(location for location, part in self.parts.items() if part.destroyed)

    @property
    def mobility_penalty(self) -> float:
        """LL/RL/侧躯摧毁累计的机动惩罚，供移动速度折算（腿 0.25/侧躯 0.15）。"""
        penalty = 0.0
        for location in (MechLocation.LL, MechLocation.RL):
            if self.is_destroyed(location):
                penalty += LEG_MOBILITY_PENALTY
        for location in (MechLocation.LT, MechLocation.RT):
            if self.is_destroyed(location):
                penalty += SIDE_TORSO_MOBILITY_PENALTY
        return penalty

    def is_destroyed(self, location: MechLocation) -> bool:
        return self.parts[location].destroyed

    def apply_damage(self, location: MechLocation, amount: float) -> None:
        """部位伤害：先弹着部位，溢出沿传导链向后，总扣减精确为 min(amount, 总余量)。"""
        remain = float(amount)
        start = DAMAGE_CHAIN.index(location)
        for chain_location in DAMAGE_CHAIN[start:]:
            if remain <= 0.0:
                return
            remain = self.parts[chain_location].damage(remain)

    def scale_integrity(self, scale: float) -> None:
        """全机等比缩放装甲/结构（遗留聚合伤害 take_damage 的底盘同步路径）。"""
        for part in self.parts.values():
            part.armor = max(0.0, part.armor * scale)
            part.structure = max(0.0, part.structure * scale)

    def mount_weapon(self, weapon: Weapon) -> None:
        """把武器登记到其挂载部位的武器槽。"""
        self.slots[weapon.mounted_location].append(weapon)

    def weapons_at(self, location: MechLocation) -> tuple[Weapon, ...]:
        """该部位武器槽内的全部武器。"""
        return tuple(self.slots[location])


def make_medium_chassis() -> MechChassis:
    """中型机甲底盘工厂：总装甲 45（~40 点级别）/ 结构 36，供测试与战斗用。"""
    return MechChassis(
        {
            location: BodyPart(float(armor), float(structure))
            for location, (armor, structure) in _MEDIUM_TEMPLATE.items()
        }
    )
