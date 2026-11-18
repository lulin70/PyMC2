"""PyMC2 测试配置：SDL dummy 驱动必须在任何 pygame 导入之前设置。"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("SDL_JOYSTICK_DRIVER", "dummy")
