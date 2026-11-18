"""M4 AI 集成测试：完整战斗中 rank 单调成长、goal 切换、决策健壮性与决策频率可控。

ROADMAP M4 验收：AI 对战压力场景无死锁；技能 rank 单调性；shutdown/部位摧毁
不影响 AI 决策崩溃；决策频率 N=1 与 N=30 结果可控（可重放、均能分出胜负）。
"""

from __future__ import annotations

from pymc2.domain.entities.mech import Faction, Mech, MechLocation, Weapon
from pymc2.domain.entities.pilot import Pilot
from pymc2.domain.orders.tactical_order import OrderType
from pymc2.services.ai_commander import AiCommander, GoalType
from pymc2.services.battle_service import BattleService, create_m4_battle
from pymc2.services.order_dispatcher import OrderDispatcher

FRAME_DT_S = 0.05
SETTLE_FRAMES = 500
DECISION_FRAMES = 700


class StaticRng:
    """固定随机源（恒命中、部位恒 CT），保证战斗结果确定可回归。"""

    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


def make_duel_battle() -> BattleService:
    """AI 对固定靶：玩家单机驻守原地，敌方单机（带驾驶员）由指挥官驱动。"""
    battle = BattleService(rng=StaticRng(0.0))
    battle.add_mech(Mech("px-01", Faction.PLAYER, 208.0, 336.0, weapon=Weapon("ppc", 25, 420.0, 2.0)))
    battle.add_mech(
        Mech(
            "ex-01",
            Faction.ENEMY,
            592.0,
            336.0,
            weapon=Weapon("mlaser", 15, 360.0, 2.5),
            pilot=Pilot("enemy-veteran"),
        )
    )
    return battle


def run_battle(
    battle: BattleService,
    commander: AiCommander,
    *,
    frames: int,
    on_frame=None,
) -> OrderDispatcher:
    """推进 N 帧完整决策循环（update → dispatch → tick）。"""
    dispatcher = OrderDispatcher()
    for _ in range(frames):
        commander.update(battle, dispatcher)
        battle.tick(FRAME_DT_S)
        if on_frame is not None:
            on_frame()
    return dispatcher


def snapshot(battle: BattleService, dispatcher: OrderDispatcher) -> tuple:
    """战局终态快照（位置/血量/存活/指令日志），供可重放断言。"""
    return (
        {mech_id: mech.position for mech_id, mech in battle.mechs.items()},
        {mech_id: (mech.hull, mech.alive) for mech_id, mech in battle.mechs.items()},
        tuple(dispatcher.order_log),
    )


class TestRankGrowthMonotonic:
    def test_pilot_rank_never_decreases_during_full_battle(self) -> None:
        battle = create_m4_battle(rng=StaticRng(0.0))
        commander = AiCommander(Faction.ENEMY, decision_interval_ticks=1)
        ranks: dict[str, list[float]] = {"ex-01": [], "ex-02": []}

        def sample() -> None:
            for mech_id, samples in ranks.items():
                mech = battle.get(mech_id)
                assert mech is not None and mech.pilot is not None
                samples.append(mech.pilot.gunnery_rank)

        run_battle(battle, commander, frames=SETTLE_FRAMES, on_frame=sample)
        # 单调性：战斗全程 rank 只升不降（恒命中 rng 下 hit_rate 与 kills 均不减）
        for samples in ranks.values():
            assert all(b >= a for a, b in zip(samples, samples[1:], strict=False))
            assert samples[-1] > samples[0]  # 且确实成长
        # 战斗分出胜负且击杀入账
        player = battle.get("px-01")
        assert player is not None and player.alive is False
        total_kills = 0
        for mech_id in ranks:
            mech = battle.get(mech_id)
            assert mech is not None and mech.pilot is not None
            total_kills += mech.pilot.kills
        assert total_kills >= 1


