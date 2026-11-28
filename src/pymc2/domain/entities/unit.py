"""Unit 协议（P2 单位谱系）：Mech/Vehicle/Turret 的最小共同接口（鸭子类型）。

选型（docs/P2_DESIGN_20261121.md §2.1）：Mech 已承载底盘/热量/驾驶员三套系统，继承重构
会触碰 M1-M7 全部构造路径；Protocol 只做类型收敛——battle_service/AiCommander/renderer/
salvage 按 Unit 泛化，mypy 静态校验三方成员一致，运行时零分支。

协议成员口径：
- 可变状态属性：battle_service/指令链读写的存量字段（fire_mode/guarding/attack_target_id/
  move_target 等），各实体自行持有；
- P2 新增派生 property：mobility_penalty（移动物理）、move_speed_px_s（移动物理）、
  mobile（可移动门槛：move_mech 拒绝 + AI 目标优先级）、sprite_kind（渲染精灵分派）；
- 方法签名与 Mech 现有签名逐字一致（tick 的 move_speed_scale 为 keyword-only 带缺省）。
"""

from __future__ import annotations

from typing import Protocol

from pymc2.domain.entities.mech import Faction, FireMode, Weapon
from pymc2.domain.entities.mech_chassis import MechLocation
from pymc2.domain.entities.pilot import Pilot
from pymc2.domain.systems.heat import HeatSystem


class Unit(Protocol):
    """战术单位最小协议：id/faction/位置/武器/血量比例/移动与伤害入口。"""

    id: str
    faction: Faction
    x: float
    y: float
    max_hull: int
    weapon: Weapon
    move_target: tuple[float, float] | None
    fire_mode: FireMode
    guarding: bool
    attack_target_id: str | None
    heat: HeatSystem
    pilot: Pilot | None

    @property
    def position(self) -> tuple[float, float]: ...

    @property
    def tile(self) -> tuple[int, int]: ...

    @property
    def alive(self) -> bool: ...

    @property
    def hull(self) -> float: ...

    @property
    def hull_ratio(self) -> float: ...

    @property
    def weapon_operational(self) -> bool: ...

    @property
    def weapons(self) -> tuple[Weapon, ...]: ...

    def is_location_destroyed(self, location: MechLocation) -> bool: ...

    @property
    def mobility_penalty(self) -> float: ...

    @property
    def move_speed_px_s(self) -> float: ...

    @property
    def mobile(self) -> bool: ...

    @property
    def sprite_kind(self) -> str: ...

    def tick(self, dt: float, *, move_speed_scale: float = 1.0) -> None: ...

    def move_to(self, x: float, y: float) -> None: ...

    def apply_damage(self, location: MechLocation, amount: float) -> bool: ...
