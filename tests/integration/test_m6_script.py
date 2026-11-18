"""M6 集成：示例脚本全事件触发覆盖（5 动作逐个被执行）+ PRESERVE_FORCE 与脚本 defeat 共存。"""

from __future__ import annotations

import random
from pathlib import Path

from pymc2.domain.entities.mech import Faction, Mech, Weapon
from pymc2.domain.scripting.mission_script import (
    ScriptContext,
    ScriptEngine,
    load_mission_script,
)
from pymc2.services.ai_commander import AiCommander, GoalType
from pymc2.services.battle_service import BattleService
from pymc2.services.campaign_state import CampaignState
from pymc2.services.order_dispatcher import OrderDispatcher

EXAMPLE_SCRIPT = Path(__file__).resolve().parents[2] / "data" / "missions" / "m1_river_crossing.json"


def _script_battle() -> tuple[BattleService, ScriptContext, ScriptEngine]:
    """示例脚本 + 基础战局（玩家双机远程炮 vs 敌 ex-01，与脚本叙事一致）。"""
    script = load_mission_script(EXAMPLE_SCRIPT)
    battle = BattleService(rng=random.Random(7))
    battle.add_mech(Mech("px-01", Faction.PLAYER, 208.0, 336.0, weapon=Weapon("ppc", 25, 420.0, 2.0)))
    battle.add_mech(Mech("px-02", Faction.PLAYER, 272.0, 336.0, weapon=Weapon("ppc", 25, 420.0, 2.0)))
    battle.add_mech(Mech("ex-01", Faction.ENEMY, 592.0, 336.0))
    context = ScriptContext(battle=battle, campaign=CampaignState("it-m6", requisition_points=0))
    engine = ScriptEngine(script, context)
    engine.attach(battle)
    return battle, context, engine


def _kill(mech_id: str, battle: BattleService) -> None:
    mech = battle.get(mech_id)
    assert mech is not None
    mech.take_damage(mech.max_hull)


class TestExampleScriptEventCoverage:
    def test_show_message_via_tick_reached(self) -> None:
        """动作 show_message ← 条件 tick_reached（开场简报）。"""
        battle, context, engine = _script_battle()
        fired = engine.tick(battle)  # tick 1
        assert fired == ["intro_briefing"]
        assert context.messages == ["任务开始：渡河突破口——清除桥头敌军。"]

    def test_spawn_reinforcement_via_tick_reached(self) -> None:
        """动作 spawn_reinforcement ← 条件 tick_reached（增援 6 秒空投）。"""
        battle, context, engine = _script_battle()
        for _ in range(360):
            engine.tick(battle)
        assert "ex-r1" in context.spawned_unit_ids
        spawned = battle.get("ex-r1")
        assert spawned is not None
        assert spawned.faction is Faction.ENEMY
        assert spawned.alive is True
        assert "敌军增援从东侧抵达！" in context.messages

    def test_grant_rp_via_enemy_destroyed_count(self) -> None:
        """动作 grant_rp ← 条件 enemy_destroyed{count:1}（首杀奖励入账战役）。"""
        battle, context, engine = _script_battle()
        _kill("ex-01", battle)
        fired = engine.tick(battle)
        assert "enemy_patrol_down" in fired
        assert context.granted_rp == 15
        assert context.campaign is not None
        assert context.campaign.requisition_points == 15

    def test_victory_via_enemy_destroyed_all(self) -> None:
        """动作 victory ← 条件 enemy_destroyed（全灭，含增援）。"""
        battle, context, engine = _script_battle()
        for _ in range(360):
            engine.tick(battle)  # 增援入场
        _kill("ex-01", battle)
        _kill("ex-r1", battle)
        fired = engine.tick(battle)
        assert "mission_victory" in fired
        assert context.outcome == "victory"

    def test_defeat_via_allies_destroyed(self) -> None:
        """动作 defeat ← 条件 allies_destroyed（己方全灭）。"""
        battle, context, engine = _script_battle()
        _kill("px-01", battle)
        _kill("px-02", battle)
        fired = engine.tick(battle)
        assert "mission_defeat" in fired
        assert context.outcome == "defeat"

    def test_unit_below_hull_warning_message(self) -> None:
        """条件 unit_below_hull → 警告消息（残血触发）。"""
        battle, context, engine = _script_battle()
        px02 = battle.get("px-02")
        assert px02 is not None
        px02.take_damage(px02.max_hull * 0.7)  # hull_ratio 0.3 < 0.4
        engine.tick(battle)
        assert "警告：px-02 完好率不足 40%。" in context.messages

    def test_unit_in_region_crossing_message(self) -> None:
        """条件 unit_in_region → 渡口消息（推进触发）。"""
        battle, context, engine = _script_battle()
        px01 = battle.get("px-01")
        assert px01 is not None
        px01.x, px01.y = 15 * 32 + 16, 10 * 32 + 16  # tile (15,10) ∈ rect [14,8,18,12]
        engine.tick(battle)
        assert "已抵达渡口，敌军进入射程。" in context.messages

    def test_full_battle_tick_drives_all_script_events(self) -> None:
        """battle_service.tick 经 tick_hook 驱动：真战斗循环中开场/渡口/增援/首杀/胜利全触发。

        时序脚本：先机动到渡口并按兵不动，等增援（tick 360）入场后才开始交锋——
        「全灭」必然含增援，事件序列与 rng 无关（时长由固定 seed 确定）。
        """
        battle, context, _engine = _script_battle()
        px01 = battle.get("px-01")
        px02 = battle.get("px-02")
        assert px01 is not None and px02 is not None
        px01.move_to(15 * 32 + 16, 10 * 32 + 16)  # 推进至渡口
        px02.move_to(15 * 32 + 16, 11 * 32 + 16)
        for _frame in range(6000):
            battle.tick(1 / 60)  # 引擎由 tick_hook 驱动（移动/增援/开场消息全在此发生）
            if context.spawned_unit_ids:  # 增援入场后才进入射程交锋
                for target_id in ("ex-01", "ex-r1"):
                    target = battle.get(target_id)
                    if target is not None and target.alive:
                        battle.fire_at("px-01", target_id)
                        battle.fire_at("px-02", target_id)
            if context.outcome is not None:
                break
        assert "任务开始：渡河突破口——清除桥头敌军。" in context.messages
        assert "已抵达渡口，敌军进入射程。" in context.messages
        assert context.spawned_unit_ids == ["ex-r1"]
        assert "敌军增援从东侧抵达！" in context.messages
        assert context.outcome == "victory"
        assert context.granted_rp >= 15  # 首杀奖励已入账
        assert not any(m.alive for m in battle.mechs.values() if m.faction is Faction.ENEMY)


