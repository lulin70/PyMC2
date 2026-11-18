"""战役状态单测（M5）：Pilot/底盘/机甲/战役四级序列化往返（M4 遗留项验收）。"""

from __future__ import annotations

import pytest

from pymc2.domain.entities.mech import Faction, Mech, Weapon
from pymc2.domain.entities.mech_chassis import MechLocation, make_medium_chassis
from pymc2.domain.entities.pilot import Pilot, PilotSkill, SkillStats
from pymc2.services.campaign_state import (
    CampaignState,
    chassis_from_dict,
    chassis_to_dict,
    mech_from_dict,
    mech_to_dict,
    start_campaign,
)


class TestPilotSerialization:
    def test_roundtrip_preserves_name_kills_and_dual_counts(self) -> None:
        pilot = Pilot("veteran-gunner")
        for hit in (True, False, True):
            pilot.record_shot(hit)
        pilot.record_kill()
        restored = Pilot.from_dict(pilot.to_dict())
        assert restored.name == "veteran-gunner"
        assert restored.kills == 1
        stats = restored.skill_stats[PilotSkill.GUNNERY]
        assert (stats.uses, stats.successes) == (3, 2)
        assert restored.gunnery_rank == pilot.gunnery_rank

    def test_roundtrip_rank_carried_for_campaign_carryover(self) -> None:
        """任务间携带核心口径：kills 与双计数全部还原 → rank 逐位一致。"""
        pilot = Pilot("carried")
        for _ in range(6):
            pilot.record_kill()
        for hit in (True, True, False, False):
            pilot.record_shot(hit)
        assert Pilot.from_dict(pilot.to_dict()).gunnery_rank == pilot.gunnery_rank

    def test_unknown_skill_key_raises(self) -> None:
        bad = {"name": "x", "kills": 0, "skill_stats": {"piloting": {"uses": 1, "successes": 0}}}
        with pytest.raises(ValueError, match="piloting"):
            Pilot.from_dict(bad)

    def test_fresh_pilot_roundtrip_is_empty_counts(self) -> None:
        restored = Pilot.from_dict(Pilot("rookie").to_dict())
        assert restored.kills == 0
        assert restored.skill_stats[PilotSkill.GUNNERY] == SkillStats()


def _wounded_mech() -> Mech:
    mech = Mech(
        "px-01",
        Faction.PLAYER,
        224.0,
        336.0,
        hull=100,
        weapon=Weapon("ppc", 25, 420.0, 2.0, mounted_location=MechLocation.RA),
        pilot=Pilot("gunner"),
    )
    mech.apply_damage(MechLocation.CT, 17.0)  # 溢出链总扣减 17 → hull 100→83
    return mech


class TestChassisSerialization:
    def test_roundtrip_keeps_part_integrity_exact(self) -> None:
        chassis = make_medium_chassis()
        chassis.apply_damage(MechLocation.CT, 7.0)
        restored = chassis_from_dict(chassis_to_dict(chassis))
        for location in MechLocation:
            assert restored.parts[location].armor == chassis.parts[location].armor
            assert restored.parts[location].structure == chassis.parts[location].structure
        assert restored.total_integrity == chassis.total_integrity

    def test_destroyed_part_stays_destroyed_after_roundtrip(self) -> None:
        chassis = make_medium_chassis()
        chassis.apply_damage(MechLocation.CT, 100.0)
        restored = chassis_from_dict(chassis_to_dict(chassis))
        assert restored.is_destroyed(MechLocation.CT) is True

    def test_missing_location_raises(self) -> None:
        partial = {"parts": {"ct": {"armor": 12.0, "structure": 9.0}}}
        with pytest.raises(ValueError, match="missing locations"):
            chassis_from_dict(partial)


class TestMechSerialization:
    def test_roundtrip_restores_identity_weapon_chassis_pilot(self) -> None:
        mech = _wounded_mech()
        restored = mech_from_dict(mech_to_dict(mech))
        assert restored.id == "px-01"
        assert restored.faction is Faction.PLAYER
        assert restored.position == (224.0, 336.0)
        assert restored.weapon.name == "ppc"
        assert restored.weapon.mounted_location is MechLocation.RA
        assert restored.chassis.parts[MechLocation.CT].armor == mech.chassis.parts[MechLocation.CT].armor
        assert restored.pilot is not None
        assert restored.pilot.name == "gunner"

    def test_roundtrip_keeps_hull_ratio_denominator(self) -> None:
        """受损机甲往返后 max_hull 保持出厂口径（hull_ratio 分母不塌缩）。"""
        mech = _wounded_mech()
        restored = mech_from_dict(mech_to_dict(mech))
        assert restored.max_hull == mech.max_hull == 100
        assert restored.hull == mech.hull == 83.0
        assert restored.hull_ratio == mech.hull_ratio

    def test_roundtrip_without_pilot(self) -> None:
        mech = Mech("px-02", Faction.PLAYER, 0.0, 0.0)
        restored = mech_from_dict(mech_to_dict(mech))
        assert restored.pilot is None


class TestCampaignStateSerialization:
    def test_roundtrip_full_campaign_state(self) -> None:
        state = start_campaign("uat")
        state.mission_index = 1
        state.requisition_points = 55
        state.roster[0].apply_damage(MechLocation.RT, 5.0)
        if state.roster[0].pilot is not None:
            state.roster[0].pilot.record_kill()
        restored = CampaignState.from_dict(state.to_dict())
        assert restored.campaign_id == "uat"
        assert restored.mission_index == 1
        assert restored.requisition_points == 55
        assert [m.id for m in restored.roster] == ["px-01", "px-02"]
        assert restored.roster[0].hull == state.roster[0].hull
        assert restored.roster[0].pilot is not None
        assert restored.roster[0].pilot.kills == 1

    def test_start_campaign_initial_loadout(self) -> None:
        state = start_campaign()
        assert state.requisition_points == 30
        assert [m.id for m in state.roster] == ["px-01", "px-02"]
        assert all(m.pilot is not None for m in state.roster)
        assert state.mission_index == 0
