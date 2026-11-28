"""装配编辑器 GUI（W2，设计 docs/W2_DESIGN_20261128.md §4）。

三栏布局 + 拖拽状态机；纯程序化绘制（无 TGA 依赖 → FR-6.3）。
数据变更经 LoadoutConfig（模型层），本层只做命中检测与状态流转。
"""

from __future__ import annotations

import pygame

from pymc2.domain.loadout import (
    ARMOR_CAPS,
    TONNAGE_MAX,
    WEAPON_LIBRARY,
    LoadoutConfig,
    WeaponSlotConfig,
)
from pymc2.services.loadout_service import LoadoutService

# ---------------------------------------------------------------------------
# 布局常量（1280×720，设计 §4.3）
# ---------------------------------------------------------------------------

LIBRARY_RECT = pygame.Rect(10, 10, 300, 500)
PARTS_RECT = pygame.Rect(340, 10, 400, 500)
SUMMARY_RECT = pygame.Rect(770, 10, 500, 500)
FOOTER_RECT = pygame.Rect(10, 520, 1260, 40)
ROW_HEIGHT = 24
VISIBLE_ROWS = LIBRARY_RECT.height // ROW_HEIGHT

CONFIRM_RECT = pygame.Rect(790, 440, 100, 32)
CANCEL_RECT = pygame.Rect(900, 440, 100, 32)

# 机甲部位图（中央）：部位 → 屏幕矩形（相对 PARTS_RECT）
_PART_LAYOUT: dict[str, pygame.Rect] = {
    "head": pygame.Rect(200, 20, 60, 50),
    "ct": pygame.Rect(190, 80, 80, 120),
    "lt": pygame.Rect(110, 80, 70, 110),
    "rt": pygame.Rect(280, 80, 70, 110),
    "la": pygame.Rect(30, 80, 70, 100),
    "ra": pygame.Rect(360, 80, 70, 100),
    "ll": pygame.Rect(160, 210, 60, 90),
    "rl": pygame.Rect(240, 210, 60, 90),
}
_PART_LAYOUT = {loc: rect.move(PARTS_RECT.x, PARTS_RECT.y) for loc, rect in _PART_LAYOUT.items()}

_BG = (24, 26, 30)
_PANEL = (40, 44, 52)
_TEXT = (230, 230, 230)
_DIM = (150, 150, 150)
_ACCENT = (90, 160, 255)
_OK = (60, 180, 90)
_WARN = (220, 60, 60)
_SLOT = (70, 76, 88)


class MechLabState:
    """装配界面运行时状态（设计 §4.1）。"""

    def __init__(self, service: LoadoutService | None = None) -> None:
        self.service = service if service is not None else LoadoutService()
        self.config: LoadoutConfig = self.service.default_config()
        self.phase: str = "idle"  # idle | dragging
        self.drag_spec_name: str | None = None  # 拖拽中的武器目录名
        self.drag_from_slot: int | None = None  # 从槽位拖出（卸下）时记录原槽位序号
        self.drag_pos: tuple[int, int] | None = None
        self.drag_ghost: pygame.Surface | None = None
        self.scroll: int = 0
        self.error_line: str | None = None
        self.error_frames: int = 0
        self.font: pygame.font.Font | None = None
        # 确认回调（main 注入：应用配置到玩家机甲 + 保存，FR-5.1）；None=仅内存编辑
        self.on_confirm = None
        try:
            pygame.font.init()
            self.font = pygame.font.Font(None, 18)
        except Exception:  # noqa: BLE001 - 无字体环境降级为无标签
            self.font = None

    # -- 错误提示（FR-2.3 红显 2s ≈ 120 帧）--

    def show_error(self, message: str) -> None:
        self.error_line = message
        self.error_frames = 120


def handle_mech_lab_event(state: MechLabState, event: pygame.event.Event) -> bool:
    """处理装配界面事件；返回 True 表示事件已消费（不落入战斗输入路径）。"""
    if event.type == pygame.KEYDOWN:
        return False  # L 键切换由 main.handle_event 统一处理
    if event.type == pygame.MOUSEWHEEL:
        state.scroll = max(0, state.scroll - event.y * 3)
        return True
    if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
        return _on_mousedown(state, event.pos)
    if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
        return _on_mouseup(state, event.pos)
    if event.type == pygame.MOUSEMOTION and state.phase == "dragging":
        state.drag_pos = event.pos
        return True
    return state.phase == "dragging"


