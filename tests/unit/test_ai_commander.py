"""AiCommander 单测：M4 goal 分解（优先级评估/后撤/最弱优先/接近/回退/决策循环）。

M2 存量适配（ROADMAP M4 语义变化）：无敌人时不再返回空指令，而是切换
SECURE_REGION 向地图中央移动（goal 分解替代简单攻击后的既定行为）。
"""

from __future__ import annotations

import math

from pymc2.domain.entities.mech import TILE_SIZE_PX, Faction, Mech, Weapon
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType, TacticalOrder
from pymc2.services.ai_commander import AiCommander, GoalType
from pymc2.services.battle_service import BattleService
from pymc2.services.order_dispatcher import OrderDispatcher


def make_battle() -> BattleService:
    """三机甲战局：ex-01(200) 距 px-01(100) 近、距 px-02(500) 远。"""
    battle = BattleService()
    battle.add_mech(Mech("px-01", Faction.PLAYER, 100.0, 100.0, weapon=Weapon("ppc", 25, 420.0, 2.0)))
    battle.add_mech(Mech("px-02", Faction.PLAYER, 500.0, 100.0, weapon=Weapon("ppc", 25, 420.0, 2.0)))
    battle.add_mech(Mech("ex-01", Faction.ENEMY, 200.0, 100.0, weapon=Weapon("mlaser", 15, 360.0, 2.5)))
    return battle


def tile_center(tile: tuple[int, int]) -> tuple[float, float]:
    return (tile[0] * TILE_SIZE_PX + TILE_SIZE_PX / 2, tile[1] * TILE_SIZE_PX + TILE_SIZE_PX / 2)


class TestPropose:
    def test_enemy_commander_targets_nearest_player(self) -> None:
        orders = AiCommander(Faction.ENEMY).propose(make_battle())
        assert len(orders) == 1
        order = orders[0]
        assert order.order_type is OrderType.ATTACK_OBJECT
        assert order.origin is OrderOrigin.COMMANDER
        assert order.unit_ids == ("ex-01",)
        assert order.target_unit_id == "px-01"
        assert order.target_tile is None

    def test_player_commander_orders_all_player_mechs(self) -> None:
        orders = AiCommander(Faction.PLAYER).propose(make_battle())
        assert [order.unit_ids for order in orders] == [("px-01",), ("px-02",)]
        assert all(order.target_unit_id == "ex-01" for order in orders)
        assert all(order.origin is OrderOrigin.COMMANDER for order in orders)

    def test_dead_mech_gets_no_order(self) -> None:
        battle = make_battle()
        dead = battle.get("ex-01")
        assert dead is not None
        dead.take_damage(999)
        assert AiCommander(Faction.ENEMY).propose(battle) == []

    def test_no_targets_switches_to_secure_region(self) -> None:
        """M4 适配：敌全灭 → goal 切换 SECURE_REGION，向地图中央 tile (20,20) 移动。"""
        battle = BattleService()
        battle.add_mech(Mech("ex-01", Faction.ENEMY, 0.0, 0.0))
        commander = AiCommander(Faction.ENEMY)
        orders = commander.propose(battle)
        assert len(orders) == 1
        assert orders[0].order_type is OrderType.MOVE_TO_POINT
        assert orders[0].target_tile == (20, 20)
        assert commander.current_goal is not None
        assert commander.current_goal.goal_type is GoalType.SECURE_REGION

    def test_propose_is_deterministic(self) -> None:
        first = AiCommander(Faction.PLAYER).propose(make_battle())
        second = AiCommander(Faction.PLAYER).propose(make_battle())
        assert first == second

    def test_default_faction_is_enemy(self) -> None:
        assert AiCommander().faction is Faction.ENEMY


