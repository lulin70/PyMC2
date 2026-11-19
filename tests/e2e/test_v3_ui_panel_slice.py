"""Wave V3 UI 面板 e2e：带 fixture 资产包跑真实战局，验证面板渲染与槽位对齐管线。

程序化 fixture（PIL 现场生成图集 + 合成 buttonlayout1280.fit，真资产零入库）验证
端到端管线：create_game 接线 → run_frame 真实渲染 → 像素断言（面板部件落位、面板
上方仍为战场、原 HUD 状态行收敛）→ 选中机甲 dropteam 槽文字 → 任务名/RP 槽位
（run_frame 透传 game 字段，与 main 接线同路径）→ AUTO 托管条 + show_hints=False
提示槽位收敛 → 回退路径（包无 fit vs 无包 全图字节一致；有面板 vs 无包 不一致）。
真实资产观感由用户真机确认（~/.pymc2/mc2-assets 自动发现）。截图存 docs/assets/uat_v3/。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pygame
import pytest
from PIL import Image

import pymc2.main as main_module
from pymc2.infrastructure.mc2_assets import MC2AssetPack
from pymc2.presentation.renderer import (
    WINDOW_HEIGHT_PX,
    WINDOW_WIDTH_PX,
    Renderer,
)
from pymc2.services.battle_service import BattleService, create_m2_battle

SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "assets" / "uat_v3"
SCREEN_SIZE = (WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX)
# fixture 色板（单色 UV 区让像素断言无需推算）
PALETTE_COLOR = (70, 90, 140, 255)
COMP_TOP_COLOR = (150, 120, 60, 255)
SHEET_BASE_COLOR = (30, 34, 40, 255)
# 画布→屏幕偏移（panel_base_offset((1280,720))）：面板槽位断言坐标的换算基准
OFFSET_X, OFFSET_Y = 128, -48
PX01_SPAWN = (6 * 32 + 16, 10 * 32 + 16)  # px-01 出生 tile 中心（相机保持 (0,0)）

# 与官方 buttonlayout1280.fit [Statics] 全量 23 部件同构的合成布局（段号/坐标/UV
# 对照 docs/V3_DESIGN_20261120.md §2，FileName 映射到三张合成图集；零尺寸/缺图集/
# .tga 后缀大写引用/负坐标补边四类容错样本与官方段号一致）。
SYNTHETIC_FIT = """FITini
// synthetic in-game UI layout fixture (mirrors official buttonlayout1280.fit)
[HiresOffsets]
l xOffset = 128
l yOffset = 256

[ResourcePoints]
l RPTextLeft = 9
l RPTextTop = 746

[HelpText]
l HelpTextLeft = 250
l HelpTextBottom = 601

[Statics]
l staticCount = 23

[Static0]
l XLocation = 331
l YLocation = 603
l Width = 256
l Height = 16
st FileName = "MCUI_TestA"
l UNormal = 0
l VNormal = 0
l UWidth = 256
l VHeight = 16

[Static1]
l XLocation = 867
l YLocation = 561
l Width = 15
l Height = 203
st FileName = "MCUI_TestA"
l UNormal = 0
l VNormal = 16
l UWidth = 15
l VHeight = 203

[Static2]
l XLocation = 908
l YLocation = 410
l Width = 0
l Height = 0
st FileName = "MCUI_Compass"
l UNormal = 0
l VNormal = 0
l UWidth = 0
l VHeight = 0

[Static3]
l XLocation = 800
l YLocation = 600
l Width = 40
l Height = 40
st FileName = "MCUI_Missing"
l UNormal = 0
l VNormal = 0
l UWidth = 40
l VHeight = 40

[Static4]
l XLocation = 227
l YLocation = 556
l Width = 18
l Height = 24
st FileName = "MCUI_TestA"
l UNormal = 196
l VNormal = 16
l UWidth = 18
l VHeight = 24

