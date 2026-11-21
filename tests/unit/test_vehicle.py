"""Vehicle 载具实体单测（P2）：整体 HP 无部位传导 / 无热量反应堆 / 可移动 / 协议成员。"""

from __future__ import annotations

import pytest

from pymc2.domain.entities.mech import TILE_SIZE_PX, Faction, FireMode, Weapon, WeaponType
from pymc2.domain.entities.mech_chassis import MechLocation
from pymc2.domain.entities.vehicle import VEHICLE_MOVE_SPEED_PX_S, Vehicle


class TestWholeHp:
    def test_damage_ignores_location(self) -> None:
        """整体 HP：任意部位参数扣减同量（无部位传导/护甲差异）。"""
        vehicle = Vehicle("ev-01", Faction.ENEMY, 100.0, 100.0, hp=80)
        assert vehicle.apply_damage(MechLocation.RA, 30.0) is False
        assert vehicle.hull == 50.0
        assert vehicle.apply_damage(MechLocation.CT, 30.0) is False
        assert vehicle.hull == 20.0

    def test_death_at_zero(self) -> None:
        vehicle = Vehicle("ev-01", Faction.ENEMY, 0.0, 0.0, hp=80)
        assert vehicle.alive is True
        assert vehicle.apply_damage(MechLocation.CT, 80.0) is True
        assert vehicle.hull == 0.0 and vehicle.alive is False
        # 补刀/溢出：hp 钳 0，重复扣不减为负、不死复生
        assert vehicle.apply_damage(MechLocation.CT, 50.0) is True
        assert vehicle.hull == 0.0 and vehicle.alive is False

    def test_zero_and_negative_amount_noop(self) -> None:
        vehicle = Vehicle("ev-01", Faction.ENEMY, 0.0, 0.0, hp=80)
        assert vehicle.apply_damage(MechLocation.CT, 0) is False
        assert vehicle.hull == 80.0

    def test_hull_ratio_and_max(self) -> None:
        vehicle = Vehicle("ev-01", Faction.ENEMY, 0.0, 0.0, hp=80)
        assert vehicle.max_hull == 80 and vehicle.hull_ratio == 1.0
        vehicle.apply_damage(MechLocation.CT, 40.0)
        assert vehicle.hull_ratio == pytest.approx(0.5)

    def test_weapon_operational_tied_to_alive(self) -> None:
        vehicle = Vehicle("ev-01", Faction.ENEMY, 0.0, 0.0, hp=80)
        assert vehicle.weapon_operational is True
        vehicle.apply_damage(MechLocation.CT, 80.0)
        assert vehicle.weapon_operational is False


class TestNoHeat:
    def test_default_weapon_heatless_ballistic(self) -> None:
        vehicle = Vehicle("ev-01", Faction.ENEMY, 0.0, 0.0)
        assert vehicle.weapon.name == "autocannon"
        assert vehicle.weapon.wtype is WeaponType.BALLISTIC
        assert vehicle.weapon.heat_per_shot == 0.0

    def test_injected_weapon_heat_forced_zero(self) -> None:
        """无热量反应堆：注入带产热武器也强制归零（其余参数保留）。"""
        hot = Weapon("mlaser", 15, 360.0, 2.5, heat_per_shot=7.0, wtype=WeaponType.ENERGY)
        vehicle = Vehicle("ev-01", Faction.ENEMY, 0.0, 0.0, weapon=hot)
        assert vehicle.weapon.heat_per_shot == 0.0
        assert vehicle.weapon.damage == 15 and vehicle.weapon.wtype is WeaponType.ENERGY
        vehicle.heat.add_heat(vehicle.weapon.heat_per_shot)
        vehicle.tick(1.0)
        assert vehicle.heat.heat == 0.0
        assert vehicle.heat.shutdown_active is False
        assert vehicle.heat.accuracy_multiplier == 1.0


class TestMobility:
    def test_move_and_advance(self) -> None:
        vehicle = Vehicle("ev-01", Faction.ENEMY, 0.0, 0.0)
        assert vehicle.mobile is True
        assert vehicle.move_speed_px_s == VEHICLE_MOVE_SPEED_PX_S
        vehicle.move_to(120.0, 0.0)
        vehicle.tick(0.5)  # 120 px/s × 0.5s = 60px
        assert vehicle.x == pytest.approx(60.0)
        vehicle.tick(0.5)
        assert (vehicle.x, vehicle.y) == (120.0, 0.0)
        assert vehicle.move_target is None

    def test_no_mobility_penalty(self) -> None:
        vehicle = Vehicle("ev-01", Faction.ENEMY, 0.0, 0.0)
        assert vehicle.mobility_penalty == 0.0

    def test_dead_unit_freezes(self) -> None:
        vehicle = Vehicle("ev-01", Faction.ENEMY, 0.0, 0.0)
        vehicle.move_to(100.0, 0.0)
        vehicle.apply_damage(MechLocation.CT, 80.0)
        vehicle.tick(1.0)
        assert vehicle.x == 0.0


class TestUnitProtocolMembers:
    def test_state_members(self) -> None:
        vehicle = Vehicle("ev-01", Faction.ENEMY, 100.0, 64.0)
        assert vehicle.id == "ev-01"
        assert vehicle.faction is Faction.ENEMY
        assert vehicle.position == (100.0, 64.0)
        assert vehicle.tile == (100 // TILE_SIZE_PX, 64 // TILE_SIZE_PX)
        assert vehicle.fire_mode is FireMode.FREE
        assert vehicle.guarding is False
        assert vehicle.attack_target_id is None
        assert vehicle.move_target is None
        assert vehicle.pilot is None
        assert vehicle.sprite_kind == "vehicle"