class TestGoalPriority:
    def test_priority_table_order(self) -> None:
        table = AiCommander(Faction.ENEMY)._goal_table()
        assert [goal.goal_type for goal in table] == [
            GoalType.PRESERVE_FORCE,
            GoalType.DESTROY_OBJECT,
            GoalType.SECURE_REGION,
        ]
        assert [goal.priority for goal in table] == [0, 1, 2]
        assert all(callable(goal.is_complete) for goal in table)

    def test_current_goal_is_destroy_object_when_enemies_present(self) -> None:
        commander = AiCommander(Faction.ENEMY)
        commander.propose(make_battle())
        assert commander.current_goal is not None
        assert commander.current_goal.goal_type is GoalType.DESTROY_OBJECT

    def test_current_goal_is_preserve_force_when_own_mech_damaged(self) -> None:
        battle = make_battle()
        damaged = battle.get("ex-01")
        assert damaged is not None
        damaged.take_damage(80)  # hull 20% < 30%
        commander = AiCommander(Faction.ENEMY)
        commander.propose(battle)
        assert commander.current_goal is not None
        assert commander.current_goal.goal_type is GoalType.PRESERVE_FORCE

    def test_current_goal_is_none_when_all_complete(self) -> None:
        battle = BattleService()
        battle.add_mech(Mech("ex-01", Faction.ENEMY, *tile_center((20, 20))))
        commander = AiCommander(Faction.ENEMY)
        assert commander.propose(battle)  # 走回退路径（默认 GUARD）
        assert commander.current_goal is None


class TestPreserveForce:
    def test_damaged_mech_retreats_away_from_nearest_enemy(self) -> None:
        battle = BattleService()
        battle.add_mech(Mech("ex-01", Faction.ENEMY, 592.0, 336.0, weapon=Weapon("mlaser", 15, 360.0, 2.5)))
        battle.add_mech(Mech("px-01", Faction.PLAYER, 208.0, 336.0))
        damaged = battle.get("ex-01")
        enemy = battle.get("px-01")
        assert damaged is not None and enemy is not None
        damaged.take_damage(80)  # hull 20% < 30%
        orders = AiCommander(Faction.ENEMY).propose(battle)
        assert len(orders) == 1
        order = orders[0]
        assert order.order_type is OrderType.MOVE_TO_POINT
        assert order.target_tile is not None
        tx = order.target_tile[0] * TILE_SIZE_PX + TILE_SIZE_PX / 2
        ty = order.target_tile[1] * TILE_SIZE_PX + TILE_SIZE_PX / 2
        # 后撤目标在机甲东侧（远离西侧敌人），且比当前位置离敌更远
        assert tx > damaged.x
        assert math.hypot(tx - enemy.x, ty - enemy.y) > math.hypot(damaged.x - enemy.x, damaged.y - enemy.y)

    def test_healthy_mech_keeps_attacking_while_squadmate_retreats(self) -> None:
        battle = BattleService()
        battle.add_mech(Mech("ex-01", Faction.ENEMY, 592.0, 336.0, weapon=Weapon("mlaser", 15, 360.0, 2.5)))
        battle.add_mech(Mech("ex-02", Faction.ENEMY, 400.0, 336.0, weapon=Weapon("mlaser", 15, 360.0, 2.5)))
        battle.add_mech(Mech("px-01", Faction.PLAYER, 208.0, 336.0))
        damaged = battle.get("ex-01")
        assert damaged is not None
        damaged.take_damage(80)
        orders = AiCommander(Faction.ENEMY).propose(battle)
        by_unit = {order.unit_ids[0]: order for order in orders}
        assert by_unit["ex-01"].order_type is OrderType.MOVE_TO_POINT  # 残机后撤
        assert by_unit["ex-02"].order_type is OrderType.ATTACK_OBJECT  # 健机继续进攻
        assert by_unit["ex-02"].target_unit_id == "px-01"

    def test_retreat_threshold_injected(self) -> None:
        battle = BattleService()
        battle.add_mech(Mech("ex-01", Faction.ENEMY, 400.0, 336.0, weapon=Weapon("mlaser", 15, 360.0, 2.5)))
        battle.add_mech(Mech("px-01", Faction.PLAYER, 208.0, 336.0))
        mech = battle.get("ex-01")
        assert mech is not None
        mech.take_damage(60)  # hull 40%：默认阈值不触发
        orders = AiCommander(Faction.ENEMY).propose(battle)
        lenient = AiCommander(Faction.ENEMY, preserve_hull_ratio=0.5).propose(battle)
        assert orders[0].order_type is OrderType.ATTACK_OBJECT
        assert lenient[0].order_type is OrderType.MOVE_TO_POINT


