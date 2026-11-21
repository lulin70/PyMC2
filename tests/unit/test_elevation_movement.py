"""P1-A 移动代价与爬升限制单测：上坡慢 / 下坡不变 / 乘数表 / 爬升>1 拒绝 / 让路语义不变。"""

from __future__ import annotations

import pytest

from pymc2.domain.elevation import CLIMB_COST_PER_LEVEL, ElevationGrid
from pymc2.domain.entities.mech import MAP_TILES, MOVE_SPEED_PX_S, TILE_SIZE_PX, Faction, Mech
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType, TacticalOrder
from pymc2.services.battle_service import BattleService
from pymc2.services.order_dispatcher import OrderDispatcher

DT = 0.05
BASE_STEP = MOVE_SPEED_PX_S * DT  # 4.8px/帧（无机动惩罚）


def _grid(elev: dict[tuple[int, int], int]) -> ElevationGrid:
    rows = [[0] * MAP_TILES for _ in range(MAP_TILES)]
    for (x, y), value in elev.items():
        rows[y][x] = value
    return ElevationGrid(rows)


def _mech_at(tile: tuple[int, int], mech_id: str = "px-01") -> Mech:
    return Mech(
        mech_id,
        Faction.PLAYER,
        tile[0] * TILE_SIZE_PX + TILE_SIZE_PX / 2,
        tile[1] * TILE_SIZE_PX + TILE_SIZE_PX / 2,
    )


def _service(elev: dict[tuple[int, int], int] | None = None) -> BattleService:
    return BattleService(elevation=_grid(elev) if elev else None)


def _tile_center(tile: tuple[int, int]) -> tuple[float, float]:
    return (tile[0] * TILE_SIZE_PX + TILE_SIZE_PX / 2, tile[1] * TILE_SIZE_PX + TILE_SIZE_PX / 2)


class TestClimbMultiplier:
    """_climb_multiplier：耗时 ×1.5^Δ 以速度倒数表达（1/1.5^Δ）；下坡/平地/tile 内 ×1.0。"""

    def test_within_tile_no_penalty(self) -> None:
        service = _service({(7, 10): 3})
        mech = _mech_at((6, 10))
        # 贴 tile 中点起步，基础速度预测仍在本 tile → 不加成
        assert service._climb_multiplier(mech, _tile_center((7, 10)), DT) == 1.0

    def test_uphill_one_level(self) -> None:
        service = _service({(7, 10): 1})
        mech = _mech_at((6, 10))
        mech.x = 6 * TILE_SIZE_PX + TILE_SIZE_PX - 1.0  # 贴东缘：预测跨入 tile 7（Δ+1）
        assert service._climb_multiplier(mech, _tile_center((7, 10)), DT) == pytest.approx(1.0 / CLIMB_COST_PER_LEVEL)

    def test_uphill_two_levels_compound(self) -> None:
        service = _service({(7, 11): 2})
        mech = _mech_at((6, 11))
        mech.x = 6 * TILE_SIZE_PX + TILE_SIZE_PX - 1.0  # 预测跨入 tile 7（Δ+2）
        assert service._climb_multiplier(mech, _tile_center((7, 11)), DT) == pytest.approx(
            1.0 / CLIMB_COST_PER_LEVEL**2
        )

    def test_downhill_no_bonus(self) -> None:
        service = _service({(6, 10): 3, (7, 10): 0})
        mech = _mech_at((6, 10))
        mech.x = 7 * TILE_SIZE_PX - 1.0  # 贴东缘：预测跨入 tile 7（Δ-3）
        assert service._climb_multiplier(mech, _tile_center((7, 10)), DT) == 1.0

    def test_flat_grid_fast_path(self) -> None:
        service = _service()
        mech = _mech_at((6, 10))
        assert service._climb_multiplier(mech, _tile_center((39, 39)), DT) == 1.0