class TestGoalSwitch:
    def test_goal_switches_to_secure_region_after_annihilation(self) -> None:
        battle = make_duel_battle()
        commander = AiCommander(Faction.ENEMY, decision_interval_ticks=1)
        dispatcher = OrderDispatcher()
        player = battle.get("px-01")
        assert player is not None
        for _ in range(SETTLE_FRAMES):
            commander.update(battle, dispatcher)
            battle.tick(FRAME_DT_S)
            if not player.alive:
                commander.update(battle, dispatcher)  # 死亡后的下一次决策（N=1 必然触发）
                break
        assert player.alive is False
        # 敌全灭 → force goal 切换 SECURE_REGION，当前指令为向中央 (20,20) 移动
        assert commander.current_goal is not None
        assert commander.current_goal.goal_type is GoalType.SECURE_REGION
        orders = commander.propose(battle)
        assert orders[0].order_type is OrderType.MOVE_TO_POINT
        assert orders[0].target_tile == (20, 20)

    def test_victory_pilot_credits_exactly_one_kill(self) -> None:
        battle = make_duel_battle()
        commander = AiCommander(Faction.ENEMY, decision_interval_ticks=1)
        run_battle(battle, commander, frames=SETTLE_FRAMES)
        enemy = battle.get("ex-01")
        assert enemy is not None and enemy.pilot is not None
        assert enemy.pilot.kills == 1


def destroy_part(mech: Mech, location: MechLocation) -> None:
    """精确摧毁单个部位：伤害恰好等于该部位总余量，避免沿传导链溢出误杀 HEAD。"""
    part = mech.chassis.parts[location]
    mech.chassis.apply_damage(location, part.armor + part.structure)


class TestDecisionRobustness:
    def test_shutdown_mech_does_not_crash_ai_and_stays_put(self) -> None:
        battle = create_m4_battle(rng=StaticRng(0.0))
        ex1 = battle.get("ex-01")
        assert ex1 is not None
        ex1.heat.add_heat(100.0)  # 强制 shutdown 2s
        assert ex1.heat.shutdown_active
        start = ex1.position
        commander = AiCommander(Faction.ENEMY, decision_interval_ticks=1)
        run_battle(battle, commander, frames=10)  # shutdown 期内决策照常发生
        assert ex1.heat.shutdown_active  # 2s（40 帧）未结束
        assert ex1.position == start  # 移动/开火被拒，决策链路无异常
        assert not battle.projectiles

    def test_destroyed_weapon_arm_does_not_crash_ai(self) -> None:
        battle = create_m4_battle(rng=StaticRng(0.0))
        ex1 = battle.get("ex-01")
        assert ex1 is not None
        destroy_part(ex1, MechLocation.RA)  # RA 全毁（非致命部位）
        assert ex1.alive is True
        assert ex1.chassis.is_destroyed(MechLocation.RA)
        commander = AiCommander(Faction.ENEMY, decision_interval_ticks=1)
        run_battle(battle, commander, frames=10)  # 决策/分发不崩溃
        assert ex1.alive is True

    def test_destroyed_leg_mobility_penalty_still_receives_orders(self) -> None:
        battle = create_m4_battle(rng=StaticRng(0.0))
        ex2 = battle.get("ex-02")
        assert ex2 is not None
        destroy_part(ex2, MechLocation.LL)
        assert ex2.alive is True
        assert ex2.chassis.is_destroyed(MechLocation.LL)
        assert ex2.chassis.mobility_penalty > 0.0
        commander = AiCommander(Faction.ENEMY, decision_interval_ticks=1)
        run_battle(battle, commander, frames=10)
        assert ex2.alive is True


class TestDecisionFrequency:
    def test_n1_and_n30_both_replayable_and_decisive(self) -> None:
        def run(interval: int):
            battle = create_m4_battle(rng=StaticRng(0.0))
            commander = AiCommander(Faction.ENEMY, decision_interval_ticks=interval)
            dispatcher = run_battle(battle, commander, frames=DECISION_FRAMES)
            return battle, commander, snapshot(battle, dispatcher)

        battle_n1, commander_n1, snap_n1_a = run(1)
        _, _, snap_n1_b = run(1)
        assert snap_n1_a == snap_n1_b  # N=1 可重放
        battle_n30, commander_n30, snap_n30_a = run(30)
        _, _, snap_n30_b = run(30)
        assert snap_n30_a == snap_n30_b  # N=30 可重放
        # 决策频率可控：N=1 每帧决策，N=30 每 30 帧一次（30 与 700 帧）
        assert commander_n1.tick_count == DECISION_FRAMES
        assert commander_n30.tick_count == DECISION_FRAMES
        assert commander_n1.decision_interval_ticks == 1
        assert commander_n30.decision_interval_ticks == 30
        # 两种频率下战斗均分出胜负（结果可控）
        player_n1 = battle_n1.get("px-01")
        player_n30 = battle_n30.get("px-01")
        assert player_n1 is not None and player_n1.alive is False
        assert player_n30 is not None and player_n30.alive is False
