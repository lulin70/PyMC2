"""战斗编排服务：移动/开火/停止/火力姿态指令与逐帧推进（M1+M2）。"""

from __future__ import annotations

import math
import random

from pymc2.domain.entities.mech import TILE_SIZE_PX, Faction, FireMode, Mech, Weapon
from pymc2.domain.entities.mech_chassis import MechLocation, make_medium_chassis
from pymc2.domain.systems.ballistic import (
    DEFAULT_PROJECTILE_SPEED_PX_S,
    Projectile,
    ShotRng,
    resolve_shot,
)

SELECT_HALF_PX = 16.0

# M1 初始战局：双机甲沿道路（tile row 10）对峙，间距 384px，双方武器均可及。
PLAYER_SPAWN_TILE = (6, 10)
ENEMY_SPAWN_TILE = (18, 10)
PLAYER_WEAPON_DAMAGE = 25
ENEMY_WEAPON_DAMAGE = 15


def _tile_center(tile_x: int, tile_y: int) -> tuple[float, float]:
    return (tile_x * TILE_SIZE_PX + TILE_SIZE_PX / 2, tile_y * TILE_SIZE_PX + TILE_SIZE_PX / 2)


class BattleService:
    """战术层编排：持有单位与投射物，提供指令接口与 tick。"""

    def __init__(
        self,
        *,
        rng: ShotRng | None = None,
        projectile_speed_px_s: float = DEFAULT_PROJECTILE_SPEED_PX_S,
    ) -> None:
        self.mechs: dict[str, Mech] = {}
        self.projectiles: list[Projectile] = []
        self.selected_ids: list[str] = []
        self._rng: ShotRng = rng if rng is not None else random.Random()
        self.projectile_speed_px_s = projectile_speed_px_s

    def add_mech(self, mech: Mech) -> None:
        self.mechs[mech.id] = mech

    def get(self, mech_id: str) -> Mech | None:
        return self.mechs.get(mech_id)

    @property
    def selected_id(self) -> str | None:
        """主选中单位 id（多选时为首个）。"""
        return self.selected_ids[0] if self.selected_ids else None

    @property
    def selected(self) -> Mech | None:
        sid = self.selected_id
        if sid is None:
            return None
        return self.mechs.get(sid)

    def select_at(self, x: float, y: float) -> bool:
        """选中点击处的己方机甲（敌方不可选，替换当前选择）。"""
        mech = self.unit_at(x, y, faction=Faction.PLAYER)
        if mech is None:
            return False
        self.selected_ids = [mech.id]
        return True

    def select_in_rect(self, x0: float, y0: float, x1: float, y1: float) -> int:
        """框选：替换当前选择为矩形内全部己方机甲；返回选中数量（0 表示清空）。"""
        left, right = min(x0, x1), max(x0, x1)
        top, bottom = min(y0, y1), max(y0, y1)
        picked: list[str] = []
        for mech in self.mechs.values():
            if mech.faction is not Faction.PLAYER:
                continue
            if left <= mech.x <= right and top <= mech.y <= bottom:
                picked.append(mech.id)
        self.selected_ids = picked
        return len(picked)

    def unit_at(self, x: float, y: float, *, faction: Faction | None = None) -> Mech | None:
        """返回命中点击处的机甲（可按阵营过滤），无命中返回 None。"""
        for mech in self.mechs.values():
            if faction is not None and mech.faction is not faction:
                continue
            if abs(mech.x - x) <= SELECT_HALF_PX and abs(mech.y - y) <= SELECT_HALF_PX:
                return mech
        return None

    def move_mech(self, mech_id: str, x: float, y: float) -> bool:
        """下达移动指令（移动即解除警戒姿态；强制关机期间被拒）。"""
        mech = self.mechs.get(mech_id)
        if mech is None or not mech.alive:
            return False
        if mech.heat.shutdown_active:
            return False
        mech.guarding = False
        mech.move_to(x, y)
        return True

    def set_fire_mode(self, mech_id: str, mode: FireMode) -> bool:
        """设置火力姿态（HOLD 后 fire_at 被拒绝直至恢复 FREE）。"""
        mech = self.mechs.get(mech_id)
        if mech is None or not mech.alive:
            return False
        mech.fire_mode = mode
        return True

    def stop_mech(self, mech_id: str) -> bool:
        """停止当前移动（警戒姿态不变）。"""
        mech = self.mechs.get(mech_id)
        if mech is None or not mech.alive:
            return False
        mech.move_target = None
        return True

    def set_guard(self, mech_id: str, *, guarding: bool = True) -> bool:
        """设置警戒姿态：原地不追击（警戒时清除移动目标）。"""
        mech = self.mechs.get(mech_id)
        if mech is None or not mech.alive:
            return False
        mech.guarding = guarding
        if guarding:
            mech.move_target = None
        return True

    def fire_at(self, attacker_id: str, target_id: str) -> bool:
        """直射开火：校验存活/火力姿态/强制关机/武器可用/冷却/射程后判定命中。

        命中部位在开火瞬间按权重随机判定并随弹丸携带；开火成功即产热。
        """
        attacker = self.mechs.get(attacker_id)
        target = self.mechs.get(target_id)
        if attacker is None or target is None:
            return False
        if not attacker.alive or not target.alive:
            return False
        if attacker.fire_mode is FireMode.HOLD:
            return False
        if attacker.heat.shutdown_active:
            return False
        if not attacker.weapon_operational:
            return False
        if not attacker.weapon.ready:
            return False
        distance = math.hypot(target.x - attacker.x, target.y - attacker.y)
        if distance > attacker.weapon.range_px:
            return False
        result = resolve_shot(attacker, target, self._rng)
        self.projectiles.append(
            Projectile(
                attacker_id=attacker.id,
                target_id=target.id,
                x=attacker.x,
                y=attacker.y,
                impact_x=result.impact_x,
                impact_y=result.impact_y,
                damage=result.damage,
                will_hit=result.hit,
                location=result.location,
                speed_px_s=self.projectile_speed_px_s,
            )
        )
        attacker.weapon.cd_remaining = attacker.weapon.cooldown_s
        attacker.heat.add_heat(attacker.weapon.heat_per_shot)
        return True

    def nearest_enemy(self, mech: Mech) -> Mech | None:
        """返回最近的存活敌方机甲（不限射程，射程校验在 fire_at 内）。"""
        best: Mech | None = None
        best_dist = float("inf")
        for other in self.mechs.values():
            if other.faction is mech.faction or not other.alive:
                continue
            dist = math.hypot(other.x - mech.x, other.y - mech.y)
            if dist < best_dist:
                best, best_dist = other, dist
        return best

    def tick(self, dt: float) -> None:
        """推进一帧：单位移动与冷却、弹道飞行、到达结算伤害。"""
        for mech in self.mechs.values():
            mech.tick(dt)
        still_flying: list[Projectile] = []
        for projectile in self.projectiles:
            if projectile.advance(dt):
                self._resolve_impact(projectile)
            else:
                still_flying.append(projectile)
        self.projectiles = still_flying

    def _resolve_impact(self, projectile: Projectile) -> None:
        target = self.mechs.get(projectile.target_id)
        if target is None or not target.alive or not projectile.will_hit:
            return
        target.apply_damage(projectile.location, projectile.damage)


