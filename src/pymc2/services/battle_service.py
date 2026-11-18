"""战斗编排服务：移动指令、开火指令与逐帧推进（M1）。"""

from __future__ import annotations

import math
import random

from pymc2.domain.entities.mech import TILE_SIZE_PX, Faction, Mech, Weapon
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
        self.selected_id: str | None = None
        self._rng: ShotRng = rng if rng is not None else random.Random()
        self.projectile_speed_px_s = projectile_speed_px_s

    def add_mech(self, mech: Mech) -> None:
        self.mechs[mech.id] = mech

    def get(self, mech_id: str) -> Mech | None:
        return self.mechs.get(mech_id)

    @property
    def selected(self) -> Mech | None:
        if self.selected_id is None:
            return None
        return self.mechs.get(self.selected_id)

    def select_at(self, x: float, y: float) -> bool:
        """选中点击处的己方机甲（敌方不可选）。"""
        for mech in self.mechs.values():
            if mech.faction is not Faction.PLAYER:
                continue
            if abs(mech.x - x) <= SELECT_HALF_PX and abs(mech.y - y) <= SELECT_HALF_PX:
                self.selected_id = mech.id
                return True
        return False

    def move_mech(self, mech_id: str, x: float, y: float) -> bool:
        """下达移动指令。"""
        mech = self.mechs.get(mech_id)
        if mech is None or not mech.alive:
            return False
        mech.move_to(x, y)
        return True

    def fire_at(self, attacker_id: str, target_id: str) -> bool:
        """直射开火：校验存活/冷却/射程后立即判定命中，弹丸进入飞行。"""
        attacker = self.mechs.get(attacker_id)
        target = self.mechs.get(target_id)
        if attacker is None or target is None:
            return False
        if not attacker.alive or not target.alive:
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
                speed_px_s=self.projectile_speed_px_s,
            )
        )
        attacker.weapon.cd_remaining = attacker.weapon.cooldown_s
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
        target.take_damage(projectile.damage)


def create_m1_battle(*, rng: ShotRng | None = None) -> BattleService:
    """构建 M1 垂直切片初始战局：己方 px-01 与敌方 ex-01 沿道路对峙。"""
    service = BattleService(rng=rng)
    px, py = _tile_center(*PLAYER_SPAWN_TILE)
    ex, ey = _tile_center(*ENEMY_SPAWN_TILE)
    service.add_mech(Mech("px-01", Faction.PLAYER, px, py, weapon=Weapon("ppc", PLAYER_WEAPON_DAMAGE, 420.0, 2.0)))
    service.add_mech(Mech("ex-01", Faction.ENEMY, ex, ey, weapon=Weapon("mlaser", ENEMY_WEAPON_DAMAGE, 360.0, 2.5)))
    return service
