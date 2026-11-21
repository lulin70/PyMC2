"""武器三分类（P2）弹道剖面单测：弹速倍率 / ENERGY 恒 CT 直击 / MISSILE 齐射均摊与末端加速。"""

from __future__ import annotations

import pytest

from pymc2.domain.entities.mech import Faction, Mech, Weapon, WeaponType
from pymc2.domain.entities.mech_chassis import MechLocation
from pymc2.domain.systems.ballistic import (
    DEFAULT_PROJECTILE_SPEED_PX_S,
    MISSILE_ACCEL_PX_S2,
    MISSILE_VOLLEY_COUNT,
    MISSILE_VOLLEY_SPREAD_PX,
    WEAPON_SPEED_MULTIPLIER,
    Projectile,
    ShotRng,
    build_projectiles,
    resolve_shot,
)


class StaticRng:
    """固定随机源：random 恒 value（0.0 → 全命中），uniform 取中值 → 散布确定性。"""

    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def random(self) -> float:
        return self.value

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


class UpperRng:
    """uniform 恒取上界的随机源（弹着点散布上界确定性断言用）。"""

    def random(self) -> float:
        return 0.0

    def uniform(self, a: float, b: float) -> float:
        return b


def _attacker(weapon: Weapon) -> Mech:
    return Mech("px-01", Faction.PLAYER, 100.0, 100.0, weapon=weapon)


def _target() -> Mech:
    return Mech("ex-01", Faction.ENEMY, 400.0, 100.0)


class TestSpeedMultiplierTable:
    def test_profile_constants(self) -> None:
        assert WEAPON_SPEED_MULTIPLIER[WeaponType.BALLISTIC] == 1.5
        assert WEAPON_SPEED_MULTIPLIER[WeaponType.ENERGY] == 1.0
        assert WEAPON_SPEED_MULTIPLIER[WeaponType.MISSILE] == 0.7

    def test_weapon_default_is_ballistic(self) -> None:
        assert Weapon("legacy", 10, 400.0, 2.0).wtype is WeaponType.BALLISTIC


class TestBallisticProfile:
    def test_single_fast_projectile_rolled_location(self) -> None:
        weapon = Weapon("ppc", 25, 420.0, 2.0, wtype=WeaponType.BALLISTIC)
        attacker, target = _attacker(weapon), _target()
        result = resolve_shot(attacker, target, StaticRng(0.0))
        projectiles = build_projectiles(attacker, target, result, StaticRng(0.0), DEFAULT_PROJECTILE_SPEED_PX_S)
        assert len(projectiles) == 1
        p = projectiles[0]
        assert p.wtype is WeaponType.BALLISTIC
        assert p.speed_px_s == pytest.approx(DEFAULT_PROJECTILE_SPEED_PX_S * 1.5)
        assert p.damage == 25 and p.will_hit is True
        assert p.location == result.location  # 部位权重判定原样携带


class TestEnergyProfile:
    def test_single_projectile_always_center_torso(self) -> None:
        """ENERGY 无视部位加成：命中恒 CT 直击（跳过部位权重判定）。"""
        weapon = Weapon("laser", 20, 400.0, 2.0, wtype=WeaponType.ENERGY)
        attacker, target = _attacker(weapon), Mech("ex-01", Faction.ENEMY, 200.0, 100.0)
        result = resolve_shot(attacker, target, StaticRng(0.5))  # 贴近命中 + 部位判定滚出非 CT
        assert result.hit is True and result.location is not MechLocation.CT
        p = build_projectiles(attacker, target, result, StaticRng(0.5), DEFAULT_PROJECTILE_SPEED_PX_S)[0]
        assert p.wtype is WeaponType.ENERGY
        assert p.location is MechLocation.CT
        assert p.damage == 20
        assert p.speed_px_s == pytest.approx(DEFAULT_PROJECTILE_SPEED_PX_S)


