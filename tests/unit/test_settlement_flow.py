"""窗口内结算呈现 + 名册全灭终局单测（用户真机反馈缺口修复）。

覆盖：_defeat_is_terminal 终局判定（空名册软锁回归）、_settlement_overlay
文案映射、draw_settlement_overlay 遮罩像素效果、_show_settlement 事件驱动
（回车提前继续 / QUIT 退出 / 无输入超时自动继续）。dummy 驱动，不依赖资产。
"""

from __future__ import annotations

import pygame
import pytest

import pymc2.main as main_module
from pymc2.presentation.renderer import WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX
from pymc2.services.campaign_state import start_campaign
from pymc2.services.mission_chain import MissionResult

SCREEN_SIZE = (WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX)


def make_result(*, victory: bool) -> MissionResult:
    return MissionResult(
        mission_id="m3_final_assault",
        victory=victory,
        rp_reward=25,
        rp_salvage=9,
        lost_unit_ids=["px-01", "px-02"],
        survivor_ids=[],
    )


@pytest.fixture()
def screen() -> pygame.Surface:
    pygame.init()
    surface = pygame.display.set_mode(SCREEN_SIZE)
    yield surface
    pygame.quit()


# --- 终局判定：名册全灭后不再重试空名册关卡（软锁回归）---


def test_defeat_with_empty_roster_is_terminal() -> None:
    state = start_campaign()
    state.roster.clear()
    assert main_module._defeat_is_terminal(state, make_result(victory=False)) is True


def test_defeat_with_survivors_is_not_terminal() -> None:
    state = start_campaign()  # 默认名册非空
    assert main_module._defeat_is_terminal(state, make_result(victory=False)) is False


def test_victory_with_empty_roster_is_not_terminal() -> None:
    state = start_campaign()
    state.roster.clear()
    assert main_module._defeat_is_terminal(state, make_result(victory=True)) is False


# --- 结算文案 ---


def test_settlement_overlay_verdict_mapping() -> None:
    state = start_campaign()
    title, lines = main_module._settlement_overlay(state, make_result(victory=True))
    assert title == "MISSION ACCOMPLISHED"
    assert any("PRESS ENTER" in line for line in lines)
    title, _lines = main_module._settlement_overlay(state, make_result(victory=False))
    assert title == "MISSION FAILED"


def test_settlement_overlay_no_losses_shows_none() -> None:
    state = start_campaign()
    result = make_result(victory=False)
    result.lost_unit_ids = []
    _title, lines = main_module._settlement_overlay(state, result)
    assert any("LOSSES: none" in line for line in lines)


# --- 遮罩层绘制：像素级验证（变暗 + 有文字渲染）---


def test_settlement_overlay_darkens_screen(screen: pygame.Surface) -> None:
    screen.fill((255, 255, 255))
    renderer = main_module.Renderer(screen)
    renderer.draw_settlement_overlay("MISSION FAILED", ("LOSSES: px-01",))
    center = screen.get_at((SCREEN_SIZE[0] // 2, SCREEN_SIZE[1] // 2))
    # 遮罩 180 透明度压暗纯白底：中心不再接近白色（文字区则更深）
    assert center[:3] != (255, 255, 255)
    assert sum(center[:3]) < 300


# --- 结算等待循环：事件驱动 ---


def test_show_settlement_returns_on_confirm_key(screen: pygame.Surface) -> None:
    game = main_module.Game(
        screen=screen,
        service=main_module.create_m2_battle(),
        renderer=main_module.Renderer(screen),
    )
    clock = pygame.time.Clock()
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN))
    main_module._show_settlement(game, "MISSION FAILED", ("PRESS ENTER",), clock, timeout_s=5.0)
    assert game.running  # 正常继续路径：不退出


def test_show_settlement_quits_on_quit_event(screen: pygame.Surface) -> None:
    game = main_module.Game(
        screen=screen,
        service=main_module.create_m2_battle(),
        renderer=main_module.Renderer(screen),
    )
    clock = pygame.time.Clock()
    pygame.event.post(pygame.event.Event(pygame.QUIT))
    main_module._show_settlement(game, "MISSION FAILED", (), clock, timeout_s=5.0)
    assert not game.running


def test_show_settlement_timeout_auto_continue(screen: pygame.Surface) -> None:
    game = main_module.Game(
        screen=screen,
        service=main_module.create_m2_battle(),
        renderer=main_module.Renderer(screen),
    )
    clock = pygame.time.Clock()
    main_module._show_settlement(game, "MISSION FAILED", (), clock, timeout_s=0.05)
    assert game.running  # 超时自动继续：不误伤运行态
