"""Turret 炮塔实体单测（P2）：不可移动 / 整体 HP / 无热量 / 守卫开火可用。"""

from __future__ import annotations

from pymc2.domain.entities.mech import Faction, Weapon, WeaponType
from pymc2.domain.entities.mech_chassis import MechLocation
from pymc2.domain.entities.turret import TURRET_DEFAULT_HP, Turret


class TestImmobility:
    def test_mobile_gate_and_speed(self) -> None:
        turret = Turret("et-01", Faction.ENEMY, 0.0, 0.0)
        assert turret.mobile is False
        assert turret.move_speed_px_s == 0.0

    def test_move_to_is_noop(self) -> None:
        """固定工事：move_to 不落移动意图（占位快照恒静态阻挡）。"""
        turret = Turret("et-01", Faction.ENEMY, 100.0, 100.0)
        turret.move_to(500.0, 500.0)
        assert turret.move_target is None
        turret.tick(1.0)
        assert (turret.x, turret.y) == (100.0, 100.0)

    def test_direct_move_target_still_ignored(self) -> None:
        """move_speed 0：即使 move_target 被外部置位也不位移。"""
        turret = Turret("et-01", Faction.ENEMY, 100.0, 100.0)
        turret.move_target = (200.0, 100.0)
        turret.tick(1.0)
        assert (turret.x, turret.y) == (100.0, 100.0)
        assert turret.move_target == (200.0, 100.0)


class TestWholeHpAndHeat:
    def test_default_hp(self) -> None:
        turret = Turret("et-01", Faction.ENEMY, 0.0, 0.0)
        assert turret.max_hull == TURRET_DEFAULT_HP == 60
        assert turret.hull_ratio == 1.0

    def test_whole_hp_no_location_transfer(self) -> None:
        turret = Turret("et-01", Faction.ENEMY, 0.0, 0.0)
        turret.apply_damage(MechLocation.HEAD, 10.0)  # HEAD 摧毁不致死（无部位语义）
        assert turret.alive is True and turret.hull == 50.0
        turret.apply_damage(MechLocation.CT, 50.0)
        assert turret.alive is False and turret.hull == 0.0

    def test_no_heat(self) -> None:
        turret = Turret("et-01", Faction.ENEMY, 0.0, 0.0, weapon=Weapon("ppc", 20, 420.0, 2.0, heat_per_shot=9.0))
        assert turret.weapon.heat_per_shot == 0.0
        assert turret.weapon.wtype is WeaponType.BALLISTIC
        turret.tick(1.0)
        assert turret.heat.heat == 0.0 and turret.heat.shutdown_active is False

    def test_sprite_kind(self) -> None:
        turret = Turret("et-01", Faction.ENEMY, 0.0, 0.0)
        assert turret.sprite_kind == "turret"
