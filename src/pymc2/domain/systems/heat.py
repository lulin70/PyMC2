"""热量系统（M3）：武器产热、被动散热与阈值效果。

BattleTech 简化版：
- heat 0-100 钳制；散热率 0.5/s 基准（环境加成接口预留，默认关断）。
- heat ≥ 80 → 命中精度惩罚（×0.7，经 ballistic 乘入命中率）。
- heat = 100 → 强制 shutdown 2 秒，期间不可行动（开火/移动被拒）只散热。
"""

from __future__ import annotations

MAX_HEAT = 100.0
COOLING_RATE_PER_S = 0.5
ACCURACY_PENALTY_THRESHOLD = 80.0
ACCURACY_PENALTY_MULTIPLIER = 0.7
SHUTDOWN_DURATION_S = 2.0


class HeatSystem:
    """单机甲热量槽：产热、散热、精度阈值与强制关机计时。"""

    def __init__(self) -> None:
        self.heat = 0.0
        self._shutdown_remaining = 0.0

    def add_heat(self, amount: float) -> None:
        """武器开火产热；钳制 0-100，触顶（100）触发强制 shutdown。"""
        self.heat = min(MAX_HEAT, max(0.0, self.heat + amount))
        if self.heat >= MAX_HEAT:
            self._shutdown_remaining = SHUTDOWN_DURATION_S

    def tick_cooling(self, dt: float, ambient_modifier: float = 0.0) -> None:
        """推进散热（0.5/s 基准）与关机计时。

        ambient_modifier 为环境加成预留接口（热区传负、冷区传正），默认 0。
        """
        rate = max(0.0, COOLING_RATE_PER_S + ambient_modifier)
        self.heat = max(0.0, self.heat - rate * dt)
        if self._shutdown_remaining > 0.0:
            self._shutdown_remaining = max(0.0, self._shutdown_remaining - dt)

    @property
    def accuracy_multiplier(self) -> float:
        """heat ≥ 80 → 0.7 精度惩罚系数；否则 1.0。"""
        if self.heat >= ACCURACY_PENALTY_THRESHOLD:
            return ACCURACY_PENALTY_MULTIPLIER
        return 1.0

    @property
    def shutdown_active(self) -> bool:
        """强制关机进行中（不可行动，只散热）。"""
        return self._shutdown_remaining > 0.0

    @property
    def shutdown_remaining(self) -> float:
        """剩余关机时间（秒）。"""
        return self._shutdown_remaining
