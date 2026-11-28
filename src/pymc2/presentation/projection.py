"""W3 斜投影 MVP：固定倾角仿射变换（纯函数，零 pygame 依赖）。

设计（docs/W3_DESIGN_20261128.md §2）：
- 投影仅渲染层：逻辑层（命中/LOS/移动/热量）世界坐标不变，本模块只被
  presentation/renderer.py 与 main.py 拾取入口消费（依赖单向 main → renderer → projection）。
- 固定倾角 30° 仿射：纵向压缩 cos(30°)，无透视缩放（28-60° FOV 连续可调属远期池）。
- 常量单处定义（NFR 可维护性）：倾角/压缩系数禁止魔法数字散落。
- 顶视（top）恒等存量公式：int(x - cam_x), int(y - cam_y)——top 模式全链路逐字节一致
  （FR-4.2 回归钉），isometric 仅在 Renderer/拾取入口按模式分支。
"""

from __future__ import annotations

import math

PROJECTION_TOP = "top"
PROJECTION_ISO = "isometric"

# 固定倾角（MVP 定值，M2 里程碑观感校准点；仅此一处可调）。
ISO_TILT_DEG = 30.0
# 纵向压缩系数：倾斜 30° 时世界 y 轴在屏幕上的投影缩短比。
ISO_Y_SCALE = math.cos(math.radians(ISO_TILT_DEG))


def world_to_screen_iso(x: float, y: float, cam_x: float, cam_y: float) -> tuple[int, int]:
    """isometric 正变换：screen_x = x - cam_x；screen_y = (y - cam_y) × ISO_Y_SCALE。

    高程项不在此处理——调用侧（Renderer）按 tile 统一叠加压缩后偏移，与丘陵地形
    表面整面缩放的烘焙偏移保持同一压缩系数（设计 §3 一致性推导）。
    """
    return (int(x - cam_x), int((y - cam_y) * ISO_Y_SCALE))


def screen_to_world_iso(sx: float, sy: float, cam_x: float, cam_y: float) -> tuple[float, float]:
    """isometric 逆变换（平坦地形精确；高程项由 Renderer 两段式回补，设计 §4）。

    wy = sy / ISO_Y_SCALE + cam_y（忽略高程的系统误差 = e × STEP × K 像素，
    由 Renderer.screen_to_world 一次查表回补，不迭代）。
    """
    return (sx + cam_x, sy / ISO_Y_SCALE + cam_y)
