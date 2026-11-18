"""指令分发器：TacticalOrder 单一入口，按类型路由到 BattleService 能力（M2）。

MC2 参照：tacordr 层指令为纯数据对象，可序列化；指令日志（环形队列）保留
最近 2000 条，为回放系统（同一指令序列重放结果一致）与 M4 AI 复盘保留通道。
"""

from __future__ import annotations

from collections import deque

from pymc2.domain.entities.mech import TILE_SIZE_PX, FireMode
from pymc2.domain.orders.tactical_order import OrderType, TacticalOrder
from pymc2.services.battle_service import BattleService

ORDER_LOG_MAXLEN = 2000


class OrderDispatcher:
    """战术指令分发器：dispatch(order, battle) 单一入口，所有指令（含失败）入日志。"""

    def __init__(self, *, log_maxlen: int = ORDER_LOG_MAXLEN) -> None:
        self._order_log: deque[TacticalOrder] = deque(maxlen=log_maxlen)

    @property
    def order_log(self) -> tuple[TacticalOrder, ...]:
        """当前指令日志快照（旧→新）。"""
        return tuple(self._order_log)

    def dispatch(self, order: TacticalOrder, battle: BattleService) -> bool:
        """分发一条指令；返回是否有至少一个单位成功执行。"""
        self._order_log.append(order)
        match order.order_type:
            case OrderType.MOVE_TO_POINT:
                return self._dispatch_move(order, battle)
            case OrderType.ATTACK_OBJECT:
                return self._dispatch_attack(order, battle)
            case OrderType.HOLD_FIRE:
                return self._apply_fire_mode(order, battle, FireMode.HOLD)
            case OrderType.FIRE_FREE:
                return self._apply_fire_mode(order, battle, FireMode.FREE)
            case OrderType.FIRE_HOLD:
                # M2 与 HOLD_FIRE 同效（fire_mode=HOLD）；MC2 双枚举为 M4 细化预留。
                return self._apply_fire_mode(order, battle, FireMode.HOLD)
            case OrderType.GUARD:
                return self._dispatch_guard(order, battle)
            case OrderType.STOP:
                return self._dispatch_stop(order, battle)
        return False  # 未知类型（预留扩展位）

    def _dispatch_move(self, order: TacticalOrder, battle: BattleService) -> bool:
        if order.target_tile is None:
            return False
        tile_x, tile_y = order.target_tile
        x = tile_x * TILE_SIZE_PX + TILE_SIZE_PX / 2
        y = tile_y * TILE_SIZE_PX + TILE_SIZE_PX / 2
        executed = False
        for uid in order.unit_ids:
            if battle.move_mech(uid, x, y):
                executed = True
        return executed

    def _dispatch_attack(self, order: TacticalOrder, battle: BattleService) -> bool:
        if order.target_unit_id is None:
            return False
        executed = False
        for uid in order.unit_ids:
            if battle.fire_at(uid, order.target_unit_id):
                attacker = battle.get(uid)
                if attacker is not None:
                    attacker.attack_target_id = order.target_unit_id
                executed = True
        return executed

    def _apply_fire_mode(self, order: TacticalOrder, battle: BattleService, mode: FireMode) -> bool:
        executed = False
        for uid in order.unit_ids:
            if battle.set_fire_mode(uid, mode):
                executed = True
        return executed

    def _dispatch_guard(self, order: TacticalOrder, battle: BattleService) -> bool:
        executed = False
        for uid in order.unit_ids:
            if battle.set_guard(uid):
                executed = True
        return executed

    def _dispatch_stop(self, order: TacticalOrder, battle: BattleService) -> bool:
        executed = False
        for uid in order.unit_ids:
            if battle.stop_mech(uid):
                executed = True
        return executed
