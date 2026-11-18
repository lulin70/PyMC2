"""main.py 冒烟测试：dummy 驱动下空跑 60 帧不崩 + QUIT 正常退出（资产探测隔离）。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pygame
import pytest

import pymc2.main as main_module
from pymc2.infrastructure.mc2_assets import ASSETS_ENV_VAR
from pymc2.presentation.renderer import WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX


@pytest.fixture()
def screen() -> Iterator[pygame.Surface]:
    pygame.init()
    yield pygame.display.set_mode((WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX))
    pygame.quit()


@pytest.fixture()
def isolated_asset_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离资产自动发现：env 清空 + HOME 指向空目录（冒烟绝不读本机真实 MC2 资产仓库）。"""
    monkeypatch.delenv(ASSETS_ENV_VAR, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def test_main_runs_60_frames_without_crash(screen: pygame.Surface) -> None:
    game = main_module.create_game(screen)
    for _ in range(60):
        main_module.run_frame(game, 1 / 60)
    assert game.frames == 60
    assert game.running is True
    assert game.service.projectiles == []  # 首轮自动交战弹丸（约 0.8s 飞行）已落地


def test_main_exits_on_quit_event(screen: pygame.Surface, isolated_asset_env: None) -> None:
    pygame.event.post(pygame.event.Event(pygame.QUIT))
    assert main_module.main() == 0
