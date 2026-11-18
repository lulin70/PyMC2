"""OrderDispatcher 单测：7 类指令路由、HOLD 拒绝开火、指令日志（含 origin）与环形队列。"""

from __future__ import annotations

from pymc2.domain.entities.mech import FireMode
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType, TacticalOrder
from pymc2.services.battle_service import BattleService, create_m2_battle
from pymc2.services.order_dispatcher import ORDER_LOG_MAXLEN, OrderDispatcher


class StaticRng:
    """固定随机源：random() 恒返回 value（0.0 必命中）。"""

    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


TILE_12_10_CENTER = (12 * 32 + 16, 10 * 32 + 16)  # (400.0, 336.0)


def make_battle() -> BattleService:
    return create_m2_battle(rng=StaticRng(0.0))


def make_order(
    order_type: OrderType,
    unit_ids: tuple[str, ...] = ("px-01",),
    *,
    origin: OrderOrigin = OrderOrigin.PLAYER,
    tile: tuple[int, int] | None = None,
    target: str | None = None,
) -> TacticalOrder:
    return TacticalOrder(order_type, unit_ids, origin, tile, target)


class TestMoveRouting:
    def test_move_order_routes_to_move_mech(self) -> None:
        battle = make_battle()
        dispatcher = OrderDispatcher()
        assert dispatcher.dispatch(make_order(OrderType.MOVE_TO_POINT, tile=(12, 10)), battle) is True
        mech = battle.get("px-01")
        assert mech is not None
        assert mech.move_target == TILE_12_10_CENTER

    def test_move_order_moves_all_selected_units(self) -> None:
        battle = make_battle()
        dispatcher = OrderDispatcher()
        assert (
            dispatcher.dispatch(make_order(OrderType.MOVE_TO_POINT, ("px-01", "px-02"), tile=(12, 10)), battle) is True
        )
        assert battle.get("px-01").move_target == TILE_12_10_CENTER  # type: ignore[union-attr]
        assert battle.get("px-02").move_target == TILE_12_10_CENTER  # type: ignore[union-attr]

    def test_move_unknown_unit_fails(self) -> None:
        battle = make_battle()
        dispatcher = OrderDispatcher()
        assert dispatcher.dispatch(make_order(OrderType.MOVE_TO_POINT, ("nope",), tile=(12, 10)), battle) is False


class TestAttackRouting:
    def test_attack_order_routes_to_fire_at(self) -> None:
        battle = make_battle()
        dispatcher = OrderDispatcher()
        assert dispatcher.dispatch(make_order(OrderType.ATTACK_OBJECT, target="ex-01"), battle) is True
        assert len(battle.projectiles) == 1
        assert battle.get("px-01").attack_target_id == "ex-01"  # type: ignore[union-attr]

    def test_attack_unknown_target_fails(self) -> None:
        battle = make_battle()
        dispatcher = OrderDispatcher()
        assert dispatcher.dispatch(make_order(OrderType.ATTACK_OBJECT, target="nope"), battle) is False
        assert not battle.projectiles


class TestFireModeRouting:
    def test_hold_fire_sets_hold_and_rejects_attack(self) -> None:
        battle = make_battle()
        dispatcher = OrderDispatcher()
        assert dispatcher.dispatch(make_order(OrderType.HOLD_FIRE, ("px-01", "px-02")), battle) is True
        assert battle.get("px-01").fire_mode is FireMode.HOLD  # type: ignore[union-attr]
        assert dispatcher.dispatch(make_order(OrderType.ATTACK_OBJECT, target="ex-01"), battle) is False
        assert not battle.projectiles

    def test_fire_hold_sets_hold_too(self) -> None:
        battle = make_battle()
        dispatcher = OrderDispatcher()
        assert dispatcher.dispatch(make_order(OrderType.FIRE_HOLD), battle) is True
        assert battle.get("px-01").fire_mode is FireMode.HOLD  # type: ignore[union-attr]

    def test_fire_free_sets_free_and_restores_attack(self) -> None:
        battle = make_battle()
        dispatcher = OrderDispatcher()
        assert dispatcher.dispatch(make_order(OrderType.FIRE_HOLD), battle) is True
        assert dispatcher.dispatch(make_order(OrderType.ATTACK_OBJECT, target="ex-01"), battle) is False
        assert dispatcher.dispatch(make_order(OrderType.FIRE_FREE, ("px-01", "px-02")), battle) is True
        assert battle.get("px-02").fire_mode is FireMode.FREE  # type: ignore[union-attr]
        assert dispatcher.dispatch(make_order(OrderType.ATTACK_OBJECT, target="ex-01"), battle) is True
        assert len(battle.projectiles) == 1


