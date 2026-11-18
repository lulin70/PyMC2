"""salvage / refit 单测（M5）：折算口径、repair/hire RP 边界、幸存者携带回写。"""

from __future__ import annotations

from pymc2.domain.entities.mech import Faction, Mech
from pymc2.domain.entities.mech_chassis import MechLocation
from pymc2.services.battle_service import BattleService
from pymc2.services.campaign_state import CampaignState, make_roster_mech, start_campaign
from pymc2.services.refit_service import HIRE_COST_RP, RefitService
from pymc2.services.salvage_service import SALVAGE_BASE_RP, SalvageService


def _battle_with(mechs: list[Mech]) -> BattleService:
    service = BattleService()
    for mech in mechs:
        service.add_mech(mech)
    return service


class TestSalvageValue:
    def test_full_integrity_wreck_pays_base(self) -> None:
        wreck = Mech("ex-01", Faction.ENEMY, 0.0, 0.0)
        assert SalvageService.salvage_value(wreck) == SALVAGE_BASE_RP

    def test_half_integrity_wreck_pays_half(self) -> None:
        wreck = Mech("ex-01", Faction.ENEMY, 0.0, 0.0)
        wreck.take_damage(wreck.max_hull / 2)
        assert SalvageService.salvage_value(wreck) == round(SALVAGE_BASE_RP / 2)

    def test_empty_wreck_pays_zero(self) -> None:
        wreck = Mech("ex-01", Faction.ENEMY, 0.0, 0.0)
        wreck.take_damage(wreck.max_hull)  # 全空残骸
        assert SalvageService.salvage_value(wreck) == 0

    def test_destroyed_mech_uses_part_integrity(self) -> None:
        """部位摧毁机甲按剩余部位完好度折算（CT 打穿全毁，但溢出未清空的部位仍有余量）。"""
        wreck = Mech("ex-01", Faction.ENEMY, 0.0, 0.0)
        wreck.apply_damage(MechLocation.CT, 27.0)  # CT(armor+structure) 打穿 → 全毁，少量溢出
        assert not wreck.alive
        assert 0 < wreck.hull < wreck.max_hull
        assert SalvageService.salvage_value(wreck) == round(wreck.hull / wreck.max_hull * SALVAGE_BASE_RP)


class TestSalvageCollect:
    def test_collect_only_counts_destroyed_enemies(self) -> None:
        state = CampaignState("t", requisition_points=0)
        wreck = Mech("ex-01", Faction.ENEMY, 0.0, 0.0)
        wreck.apply_damage(MechLocation.CT, 27.0)  # 摧毁但残骸有余量 → 有 salvage
        assert not wreck.alive
        survivor = Mech("ex-02", Faction.ENEMY, 0.0, 0.0)
        friend = Mech("px-01", Faction.PLAYER, 0.0, 0.0)
        friend.take_damage(50.0)  # 己方损毁不计 salvage
        earned = SalvageService().collect(state, _battle_with([wreck, survivor, friend]))
        assert earned == round(wreck.hull / wreck.max_hull * SALVAGE_BASE_RP)
        assert earned > 0
        assert state.requisition_points == earned

    def test_collect_zero_when_no_wrecks(self) -> None:
        state = CampaignState("t", requisition_points=10)
        survivor = Mech("ex-01", Faction.ENEMY, 0.0, 0.0)
        SalvageService().collect(state, _battle_with([survivor]))
        assert state.requisition_points == 10


class TestRepair:
    def test_repair_restores_full_integrity_and_charges_half_per_point(self) -> None:
        state = start_campaign()
        mech = state.roster[0]
        mech.take_damage(40.0)
        refit = RefitService()
        assert refit.repair_cost(mech) == 20
        assert refit.repair(state, "px-01") is True
        assert mech.hull == mech.max_hull
        assert state.requisition_points == 30 - 20

    def test_repair_restores_part_armor_not_just_total(self) -> None:
        """修复重铺部位装甲/结构（弹着部位余量回到满值，而非仅聚合血量回升）。"""
        state = start_campaign()
        mech = state.roster[0]
        before_ct = mech.chassis.parts[MechLocation.CT].armor
        mech.apply_damage(MechLocation.CT, 5.0)
        assert RefitService().repair(state, "px-01") is True
        assert mech.chassis.parts[MechLocation.CT].armor == before_ct

    def test_repair_rejected_when_rp_insufficient(self) -> None:
        state = start_campaign()
        state.requisition_points = 5
        state.roster[0].take_damage(40.0)
        assert RefitService().repair(state, "px-01") is False
        assert state.requisition_points == 5
        assert state.roster[0].hull < state.roster[0].max_hull

    def test_repair_rejected_when_already_full(self) -> None:
        state = start_campaign()
        assert RefitService().repair(state, "px-01") is False
        assert state.requisition_points == 30

    def test_repair_rejected_for_unknown_id(self) -> None:
        state = start_campaign()
        assert RefitService().repair(state, "px-99") is False


class TestHire:
    def test_hire_appends_new_mech_with_rookie_pilot(self) -> None:
        state = start_campaign()
        state.requisition_points = 100  # hire 测试独立于起始 RP（40 > 起始 30）
        hired = RefitService().hire(state)
        assert hired is not None
        assert hired.id == "px-03"
        assert hired.pilot is not None
        assert hired.pilot.name == "recruit-px-03"
        assert hired.hull == hired.max_hull
        assert [m.id for m in state.roster] == ["px-01", "px-02", "px-03"]
        assert state.requisition_points == 100 - HIRE_COST_RP

    def test_hire_rejected_when_rp_insufficient(self) -> None:
        state = start_campaign()
        state.requisition_points = HIRE_COST_RP - 1
        assert RefitService().hire(state) is None
        assert len(state.roster) == 2
        assert state.requisition_points == HIRE_COST_RP - 1

    def test_hire_id_increments_from_existing_roster(self) -> None:
        state = start_campaign()
        state.requisition_points = 100
        state.roster.append(make_roster_mech("px-07", ("mlaser", 15, 380.0, 1.5), "x"))
        hired = RefitService().hire(state)
        assert hired is not None and hired.id == "px-08"


class TestCarryoverSurvivors:
    def test_survivor_state_written_back_and_destroyed_removed(self) -> None:
        state = start_campaign()
        battle = _battle_with([state.roster[0], state.roster[1]])
        px01 = battle.get("px-01")
        px02 = battle.get("px-02")
        assert px01 is not None and px02 is not None and px01.pilot is not None
        px01.take_damage(20.0)
        px01.pilot.record_kill()
        px01.pilot.record_shot(True)
        px02.take_damage(px02.max_hull)  # 阵亡
        survivors = RefitService().carryover_survivors(state, battle)
        assert survivors == ["px-01"]
        assert [m.id for m in state.roster] == ["px-01"]  # 阵亡机甲移出名册
        carried = state.roster[0]
        assert carried is not px01  # 回写为序列化副本（与战斗实例隔离）
        assert carried.hull == px01.hull  # 装甲状态携带
        assert carried.pilot is not None and carried.pilot.kills == 1  # rank 携带源
        assert carried.pilot.gunnery_rank == px01.pilot.gunnery_rank

    def test_enemy_mechs_never_enter_roster(self) -> None:
        state = start_campaign()
        battle = _battle_with([state.roster[0], state.roster[1], Mech("ex-01", Faction.ENEMY, 0.0, 0.0)])
        assert RefitService().carryover_survivors(state, battle) == ["px-01", "px-02"]
        assert [m.id for m in state.roster] == ["px-01", "px-02"]  # 敌方不入名册
