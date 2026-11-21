"""P4 朝向帧 e2e：fixture 资产包（合成 TestMech .ase）跑真实战局，验证朝向选帧与回退。

端到端管线（真 MC2 资产零入库，箭头网格 fixture）：create_game 接线（madcat →
TestMech.ase 朝向帧）→ 开局自动交战锁定东向目标 → 域状态 heading 量化为东桶 + 渲染
像素走朝向帧（箭头纹理色 + 脚影）→ 南移到达后朝向南桶且攻击朝向重算（NE 桶）→
无 .ase 回退静态卡（像素断言，存量输出不变）。
截图存 docs/assets/uat_p4/。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pygame
import pytest

import pymc2.main as main_module
from helpers.ase_fixture import STATIC_CARD_COLOR, write_arrow_ase, write_arrow_texture, write_static_card
from pymc2.domain.entities.mech import heading_bucket
from pymc2.infrastructure.mc2_assets import MC2AssetPack
from pymc2.presentation.renderer import WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX
from pymc2.services.battle_service import BattleService, create_m2_battle

SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "assets" / "uat_p4"
SCREEN_SIZE = (WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX)
# px-01 出生 tile (6,10) 中心 (208,336)；精灵 32px 覆盖 (192,320)-(224,352)。
# 脚影椭圆中心 = 精灵中心 y+8 → (208,344)（朝向帧内容集中在上 2/3，该点为影/草地混合
# 区）；弹丸三型色（黄 b120、青 g220、橙 b60）均不命中箭头紫判据（r>150、b>150、g<120）。
PX01_CENTER = (208, 336)


class SequenceRng:
    """交替随机源（P2 e2e 同款）：开火判定 0.0（恒命中）、部位判定 0.5（恒 RT 非
    致死部位）——隔离 CT 集火速杀对切片节奏的干扰，机甲全程存活窗口确定。"""

    def __init__(self) -> None:
        self._values = (0.0, 0.5)
        self._index = 0

    def _next(self) -> float:
        value = self._values[self._index % len(self._values)]
        self._index += 1
        return value

    def random(self) -> float:
        return self._next()

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


def make_heading_pack(tmp_path: Path) -> MC2AssetPack:
    """朝向帧 fixture pack：TestMech.ase + 配套紫纹理 + 静态卡（无地面纹理 → 程序化地形）。"""
    root = tmp_path / "heading"
    write_arrow_ase(root / "Data" / "TGL" / "MadCat.ase")
    write_arrow_texture(root / "Data" / "TGL" / "128" / "MadcatRGB.tga")
    write_static_card(root / "Data" / "TGL" / "128" / "MadCat.tga")
    pack = MC2AssetPack.load(root)
    assert pack is not None
    return pack


def make_static_pack(tmp_path: Path) -> MC2AssetPack:
    """静态卡-only fixture pack（无 .ase）：回退路径用。"""
    root = tmp_path / "static"
    write_static_card(root / "Data" / "TGL" / "128" / "MadCat.tga")
    pack = MC2AssetPack.load(root)
    assert pack is not None
    return pack


def arrow_pixels(screen: pygame.Surface, center: tuple[int, int], size: int = 32) -> int:
    """精灵盒内箭头纹理色（紫）像素计数：r>150、b>150、g<120（弹丸三型色均不命中）。"""
    cx, cy = center
    half = size // 2
    return sum(
        1
        for x in range(cx - half, cx + half)
        for y in range(cy - half, cy + half)
        if (p := screen.get_at((x, y))).r > 150 and p.b > 150 and p.g < 120
    )


@pytest.fixture()
def heading_game(tmp_path: Path) -> Iterator[main_module.Game]:
    """dummy 驱动战局 + 朝向帧 fixture pack（无指挥官：仅自动交战自然锁定）。"""
    pygame.init()
    screen = pygame.display.set_mode(SCREEN_SIZE)
    service: BattleService = create_m2_battle(rng=SequenceRng())
    yield main_module.create_game(screen, service=service, asset_pack=make_heading_pack(tmp_path))
    pygame.quit()


@pytest.fixture()
def static_game(tmp_path: Path) -> Iterator[main_module.Game]:
    """dummy 驱动战局 + 静态卡-only pack（回退路径）。"""
    pygame.init()
    screen = pygame.display.set_mode(SCREEN_SIZE)
    service: BattleService = create_m2_battle(rng=SequenceRng())
    yield main_module.create_game(screen, service=service, asset_pack=make_static_pack(tmp_path))
    pygame.quit()


def save_screenshot(screen: pygame.Surface, name: str) -> Path:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / name
    pygame.image.save(screen, str(path))
    assert path.stat().st_size > 0
    return path


def test_heading_frames_in_battle(heading_game: main_module.Game) -> None:
    game = heading_game
    px = game.service.get("px-01")
    assert px is not None
    saved: list[Path] = []

    # --- 步骤 1：开局自动交战锁定东向目标（ex-01 在同排 384px < 射程 420）→ 朝向目标东桶 ---
    main_module.run_frame(game, 1 / 60)
    assert px.attack_target_id == "ex-01"
    assert px.heading == 2  # 目标正东 → bucket 2
    assert arrow_pixels(game.screen, PX01_CENTER) > 0  # 渲染走朝向帧（箭头紫，非静态卡绿）
    assert game.renderer._heading_frames_active(px)
    saved.append(save_screenshot(game.screen, "01_attack_facing_east.png"))

    # --- 步骤 2：南移——移动中朝向 = 移动方向南桶（移动优先于攻击目标）；到达帧即被
    # 攻击朝向重算覆盖（同 tick 语义：move_target 清空 → _update_attack_headings 接管）---
    game.service.move_mech("px-01", float(PX01_CENTER[0]), 500.0)  # 正南 164px
    for _ in range(15):
        main_module.run_frame(game, 1 / 30)
        assert px.move_target is not None  # 164px/81.6px/s ≈ 2.0s，前 0.5s 必在移动中
        assert px.heading == 4  # 移动方向决定朝向：正南 → bucket 4
    assert arrow_pixels(game.screen, (PX01_CENTER[0], int(px.y))) > 0  # 移动中朝向帧生效
    saved.append(save_screenshot(game.screen, "02_moving_facing_south.png"))
    for _ in range(90):
        main_module.run_frame(game, 1 / 30)
        if px.move_target is None:
            break
    assert px.move_target is None and px.y == 500.0

    # --- 步骤 3：静止待击 → 朝向重算回攻击目标（东北桶，量化函数对齐）---
    for _ in range(5):
        main_module.run_frame(game, 1 / 60)
    ex = game.service.get("ex-01")
    assert ex is not None
    expected = heading_bucket(ex.x - px.x, ex.y - px.y)
    assert px.heading == expected  # 攻击目标方向决定静止朝向
    assert arrow_pixels(game.screen, (PX01_CENTER[0], 500)) > 0  # 朝向帧仍生效
    saved.append(save_screenshot(game.screen, "03_attack_facing_recomputed.png"))

    # --- 验收留痕：3 张截图全部落盘 ---
    assert len(saved) == 3
    assert all(p.exists() and p.stat().st_size > 0 for p in saved)


def test_fallback_without_ase_keeps_static_card(static_game: main_module.Game) -> None:
    """无 .ase 回退：渲染走静态卡（存量输出），朝向域状态照常更新。"""
    game = static_game
    px = game.service.get("px-01")
    assert px is not None
    main_module.run_frame(game, 1 / 60)
    assert px.heading == 2  # 域状态照常（朝目标东）
    assert not game.renderer._heading_frames_active(px)
    # 静态卡命中：中心像素 = fixture 绿色；且无脚影（影区像素 = 纯草地混合前后一致由集成覆盖）
    assert game.screen.get_at(PX01_CENTER)[:3] == STATIC_CARD_COLOR[:3]
    saved = save_screenshot(game.screen, "04_fallback_static_card.png")
    assert saved.exists()
