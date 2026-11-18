"""pygame-ce 渲染：程序化地形、机甲方块、血条与弹丸（M1）。

零外部资产：地形由 tile 坐标确定性哈希生成色块，机甲/血条/选中框均为几何绘制。
"""

from __future__ import annotations

import hashlib

import pygame

from pymc2.domain.entities.mech import (
    MAP_SIZE_PX,
    MAP_TILES,
    TILE_SIZE_PX,
    Faction,
    Mech,
)
from pymc2.domain.systems.ballistic import Projectile
from pymc2.services.battle_service import BattleService

WINDOW_WIDTH_PX = 1280
WINDOW_HEIGHT_PX = 720

_GRASS_BASE = (58, 96, 44)
_GRASS_VARIATION = 12
_ROAD_COLOR = (72, 72, 76)
_ROAD_DASH_COLOR = (110, 110, 116)
_ROAD_ROW = 10
_ROAD_COL = 25

_MECH_SIZE_PX = 24
_HEALTH_BAR_WIDTH = 28
_HEALTH_BAR_HEIGHT = 4

_FACTION_COLORS: dict[Faction, tuple[int, int, int]] = {
    Faction.PLAYER: (70, 130, 230),
    Faction.ENEMY: (210, 70, 60),
}
_MECH_BORDER_COLOR = (20, 24, 28)
_SELECTION_COLOR = (250, 210, 60)
_MOVE_MARKER_COLOR = (250, 150, 40)
_PROJECTILE_COLOR = (255, 230, 120)
_LABEL_COLOR = (230, 230, 230)


def _clamp8(value: int) -> int:
    return max(0, min(255, value))


def _tile_color(tile_x: int, tile_y: int) -> tuple[int, int, int]:
    """确定性地形色块：道路平直，草地按 tile 哈希微扰动明度。"""
    if tile_y == _ROAD_ROW or tile_x == _ROAD_COL:
        return _ROAD_COLOR
    digest = hashlib.md5(f"{tile_x}:{tile_y}".encode()).digest()
    delta = digest[0] % (2 * _GRASS_VARIATION + 1) - _GRASS_VARIATION
    r, g, b = _GRASS_BASE
    return (_clamp8(r + delta), _clamp8(g + delta), _clamp8(b + delta))


def _build_terrain_surface() -> pygame.Surface:
    """预渲染整张 40x40 tile 地形（1280x1280），每帧按相机偏移 blit。"""
    surface = pygame.Surface((MAP_SIZE_PX, MAP_SIZE_PX))
    for tile_y in range(MAP_TILES):
        for tile_x in range(MAP_TILES):
            rect = pygame.Rect(tile_x * TILE_SIZE_PX, tile_y * TILE_SIZE_PX, TILE_SIZE_PX, TILE_SIZE_PX)
            surface.fill(_tile_color(tile_x, tile_y), rect)
    for tile_x in range(0, MAP_TILES, 2):
        surface.fill(
            _ROAD_DASH_COLOR,
            pygame.Rect(tile_x * TILE_SIZE_PX + 8, _ROAD_ROW * TILE_SIZE_PX + 14, 16, 4),
        )
    for tile_y in range(0, MAP_TILES, 2):
        surface.fill(
            _ROAD_DASH_COLOR,
            pygame.Rect(_ROAD_COL * TILE_SIZE_PX + 14, tile_y * TILE_SIZE_PX + 8, 4, 16),
        )
    return surface


class Renderer:
    """把 BattleService 状态绘制到屏幕（相机平移，无缩放）。"""

    def __init__(self, screen: pygame.Surface) -> None:
        self.screen = screen
        self._terrain = _build_terrain_surface()
        self._font: pygame.font.Font | None = None
        try:
            pygame.font.init()
            self._font = pygame.font.Font(None, 16)
        except Exception:  # noqa: BLE001 - 无字体环境下降级为无标签
            self._font = None

    def world_to_screen(self, x: float, y: float, cam_x: float, cam_y: float) -> tuple[int, int]:
        return (int(x - cam_x), int(y - cam_y))

    def render(self, service: BattleService, cam_x: float, cam_y: float) -> None:
        """绘制一帧：地形 → 移动标记 → 弹丸 → 机甲（血条/选中框）。"""
        self.screen.fill(_GRASS_BASE)
        self.screen.blit(self._terrain, (-int(cam_x), -int(cam_y)))
        selected = service.selected
        if selected is not None and selected.move_target is not None:
            self._draw_move_marker(selected.move_target, cam_x, cam_y)
        for projectile in service.projectiles:
            self._draw_projectile(projectile, cam_x, cam_y)
        for mech in service.mechs.values():
            self._draw_mech(mech, cam_x, cam_y, selected=mech.id == service.selected_id)

    def _draw_mech(self, mech: Mech, cam_x: float, cam_y: float, *, selected: bool) -> None:
        sx, sy = self.world_to_screen(mech.x, mech.y, cam_x, cam_y)
        half = _MECH_SIZE_PX // 2
        rect = pygame.Rect(sx - half, sy - half, _MECH_SIZE_PX, _MECH_SIZE_PX)
        pygame.draw.rect(self.screen, _FACTION_COLORS[mech.faction], rect)
        pygame.draw.rect(self.screen, _MECH_BORDER_COLOR, rect, 2)
        if selected:
            pygame.draw.rect(self.screen, _SELECTION_COLOR, rect.inflate(8, 8), 2)
        self._draw_health_bar(mech, sx, sy - half - 10)
        self._draw_label(mech.id, sx, sy - half - 26)

    def _draw_health_bar(self, mech: Mech, center_x: int, top: int) -> None:
        width, height = _HEALTH_BAR_WIDTH, _HEALTH_BAR_HEIGHT
        bg = pygame.Rect(center_x - width // 2, top, width, height)
        pygame.draw.rect(self.screen, (40, 16, 16), bg)
        fill_width = int(width * mech.hull_ratio)
        if fill_width > 0:
            pygame.draw.rect(self.screen, (80, 200, 80), (bg.x, bg.y, fill_width, height))

    def _draw_label(self, text: str, center_x: int, top: int) -> None:
        if self._font is None:
            return
        label = self._font.render(text, True, _LABEL_COLOR)
        self.screen.blit(label, (center_x - label.get_width() // 2, top - 12))

    def _draw_move_marker(self, target: tuple[float, float], cam_x: float, cam_y: float) -> None:
        sx, sy = self.world_to_screen(target[0], target[1], cam_x, cam_y)
        rect = pygame.Rect(sx - 6, sy - 6, 12, 12)
        pygame.draw.rect(self.screen, _MOVE_MARKER_COLOR, rect, 2)
        pygame.draw.line(self.screen, _MOVE_MARKER_COLOR, rect.topleft, rect.bottomright, 2)
        pygame.draw.line(self.screen, _MOVE_MARKER_COLOR, rect.bottomleft, rect.topright, 2)

    def _draw_projectile(self, projectile: Projectile, cam_x: float, cam_y: float) -> None:
        sx, sy = self.world_to_screen(projectile.x, projectile.y, cam_x, cam_y)
        pygame.draw.circle(self.screen, _PROJECTILE_COLOR, (sx, sy), 3)
