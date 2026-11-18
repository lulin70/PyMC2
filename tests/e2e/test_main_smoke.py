"""main.py 冒烟测试：dummy 驱动下空跑 60 帧不崩 + QUIT 正常退出。"""

from __future__ import annotations

from collections.abc import Iterator

import pygame
import pytest

import pymc2.main as main_module
from pymc2.presentation.renderer import WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX


@pytest.fixture()
def screen() -> Iterator[pygame.Surface]:
    pygame.init()
    yield pygame.display.set_mode((WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX))
    pygame.quit()


def test_main_runs_60_frames_without_crash(screen: pygame.Surface) -> None:
    game = main_module.create_game(screen)
    for _ in range(60):
        main_module.run_frame(game, 1 / 60)
    assert game.frames == 60
    assert game.running is True
    assert game.service.projectiles == []


def test_main_exits_on_quit_event(screen: pygame.Surface) -> None:
    pygame.event.post(pygame.event.Event(pygame.QUIT))
    assert main_module.main() == 0
