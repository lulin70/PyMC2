"""pygame-ce 渲染：程序化地形、机甲方块、血条与弹丸（M1）。

零外部资产：地形由 tile 坐标确定性哈希生成色块，机甲/血条/选中框均为几何绘制。
可选注入 MC2AssetPack（本地 MC2 资产，infrastructure 层加载）：
- 机甲命中精灵时按格子尺寸缩放 blit（血条/选中框/HUD 仍程序化）；
- Wave V1 地形合成：pack 含地面/覆盖资产时改铺 MC2 纹理（Detail 系列草地 +
  2lanedirt 道路 overlay，接缝观感优先选帧），pack 无地面资产时地形保持原色块；
- 环境点缀：``plan_decorations`` 确定性撒点 + ``set_decorations`` 注入，非道路
  空 tile 撒树/房精灵，纯装饰不阻挡移动不参与命中（对齐让路协商语义）。
未注入或未命中时保持原色块输出，渲染结果与未加载资产完全一致。
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Sequence

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

# Wave V1 地形合成：2lanedirt 序列帧语义（按边缘连通性勘测）——0000=竖直直道
# （上下连通）、0001=水平直道（左右连通）、0002-0005=四向弯道（本图无弯道段，
# 经 resolve_ground("road", seed) 暴露备用）。接缝观感优先：直道段铺对应直帧，
# 十字交点叠两直帧保四向连通。
_ROAD_FRAME_VERTICAL = 0
_ROAD_FRAME_HORIZONTAL = 1

# 环境点缀（纯装饰，不阻挡移动不参与命中）：确定性 seed 撒点，摧毁变体 V2 再接。
Decoration = tuple[int, int, str]  # (tile_x, tile_y, kind)
_DECORATION_COUNT = 15
_DECORATION_SEED = 20261119
_DECORATION_KINDS: tuple[str, ...] = ("tree", "jungle_tree", "jungle_tree_b", "dead_tree", "greenhouse")
_DECORATION_SIZE_PX = TILE_SIZE_PX

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


def _grass_color(tile_x: int, tile_y: int) -> tuple[int, int, int]:
    """确定性草色：按 tile 哈希微扰动明度（纹理地形缺资产时的逐格回退色）。"""
    digest = hashlib.md5(f"{tile_x}:{tile_y}".encode()).digest()
    delta = digest[0] % (2 * _GRASS_VARIATION + 1) - _GRASS_VARIATION
    r, g, b = _GRASS_BASE
    return (_clamp8(r + delta), _clamp8(g + delta), _clamp8(b + delta))


def _tile_color(tile_x: int, tile_y: int) -> tuple[int, int, int]:
    """确定性地形色块：道路平直，草地按 tile 哈希微扰动明度。"""
    if tile_y == _ROAD_ROW or tile_x == _ROAD_COL:
        return _ROAD_COLOR
    return _grass_color(tile_x, tile_y)


def _road_frame_indices(tile_x: int, tile_y: int) -> tuple[int, ...]:
    """道路 tile → 2lanedirt 帧号（tile 坐标确定性，接缝观感优先）：直道段用对应
    直帧，十字交点叠两直帧成四向连通。"""
    on_row = tile_y == _ROAD_ROW
    on_col = tile_x == _ROAD_COL
    if on_row and on_col:
        return (_ROAD_FRAME_HORIZONTAL, _ROAD_FRAME_VERTICAL)
    if on_row:
        return (_ROAD_FRAME_HORIZONTAL,)
    return (_ROAD_FRAME_VERTICAL,)


def plan_decorations(
    occupied_tiles: set[tuple[int, int]] | None = None,
    count: int = _DECORATION_COUNT,
    seed: int = _DECORATION_SEED,
) -> tuple[Decoration, ...]:
    """确定性撒点：非道路 tile 排除已占用格，random.Random(seed) 抽样（同 seed 同结果）。

    纯装饰语义：返回 (tile_x, tile_y, kind) 供渲染层 blit——不进战斗逻辑、不阻挡
    移动、不参与命中（机甲可自由穿过，对齐让路协商语义）；kind 按序循环取用。
    """
    rng = random.Random(seed)
    occupied = occupied_tiles or set()
    candidates = [
        (x, y)
        for y in range(MAP_TILES)
        for x in range(MAP_TILES)
        if y != _ROAD_ROW and x != _ROAD_COL and (x, y) not in occupied
    ]
    picked = rng.sample(candidates, min(count, len(candidates)))
    return tuple((x, y, _DECORATION_KINDS[i % len(_DECORATION_KINDS)]) for i, (x, y) in enumerate(picked))


def _build_terrain_surface() -> pygame.Surface:
    """预渲染整张 40x40 tile 程序化地形（1280x1280），每帧按相机偏移 blit。"""
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


def _fill_road_dashes(surface: pygame.Surface, tile_x: int, tile_y: int) -> None:
    """程序化道路虚线（逐 tile 版，rect 均落在本 tile 内）：与 _build_terrain_surface
    的整幅虚线逐像素等价（行虚线 tile_x 偶数、列虚线 tile_y 偶数）。"""
    if tile_y == _ROAD_ROW and tile_x % 2 == 0:
        surface.fill(
            _ROAD_DASH_COLOR,
            pygame.Rect(tile_x * TILE_SIZE_PX + 8, _ROAD_ROW * TILE_SIZE_PX + 14, 16, 4),
        )
    if tile_x == _ROAD_COL and tile_y % 2 == 0:
        surface.fill(
            _ROAD_DASH_COLOR,
            pygame.Rect(_ROAD_COL * TILE_SIZE_PX + 14, tile_y * TILE_SIZE_PX + 8, 4, 16),
        )


class Renderer:
    """把 BattleService 状态绘制到屏幕（相机平移，无缩放）。"""

    def __init__(self, screen: pygame.Surface, asset_pack: MC2AssetPack | None = None) -> None:
        self.screen = screen
        self._assets = asset_pack
        self._terrain = self._build_terrain()
        self._decorations: tuple[Decoration, ...] = ()
        self._font: pygame.font.Font | None = None
        try:
            pygame.font.init()
            self._font = pygame.font.Font(None, 16)
        except Exception:  # noqa: BLE001 - 无字体环境下降级为无标签
            self._font = None

    def draw_settlement_overlay(self, title: str, lines: Sequence[str]) -> None:
        """任务结算遮罩层：半透明黑底 + 居中标题/摘要行（窗口内结算呈现）。

        字体环境缺失时静默降级为纯遮罩（无文字），不阻塞结算流程。
        """
        veil = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 180))
        self.screen.blit(veil, (0, 0))
        if self._font is None:
            return
        width, height = self.screen.get_size()
        title_surf = self._font.render(title, True, (255, 210, 90))
        self.screen.blit(title_surf, title_surf.get_rect(midtop=(width // 2, height // 3)))
        y = height // 3 + title_surf.get_height() + 12
        for line in lines:
            line_surf = self._font.render(line, True, (230, 230, 230))
            self.screen.blit(line_surf, line_surf.get_rect(midtop=(width // 2, y)))
            y += line_surf.get_height() + 6

    def _build_terrain(self) -> pygame.Surface:
        """pack 含地面/覆盖资产 → MC2 纹理地形；否则程序化色块（逐字节不变）。"""
        assets = self._assets
        if assets is not None and assets.has_ground_assets:
            return self._build_textured_terrain_surface(assets)
        return _build_terrain_surface()

    def _build_textured_terrain_surface(self, assets: MC2AssetPack) -> pygame.Surface:
        """MC2 纹理地形（Wave V1）：非道路 tile 铺 Detail 系列草地纹理（tile 哈希
        确定性选张），道路 tile 以草皮为底叠 2lanedirt overlay（接缝优先选帧）。
        单 tile 解析失败逐格回退程序化色块/虚线（部分资产包优雅降级）。"""
        surface = pygame.Surface((MAP_SIZE_PX, MAP_SIZE_PX))
        for tile_y in range(MAP_TILES):
            for tile_x in range(MAP_TILES):
                rect = pygame.Rect(tile_x * TILE_SIZE_PX, tile_y * TILE_SIZE_PX, TILE_SIZE_PX, TILE_SIZE_PX)
                digest = hashlib.md5(f"{tile_x}:{tile_y}".encode()).digest()
                if tile_y == _ROAD_ROW or tile_x == _ROAD_COL:
                    self._fill_road_tile(surface, assets, rect, tile_x, tile_y, digest[1])
                else:
                    self._fill_grass_tile(surface, assets, rect, tile_x, tile_y, digest[1])
        return surface

    def _fill_grass_tile(
        self,
        surface: pygame.Surface,
        assets: MC2AssetPack,
        rect: pygame.Rect,
        tile_x: int,
        tile_y: int,
        seed: int,
    ) -> None:
        """草地 tile：Detail 系列纹理缩放到格子尺寸；未命中回退哈希草色。"""
        texture = assets.scaled_ground_tile("grass", seed, TILE_SIZE_PX)
        if texture is not None:
            surface.blit(texture, rect.topleft)
        else:
            surface.fill(_grass_color(tile_x, tile_y), rect)

    def _fill_road_tile(
        self,
        surface: pygame.Surface,
        assets: MC2AssetPack,
        rect: pygame.Rect,
        tile_x: int,
        tile_y: int,
        seed: int,
    ) -> None:
        """道路 tile：草皮底 + 2lanedirt overlay 叠加（tile 坐标确定性选帧）；overlay
        整体缺失时回退程序化路色 + 虚线（与现状逐像素一致）。"""
        base = assets.scaled_ground_tile("grass", seed, TILE_SIZE_PX)
        if base is not None:
            surface.blit(base, rect.topleft)
        else:
            surface.fill(_grass_color(tile_x, tile_y), rect)
        overlaid = False
        for frame_index in _road_frame_indices(tile_x, tile_y):
            overlay = assets.scaled_ground_tile("road", frame_index, TILE_SIZE_PX)
            if overlay is not None:
                overlaid = True
                surface.blit(overlay, rect.topleft)
        if not overlaid:
            surface.fill(_ROAD_COLOR, rect)
            _fill_road_dashes(surface, tile_x, tile_y)

    def set_decorations(self, decorations: tuple[Decoration, ...]) -> None:
        """注入环境点缀（create_game 按确定性 seed 撒点后传入；渲染层纯 blit 不影响玩法）。"""
        self._decorations = decorations

    def world_to_screen(self, x: float, y: float, cam_x: float, cam_y: float) -> tuple[int, int]:
        return (int(x - cam_x), int(y - cam_y))

    def render(
        self,
        service: BattleService,
        cam_x: float,
        cam_y: float,
        *,
        drag_rect: tuple[float, float, float, float] | None = None,
        auto_command: bool = False,
        show_hints: bool = True,
    ) -> None:
        """绘制一帧：地形 → 移动标记 → 弹丸 → 机甲（按 y 排序，血条/选中框）→ 攻击高亮 → 框选矩形 → HUD。

        auto_command：托管激活时状态行追加 ``AUTO: ON``；show_hints：追加键位提示
        （``--demo`` 自动演示传 False——A 托管提示对无人交互模式无意义）。
        """
        self.screen.fill(_GRASS_BASE)
        self.screen.blit(self._terrain, (-int(cam_x), -int(cam_y)))
        self._draw_decorations(cam_x, cam_y)
        for uid in service.selected_ids:
            mech = service.get(uid)
            if mech is not None and mech.move_target is not None:
                self._draw_move_marker(mech.move_target, cam_x, cam_y)
        for projectile in service.projectiles:
            self._draw_projectile(projectile, cam_x, cam_y)
        # y 排序 tie-break：y 大者后画（后绘覆盖先绘），同格/重叠时消除叠压随机感。
        for mech in sorted(service.mechs.values(), key=lambda m: m.y):
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
        self._draw_hud(service, auto_command=auto_command, show_hints=show_hints)

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

    def _draw_decorations(self, cam_x: float, cam_y: float) -> None:
        """环境点缀（Wave V1）：非道路空 tile 的树/房精灵，画在地形之上、机甲之下。
        资产未注入或单个 kind 未命中时逐个跳过；纯装饰不参与命中。"""
        if not self._decorations or self._assets is None:
            return
        for tile_x, tile_y, kind in self._decorations:
            sprite = self._assets.scaled_environment_sprite(kind, _DECORATION_SIZE_PX)
            if sprite is None:
                continue
            sx, sy = self.world_to_screen(
                tile_x * TILE_SIZE_PX + TILE_SIZE_PX // 2,
                tile_y * TILE_SIZE_PX + TILE_SIZE_PX // 2,
                cam_x,
                cam_y,
            )
            self.screen.blit(sprite, sprite.get_rect(center=(sx, sy)))

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

    def _draw_hud(self, service: BattleService, *, auto_command: bool = False, show_hints: bool = True) -> None:
        """底部状态行：火力姿态（含警戒/关机）、热量、选中列表、托管状态（AUTO: ON）与键位提示。"""
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
        text = f"FIRE: {stance} | SEL: {names}{heat_text}"
        if auto_command:
            text += " | AUTO: ON"
        if show_hints:
            # pygame 内置字体无 CJK 字形，提示行用 ASCII 保证可渲染（中文键位说明见 README）。
            text += " | MOVE: ARROWS/W S D | FIRE: F | AUTO: A"
        line = self._font.render(text, True, _LABEL_COLOR)
        self.screen.blit(line, (8, self.screen.get_height() - 20))

    def _draw_projectile(self, projectile: Projectile, cam_x: float, cam_y: float) -> None:
        sx, sy = self.world_to_screen(projectile.x, projectile.y, cam_x, cam_y)
        pygame.draw.circle(self.screen, _PROJECTILE_COLOR, (sx, sy), 3)
