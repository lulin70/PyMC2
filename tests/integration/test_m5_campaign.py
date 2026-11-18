"""M5 集成：任务链 3 任务完整走通（开局→清场→结算→整备→携带→下一任务）+ 战败重试路径。"""

from __future__ import annotations

import pytest

from pymc2.domain.entities.mech import Faction
from pymc2.domain.entities.mech_chassis import MechLocation
from pymc2.services.battle_service import BattleService
from pymc2.services.campaign_state import start_campaign
from pymc2.services.mission_chain import MISSION_CHAIN, MissionChain
from pymc2.services.refit_service import HIRE_COST_RP, RefitService


def _wipe_enemies(battle: BattleService, *, leave_hull: bool = True) -> None:
    """确定性清场：敌 CT 打穿全毁（leave_hull 保留溢出后残骸余量供 salvage 折算）。"""
    for mech in battle.mechs.values():
        if mech.faction is Faction.ENEMY and mech.alive:
            mech.apply_damage(
                MechLocation.CT,
                mech.chassis.parts[MechLocation.CT].armor + mech.chassis.parts[MechLocation.CT].structure + 1.0,
            )


def _wipe_player(battle: BattleService) -> None:
    for mech in battle.mechs.values():
        if mech.faction is Faction.PLAYER and mech.alive:
            mech.take_damage(mech.max_hull)


class TestMissionChainWalkthrough:
    def test_three_missions_full_chain_with_refit_carryover(self) -> None:
        """任务 1 → 结算 → 整备 → 任务 2 携带断言 → 任务 3 → 战役完结。"""
        state = start_campaign("it-m5")
        chain = MissionChain()
        refit = RefitService()

        # --- 任务 1：开局（roster 2 台参战 vs 1 台敌）---
        battle1 = chain.start_mission(state)
        assert sorted(battle1.mechs) == ["ex-01", "px-01", "px-02"]
        assert chain.current_def(state).mission_id == "m1_river_crossing"
        px01 = battle1.get("px-01")
        assert px01 is not None and px01.pilot is not None
        px01.pilot.record_kill()  # 模拟战斗中击杀 → rank 成长
        for _ in range(2):
            px01.pilot.record_shot(True)
        px01.take_damage(30.0)  # 模拟受损
        px02 = battle1.get("px-02")
        assert px02 is not None
        px02.take_damage(20.0)

        # --- 任务 1 结算：敌全灭 → 奖励 + salvage + 携带回写 ---
        assert chain.is_victory(battle1) is False
        _wipe_enemies(battle1)
        assert chain.is_victory(battle1) is True
        result1 = chain.complete_mission(state, battle1)
        assert result1.victory is True
        assert result1.rp_reward == 25
        assert result1.rp_salvage > 0
        assert state.mission_index == 1
        rp_after_m1 = state.requisition_points
        assert rp_after_m1 == 30 + 25 + result1.rp_salvage
        assert result1.survivor_ids == ["px-01", "px-02"]
        # 携带回写：受损状态与 pilot 成长已进名册
        carried_px01 = state.roster[0]
        assert carried_px01.hull == px01.hull == carried_px01.max_hull - 30.0
        assert carried_px01.pilot is not None and carried_px01.pilot.kills == 1

        # --- 整备：repair 受损机甲 + hire 新机甲 ---
        repair_cost = refit.repair_cost(state.roster[0])
        assert repair_cost == 15  # 缺口 30 × 0.5 RP/点
        assert refit.repair(state, "px-01") is True
        assert state.roster[0].hull == state.roster[0].max_hull
        hired = refit.hire(state)
        assert hired is not None and hired.id == "px-03"
        assert state.requisition_points == rp_after_m1 - repair_cost - HIRE_COST_RP
        assert [m.id for m in state.roster] == ["px-01", "px-02", "px-03"]

        # --- 任务 2 开局：pilot rank / 装甲状态 / 新机甲三项携带 ---
        battle2 = chain.start_mission(state)
        assert sorted(battle2.mechs) == ["ex-01", "ex-02", "px-01", "px-02", "px-03"]
        assert chain.current_def(state).mission_id == "m2_ridge_ambush"
        b2_px01 = battle2.get("px-01")
        assert b2_px01 is not None
        assert b2_px01.pilot is not None
        assert b2_px01.pilot.kills == 1  # rank 携带（kills 分量）
        assert b2_px01.pilot.gunnery_rank == carried_px01.pilot.gunnery_rank
        assert b2_px01.hull == b2_px01.max_hull  # 整备后的装甲状态携带
        b2_px03 = battle2.get("px-03")
        assert b2_px03 is not None and b2_px03.alive  # 新机甲在列

        # --- 任务 2 结算（敌 2 台，位置与任务 1 不同）---
        ex1 = battle2.get("ex-01")
        ex2 = battle2.get("ex-02")
        assert ex1 is not None and ex2 is not None
        assert ex1.position != battle1.get("ex-01").position  # 初始位置逐关变体
        _wipe_enemies(battle2)
        result2 = chain.complete_mission(state, battle2)
        assert result2.victory and result2.rp_reward == 30
        assert state.mission_index == 2

        # --- 任务 3 开局（敌 3 台）→ 结算 → 战役完结 ---
        battle3 = chain.start_mission(state)
        enemies3 = [m for m in battle3.mechs.values() if m.faction is Faction.ENEMY]
        assert len(enemies3) == 3
        assert any(m.pilot is not None for m in enemies3)  # 编制含 veteran 驾驶员变体
        _wipe_enemies(battle3)
        result3 = chain.complete_mission(state, battle3)
        assert result3.victory and result3.rp_reward == 40
        assert state.mission_index == 3
        assert chain.has_next(state) is False
        with pytest.raises(IndexError, match="complete"):
            chain.current_def(state)

    def test_mission_defs_are_progressive_variants(self) -> None:
        """三关编制/位置逐关递进（验收任务链变体要求）。"""
        assert len(MISSION_CHAIN) == 3
        counts = [len(m.enemies) for m in MISSION_CHAIN]
        assert counts == [1, 2, 3]
        tiles = [e.tile for m in MISSION_CHAIN for e in m.enemies]
        assert len(set(tiles)) == len(tiles)  # 全部出生位置互不相同
        rewards = [m.rp_reward for m in MISSION_CHAIN]
        assert rewards == sorted(rewards)  # 奖励递增


