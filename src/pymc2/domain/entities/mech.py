"""Mech 实体：M1 垂直切片的最小战术单元。

坐标约定：
- position 为世界像素坐标（浮点），地图 40x40 tile、每 tile 32px。
- tile 由像素坐标换算得出，不单独存储。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

TILE_SIZE_PX = 32
MAP_TILES = 40
MAP_SIZE_PX = MAP_TILES * TILE_SIZE_PX
MOVE_SPEED_PX_S = 96.0


class Faction(Enum):
    """阵营：玩家 / 敌方。"""

    PLAYER = "player"
    ENEMY = "enemy"


class FireMode(Enum):
    """火力姿态（MC2 fire mode 简化版）：FREE 自由开火 / HOLD 保持火力。"""

    FREE = "free"
    HOLD = "hold"


@dataclass
class Weapon:
    """直射武器：伤害固定，命中判定在 ballistic 系统完成。"""

    name: str
    damage: int
    range_px: float
    cooldown_s: float
    cd_remaining: float = 0.0

    def tick(self, dt: float) -> None:
        """推进冷却计时，不低于 0。"""
        if self.cd_remaining > 0.0:
            self.cd_remaining = max(0.0, self.cd_remaining - dt)

    @property
    def ready(self) -> bool:
        """冷却结束、可以开火。"""
        return self.cd_remaining <= 0.0


class Mech:
    """单台机甲：位置（像素 + tile）、装甲值、武器与移动指令。"""

    def __init__(
        self,
        mech_id: str,
        faction: Faction,
        x: float,
        y: float,
        *,
        hull: int = 100,
        weapon: Weapon | None = None,
    ) -> None:
        self.id = mech_id
        self.faction = faction
        self.x = x
        self.y = y
        self.max_hull = hull
        self.hull = hull
        self.alive = True
        self.weapon = weapon if weapon is not None else Weapon("standard", 10, 400.0, 2.0)
        self.move_target: tuple[float, float] | None = None
        self.fire_mode = FireMode.FREE
        self.guarding = False
        self.attack_target_id: str | None = None

    @property
    def position(self) -> tuple[float, float]:
        """世界像素坐标。"""
        return (self.x, self.y)

    @property
    def tile(self) -> tuple[int, int]:
        """所在 tile 坐标（由像素换算）。"""
        return (int(self.x) // TILE_SIZE_PX, int(self.y) // TILE_SIZE_PX)

    @property
    def hull_ratio(self) -> float:
        """剩余装甲比例 0.0-1.0。"""
        return self.hull / self.max_hull

    def move_to(self, x: float, y: float) -> None:
        """下达移动指令（下个 tick 起以固定速度直线移动）。"""
        self.move_target = (x, y)

    def tick(self, dt: float) -> None:
        """推进一帧：武器冷却 + 朝目标点移动；阵亡后静止。"""
        if not self.alive:
            return
        self.weapon.tick(dt)
        target = self.move_target
        if target is not None:
            self._advance_move(dt, target)

    def _advance_move(self, dt: float, target: tuple[float, float]) -> None:
        tx, ty = target
        dx = tx - self.x
        dy = ty - self.y
        dist = math.hypot(dx, dy)
        step = MOVE_SPEED_PX_S * dt
        if dist <= step:
            self.x, self.y = tx, ty
            self.move_target = None
            return
        self.x += dx / dist * step
        self.y += dy / dist * step

    def take_damage(self, amount: int) -> bool:
        """结算伤害，装甲下限钳制到 0；返回是否致死。"""
        self.hull = max(0, self.hull - amount)
        if self.hull == 0:
            self.alive = False
        return not self.alive
