"""Wave V1 地形合成 e2e：带 fixture 资产包跑真实战局，验证 MC2 纹理地形管线。

程序化 fixture（PIL 现场生成，真资产零入库）验证端到端管线：create_game 接线
（纹理地形 + 确定性撒点装饰）→ run_frame 真实渲染 → 像素断言（草地=Detail 色、
道路蒙版内=overlay 色）→ 装饰不阻挡移动（机甲走到点缀 tile 中心落位）→ 自动
交战照常开火击毁。真实资产观感由用户真机确认（~/.pymc2/mc2-assets 自动发现）。
截图存 docs/assets/uat_v1/。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pygame
import pytest
from PIL import Image

import pymc2.main as main_module
from pymc2.domain.entities.mech import TILE_SIZE_PX, Faction
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType, TacticalOrder
from pymc2.infrastructure.mc2_assets import MC2AssetPack
from pymc2.presentation.renderer import WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX
from pymc2.services.ai_commander import AiCommander
from pymc2.services.battle_service import BattleService, create_m2_battle

SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "assets" / "uat_v1"
SCREEN_SIZE = (WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX)
# fixture 色板（与集成测试同风格：单色纹理让像素断言无需推算 seed）
GRASS_COLOR = (90, 120, 70, 255)
ROAD_OPAQUE_COLOR = (170, 60, 50, 255)
TREE_COLOR = (30, 110, 40, 255)
PLAYER_SPAWN_TILE = (6, 10)


class StaticRng:
    """固定随机源（恒命中、恒 CT），保证 e2e 结果确定可回归。"""

    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


def make_terrain_pack(tmp_path: Path) -> MC2AssetPack:
    """地形 fixture pack：单张 Detail + 半幅蒙版道路 overlay + 树精灵（32x32 = 格子尺寸）。"""
    root = tmp_path / "terrain"

    def write(path: Path, image: Image.Image) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)

    write(root / "Data" / "Textures" / "64" / "Detail_10000.tga", Image.new("RGBA", (32, 32), GRASS_COLOR))
    overlay = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    for x in range(16):
        for y in range(32):
            overlay.putpixel((x, y), ROAD_OPAQUE_COLOR)
    write(root / "Data" / "Textures" / "64Overlays" / "2lanedirt0000.TGA", overlay)
    write(root / "Data" / "TGL" / "128" / "A_Pine&Palm.tga", Image.new("RGBA", (32, 32), TREE_COLOR))
    pack = MC2AssetPack.load(root)
    assert pack is not None
    return pack


def tile_center(tile: tuple[int, int]) -> tuple[float, float]:
    return (tile[0] * TILE_SIZE_PX + TILE_SIZE_PX / 2, tile[1] * TILE_SIZE_PX + TILE_SIZE_PX / 2)


@pytest.fixture()
def game(tmp_path: Path) -> Iterator[main_module.Game]:
    """dummy 驱动下的交互模式战局 + 地形 fixture pack（main 默认接线最小复刻）。"""
    pygame.init()
    screen = pygame.display.set_mode(SCREEN_SIZE)
    service: BattleService = create_m2_battle(rng=StaticRng(0.0))
    commander = AiCommander(Faction.ENEMY, decision_interval_ticks=1)
    yield main_module.create_game(
        screen,
        service=service,
        commander=commander,
        asset_pack=make_terrain_pack(tmp_path),
    )
    pygame.quit()


def save_screenshot(screen: pygame.Surface, name: str) -> Path:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / name
    pygame.image.save(screen, str(path))
    assert path.stat().st_size > 0
    return path


def visible_decorations(game: main_module.Game) -> tuple[tuple[int, int, str], ...]:
    """开局相机 (0,0) 视口内（y<22）且不与机甲出生点重叠的装饰撒点。"""
    return tuple((tile_x, tile_y, kind) for tile_x, tile_y, kind in game.renderer._decorations if tile_y < 22)


def test_terrain_textured_battle_with_decorations(game: main_module.Game) -> None:
    px = game.service.get("px-01")
    enemy = game.service.get("ex-01")
    assert px is not None and enemy is not None
    saved: list[Path] = []

    # --- 步骤 0：开局帧——纹理地形 + 装饰 + 自动交战同帧生效 ---
    main_module.run_frame(game, 1 / 60)
    decos = visible_decorations(game)
    assert decos, "开局视口内应有装饰撒点（15 撒点落在 40x40 地图，视内为空概率 ~1e-6）"
    saved.append(save_screenshot(game.screen, "01_opening_textured_terrain.png"))

    # 草地 tile 铺 Detail 纹理：取一个非道路、无装饰、无机甲的视口内 tile 中心
    deco_tiles = {(tile_x, tile_y) for tile_x, tile_y, _kind in game.renderer._decorations}
    spawn_tiles = {mech.tile for mech in game.service.mechs.values()}
    grass_tile = next(
        (x, y) for y in range(22) for x in range(40) if y != 10 and x != 25 and (x, y) not in deco_tiles | spawn_tiles
    )
    gx, gy = tile_center(grass_tile)
    assert game.screen.get_at((int(gx), int(gy)))[:3] == GRASS_COLOR[:3]
    # 道路 tile：overlay 蒙版内 = overlay 色，蒙版外 = 草皮底色（tile(30,10) 远离出生点）
    assert game.screen.get_at((30 * TILE_SIZE_PX + 4, 10 * TILE_SIZE_PX + 16))[:3] == ROAD_OPAQUE_COLOR[:3]
    assert game.screen.get_at((30 * TILE_SIZE_PX + 24, 10 * TILE_SIZE_PX + 16))[:3] == GRASS_COLOR[:3]
    # 装饰：首个视口内撒点（kind 循环首项 = tree）blit 在 tile 中心
    deco_x, deco_y, deco_kind = decos[0]
    assert deco_kind == "tree"
    dx, dy = tile_center((deco_x, deco_y))
    assert game.screen.get_at((int(dx), int(dy)))[:3] == TREE_COLOR[:3]
    # 自动交战照常：开局帧 px-01 已锁定敌机甲（装饰不影响命中链路）
    assert px.attack_target_id == "ex-01"
    assert any(p.attacker_id == "px-01" for p in game.service.projectiles)

    # --- 步骤 1：机甲移动到装饰 tile 中心——纯装饰不阻挡移动（让路协商语义对齐）---
    nearest = min(decos, key=lambda d: (d[0] - PLAYER_SPAWN_TILE[0]) ** 2 + (d[1] - PLAYER_SPAWN_TILE[1]) ** 2)
    target_tile = (nearest[0], nearest[1])
    game.dispatcher.dispatch(
        TacticalOrder(
            order_type=OrderType.MOVE_TO_POINT,
            unit_ids=("px-01",),
            origin=OrderOrigin.PLAYER,
            target_tile=target_tile,
            target_unit_id=None,
        ),
        game.service,
    )
    for _ in range(400):
        main_module.run_frame(game, 0.05)
        if px.move_target is None:
            break
    assert px.move_target is None
    assert px.position == tile_center(target_tile)  # 穿过/踏上装饰格正常落位
    saved.append(save_screenshot(game.screen, "02_move_through_decoration.png"))

    # --- 步骤 2：自动交战击毁——装饰不参与命中，战斗链路与 M2 语义一致 ---
    for _ in range(40):
        main_module.run_frame(game, 0.05)
        if not enemy.alive:
            break
    assert not enemy.alive  # 恒命中恒 CT：t≈0.82s CT 摧毁（装饰不拦截弹丸）
    saved.append(save_screenshot(game.screen, "03_auto_engagement_kill.png"))

    # --- 验收留痕：3 张截图全部落盘 ---
    assert len(saved) == 3
    assert all(p.exists() and p.stat().st_size > 0 for p in saved)