class TestDefeatPath:
    def test_defeat_keeps_mission_pointer_and_salvage_only(self) -> None:
        """敌存活 + 己方全灭 → 战败：无奖励、指针不推进、名册清空。"""
        state = start_campaign("it-m5-lose")
        chain = MissionChain()
        battle = chain.start_mission(state)
        assert chain.is_defeat(battle) is False
        _wipe_player(battle)
        assert chain.is_defeat(battle) is True
        assert chain.is_victory(battle) is False
        result = chain.complete_mission(state, battle)
        assert result.victory is False
        assert result.rp_reward == 0
        assert result.rp_salvage == 0  # 敌存活 → 无残骸可回收
        assert state.mission_index == 0  # 指针不推进（重试当前关）
        assert state.roster == []  # 己方全灭 → 名册空（重试需先 hire）
        assert state.requisition_points == 30

    def test_mutual_destruction_counts_as_victory(self) -> None:
        """同归于尽：敌全灭即任务目标达成（victory 优先），但无幸存者可携带。"""
        state = start_campaign("it-m5-mutual")
        chain = MissionChain()
        battle = chain.start_mission(state)
        enemy = battle.get("ex-01")
        assert enemy is not None
        enemy.apply_damage(MechLocation.CT, 27.0)  # 敌被摧毁（salvage 源）
        _wipe_player(battle)  # 己方全灭
        result = chain.complete_mission(state, battle)
        assert result.victory is True  # 目标达成
        assert result.rp_reward == 25
        assert result.rp_salvage > 0
        assert result.survivor_ids == []
        assert state.mission_index == 1  # 指针照常推进
        assert state.roster == []

    def test_partial_survival_on_defeat_still_carries_survivors(self) -> None:
        """战败但未全灭（脚本 defeat 等价路径）：幸存者照样回写携带。"""
        state = start_campaign("it-m5-partial")
        chain = MissionChain()
        battle = chain.start_mission(state)
        px01 = battle.get("px-01")
        px02 = battle.get("px-02")
        assert px01 is not None and px02 is not None
        px01.take_damage(10.0)
        px02.take_damage(px02.max_hull)
        result = chain.complete_mission(state, battle)  # 无胜利无全灭 → 战败结算
        assert result.victory is False
        assert result.survivor_ids == ["px-01"]
        assert [m.id for m in state.roster] == ["px-01"]
        assert state.mission_index == 0


class TestSnapshotIsolation:
    def test_battle_copy_is_isolated_from_roster(self) -> None:
        """参战副本与名册对象隔离：战斗中受伤不直接改名册（结算才回写）。"""
        state = start_campaign("it-m5-iso")
        chain = MissionChain()
        battle = chain.start_mission(state)
        combat_px01 = battle.get("px-01")
        assert combat_px01 is not None
        combat_px01.take_damage(50.0)
        assert state.roster[0].hull == state.roster[0].max_hull  # 名册未受战斗损伤影响
        assert combat_px01 is not state.roster[0]