def create_m1_battle(*, rng: ShotRng | None = None) -> BattleService:
    """构建 M1 垂直切片初始战局：己方 px-01 与敌方 ex-01 沿道路对峙。"""
    service = BattleService(rng=rng)
    px, py = _tile_center(*PLAYER_SPAWN_TILE)
    ex, ey = _tile_center(*ENEMY_SPAWN_TILE)
    service.add_mech(Mech("px-01", Faction.PLAYER, px, py, weapon=Weapon("ppc", PLAYER_WEAPON_DAMAGE, 420.0, 2.0)))
    service.add_mech(Mech("ex-01", Faction.ENEMY, ex, ey, weapon=Weapon("mlaser", ENEMY_WEAPON_DAMAGE, 360.0, 2.5)))
    return service


def create_m2_battle(*, rng: ShotRng | None = None) -> BattleService:
    """构建 M2 指令层初始战局：双己方机甲（框选/多单位指令验收）+ 敌方机甲。"""
    service = BattleService(rng=rng)
    px1, py1 = _tile_center(*PLAYER_SPAWN_TILE)
    px2, py2 = _tile_center(8, 10)
    ex, ey = _tile_center(*ENEMY_SPAWN_TILE)
    service.add_mech(Mech("px-01", Faction.PLAYER, px1, py1, weapon=Weapon("ppc", PLAYER_WEAPON_DAMAGE, 420.0, 2.0)))
    service.add_mech(Mech("px-02", Faction.PLAYER, px2, py2, weapon=Weapon("mlaser", 15, 380.0, 1.5)))
    service.add_mech(Mech("ex-01", Faction.ENEMY, ex, ey, weapon=Weapon("mlaser", ENEMY_WEAPON_DAMAGE, 360.0, 2.5)))
    return service


def create_m3_battle(*, rng: ShotRng | None = None) -> BattleService:
    """构建 M3 机甲实体战局：中型底盘（部位装甲/结构），武器挂载手臂。"""
    service = BattleService(rng=rng)
    px, py = _tile_center(*PLAYER_SPAWN_TILE)
    ex, ey = _tile_center(*ENEMY_SPAWN_TILE)
    service.add_mech(
        Mech(
            "px-01",
            Faction.PLAYER,
            px,
            py,
            chassis=make_medium_chassis(),
            weapon=Weapon("ppc", PLAYER_WEAPON_DAMAGE, 420.0, 2.0, mounted_location=MechLocation.RA),
        )
    )
    service.add_mech(
        Mech(
            "ex-01",
            Faction.ENEMY,
            ex,
            ey,
            chassis=make_medium_chassis(),
            weapon=Weapon("mlaser", ENEMY_WEAPON_DAMAGE, 360.0, 2.5, mounted_location=MechLocation.RA),
        )
    )
    return service
