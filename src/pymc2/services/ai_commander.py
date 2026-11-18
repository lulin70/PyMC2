"""AI 指挥官占位：M2 提供决策入口与最近目标攻击；M4 深化为 goal 分解。"""

from __future__ import annotations

from pymc2.domain.entities.mech import Faction
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType, TacticalOrder
from pymc2.services.battle_service import BattleService


class AiCommander:
    """阵营指挥官：propose 产出 origin=COMMANDER 的指令，经 OrderDispatcher 与玩家指令同路径执行。"""

    def __init__(self, faction: Faction = Faction.ENEMY) -> None:
        self.faction = faction

    def propose(self, battle: BattleService) -> list[TacticalOrder]:
        """为所属阵营每台存活机甲提出「攻击最近敌人」指令（M2 简单实现）。"""
        orders: list[TacticalOrder] = []
        for mech in battle.mechs.values():
            if mech.faction is not self.faction or not mech.alive:
                continue
            target = battle.nearest_enemy(mech)
            if target is None:
                continue
            orders.append(
                TacticalOrder(
                    order_type=OrderType.ATTACK_OBJECT,
                    unit_ids=(mech.id,),
                    origin=OrderOrigin.COMMANDER,
                    target_tile=None,
                    target_unit_id=target.id,
                )
            )
        return orders