[Static5]
l XLocation = 848
l YLocation = 561
l Width = 19
l Height = 19
st FileName = "MCUI_TestA"
l UNormal = 214
l VNormal = 16
l UWidth = 19
l VHeight = 19

[Static6]
l XLocation = 212
l YLocation = 556
l Width = 15
l Height = 208
st FileName = "MCUI_TestA"
l UNormal = 233
l VNormal = 16
l UWidth = 15
l VHeight = 208

[Static7]
l XLocation = 0
l YLocation = 556
l Width = 4
l Height = 208
st FileName = "MCUI_TestA"
l UNormal = 248
l VNormal = 16
l UWidth = 4
l VHeight = 208

[Static8]
l XLocation = 855
l YLocation = 546
l Width = 169
l Height = 15
st FileName = "MCUI_TestB.TGA"
l UNormal = 21
l VNormal = 32
l UWidth = 169
l VHeight = 15

[Static9]
l XLocation = 867
l YLocation = 764
l Width = 157
l Height = 4
st FileName = "MCUI_TestA"
l UNormal = 21
l VNormal = 47
l UWidth = 157
l VHeight = 4

[Static10]
l XLocation = 227
l YLocation = 619
l Width = 160
l Height = 149
st FileName = "MCUI_TestA"
l UNormal = 21
l VNormal = 51
l UWidth = 160
l VHeight = 149

[Static11]
l XLocation = 387
l YLocation = 619
l Width = 160
l Height = 149
st FileName = "MCUI_TestA"
l UNormal = 21
l VNormal = 51
l UWidth = 160
l VHeight = 149

[Static12]
l XLocation = 547
l YLocation = 619
l Width = 160
l Height = 149
st FileName = "MCUI_TestA"
l UNormal = 21
l VNormal = 51
l UWidth = 160
l VHeight = 149

[Static13]
l XLocation = 707
l YLocation = 619
l Width = 160
l Height = 149
st FileName = "MCUI_TestA"
l UNormal = 21
l VNormal = 51
l UWidth = 160
l VHeight = 149

[Static14]
l XLocation = 227
l YLocation = 580
l Width = 104
l Height = 39
st FileName = "MCUI_TestA"
l UNormal = 21
l VNormal = 200
l UWidth = 104
l VHeight = 39

[Static15]
l XLocation = 762
l YLocation = 580
l Width = 105
l Height = 39
st FileName = "MCUI_TestA"
l UNormal = 125
l VNormal = 200
l UWidth = 105
l VHeight = 39

[Static16]
l XLocation = 0
l YLocation = 764
l Width = 227
l Height = 4
st FileName = "MCUI_TestA"
l UNormal = 0
l VNormal = 239
l UWidth = 227
l VHeight = 4

[Static17]
l XLocation = 0
l YLocation = 545
l Width = 234
l Height = 11
st FileName = "MCUI_TestA"
l UNormal = 0
l VNormal = 243
l UWidth = 234
l VHeight = 11

[Static18]
l XLocation = 908
l YLocation = 410
l Width = 0
l Height = 0
st FileName = "MCUI_Compass"
l UNormal = 0
l VNormal = 0
l UWidth = 0
l VHeight = 0

[Static19]
l XLocation = 973
l YLocation = 726
l Width = 42
l Height = 36
st FileName = "MCUI_TestB"
l UNormal = 126
l VNormal = 36
l UWidth = 42
l VHeight = 36

[Static20]
l XLocation = 229
l YLocation = 624
l Width = 0
l Height = 0
st FileName = "MCUI_TestC"
l UNormal = 42
l VNormal = 36
l UWidth = 0
l VHeight = 0

[Static21]
l XLocation = 1018
l YLocation = 546
l Width = 0
l Height = 0
st FileName = "MCUI_TestC"
l UNormal = 83
l VNormal = 0
l UWidth = 0
l VHeight = 0

[Static22]
l XLocation = -53
l YLocation = 545
l Width = 57
l Height = 223
st FileName = "mcui_testc"
l UNormal = 0
l VNormal = 0
l UWidth = 57
l VHeight = 223

