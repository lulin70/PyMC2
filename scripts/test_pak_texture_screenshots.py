"""W1 E2E：pak 纹理映射开关对比截图生成器（无头模式）。

用法：
    .venv/bin/python scripts/test_pak_texture_screenshots.py
产出：
    docs/assets/uat_w1/pak_texture_baseline.png  （mission_terrain=None，V1.1 程序化池）
    docs/assets/uat_w1/pak_texture_enabled.png   （mc2_01.pak 纹理查表优先）
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from pymc2.infrastructure.mc2_assets import MC2AssetPack  # noqa: E402
from pymc2.infrastructure.mission_pak import load_mission_pak  # noqa: E402
from pymc2.presentation.renderer import Renderer  # noqa: E402
from pymc2.services.battle_service import BattleService  # noqa: E402

ASSETS_ROOT = Path.home() / ".pymc2" / "mc2-assets"
PAK_PATH = ASSETS_ROOT / "Data" / "missions" / "mc2_01.pak"
OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "assets" / "uat_w1"


def main() -> None:
    pygame.init()
    screen = pygame.display.set_mode((1280, 720))
    battle = BattleService()
    assets = MC2AssetPack.load(ASSETS_ROOT)
    print(f"assets loaded: {assets is not None}")

    mission_terrain = load_mission_pak(PAK_PATH) if PAK_PATH.is_file() else None
    print(f"mission_terrain loaded: {mission_terrain is not None}")

    # 基线：mission_terrain=None → 程序化 JPG 池（V1.1 行为）
    renderer_base = Renderer(screen, asset_pack=assets, elevation=battle.elevation)
    renderer_base.render(battle, 0.0, 0.0)
    pygame.display.flip()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_base = OUT_DIR / "pak_texture_baseline.png"
    pygame.image.save(screen, str(out_base))
    print(f"Screenshot saved: {out_base}")

    # 开启：mission_terrain 注入 → pak texture_id → TGA 查表优先
    renderer_pak = Renderer(
        screen,
        asset_pack=assets,
        elevation=battle.elevation,
        mission_terrain=mission_terrain,
    )
    renderer_pak.render(battle, 0.0, 0.0)
    pygame.display.flip()
    out_pak = OUT_DIR / "pak_texture_enabled.png"
    pygame.image.save(screen, str(out_pak))
    print(f"Screenshot saved: {out_pak}")

    pygame.quit()


if __name__ == "__main__":
    main()
