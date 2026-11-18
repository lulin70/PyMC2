"""MissionScript 单测（M6）：JSON 加载、错误路径、引擎 once/cooldown 门控与 tick_hook 驱动。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pymc2.domain.entities.mech import Faction, Mech
from pymc2.domain.scripting.mission_script import (
    MissionScript,
    MissionScriptError,
    ScriptContext,
    ScriptEngine,
    load_mission_script,
    mission_script_from_dict,
)
from pymc2.services.battle_service import BattleService

EXAMPLE_SCRIPT = Path(__file__).resolve().parents[2] / "data" / "missions" / "m1_river_crossing.json"

BASE_TRIGGER = {
    "id": "t1",
    "condition": {"kind": "tick_reached", "params": {"tick": 1}},
    "actions": [{"kind": "show_message", "params": {"text": "hi"}}],
}


def _battle_with_player() -> BattleService:
    service = BattleService()
    service.add_mech(Mech("px-01", Faction.PLAYER, 208.0, 336.0))
    return service


def load_mission_script_from_dict(data: dict) -> MissionScript:
    """测试辅助：走 dict 路径并保持 MissionScriptError 包装语义一致。"""
    try:
        return mission_script_from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise MissionScriptError(str(exc)) from exc


class TestJsonLoading:
    def test_load_example_script_from_disk(self) -> None:
        script = load_mission_script(EXAMPLE_SCRIPT)
        assert script.script_id == "m1_river_crossing"
        assert len(script.triggers) == 7

    def test_defaults_once_true_and_cooldown_zero(self) -> None:
        script = mission_script_from_dict({"script_id": "s", "triggers": [BASE_TRIGGER]})
        trigger = script.triggers[0]
        assert trigger.once is True
        assert trigger.cooldown_ticks == 0
        assert trigger.trigger_id == "t1"

    def test_explicit_once_false_and_cooldown(self) -> None:
        entry = {**BASE_TRIGGER, "once": False, "cooldown_ticks": 30}
        trigger = mission_script_from_dict({"script_id": "s", "triggers": [entry]}).triggers[0]
        assert trigger.once is False
        assert trigger.cooldown_ticks == 30

    def test_victory_and_defeat_trigger_views(self) -> None:
        script = mission_script_from_dict(
            {
                "script_id": "s",
                "triggers": [
                    BASE_TRIGGER,
                    {"id": "w", "condition": {"kind": "enemy_destroyed"}, "actions": [{"kind": "victory"}]},
                    {"id": "l", "condition": {"kind": "allies_destroyed"}, "actions": [{"kind": "defeat"}]},
                ],
            }
        )
        assert [t.trigger_id for t in script.victory_triggers] == ["w"]
        assert [t.trigger_id for t in script.defeat_triggers] == ["l"]

    def test_invalid_json_reports_line_and_column(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text('{\n  "script_id": "x",\n  "triggers": [}\n', encoding="utf-8")
        with pytest.raises(MissionScriptError, match=r"bad\.json.*line 3"):
            load_mission_script(path)

    def test_missing_file_wrapped(self, tmp_path: Path) -> None:
        with pytest.raises(MissionScriptError, match="cannot read"):
            load_mission_script(tmp_path / "nope.json")

    def test_missing_top_level_key(self) -> None:
        with pytest.raises(MissionScriptError, match="script_id"):
            load_mission_script_from_dict({"triggers": []})

    def test_unknown_condition_kind(self) -> None:
        bad = {
            "script_id": "s",
            "triggers": [{"id": "t", "condition": {"kind": "moon_phase"}, "actions": [{"kind": "victory"}]}],
        }
        with pytest.raises(MissionScriptError, match="unknown condition kind 'moon_phase'"):
            load_mission_script_from_dict(bad)

    def test_unknown_action_kind(self) -> None:
        bad = {
            "script_id": "s",
            "triggers": [
                {
                    "id": "t",
                    "condition": {"kind": "tick_reached", "params": {"tick": 1}},
                    "actions": [{"kind": "explode"}],
                }
            ],
        }
        with pytest.raises(MissionScriptError, match="unknown action kind 'explode'"):
            load_mission_script_from_dict(bad)

    def test_missing_required_condition_param(self) -> None:
        bad = {
            "script_id": "s",
            "triggers": [
                {"id": "t", "condition": {"kind": "unit_below_hull", "params": {"unit_id": "px-01"}}, "actions": []}
            ],
        }
        with pytest.raises(MissionScriptError, match="missing params \\['ratio'\\]"):
            load_mission_script_from_dict(bad)

    def test_missing_required_action_param(self) -> None:
        bad = {
            "script_id": "s",
            "triggers": [
                {
                    "id": "t",
                    "condition": {"kind": "tick_reached", "params": {"tick": 1}},
                    "actions": [{"kind": "grant_rp"}],
                }
            ],
        }
        with pytest.raises(MissionScriptError, match="missing params \\['amount'\\]"):
            load_mission_script_from_dict(bad)

    def test_triggers_not_a_list(self) -> None:
        with pytest.raises(MissionScriptError, match="must be a JSON array"):
            load_mission_script_from_dict({"script_id": "s", "triggers": "nope"})

    def test_top_level_not_object(self) -> None:
        with pytest.raises(MissionScriptError, match="top level"):
            load_mission_script_from_dict([1, 2])


# ------------------------------------------------------------------ 引擎门控


class TestScriptEngine:
    def test_once_trigger_fires_exactly_once(self) -> None:
        script = mission_script_from_dict({"script_id": "s", "triggers": [BASE_TRIGGER]})
        engine = ScriptEngine(script, ScriptContext(battle=_battle_with_player()))
        assert engine.tick(_battle_with_player()) == ["t1"]
        assert engine.tick(_battle_with_player()) == []
        assert engine.tick(_battle_with_player()) == []
        assert engine.fire_count("t1") == 1

    def test_cooldown_suppresses_refire_within_window(self) -> None:
        entry = {**BASE_TRIGGER, "once": False, "cooldown_ticks": 3}
        script = mission_script_from_dict({"script_id": "s", "triggers": [entry]})
        engine = ScriptEngine(script, ScriptContext(battle=_battle_with_player()))
        assert engine.tick(_battle_with_player()) == ["t1"]  # tick 1 触发
        assert engine.tick(_battle_with_player()) == []  # tick 2 冷却中
        assert engine.tick(_battle_with_player()) == []  # tick 3 冷却中
        assert engine.tick(_battle_with_player()) == ["t1"]  # tick 4 解除

    def test_triggered_actions_write_context(self) -> None:
        script = mission_script_from_dict(
            {
                "script_id": "s",
                "triggers": [
                    {
                        "id": "bonus",
                        "condition": {"kind": "tick_reached", "params": {"tick": 2}},
                        "actions": [
                            {"kind": "grant_rp", "params": {"amount": 10}},
                            {"kind": "show_message", "params": {"text": "bonus"}},
                        ],
                    }
                ],
            }
        )
        context = ScriptContext(battle=_battle_with_player())
        engine = ScriptEngine(script, context)
        assert engine.tick(_battle_with_player()) == []
        assert engine.tick(_battle_with_player()) == ["bonus"]
        assert context.granted_rp == 10
        assert context.messages == ["bonus"]

    def test_attach_drives_engine_from_battle_service_tick(self) -> None:
        """battle_service.tick 经 tick_hook 驱动触发器评估（M6 驱动点契约）。"""
        script = mission_script_from_dict({"script_id": "s", "triggers": [BASE_TRIGGER]})
        battle = _battle_with_player()
        context = ScriptContext(battle=battle)
        engine = ScriptEngine(script, context)
        engine.attach(battle)
        battle.tick(1 / 60)
        battle.tick(1 / 60)
        assert engine.tick_count == 2
        assert context.messages == ["hi"]  # tick 1 即触发（once 不重复）

    def test_outcome_persists_after_firing(self) -> None:
        script = mission_script_from_dict(
            {
                "script_id": "s",
                "triggers": [{"id": "w", "condition": {"kind": "enemy_destroyed"}, "actions": [{"kind": "victory"}]}],
            }
        )
        context = ScriptContext(battle=_battle_with_player())
        engine = ScriptEngine(script, context)
        assert engine.tick(_battle_with_player()) == []
        battle = context.battle
        battle.add_mech(Mech("ex-01", Faction.ENEMY, 0.0, 0.0))
        ex = battle.get("ex-01")
        assert ex is not None
        ex.take_damage(ex.max_hull)
        assert engine.tick(battle) == ["w"]
        assert context.outcome == "victory"
        assert engine.tick(battle) == []  # once 门控：不重复结算

    def test_json_roundtrip_example_script_matches_on_disk(self) -> None:
        """示例脚本可 JSON 化往返（dataclass 结构与磁盘文件一致）。"""
        script = load_mission_script(EXAMPLE_SCRIPT)
        payload = json.loads(
            json.dumps(
                {
                    "script_id": script.script_id,
                    "triggers": [
                        {
                            "id": t.trigger_id,
                            "condition": {"kind": t.condition.kind, "params": t.condition.params},
                            "actions": [{"kind": a.kind, "params": a.params} for a in t.actions],
                            "once": t.once,
                            "cooldown_ticks": t.cooldown_ticks,
                        }
                        for t in script.triggers
                    ],
                }
            )
        )
        assert mission_script_from_dict(payload) == script
