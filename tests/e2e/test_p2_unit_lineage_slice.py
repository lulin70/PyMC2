"""P2 单位谱系 e2e：m2b 任务脚本实战切片（载具 + 炮塔战局）+ 无资产回退。

切片一（脚本驱动，docs/P2_DESIGN_20261121.md §6/§7）：data/missions/m2b_unit_lineage.json
实战——脚本空投敌方载具（kind=vehicle, ObjectTypeNum 3 语义）与炮塔（kind=turret, 21）→
敌方 AiCommander 指挥载具推进接敌 → 玩家推进进炮塔射程 → 炮塔守卫开火 → 集火击毁全部
敌方 → victory 结算 + salvage 口径（载具/炮塔整体 HP 击毁 → 0 RP）。截图 4 张存
docs/assets/uat_p2/。

切片二（无资产回退）：不注入资产包 → 载具/炮塔程序化几何回退（载具方块/炮塔圆形基座，
阵营色像素断言），输出与未加载资产状态一致。

SequenceRng：random() 交替 0.0/0.5——开火判定恒命中（0.0 < 任意正命中率）、部位判定恒
非 CT（0.5 落在权重表后段）——隔离「确定性 CT 集火速杀」对切片节奏的干扰（部位命中
位置随机化不影响命中/伤害断言口径）。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pygame
import pytest

import pymc2.main as main_module
from pymc2.domain.entities.mech import Faction, Mech, Weapon
from pymc2.domain.entities.turret import Turret
from pymc2.domain.entities.vehicle import Vehicle
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType, TacticalOrder
from pymc2.domain.scripting.mission_script import ScriptContext
from pymc2.presentation.renderer import WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX
from pymc2.services.ai_commander import AiCommander
from pymc2.services.battle_service import BattleService
from pymc2.services.campaign_state import CampaignState
from pymc2.services.salvage_service import SalvageService

SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "assets" / "uat_p2"
SCREEN_SIZE = (WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX)
MISSION_SCRIPT = Path(__file__).resolve().parents[2] / "data" / "missions" / "m2b_unit_lineage.json"

PLAYER_COLOR = (70, 130, 230)
ENEMY_COLOR = (210, 70, 60)


class SequenceRng:
    """交替随机源：开火判定用 0.0（恒命中）、部位判定用 0.5（恒非 CT）。"""

    def __init__(self) -> None:
        self._values = (0.0, 0.5)
        self._index = 0

    def _next(self) -> float:
        value = self._values[self._index % len(self._values)]
        self._index += 1
        return value

    def random(self) -> float:
        return self._next()

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


def save_screenshot(screen: pygame.Surface, name: str) -> Path:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / name
    pygame.image.save(screen, str(path))
    assert path.stat().st_size > 0
    return path


def _tile_center(tile_x: int, tile_y: int) -> tuple[float, float]:
    return (tile_x * 32 + 16.0, tile_y * 32 + 16.0)


def _move_unit(game: main_module.Game, unit_id: str, tile: tuple[int, int], max_frames: int = 400) -> None:
    """下发 MOVE_TO_POINT 并逐帧等待到达（端点直判）。"""
    order = TacticalOrder(
        order_type=OrderType.MOVE_TO_POINT,
        unit_ids=(unit_id,),
        origin=OrderOrigin.PLAYER,
        target_tile=tile,
        target_unit_id=None,
    )
    assert game.dispatcher.dispatch(order, game.service)
    unit = game.service.get(unit_id)
    assert unit is not None
    for _ in range(max_frames):
        main_module.run_frame(game, 0.05)
        if unit.move_target is None:
            break
    assert unit.move_target is None
    assert unit.position == _tile_center(*tile)


@pytest.fixture()
def script_game() -> Iterator[tuple[main_module.Game, ScriptContext]]:
    """m2b 实战切片战局：双玩家机甲 + 敌方指挥官 + 任务脚本挂载（dispatcher 由 create_game 接线）。"""
    pygame.init()
    try:
        screen = pygame.display.set_mode(SCREEN_SIZE)
        service = BattleService(rng=SequenceRng())
        px1, py1 = _tile_center(6, 10)
        px2, py2 = _tile_center(8, 10)
        service.add_mech(Mech("px-01", Faction.PLAYER, px1, py1, weapon=Weapon("ppc", 25, 420.0, 2.0)))
        service.add_mech(Mech("px-02", Faction.PLAYER, px2, py2, weapon=Weapon("mlaser", 15, 380.0, 1.5)))
        game = main_module.create_game(screen, service=service, commander=AiCommander(Faction.ENEMY))
        context = ScriptContext(battle=service)
        engine = main_module.ScriptEngine(main_module.load_mission_script(MISSION_SCRIPT), context)
        engine.attach(service)
        yield game, context
    finally:
        pygame.quit()


def test_m2b_script_battle_slice(script_game: tuple[main_module.Game, ScriptContext]) -> None:
    game, context = script_game
    service = game.service
    saved: list[Path] = []

    # --- 步骤 1：脚本空投（tick 30）——载具/炮塔入局（kind 分派类型断言）---
    for _ in range(40):
        main_module.run_frame(game, 0.05)
        if "ev-01" in service.mechs and "et-01" in service.mechs:
            break
    vehicle = service.get("ev-01")
    turret = service.get("et-01")
    assert isinstance(vehicle, Vehicle) and vehicle.max_hull == 80
    assert isinstance(turret, Turret) and turret.max_hull == 60
    assert vehicle.mobile is True and turret.mobile is False
    assert any("ObjectTypeNum" in message for message in context.messages)  # 脚本战报
    main_module.run_frame(game, 1 / 60)
    saved.append(save_screenshot(game.screen, "01_script_spawn_units.png"))

    # --- 步骤 2：敌方指挥官令载具推进接敌（x 递减）---
    spawn_x = vehicle.x
    advanced = False
    for _ in range(120):
        main_module.run_frame(game, 0.05)
        if vehicle.x < spawn_x:
            advanced = True
            break
    assert advanced, "敌方指挥官应下令载具接近（载具可移动语义）"
    assert turret.x == _tile_center(28, 8)[0]  # 炮塔固守原地
    main_module.run_frame(game, 1 / 60)
    saved.append(save_screenshot(game.screen, "02_vehicle_advance.png"))

    # --- 步骤 3：玩家推进进炮塔射程——途中炮塔守卫开火（自动交战泛化路径）---
    px1 = service.get("px-01")
    assert px1 is not None
    move_order = TacticalOrder(
        order_type=OrderType.MOVE_TO_POINT,
        unit_ids=("px-01",),
        origin=OrderOrigin.PLAYER,
        target_tile=(22, 10),
        target_unit_id=None,
    )
    assert game.dispatcher.dispatch(move_order, service)
    turret_fired = False
    for _ in range(400):
        main_module.run_frame(game, 0.05)
        if any(p.attacker_id == "et-01" for p in service.projectiles):
            turret_fired = True  # 进入射程瞬间炮塔自动开火（推进途中即触发）
            break
        if px1.move_target is None:
            break
    assert turret_fired, "玩家进入炮塔射程后应自动开火（守卫语义）"
    main_module.run_frame(game, 1 / 60)  # 弹丸飞行帧留影
    saved.append(save_screenshot(game.screen, "03_turret_guard_fire.png"))
    for _ in range(400):  # 走完剩余路程（守卫开火观察窗打断处续走）
        main_module.run_frame(game, 0.05)
        if px1.move_target is None:
            break
    assert px1.move_target is None
    _move_unit(game, "px-02", (23, 11))

    # --- 步骤 4：集火击毁全部敌方 → victory 结算 + salvage 口径 ---
    for _ in range(2400):
        main_module.run_frame(game, 0.05)
        if context.outcome is not None:
            break
    assert context.outcome == "victory"
    assert vehicle.alive is False and turret.alive is False
    assert vehicle.hull == 0.0 and turret.hull == 0.0  # 整体 HP 击毁即全损
    assert context.granted_rp == 15  # 脚本 first_blood 入账
    state = CampaignState("p2-slice")
    assert SalvageService().collect(state, service) == 0  # 载具/炮塔残骸无折算（偏离 §8.3）
    assert state.requisition_points == 0
    px1 = service.get("px-01")
    px2 = service.get("px-02")
    assert px1 is not None and px1.alive and px2 is not None and px2.alive  # 玩家幸存
    main_module.run_frame(game, 1 / 60)
    game.renderer.draw_settlement_overlay("MISSION ACCOMPLISHED", ("M2B UNIT LINEAGE", "PRESS ENTER TO CONTINUE"))
    saved.append(save_screenshot(game.screen, "04_kill_salvage_settlement.png"))

    # --- 验收留痕：4 张截图全部落盘 ---
    assert len(saved) == 4
    assert all(p.exists() and p.stat().st_size > 0 for p in saved)


def test_no_assets_programmatic_fallback() -> None:
    """无资产包路径：载具/炮塔程序化几何回退（炮塔圆形基座/载具方块，阵营色像素断言）。"""
    pygame.init()
    try:
        screen = pygame.display.set_mode(SCREEN_SIZE)
        service = BattleService(rng=SequenceRng())
        mx, my = _tile_center(10, 10)
        vx, vy = _tile_center(14, 10)
        tx, ty = _tile_center(18, 12)
        service.add_mech(Mech("px-01", Faction.PLAYER, mx, my, weapon=Weapon("ppc", 25, 420.0, 2.0)))
        service.add_mech(Vehicle("ev-01", Faction.ENEMY, vx, vy, hp=80))
        service.add_mech(Turret("et-01", Faction.ENEMY, tx, ty, hp=60))
        game = main_module.create_game(screen, service=service)  # asset_pack=None：程序化绘制
        vehicle = service.get("ev-01")
        turret = service.get("et-01")
        assert vehicle is not None and turret is not None
        assert game.renderer._unit_sprite(vehicle) is None  # 无包：回退程序化
        assert game.renderer._unit_sprite(turret) is None
        main_module.run_frame(game, 1 / 60)
        assert screen.get_at((int(vx), int(vy)))[:3] == ENEMY_COLOR  # 载具方块=阵营色
        assert screen.get_at((int(tx), int(ty)))[:3] == ENEMY_COLOR  # 炮塔圆形基座=阵营色
        assert screen.get_at((int(mx), int(my)))[:3] == PLAYER_COLOR  # 机甲方块=阵营色（存量不变）
        save_screenshot(screen, "05_no_assets_fallback.png")
    finally:
        pygame.quit()
