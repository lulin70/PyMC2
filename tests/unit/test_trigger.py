"""Trigger DSL 单测（M6）：五种条件逐种求值 + 五种动作逐种执行（含错误路径）。"""

from __future__ import annotations

import pytest

from pymc2.domain.entities.mech import Faction, Mech
from pymc2.domain.entities.mech_chassis import MechLocation
from pymc2.domain.entities.pilot import Pilot
from pymc2.domain.entities.turret import Turret
from pymc2.domain.entities.vehicle import Vehicle
from pymc2.domain.scripting.mission_script import ScriptContext
from pymc2.domain.scripting.trigger import (
    TriggerAction,
    TriggerCondition,
    apply_action,
    destroyed_count,
    evaluate_condition,
)
from pymc2.services.battle_service import BattleService
from pymc2.services.campaign_state import CampaignState


def _battle(*mechs: Mech) -> BattleService:
    service = BattleService()
    for mech in mechs:
        service.add_mech(mech)
    return service


def _enemy(unit_id: str = "ex-01", tile_px: float = 500.0) -> Mech:
    return Mech(unit_id, Faction.ENEMY, tile_px, 320.0)


def _player(unit_id: str = "px-01", tile_px: float = 200.0) -> Mech:
    return Mech(unit_id, Faction.PLAYER, tile_px, 320.0)


# ------------------------------------------------------------------ 条件


class TestTickReached:
    def test_not_reached_then_reached(self) -> None:
        battle = _battle()
        cond = TriggerCondition("tick_reached", {"tick": 10})
        assert evaluate_condition(cond, battle, 9) is False
        assert evaluate_condition(cond, battle, 10) is True
        assert evaluate_condition(cond, battle, 11) is True


class TestEnemyDestroyed:
    def test_count_threshold(self) -> None:
        e1, e2 = _enemy("ex-01"), _enemy("ex-02")
        e1.take_damage(e1.max_hull)
        battle = _battle(e1, e2)
        cond = TriggerCondition("enemy_destroyed", {"count": 1})
        assert evaluate_condition(cond, battle, 1) is True
        cond_two = TriggerCondition("enemy_destroyed", {"count": 2})
        assert evaluate_condition(cond_two, battle, 1) is False

    def test_default_is_total_annihilation(self) -> None:
        e1, e2 = _enemy("ex-01"), _enemy("ex-02")
        battle = _battle(e1, e2)
        cond = TriggerCondition("enemy_destroyed")
        assert evaluate_condition(cond, battle, 1) is False
        e2.take_damage(e2.max_hull)
        e1.take_damage(e1.max_hull)
        assert evaluate_condition(cond, battle, 1) is True

    def test_reinforcement_delayed_annihilation(self) -> None:
        """增援入场后「全灭」需含增援（计数随 mechs 动态增长）。"""
        e1 = _enemy()
        battle = _battle(e1)
        cond = TriggerCondition("enemy_destroyed")
        e1.take_damage(e1.max_hull)
        assert evaluate_condition(cond, battle, 1) is True
        battle.add_mech(_enemy("ex-r1"))
        assert evaluate_condition(cond, battle, 2) is False


class TestAlliesDestroyed:
    def test_default_and_count(self) -> None:
        p1, p2 = _player("px-01"), _player("px-02")
        battle = _battle(p1, p2)
        cond = TriggerCondition("allies_destroyed")
        assert evaluate_condition(cond, battle, 1) is False
        p2.take_damage(p2.max_hull)
        assert evaluate_condition(TriggerCondition("allies_destroyed", {"count": 1}), battle, 1) is True
        assert evaluate_condition(cond, battle, 1) is False
        p1.take_damage(p1.max_hull)
        assert evaluate_condition(cond, battle, 1) is True


class TestUnitBelowHull:
    def test_ratio_threshold(self) -> None:
        mech = _player()
        battle = _battle(mech)
        cond = TriggerCondition("unit_below_hull", {"unit_id": "px-01", "ratio": 0.3})
        assert evaluate_condition(cond, battle, 1) is False
        mech.take_damage(mech.max_hull * 0.8)
        assert evaluate_condition(cond, battle, 1) is True

    def test_destroyed_unit_triggers(self) -> None:
        mech = _player()
        mech.take_damage(mech.max_hull)
        battle = _battle(mech)
        cond = TriggerCondition("unit_below_hull", {"unit_id": "px-01", "ratio": 0.3})
        assert evaluate_condition(cond, battle, 1) is True

    def test_unknown_unit_is_false(self) -> None:
        battle = _battle()
        cond = TriggerCondition("unit_below_hull", {"unit_id": "ghost", "ratio": 0.3})
        assert evaluate_condition(cond, battle, 1) is False


