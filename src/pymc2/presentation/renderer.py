"""pygame-ce 渲染：程序化地形、机甲方块、血条与弹丸（M1）。

零外部资产：地形由 tile 坐标确定性哈希生成色块，机甲/血条/选中框均为几何绘制。
可选注入 MC2AssetPack（本地 MC2 资产，infrastructure 层加载）：
- 机甲命中精灵时按格子尺寸缩放 blit（血条/选中框/HUD 仍程序化）；
- Wave V1 地形合成：pack 含地面/覆盖资产时改铺 MC2 纹理（Detail 系列草地 +
  2lanedirt 道路 overlay，接缝观感优先选帧），pack 无地面资产时地形保持原色块；
- 环境点缀：``plan_decorations`` 确定性撒点 + ``set_decorations`` 注入，非道路
  空 tile 撒树/房精灵，纯装饰不阻挡移动不参与命中（对齐让路协商语义）；
- Wave V2 机甲受损变体：``MECH_DAMAGED_HULL_RATIO`` 以下存活机甲改用 damaged 精灵
  （资产包变体派生，未命中回退基名精灵），无资产包路径渲染输出零改动；
- Wave V3 UI 面板：pack 含 ``buttonlayout1280.fit`` + MCUI 图集时，按官方布局
  （[Statics] 部件 dest + UV 取块）预合成底部面板，画布→屏幕偏移 ``panel_base_offset``
  （水平居中 + 底边锚定，对齐原版 HiresOffsets 语义）；动态信息按官方文本槽位对齐
  绘制程序化文字（RP/任务与托管/dropteam 选中机甲槽/键位提示），布局缺失或零部件
  零解析时完全回退现状 HUD（逐字节不变）。
- P1-A terrace：注入 ``elevation`` 网格（create_game 从 service.elevation 传入）且含丘陵时，
  地形改铺 terrace 版面（顶面上移 + 南侧深色崖壁，行序绘制自然遮挡），机甲/装饰/弹丸/
  移动标记/攻击高亮按所在 tile 高程同步上移，机甲 y 排序键改 (elevation, y)；HUD 状态行
  选中机甲追加 ``ELEV: n``。flat/未注入时渲染输出与现状逐字节一致。
未注入或未命中时保持原色块输出，渲染结果与未加载资产完全一致。
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Sequence

import pygame

from pymc2.domain.elevation import ELEVATION_MAX, ElevationGrid
from pymc2.domain.entities.mech import (
    MAP_SIZE_PX,
    MAP_TILES,
    TILE_SIZE_PX,
    Faction,
    Mech,
    WeaponType,
)
from pymc2.domain.entities.turret import Turret
from pymc2.domain.entities.unit import Unit
from pymc2.domain.systems.ballistic import Projectile
from pymc2.infrastructure.mc2_assets import MC2AssetPack
from pymc2.infrastructure.mesh_render import mech_shadow_surface
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

# Wave V2 机甲受损变体：hull_ratio ≤ 阈值且存活 → 请求 MC2 资产受损精灵（damaged
# 变体 stem 派生，未命中回退基名精灵；无资产包时本常量不参与任何路径，渲染零改动）。
MECH_DAMAGED_HULL_RATIO = 0.5

# Wave V3 UI 面板：buttonlayout1280.fit 官方设计画布 1024×768（布局坐标的参照系）。
PANEL_FIT_CANVAS = (1024, 768)
# 面板槽位文本行距/边距（_font 16px 观感校准，dropteam palette 149px 高容纳 3 行）。
_PANEL_SLOT_PAD_X = 8
_PANEL_SLOT_PAD_Y = 6
_PANEL_LINE_STEP = 15
_DROPTEAM_SLOT_COUNT = 4  # 原版 4 dropteam：选中机甲最多占 4 个槽位

# P1-A terrace：tile 顶面每 1 级高程的屏幕上移量；侧面（南侧崖壁）深色正面。
ELEVATION_STEP_PX = 10
_CLIFF_COLOR = (40, 34, 28)


def panel_base_offset(screen_size: tuple[int, int]) -> tuple[int, int]:
    """buttonlayout1280.fit 画布坐标 → 屏幕坐标的全局偏移（Wave V3）。

    对齐原版 [HiresOffsets] 语义：水平居中（(w−1024)//2）+ 底边锚定（h−768）。
    1280×720 → (128, −48)：面板底边贴窗口底边。"""
    width, height = screen_size
    return ((width - PANEL_FIT_CANVAS[0]) // 2, height - PANEL_FIT_CANVAS[1])


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
# P2 武器三分类弹丸分色（BALLISTIC 保持存量色；ENERGY 青色圆点；MISSILE 橙色方形弹体）。
_ENERGY_PROJECTILE_COLOR = (90, 220, 255)
_MISSILE_PROJECTILE_COLOR = (255, 150, 60)
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

    def __init__(
        self,
        screen: pygame.Surface,
        asset_pack: MC2AssetPack | None = None,
        elevation: ElevationGrid | None = None,
    ) -> None:
        self.screen = screen
        self._assets = asset_pack
        # P1-A 高程单一数据源（create_game 从 service.elevation 注入）；None = flat 语义。
        self._elevation = elevation
        self._terrain, self._terrain_y_offset = self._build_terrain()
        self._decorations: tuple[Decoration, ...] = ()
        self._font: pygame.font.Font | None = None
        try:
            pygame.font.init()
            self._font = pygame.font.Font(None, 16)
        except Exception:  # noqa: BLE001 - 无字体环境下降级为无标签
            self._font = None
        # Wave V3 UI 面板：布局/部件在 __init__ 一次性预合成（布局坐标随资产固定，
        # 不随帧变化）；panel_offset 随窗口尺寸计算（1280×720 → (128, −48)）。
        self._layout = self._assets.button_layout if self._assets is not None else None
        self._panel = self._build_panel_surface()
        self._panel_offset = panel_base_offset(self.screen.get_size())

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

    def _build_terrain(self) -> tuple[pygame.Surface, int]:
        """pack 含地面/覆盖资产 → MC2 纹理地形；否则程序化色块（逐字节不变）。

        P1-A：高程网格含丘陵时改铺 terrace 版面并返回版面 y 偏移（flat/未注入 →
        原路径零改动，偏移 0）。"""
        assets = self._assets
        if assets is not None and assets.has_ground_assets:
            surface = self._build_textured_terrain_surface(assets)
        else:
            surface = _build_terrain_surface()
        if self._elevation is None or self._elevation.is_flat():
            return surface, 0
        return self._terraced_terrain_surface(surface), ELEVATION_MAX * ELEVATION_STEP_PX

    def _terraced_terrain_surface(self, base: pygame.Surface) -> pygame.Surface:
        """terrace 版面（P1-A）：底图下移 ELEVATION_MAX*STEP 预留顶部抬升空间，逐 tile
        自北向南（行序）覆盖绘制——顶面 = 原 tile 纹理区上移 e*STEP（行序使高层顶面
        正确压住北邻低层底边），南邻更低时补深色崖壁（高度 = 高差×STEP，上缘接本 tile
        顶面下缘、下缘接南邻顶面上缘，两端精确衔接）；地图南缘越界视为 0 → 边缘崖壁。
        北/东/西侧高差在本投影（纯 Y 偏移）下为零宽接缝，不单独画侧面（设计文档 §5）。"""
        assert self._elevation is not None
        grid = self._elevation
        offset = ELEVATION_MAX * ELEVATION_STEP_PX
        surface = pygame.Surface((MAP_SIZE_PX, MAP_SIZE_PX + offset))
        surface.blit(base, (0, offset))
        for tile_y in range(MAP_TILES):
            for tile_x in range(MAP_TILES):
                e = grid.elevation_at((tile_x, tile_y))
                if e <= 0:
                    continue
                shift = e * ELEVATION_STEP_PX
                surface.blit(
                    base,
                    (tile_x * TILE_SIZE_PX, tile_y * TILE_SIZE_PX - shift + offset),
                    pygame.Rect(tile_x * TILE_SIZE_PX, tile_y * TILE_SIZE_PX, TILE_SIZE_PX, TILE_SIZE_PX),
                )
                e_south = grid.elevation_at((tile_x, tile_y + 1))
                if e_south < e:
                    band_top = tile_y * TILE_SIZE_PX + TILE_SIZE_PX - shift + offset
                    surface.fill(
                        _CLIFF_COLOR,
                        pygame.Rect(
                            tile_x * TILE_SIZE_PX,
                            band_top,
                            TILE_SIZE_PX,
                            (e - e_south) * ELEVATION_STEP_PX,
                        ),
                    )
        return surface

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

    def _tile_elevation(self, tile: tuple[int, int]) -> int:
        """tile 高程（P1-A）：未注入网格视为 0（flat 语义，存量零改动）。"""
        if self._elevation is None:
            return 0
        return self._elevation.elevation_at(tile)

    def _elev_offset(self, tile: tuple[int, int]) -> int:
        """实体按所在 tile 高程的屏幕 y 上移量（P1-A terrace）。"""
        return self._tile_elevation(tile) * ELEVATION_STEP_PX

    @staticmethod
    def _tile_of_px(x: float, y: float) -> tuple[int, int]:
        """世界像素坐标 → tile 坐标（与 Mech.tile 同口径）。"""
        return (int(x) // TILE_SIZE_PX, int(y) // TILE_SIZE_PX)

    def render(
        self,
        service: BattleService,
        cam_x: float,
        cam_y: float,
        *,
        drag_rect: tuple[float, float, float, float] | None = None,
        auto_command: bool = False,
        show_hints: bool = True,
        mission_id: str | None = None,
        requisition_points: int | None = None,
    ) -> None:
        """绘制一帧：地形 → 移动标记 → 弹丸 → 机甲（按 y 排序，血条/选中框）→ 攻击高亮 → 框选矩形 → UI。

        auto_command：托管激活时状态行/任务条追加 ``AUTO: ON``；show_hints：追加键位
        提示（``--demo`` 自动演示传 False——A 托管提示对无人交互模式无意义）；
        mission_id/requisition_points：V3 面板槽位内容（默认 None → 槽位不画，存量
        调用零改动）。面板可用时槽位化呈现，否则维持现状 HUD（逐字节不变）。
        """
        self.screen.fill(_GRASS_BASE)
        self.screen.blit(self._terrain, (-int(cam_x), -int(cam_y) - self._terrain_y_offset))
        self._draw_decorations(cam_x, cam_y)
        for uid in service.selected_ids:
            mech = service.get(uid)
            if mech is not None and mech.move_target is not None:
                self._draw_move_marker(mech.move_target, cam_x, cam_y)
        for projectile in service.projectiles:
            self._draw_projectile(projectile, cam_x, cam_y)
        # y 排序 tie-break：先按所在 tile 高程（高层后画，避免被低层前景遮挡错序），
        # 再按 y 大者后画（后绘覆盖先绘），同格/重叠时消除叠压随机感。
        # flat 网格高程恒 0 → 退化为纯 y 排序（存量次序不变）。
        for unit in sorted(service.mechs.values(), key=lambda m: (self._tile_elevation(m.tile), m.y)):
            self._draw_unit(unit, cam_x, cam_y, selected=unit.id in service.selected_ids)
        for uid in service.selected_ids:
            selected_unit = service.get(uid)
            if selected_unit is None or selected_unit.attack_target_id is None:
                continue
            target = service.get(selected_unit.attack_target_id)
            if target is not None and target.alive:
                self._draw_attack_mark(target, cam_x, cam_y)
        if drag_rect is not None:
            self._draw_drag_rect(drag_rect, cam_x, cam_y)
        if self._panel is not None and self._layout is not None:
            self._draw_panel(
                service,
                auto_command=auto_command,
                show_hints=show_hints,
                mission_id=mission_id,
                requisition_points=requisition_points,
            )
        else:
            self._draw_hud(service, auto_command=auto_command, show_hints=show_hints)

    def _build_panel_surface(self) -> pygame.Surface | None:
        """按布局 bbox 预合成整块面板（Wave V3，每帧 1 blit）：部件逐个 blit 到
        bbox 局部坐标（负 dest 补边合法）；布局缺失时返回 None（回退现状 HUD）。"""
        if self._layout is None or not self._layout.pieces:
            return None
        origin_x, origin_y = self._layout.canvas_origin
        surface = pygame.Surface(self._layout.canvas_size, pygame.SRCALPHA)
        for piece in self._layout.pieces:
            surface.blit(piece.surface, (piece.dest[0] - origin_x, piece.dest[1] - origin_y))
        return surface

    def _panel_pos(self, base_x: int, base_y: int) -> tuple[int, int]:
        """画布坐标 → 屏幕坐标（全局偏移，槽位文字与面板同一参照系）。"""
        return (self._panel_offset[0] + base_x, self._panel_offset[1] + base_y)

    def _draw_panel(
        self,
        service: BattleService,
        *,
        auto_command: bool,
        show_hints: bool,
        mission_id: str | None,
        requisition_points: int | None,
    ) -> None:
        """MC2 底部面板（Wave V3）：整块预合成贴图 + 官方文本槽位对齐的程序化文字。

        RP 槽位（[ResourcePoints]）、任务/托管条（compalette top）、dropteam 选中机甲
        槽（前 4 台）、键位提示槽（[HelpText] 底对齐）。字体缺失时只贴面板不画文字。"""
        layout, panel = self._layout, self._panel
        if layout is None or panel is None:
            return
        origin_x, origin_y = layout.canvas_origin
        self.screen.blit(panel, (self._panel_offset[0] + origin_x, self._panel_offset[1] + origin_y))
        if self._font is None:
            return
        # dropteam 槽位：选中机甲前 4 台逐槽 3 行（id / 火力姿态 / hull+heat）。
        selected_mechs = [mech for uid in service.selected_ids if (mech := service.get(uid)) is not None]
        if layout.dropteam_origin is not None:
            base_x, base_y = layout.dropteam_origin
            for index, mech in enumerate(selected_mechs[:_DROPTEAM_SLOT_COUNT]):
                slot_x, slot_y = self._panel_pos(base_x + index * layout.dropteam_stride, base_y)
                heat_text = f"{mech.heat.heat:.0f}"
                if mech.heat.shutdown_active:
                    heat_text += " SHUTDOWN"
                lines = (
                    mech.id,
                    f"FIRE {self._stance_text(mech)}",
                    f"HULL {mech.hull_ratio * 100:.0f}% HEAT {heat_text}",
                )
                for line_index, line in enumerate(lines):
                    self.screen.blit(
                        self._font.render(line, True, _LABEL_COLOR),
                        (slot_x + _PANEL_SLOT_PAD_X, slot_y + _PANEL_SLOT_PAD_Y + line_index * _PANEL_LINE_STEP),
                    )
        # 任务/托管条：compalette top 槽位（任务名 + AUTO + 超额选中计数）。
        strip_parts: list[str] = []
        if mission_id:
            strip_parts.append(f"M:{mission_id}")
        if auto_command:
            strip_parts.append("AUTO: ON")
        if len(selected_mechs) > _DROPTEAM_SLOT_COUNT:
            strip_parts.append(f"+{len(selected_mechs) - _DROPTEAM_SLOT_COUNT}")
        if strip_parts and layout.compalette_top_pos is not None:
            strip_x, strip_y = self._panel_pos(*layout.compalette_top_pos)
            self.screen.blit(
                self._font.render(" | ".join(strip_parts), True, _LABEL_COLOR),
                (strip_x + _PANEL_SLOT_PAD_X, strip_y + 1),
            )
        # RP 槽位：[ResourcePoints] 锚点（RP 未注入则不画）。
        if requisition_points is not None and layout.rp_text_pos is not None:
            rp_x, rp_y = self._panel_pos(*layout.rp_text_pos)
            self.screen.blit(
                self._font.render(f"RP: {requisition_points}", True, _LABEL_COLOR),
                (rp_x, rp_y),
            )
        # 键位提示槽位：[HelpText] 底对齐锚点（show_hints 才画；面板模式提示收进面板）。
        if show_hints and layout.help_text_pos is not None:
            help_x, help_bottom = self._panel_pos(*layout.help_text_pos)
            hint = self._font.render("MOVE: ARROWS/WASD (HOLD=CONT) | FIRE: F | AUTO: Q", True, _LABEL_COLOR)
            self.screen.blit(hint, (help_x, help_bottom - hint.get_height()))

    @staticmethod
    def _stance_text(unit: Unit) -> str:
        """火力姿态文本（HUD 状态行与面板 dropteam 槽位共用，含警戒后缀）。"""
        stance = unit.fire_mode.value.upper()
        if unit.guarding:
            stance += "+GUARD"
        return stance

    def _draw_unit(self, unit: Unit, cam_x: float, cam_y: float, *, selected: bool) -> None:
        """绘制单单位（Unit 泛化）：机甲/载具走各自精灵链，无资产时程序化几何回退。

        回退几何（无资产/未命中）：机甲与载具=阵营色方块（载具以 id 标签区分），
        炮塔=阵营色圆形基座（固定工事几何区分）；描边/选中框/血条/残骸共用存量绘制。
        P4：机甲命中 .ase 朝向帧时（_unit_sprite 朝向分支）先铺程序化半透明椭圆脚影
        （mesh_render.mech_shadow_surface，与选中框/血条程序化风格一致）；静态卡路径
        不加脚影——存量像素输出不变。"""
        sx, sy = self.world_to_screen(unit.x, unit.y, cam_x, cam_y)
        sy -= self._elev_offset(unit.tile)  # P1-A：随所在 tile 高程同步上移
        half = _MECH_SIZE_PX // 2
        rect = pygame.Rect(sx - half, sy - half, _MECH_SIZE_PX, _MECH_SIZE_PX)
        sprite = self._unit_sprite(unit) if unit.alive else None
        if unit.alive and sprite is not None:
            if isinstance(unit, Mech) and self._heading_frames_active(unit):
                shadow = mech_shadow_surface(sprite.get_width())
                self.screen.blit(shadow, shadow.get_rect(center=(sx, sy + sprite.get_height() // 4)))
            # MC2 资产命中：精灵按格子尺寸缩放绘制；阵营描边/选中框/血条仍程序化。
            sprite_rect = sprite.get_rect(center=(sx, sy))
            self.screen.blit(sprite, sprite_rect)
            pygame.draw.rect(self.screen, _MECH_BORDER_COLOR, sprite_rect, 2)
            if selected:
                pygame.draw.rect(self.screen, _SELECTION_COLOR, sprite_rect.inflate(8, 8), 2)
        elif unit.alive:
            if isinstance(unit, Turret):
                pygame.draw.circle(self.screen, _FACTION_COLORS[unit.faction], (sx, sy), half)
            else:
                pygame.draw.rect(self.screen, _FACTION_COLORS[unit.faction], rect)
            pygame.draw.rect(self.screen, _MECH_BORDER_COLOR, rect, 2)
            if selected:
                pygame.draw.rect(self.screen, _SELECTION_COLOR, rect.inflate(8, 8), 2)
        else:
            # 摧毁残骸：深灰块 + 交叉裂纹（机甲全毁爆炸的几何表现）。
            pygame.draw.rect(self.screen, _WRECK_COLOR, rect)
            pygame.draw.line(self.screen, _MECH_BORDER_COLOR, rect.topleft, rect.bottomright, 2)
            pygame.draw.line(self.screen, _MECH_BORDER_COLOR, rect.bottomleft, rect.topright, 2)
        self._draw_health_bar(unit, sx, sy - half - 10)
        self._draw_label(unit.id, sx, sy - half - 26)

    def _unit_sprite(self, unit: Unit) -> pygame.Surface | None:
        """资产包单位精灵：机甲走存量 id 链（受损变体口径不变），载具/炮塔走
        resolve_unit_sprite（kind → stem 常量）；未注入/未命中 → None。

        P4 朝向帧：机甲优先取 .ase 预渲染朝向帧（scaled_mech_heading_sprite，按
        unit.heading 选帧）；未命中回退静态卡链（受损变体口径仅在静态卡路径生效，
        docs/P4_DESIGN_20261121.md §8 偏离 2）。"""
        if self._assets is None:
            return None
        if isinstance(unit, Mech):
            heading_sprite = self._assets.scaled_mech_heading_sprite(unit.id, unit.heading, TILE_SIZE_PX)
            if heading_sprite is not None:
                return heading_sprite
            damaged = unit.alive and unit.hull_ratio <= MECH_DAMAGED_HULL_RATIO
            return self._assets.scaled_mech_sprite(unit.id, TILE_SIZE_PX, damaged=damaged)
        damaged = unit.alive and unit.hull_ratio <= MECH_DAMAGED_HULL_RATIO
        return self._assets.scaled_unit_sprite(unit.sprite_kind, TILE_SIZE_PX, damaged=damaged)

    def _heading_frames_active(self, unit: Mech) -> bool:
        """机甲当前是否使用 .ase 朝向帧（脚影铺设条件；缓存命中零开销）。"""
        if self._assets is None:
            return False
        return self._assets.scaled_mech_heading_sprite(unit.id, unit.heading, TILE_SIZE_PX) is not None

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
            sy -= self._elev_offset((tile_x, tile_y))  # P1-A：丘陵 tile 上的装饰同步上移
            self.screen.blit(sprite, sprite.get_rect(center=(sx, sy)))

    def _draw_health_bar(self, unit: Unit, center_x: int, top: int) -> None:
        width, height = _HEALTH_BAR_WIDTH, _HEALTH_BAR_HEIGHT
        bg = pygame.Rect(center_x - width // 2, top, width, height)
        pygame.draw.rect(self.screen, (40, 16, 16), bg)
        fill_width = int(width * unit.hull_ratio)
        if fill_width > 0:
            pygame.draw.rect(self.screen, (80, 200, 80), (bg.x, bg.y, fill_width, height))

    def _draw_label(self, text: str, center_x: int, top: int) -> None:
        if self._font is None:
            return
        label = self._font.render(text, True, _LABEL_COLOR)
        self.screen.blit(label, (center_x - label.get_width() // 2, top - 12))

    def _draw_move_marker(self, target: tuple[float, float], cam_x: float, cam_y: float) -> None:
        sx, sy = self.world_to_screen(target[0], target[1], cam_x, cam_y)
        sy -= self._elev_offset(self._tile_of_px(target[0], target[1]))  # P1-A：目标 tile 高程
        rect = pygame.Rect(sx - 6, sy - 6, 12, 12)
        pygame.draw.rect(self.screen, _MOVE_MARKER_COLOR, rect, 2)
        pygame.draw.line(self.screen, _MOVE_MARKER_COLOR, rect.topleft, rect.bottomright, 2)
        pygame.draw.line(self.screen, _MOVE_MARKER_COLOR, rect.bottomleft, rect.topright, 2)

    def _draw_attack_mark(self, target: Unit, cam_x: float, cam_y: float) -> None:
        """攻击目标高亮：围绕被指定目标画一圈红色外框（M1 几何风格）。"""
        sx, sy = self.world_to_screen(target.x, target.y, cam_x, cam_y)
        sy -= self._elev_offset(target.tile)  # P1-A：目标所在 tile 高程
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
            stance = self._stance_text(primary)
            heat_text = f" | HEAT: {primary.heat.heat:.0f}"
            if primary.heat.shutdown_active:
                heat_text += " [SHUTDOWN]"
        names = ",".join(service.selected_ids) if service.selected_ids else "-"
        text = f"FIRE: {stance} | SEL: {names}{heat_text}"
        if primary is not None:
            # P1-A：选中单位所在 tile 高程（ASCII，收进现有状态行尾部惯例）。
            text += f" | ELEV: {self._tile_elevation(primary.tile)}"
            # P1-B：锁定目标视线被地形遮挡提示（flat 快路径恒通 → 永不追加，
            # 存量 flat 帧逐字节不变）。
            target = service.get(primary.attack_target_id) if primary.attack_target_id is not None else None
            if (
                target is not None
                and target.alive
                and not service.elevation.has_line_of_sight(primary.tile, target.tile)
            ):
                text += " | NO LOS"
        if auto_command:
            text += " | AUTO: ON"
        if show_hints:
            # pygame 内置字体无 CJK 字形，提示行用 ASCII 保证可渲染（中文键位说明见 README）。
            text += " | MOVE: ARROWS/WASD (HOLD=CONT) | FIRE: F | AUTO: Q"
        line = self._font.render(text, True, _LABEL_COLOR)
        self.screen.blit(line, (8, self.screen.get_height() - 20))

    def _draw_projectile(self, projectile: Projectile, cam_x: float, cam_y: float) -> None:
        """弹丸绘制（P2 按武器三分类分形分色）：BALLISTIC 存量黄色圆点、ENERGY
        青色圆点、MISSILE 橙色方形弹体。"""
        sx, sy = self.world_to_screen(projectile.x, projectile.y, cam_x, cam_y)
        sy -= self._elev_offset(self._tile_of_px(projectile.x, projectile.y))  # P1-A：所在 tile 高程
        if projectile.wtype is WeaponType.MISSILE:
            pygame.draw.rect(self.screen, _MISSILE_PROJECTILE_COLOR, (sx - 2, sy - 2, 4, 4))
        elif projectile.wtype is WeaponType.ENERGY:
            pygame.draw.circle(self.screen, _ENERGY_PROJECTILE_COLOR, (sx, sy), 3)
        else:
            pygame.draw.circle(self.screen, _PROJECTILE_COLOR, (sx, sy), 3)