class TestWeakestFirst:
    def test_damaged_enemy_targeted_over_nearer_full_hull(self) -> None:
        battle = BattleService()
        battle.add_mech(Mech("ex-01", Faction.ENEMY, 400.0, 100.0, weapon=Weapon("mlaser", 15, 360.0, 2.5)))
        battle.add_mech(Mech("px-01", Faction.PLAYER, 500.0, 100.0))  # 近且满血
        weak = Mech("px-02", Faction.PLAYER, 700.0, 100.0)  # 远但残血
        battle.add_mech(weak)
        weak.take_damage(70)  # hull 30
        orders = AiCommander(Faction.ENEMY).propose(battle)
        assert len(orders) == 1
        assert orders[0].order_type is OrderType.ATTACK_OBJECT
        assert orders[0].target_unit_id == "px-02"  # 最弱优先（预期击杀最大化）


class TestApproach:
    def test_out_of_range_mech_moves_toward_weakest_target(self) -> None:
        battle = BattleService()
        battle.add_mech(Mech("ex-01", Faction.ENEMY, 100.0, 100.0, weapon=Weapon("mlaser", 15, 360.0, 2.5)))
        battle.add_mech(Mech("px-01", Faction.PLAYER, 1000.0, 100.0))
        orders = AiCommander(Faction.ENEMY).propose(battle)
        assert len(orders) == 1
        order = orders[0]
        assert order.order_type is OrderType.MOVE_TO_POINT
        assert order.target_tile is not None
        cx = order.target_tile[0] * TILE_SIZE_PX + TILE_SIZE_PX / 2
        cy = order.target_tile[1] * TILE_SIZE_PX + TILE_SIZE_PX / 2
        enemy = battle.get("px-01")
        assert enemy is not None
        assert math.hypot(cx - enemy.x, cy - enemy.y) < 360.0  # 接近点进入射程内
        assert math.hypot(cx - 100.0, cy - 100.0) < 900.0  # 比出发点更近目标


class TestFallback:
    def test_all_goals_complete_falls_back_to_guard(self) -> None:
        battle = BattleService()
        battle.add_mech(Mech("ex-01", Faction.ENEMY, *tile_center((20, 20))))
        orders = AiCommander(Faction.ENEMY).propose(battle)
        assert len(orders) == 1
        assert orders[0].order_type is OrderType.GUARD
        assert orders[0].origin is OrderOrigin.COMMANDER

    def test_injected_fallback_replaces_default(self) -> None:
        def stop_fallback(mech: Mech, battle: BattleService) -> TacticalOrder:
            return TacticalOrder(OrderType.STOP, (mech.id,), OrderOrigin.COMMANDER, None, None)

        battle = BattleService()
        battle.add_mech(Mech("ex-01", Faction.ENEMY, *tile_center((20, 20))))
        orders = AiCommander(Faction.ENEMY, fallback=stop_fallback).propose(battle)
        assert len(orders) == 1
        assert orders[0].order_type is OrderType.STOP

    def test_fallback_factory_may_return_none(self) -> None:
        battle = BattleService()
        battle.add_mech(Mech("ex-01", Faction.ENEMY, *tile_center((20, 20))))
        commander = AiCommander(Faction.ENEMY, fallback=lambda mech, battle: None)
        assert commander.propose(battle) == []


class TestDecisionLoop:
    def test_update_gate_n30_fires_only_on_interval(self) -> None:
        battle = make_battle()
        dispatcher = OrderDispatcher()
        commander = AiCommander(Faction.ENEMY, decision_interval_ticks=30)
        decided = [commander.update(battle, dispatcher) for _ in range(30)]
        assert decided[:29] == [False] * 29
        assert decided[-1] is True
        assert len(dispatcher.order_log) == 1  # 一台机甲一条指令

    def test_update_gate_n1_fires_every_frame(self) -> None:
        battle = make_battle()
        dispatcher = OrderDispatcher()
        commander = AiCommander(Faction.ENEMY, decision_interval_ticks=1)
        decided = [commander.update(battle, dispatcher) for _ in range(5)]
        assert decided == [True] * 5
        assert len(dispatcher.order_log) == 5  # 每帧重发同一攻击决策

    def test_update_orders_carry_commander_origin(self) -> None:
        battle = make_battle()
        dispatcher = OrderDispatcher()
        commander = AiCommander(Faction.ENEMY, decision_interval_ticks=1)
        for _ in range(3):
            commander.update(battle, dispatcher)
        assert all(order.origin is OrderOrigin.COMMANDER for order in dispatcher.order_log)
        assert all(order.order_type is OrderType.ATTACK_OBJECT for order in dispatcher.order_log)
