"""直射弹道系统：命中判定、未命中散布与投射物飞行。

M1 简化规则：
- 命中率随距离线性衰减：贴脸 100%，射程边缘 25%，超出射程 0%。
- 未命中时弹着点在目标周围散布（写入 ShotResult，供表现层绘制）。
- 命中判定在开火瞬间结算（目标静止直接判定），投射物到达弹着点后由服务层结算伤害。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from pymc2.domain.entities.mech import Mech
from pymc2.domain.entities.mech_chassis import MechLocation, roll_location

HIT_FALLOFF = 0.75
MIN_SCATTER_PX = 8.0
MAX_SCATTER_PX = 48.0
DEFAULT_PROJECTILE_SPEED_PX_S = 480.0


class ShotRng(Protocol):
    """开火判定所需的随机源协议（random.Random 天然满足）。"""

    def random(self) -> float: ...

    def uniform(self, a: float, b: float) -> float: ...


@dataclass(frozen=True)
class ShotResult:
    """一次开火的判定结果：是否命中、伤害、弹着点与命中部位。"""

    hit: bool
    damage: int
    impact_x: float
    impact_y: float
    location: MechLocation  # 命中部位；未命中无部位语义（恒 CT 占位）


@dataclass
class Projectile:
    """飞行中的直射弹丸：直线飞向弹着点，到达后返回 True 由服务层结算。

    location 为开火瞬间判定的命中部位，弹丸到达后按部位结算伤害。
    """

    attacker_id: str
    target_id: str
    x: float
    y: float
    impact_x: float
    impact_y: float
    damage: int
    will_hit: bool
    location: MechLocation = MechLocation.CT
    speed_px_s: float = DEFAULT_PROJECTILE_SPEED_PX_S

    def advance(self, dt: float) -> bool:
        """推进飞行一段 dt；返回是否已到达弹着点。"""
        dx = self.impact_x - self.x
        dy = self.impact_y - self.y
        dist = math.hypot(dx, dy)
        step = self.speed_px_s * dt
        if dist <= step:
            self.x, self.y = self.impact_x, self.impact_y
            return True
        self.x += dx / dist * step
        self.y += dy / dist * step
        return False


def hit_probability(distance_px: float, weapon_range_px: float) -> float:
    """按距离返回命中率（0.0-1.0）：线性衰减。"""
    if distance_px >= weapon_range_px:
        return 0.0
    if distance_px <= 0.0:
        return 1.0
    return 1.0 - HIT_FALLOFF * (distance_px / weapon_range_px)


def resolve_shot(attacker: Mech, target: Mech, rng: ShotRng) -> ShotResult:
    """结算一次直射开火：命中率乘热量精度惩罚，命中部位按权重随机。"""
    distance = math.hypot(target.x - attacker.x, target.y - attacker.y)
    probability = hit_probability(distance, attacker.weapon.range_px) * attacker.heat.accuracy_multiplier
    if rng.random() < probability:
        return ShotResult(True, attacker.weapon.damage, target.x, target.y, roll_location(rng))
    angle = rng.uniform(0.0, math.tau)
    radius = rng.uniform(MIN_SCATTER_PX, MAX_SCATTER_PX)
    return ShotResult(
        hit=False,
        damage=0,
        impact_x=target.x + math.cos(angle) * radius,
        impact_y=target.y + math.sin(angle) * radius,
        location=MechLocation.CT,
    )
