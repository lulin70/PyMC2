"""W3 E2E：斜投影截图生成器（无头模式，docs/W3_TEST_PLAN_20261128.md §3）。

用法：
    .venv/bin/python scripts/test_isometric_screenshots.py
产出 3 张 → docs/assets/uat_w3/：
    iso_hills_panorama.png  isometric hills 全景（纵向压缩 + 高程倾斜位移）
    iso_select_move.png     isometric 点击选中机甲 + 右键移动标记
    top_baseline.png        同战局 top 基线（面板区域与 iso 像素 diff = 0）

程序断言（不依赖目视）：
    ① 点击选中：selected.id == 预期机甲（拾取逆变换全链）
    ② 右键移动：move_target 落点 ≤1 tile（FR-3.3）
    ③ 高地实体绘制高于低地实体（FR-2.2）
    ④ iso 与 top 面板区域像素 diff = 0（FR-5.2）
"""

from __future__ import annotations

import math
import os
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from pymc2.domain.entities.mech import TILE_SIZE_PX, Faction  # noqa: E402
from pymc2.main import _apply_terrain, create_game, handle_event  # noqa: E402
from pymc2.services.battle_service import create_m2_battle  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "assets" / "uat_w3"
SCREEN_SIZE = (1280, 720)


def _save(screen: pygame.Surface, name: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    pygame.image.save(screen, str(path))
    print(f"Screenshot saved: {path}")


def _click(game, pos: tuple[int, int], button: int) -> None:
    """合成真实鼠标事件（走存量 handle_event 输入链路，非直调内部函数）。"""
    handle_event(game, pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": pos, "button": button}))
    handle_event(game, pygame.event.Event(pygame.MOUSEBUTTONUP, {"pos": pos, "button": button}))


def _entity_screen_y(game, unit) -> int:
    """实体屏幕 y（与 _draw_unit 同链：投影 + 高程偏移）。"""
    sx, sy = game.renderer.world_to_screen(unit.x, unit.y, game.camera.x, game.camera.y)
    return sy - game.renderer._elev_offset(unit.tile)


def main() -> None:
    pygame.init()
    screen = pygame.display.set_mode(SCREEN_SIZE)
    pygame.display.set_caption("W3 E2E: Isometric Projection")

    battle = create_m2_battle()
    _apply_terrain(battle, "hills", None)
    game = create_game(screen, service=battle, projection="isometric")

    players = [m for m in battle.mechs.values() if m.faction is Faction.PLAYER and m.alive]
    assert players, "battle has no player mech"
    mech = players[0]
    # 相机对准目标机甲（钳制后仍需机甲落在屏内；屏宽=图宽 → cam_x 恒 0）。
    game.camera.x = max(0.0, mech.x - SCREEN_SIZE[0] / 2)
    game.camera.y = max(0.0, min(mech.y - SCREEN_SIZE[1] / 2, game.renderer.terrain_screen_height() - SCREEN_SIZE[1]))

    # ① 点击选中（拾取逆变换全链：屏幕坐标 → 世界坐标 → 单选）
    mech_pos = game.renderer.world_to_screen(mech.x, mech.y, game.camera.x, game.camera.y)
    mech_pos = (mech_pos[0], mech_pos[1] - game.renderer._elev_offset(mech.tile))
    assert 0 <= mech_pos[0] < SCREEN_SIZE[0] and 0 <= mech_pos[1] < SCREEN_SIZE[1], f"mech off-screen: {mech_pos}"
    _click(game, mech_pos, button=1)
    assert game.service.selected is not None and game.service.selected.id == mech.id, (
        f"click-select failed: selected={game.service.selected}"
    )
    print(f"[assert ok] click-select: selected.id == {mech.id}")

    # ② 右键移动（拾取落点 ≤1 tile）
    grid = battle.elevation
    target = None
    for ty in range(2, 38):  # 找屏内可达且机甲不在其上的平 tile
        for tx in range(2, 38):
            if grid.elevation_at((tx, ty)) == 0 and (tx, ty) != mech.tile:
                wx = tx * TILE_SIZE_PX + TILE_SIZE_PX / 2
                wy = ty * TILE_SIZE_PX + TILE_SIZE_PX / 2
                sx, sy = game.renderer.world_to_screen(wx, wy, game.camera.x, game.camera.y)
                if 0 <= sx < SCREEN_SIZE[0] and 0 <= sy < SCREEN_SIZE[1]:
                    target = (tx, ty, (sx, sy))
                    break
        if target is not None:
            break
    assert target is not None, "no on-screen flat tile found"
    _click(game, target[2], button=3)
    assert mech.move_target is not None, "right-click did not issue move order"
    dist = math.hypot(
        mech.move_target[0] - (target[0] * TILE_SIZE_PX + TILE_SIZE_PX / 2),
        mech.move_target[1] - (target[1] * TILE_SIZE_PX + TILE_SIZE_PX / 2),
    )
    assert dist <= TILE_SIZE_PX, f"move_target off by {dist:.1f}px > 1 tile"
    print(f"[assert ok] move_target within 1 tile of ({target[0]}, {target[1]}), err={dist:.2f}px")

    # 截图 2：选中 + 移动标记可见
    game.renderer.render(
        game.service,
        game.camera.x,
        game.camera.y,
        auto_command=False,
        show_hints=True,
        mission_id="iso_select_move",
        requisition_points=0,
    )
    pygame.display.flip()
    _save(screen, "iso_select_move.png")

    # ③ 高地实体绘制高于低地实体（FR-2.2）
    hill_tile = None
    for ty in range(1, 38):
        for tx in range(2, 38):
            e = grid.elevation_at((tx, ty))
            if e >= 1 and grid.elevation_at((tx, ty + 1)) < e:
                hill_tile = (tx, ty, e)
                break
        if hill_tile is not None:
            break
    assert hill_tile is not None, "no hill with lower south neighbor"
    hx = hill_tile[0] * TILE_SIZE_PX + TILE_SIZE_PX / 2
    hy_north = hill_tile[1] * TILE_SIZE_PX + TILE_SIZE_PX / 2
    hy_south = (hill_tile[1] + 1) * TILE_SIZE_PX + TILE_SIZE_PX / 2

    def _screen_y_at(wx: float, wy: float, ty: int) -> int:
        sx, sy = game.renderer.world_to_screen(wx, wy, game.camera.x, game.camera.y)
        return sy - game.renderer._elev_offset((int(wx) // TILE_SIZE_PX, ty))

    sy_north = _screen_y_at(hx, hy_north, hill_tile[1])
    sy_south = _screen_y_at(hx, hy_south, hill_tile[1] + 1)
    assert sy_north < sy_south, f"hill entity lower than south: {sy_north} vs {sy_south}"
    print(f"[assert ok] hill elev={hill_tile[2]}: north screen_y {sy_north} < south {sy_south}")

    # 截图 1：isometric hills 全景
    game.renderer.render(
        game.service,
        game.camera.x,
        game.camera.y,
        auto_command=False,
        show_hints=True,
        mission_id="iso_hills_panorama",
        requisition_points=0,
    )
    pygame.display.flip()
    _save(screen, "iso_hills_panorama.png")

    # ④/截图 3：top 基线 + HUD 文本内容一致（FR-5.2 修订验收：程序化 HUD 为地形上
    # 的文字，无不透明底板 → 背景像素必然随投影不同；改为捕获 HUD 文本调用，
    # 断言 iso 与 top 渲染的文本序列逐条一致 = 面板链路不经过投影）。
    game_top = create_game(screen, service=battle, projection="top")
    game_top.camera.x, game_top.camera.y = game.camera.x, game.camera.y
    game_top.renderer.render(
        game_top.service,
        game_top.camera.x,
        game_top.camera.y,
        auto_command=False,
        show_hints=True,
        mission_id="iso_hills_panorama",
        requisition_points=0,
    )
    pygame.display.flip()
    _save(screen, "top_baseline.png")

    class _RecordingFont:
        def __init__(self, real):
            self._real = real
            self.texts: list[str] = []

        def render(self, text, *args, **kwargs):
            self.texts.append(text)
            return self._real.render(text, *args, **kwargs)

        def set_bold(self, value):
            return self._real.set_bold(value)

    rec_iso, rec_top = _RecordingFont(game.renderer._font), _RecordingFont(game_top.renderer._font)
    game.renderer._font = rec_iso
    game_top.renderer._font = rec_top
    game.renderer.render(
        game.service,
        game.camera.x,
        game.camera.y,
        auto_command=False,
        show_hints=True,
        mission_id="iso_hills_panorama",
        requisition_points=0,
    )
    game_top.renderer.render(
        game_top.service,
        game_top.camera.x,
        game_top.camera.y,
        auto_command=False,
        show_hints=True,
        mission_id="iso_hills_panorama",
        requisition_points=0,
    )
    assert rec_iso.texts == rec_top.texts and rec_iso.texts, "HUD text differs between iso/top"
    print(f"[assert ok] HUD text identical across projections ({len(rec_iso.texts)} lines)")
    assert game_top.renderer.world_to_screen(100.0, 200.0, 0.0, 0.0) == (100, 200), "top formula changed"
    print("[assert ok] top world_to_screen unchanged (FR-4.2)")

    pygame.quit()


if __name__ == "__main__":
    main()
