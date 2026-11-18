"""弹道系统单测：命中率边界、命中/未命中判定、投射物飞行。"""

from __future__ import annotations

import math

import pytest

from pymc2.domain.entities.mech import Faction, Mech, Weapon
from pymc2.domain.systems.ballistic import (
    DEFAULT_PROJECTILE_SPEED_PX_S,
    MAX_SCATTER_PX,
    MIN_SCATTER_PX,
    Projectile,
    hit_probability,
    resolve_shot,
)


class StaticRng:
    """固定随机源：random() 恒返回 value，uniform() 取中点（确定性散布）。"""

    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


def make_pair(distance: float) -> tuple[Mech, Mech]:
    attacker = Mech("a", Faction.PLAYER, 0.0, 0.0, weapon=Weapon("ppc", 25, 420.0, 2.0))
    target = Mech("b", Faction.ENEMY, distance, 0.0, weapon=Weapon("mlaser", 15, 360.0, 2.5))
    return attacker, target


class TestHitProbability:
    def test_point_blank_is_certain(self) -> None:
        assert hit_probability(0.0, 420.0) == pytest.approx(1.0)

    def test_linear_falloff_midpoint(self) -> None:
        assert hit_probability(210.0, 420.0) == pytest.approx(0.625)

    def test_range_edge_is_floor(self) -> None:
        assert hit_probability(419.9, 420.0) == pytest.approx(0.25, abs=0.01)

    def test_exactly_at_range_is_zero(self) -> None:
        assert hit_probability(420.0, 420.0) == 0.0

    def test_beyond_range_is_zero(self) -> None:
        assert hit_probability(1000.0, 420.0) == 0.0


class TestResolveShot:
    def test_hit_applies_full_damage_at_target_position(self) -> None:
        attacker, target = make_pair(100.0)
        result = resolve_shot(attacker, target, StaticRng(0.0))
        assert result.hit is True
        assert result.damage == 25
        assert (result.impact_x, result.impact_y) == (100.0, 0.0)

    def test_miss_scatters_around_target(self) -> None:
        attacker, target = make_pair(100.0)
        result = resolve_shot(attacker, target, StaticRng(1.0))
        assert result.hit is False
        assert result.damage == 0
        offset = math.hypot(result.impact_x - target.x, result.impact_y - target.y)
        assert offset == pytest.approx((MIN_SCATTER_PX + MAX_SCATTER_PX) / 2)
        assert MIN_SCATTER_PX <= offset <= MAX_SCATTER_PX

    def test_beyond_range_never_hits_even_with_low_roll(self) -> None:
        attacker, target = make_pair(500.0)
        result = resolve_shot(attacker, target, StaticRng(0.0))
        assert result.hit is False
        assert result.damage == 0


class TestProjectile:
    def test_advance_moves_toward_impact(self) -> None:
        p = Projectile("a", "b", 0.0, 0.0, 480.0, 0.0, 25, True)
        arrived = p.advance(0.5)  # 480px/s * 0.5s = 240px
        assert arrived is False
        assert p.x == pytest.approx(240.0)
        assert p.y == pytest.approx(0.0)

    def test_advance_arrives_and_snaps(self) -> None:
        p = Projectile("a", "b", 0.0, 0.0, 128.0, 0.0, 25, True)
        arrived = p.advance(0.5)  # 240px 步长 > 128px 距离
        assert arrived is True
        assert (p.x, p.y) == (128.0, 0.0)

    def test_default_speed(self) -> None:
        p = Projectile("a", "b", 0.0, 0.0, 100.0, 0.0, 25, True)
        assert p.speed_px_s == DEFAULT_PROJECTILE_SPEED_PX_S
