"""让路协商单元测试（MC2-05 直线插值版）：占位快照、静态阻挡、互换对冲、超时阀、
穿越语义、计数清零生命周期与渲染 y 排序 tie-break。

几何约定：dt=0.05 → 步长 96×0.05=4.8px；row 10 tile 中心 y=336.0；
tile 边界为 32 的整数倍（tile(5,10) 中心 x=176.0）。
"""

from __future__ import annotations

import pygame
import pytest

from pymc2.domain.entities.mech import Faction, Mech
from pymc2.presentation.renderer import Renderer
from pymc2.services.battle_service import YIELD_TIMEOUT_TICKS, BattleService

DT = 0.05  # 每帧步长 4.8px


def _mech(mech_id: str, faction: Faction, x: float, y: float, *, alive: bool = True) -> Mech:
    mech = Mech(mech_id, faction, x, y)
    if not alive:
        mech.take_damage(10_000)
    return mech


def _blocked_pair(service: BattleService, *, yield_ticks: int = 0) -> tuple[Mech, Mech]:
    """构造「移动者即将进入 tile(6,10)、静态占据者站 tile(6,10) 中心」的最小对局。"""
    mover = _mech("m", Faction.PLAYER, 188.0, 336.0)  # tile(5,10)，下帧进入 tile(6,10)
    static = _mech("s", Faction.ENEMY, 208.0, 336.0)  # tile(6,10) 中心
    service.add_mech(mover)
    service.add_mech(static)
    mover.move_to(208.0, 336.0)
    if yield_ticks:
        service._yield_ticks["m"] = yield_ticks
    return mover, static


class TestOccupancySnapshot:
    """占位快照构建：静态/移动/阵亡分流与运动快照字段。"""

    def test_static_movers_and_dead_partition(self) -> None:
        service = BattleService()
        service.add_mech(_mech("s", Faction.PLAYER, 336.0, 336.0))  # tile(10,10)
        service.add_mech(_mech("d", Faction.ENEMY, 368.0, 336.0, alive=False))  # tile(11,10)
        mover = _mech("m", Faction.PLAYER, 188.0, 336.0)
        service.add_mech(mover)
        mover.move_to(208.0, 336.0)

        snapshot = service._occupancy_snapshot(DT)

        assert snapshot.static_tiles == {(10, 10): "s"}  # 阵亡不入表
        frame = snapshot.movers["m"]
        assert frame.current_tile == (5, 10)
        assert frame.next_tile == (6, 10)  # 188+4.8=192.8 → tile 6
        assert frame.target_tile == (6, 10)

    def test_near_arrival_mover_predicts_target_tile(self) -> None:
        """dist ≤ step（吸附分支）→ 本帧将进入 tile == 目标 tile。"""
        service = BattleService()
        mover = _mech("m", Faction.PLAYER, 205.0, 336.0)
        service.add_mech(mover)
        mover.move_to(208.0, 336.0)

        frame = service._occupancy_snapshot(DT).movers["m"]

        assert frame.next_tile == (6, 10) == frame.target_tile


class TestStaticBlockYield:
    """静态阻挡：yield 冻结移动、计数递增、非移动系统照常、仅 tile 进入瞬间生效。"""

    def test_yield_freezes_movement_and_counts(self) -> None:
        service = BattleService()
        mover, static = _blocked_pair(service)

        service.tick(DT)

        assert mover.position == (188.0, 336.0)  # 冻结在 tile(5,10) 边界内
        assert mover.move_target == (208.0, 336.0)  # 指令保持（朝向语义：不丢目标）
        assert static.position == (208.0, 336.0)
        assert service._yield_ticks["m"] == 1

        service.tick(DT)

        assert mover.position == (188.0, 336.0)
        assert service._yield_ticks["m"] == 2

    def test_yield_keeps_weapon_and_heat_ticking(self) -> None:
        """让路帧仅冻结移动：武器冷却与散热照常推进。"""
        service = BattleService()
        mover, _static = _blocked_pair(service)
        mover.weapon.cd_remaining = 1.0
        mover.heat.add_heat(20.0)

        service.tick(DT)
        service.tick(DT)

        assert mover.weapon.cd_remaining == pytest.approx(1.0 - 2 * DT)
        assert mover.heat.heat == pytest.approx(20.0 - 2 * 0.5 * DT)

    def test_block_only_when_entering_new_tile(self) -> None:
        """tile 内移动不阻挡：自由推进至边界，进入被占 tile 的首帧才冻结。"""
        service = BattleService()
        mover = _mech("m", Faction.PLAYER, 176.0, 336.0)
        service.add_mech(mover)
        service.add_mech(_mech("s", Faction.ENEMY, 208.0, 336.0))
        mover.move_to(208.0, 336.0)

        service.tick(DT)
        service.tick(DT)
        service.tick(DT)

        assert mover.position == pytest.approx((190.4, 336.0))  # 三帧均自由（tile 5 内）
        assert "m" not in service._yield_ticks

        service.tick(DT)  # 第 4 帧将进入 tile(6,10) → 冻结

        assert mover.position == pytest.approx((190.4, 336.0))
        assert service._yield_ticks["m"] == 1


