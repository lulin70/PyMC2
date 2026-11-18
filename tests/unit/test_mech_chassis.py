"""MechChassis 单测（M3）：部位摧毁顺序、致命规则、武器槽、mobility、命中权重分布。"""

from __future__ import annotations

import random

import pytest

from pymc2.domain.entities.mech import Faction, Mech, Weapon
from pymc2.domain.entities.mech_chassis import (
    HIT_WEIGHTS,
    SIDE_TORSO_MOBILITY_PENALTY,
    TOTAL_HIT_WEIGHT,
    BodyPart,
    MechChassis,
    MechLocation,
    make_medium_chassis,
    roll_location,
)


class StaticRng:
    """固定随机源：random() 恒返回 value。"""

    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value


def make_mech(
    *,
    chassis: MechChassis | None = None,
    weapon: Weapon | None = None,
    hull: int = 100,
) -> Mech:
    return Mech("m-01", Faction.PLAYER, 0.0, 0.0, hull=hull, weapon=weapon, chassis=chassis)


class TestBodyPart:
    def test_damage_absorbed_by_armor_first(self) -> None:
        part = BodyPart(armor=10.0, structure=5.0)
        leftover = part.damage(4.0)
        assert leftover == 0.0
        assert part.armor == 6.0
        assert part.structure == 5.0
        assert part.destroyed is False

    def test_damage_overflow_spills_into_structure(self) -> None:
        part = BodyPart(armor=10.0, structure=5.0)
        leftover = part.damage(12.0)
        assert leftover == 0.0
        assert part.armor == 0.0
        assert part.structure == 3.0
        assert part.destroyed is False

    def test_structure_zero_destroys_part(self) -> None:
        part = BodyPart(armor=10.0, structure=5.0)
        leftover = part.damage(15.0)
        assert leftover == 0.0
        assert part.destroyed is True

    def test_overflow_beyond_capacity_is_returned(self) -> None:
        part = BodyPart(armor=10.0, structure=5.0)
        assert part.damage(20.0) == 5.0
        assert part.destroyed is True

    def test_destroyed_part_passes_full_damage_through(self) -> None:
        part = BodyPart(armor=10.0, structure=5.0)
        part.damage(15.0)
        assert part.damage(3.0) == 3.0

    def test_zero_damage_is_noop(self) -> None:
        part = BodyPart(armor=10.0, structure=5.0)
        assert part.damage(0.0) == 0.0
        assert (part.armor, part.structure) == (10.0, 5.0)

    def test_structure_zero_at_birth_is_destroyed(self) -> None:
        assert BodyPart(armor=10.0, structure=0.0).destroyed is True


class TestMediumChassisFactory:
    def test_armor_total_is_medium_class(self) -> None:
        chassis = make_medium_chassis()
        assert sum(part.armor for part in chassis.parts.values()) == 45.0

    def test_structure_total(self) -> None:
        chassis = make_medium_chassis()
        assert sum(part.structure for part in chassis.parts.values()) == 36.0

    def test_total_integrity(self) -> None:
        assert make_medium_chassis().total_integrity == 81.0

    def test_has_all_eight_locations(self) -> None:
        assert set(make_medium_chassis().parts) == set(MechLocation)

    def test_no_part_destroyed_at_birth(self) -> None:
        assert make_medium_chassis().destroyed_locations == frozenset()

    def test_factory_returns_fresh_instances(self) -> None:
        first, second = make_medium_chassis(), make_medium_chassis()
        first.apply_damage(MechLocation.CT, 5.0)
        assert first.parts[MechLocation.CT].armor == 7.0
        assert second.parts[MechLocation.CT].armor == 12.0


class TestFromTotal:
    def test_total_matches_requested(self) -> None:
        assert MechChassis.from_total(100).total_integrity == 100.0
        assert MechChassis.from_total(50).total_integrity == 50.0

    def test_every_part_has_armor_and_structure(self) -> None:
        chassis = MechChassis.from_total(100)
        for part in chassis.parts.values():
            assert part.armor >= 1.0
            assert part.structure >= 1.0

    def test_ct_absorbs_single_ppc_hit_without_destruction(self) -> None:
        """M1/M2 兼容钉：from_total(100) 的 CT 必须完整吸收一发 25 伤害。"""
        chassis = MechChassis.from_total(100)
        chassis.apply_damage(MechLocation.CT, 25)
        assert chassis.is_destroyed(MechLocation.CT) is False
        assert chassis.total_integrity == 75.0

    def test_ct_repeated_hits_destroy_and_keep_total_exact(self) -> None:
        """M2 e2e 兼容钉：25 + 15 两发 → CT 摧毁、总余量精确 60。"""
        chassis = MechChassis.from_total(100)
        chassis.apply_damage(MechLocation.CT, 25)
        chassis.apply_damage(MechLocation.CT, 15)
        assert chassis.is_destroyed(MechLocation.CT) is True
        assert chassis.total_integrity == 60.0


