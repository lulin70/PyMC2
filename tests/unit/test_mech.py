"""Mech 实体单测：移动、冷却推进、伤害致死。"""

from __future__ import annotations

import pytest

from pymc2.domain.entities.mech import Faction, Mech, Weapon


def make_mech(x: float = 100.0, y: float = 100.0, *, hull: int = 100) -> Mech:
    return Mech("m-01", Faction.PLAYER, x, y, hull=hull, weapon=Weapon("ppc", 25, 420.0, 2.0))


class TestMovement:
    def test_move_to_sets_target(self) -> None:
        mech = make_mech()
        mech.move_to(196.0, 100.0)
        assert mech.move_target == (196.0, 100.0)

    def test_tick_moves_toward_target_at_fixed_speed(self) -> None:
        mech = make_mech()
        mech.move_to(292.0, 100.0)  # 距离 192px，速度 96px/s
        mech.tick(1.0)
        assert mech.position == (196.0, 100.0)
        assert mech.move_target == (292.0, 100.0)

    def test_tick_arrives_snaps_and_clears_target(self) -> None:
        mech = make_mech()
        mech.move_to(196.0, 100.0)  # 距离 96px，一步到位
        mech.tick(1.0)
        assert mech.position == (196.0, 100.0)
        assert mech.move_target is None

    def test_tick_moves_along_diagonal(self) -> None:
        mech = make_mech()
        mech.move_to(196.0, 196.0)  # 对角线距离约 135.8px
        mech.tick(1.0)
        assert mech.position[0] == pytest.approx(167.85, abs=0.5)
        assert mech.position[1] == pytest.approx(167.85, abs=0.5)

    def test_dead_mech_does_not_move(self) -> None:
        mech = make_mech()
        mech.move_to(196.0, 100.0)
        mech.take_damage(100)
        mech.tick(1.0)
        assert mech.position == (100.0, 100.0)

    def test_tile_derived_from_pixel(self) -> None:
        mech = make_mech(208.0, 336.0)
        assert mech.tile == (6, 10)


class TestWeaponCooldown:
    def test_weapon_ready_by_default(self) -> None:
        assert make_mech().weapon.ready is True

    def test_cooldown_progresses_with_tick(self) -> None:
        mech = make_mech()
        mech.weapon.cd_remaining = 2.0
        mech.tick(0.5)
        assert mech.weapon.cd_remaining == pytest.approx(1.5)
        assert mech.weapon.ready is False

    def test_cooldown_never_negative(self) -> None:
        mech = make_mech()
        mech.weapon.cd_remaining = 0.3
        mech.tick(1.0)
        assert mech.weapon.cd_remaining == 0.0
        assert mech.weapon.ready is True


class TestDamage:
    def test_take_damage_reduces_hull(self) -> None:
        mech = make_mech()
        killed = mech.take_damage(30)
        assert mech.hull == 70
        assert mech.alive is True
        assert killed is False

    def test_damage_clamps_at_zero_and_dies(self) -> None:
        mech = make_mech()
        killed = mech.take_damage(1000)
        assert mech.hull == 0
        assert mech.alive is False
        assert killed is True

    def test_exact_lethal_damage_kills(self) -> None:
        mech = make_mech()
        killed = mech.take_damage(100)
        assert mech.hull == 0
        assert mech.alive is False
        assert killed is True

    def test_surviving_damage_keeps_alive(self) -> None:
        mech = make_mech()
        mech.take_damage(99)
        assert mech.hull == 1
        assert mech.alive is True

    def test_hull_ratio(self) -> None:
        mech = make_mech()
        assert mech.hull_ratio == pytest.approx(1.0)
        mech.take_damage(75)
        assert mech.hull_ratio == pytest.approx(0.25)
