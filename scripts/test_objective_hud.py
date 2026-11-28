"""W1 E2E：HUD 目标面板截图生成器（无头模式）。

用法：
    SDL_VIDEODRIVER=dummy .venv/bin/python scripts/test_objective_hud.py \
        --status pending --screenshot docs/assets/uat_w1/objective_panel_pending.png
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

# 必须在 pygame 初始化前设置（无头环境）
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from pymc2.domain.scripting.objective import (  # noqa: E402
    Objective,
    ObjectiveSet,
    ObjectiveStatus,
    ObjectiveType,
)
from pymc2.presentation.renderer import Renderer  # noqa: E402
from pymc2.services.battle_service import BattleService  # noqa: E402


def generate_test_objectives(status_scenario: str) -> ObjectiveSet:
    """生成测试 ObjectiveSet（3 PRIMARY + 2 SECONDARY）。"""
    if status_scenario == "pending":
        objectives = [
            Objective("p1", ObjectiveType.PRIMARY, "Destroy enemy base", None, ObjectiveStatus.PENDING),
            Objective("p2", ObjectiveType.PRIMARY, "Eliminate all hostiles", None, ObjectiveStatus.PENDING),
            Objective("p3", ObjectiveType.PRIMARY, "Defend allied units", None, ObjectiveStatus.PENDING),
            Objective("s1", ObjectiveType.SECONDARY, "Capture resource points", None, ObjectiveStatus.PENDING),
            Objective("s2", ObjectiveType.SECONDARY, "Rescue stranded pilot", None, ObjectiveStatus.PENDING),
        ]
    elif status_scenario == "completed":
        objectives = [
            Objective("p1", ObjectiveType.PRIMARY, "Destroy enemy base", None, ObjectiveStatus.COMPLETED),
            Objective("p2", ObjectiveType.PRIMARY, "Eliminate all hostiles", None, ObjectiveStatus.COMPLETED),
            Objective("p3", ObjectiveType.PRIMARY, "Defend allied units", None, ObjectiveStatus.PENDING),
            Objective("s1", ObjectiveType.SECONDARY, "Capture resource points", None, ObjectiveStatus.COMPLETED),
            Objective("s2", ObjectiveType.SECONDARY, "Rescue stranded pilot", None, ObjectiveStatus.COMPLETED),
        ]
    elif status_scenario == "failed":
        objectives = [
            Objective("p1", ObjectiveType.PRIMARY, "Destroy enemy base", None, ObjectiveStatus.FAILED),
            Objective("p2", ObjectiveType.PRIMARY, "Eliminate all hostiles", None, ObjectiveStatus.PENDING),
            Objective("p3", ObjectiveType.PRIMARY, "Defend allied units", None, ObjectiveStatus.PENDING),
            Objective("s1", ObjectiveType.SECONDARY, "Capture resource points", None, ObjectiveStatus.COMPLETED),
            Objective("s2", ObjectiveType.SECONDARY, "Rescue stranded pilot", None, ObjectiveStatus.FAILED),
        ]
    else:
        raise ValueError(f"Unknown status scenario: {status_scenario}")

    return ObjectiveSet(objectives=objectives)


def main() -> None:
    parser = argparse.ArgumentParser(description="W1 HUD 目标面板截图生成器")
    parser.add_argument(
        "--status",
        choices=["pending", "completed", "failed"],
        required=True,
        help="目标状态场景",
    )
    parser.add_argument("--screenshot", type=Path, required=True, help="截图保存路径")
    args = parser.parse_args()

    pygame.init()
    screen = pygame.display.set_mode((1280, 720))
    pygame.display.set_caption(f"W1 E2E: Objective HUD ({args.status})")

    objectives = generate_test_objectives(args.status)
    renderer = Renderer(screen, asset_pack=None, objectives=objectives)
    battle = BattleService()

    renderer.render(battle, 0.0, 0.0)
    pygame.display.flip()

    args.screenshot.parent.mkdir(parents=True, exist_ok=True)
    pygame.image.save(screen, str(args.screenshot))
    print(f"Screenshot saved: {args.screenshot}")

    pygame.quit()


if __name__ == "__main__":
    main()