class TestOverflowChain:
    def test_overflow_chains_to_next_location(self) -> None:
        chassis = make_medium_chassis()  # LA 容量 7（armor 4 + structure 3）
        chassis.apply_damage(MechLocation.LA, 10.0)
        assert chassis.is_destroyed(MechLocation.LA) is True
        assert chassis.parts[MechLocation.RA].armor == 1.0  # 溢出 3 打在 RA 装甲
        assert chassis.total_integrity == 71.0

    def test_chain_absorbs_from_struck_location_onward(self) -> None:
        """传导链自弹着位向后：CT/LT/RT 位于 LA 之前，不参与 LA 溢出。"""
        chassis = make_medium_chassis()
        chassis.apply_damage(MechLocation.LA, 1000.0)
        assert chassis.destroyed_locations == frozenset(
            {MechLocation.LA, MechLocation.RA, MechLocation.LL, MechLocation.RL, MechLocation.HEAD}
        )
        assert chassis.total_integrity == 45.0  # CT 21 + LT 12 + RT 12 完好

    def test_chain_end_overflow_is_wasted(self) -> None:
        """HEAD 是传导链末端：超出容量部分无处传导，按 BT 规则作废。"""
        chassis = make_medium_chassis()
        chassis.apply_damage(MechLocation.HEAD, 999.0)
        assert chassis.destroyed_locations == frozenset({MechLocation.HEAD})
        assert chassis.total_integrity == 73.0


class TestDestructionRules:
    def test_ct_destroyed_kills_mech(self) -> None:
        mech = make_mech(chassis=make_medium_chassis())
        mech.chassis.apply_damage(MechLocation.CT, 21.0)  # CT 容量 21
        assert mech.chassis.is_destroyed(MechLocation.CT) is True
        assert mech.alive is False

    def test_head_destroyed_kills_mech(self) -> None:
        mech = make_mech(chassis=make_medium_chassis())
        mech.chassis.apply_damage(MechLocation.HEAD, 8.0)  # HEAD 容量 8
        assert mech.chassis.is_destroyed(MechLocation.HEAD) is True
        assert mech.alive is False

    def test_head_partial_damage_keeps_alive(self) -> None:
        mech = make_mech(chassis=make_medium_chassis())
        mech.chassis.apply_damage(MechLocation.HEAD, 7.0)
        assert mech.alive is True

    def test_side_torso_destroyed_survives_with_mobility_penalty(self) -> None:
        mech = make_mech(chassis=make_medium_chassis())
        mech.chassis.apply_damage(MechLocation.LT, 12.0)  # LT 容量 12
        assert mech.alive is True
        assert mech.chassis.mobility_penalty == pytest.approx(SIDE_TORSO_MOBILITY_PENALTY)

    def test_both_side_torsos_stack_penalty(self) -> None:
        mech = make_mech(chassis=make_medium_chassis())
        mech.chassis.apply_damage(MechLocation.LT, 12.0)
        mech.chassis.apply_damage(MechLocation.RT, 12.0)
        assert mech.alive is True
        assert mech.chassis.mobility_penalty == pytest.approx(0.30)

    def test_leg_destroyed_survives_with_mobility_penalty(self) -> None:
        mech = make_mech(chassis=make_medium_chassis())
        mech.chassis.apply_damage(MechLocation.LL, 7.0)  # LL 容量 7
        assert mech.alive is True
        assert mech.chassis.mobility_penalty == pytest.approx(0.25)

    def test_both_legs_stack_penalty(self) -> None:
        mech = make_mech(chassis=make_medium_chassis())
        mech.chassis.apply_damage(MechLocation.LL, 7.0)
        mech.chassis.apply_damage(MechLocation.RL, 7.0)
        assert mech.alive is True
        assert mech.chassis.mobility_penalty == pytest.approx(0.50)

    def test_destroyed_locations_tracks_set(self) -> None:
        mech = make_mech(chassis=make_medium_chassis())
        mech.chassis.apply_damage(MechLocation.LL, 7.0)
        assert mech.chassis.destroyed_locations == frozenset({MechLocation.LL})