class TestUnitInRegion:
    def test_inside_outside_rect(self) -> None:
        """rect 为 tile 坐标闭区间：tile (6,10) 在 [4,8,10,12] 内、不在 [8,8,10,12] 内。"""
        mech = Mech("px-01", Faction.PLAYER, 6 * 32 + 16, 10 * 32 + 16)
        battle = _battle(mech)
        inside = TriggerCondition("unit_in_region", {"unit_id": "px-01", "rect": [4, 8, 10, 12]})
        outside = TriggerCondition("unit_in_region", {"unit_id": "px-01", "rect": [8, 8, 10, 12]})
        assert evaluate_condition(inside, battle, 1) is True
        assert evaluate_condition(outside, battle, 1) is False

    def test_boundary_tile_counts_as_inside(self) -> None:
        mech = Mech("px-01", Faction.PLAYER, 10 * 32 + 16, 12 * 32 + 16)
        battle = _battle(mech)
        cond = TriggerCondition("unit_in_region", {"unit_id": "px-01", "rect": [8, 8, 10, 12]})
        assert evaluate_condition(cond, battle, 1) is True

    def test_unknown_unit_is_false(self) -> None:
        battle = _battle()
        cond = TriggerCondition("unit_in_region", {"unit_id": "ghost", "rect": [0, 0, 5, 5]})
        assert evaluate_condition(cond, battle, 1) is False


class TestDestroyedCount:
    def test_destroyed_count_helper(self) -> None:
        e1, p1 = _enemy(), _player()
        e1.take_damage(e1.max_hull)
        battle = _battle(e1, p1)
        assert destroyed_count(battle, Faction.ENEMY) == 1
        assert destroyed_count(battle, Faction.PLAYER) == 0


def test_unknown_condition_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown condition kind"):
        evaluate_condition(TriggerCondition("teleport"), _battle(), 1)


# ------------------------------------------------------------------ 动作


class TestSpawnReinforcement:
    def test_spawn_adds_mechs_and_records_ids(self) -> None:
        battle = _battle(_player())
        context = ScriptContext(battle=battle)
        action = TriggerAction(
            "spawn_reinforcement",
            {
                "faction": "enemy",
                "mechs": [{"id": "ex-r1", "tile": [24, 10]}, {"id": "ex-r2", "tile": [24, 12], "damage": 20}],
            },
        )
        apply_action(action, context)
        assert context.spawned_unit_ids == ["ex-r1", "ex-r2"]
        r1 = battle.get("ex-r1")
        assert r1 is not None
        assert r1.faction is Faction.ENEMY
        assert r1.tile == (24, 10)
        assert r1.alive is True
        r2 = battle.get("ex-r2")
        assert r2 is not None and r2.weapon.damage == 20

    def test_spawn_player_faction(self) -> None:
        battle = _battle()
        context = ScriptContext(battle=battle)
        apply_action(
            TriggerAction("spawn_reinforcement", {"faction": "player", "mechs": [{"id": "px-r1", "tile": [2, 4]}]}),
            context,
        )
        spawned = battle.get("px-r1")
        assert spawned is not None and spawned.faction is Faction.PLAYER

    def test_duplicate_id_raises(self) -> None:
        existing = _enemy("ex-01")
        battle = _battle(existing)
        context = ScriptContext(battle=battle)
        action = TriggerAction("spawn_reinforcement", {"faction": "enemy", "mechs": [{"id": "ex-01", "tile": [0, 0]}]})
        with pytest.raises(ValueError, match="already exists"):
            apply_action(action, context)


class TestOutcomeActions:
    def test_victory_sets_outcome(self) -> None:
        context = ScriptContext(battle=_battle())
        apply_action(TriggerAction("victory"), context)
        assert context.outcome == "victory"

    def test_defeat_sets_outcome(self) -> None:
        context = ScriptContext(battle=_battle())
        apply_action(TriggerAction("defeat"), context)
        assert context.outcome == "defeat"


