"""P3 内容驱动 e2e：合成 .pak 构建真实战局（内容驱动战局切片，docs/P3_DESIGN_20261121.md §4）。

fixture .pak（60 顶点图 + 西北角丘陵 + .fit 布置玩家双机甲/敌方载具 + pak 静态炮塔）：
terrace 高程渲染生效 → 敌方指挥官令载具推进 → 玩家推进进炮塔射程守卫开火 → 全歼
victory 结算。SequenceRng（交替 0.0/0.5）：开火恒命中、部位恒非 CT（部位随机不影响
载具/炮塔整体 HP 口径）。截图存 docs/assets/uat_p3/。真实官方 .pak 观感由用户真机
确认（--mission-pak 缓存路径）。

tile 约定：本文件 tile 元组 = (col, row)（与 tile_world(col, row) 同序）。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pygame
import pytest

import pymc2.main as main_module
from helpers.pak_fixture import (
    make_mission_fit_text,
    make_object_types_pak,
    make_objects_blob,
    make_pak,
    make_terrain_blob,
    tile_world,
)
from pymc2.domain.entities.mech import Faction
from pymc2.domain.entities.turret import Turret
from pymc2.domain.entities.vehicle import Vehicle
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType, TacticalOrder
from pymc2.presentation.renderer import WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX
from pymc2.services.ai_commander import AiCommander
from pymc2.services.mission_pak_builder import build_battle_from_mission_pak

SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "assets" / "uat_p3"
SCREEN_SIZE = (WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX)

PLAYER_COLOR = (70, 130, 230)
ENEMY_COLOR = (210, 70, 60)

Z = 4  # PacketStorageType.ZLIB int 值
MECH, VEHICLE, TURRET_CLASS = 2, 3, 21

# 战场布置（MC2 tile (col, row)，60 顶点图 → 59 tile；单位质心窗 origin (0, 7)）：
# 丘陵在 tile rows/cols 2..10（战区外 ≥10 tile），战区全平 → LOS 通、terrace 渲染生效。
# 载具初始在双方武器射程外（512px > 400）：AI 指挥官按 DESTROY_OBJECT 先接近再打。
PLAYER_TILES = ((20, 20), (22, 20))  # (col, row)
ENEMY_VEHICLE_TILE = (36, 20)
ENEMY_TURRET_TILE = (30, 21)
HILL_RANGE = range(2, 11)  # 丘陵 tile 区（rows/cols 2..10，窗口内、战区外）


def _hilly_elevations(side: int = 60) -> list[list[float]]:
    """基底 100m + 西北角圆丘（tile 2..10 渐升至 400m）：非平地形但战区高差为 0。"""
    rows = [[100.0 for _ in range(side)] for _ in range(side)]
    for r in HILL_RANGE:
        for c in HILL_RANGE:
            rows[r][c] = 100.0 + 300.0 * (1.0 - (max(r - 2, c - 2) / 8.0))
    return rows


class SequenceRng:
    """交替随机源：开火判定 0.0（恒命中）、部位判定 0.5（恒非 CT）。"""

    def __init__(self) -> None:
        self._values = (0.0, 0.5)
        self._index = 0

    def random(self) -> float:
        value = self._values[self._index % len(self._values)]
        self._index += 1
        return value

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


def _build_fixture_battle(tmp_path: Path):
    """合成任务目录（Missions/tut03.pak+.fit + Objects/object2.pak）→ pak 战局。"""
    missions = tmp_path / "Data" / "Missions"
    objects_dir = tmp_path / "Data" / "Objects"
    missions.mkdir(parents=True)
    objects_dir.mkdir(parents=True)
    (objects_dir / "object2.pak").write_bytes(
        make_object_types_pak([(10, (MECH, "Thor")), (11, (VEHICLE, "Centipede")), (12, (TURRET_CLASS, "SRMTurret"))])
    )
    parts = [
        (1, 10, *tile_world(*PLAYER_TILES[0]), 180.0, 0, True, True),  # 玩家机甲 1
        (2, 10, *tile_world(*PLAYER_TILES[1]), 180.0, 0, True, True),  # 玩家机甲 2
        (3, 11, *tile_world(*ENEMY_VEHICLE_TILE), 0.0, 1, False, True),  # 敌方载具
    ]
    pak_objects = [(12, *tile_world(*ENEMY_TURRET_TILE), 100.0, 90.0, 0, -1, -1)]  # 中立炮塔 → ENEMY 守卫
    (missions / "tut03.pak").write_bytes(
        make_pak([(Z, make_terrain_blob(_hilly_elevations())), (Z, make_objects_blob(pak_objects))])
    )
    (missions / "tut03.fit").write_text(make_mission_fit_text(parts), encoding="utf-8")
    return build_battle_from_mission_pak(
        missions / "tut03.pak", missions / "tut03.fit", objects_dir / "object2.pak", rng=SequenceRng()
    )


def save_screenshot(screen: pygame.Surface, name: str) -> Path:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / name
    pygame.image.save(screen, str(path))
    assert path.stat().st_size > 0
    return path


def _tile_px(tile: tuple[int, int], crop_origin: tuple[int, int] = (0, 0)) -> tuple[float, float]:
    """MC2 tile (col, row) → PyMC2 像素坐标（裁剪窗 origin 平移后取 tile 中心）。"""
    return ((tile[0] - crop_origin[1]) * 32 + 16.0, (tile[1] - crop_origin[0]) * 32 + 16.0)


@pytest.fixture()
def pak_game(tmp_path: Path) -> Iterator[tuple[main_module.Game, object]]:
    """合成 .pak 战局 + 敌方指挥官（dispatcher 由 create_game 接线）。"""
    pygame.init()
    try:
        screen = pygame.display.set_mode(SCREEN_SIZE)
        result = _build_fixture_battle(tmp_path)
        assert result.units_placed == 4  # 2 机甲 + 1 载具 + 1 炮塔
        assert result.skipped_units == ()
        assert not result.service.elevation.is_flat()  # 丘陵量化生效（terrace 渲染前提）
        assert result.crop_origin == (0, 0)  # 四台候选全在 tile 20..36×20..21（跨度 < 40）→ 首窗 (0,0)
        game = main_module.create_game(screen, service=result.service, commander=AiCommander(Faction.ENEMY))
        yield game, result
    finally:
        pygame.quit()


def test_p3_mission_pak_slice(pak_game: tuple[main_module.Game, object]) -> None:
    """切片：内容驱动战局 → terrace 渲染 → 载具推进 → 炮塔守卫开火 → 全歼 victory。"""
    game, result = pak_game
    service = game.service
    origin = result.crop_origin
    saved: list[Path] = []
    units = service.mechs
    player1 = units["thor-01"]
    player2 = units["thor-02"]
    vehicle = units["centipede-03"]
    turret = units["turret-00"]
    assert isinstance(vehicle, Vehicle) and vehicle.mobile
    assert isinstance(turret, Turret) and not turret.mobile
    assert player1.faction is Faction.PLAYER
    assert vehicle.faction is Faction.ENEMY and turret.faction is Faction.ENEMY  # 中立炮塔按 ENEMY 守卫

    # --- 步骤 1：内容驱动布置 + terrace 渲染（高程非平、阵营色实体像素断言）---
    main_module.run_frame(game, 1 / 60)
    assert service.elevation.elevation_at(player1.tile) == 0  # 战区全平（LOS/爬升不受扰）
    expected_px = _tile_px(PLAYER_TILES[0], origin)
    assert (int(player1.x), int(player1.y)) == (int(expected_px[0]), int(expected_px[1]))
    assert game.screen.get_at((int(player1.x), int(player1.y)))[:3] == PLAYER_COLOR
    assert game.screen.get_at((int(turret.x), int(turret.y)))[:3] == ENEMY_COLOR
    saved.append(save_screenshot(game.screen, "01_pak_battle_terrain.png"))

    # --- 步骤 2：敌方指挥官令载具推进接敌（x 递减；炮塔固守原地）---
    spawn_x = vehicle.x
    advanced = False
    for _ in range(120):
        main_module.run_frame(game, 0.05)
        if vehicle.x < spawn_x:
            advanced = True
            break
    assert advanced, "敌方指挥官应下令载具接近（载具可移动语义）"
    assert turret.x == _tile_px(ENEMY_TURRET_TILE, origin)[0]  # 炮塔固守
    main_module.run_frame(game, 1 / 60)
    saved.append(save_screenshot(game.screen, "02_enemy_vehicle_advance.png"))

    # --- 步骤 3：玩家推进进炮塔射程——守卫开火（自动交战泛化路径）---
    move_order = TacticalOrder(
        order_type=OrderType.MOVE_TO_POINT,
        unit_ids=("thor-02",),
        origin=OrderOrigin.PLAYER,
        target_tile=(26, 21),  # tile (col=26, row=21)：进炮塔射程（132px < 400）
        target_unit_id=None,
    )
    assert game.dispatcher.dispatch(move_order, service)
    turret_fired = False
    for _ in range(400):
        main_module.run_frame(game, 0.05)
        if any(p.attacker_id == "turret-00" for p in service.projectiles):
            turret_fired = True  # 进入炮塔射程瞬间守卫开火
            break
        if player2.move_target is None:
            break
    assert turret_fired, "玩家进入炮塔射程后应自动开火（守卫语义，内容驱动战局同 P2）"
    main_module.run_frame(game, 1 / 60)
    saved.append(save_screenshot(game.screen, "03_turret_guard_fire.png"))

    # --- 步骤 4：全歼敌方 → victory（无脚本：MissionChain 谓词直判）---
    for _ in range(2400):
        main_module.run_frame(game, 0.05)
        if main_module.MissionChain.is_victory(service):
            break
    assert main_module.MissionChain.is_victory(service)
    assert not vehicle.alive and not turret.alive
    assert player1.alive and player2.alive  # 双玩家机甲幸存（战力优势 + 恒命中随机源）
    main_module.run_frame(game, 1 / 60)
    game.renderer.draw_settlement_overlay("MISSION ACCOMPLISHED", ("TUT03 PAK BATTLE", "PRESS ENTER TO CONTINUE"))
    saved.append(save_screenshot(game.screen, "04_pak_victory_settlement.png"))

    # --- 验收留痕：4 张截图全部落盘 ---
    assert len(saved) == 4
    assert all(p.exists() and p.stat().st_size > 0 for p in saved)


def test_pak_battle_movement_on_quantized_grid(pak_game: tuple[main_module.Game, object]) -> None:
    """内容驱动战局的移动链路：玩家机甲在量化高程网格上正常行进（爬升规则 + 让路无回归）。"""
    game, _result = pak_game
    service = game.service
    player1 = service.get("thor-01")
    assert player1 is not None
    assert service.elevation.elevation_at((16, 16)) == 0  # 目标 tile 在战区（高程 0，爬升合法）
    order = TacticalOrder(
        order_type=OrderType.MOVE_TO_POINT,
        unit_ids=("thor-01",),
        origin=OrderOrigin.PLAYER,
        target_tile=(16, 16),
        target_unit_id=None,
    )
    assert game.dispatcher.dispatch(order, service)
    for _ in range(400):
        main_module.run_frame(game, 0.05)
        if player1.move_target is None:
            break
    assert player1.move_target is None
    assert player1.tile == (16, 16)