class TestGuardAndStopRouting:
    def test_guard_stops_and_sets_flag(self) -> None:
        battle = make_battle()
        dispatcher = OrderDispatcher()
        dispatcher.dispatch(make_order(OrderType.MOVE_TO_POINT, tile=(12, 10)), battle)
        assert dispatcher.dispatch(make_order(OrderType.GUARD), battle) is True
        mech = battle.get("px-01")
        assert mech is not None
        assert mech.move_target is None
        assert mech.guarding is True

    def test_next_move_clears_guard(self) -> None:
        battle = make_battle()
        dispatcher = OrderDispatcher()
        dispatcher.dispatch(make_order(OrderType.GUARD), battle)
        dispatcher.dispatch(make_order(OrderType.MOVE_TO_POINT, tile=(12, 10)), battle)
        mech = battle.get("px-01")
        assert mech is not None
        assert mech.guarding is False

    def test_stop_clears_move_and_keeps_guard_flag_untouched(self) -> None:
        battle = make_battle()
        dispatcher = OrderDispatcher()
        dispatcher.dispatch(make_order(OrderType.MOVE_TO_POINT, tile=(12, 10)), battle)
        assert dispatcher.dispatch(make_order(OrderType.STOP), battle) is True
        mech = battle.get("px-01")
        assert mech is not None
        assert mech.move_target is None
        assert mech.guarding is False

    def test_stop_unknown_unit_fails_but_still_logged(self) -> None:
        battle = make_battle()
        dispatcher = OrderDispatcher()
        assert dispatcher.dispatch(make_order(OrderType.STOP, ("nope",)), battle) is False
        assert len(dispatcher.order_log) == 1


class TestOrderLog:
    def test_log_records_orders_with_origin(self) -> None:
        battle = make_battle()
        dispatcher = OrderDispatcher()
        player_order = make_order(OrderType.MOVE_TO_POINT, tile=(12, 10))
        commander_order = make_order(OrderType.ATTACK_OBJECT, ("ex-01",), origin=OrderOrigin.COMMANDER, target="px-01")
        dispatcher.dispatch(player_order, battle)
        dispatcher.dispatch(commander_order, battle)
        log = dispatcher.order_log
        assert [entry.origin for entry in log] == [OrderOrigin.PLAYER, OrderOrigin.COMMANDER]
        assert log[0] is player_order
        assert log[1] is commander_order

    def test_log_ring_buffer_maxlen(self) -> None:
        battle = make_battle()
        dispatcher = OrderDispatcher()
        assert dispatcher._order_log.maxlen == ORDER_LOG_MAXLEN == 2000
        orders = [make_order(OrderType.STOP, ("nope",)) for _ in range(ORDER_LOG_MAXLEN + 5)]
        for order in orders:
            dispatcher.dispatch(order, battle)
        assert len(dispatcher.order_log) == ORDER_LOG_MAXLEN
        assert dispatcher.order_log[0] is orders[5]  # 最旧 5 条已被环形队列淘汰


class TestUnknownOrderType:
    def test_reserved_enum_member_dispatches_false(self) -> None:
        """枚举预留扩展位：未知类型（运行时注入）不崩溃、返回 False 且入日志。"""
        battle = make_battle()
        dispatcher = OrderDispatcher()
        order = make_order(OrderType.STOP)
        object.__setattr__(order, "order_type", "future_order")
        assert dispatcher.dispatch(order, battle) is False
        assert len(dispatcher.order_log) == 1