class TestHeadOnSwap:
    """互换对冲：互以对方当前 tile 为目标 → 双方 yield；目标错开则不阻。"""

    def test_swap_blocks_both_movers(self) -> None:
        service = BattleService()
        a = _mech("a", Faction.PLAYER, 176.0, 336.0)  # tile(5,10) → tile(6,10)
        b = _mech("b", Faction.ENEMY, 208.0, 336.0)  # tile(6,10) → tile(5,10)
        service.add_mech(a)
        service.add_mech(b)
        a.move_to(208.0, 336.0)
        b.move_to(176.0, 336.0)

        service.tick(DT)

        assert a.position == (176.0, 336.0)
        assert b.position == (208.0, 336.0)
        assert service._yield_ticks == {"a": 1, "b": 1}

    def test_non_mutual_targets_do_not_block(self) -> None:
        """A 指向 B 的 tile 但 B 另有去处 → 非互换，双方自由移动（穿越语义）。"""
        service = BattleService()
        a = _mech("a", Faction.PLAYER, 176.0, 336.0)  # → tile(6,10)（B 的 tile）
        b = _mech("b", Faction.ENEMY, 208.0, 336.0)  # → tile(7,10)（他处）
        service.add_mech(a)
        service.add_mech(b)
        a.move_to(208.0, 336.0)
        b.move_to(240.0, 336.0)

        service.tick(DT)

        assert a.position == pytest.approx((180.8, 336.0))
        assert b.position == pytest.approx((212.8, 336.0))
        assert service._yield_ticks == {}


class TestCrossingSemantics:
    """两个移动机甲非互换不互阻：对穿后各自到达（CC2 语义）。"""

    def test_two_movers_pass_through_and_arrive(self) -> None:
        service = BattleService()
        a = _mech("a", Faction.PLAYER, 188.0, 336.0)  # → tile(6,10)
        b = _mech("b", Faction.ENEMY, 240.0, 336.0)  # tile(7,10) → tile(5,10)，穿过 a 的路径
        service.add_mech(a)
        service.add_mech(b)
        a.move_to(208.0, 336.0)
        b.move_to(176.0, 336.0)

        for _ in range(60):
            service.tick(DT)

        assert a.position == (208.0, 336.0)
        assert b.position == (176.0, 336.0)
        assert service._yield_ticks == {}


class TestTimeoutValve:
    """超时逃生阀：边界 90 帧仍阻挡，>90 强制通行直至进入新 tile。"""

    def test_boundary_count_still_blocks(self) -> None:
        service = BattleService()
        mover, _static = _blocked_pair(service, yield_ticks=YIELD_TIMEOUT_TICKS)

        service.tick(DT)

        assert mover.position == (188.0, 336.0)  # 计数恰为 90 → 仍让路
        assert service._yield_ticks["m"] == YIELD_TIMEOUT_TICKS + 1

    def test_above_timeout_forces_passage_and_enters_tile(self) -> None:
        service = BattleService()
        mover, _static = _blocked_pair(service, yield_ticks=YIELD_TIMEOUT_TICKS + 1)

        service.tick(DT)

        assert mover.position == pytest.approx((192.8, 336.0))  # 强制通行
        assert mover.tile == (6, 10)  # 进入新 tile → 计数清零
        assert "m" not in service._yield_ticks

    def test_forced_passage_reaches_target_past_static(self) -> None:
        service = BattleService()
        mover, _static = _blocked_pair(service, yield_ticks=YIELD_TIMEOUT_TICKS + 1)

        for _ in range(10):
            service.tick(DT)
            if mover.move_target is None:
                break

        assert mover.move_target is None
        assert mover.position == (208.0, 336.0)


class TestYieldCounterLifecycle:
    """计数清零：进入新 tile / 到达 / 停止指令 / 阵亡不入阻。"""

    def test_counter_clears_when_blocker_moves_away(self) -> None:
        service = BattleService()
        mover, static = _blocked_pair(service)

        for _ in range(3):
            service.tick(DT)
        assert service._yield_ticks["m"] == 3

        static.move_to(400.0, 336.0)  # 占据者转为移动 → 不再是静态阻挡源
        service.tick(DT)

        assert mover.position == pytest.approx((192.8, 336.0))  # 恢复推进并进入 tile 6
        assert "m" not in service._yield_ticks  # 进入新 tile → 清零

    def test_counter_clears_on_stop_order(self) -> None:
        service = BattleService()
        mover, _static = _blocked_pair(service, yield_ticks=5)

        assert service.stop_mech("m") is True
        service.tick(DT)

        assert mover.move_target is None
        assert "m" not in service._yield_ticks  # 无移动意图 → 清零

    def test_dead_unit_never_blocks_and_passes_through(self) -> None:
        service = BattleService()
        mover, static = _blocked_pair(service)
        static.take_damage(10_000)

        for _ in range(10):
            service.tick(DT)
            if mover.move_target is None:
                break

        assert mover.move_target is None  # 阵亡者不入占位表 → 直达目标
        assert mover.position == (208.0, 336.0)
        assert "m" not in service._yield_ticks


class TestRendererYSort:
    """渲染 tie-break：机甲按 y 升序绘制（y 大者后画、覆盖在上）。"""

    def test_mechs_drawn_in_ascending_y_order(self) -> None:
        pygame.init()
        try:
            screen = pygame.Surface((320, 480))
            renderer = Renderer(screen)
            service = BattleService()
            service.add_mech(_mech("low", Faction.PLAYER, 100.0, 100.0))
            service.add_mech(_mech("high", Faction.ENEMY, 100.0, 300.0))
            service.add_mech(_mech("mid", Faction.PLAYER, 100.0, 200.0))

            drawn: list[str] = []

            def record(mech: Mech, cam_x: float, cam_y: float, *, selected: bool) -> None:
                drawn.append(mech.id)

            renderer._draw_mech = record
            renderer.render(service, 0.0, 0.0)

            assert drawn == ["low", "mid", "high"]  # y 升序：y 大者后画
        finally:
            pygame.quit()
