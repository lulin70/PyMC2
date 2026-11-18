"""pygame-ce 渲染：程序化地形、机甲方块、血条与弹丸（M1）。

零外部资产：地形由 tile 坐标确定性哈希生成色块，机甲/血条/选中框均为几何绘制。
可选注入 MC2AssetPack（本地 MC2 资产，infrastructure 层加载）：机甲命中精灵时按
格子尺寸缩放 blit（血条/选中框/HUD 仍程序化）；未注入或未命中时保持原色块输出，
渲染结果与未加载资产完全一致。
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
from pymc2.infrastructure.mc2_assets import MC2AssetPack
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
_WRECK_COLOR = (60, 60, 66)  # 摧毁残骸（CT/HEAD 全毁爆炸留痕）
_SELECTION_COLOR = (250, 210, 60)
_MOVE_MARKER_COLOR = (250, 150, 40)
_ATTACK_MARK_COLOR = (255, 96, 64)
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

    def __init__(self, screen: pygame.Surface, asset_pack: MC2AssetPack | None = None) -> None:
        self.screen = screen
        self._assets = asset_pack
        self._terrain = _build_terrain_surface()
        self._font: pygame.font.Font | None = None
        try:
            pygame.font.init()
            self._font = pygame.font.Font(None, 16)
        except Exception:  # noqa: BLE001 - 无字体环境下降级为无标签
            self._font = None

    def world_to_screen(self, x: float, y: float, cam_x: float, cam_y: float) -> tuple[int, int]:
        return (int(x - cam_x), int(y - cam_y))

    def render(
        self,
        service: BattleService,
        cam_x: float,
        cam_y: float,
        *,
        drag_rect: tuple[float, float, float, float] | None = None,
    ) -> None:
        """绘制一帧：地形 → 移动标记 → 弹丸 → 机甲（血条/选中框）→ 攻击高亮 → 框选矩形 → HUD。"""
        self.screen.fill(_GRASS_BASE)
        self.screen.blit(self._terrain, (-int(cam_x), -int(cam_y)))
        for uid in service.selected_ids:
            mech = service.get(uid)
            if mech is not None and mech.move_target is not None:
                self._draw_move_marker(mech.move_target, cam_x, cam_y)
        for projectile in service.projectiles:
            self._draw_projectile(projectile, cam_x, cam_y)
        for mech in service.mechs.values():
            self._draw_mech(mech, cam_x, cam_y, selected=mech.id in service.selected_ids)
        for uid in service.selected_ids:
            mech = service.get(uid)
            if mech is None or mech.attack_target_id is None:
                continue
            target = service.get(mech.attack_target_id)
            if target is not None and target.alive:
                self._draw_attack_mark(target, cam_x, cam_y)
        if drag_rect is not None:
            self._draw_drag_rect(drag_rect, cam_x, cam_y)
        self._draw_hud(service)

    def _draw_mech(self, mech: Mech, cam_x: float, cam_y: float, *, selected: bool) -> None:
        sx, sy = self.world_to_screen(mech.x, mech.y, cam_x, cam_y)
        half = _MECH_SIZE_PX // 2
        rect = pygame.Rect(sx - half, sy - half, _MECH_SIZE_PX, _MECH_SIZE_PX)
        sprite = self._mech_sprite(mech) if mech.alive else None
        if mech.alive and sprite is not None:
            # MC2 资产命中：精灵按格子尺寸缩放绘制；阵营描边/选中框/血条仍程序化。
            sprite_rect = sprite.get_rect(center=(sx, sy))
            self.screen.blit(sprite, sprite_rect)
            pygame.draw.rect(self.screen, _MECH_BORDER_COLOR, sprite_rect, 2)
            if selected:
                pygame.draw.rect(self.screen, _SELECTION_COLOR, sprite_rect.inflate(8, 8), 2)
        elif mech.alive:
            pygame.draw.rect(self.screen, _FACTION_COLORS[mech.faction], rect)
            pygame.draw.rect(self.screen, _MECH_BORDER_COLOR, rect, 2)
            if selected:
                pygame.draw.rect(self.screen, _SELECTION_COLOR, rect.inflate(8, 8), 2)
        else:
            # 摧毁残骸：深灰块 + 交叉裂纹（机甲全毁爆炸的几何表现）。
            pygame.draw.rect(self.screen, _WRECK_COLOR, rect)
            pygame.draw.line(self.screen, _MECH_BORDER_COLOR, rect.topleft, rect.bottomright, 2)
            pygame.draw.line(self.screen, _MECH_BORDER_COLOR, rect.bottomleft, rect.topright, 2)
        self._draw_health_bar(mech, sx, sy - half - 10)
        self._draw_label(mech.id, sx, sy - half - 26)

    def _mech_sprite(self, mech: Mech) -> pygame.Surface | None:
        """资产包机甲精灵：按格子尺寸（TILE_SIZE_PX）缩放缓存；未注入/未命中 → None。"""
        if self._assets is None:
            return None
        return self._assets.scaled_mech_sprite(mech.id, TILE_SIZE_PX)

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

    def _draw_attack_mark(self, target: Mech, cam_x: float, cam_y: float) -> None:
        """攻击目标高亮：围绕被指定目标画一圈红色外框（M1 几何风格）。"""
        sx, sy = self.world_to_screen(target.x, target.y, cam_x, cam_y)
        half = _MECH_SIZE_PX // 2
        rect = pygame.Rect(sx - half, sy - half, _MECH_SIZE_PX, _MECH_SIZE_PX)
        pygame.draw.rect(self.screen, _ATTACK_MARK_COLOR, rect.inflate(12, 12), 2)

    def _draw_drag_rect(self, rect: tuple[float, float, float, float], cam_x: float, cam_y: float) -> None:
        """框选拖拽矩形（世界坐标四元组 → 屏幕细边框）。"""
        x0, y0, x1, y1 = rect
        sx0, sy0 = self.world_to_screen(min(x0, x1), min(y0, y1), cam_x, cam_y)
        sx1, sy1 = self.world_to_screen(max(x0, x1), max(y0, y1), cam_x, cam_y)
        pygame.draw.rect(self.screen, _SELECTION_COLOR, pygame.Rect(sx0, sy0, sx1 - sx0, sy1 - sy0), 1)

    def _draw_hud(self, service: BattleService) -> None:
        """底部状态行：主选中机甲火力姿态（含警戒/关机）、热量与选中列表。"""
        if self._font is None:
            return
        primary = service.selected
        if primary is None:
            stance = "-"
            heat_text = ""
        else:
            stance = primary.fire_mode.value.upper()
            if primary.guarding:
                stance += "+GUARD"
            heat_text = f" | HEAT: {primary.heat.heat:.0f}"
            if primary.heat.shutdown_active:
                heat_text += " [SHUTDOWN]"
        names = ",".join(service.selected_ids) if service.selected_ids else "-"
        line = self._font.render(f"FIRE: {stance} | SEL: {names}{heat_text}", True, _LABEL_COLOR)
        self.screen.blit(line, (8, self.screen.get_height() - 20))

    def _draw_projectile(self, projectile: Projectile, cam_x: float, cam_y: float) -> None:
        sx, sy = self.world_to_screen(projectile.x, projectile.y, cam_x, cam_y)
        pygame.draw.circle(self.screen, _PROJECTILE_COLOR, (sx, sy), 3)