def _on_mousedown(state: MechLabState, pos: tuple[int, int]) -> bool:
    # 武器库条目 → 拖出新武器
    if LIBRARY_RECT.collidepoint(pos):
        index = pos[1] // ROW_HEIGHT + state.scroll
        if 0 <= index < len(WEAPON_LIBRARY):
            state.phase = "dragging"
            state.drag_spec_name = WEAPON_LIBRARY[index].name
            state.drag_from_slot = None
            state.drag_pos = pos
        return True
    # 槽位（有武器）→ 卸下拖动（US-4）
    loc = _slot_at(pos)
    if loc is not None:
        slot_indexes = [i for i, w in enumerate(state.config.weapons) if w.location == loc]
        if slot_indexes:
            state.phase = "dragging"
            state.drag_from_slot = slot_indexes[-1]  # 取该部位最后挂载
            state.drag_spec_name = state.config.weapons[state.drag_from_slot].spec_name
            state.drag_pos = pos
        return True
    return False


def _on_mouseup(state: MechLabState, pos: tuple[int, int]) -> bool:
    if state.phase != "dragging":
        # 按钮点击（FR-4.2 [确认][取消]）
        if CONFIRM_RECT.collidepoint(pos):
            err = state.config.validate()
            if err is not None:
                state.show_error(f"{err.code}: {err.message}")  # 超重确认拦截（FR-2.3）
            elif state.on_confirm is not None:
                state.on_confirm(state.config)
            return True
        if CANCEL_RECT.collidepoint(pos):
            state.config = state.service.default_config(state.config.chassis_kind)
            return True
        return False
    state.phase = "idle"
    spec_name, from_slot = state.drag_spec_name, state.drag_from_slot
    state.drag_spec_name = None
    state.drag_from_slot = None
    state.drag_pos = None
    if spec_name is None:
        return True

    # 卸下路径：从配置移除（US-4）
    removed: WeaponSlotConfig | None = None
    if from_slot is not None and 0 <= from_slot < len(state.config.weapons):
        removed = state.config.weapons.pop(from_slot)

    loc = _slot_at(pos)
    if loc is not None:
        # 装入/换位：吨位校验（超重 → 红显不变更，FR-2.3）
        candidate = WeaponSlotConfig(spec_name, loc)
        if removed is not None and removed.location == loc:
            state.config.weapons.append(removed)  # 原槽放回
        else:
            trial = list(state.config.weapons)
            trial.append(candidate)
            trial_cfg = LoadoutConfig(state.config.chassis_kind, state.config.armor_allocation, trial)
            err = trial_cfg.validate()
            if err is None:
                state.config.weapons = trial
            else:
                if removed is not None:
                    state.config.weapons.append(removed)  # 失败回滚原槽
                state.show_error(f"{err.code}: {err.message}")
    elif LIBRARY_RECT.collidepoint(pos) and removed is not None:
        pass  # 拖回武器库 → 保持卸下（US-4）
    elif removed is not None:
        state.config.weapons.append(removed)  # 无效释放 → 原槽回滚
    return True


def _slot_at(pos: tuple[int, int]) -> str | None:
    """命中检测：部位矩形（≤槽位矩形粒度，FR-4.3）。"""
    for loc, rect in _PART_LAYOUT.items():
        if rect.collidepoint(pos):
            return loc
    return None