FITEnd
"""


class StaticRng:
    """固定随机源（恒命中、恒 CT），保证 e2e 结果确定可回归。"""

    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


def make_panel_pack(tmp_path: Path, *, with_fit: bool = True) -> MC2AssetPack:
    """面板 fixture pack：三张 UI 图集（图集 A 的 UV 区涂 palette 色）+ 可选布局文件。

    with_fit 两种取值用不同子目录（回退用例同一 tmp_path 双 pack 互不串目录）。"""
    root = tmp_path / ("panel" if with_fit else "panel_plain")
    art = root / "Data" / "Art"
    sheet_a = Image.new("RGBA", (256, 256), SHEET_BASE_COLOR)
    for x in range(21, 181):
        for y in range(51, 200):
            sheet_a.putpixel((x, y), PALETTE_COLOR)
    art.mkdir(parents=True)
    sheet_a.save(art / "MCUI_TestA.tga")
    Image.new("RGBA", (256, 256), COMP_TOP_COLOR).save(art / "MCUI_TestB.tga")
    Image.new("RGBA", (128, 256), (90, 90, 90, 255)).save(art / "MCUI_TestC.tga")
    if with_fit:
        (art / "buttonlayout1280.fit").write_text(SYNTHETIC_FIT, encoding="ascii")
    pack = MC2AssetPack.load(root)
    assert pack is not None
    return pack


@pytest.fixture()
def game(tmp_path: Path) -> Iterator[main_module.Game]:
    """dummy 驱动下的交互模式战局 + 面板 fixture pack（main 默认接线最小复刻）。"""
    pygame.init()
    screen = pygame.display.set_mode(SCREEN_SIZE)
    service: BattleService = create_m2_battle(rng=StaticRng(0.0))
    yield main_module.create_game(screen, service=service, asset_pack=make_panel_pack(tmp_path))
    pygame.quit()


def save_screenshot(screen: pygame.Surface, name: str) -> Path:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / name
    pygame.image.save(screen, str(path))
    assert path.stat().st_size > 0
    return path


def has_pixel_other_than(
    screen: pygame.Surface, x: int, y: int, width: int, height: int, color: tuple[int, int, int]
) -> bool:
    for offset_y in range(height):
        for offset_x in range(width):
            if screen.get_at((x + offset_x, y + offset_y))[:3] != color:
                return True
    return False


def has_bright_pixel(screen: pygame.Surface, x: int, y: int, width: int, height: int) -> bool:
    """条带内存在亮色像素（r>200：槽位文字亮色；战场草地/路面远低于该亮度）。"""
    for offset_y in range(height):
        for offset_x in range(width):
            if screen.get_at((x + offset_x, y + offset_y))[0] > 200:
                return True
    return False


# 槽位屏幕坐标（画布锚点 + 偏移）
SLOT_TEXT = (227 + OFFSET_X + 8, 619 + OFFSET_Y + 6)  # dropteam 槽 0 第 1 行
STRIP_TEXT = (855 + OFFSET_X + 8, 546 + OFFSET_Y + 1)  # 任务/托管条
RP_TEXT = (9 + OFFSET_X, 746 + OFFSET_Y)  # RP 槽位
HINT_TEXT = (250 + OFFSET_X, 601 + OFFSET_Y - 16)  # 提示槽位（底对齐 → 取文字行）
HUD_LINE = (8, WINDOW_HEIGHT_PX - 26)  # 旧 HUD 状态行（面板模式下应收敛）


def test_panel_slots_in_battle(game: main_module.Game) -> None:
    saved: list[Path] = []
    px01 = game.service.get("px-01")
    assert px01 is not None
    assert game.renderer._panel is not None  # 布局解析成功 → 面板模式

    # --- 步骤 0：开局帧——面板落位 + 上方仍为战场 + 旧 HUD 状态行收敛 ---
    main_module.run_frame(game, 1 / 60)
    assert game.screen.get_at((415, 651))[:3] == PALETTE_COLOR[:3]  # palette 槽 0 内部（避开文字行）
    assert game.screen.get_at((640, 470))[:3] != PALETTE_COLOR[:3]  # 面板上缘之上仍是战场
    assert not has_bright_pixel(game.screen, HUD_LINE[0], HUD_LINE[1], 200, 16)  # 旧状态行不再绘制
    saved.append(save_screenshot(game.screen, "01_opening_panel.png"))

    # --- 步骤 1：选中 px-01——dropteam 槽 0 出现机甲状态文字 ---
    assert game.service.select_at(*PX01_SPAWN)
    main_module.run_frame(game, 1 / 60)  # 相机钳制后仍为 (0,0)：px-01 出生点在视口左半
    assert has_pixel_other_than(game.screen, SLOT_TEXT[0], SLOT_TEXT[1], 140, 14, PALETTE_COLOR[:3])
    saved.append(save_screenshot(game.screen, "02_selected_dropteam_slot.png"))

    # --- 步骤 2：任务名 + RP 注入（run_frame 透传 game 字段，与 main 接线同路径）---
    game.mission_id = "m1"
    game.requisition_points = 120
    main_module.run_frame(game, 1 / 60)
    assert has_bright_pixel(game.screen, RP_TEXT[0], RP_TEXT[1], 90, 16)  # RP 槽位文字
    assert has_pixel_other_than(game.screen, STRIP_TEXT[0], STRIP_TEXT[1], 160, 14, COMP_TOP_COLOR[:3])
    assert has_bright_pixel(game.screen, HINT_TEXT[0], HINT_TEXT[1], 320, 16)  # 键位提示收进面板
    saved.append(save_screenshot(game.screen, "03_mission_rp_slots.png"))

    # --- 步骤 3：托管条 + 提示收敛（render 直驱：auto_command/show_hints 全路径）---
    game.renderer.render(game.service, 0, 0, auto_command=True, show_hints=False)
    assert has_pixel_other_than(game.screen, STRIP_TEXT[0], STRIP_TEXT[1], 160, 14, COMP_TOP_COLOR[:3])
    assert not has_bright_pixel(game.screen, HINT_TEXT[0], HINT_TEXT[1], 320, 16)  # show_hints=False 收敛
    saved.append(save_screenshot(game.screen, "04_auto_command_strip.png"))

    # --- 验收留痕：4 张截图全部落盘 ---
    assert len(saved) == 4
    assert all(p.exists() and p.stat().st_size > 0 for p in saved)


def test_fallback_path_byte_identical(tmp_path: Path) -> None:
    """回退路径：包无 fit → 与无资产包渲染逐字节一致；面板 pack → 输出改变。"""
    pygame.init()
    try:
        screen = pygame.display.set_mode(SCREEN_SIZE)
        renderer_plain = Renderer(screen)
        renderer_fallback = Renderer(screen, asset_pack=make_panel_pack(tmp_path, with_fit=False))
        renderer_panel = Renderer(screen, asset_pack=make_panel_pack(tmp_path))
        assert renderer_fallback._panel is None and renderer_panel._panel is not None
        service_a = create_m2_battle(rng=StaticRng(0.0))
        service_b = create_m2_battle(rng=StaticRng(0.0))
        service_c = create_m2_battle(rng=StaticRng(0.0))
        renderer_plain.render(service_a, 0, 0)
        plain = pygame.image.tobytes(screen, "RGB")
        renderer_fallback.render(service_b, 0, 0)
        assert pygame.image.tobytes(screen, "RGB") == plain  # 回退逐字节不变
        renderer_panel.render(service_c, 0, 0)
        assert pygame.image.tobytes(screen, "RGB") != plain  # 面板模式输出改变
        save_screenshot(screen, "05_fallback_hud.png")
    finally:
        pygame.quit()
