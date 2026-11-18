"""AiCommander 单测：M2 决策入口占位——最近目标攻击、origin=COMMANDER、存活过滤。"""

from __future__ import annotations

from pymc2.domain.entities.mech import Faction, Mech, Weapon
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType
from pymc2.services.ai_commander import AiCommander
from pymc2.services.battle_service import BattleService


def make_battle() -> BattleService:
    """三机甲战局：ex-01(200) 距 px-01(100) 近、距 px-02(500) 远。"""
    battle = BattleService()
    battle.add_mech(Mech("px-01", Faction.PLAYER, 100.0, 100.0, weapon=Weapon("ppc", 25, 420.0, 2.0)))
    battle.add_mech(Mech("px-02", Faction.PLAYER, 500.0, 100.0, weapon=Weapon("ppc", 25, 420.0, 2.0)))
    battle.add_mech(Mech("ex-01", Faction.ENEMY, 200.0, 100.0, weapon=Weapon("mlaser", 15, 360.0, 2.5)))
    return battle


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

    def test_no_targets_returns_empty(self) -> None:
        battle = BattleService()
        battle.add_mech(Mech("ex-01", Faction.ENEMY, 0.0, 0.0))
        assert AiCommander(Faction.ENEMY).propose(battle) == []

    def test_propose_is_deterministic(self) -> None:
        first = AiCommander(Faction.PLAYER).propose(make_battle())
        second = AiCommander(Faction.PLAYER).propose(make_battle())
        assert first == second

    def test_default_faction_is_enemy(self) -> None:
        assert AiCommander().faction is Faction.ENEMY