class TestGrantRp:
    def test_grant_records_and_deposits_into_campaign(self) -> None:
        campaign = CampaignState("t", requisition_points=10)
        context = ScriptContext(battle=_battle(), campaign=campaign)
        apply_action(TriggerAction("grant_rp", {"amount": 15}), context)
        assert context.granted_rp == 15
        assert campaign.requisition_points == 25

    def test_grant_without_campaign_still_records(self) -> None:
        context = ScriptContext(battle=_battle())
        apply_action(TriggerAction("grant_rp", {"amount": 7}), context)
        assert context.granted_rp == 7


class TestShowMessage:
    def test_message_appended_in_order(self) -> None:
        context = ScriptContext(battle=_battle())
        apply_action(TriggerAction("show_message", {"text": "first"}), context)
        apply_action(TriggerAction("show_message", {"text": "second"}), context)
        assert context.messages == ["first", "second"]


def test_unknown_action_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown action kind"):
        apply_action(TriggerAction("explode"), ScriptContext(battle=_battle()))


def test_wounded_pilot_gunnery_rank_unaffected_by_condition_eval() -> None:
    """条件求值为只读观察：不推进技能/伤害状态（guard 断言）。"""
    mech = _player()
    mech.pilot = Pilot("g")
    mech.apply_damage(MechLocation.CT, 1.0)
    battle = _battle(mech)
    before = (mech.hull, mech.pilot.gunnery_rank)
    for kind, params in (
        ("tick_reached", {"tick": 1}),
        ("enemy_destroyed", {}),
        ("unit_below_hull", {"unit_id": "px-01", "ratio": 0.9}),
        ("unit_in_region", {"unit_id": "px-01", "rect": [0, 0, 39, 39]}),
    ):
        evaluate_condition(TriggerCondition(kind, params), battle, 1)
    assert (mech.hull, mech.pilot.gunnery_rank) == before


class TestSpawnUnitKinds:
    """P2 单位谱系：spawn_reinforcement 条目 kind 三态分派（缺省 mech 容错）。"""

    def test_vehicle_kind_spawns_vehicle(self) -> None:
        battle = _battle()
        context = ScriptContext(battle=battle)
        apply_action(
            TriggerAction(
                "spawn_reinforcement",
                {
                    "faction": "enemy",
                    "mechs": [{"id": "ev-01", "kind": "vehicle", "tile": [26, 12], "hull": 80, "damage": 12}],
                },
            ),
            context,
        )
        unit = battle.get("ev-01")
        assert isinstance(unit, Vehicle)
        assert unit.max_hull == 80 and unit.weapon.damage == 12
        assert unit.weapon.heat_per_shot == 0.0  # 无热量反应堆
        assert unit.mobile is True and unit.alive is True

    def test_turret_kind_spawns_immobile_turret(self) -> None:
        battle = _battle()
        context = ScriptContext(battle=battle)
        apply_action(
            TriggerAction(
                "spawn_reinforcement",
                {
                    "faction": "enemy",
                    "mechs": [{"id": "et-01", "kind": "turret", "tile": [28, 8], "hull": 60, "range_px": 420.0}],
                },
            ),
            context,
        )
        unit = battle.get("et-01")
        assert isinstance(unit, Turret)
        assert unit.max_hull == 60
        assert unit.weapon.range_px == 420.0
        assert unit.mobile is False

    def test_missing_kind_defaults_to_mech(self) -> None:
        """缺省容错：无 kind 条目与存量行为一致（Mech）。"""
        battle = _battle()
        context = ScriptContext(battle=battle)
        apply_action(
            TriggerAction(
                "spawn_reinforcement",
                {"faction": "enemy", "mechs": [{"id": "ex-r1", "tile": [24, 10], "pilot_name": "vet"}]},
            ),
            context,
        )
        unit = battle.get("ex-r1")
        assert isinstance(unit, Mech)
        assert unit.pilot is not None and unit.pilot.name == "vet"

    def test_unknown_kind_raises(self) -> None:
        context = ScriptContext(battle=_battle())
        with pytest.raises(ValueError, match="unknown unit kind"):
            apply_action(
                TriggerAction(
                    "spawn_reinforcement",
                    {"faction": "enemy", "mechs": [{"id": "e-inf", "kind": "infantry", "tile": [10, 10]}]},
                ),
                context,
            )
