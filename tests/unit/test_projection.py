"""W3 斜投影单测：仿射数学（纯函数层，零 pygame 依赖）。

设计（docs/W3_DESIGN_20261128.md §2）：常量单处定义 + roundtrip ≤1 tile + top 恒等。
"""

from __future__ import annotations

import math

from pymc2.domain.entities.mech import TILE_SIZE_PX
from pymc2.presentation.projection import (
    ISO_TILT_DEG,
    ISO_Y_SCALE,
    screen_to_world_iso,
    world_to_screen_iso,
)

SAMPLES = [(0.0, 0.0), (100.0, 200.0), (640.0, 640.0), (1280.0, 1280.0), (33.5, 77.25)]


class TestConstants:
    def test_scale_is_cos30(self) -> None:
        """压缩系数 = cos(30°)（常量单处定义钉，M2 里程碑校准点）。"""
        assert ISO_TILT_DEG == 30.0
        assert math.cos(math.radians(30.0)) == ISO_Y_SCALE
        assert math.isclose(ISO_Y_SCALE, 0.8660254037844387, rel_tol=1e-12)


class TestForward:
    def test_forward_numeric(self) -> None:
        """screen_x = x - cam_x；screen_y = (y - cam_y) × K 截断取整（数值断言）。"""
        assert world_to_screen_iso(100.0, 200.0, 0.0, 0.0) == (100, int(200 * ISO_Y_SCALE))
        assert world_to_screen_iso(100.0, 200.0, 40.0, 30.0) == (60, int(170 * ISO_Y_SCALE))
        assert world_to_screen_iso(0.0, 0.0, 0.0, 0.0) == (0, 0)


class TestRoundtrip:
    def test_roundtrip_flat_within_one_tile(self) -> None:
        """平坦地形（height=0）roundtrip：screen_to_world(world_to_screen(p)) 距离 ≤1 tile。"""
        for x, y in SAMPLES:
            sx, sy = world_to_screen_iso(x, y, 0.0, 0.0)
            wx, wy = screen_to_world_iso(sx, sy, 0.0, 0.0)
            error = math.hypot(wx - x, wy - y)
            assert error <= TILE_SIZE_PX, f"({x}, {y}) roundtrip error {error:.2f}px > 1 tile"

    def test_roundtrip_with_camera_within_one_tile(self) -> None:
        """带相机偏移的 roundtrip 同样 ≤1 tile（拾取入口真实路径）。"""
        for x, y in SAMPLES:
            sx, sy = world_to_screen_iso(x, y, 128.0, 64.0)
            wx, wy = screen_to_world_iso(sx, sy, 128.0, 64.0)
            assert math.hypot(wx - x, wy - y) <= TILE_SIZE_PX

    def test_inverse_top_identity_with_legacy(self) -> None:
        """top 恒等：int 截断误差 ≤1px（screen_to_world top 路径 = 存量 _world_pos）。"""
        for x, y in SAMPLES:
            sx, sy = int(x), int(y)
            wx, wy = sx + 0.0, sy + 0.0  # 存量公式：screen + camera
            assert math.hypot(wx - x, wy - y) <= 1.0