class TestMovementCost:
    def test_uphill_slower_than_flat(self) -> None:
        hills = _service({(7, 10): 1})
        flat = _service()
        hills_mech, flat_mech = _mech_at((6, 10)), _mech_at((6, 10))
        hills.add_mech(hills_mech)
        flat.add_mech(flat_mech)
        target = _tile_center((8, 10))
        assert hills.move_mech("px-01", *target)
        assert flat.move_mech("px-01", *target)
        for _ in range(10):  # 前 10 帧内含一次跨入 +1 tile 的减速帧
            hills.tick(DT)
            flat.tick(DT)
        assert hills_mech.x < flat_mech.x  # 上坡落后
        assert flat_mech.x == pytest.approx(208 + 10 * BASE_STEP)  # flat 逐帧存量公式不变
        while hills_mech.move_target is not None or flat_mech.move_target is not None:
            hills.tick(DT)
            flat.tick(DT)
        assert hills_mech.position == flat_mech.position == target  # 最终同点落位

    def test_downhill_identical_to_flat(self) -> None:
        down = _service({(6, 10): 3})
        flat = _service()
        down_mech, flat_mech = _mech_at((6, 10)), _mech_at((6, 10))
        down.add_mech(down_mech)
        flat.add_mech(flat_mech)
        target = _tile_center((7, 10))
        assert down.move_mech("px-01", *target)  # Δ-3 下坡放行
        assert flat.move_mech("px-01", *target)
        for _ in range(10):
            down.tick(DT)
            flat.tick(DT)
        assert down_mech.x == flat_mech.x  # 下坡每帧与 flat 完全一致（×1.0）
        assert down_mech.move_target is None and flat_mech.move_target is None


class TestClimbRejection:
    def test_move_to_two_levels_higher_rejected(self) -> None:
        service = _service({(8, 10): 3})
        service.add_mech(_mech_at((6, 10)))
        assert not service.move_mech("px-01", *_tile_center((8, 10)))
        mech = service.get("px-01")
        assert mech is not None
        assert mech.move_target is None

    def test_move_to_one_level_higher_allowed(self) -> None:
        service = _service({(7, 10): 1})
        service.add_mech(_mech_at((6, 10)))
        assert service.move_mech("px-01", *_tile_center((7, 10)))

    def test_flat_grid_never_rejects(self) -> None:
        service = _service()
        service.add_mech(_mech_at((6, 10)))
        assert service.move_mech("px-01", *_tile_center((39, 39)))

    def test_rejected_order_still_logged(self) -> None:
        """回执语义 = 现有 dispatcher 失败惯例：指令入 order_log、dispatch 返回 False。"""
        service = _service({(8, 10): 3})
        service.add_mech(_mech_at((6, 10)))
        dispatcher = OrderDispatcher()
        order = TacticalOrder(
            order_type=OrderType.MOVE_TO_POINT,
            unit_ids=("px-01",),
            origin=OrderOrigin.PLAYER,
            target_tile=(8, 10),
            target_unit_id=None,
        )
        assert not dispatcher.dispatch(order, service)
        assert dispatcher.order_log == (order,)
        mech = service.get("px-01")
        assert mech is not None
        assert mech.move_target is None


class TestYieldSemanticsUnchanged:
    def test_static_occupant_blocks_regardless_of_elevation(self) -> None:
        """高程不参与占位判定：丘陵网格下静态占据 tile 依旧硬阻挡（让路语义不变）。"""
        service = _service({(7, 10): 1})
        mover = _mech_at((6, 10), "mover")
        service.add_mech(mover)
        service.add_mech(_mech_at((7, 10), "blocker"))
        mover.x = 6 * TILE_SIZE_PX + TILE_SIZE_PX - 1.0  # 贴东缘：本帧即跨入被占据 tile
        assert service.move_mech("mover", *_tile_center((9, 10)))
        service.tick(DT)
        assert service._yield_ticks.get("mover") == 1  # 跨入被静态占据 tile → yield

    def test_swap_still_blocks_both(self) -> None:
        service = _service({(6, 10): 1, (7, 10): 1})
        service.add_mech(_mech_at((6, 10), "a"))
        service.add_mech(_mech_at((7, 10), "b"))
        assert service.move_mech("a", *_tile_center((7, 10)))
        assert service.move_mech("b", *_tile_center((6, 10)))
        service.tick(DT)
        assert service._yield_ticks == {"a": 1, "b": 1}  # 互换对冲照常双方 yield
