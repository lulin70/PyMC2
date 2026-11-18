"""自动交战单测（用户反馈：开火自动化，不要右键点敌人）。

覆盖：FREE 自动锁定射程内最近敌机甲并开火 / 超射程不锁 / HOLD 火力纪律不锁 /
显式指定目标优先于 SELF（不覆盖）/ 目标死亡与出射程清锁重锁 / 未接 dispatcher
时零行为（存量兼容）。自动交战全链路（敌入射程 → 开火 → 掉血）见
integration/test_battle_service.py::TestAutoEngagement。
"""

from __future__ import annotations

from pymc2.domain.entities.mech import Faction, FireMode, Mech, Weapon
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType, TacticalOrder
from pymc2.services.battle_service import BattleService
from pymc2.services.order_dispatcher import OrderDispatcher

PLAYER_X, ENEMY_ROW_Y = 200.0, 200.0


class StaticRng:
    """固定随机源：random() 恒返回 value（0.0 必命中）。"""

    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


def make_battle() -> tuple[BattleService, OrderDispatcher]:
    """单机甲战局 + 已接线的 dispatcher（自动交战开启）。"""
    battle = BattleService(rng=StaticRng(0.0))
    dispatcher = OrderDispatcher()
    battle.dispatcher = dispatcher
    battle.add_mech(Mech("px-01", Faction.PLAYER, PLAYER_X, ENEMY_ROW_Y, weapon=Weapon("ppc", 25, 420.0, 2.0)))
    return battle, dispatcher


def add_enemy(battle: BattleService, mech_id: str, x: float) -> Mech:
    """加入一台敌方机甲（与玩家同排，距离 = x - 200）。"""
    mech = Mech(mech_id, Faction.ENEMY, x, ENEMY_ROW_Y, weapon=Weapon("mlaser", 15, 360.0, 2.5))
    battle.add_mech(mech)
    return mech


def player_attack(unit_id: str, target_id: str) -> TacticalOrder:
    """构造玩家显式攻击指令（origin=PLAYER）。"""
    return TacticalOrder(
        order_type=OrderType.ATTACK_OBJECT,
        unit_ids=(unit_id,),
        origin=OrderOrigin.PLAYER,
        target_tile=None,
        target_unit_id=target_id,
    )


def self_order_targets(dispatcher: OrderDispatcher, unit_id: str = "px-01") -> list[str | None]:
    """指定机甲的 SELF 源自动交战指令目标序列（旧→新；敌方机甲同样会自动还击，须按单位过滤）。"""
    return [
        order.target_unit_id
        for order in dispatcher.order_log
        if order.origin is OrderOrigin.SELF and order.unit_ids == (unit_id,)
    ]


class TestAutoLock:
    def test_free_mech_auto_locks_nearest_in_range_and_fires(self) -> None:
        battle, dispatcher = make_battle()
        near = add_enemy(battle, "ex-near", 300.0)  # 距离 100px
        add_enemy(battle, "ex-far", 500.0)  # 距离 300px，同样在射程内
        battle.tick(1 / 60)
        player = battle.get("px-01")
        assert player is not None
        assert player.attack_target_id == near.id  # 锁最近
        assert self_order_targets(dispatcher) == [near.id]
        # 锁定即开火（武器就绪）；敌方机甲同样自动还击（弹丸含敌方来源）
        player_shots = [p for p in battle.projectiles if p.attacker_id == "px-01"]
        assert len(player_shots) == 1
        assert player_shots[0].target_id == near.id

    def test_out_of_range_enemy_not_locked(self) -> None:
        battle, dispatcher = make_battle()
        add_enemy(battle, "ex-far", 700.0)  # 距离 500px > 射程 420px（敌方射程 360 亦不及）
        battle.tick(1 / 60)
        player = battle.get("px-01")
        assert player is not None
        assert player.attack_target_id is None
        assert self_order_targets(dispatcher) == []
        assert not battle.projectiles

    def test_hold_fire_mode_never_locks(self) -> None:
        battle, dispatcher = make_battle()
        add_enemy(battle, "ex-near", 300.0)
        assert battle.set_fire_mode("px-01", FireMode.HOLD) is True
        battle.tick(1 / 60)
        player = battle.get("px-01")
        assert player is not None
        assert player.attack_target_id is None  # 火力纪律：不自动锁定
        assert self_order_targets(dispatcher) == []
        # HOLD 仅约束持有者自身：px-01 不开火（敌方 FREE 机甲仍会自动还击）
        assert not [p for p in battle.projectiles if p.attacker_id == "px-01"]

    def test_without_dispatcher_no_auto_engagement(self) -> None:
        """未接 dispatcher（存量直构战局）→ 零自动交战行为。"""
        battle = BattleService(rng=StaticRng(0.0))
        battle.add_mech(Mech("px-01", Faction.PLAYER, PLAYER_X, ENEMY_ROW_Y))
        battle.add_mech(Mech("ex-01", Faction.ENEMY, 300.0, ENEMY_ROW_Y))
        battle.tick(1 / 60)
        player = battle.get("px-01")
        assert player is not None
        assert player.attack_target_id is None
        assert not battle.projectiles


class TestExplicitTargetPriority:
    def test_player_designated_target_not_overridden_by_self(self) -> None:
        """玩家显式指定远距目标后，SELF 不改锁；冷却结束继续对该目标开火。"""
        battle, dispatcher = make_battle()
        add_enemy(battle, "ex-near", 300.0)  # 更近但未被指定
        far = add_enemy(battle, "ex-far", 500.0)
        assert dispatcher.dispatch(player_attack("px-01", far.id), battle) is True
        battle.tick(1 / 60)  # 冷却中：SELF 不下发、不改锁
        player = battle.get("px-01")
        assert player is not None
        assert player.attack_target_id == far.id
        assert self_order_targets(dispatcher) == []
        battle.tick(2.0)  # 冷却结束：SELF 继续对显式目标开火（不改打更近的 ex-near）
        assert player.attack_target_id == far.id
        assert self_order_targets(dispatcher) == [far.id]
        # 两发（玩家显式 + SELF 续射）均落 far：恒命中恒 CT → 总量精确扣 50
        assert far.hull == 50
        assert not battle.projectiles


class TestStaleTarget:
    def test_target_death_clears_lock_and_relocks(self) -> None:
        battle, dispatcher = make_battle()
        near = add_enemy(battle, "ex-near", 300.0)
        far = add_enemy(battle, "ex-far", 500.0)
        battle.tick(1 / 60)
        player = battle.get("px-01")
        assert player is not None
        assert player.attack_target_id == near.id
        near.take_damage(200.0)  # 目标死亡
        player.weapon.cd_remaining = 0.0  # 跳过冷却，验证下个 tick 即重锁
        battle.tick(1 / 60)
        assert player.attack_target_id == far.id  # 自动重锁射程内最近
        assert self_order_targets(dispatcher) == [near.id, far.id]

    def test_target_out_of_range_clears_lock_without_relock(self) -> None:
        battle, dispatcher = make_battle()
        near = add_enemy(battle, "ex-near", 300.0)
        battle.tick(1 / 60)
        player = battle.get("px-01")
        assert player is not None
        assert player.attack_target_id == near.id
        near.x = 2000.0  # 出射程
        battle.tick(1 / 60)
        assert player.attack_target_id is None  # 清锁；无射程内目标 → 不重锁
        assert self_order_targets(dispatcher) == [near.id]
