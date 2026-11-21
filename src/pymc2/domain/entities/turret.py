"""Turret 炮塔实体（P2 单位谱系）：整体 HP + 不可移动的固定火力点。

MC2 参照语义（docs/P2_DESIGN_20261121.md §2.3）：炮塔为固定防御工事——整体 HP
（同载具裁剪）、不可移动、武器射程内自动开火。继承 Vehicle 骨架（整体 HP/无热量
同款），覆写三处：mobile=False（move_mech 拒绝 + AI 不后撤不占区）、move_to no-op
（永无移动意图）、sprite_kind="turret"。

自动开火零新增代码：fire_mode 缺省 FREE + BattleService._auto_engage 泛化后天然覆盖
（射程 + LOS 过滤 + dispatcher 下发与机甲同一条路径）——守卫语义一致。
"""

from __future__ import annotations

from pymc2.domain.entities.mech import Faction, Weapon
from pymc2.domain.entities.vehicle import Vehicle

TURRET_DEFAULT_HP = 60


class Turret(Vehicle):
    """单座炮塔：固定火力点（不可移动），整体 HP 与无热量语义同载具。"""

    def __init__(
        self,
        turret_id: str,
        faction: Faction,
        x: float,
        y: float,
        *,
        hp: int = TURRET_DEFAULT_HP,
        weapon: Weapon | None = None,
    ) -> None:
        super().__init__(turret_id, faction, x, y, hp=hp, weapon=weapon)

    @property
    def mobile(self) -> bool:
        """炮塔不可移动：move_mech 门槛拒绝；AI 目标优先级中排攻击性目标之后。"""
        return False

    @property
    def move_speed_px_s(self) -> float:
        """固定工事：基础移速 0（无移动语义）。"""
        return 0.0

    @property
    def sprite_kind(self) -> str:
        """精灵分派键：官方作战炮塔无静态 TGA（.ase 动画外观），恒未命中 → 程序化回退。"""
        return "turret"

    def move_to(self, x: float, y: float) -> None:
        """炮塔固定：移动指令 no-op（永无移动意图，占位快照恒静态阻挡）。"""
        return
