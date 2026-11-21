"""P1-B 视线遮挡 e2e：手写高程网格构造「射手-山脊-目标」真实战局切片 + flat 回归。

hills 切片（显式 rows 确定性构图，docs/P1B_DESIGN_20261121.md §5.7）：开局山脊隔断
（(12,9)/(12,10)/(12,11) 1 级）→ 自动交战不锁定不开火 → 两步爬升（0→1→2 级，端点
直判）至高点 (12,8) 后视线恢复 → 自动锁定开火命中（StaticRng(0.0) 全命中）→ 挪回
山脊背后击发抑制（锁定保持 + HUD NO LOS）。flat 回归切片：LOS 恒通 + 既有自动交战
断言不变（开局即互锁互射、命中扣血）。截图存 docs/assets/uat_p1b/。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pygame
import pytest

import pymc2.main as main_module
from pymc2.domain.elevation import ElevationGrid
from pymc2.domain.entities.mech import MAP_TILES, TILE_SIZE_PX, FireMode
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType, TacticalOrder
from pymc2.presentation.renderer import WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX
from pymc2.services.battle_service import create_m2_battle

SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "assets" / "uat_p1b"
SCREEN_SIZE = (WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX)

RIDGE_TILES = ((12, 9), (12, 10), (12, 11))  # 1 级山脊：隔断 row 10 地对地视线
HIGH_POINT_TILE = (12, 8)  # 2 级高点：俯射越过山脊北端
STEP_TILE = (11, 8)  # 1 级踏石：0→2 级两步爬升（端点 Δ≤1）
PLAYER_SPAWN_TILE = (6, 10)
PLAYER_BODY_COLOR = (70, 130, 230)


def _los_grid() -> ElevationGrid:
    """手写高程：山脊 3 tile 1 级 + 踏石 1 级 + 高点 2 级（其余全零）。"""
    rows = [[0] * MAP_TILES for _ in range(MAP_TILES)]
    for tile_x, tile_y in RIDGE_TILES:
        rows[tile_y][tile_x] = 1
    rows[STEP_TILE[1]][STEP_TILE[0]] = 1
    rows[HIGH_POINT_TILE[1]][HIGH_POINT_TILE[0]] = 2
    return ElevationGrid(rows)


class StaticRng:
    """固定随机源（0.0 → 全部开火命中）：战局确定性、伤害可精确断言。"""

    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


class _RecordingFont:
    """记录 render 文本的字体包装（断言 HUD NO LOS 提示）。"""

    def __init__(self, real: pygame.font.Font, texts: list[str]) -> None:
        self._real = real
        self._texts = texts

    def render(self, text: str, *args: object, **kwargs: object) -> pygame.Surface:
        self._texts.append(text)
        return self._real.render(text, *args, **kwargs)  # type: ignore[arg-type]


@pytest.fixture()
def los_game() -> Iterator[main_module.Game]:
    """dummy 驱动下的交互战局 + 手写 LOS 高程（main 默认接线复刻：service.elevation → Renderer）。

    敌方 HOLD（隔离单向火力）；StaticRng(0.0) 使 px-01 高点首射必命中。
    """
    pygame.init()
    try:
        screen = pygame.display.set_mode(SCREEN_SIZE)
        service = create_m2_battle(rng=StaticRng(0.0))
        service.elevation = _los_grid()
        assert service.set_fire_mode("ex-01", FireMode.HOLD)
        yield main_module.create_game(screen, service=service)
    finally:
        pygame.quit()


def save_screenshot(screen: pygame.Surface, name: str) -> Path:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / name
    pygame.image.save(screen, str(path))
    assert path.stat().st_size > 0
    return path


def _tile_center(tile: tuple[int, int]) -> tuple[float, float]:
    return (tile[0] * TILE_SIZE_PX + TILE_SIZE_PX / 2, tile[1] * TILE_SIZE_PX + TILE_SIZE_PX / 2)


def _move_to(game: main_module.Game, tile: tuple[int, int], unit_id: str = "px-01") -> None:
    """下发 MOVE_TO_POINT 并等待到达（端点直判爬升：0→1→2 两步）。"""
    order = TacticalOrder(
        order_type=OrderType.MOVE_TO_POINT,
        unit_ids=(unit_id,),
        origin=OrderOrigin.PLAYER,
        target_tile=tile,
        target_unit_id=None,
    )
    assert game.dispatcher.dispatch(order, game.service)
    mech = game.service.get(unit_id)
    assert mech is not None
    for _ in range(600):
        main_module.run_frame(game, 0.05)
        if mech.move_target is None:
            break
    assert mech.move_target is None
    assert mech.position == _tile_center(tile)


def _player_shots(game: main_module.Game) -> list[object]:
    return [p for p in game.service.projectiles if p.attacker_id == "px-01"]


def test_los_blocked_then_high_ground_engagement(los_game: main_module.Game) -> None:
    game = los_game
    service = game.service
    px = service.get("px-01")
    enemy = service.get("ex-01")
    assert px is not None and enemy is not None
    assert service.elevation.has_line_of_sight(px.tile, enemy.tile) is False  # 构图自检：山脊隔断
    saved: list[Path] = []

    # --- 步骤 1：开局山脊隔断——自动交战不锁定、不开火（双侧均被挡）---
    assert service.select_at(*_tile_center(PLAYER_SPAWN_TILE))  # 选中：HUD ELEV 段
    for _ in range(30):
        main_module.run_frame(game, 0.05)
    assert px.attack_target_id is None  # 视线被挡：不锁定
    assert enemy.attack_target_id is None  # 敌方 HOLD：本就不锁
    assert not service.projectiles
    main_module.run_frame(game, 1 / 60)
    saved.append(save_screenshot(game.screen, "01_blocked_no_fire.png"))

    # --- 步骤 2：两步爬升到高点——途中（踏石 tile）视线仍被挡，不开火---
    _move_to(game, STEP_TILE)
    assert service.elevation.elevation_at(px.tile) == 1
    assert px.attack_target_id is None
    assert not service.projectiles

    # --- 步骤 3：进入 2 级高点 tile——视线恢复，自动锁定并开火命中---
    _move_to(game, HIGH_POINT_TILE)
    assert service.elevation.elevation_at(px.tile) == 2
    assert service.elevation.has_line_of_sight(px.tile, enemy.tile) is True
    assert px.attack_target_id == enemy.id  # 通视即锁定
    assert len(_player_shots(game)) == 1  # 进入高点 tile 即开火（冷却零消耗前置就绪）
    if service.projectiles:
        main_module.run_frame(game, 1 / 60)  # 弹丸飞行帧留影
    saved.append(save_screenshot(game.screen, "02_high_ground_projectile.png"))
    for _ in range(120):
        main_module.run_frame(game, 0.05)
        if not service.projectiles:
            break
    assert not service.projectiles
    assert enemy.hull == 75  # StaticRng(0.0) 必命中：100 - 25
    main_module.run_frame(game, 1 / 60)
    saved.append(save_screenshot(game.screen, "03_hit_confirmed.png"))

    # --- 步骤 4：挪回踏石（山脊背后）——锁定保持、击发抑制、HUD NO LOS---
    hull_before = enemy.hull
    shots_before = len(_player_shots(game))
    _move_to(game, STEP_TILE)
    assert service.elevation.has_line_of_sight(px.tile, enemy.tile) is False
    assert px.attack_target_id == enemy.id  # 被挡不清锁（锁定保持语义）
    px.weapon.cd_remaining = 0.0  # 冷却清零：验证击发复验本身拦下再射
    texts: list[str] = []
    game.renderer._font = _RecordingFont(game.renderer._font, texts)  # type: ignore[assignment]
    for _ in range(10):
        main_module.run_frame(game, 0.05)
    assert len(_player_shots(game)) == shots_before  # 不新增弹丸
    assert enemy.hull == hull_before
    assert any("NO LOS" in t for t in texts)  # HUD 提示锁定目标视线被挡
    main_module.run_frame(game, 1 / 60)
    saved.append(save_screenshot(game.screen, "04_no_los_after_reblock.png"))

    # --- 验收留痕：4 张截图全部落盘 ---
    assert len(saved) == 4
    assert all(p.exists() and p.stat().st_size > 0 for p in saved)


def test_flat_los_always_clear_regression() -> None:
    """flat 回归切片：LOS 恒通 + 既有自动交战断言不变（开局互锁互射、弹丸正常飞行结算）。

    StaticRng(0.5)：384px/320px 距离命中率均 < 0.5 → 全员未命中（P1-A e2e 同款惯例），
    无人阵亡、无伤害——锁定/开火/弹道路径断言与伤害结算解耦（CT 透甲击杀属 M3 存量
    语义，非本切片回归面）。
    """
    pygame.init()
    try:
        screen = pygame.display.set_mode(SCREEN_SIZE)
        service = create_m2_battle(rng=StaticRng(0.5))
        game = main_module.create_game(screen, service=service)
        px = service.get("px-01")
        px2 = service.get("px-02")
        enemy = service.get("ex-01")
        assert px is not None and px2 is not None and enemy is not None
        assert service.elevation.is_flat()
        assert service.elevation.has_line_of_sight(px.tile, enemy.tile) is True  # flat 恒通
        for _ in range(20):  # 1.0s：开局三方互锁开火，弹丸全部落地（未命中散布）
            main_module.run_frame(game, 0.05)
        assert px.attack_target_id == enemy.id  # 存量自动交战锁定语义不变
        assert px2.attack_target_id == enemy.id
        assert enemy.attack_target_id == px2.id  # 敌 360 射程够 px-02（320px）不够 px-01（384px）
        assert not service.projectiles  # 弹丸已落地结算
        assert enemy.hull == 100 and px.hull == 100 and px2.hull == 100  # 全员未命中：零伤害
        main_module.run_frame(game, 1 / 60)
        save_screenshot(screen, "05_flat_regression.png")
    finally:
        pygame.quit()
