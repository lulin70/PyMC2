"""TacticalOrder DSL 单测：构造校验、不可变、序列化往返（M2）。"""

from __future__ import annotations

import dataclasses

import pytest

from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType, TacticalOrder


class TestEnums:
    def test_seven_order_types_in_m2(self) -> None:
        assert {member.name for member in OrderType} == {
            "MOVE_TO_POINT",
            "ATTACK_OBJECT",
            "HOLD_FIRE",
            "FIRE_FREE",
            "FIRE_HOLD",
            "GUARD",
            "STOP",
        }

    def test_three_origins(self) -> None:
        assert {member.name for member in OrderOrigin} == {"PLAYER", "COMMANDER", "SELF"}


class TestConstruction:
    def test_construct_with_all_fields(self) -> None:
        order = TacticalOrder(
            order_type=OrderType.MOVE_TO_POINT,
            unit_ids=("px-01", "px-02"),
            origin=OrderOrigin.PLAYER,
            target_tile=(12, 10),
            target_unit_id=None,
            issued_tick=42,
        )
        assert order.order_type is OrderType.MOVE_TO_POINT
        assert order.unit_ids == ("px-01", "px-02")
        assert order.origin is OrderOrigin.PLAYER
        assert order.target_tile == (12, 10)
        assert order.target_unit_id is None
        assert order.issued_tick == 42

    def test_issued_tick_defaults_to_zero(self) -> None:
        order = TacticalOrder(OrderType.STOP, ("px-01",), OrderOrigin.SELF, None, None)
        assert order.issued_tick == 0

    def test_frozen_immutable(self) -> None:
        order = TacticalOrder(OrderType.STOP, ("px-01",), OrderOrigin.PLAYER, None, None)
        with pytest.raises(dataclasses.FrozenInstanceError):
            order.order_type = OrderType.GUARD  # type: ignore[misc]

    def test_equal_and_hashable(self) -> None:
        a = TacticalOrder(OrderType.MOVE_TO_POINT, ("px-01",), OrderOrigin.PLAYER, (1, 2), None, 7)
        b = TacticalOrder(OrderType.MOVE_TO_POINT, ("px-01",), OrderOrigin.PLAYER, (1, 2), None, 7)
        assert a == b
        assert hash(a) == hash(b)

    def test_move_requires_target_tile(self) -> None:
        with pytest.raises(ValueError, match="target_tile"):
            TacticalOrder(OrderType.MOVE_TO_POINT, ("px-01",), OrderOrigin.PLAYER, None, None)

    def test_attack_requires_target_unit(self) -> None:
        with pytest.raises(ValueError, match="target_unit_id"):
            TacticalOrder(OrderType.ATTACK_OBJECT, ("px-01",), OrderOrigin.PLAYER, None, None)

    def test_empty_unit_ids_rejected(self) -> None:
        with pytest.raises(ValueError, match="unit_ids"):
            TacticalOrder(OrderType.STOP, (), OrderOrigin.PLAYER, None, None)

    def test_stance_orders_need_no_payload(self) -> None:
        for order_type in (
            OrderType.HOLD_FIRE,
            OrderType.FIRE_FREE,
            OrderType.FIRE_HOLD,
            OrderType.GUARD,
            OrderType.STOP,
        ):
            order = TacticalOrder(order_type, ("px-01",), OrderOrigin.PLAYER, None, None)
            assert order.target_tile is None
            assert order.target_unit_id is None


class TestSerialization:
    def test_move_order_roundtrip(self) -> None:
        order = TacticalOrder(OrderType.MOVE_TO_POINT, ("px-01", "px-02"), OrderOrigin.PLAYER, (12, 10), None, 3)
        assert TacticalOrder.from_dict(order.to_dict()) == order

    def test_attack_order_roundtrip(self) -> None:
        order = TacticalOrder(OrderType.ATTACK_OBJECT, ("ex-01",), OrderOrigin.COMMANDER, None, "px-01", 9)
        assert TacticalOrder.from_dict(order.to_dict()) == order

    def test_to_dict_is_json_compatible(self) -> None:
        order = TacticalOrder(OrderType.HOLD_FIRE, ("px-01",), OrderOrigin.PLAYER, None, None)
        payload = order.to_dict()
        assert payload["order_type"] == "hold_fire"
        assert payload["unit_ids"] == ["px-01"]
        assert payload["origin"] == "player"
        assert payload["target_tile"] is None
        assert payload["issued_tick"] == 0

    def test_from_dict_unknown_type_raises(self) -> None:
        with pytest.raises(ValueError):
            TacticalOrder.from_dict({"order_type": "warp_jump", "unit_ids": ["px-01"], "origin": "player"})
