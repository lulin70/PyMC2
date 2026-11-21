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

from pymc2.domain.entities.mech import WeaponType
from pymc2.domain.entities.mech_chassis import MechLocation, roll_location
from pymc2.domain.entities.unit import Unit

HIT_FALLOFF = 0.75
MIN_SCATTER_PX = 8.0
MAX_SCATTER_PX = 48.0
DEFAULT_PROJECTILE_SPEED_PX_S = 480.0

# P2 武器三分类弹道剖面（docs/P2_DESIGN_20261121.md §3）：弹速 × 基础速度的倍率表。
WEAPON_SPEED_MULTIPLIER: dict[WeaponType, float] = {
    WeaponType.BALLISTIC: 1.5,
    WeaponType.ENERGY: 1.0,
    WeaponType.MISSILE: 0.7,
}
# MISSILE 齐射：多发小伤均摊（余数记首发）+ 弹着点散布 + 末端加速。
MISSILE_VOLLEY_COUNT = 3
MISSILE_VOLLEY_SPREAD_PX = 12.0
MISSILE_ACCEL_PX_S2 = 240.0

# M4 驾驶员技能系数：rank 0 → 0.85，rank 5 → 1.15；钳 [0.7, 1.2] 防越界。
# 无驾驶员（skill_rank=None）→ 中性 1.0（M1/M2 兼容口径）。
SKILL_BASE_MULTIPLIER = 0.85
SKILL_RANK_STEP = 0.06
SKILL_MULTIPLIER_MIN = 0.7
SKILL_MULTIPLIER_MAX = 1.2


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
    wtype 为武器三分类（P2）：MISSILE 弹体逐帧末端加速（advance 内），渲染层按类型分色分形。
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
    wtype: WeaponType = WeaponType.BALLISTIC

    def advance(self, dt: float) -> bool:
        """推进飞行一段 dt；返回是否已到达弹着点（MISSILE 末端加速：速度逐帧累加）。"""
        if self.wtype is WeaponType.MISSILE:
            self.speed_px_s += MISSILE_ACCEL_PX_S2 * dt
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


def skill_multiplier(skill_rank: float | None) -> float:
    """驾驶员技能系数：0.85 + rank*0.06，钳 [0.7, 1.2]；无驾驶员（None）→ 1.0。"""
    if skill_rank is None:
        return 1.0
    raw = SKILL_BASE_MULTIPLIER + SKILL_RANK_STEP * skill_rank
    return max(SKILL_MULTIPLIER_MIN, min(SKILL_MULTIPLIER_MAX, raw))


def hit_probability(distance_px: float, weapon_range_px: float, *, skill_rank: float | None = None) -> float:
    """按距离返回命中率（0.0-1.0）：线性衰减 × 驾驶员技能系数（M4 消费点）。"""
    if distance_px >= weapon_range_px:
        return 0.0
    base = 1.0 if distance_px <= 0.0 else 1.0 - HIT_FALLOFF * (distance_px / weapon_range_px)
    return min(1.0, base * skill_multiplier(skill_rank))


def resolve_shot(attacker: Unit, target: Unit, rng: ShotRng) -> ShotResult:
    """结算一次直射开火：命中率乘技能系数与热量精度惩罚，命中部位按权重随机。"""
    distance = math.hypot(target.x - attacker.x, target.y - attacker.y)
    skill_rank = attacker.pilot.gunnery_rank if attacker.pilot is not None else None
    probability = hit_probability(distance, attacker.weapon.range_px, skill_rank=skill_rank)
    probability *= attacker.heat.accuracy_multiplier
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


def build_projectiles(
    attacker: Unit,
    target: Unit,
    result: ShotResult,
    rng: ShotRng,
    base_speed_px_s: float,
) -> list[Projectile]:
    """武器三分类弹丸构造（P2）：按 attacker.weapon.wtype 生成 1 发（BALLISTIC/ENERGY）
    或 MISSILE_VOLLEY_COUNT 发（MISSILE）投射物；弹速按类型倍率缩放。

    - BALLISTIC：部位权重判定结果（result.location）原样携带，直射快弹；
    - ENERGY：无视部位加成直伤——恒 CT 直击（命中判定/散布与存量同口径）；
    - MISSILE：齐射单次命中率判定（同一 will_hit），伤害按判定伤害均摊（余数记首发，
      未命中 → 全零伤，与单发路径 result.damage 口径一致），各发弹着点在判定弹着点
      周围 ±MISSILE_VOLLEY_SPREAD_PX 散布；飞行中末端加速（Projectile.advance）。
    """
    weapon = attacker.weapon
    speed = base_speed_px_s * WEAPON_SPEED_MULTIPLIER[weapon.wtype]
    if weapon.wtype is not WeaponType.MISSILE:
        location = MechLocation.CT if weapon.wtype is WeaponType.ENERGY else result.location
        return [
            Projectile(
                attacker_id=attacker.id,
                target_id=target.id,
                x=attacker.x,
                y=attacker.y,
                impact_x=result.impact_x,
                impact_y=result.impact_y,
                damage=result.damage,
                will_hit=result.hit,
                location=location,
                speed_px_s=speed,
                wtype=weapon.wtype,
            )
        ]
    base_damage = result.damage // MISSILE_VOLLEY_COUNT
    damages = [base_damage] * MISSILE_VOLLEY_COUNT
    damages[0] += result.damage - base_damage * MISSILE_VOLLEY_COUNT
    projectiles: list[Projectile] = []
    for damage in damages:
        projectiles.append(
            Projectile(
                attacker_id=attacker.id,
                target_id=target.id,
                x=attacker.x,
                y=attacker.y,
                impact_x=result.impact_x + rng.uniform(-MISSILE_VOLLEY_SPREAD_PX, MISSILE_VOLLEY_SPREAD_PX),
                impact_y=result.impact_y + rng.uniform(-MISSILE_VOLLEY_SPREAD_PX, MISSILE_VOLLEY_SPREAD_PX),
                damage=damage,
                will_hit=result.hit,
                location=result.location if result.hit else MechLocation.CT,
                speed_px_s=speed,
                wtype=WeaponType.MISSILE,
            )
        )
    return projectiles
