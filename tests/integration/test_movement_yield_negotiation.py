"""让路协商集成测试（MC2-05 直线插值版）：对冲场景双双到达（不死锁）、静止单位
挡路跟随者等待→超时通过、自动交战与让路共存（让路期间火力不断）。

几何约定：dt=0.05 → 步长 4.8px；row 10 tile 中心 y=336.0；
tile(k) 中心 x = 32k+16，tile(k) 西边界 x = 32k。
"""

from __future__ import annotations

from pymc2.domain.entities.mech import Faction, Mech, Weapon
from pymc2.services.battle_service import BattleService
from pymc2.services.order_dispatcher import OrderDispatcher

DT = 0.05


class StaticRng:
    """固定随机源：random() 恒返回 value（0.0 必命中，1.0 必未命中）。"""

    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


def test_head_on_swap_both_arrive_without_deadlock() -> None:
    """对冲：双方互以对方 tile 为目标 → 双双让路 → 超时阀放行 → 互换到位（不永久卡）。"""
    service = BattleService(rng=StaticRng(0.0))
    a = Mech("px-01", Faction.PLAYER, 176.0, 336.0)
    b = Mech("ex-01", Faction.ENEMY, 208.0, 336.0)
    service.add_mech(a)
    service.add_mech(b)
    a.move_to(208.0, 336.0)
    b.move_to(176.0, 336.0)

    for _ in range(400):
        service.tick(DT)
        if a.move_target is None and b.move_target is None:
            break

    assert a.position == (208.0, 336.0)  # 互换到位
    assert b.position == (176.0, 336.0)
    assert service._yield_ticks == {}


def test_static_blocker_follower_waits_then_times_out_through() -> None:
    """跟随者被静止单位挡路：边界外冻结等待（计数累积）→ 超时强制通行 → 到达。"""
    service = BattleService(rng=StaticRng(0.0))
    static = Mech("ex-01", Faction.ENEMY, 336.0, 336.0)  # tile(10,10) 中心
    follower = Mech("px-01", Faction.PLAYER, 240.0, 336.0)  # tile(7,10)
    service.add_mech(static)
    service.add_mech(follower)
    assert service.move_mech("px-01", 432.0, 336.0) is True  # 目标 tile(13,10)，途经 tile(10,10)

    # 等待阶段：推进至 tile(10,10) 西边界外冻结，位置持续不变、指令保持
    for _ in range(40):
        service.tick(DT)
    frozen = follower.position
    assert follower.tile == (9, 10)  # 冻结在被占 tile 的西侧邻格
    assert follower.move_target == (432.0, 336.0)
    assert service._yield_ticks["px-01"] > 0

    for _ in range(20):
        service.tick(DT)
    assert follower.position == frozen  # 持续让路（等待语义，非穿人）

    # 超时逃生阀：连续让路 > 90 帧 → 强制通行 → 穿过被占 tile 到达目标
    for _ in range(300):
        service.tick(DT)
        if follower.move_target is None:
            break

    assert follower.position == (432.0, 336.0)
    assert static.position == (336.0, 336.0)  # 静止单位全程未动
    assert "px-01" not in service._yield_ticks


def test_auto_engage_keeps_firing_while_yielding() -> None:
    """自动交战与让路共存：移动者被静态敌机甲挡在射程外等待时仍自动开火掉血。"""
    service = BattleService(rng=StaticRng(0.0))
    dispatcher = OrderDispatcher()
    service.dispatcher = dispatcher
    # 双方 hull=400：恒命中 CT 直击下 100 帧内都不会被击毁（否则死亡方退出占位协商，断言失真）
    mover = Mech("px-01", Faction.PLAYER, 240.0, 336.0, hull=400, weapon=Weapon("ppc", 25, 420.0, 2.0))
    enemy = Mech("ex-01", Faction.ENEMY, 400.0, 336.0, hull=400, weapon=Weapon("mlaser", 15, 360.0, 2.5))
    service.add_mech(mover)
    service.add_mech(enemy)
    assert service.move_mech("px-01", 560.0, 336.0) is True  # 目标 tile(17,10)，途经敌方 tile(12,10)

    for _ in range(100):
        service.tick(DT)

    # 让路生效：冻结在敌方 tile 西侧邻格、指令未丢（未强制穿人）
    assert mover.tile == (11, 10)
    assert mover.move_target == (560.0, 336.0)
    assert service._yield_ticks["px-01"] > 0
    # 让路期间火力不断：射程内自动交战持续开火并命中（敌方掉血）
    assert enemy.hull < enemy.max_hull
    assert enemy.alive
    assert mover.alive
