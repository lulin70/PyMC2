"""W2 E2E：装配编辑器截图生成器（无头模式，docs/W2_TEST_PLAN_20261128.md §3）。

用法：
    .venv/bin/python scripts/test_mech_lab_screenshots.py
产出 3 张 → docs/assets/uat_w2/：
    mech_lab_initial.png    三栏初始界面（武器库 ≥6 条目）
    mech_lab_dragging.png   拖拽中（幽灵图标跟随光标）
    mech_lab_overweight.png 超重红显（吨位条红 + error_line）
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from pymc2.domain.loadout import (  # noqa: E402
    ARMOR_CAPS,
    LoadoutConfig,
    WeaponSlotConfig,
)
from pymc2.presentation.mech_lab import (  # noqa: E402
    MechLabState,
    draw_mech_lab,
)
from pymc2.services.loadout_service import LoadoutService  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "assets" / "uat_w2"


def _save(surface: pygame.Surface, name: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    pygame.image.save(surface, str(path))
    print(f"Screenshot saved: {path}")


def main() -> None:
    pygame.init()
    screen = pygame.display.set_mode((1280, 720))
    pygame.display.set_caption("W2 E2E: Mech Lab")
    state = MechLabState(LoadoutService(root=OUT_DIR.parent.parent / ".tmp_loadouts"))

    # 1) 初始界面
    draw_mech_lab(screen, state)
    pygame.display.flip()
    _save(screen, "mech_lab_initial.png")

    # 2) 拖拽中：注入 DRAGGING 态（幽灵图标跟随光标 → FR-4.3）
    state.phase = "dragging"
    state.drag_spec_name = "PPC"
    state.drag_pos = (500, 150)
    draw_mech_lab(screen, state)
    pygame.display.flip()
    _save(screen, "mech_lab_dragging.png")
    state.phase = "idle"
    state.drag_spec_name = None
    state.drag_pos = None

    # 3) 超重红显（FR-2.3：吨位条红 + 确认禁用色 + error_line 红显）
    state.config = LoadoutConfig(
        chassis_kind="light",
        armor_allocation={loc: cap for loc, cap in ARMOR_CAPS.items()},
        weapons=[
            WeaponSlotConfig("AC-10", "ct"),
            WeaponSlotConfig("PPC", "ra"),
            WeaponSlotConfig("LRM-20", "la"),
        ],
    )
    state.show_error(f"OVERWEIGHT: total {state.config.total_tonnage():.1f}t exceeds 35.0t")
    draw_mech_lab(screen, state)
    pygame.display.flip()
    _save(screen, "mech_lab_overweight.png")

    pygame.quit()


if __name__ == "__main__":
    main()
