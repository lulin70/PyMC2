"""Vehicle 载具实体（P2 单位谱系）：整体 HP + 无热量反应堆 + 可移动。

MC2 参照语义（docs/P2_DESIGN_20261121.md §2.2）：
- 载具无部位伤害：apply_damage 忽略 location（签名与 Unit 协议对齐，弹丸按部位结算
  路径复用零分支），直接扣减整体 hp；alive = hp > 0。
- 载具引擎无熔炉（无热量反应堆）：构造时强制武器 heat_per_shot=0（dataclasses.replace
  保留其余参数）——HeatSystem 恒 0，无精度惩罚、永不 shutdown；统一走存量 fire_at
  加热路径（add_heat(0) 幂等），服务层不设无热分支。
- 可移动：沿用 move_target 直线推进（与 Mech 同式），轮式/悬浮机动快于机甲双腿
  （VEHICLE_MOVE_SPEED_PX_S = 120 > 96），无部位损毁 → mobility_penalty 恒 0。

实现 Unit 协议（domain/entities/unit.py，mypy structural 校验）。
"""

from __future__ import annotations

import math
from dataclasses import replace

from pymc2.domain.entities.mech import (
    TILE_SIZE_PX,
    Faction,
    FireMode,
    Weapon,
    WeaponType,
)
from pymc2.domain.entities.mech_chassis import MechLocation
from pymc2.domain.entities.pilot import Pilot
from pymc2.domain.systems.heat import HeatSystem

VEHICLE_MOVE_SPEED_PX_S = 120.0
VEHICLE_DEFAULT_HP = 80


class Vehicle:
    """单台载具：位置（像素 + tile）、整体 HP、无热量、移动与火力指令状态。"""

    def __init__(
        self,
        vehicle_id: str,
        faction: Faction,
        x: float,
        y: float,
        *,
        hp: int = VEHICLE_DEFAULT_HP,
        weapon: Weapon | None = None,
    ) -> None:
        self.id = vehicle_id
        self.faction = faction
        self.x = x
        self.y = y
        # 无热量反应堆：载具武器不产热（缺省 autocannon 直射炮；注入武器也强制归零）。
        resolved = weapon if weapon is not None else Weapon("autocannon", 10, 400.0, 2.0, wtype=WeaponType.BALLISTIC)
        self.weapon = replace(resolved, heat_per_shot=0.0)
        self.hp = float(max(0, hp))
        self.max_hull = max(0, hp)
        self.heat = HeatSystem()
        self.pilot: Pilot | None = None  # 载具无驾驶员（弹道技能系数中性 1.0，M4 口径）
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
        """所在 tile 坐标（由像素换算，与 Mech 同口径）。"""
        return (int(self.x) // TILE_SIZE_PX, int(self.y) // TILE_SIZE_PX)

    @property
    def hull(self) -> float:
        """整体 HP 余量（无部位结构，即血量本身）。"""
        return self.hp

    @property
    def hull_ratio(self) -> float:
        """剩余血量比例 0.0-1.0。"""
        return self.hp / self.max_hull

    @property
    def alive(self) -> bool:
        """整体 HP > 0 即存活（无部位全毁规则）。"""
        return self.hp > 0.0

    @property
    def weapon_operational(self) -> bool:
        """武器可用：整体存活即可用（无部位失效语义）。"""
        return self.alive

    @property
    def weapons(self) -> tuple[Weapon, ...]:
        """单武器恒等（Unit 协议：W2 多武器选择器兼容口径）。"""
        return (self.weapon,)

    def is_location_destroyed(self, location: MechLocation) -> bool:
        """载具无部位结构：恒未摧毁（协议兼容实现）。"""
        return False

    @property
    def mobility_penalty(self) -> float:
        """机动惩罚恒 0：载具无部位损毁传导。"""
        return 0.0

    @property
    def move_speed_px_s(self) -> float:
        """基础移速：轮式/悬浮快于机甲。"""
        return VEHICLE_MOVE_SPEED_PX_S

    @property
    def mobile(self) -> bool:
        """载具可移动（AI 目标优先级按攻击性目标 > 炮塔分派）。"""
        return True

    @property
    def sprite_kind(self) -> str:
        """精灵分派键：UNIT_SPRITE_NAMES["vehicle"] → Storm.tga 实测命中。"""
        return "vehicle"

    def move_to(self, x: float, y: float) -> None:
        """下达移动指令（下个 tick 起以固定速度直线移动）。"""
        self.move_target = (x, y)

    def tick(self, dt: float, *, move_speed_scale: float = 1.0) -> None:
        """推进一帧：武器冷却 + （恒 0 的）散热 + 朝目标点移动；阵亡后静止。"""
        if not self.alive:
            return
        self.weapon.tick(dt)
        self.heat.tick_cooling(dt)
        target = self.move_target
        if target is None:
            return
        tx, ty = target
        dx = tx - self.x
        dy = ty - self.y
        dist = math.hypot(dx, dy)
        step = self.move_speed_px_s * (1.0 - self.mobility_penalty) * move_speed_scale * dt
        if dist <= step:
            self.x, self.y = tx, ty
            self.move_target = None
            return
        self.x += dx / dist * step
        self.y += dy / dist * step

    def apply_damage(self, location: MechLocation, amount: float) -> bool:
        """整体 HP 伤害：location 忽略（无部位传导），扣减量 = min(amount, hp)；返回是否致死。"""
        if amount <= 0:
            return not self.alive
        self.hp = max(0.0, self.hp - amount)
        return not self.alive
