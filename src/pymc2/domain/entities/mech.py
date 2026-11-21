"""Mech 实体：最小战术单元（M1 移动/开火 + M3 底盘/热量）。

坐标约定：
- position 为世界像素坐标（浮点），地图 40x40 tile、每 tile 32px。
- tile 由像素坐标换算得出，不单独存储。

M3 起机甲由 MechChassis 承载八部位装甲/内部结构：
- hull 为派生视图（底盘装甲+结构总余量，四舍五入到 6 位小数消浮点尾差），
  字段语义不变（聚合血量），保持 M1/M2 测试兼容。
- alive 由摧毁规则派生：CT/HEAD 摧毁 → 机甲全毁。
- 热量经 HeatSystem 累积/散热（开火产热、阈值精度惩罚与强制关机）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from pymc2.domain.entities.mech_chassis import MechChassis, MechLocation
from pymc2.domain.entities.pilot import Pilot
from pymc2.domain.systems.heat import HeatSystem

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


class WeaponType(Enum):
    """武器三分类（P2，MC2 武器体系简化）：弹速/伤害剖面差异见 ballistic 模块常量表。

    BALLISTIC 直射快弹（部位权重判定）；ENERGY 恒 CT 直击（无视部位加成）；
    MISSILE 三发小伤齐射 + 末端加速。缺省 BALLISTIC = 存量武器语义不变。
    """

    BALLISTIC = "ballistic"
    ENERGY = "energy"
    MISSILE = "missile"


@dataclass
class Weapon:
    """直射武器：伤害固定，命中判定在 ballistic 系统完成。

    mounted_location 为挂载部位（躯干/手臂）：部位摧毁则该武器失效。
    heat_per_shot 为每次开火产热（M3 热量系统）。
    wtype 为武器三分类（P2）：缺省 BALLISTIC 保持存量语义。
    """

    name: str
    damage: int
    range_px: float
    cooldown_s: float
    cd_remaining: float = 0.0
    mounted_location: MechLocation = MechLocation.CT
    heat_per_shot: float = 5.0
    wtype: WeaponType = WeaponType.BALLISTIC

    def tick(self, dt: float) -> None:
        """推进冷却计时，不低于 0。"""
        if self.cd_remaining > 0.0:
            self.cd_remaining = max(0.0, self.cd_remaining - dt)

    @property
    def ready(self) -> bool:
        """冷却结束、可以开火。"""
        return self.cd_remaining <= 0.0


class Mech:
    """单台机甲：位置（像素 + tile）、底盘（部位装甲/结构/武器槽）、热量与指令。"""

    def __init__(
        self,
        mech_id: str,
        faction: Faction,
        x: float,
        y: float,
        *,
        hull: int = 100,
        weapon: Weapon | None = None,
        chassis: MechChassis | None = None,
        pilot: Pilot | None = None,
    ) -> None:
        self.id = mech_id
        self.faction = faction
        self.x = x
        self.y = y
        self.weapon = weapon if weapon is not None else Weapon("standard", 10, 400.0, 2.0)
        # 未注入底盘时按 hull 总余量缩放中型模板构建（M1/M2 兼容口径）。
        self.chassis = chassis if chassis is not None else MechChassis.from_total(hull)
        self.chassis.mount_weapon(self.weapon)
        self.max_hull = int(round(self.chassis.total_integrity))
        self.heat = HeatSystem()
        # 驾驶员可选（缺省无驾驶员 → 弹道技能系数中性 1.0，M1/M2 兼容）。
        self.pilot = pilot
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
    def hull(self) -> float:
        """派生视图：底盘装甲+内部结构总余量（原聚合血量字段，语义不变）。"""
        return round(self.chassis.total_integrity, 6)

    @property
    def hull_ratio(self) -> float:
        """剩余装甲比例 0.0-1.0。"""
        return self.hull / self.max_hull

    @property
    def alive(self) -> bool:
        """摧毁规则派生：CT/HEAD 完好即存活（任一摧毁 → 机甲全毁）。"""
        return not (self.chassis.is_destroyed(MechLocation.CT) or self.chassis.is_destroyed(MechLocation.HEAD))

    @property
    def weapon_operational(self) -> bool:
        """武器可用：挂载部位未摧毁（LA/RA 摧毁 → 该臂武器失效）。"""
        return not self.chassis.is_destroyed(self.weapon.mounted_location)

    # ------------------------------------------------------------------ Unit 协议增量（P2，纯加法）

    @property
    def mobility_penalty(self) -> float:
        """机动惩罚（Unit 协议）：LL/RL/侧躯损毁折算，委托底盘。"""
        return self.chassis.mobility_penalty

    @property
    def move_speed_px_s(self) -> float:
        """基础移速（Unit 协议）：机甲恒为模块常量（预测数学读协议字段，数值不变）。"""
        return MOVE_SPEED_PX_S

    @property
    def mobile(self) -> bool:
        """机甲可移动（Unit 协议）：move_mech 门槛恒放行（存量语义不变）。"""
        return True

    @property
    def sprite_kind(self) -> str:
        """精灵分派键（Unit 协议）：机甲沿用 id → CHASSIS_SPRITE_NAMES 存量链。"""
        return self.id

    def move_to(self, x: float, y: float) -> None:
        """下达移动指令（下个 tick 起以固定速度直线移动）。"""
        self.move_target = (x, y)

    def tick(self, dt: float, *, move_speed_scale: float = 1.0) -> None:
        """推进一帧：武器冷却 + 热量散热 + 朝目标点移动；阵亡后静止。

        move_speed_scale 为移动步长加成（P1-A 上坡耗时，战斗层按高程差计算传入；
        默认 1.0，实体自身无高程依赖，存量调用语义不变）。
        """
        if not self.alive:
            return
        self.weapon.tick(dt)
        self.heat.tick_cooling(dt)
        target = self.move_target
        if target is not None:
            self._advance_move(dt, target, move_speed_scale)

    def _advance_move(self, dt: float, target: tuple[float, float], speed_scale: float = 1.0) -> None:
        tx, ty = target
        dx = tx - self.x
        dy = ty - self.y
        dist = math.hypot(dx, dy)
        step = MOVE_SPEED_PX_S * (1.0 - self.chassis.mobility_penalty) * speed_scale * dt
        if dist <= step:
            self.x, self.y = tx, ty
            self.move_target = None
            return
        self.x += dx / dist * step
        self.y += dy / dist * step

    def take_damage(self, amount: float) -> bool:
        """遗留聚合伤害：等比摊到全机装甲/结构，总余量精确扣减。

        兼容口径：扣减量 = min(amount, 总余量)；仅当总余量归零时致死
        （部位级结算走 apply_damage，勿混用两套记账）。
        """
        total = self.chassis.total_integrity
        if total <= 0.0 or amount <= 0:
            return not self.alive
        self.chassis.scale_integrity(max(0.0, (total - amount) / total))
        return not self.alive

    def apply_damage(self, location: MechLocation, amount: float) -> bool:
        """部位伤害：弹着部位先扣装甲再透结构，溢出沿传导链下移；返回是否致死。"""
        self.chassis.apply_damage(location, amount)
        return not self.alive