def draw_mech_lab(surface: pygame.Surface, state: MechLabState) -> None:
    """整屏绘制装配界面（纯程序化）。"""
    surface.fill(_BG)
    font = state.font
    if font is None:
        return

    # 左栏：武器库
    pygame.draw.rect(surface, _PANEL, LIBRARY_RECT, border_radius=4)
    _label(surface, font, "WEAPON LIBRARY", (LIBRARY_RECT.x + 8, LIBRARY_RECT.y + 4), _ACCENT)
    for row, spec in enumerate(WEAPON_LIBRARY[state.scroll : state.scroll + VISIBLE_ROWS]):
        y = LIBRARY_RECT.y + 28 + row * ROW_HEIGHT
        color = _TEXT
        surface.blit(font.render(f"{spec.name}", True, color), (LIBRARY_RECT.x + 12, y))
        surface.blit(
            font.render(f"{spec.tonnage:.1f}t D{spec.damage}", True, _DIM),
            (LIBRARY_RECT.x + 170, y),
        )

    # 中央：机甲部位图 + 槽位数
    pygame.draw.rect(surface, _PANEL, PARTS_RECT, border_radius=4)
    for loc, rect in _PART_LAYOUT.items():
        pygame.draw.rect(surface, _SLOT, rect, border_radius=3)
        count = sum(1 for w in state.config.weapons if w.location == loc)
        label = font.render(f"{loc.upper()}", True, _TEXT)
        surface.blit(label, (rect.x + 6, rect.y + 4))
        if count:
            surface.blit(font.render(f"x{count}", True, _ACCENT), (rect.x + 6, rect.y + 22))

    # 右栏：吨位条 + 装甲值 + 按钮
    pygame.draw.rect(surface, _PANEL, SUMMARY_RECT, border_radius=4)
    total = state.config.total_tonnage()
    max_t = TONNAGE_MAX.get(state.config.chassis_kind, 55.0)
    overweight = total > max_t
    bar_color = _WARN if overweight else _OK
    _label(surface, font, f"TONNAGE {total:.1f} / {max_t:.1f} t", (SUMMARY_RECT.x + 10, SUMMARY_RECT.y + 8), bar_color)
    bar = pygame.Rect(SUMMARY_RECT.x + 10, SUMMARY_RECT.y + 30, 480, 14)
    pygame.draw.rect(surface, (60, 64, 72), bar, border_radius=3)
    ratio = min(1.0, total / max_t) if max_t else 0.0
    pygame.draw.rect(surface, bar_color, (bar.x, bar.y, int(bar.width * ratio), bar.height), border_radius=3)
    y = SUMMARY_RECT.y + 56
    for loc, cap in ARMOR_CAPS.items():
        value = state.config.armor_allocation.get(loc, 0)
        surface.blit(font.render(f"{loc.upper():<3} {value:>3}/{cap}", True, _TEXT), (SUMMARY_RECT.x + 10, y))
        y += 20
    confirm_color = _OK if not overweight else (90, 60, 60)
    pygame.draw.rect(surface, confirm_color, CONFIRM_RECT, border_radius=4)
    surface.blit(font.render("CONFIRM", True, _TEXT), (CONFIRM_RECT.x + 18, CONFIRM_RECT.y + 8))
    pygame.draw.rect(surface, _SLOT, CANCEL_RECT, border_radius=4)
    surface.blit(font.render("CANCEL", True, _TEXT), (CANCEL_RECT.x + 20, CANCEL_RECT.y + 8))

    # 底部：错误行/提示（FR-2.3 红显）
    pygame.draw.rect(surface, _PANEL, FOOTER_RECT, border_radius=4)
    if state.error_frames > 0:
        state.error_frames -= 1
        msg = state.error_line or ""
        surface.blit(font.render(msg, True, _WARN), (FOOTER_RECT.x + 10, FOOTER_RECT.y + 12))
    else:
        surface.blit(
            font.render("Drag weapons to slots  |  L: return to battle", True, _DIM),
            (FOOTER_RECT.x + 10, FOOTER_RECT.y + 12),
        )

    # 拖拽幽灵图标（FR-4.3 跟随光标）
    if state.phase == "dragging" and state.drag_pos is not None and state.drag_spec_name:
        ghost = pygame.Rect(state.drag_pos[0] - 30, state.drag_pos[1] - 10, 60, 20)
        pygame.draw.rect(surface, _ACCENT, ghost, border_radius=3)
        surface.blit(font.render(state.drag_spec_name, True, (10, 10, 10)), (ghost.x + 4, ghost.y + 3))


def _label(
    surface: pygame.Surface,
    font: pygame.font.Font,
    text: str,
    pos: tuple[int, int],
    color: tuple[int, int, int],
) -> None:
    surface.blit(font.render(text, True, color), pos)