class TestPreserveForceScriptCoexistence:
    def test_preserve_force_orders_do_not_block_script_victory(self) -> None:
        """M4 PRESERVE_FORCE（指挥官后撤）与 M6 脚本 victory 并行不冲突。"""
        script = load_mission_script(EXAMPLE_SCRIPT)
        battle = BattleService()
        battle.add_mech(Mech("px-01", Faction.PLAYER, 208.0, 336.0))
        battle.add_mech(Mech("ex-01", Faction.ENEMY, 592.0, 336.0))
        context = ScriptContext(battle=battle)
        engine = ScriptEngine(script, context)
        engine.attach(battle)
        commander = AiCommander(Faction.PLAYER)  # 玩家侧指挥官（残血 → 后撤 goal）
        dispatcher = OrderDispatcher()
        px01 = battle.get("px-01")
        assert px01 is not None
        px01.take_damage(px01.max_hull * 0.85)  # hull 15% < 30% → PRESERVE_FORCE
        for _ in range(40):  # 跨指挥官决策边界
            commander.update(battle, dispatcher)
            battle.tick(1 / 60)
        assert commander.current_goal is not None
        assert commander.current_goal.goal_type is GoalType.PRESERVE_FORCE
        assert px01.move_target is not None  # 指挥官后撤指令照常下发
        # 脚本侧独立求值：清场 → victory（不被指挥官 goal 干扰）
        ex01 = battle.get("ex-01")
        assert ex01 is not None
        ex01.take_damage(ex01.max_hull)
        engine.tick(battle)
        assert context.outcome == "victory"

    def test_script_defeat_does_not_block_preserve_force(self) -> None:
        """脚本 defeat（己方全灭触发）与指挥官 goal 求值共存：goal 链不因 outcome 崩溃。"""
        script = load_mission_script(EXAMPLE_SCRIPT)
        battle = BattleService()
        battle.add_mech(Mech("px-01", Faction.PLAYER, 208.0, 336.0))
        battle.add_mech(Mech("ex-01", Faction.ENEMY, 592.0, 336.0))
        context = ScriptContext(battle=battle)
        engine = ScriptEngine(script, context)
        commander = AiCommander(Faction.PLAYER)
        dispatcher = OrderDispatcher()
        px01 = battle.get("px-01")
        ex01 = battle.get("ex-01")
        assert px01 is not None and ex01 is not None
        ex01.take_damage(ex01.max_hull)  # 敌全灭 → commander 转 SECURE_REGION
        for _ in range(40):  # 跨指挥官决策边界（决策间隔 30 帧）
            commander.update(battle, dispatcher)
            battle.tick(1 / 60)
        assert commander.current_goal is not None
        assert commander.current_goal.goal_type is GoalType.SECURE_REGION
        px01.take_damage(px01.max_hull)  # 己方全灭 → 脚本 defeat
        engine.tick(battle)
        assert context.outcome == "defeat"
        # 指挥官在全灭战局下照常求值（无存活单位 → goal 链空转不崩溃）
        commander.update(battle, dispatcher)
        assert commander.propose(battle) == []
