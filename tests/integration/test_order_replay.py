"""M2 指令回放一致性集成测试（ROADMAP M2 重点验收）。

同一组 TacticalOrder 序列注入两次（StaticRng 固定种子），最终位置/伤害/
指令日志完全一致；AiCommander 产出经 dispatcher 执行，与玩家指令同源同路径。
"""

from __future__ import annotations

import pytest

from pymc2.domain.entities.mech import Faction
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType, TacticalOrder
from pymc2.services.ai_commander import AiCommander
from pymc2.services.battle_service import create_m2_battle
from pymc2.services.order_dispatcher import OrderDispatcher

FRAME_DT_S = 0.05
SETTLE_FRAMES = 200


class StaticRng:
    """固定随机源（恒命中），保证重放结果确定可回归。"""

    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


def build_orders() -> list[TacticalOrder]:
    """覆盖 7 类指令的玩家指令序列（issued_tick 显式标注，便于日志对齐）。

    关键语义：issued_tick=20 的 ATTACK 发生在 FIRE_HOLD 之后，应被 HOLD 拒绝。
    """
    return [
        TacticalOrder(OrderType.MOVE_TO_POINT, ("px-01", "px-02"), OrderOrigin.PLAYER, (12, 10), None, 0),
        TacticalOrder(OrderType.FIRE_HOLD, ("px-01", "px-02"), OrderOrigin.PLAYER, None, None, 10),
        TacticalOrder(OrderType.ATTACK_OBJECT, ("px-01",), OrderOrigin.PLAYER, None, "ex-01", 20),
        TacticalOrder(OrderType.FIRE_FREE, ("px-01", "px-02"), OrderOrigin.PLAYER, None, None, 30),
        TacticalOrder(OrderType.MOVE_TO_POINT, ("px-01",), OrderOrigin.PLAYER, (14, 12), None, 40),
        TacticalOrder(OrderType.ATTACK_OBJECT, ("px-02",), OrderOrigin.PLAYER, None, "ex-01", 50),
        TacticalOrder(OrderType.GUARD, ("px-01",), OrderOrigin.PLAYER, None, None, 60),
        TacticalOrder(OrderType.STOP, ("px-02",), OrderOrigin.PLAYER, None, None, 70),
    ]


def run_replay(*, with_ai: bool) -> dict[str, object]:
    battle = create_m2_battle(rng=StaticRng(0.0))
    dispatcher = OrderDispatcher()
    for order in build_orders():
        dispatcher.dispatch(order, battle)
        battle.tick(FRAME_DT_S)
    for _ in range(SETTLE_FRAMES):
        battle.tick(FRAME_DT_S)
    if with_ai:
        for order in AiCommander(Faction.ENEMY).propose(battle):
            dispatcher.dispatch(order, battle)
        for _ in range(SETTLE_FRAMES):
            battle.tick(FRAME_DT_S)
    return {
        "positions": {mech_id: mech.position for mech_id, mech in battle.mechs.items()},
        "hulls": {mech_id: (mech.hull, mech.alive) for mech_id, mech in battle.mechs.items()},
        "log": list(dispatcher.order_log),
    }


class TestReplayConsistency:
    def test_same_order_sequence_replays_identically(self) -> None:
        snap_a = run_replay(with_ai=False)
        snap_b = run_replay(with_ai=False)
        assert snap_a == snap_b
        # 伤害语义校验：HOLD 拒绝了 issued_tick=20 的攻击，敌方只吃 px-02 一发（15）
        assert snap_a["hulls"] == {"px-01": (100, True), "px-02": (100, True), "ex-01": (85, True)}
        # 位置语义校验：GUARD/STOP 在移动途中叫停，谁都没走到目标点
        positions = snap_a["positions"]
        assert positions["ex-01"] == (592.0, 336.0)  # 敌方未被移动
        # px-01：先横移 4 步（x=227.2）被改派至 (464,400)，斜走 2 步后被 GUARD 叫停
        assert 227.2 < positions["px-01"][0] < 400.0
        assert 336.0 < positions["px-01"][1] < 400.0
        # px-02：纯横移 7 步后被 STOP 叫停（272 + 7 × 4.8px）
        assert positions["px-02"][0] == pytest.approx(272 + 7 * 4.8, abs=0.01)
        assert positions["px-02"][1] == pytest.approx(336.0, abs=0.01)
        log = snap_a["log"]
        assert len(log) == 8
        assert [order.issued_tick for order in log] == [0, 10, 20, 30, 40, 50, 60, 70]
        assert all(order.origin is OrderOrigin.PLAYER for order in log)

    def test_ai_phase_replays_identically_and_records_commander_origin(self) -> None:
        snap_a = run_replay(with_ai=True)
        snap_b = run_replay(with_ai=True)
        assert snap_a == snap_b
        # AI 指令与玩家指令在同一日志中按 origin 区分
        log = snap_a["log"]
        assert len(log) == 9
        assert all(order.origin is OrderOrigin.PLAYER for order in log[:8])
        assert log[8].origin is OrderOrigin.COMMANDER
        assert log[8].order_type is OrderType.ATTACK_OBJECT
        # AI（COMMANDER）攻击确实造成伤害：ex-01 打最近的 px-02（15 伤害）
        assert snap_a["hulls"] == {"px-01": (100, True), "px-02": (85, True), "ex-01": (85, True)}


class TestAiSamePathAsPlayer:
    def test_same_attack_order_from_player_and_commander_produces_same_effect(self) -> None:
        results: list[tuple[int, tuple[int, bool], list[TacticalOrder]]] = []
        for origin in (OrderOrigin.PLAYER, OrderOrigin.COMMANDER):
            battle = create_m2_battle(rng=StaticRng(0.0))
            dispatcher = OrderDispatcher()
            order = TacticalOrder(OrderType.ATTACK_OBJECT, ("px-01",), origin, None, "ex-01")
            assert dispatcher.dispatch(order, battle) is True
            for _ in range(SETTLE_FRAMES):
                battle.tick(FRAME_DT_S)
            enemy = battle.get("ex-01")
            assert enemy is not None
            results.append((len(battle.projectiles), (enemy.hull, enemy.alive), list(dispatcher.order_log)))
        # 同一指令仅 origin 不同 → 仿真效果完全同源
        assert results[0][:2] == results[1][:2]
        assert results[0][1] == (75, True)
        player_log, commander_log = results[0][2], results[1][2]
        assert len(player_log) == len(commander_log) == 1
        assert player_log[0].origin is OrderOrigin.PLAYER
        assert commander_log[0].origin is OrderOrigin.COMMANDER
