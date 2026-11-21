"""AI 指挥官（M4）：goal 分解驱动，替代 M2 的简单最近目标攻击。

MC2 参照：目标体系 GOAL_OBJECT / GOAL_REGION（objective 层）。本波三类 force 级 goal：
- PRESERVE_FORCE（最高优先）：己方机甲 hull 低于阈值 → 该机甲后撤（MOVE 远离最近敌人）；
- DESTROY_OBJECT：存在存活敌机甲 → 攻击最弱敌机甲（集中火力，预期击杀最大化）；
  超出射程时先接近到 standoff 距离再打；
- SECURE_REGION：敌全灭 → 向地图中央区域移动。

决策循环：update(battle, dispatcher) 逐帧调用，内部计数每 N tick（N 可注入）
触发一次 evaluate → decompose → dispatch；propose(battle) 为纯决策入口
（输出只依赖 battle 状态），供单测直接断言。回退路径（无 goal 适用时的
默认指令工厂）与决策频率均可注入。

P2 单位谱系泛化（Unit 协议）：目标选择优先级简单规则——攻击性目标（mobile，
机甲/载具）> 炮塔（固定工事）；不可移动单位跳过 PRESERVE_FORCE 后撤与
SECURE_REGION 移动（原地死守，fallback GUARD），攻击分派不变。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from pymc2.domain.entities.mech import MAP_SIZE_PX, TILE_SIZE_PX, Faction
from pymc2.domain.entities.unit import Unit
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType, TacticalOrder
from pymc2.services.battle_service import BattleService
from pymc2.services.order_dispatcher import OrderDispatcher

DEFAULT_DECISION_INTERVAL_TICKS = 30
PRESERVE_HULL_RATIO = 0.30
RETREAT_DISTANCE_PX = 240.0
SECURE_REGION_CENTER_PX = (MAP_SIZE_PX / 2.0, MAP_SIZE_PX / 2.0)
SECURE_REGION_RADIUS_PX = 96.0
DESTROY_STANDOFF_RATIO = 0.8  # 接近目标时停在射程 80% 处（留命中余量）

GoalComplete = Callable[[BattleService], bool]
FallbackFactory = Callable[[Unit, BattleService], TacticalOrder | None]


class GoalType(Enum):
    """force 级目标类型：GOAL_OBJECT 同型命名 + 保存兵力（MC2 objective 扩展）。"""

    PRESERVE_FORCE = "preserve_force"
    DESTROY_OBJECT = "destroy_object"
    SECURE_REGION = "secure_region"


@dataclass(frozen=True)
class Goal:
    """一条 force 级目标：类型 + 优先级（越小越高）+ 完成条件谓词（goal link）。"""

    goal_type: GoalType
    priority: int
    is_complete: GoalComplete


def _default_fallback(mech: Unit, battle: BattleService) -> TacticalOrder:
    """默认回退：无 goal 适用 → 原地警戒（GUARD）。"""
    return TacticalOrder(OrderType.GUARD, (mech.id,), OrderOrigin.COMMANDER, None, None)


class AiCommander:
    """阵营指挥官：goal 优先级评估 → 每单位分解为 TacticalOrder → 经 Dispatcher 执行。"""

    def __init__(
        self,
        faction: Faction = Faction.ENEMY,
        *,
        decision_interval_ticks: int = DEFAULT_DECISION_INTERVAL_TICKS,
        preserve_hull_ratio: float = PRESERVE_HULL_RATIO,
        fallback: FallbackFactory | None = None,
    ) -> None:
        self.faction = faction
        self.decision_interval_ticks = decision_interval_ticks
        self.preserve_hull_ratio = preserve_hull_ratio
        self._fallback = fallback if fallback is not None else _default_fallback
        self.tick_count = 0
        self.current_goal: Goal | None = None

    # ------------------------------------------------------------------ 决策循环

    def update(self, battle: BattleService, dispatcher: OrderDispatcher) -> bool:
        """逐帧决策循环：每 N tick 触发一次 propose → dispatch；返回本帧是否决策。"""
        self.tick_count += 1
        if self.decision_interval_ticks <= 0 or self.tick_count % self.decision_interval_ticks != 0:
            return False
        for order in self.propose(battle):
            dispatcher.dispatch(order, battle)
        return True

    def propose(self, battle: BattleService) -> list[TacticalOrder]:
        """纯决策：评估 goal 优先级 → 分解为每机甲指令（origin=COMMANDER）。"""
        self.current_goal = self._evaluate_goal(battle)
        orders: list[TacticalOrder] = []
        for mech in battle.mechs.values():
            if mech.faction is not self.faction or not mech.alive:
                continue
            order = self._order_for_mech(mech, battle)
            if order is None:
                order = self._fallback(mech, battle)
            if order is not None:
                orders.append(order)
        return orders

    # ------------------------------------------------------------------ goal 评估

    def _goal_table(self) -> tuple[Goal, ...]:
        """goal 优先级表：保存兵力 > 摧毁目标 > 控制区域。"""
        return (
            Goal(GoalType.PRESERVE_FORCE, 0, self._preserve_force_complete),
            Goal(GoalType.DESTROY_OBJECT, 1, self._destroy_object_complete),
            Goal(GoalType.SECURE_REGION, 2, self._secure_region_complete),
        )

    def _evaluate_goal(self, battle: BattleService) -> Goal | None:
        """按优先级取第一个未完成的 goal；全部完成 → None（走回退路径）。"""
        for goal in self._goal_table():
            if not goal.is_complete(battle):
                return goal
        return None

    def _own_mechs(self, battle: BattleService) -> list[Unit]:
        return [m for m in battle.mechs.values() if m.faction is self.faction and m.alive]

    def _alive_enemies(self, battle: BattleService) -> list[Unit]:
        return [m for m in battle.mechs.values() if m.faction is not self.faction and m.alive]

    def _preserve_force_complete(self, battle: BattleService) -> bool:
        return all(m.hull_ratio >= self.preserve_hull_ratio for m in self._own_mechs(battle))

    def _destroy_object_complete(self, battle: BattleService) -> bool:
        return not self._alive_enemies(battle)

    def _secure_region_complete(self, battle: BattleService) -> bool:
        mechs = self._own_mechs(battle)
        if not mechs:
            return True  # 己方全灭 → 无兵可守，视为完成
        cx, cy = SECURE_REGION_CENTER_PX
        return all(math.hypot(m.x - cx, m.y - cy) <= SECURE_REGION_RADIUS_PX for m in mechs)

    # ------------------------------------------------------------------ 每机甲分解

    def _order_for_mech(self, mech: Unit, battle: BattleService) -> TacticalOrder | None:
        """per-mech 优先级级联：后撤 > 攻击最弱 > 就位守区；全满足 → None。

        P2：不可移动单位（炮塔）不后撤不占区（固定工事原地死守走 fallback GUARD），
        攻击分派不变——MOVE 指令本就会被 move_mech 拒绝，提前分流防指令日志刷屏。
        """
        if mech.mobile and mech.hull_ratio < self.preserve_hull_ratio:
            return self._retreat_order(mech, battle)  # PRESERVE_FORCE
        enemies = self._alive_enemies(battle)
        if enemies:
            return self._destroy_order(mech, enemies)  # DESTROY_OBJECT
        if mech.mobile and not self._secure_region_complete(battle):
            return self._secure_order(mech)  # SECURE_REGION
        return None

    def _destroy_order(self, mech: Unit, enemies: list[Unit]) -> TacticalOrder:
        """DESTROY_OBJECT：最弱优先集火；超出射程先接近（MOVE 到 standoff 点）。"""
        target = self._weakest_enemy(mech, enemies)
        distance = math.hypot(target.x - mech.x, target.y - mech.y)
        if distance <= mech.weapon.range_px:
            return TacticalOrder(
                order_type=OrderType.ATTACK_OBJECT,
                unit_ids=(mech.id,),
                origin=OrderOrigin.COMMANDER,
                target_tile=None,
                target_unit_id=target.id,
            )
        return TacticalOrder(
            order_type=OrderType.MOVE_TO_POINT,
            unit_ids=(mech.id,),
            origin=OrderOrigin.COMMANDER,
            target_tile=self._approach_tile(mech, target),
            target_unit_id=None,
        )

    def _weakest_enemy(self, mech: Unit, enemies: list[Unit]) -> Unit:
        """目标选择（P2 简单规则）：攻击性目标（mobile，机甲/载具）> 炮塔（固定工事
        不构成威胁）→ 同层 hull 最低优先（集火速杀 → 预期击杀最大化）→ 同血量取最近
        → 再取 id 序。全机甲战局 mobile 恒 True，排序与存量口径一致。"""
        return min(
            enemies,
            key=lambda e: (not e.mobile, e.hull, math.hypot(e.x - mech.x, e.y - mech.y), e.id),
        )

    def _retreat_order(self, mech: Unit, battle: BattleService) -> TacticalOrder:
        """PRESERVE_FORCE：向远离最近敌人的方向后撤；无威胁则转入守区。"""
        enemy = battle.nearest_enemy(mech)
        if enemy is None:
            return self._secure_order(mech)
        px, py = self._retreat_point(mech, enemy)
        return TacticalOrder(
            order_type=OrderType.MOVE_TO_POINT,
            unit_ids=(mech.id,),
            origin=OrderOrigin.COMMANDER,
            target_tile=self._point_tile(px, py),
            target_unit_id=None,
        )

    def _retreat_point(self, mech: Unit, enemy: Unit) -> tuple[float, float]:
        """后撤点：沿远离向量 RETREAT_DISTANCE_PX；地图边缘钳制后若不再更远，
        改撤向离敌最远的地图角（保证后撤不反向缩短敌我距离）。"""
        dx, dy = mech.x - enemy.x, mech.y - enemy.y
        dist = math.hypot(dx, dy)
        if dist <= 0.0:
            ux, uy = 0.0, -1.0  # 重合时固定向北（确定性）
        else:
            ux, uy = dx / dist, dy / dist
        px = max(0.0, min(MAP_SIZE_PX, mech.x + ux * RETREAT_DISTANCE_PX))
        py = max(0.0, min(MAP_SIZE_PX, mech.y + uy * RETREAT_DISTANCE_PX))
        if math.hypot(px - enemy.x, py - enemy.y) < dist:
            corners = ((0.0, 0.0), (MAP_SIZE_PX, 0.0), (0.0, MAP_SIZE_PX), (MAP_SIZE_PX, MAP_SIZE_PX))
            px, py = max(corners, key=lambda c: math.hypot(c[0] - enemy.x, c[1] - enemy.y))
        return (px, py)

    def _secure_order(self, mech: Unit) -> TacticalOrder:
        """SECURE_REGION：向地图中央区域中心移动。"""
        cx, cy = SECURE_REGION_CENTER_PX
        return TacticalOrder(
            order_type=OrderType.MOVE_TO_POINT,
            unit_ids=(mech.id,),
            origin=OrderOrigin.COMMANDER,
            target_tile=self._point_tile(cx, cy),
            target_unit_id=None,
        )

    # ------------------------------------------------------------------ 几何工具

    def _approach_tile(self, mech: Unit, target: Unit) -> tuple[int, int]:
        """接近点：目标沿趋近方向 standoff 距离处（clamp 到地图内）。"""
        standoff = mech.weapon.range_px * DESTROY_STANDOFF_RATIO
        dx, dy = mech.x - target.x, mech.y - target.y
        dist = math.hypot(dx, dy)  # 调用前提 dist > range > 0
        return self._point_tile(target.x + dx / dist * standoff, target.y + dy / dist * standoff)

    @staticmethod
    def _point_tile(x: float, y: float) -> tuple[int, int]:
        """像素点 → 钳制在地图内的 tile 坐标（dispatcher 按 tile 中心执行）。"""
        cx = max(0.0, min(MAP_SIZE_PX - 1.0, x))
        cy = max(0.0, min(MAP_SIZE_PX - 1.0, y))
        return (int(cx) // TILE_SIZE_PX, int(cy) // TILE_SIZE_PX)
