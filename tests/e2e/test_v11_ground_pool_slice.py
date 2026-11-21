"""V11 地表族池 e2e：多族 fixture 实战验证草地/道路分族铺帧与旧缓存回退回归。

程序化 fixture（PIL 现场生成 JPG/TGA，真资产零入库）验证端到端管线：V11 族池
分类下草地 tile 用 grass 族（Forest 派生帧，绿主导）、道路 tile 底色用 dirt 族
（Desert 派生帧，沙棕红主导）+ 2lanedirt overlay 蒙版不变；旧单族缓存（仅 Detail
TGA）回退 detail 池 = V1 行为逐像素回归。真实资产观感由用户真机确认
（~/.pymc2/mc2-assets 自动发现），截图存 docs/assets/uat_v11/。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pygame
import pytest
from PIL import Image

import pymc2.main as main_module
from pymc2.domain.entities.mech import TILE_SIZE_PX, Faction
from pymc2.infrastructure.mc2_assets import MC2AssetPack
from pymc2.presentation.renderer import WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX
from pymc2.services.ai_commander import AiCommander
from pymc2.services.battle_service import BattleService, create_m2_battle

SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "assets" / "uat_v11"
SCREEN_SIZE = (WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX)
# fixture 色板：JPG 生物群系（通道关系断言抗 JPEG 有损）+ TGA overlay/树（精确断言）
FOREST_GREEN = (60, 120, 40)
DESERT_TAN = (170, 130, 70)
GRASS_COLOR = (90, 120, 70, 255)
ROAD_OPAQUE_COLOR = (170, 60, 50, 255)
TREE_COLOR = (30, 110, 40, 255)


class StaticRng:
    """固定随机源（恒命中、恒 CT），保证 e2e 结果确定可回归。"""

    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


def write_jpg(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), color).save(path)


def write_tga(path: Path, image: Image.Image) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def make_biome_pack(tmp_path: Path) -> MC2AssetPack:
    """多族 fixture pack：Forest/Desert JPG + 半幅蒙版道路 overlay + 树精灵。"""
    root = tmp_path / "biome"
    biome = root / "Data" / "Textures" / "Random_Maps"
    write_jpg(biome / "Random_Forest.jpg", FOREST_GREEN)
    write_jpg(biome / "Random_Desert.jpg", DESERT_TAN)
    overlay = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    for x in range(16):
        for y in range(32):
            overlay.putpixel((x, y), ROAD_OPAQUE_COLOR)
    write_tga(root / "Data" / "Textures" / "64Overlays" / "2lanedirt0000.TGA", overlay)
    write_tga(root / "Data" / "TGL" / "128" / "A_Pine&Palm.tga", Image.new("RGBA", (32, 32), TREE_COLOR))
    pack = MC2AssetPack.load(root)
    assert pack is not None
    return pack


def make_legacy_pack(tmp_path: Path) -> MC2AssetPack:
    """旧单族缓存 fixture：仅 Detail TGA + overlay（V1 缓存形态，回退回归用）。"""
    root = tmp_path / "legacy"
    write_tga(
        root / "Data" / "Textures" / "64" / "Detail_10000.tga",
        Image.new("RGBA", (32, 32), GRASS_COLOR),
    )
    overlay = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    for x in range(16):
        for y in range(32):
            overlay.putpixel((x, y), ROAD_OPAQUE_COLOR)
    write_tga(root / "Data" / "Textures" / "64Overlays" / "2lanedirt0000.TGA", overlay)
    pack = MC2AssetPack.load(root)
    assert pack is not None
    return pack


def tile_center(tile: tuple[int, int]) -> tuple[float, float]:
    return (tile[0] * TILE_SIZE_PX + TILE_SIZE_PX / 2, tile[1] * TILE_SIZE_PX + TILE_SIZE_PX / 2)


def save_screenshot(screen: pygame.Surface, name: str) -> Path:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / name
    pygame.image.save(screen, str(path))
    assert path.stat().st_size > 0
    return path


def make_game(tmp_path: Path, pack: MC2AssetPack) -> main_module.Game:
    """dummy 驱动下的交互模式战局 + 指定资产 pack（main 默认接线最小复刻）。"""
    pygame.init()
    screen = pygame.display.set_mode(SCREEN_SIZE)
    service: BattleService = create_m2_battle(rng=StaticRng(0.0))
    commander = AiCommander(Faction.ENEMY, decision_interval_ticks=1)
    return main_module.create_game(screen, service=service, commander=commander, asset_pack=pack)


def quiet_grass_tile(game: main_module.Game) -> tuple[int, int]:
    """非道路、无装饰、无机甲的视口内草地 tile（像素断言采样点）。"""
    deco_tiles = {(tile_x, tile_y) for tile_x, tile_y, _kind in game.renderer._decorations}
    spawn_tiles = {mech.tile for mech in game.service.mechs.values()}
    return next(
        (x, y) for y in range(22) for x in range(40) if y != 10 and x != 25 and (x, y) not in deco_tiles | spawn_tiles
    )


@pytest.fixture()
def biome_game(tmp_path: Path) -> Iterator[main_module.Game]:
    game = make_game(tmp_path, make_biome_pack(tmp_path))
    yield game
    pygame.quit()


@pytest.fixture()
def legacy_game(tmp_path: Path) -> Iterator[main_module.Game]:
    game = make_game(tmp_path, make_legacy_pack(tmp_path))
    yield game
    pygame.quit()


def test_biome_terrain_grass_and_dirt_road(biome_game: main_module.Game) -> None:
    """多族缓存实战：草地 tile 绿主导（grass 族）、道路蒙版内 overlay 色、
    蒙版外沙棕主导（dirt 族泥径底）；截图留痕 docs/assets/uat_v11/。"""
    game = biome_game
    main_module.run_frame(game, 1 / 60)
    gx, gy = tile_center(quiet_grass_tile(game))
    grass_pixel = game.screen.get_at((int(gx), int(gy)))[:3]
    assert grass_pixel[1] > grass_pixel[0], f"草地 tile 应绿主导（forest 族）：{grass_pixel}"
    # 道路 tile(30,10)：蒙版内 = overlay 色；蒙版外 = dirt 泥径底（红主导）
    road_mask = game.screen.get_at((30 * TILE_SIZE_PX + 4, 10 * TILE_SIZE_PX + 16))[:3]
    assert road_mask == ROAD_OPAQUE_COLOR[:3]
    road_base = game.screen.get_at((30 * TILE_SIZE_PX + 24, 10 * TILE_SIZE_PX + 16))[:3]
    assert road_base[0] > road_base[1], f"道路蒙版外应沙棕主导（desert 族）：{road_base}"
    assert save_screenshot(game.screen, "01_biome_grass_terrain.png").exists()
    # 推进数帧后地形仍稳定（确定性选帧不受时间影响）
    for _ in range(30):
        main_module.run_frame(game, 1 / 60)
    assert game.screen.get_at((int(gx), int(gy)))[:3] == grass_pixel
    assert save_screenshot(game.screen, "02_biome_road_dirt_base.png").exists()


def test_legacy_single_family_cache_regression(legacy_game: main_module.Game) -> None:
    """旧单族缓存（仅 Detail TGA）回退回归：grass/dirt 池同落 detail 族——草地与
    道路蒙版外均为 Detail 色（与 V1 逐像素一致），蒙版内 overlay 色不变。"""
    game = legacy_game
    main_module.run_frame(game, 1 / 60)
    gx, gy = tile_center(quiet_grass_tile(game))
    assert game.screen.get_at((int(gx), int(gy)))[:3] == GRASS_COLOR[:3]
    assert game.screen.get_at((30 * TILE_SIZE_PX + 24, 10 * TILE_SIZE_PX + 16))[:3] == GRASS_COLOR[:3]
    assert game.screen.get_at((30 * TILE_SIZE_PX + 4, 10 * TILE_SIZE_PX + 16))[:3] == ROAD_OPAQUE_COLOR[:3]
    assert save_screenshot(game.screen, "03_legacy_cache_regression.png").exists()