class TestMissileProfile:
    def test_volley_count_and_damage_split(self) -> None:
        """三发齐射：伤害均摊、余数记首发（10 → 4/3/3）。"""
        weapon = Weapon("lrm", 10, 400.0, 3.0, wtype=WeaponType.MISSILE)
        attacker, target = _attacker(weapon), _target()
        result = resolve_shot(attacker, target, StaticRng(0.0))
        rng: ShotRng = StaticRng(0.0)
        projectiles = build_projectiles(attacker, target, result, rng, DEFAULT_PROJECTILE_SPEED_PX_S)
        assert len(projectiles) == MISSILE_VOLLEY_COUNT == 3
        assert [p.damage for p in projectiles] == [4, 3, 3]
        assert all(p.wtype is WeaponType.MISSILE and p.will_hit for p in projectiles)
        assert all(p.location is result.location for p in projectiles)
        # StaticRng uniform 取中值 → 弹着点与判定弹着点重合（散布区间 [a,b] 中点 = result 值偏移 0）
        assert all(p.impact_x == target.x and p.impact_y == target.y for p in projectiles)
        assert all(p.speed_px_s == pytest.approx(DEFAULT_PROJECTILE_SPEED_PX_S * 0.7) for p in projectiles)

    def test_volley_scatter_within_spread(self) -> None:
        weapon = Weapon("lrm", 9, 400.0, 3.0, wtype=WeaponType.MISSILE)
        attacker, target = _attacker(weapon), _target()
        result = resolve_shot(attacker, target, StaticRng(0.0))
        rng: ShotRng = UpperRng()  # uniform 恒取上界 → 弹着点 = 判定点 + spread 上限
        projectiles = build_projectiles(attacker, target, result, rng, DEFAULT_PROJECTILE_SPEED_PX_S)
        for p in projectiles:
            assert p.impact_x == pytest.approx(target.x + MISSILE_VOLLEY_SPREAD_PX)
            assert p.impact_y == pytest.approx(target.y + MISSILE_VOLLEY_SPREAD_PX)

    def test_miss_volley_shares_single_judgement(self) -> None:
        """齐射单次判定：未命中 → 三发同 will_hit=False、CT 占位、零伤。"""
        weapon = Weapon("lrm", 30, 400.0, 3.0, wtype=WeaponType.MISSILE)
        attacker, target = _attacker(weapon), _target()
        result = resolve_shot(attacker, target, StaticRng(0.999))  # 距离 300/range 400 → p≈0.44
        assert result.hit is False
        projectiles = build_projectiles(attacker, target, result, StaticRng(1.0), DEFAULT_PROJECTILE_SPEED_PX_S)
        assert len(projectiles) == 3
        assert all(p.will_hit is False and p.damage == 0 for p in projectiles)
        assert all(p.location is MechLocation.CT for p in projectiles)


class TestMissileTerminalAcceleration:
    def test_projectile_speeds_up_in_flight(self) -> None:
        p = Projectile(
            attacker_id="a",
            target_id="b",
            x=0.0,
            y=0.0,
            impact_x=480.0,
            impact_y=0.0,
            damage=3,
            will_hit=True,
            speed_px_s=DEFAULT_PROJECTILE_SPEED_PX_S * 0.7,
            wtype=WeaponType.MISSILE,
        )
        speeds = [p.speed_px_s]
        for _ in range(10):
            if p.advance(0.1):
                break
            speeds.append(p.speed_px_s)
        assert len(speeds) >= 2
        assert speeds[-1] > speeds[0]  # 末端加速：速度单调增
        assert speeds[1] == pytest.approx(speeds[0] + MISSILE_ACCEL_PX_S2 * 0.1)

    def test_ballistic_projectile_constant_speed(self) -> None:
        p = Projectile(
            attacker_id="a",
            target_id="b",
            x=0.0,
            y=0.0,
            impact_x=480.0,
            impact_y=0.0,
            damage=25,
            will_hit=True,
        )
        base = p.speed_px_s
        p.advance(0.1)
        assert p.speed_px_s == pytest.approx(base)  # 非 MISSILE 恒速