class TestWeaponSlots:
    def test_mount_weapon_registers_at_location(self) -> None:
        chassis = make_medium_chassis()
        weapon = Weapon("ppc", 25, 420.0, 2.0, mounted_location=MechLocation.RA)
        chassis.mount_weapon(weapon)
        assert chassis.weapons_at(MechLocation.RA) == (weapon,)
        assert chassis.weapons_at(MechLocation.LA) == ()

    def test_arm_destroyed_disables_mounted_weapon(self) -> None:
        weapon = Weapon("armgun", 5, 300.0, 1.0, mounted_location=MechLocation.LA)
        mech = make_mech(chassis=make_medium_chassis(), weapon=weapon)
        assert mech.weapon_operational is True
        mech.chassis.apply_damage(MechLocation.LA, 7.0)
        assert mech.chassis.is_destroyed(MechLocation.LA) is True
        assert mech.weapon_operational is False
        assert mech.alive is True

    def test_arm_destruction_keeps_torso_weapon_operational(self) -> None:
        weapon = Weapon("torso gun", 5, 300.0, 1.0, mounted_location=MechLocation.CT)
        mech = make_mech(chassis=make_medium_chassis(), weapon=weapon)
        mech.chassis.apply_damage(MechLocation.LA, 7.0)
        assert mech.weapon_operational is True

    def test_mech_mounts_weapon_on_construction(self) -> None:
        weapon = Weapon("ppc", 25, 420.0, 2.0)
        mech = make_mech(weapon=weapon)
        assert mech.chassis.weapons_at(MechLocation.CT) == (weapon,)


class TestMobilityMovement:
    def test_intact_chassis_moves_at_full_speed(self) -> None:
        mech = make_mech(chassis=make_medium_chassis())
        mech.move_to(96.0, 0.0)
        mech.tick(1.0)
        assert mech.x == pytest.approx(96.0)

    def test_destroyed_leg_reduces_move_speed(self) -> None:
        mech = make_mech(chassis=make_medium_chassis())
        mech.chassis.apply_damage(MechLocation.LL, 7.0)  # penalty 0.25 → 96 × 0.75
        mech.move_to(96.0, 0.0)
        mech.tick(1.0)
        assert mech.x == pytest.approx(72.0)


class TestLegacyTakeDamage:
    def test_take_damage_reduces_total_exactly(self) -> None:
        mech = make_mech()
        mech.take_damage(30)
        assert mech.hull == 70
        assert mech.alive is True

    def test_take_damage_fatal_at_zero_total(self) -> None:
        mech = make_mech()
        assert mech.take_damage(1000) is True
        assert mech.hull == 0
        assert mech.alive is False

    def test_take_damage_never_destroys_parts_while_total_positive(self) -> None:
        """遗留路径语义：总余量未归零前不摧毁任何部位（等比摊派）。"""
        mech = make_mech()
        mech.take_damage(99)
        assert mech.hull == 1
        assert mech.alive is True
        assert mech.chassis.destroyed_locations == frozenset()

    def test_hull_derived_from_chassis(self) -> None:
        mech = make_mech()
        mech.chassis.apply_damage(MechLocation.CT, 25)
        assert mech.hull == 75


class TestHitLocationWeights:
    def test_weights_sum_to_one(self) -> None:
        assert sum(HIT_WEIGHTS.values()) == pytest.approx(1.0)
        assert sum(HIT_WEIGHTS.values()) == TOTAL_HIT_WEIGHT

    def test_ct_highest_and_head_lowest(self) -> None:
        assert HIT_WEIGHTS[MechLocation.CT] == max(HIT_WEIGHTS.values())
        assert HIT_WEIGHTS[MechLocation.HEAD] == min(HIT_WEIGHTS.values())
        assert HIT_WEIGHTS[MechLocation.LT] == HIT_WEIGHTS[MechLocation.RT]
        assert HIT_WEIGHTS[MechLocation.LL] == HIT_WEIGHTS[MechLocation.RL]

    def test_zero_roll_lands_ct(self) -> None:
        """StaticRng(0.0) 兼容钉：r=0 落 CT（累计分布首桶）。"""
        assert roll_location(StaticRng(0.0)) is MechLocation.CT

    def test_large_sample_distribution_matches_weights(self) -> None:
        rng = random.Random(42)
        samples = 20000
        counts = {location: 0 for location in MechLocation}
        for _ in range(samples):
            counts[roll_location(rng)] += 1
        for location, weight in HIT_WEIGHTS.items():
            observed = counts[location] / samples
            assert observed == pytest.approx(weight, abs=0.02), f"{location}: {observed} vs {weight}"
