"""战斗编排服务：移动/开火/停止/火力姿态指令与逐帧推进（M1+M2）。

M7 自动交战（用户反馈：开火自动化）：注入 dispatcher 后，tick 内对每个存活、
FireMode.FREE 的机甲自动锁定射程内最近敌机甲，构造 TacticalOrder（origin=SELF）
经 OrderDispatcher 下发，与玩家/指挥官指令同源同路径；显式指定目标（attack_target_id
仍有效）优先，HOLD 火力纪律不自动锁定。未注入 dispatcher 时行为与存量完全一致。

让路协商（MC2-05 直线插值版，修复敌我机甲重叠穿人）：每帧构建占位快照（静态占位
tile + 移动机甲运动快照），移动机甲在本帧将进入新 tile 且该 tile 被静态存活机甲
占据、或与另一移动机甲互换对冲时，本帧停走（yield，武器/散热照常推进）。
连续让路超过 YIELD_TIMEOUT_TICKS 强制通行（防死锁逃生阀）；两个移动机甲非互换
不互阻（穿越语义，与 PyCC2 MC2-05 一致）。yield 状态存 service 内部 dict
（按 mech.id 的连续被阻帧数），实体零新字段。

P1-A 高程：service 恒持有 ElevationGrid（缺省全零平面，flat 快路径移动数学与存量
一致）——上坡步进耗时按每 +1 级 ×CLIMB_COST_PER_LEVEL 复利加成（下坡 ×1.0），
爬升限制目标 tile 高于当前 >CLIMB_MAX_DELTA 的 MOVE_TO 直接拒绝（回执沿用现有
dispatcher 失败惯例：指令入 order_log、dispatch 返回 False）；高程不参与让路
占位/互换判定（语义不变）。见 docs/P1A_DESIGN_20261121.md §2。
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from typing import TYPE_CHECKING, NamedTuple

from pymc2.domain.elevation import CLIMB_COST_PER_LEVEL, CLIMB_MAX_DELTA, ElevationGrid
from pymc2.domain.entities.mech import MOVE_SPEED_PX_S, TILE_SIZE_PX, Faction, FireMode, Mech, Weapon
from pymc2.domain.entities.mech_chassis import MechLocation, make_medium_chassis
from pymc2.domain.entities.pilot import Pilot
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType, TacticalOrder
from pymc2.domain.systems.ballistic import (
    DEFAULT_PROJECTILE_SPEED_PX_S,
    Projectile,
    ShotRng,
    resolve_shot,
)

if TYPE_CHECKING:
    from pymc2.services.order_dispatcher import OrderDispatcher

SELECT_HALF_PX = 16.0

# 让路超时逃生阀：连续让路帧数超过该值强制通行（PyCC2 MC2-05 同款 90 帧，防死锁）。
YIELD_TIMEOUT_TICKS = 90


def _tile_of(x: float, y: float) -> tuple[int, int]:
    """像素坐标 → tile 坐标（与 Mech.tile 同口径）。"""
    return (int(x) // TILE_SIZE_PX, int(y) // TILE_SIZE_PX)


def _predicted_move_pos(mech: Mech, target: tuple[float, float], speed_scale: float, dt: float) -> tuple[float, float]:
    """本帧推进后的预测位置（不落位）：与 Mech._advance_move 同式（MOVE_SPEED_PX_S ×
    机动惩罚 × 高程加成 × dt，dist ≤ step 吸附目标）——改动移动物理必须同步此处，
    否则让路判定相位漂移。"""
    tx, ty = target
    dx = tx - mech.x
    dy = ty - mech.y
    dist = math.hypot(dx, dy)
    step = MOVE_SPEED_PX_S * (1.0 - mech.chassis.mobility_penalty) * speed_scale * dt
    if dist <= step:
        return (tx, ty)
    return (mech.x + dx / dist * step, mech.y + dy / dist * step)


class _MoverFrame(NamedTuple):
    """单个移动机甲的本帧运动快照（tile 坐标）。"""

    current_tile: tuple[int, int]
    next_tile: tuple[int, int]  # 本帧推进后将进入的 tile
    target_tile: tuple[int, int]  # 移动目标像素点所在 tile


class _OccupancySnapshot(NamedTuple):
    """每帧占位快照：静态占位 tile 与移动机甲运动快照（让路协商输入）。"""

    static_tiles: dict[tuple[int, int], str]
    movers: dict[str, _MoverFrame]


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
        elevation: ElevationGrid | None = None,
    ) -> None:
        self.mechs: dict[str, Mech] = {}
        self.projectiles: list[Projectile] = []
        self.selected_ids: list[str] = []
        self._rng: ShotRng = rng if rng is not None else random.Random()
        self.projectile_speed_px_s = projectile_speed_px_s
        # P1-A 高程：缺省全零平面（flat 快路径，移动/渲染与存量零差异）。
        self.elevation: ElevationGrid = elevation if elevation is not None else ElevationGrid.flat()
        # M6 任务脚本钩子：每帧 tick 末尾回调（None = 不启用，M1-M5 存量零影响）。
        # 返回值声明为 object 以兼容带返回值的回调（如 ScriptEngine.tick 返回触发 id 列表）。
        self.tick_hook: Callable[[BattleService], object] | None = None
        # M7 自动交战：注入 dispatcher 后 FREE 机甲自动锁定射程内最近敌机甲
        # （None = 关闭，存量测试/直构战局零影响）。
        self.dispatcher: OrderDispatcher | None = None
        # 让路协商（MC2-05）：mech.id → 连续被阻帧数；清零时机见 _advance_with_yield。
        self._yield_ticks: dict[str, int] = {}

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
        """下达移动指令（移动即解除警戒姿态；强制关机或爬升超限被拒）。"""
        mech = self.mechs.get(mech_id)
        if mech is None or not mech.alive:
            return False
        if mech.heat.shutdown_active:
            return False
        if not self._climb_permitted(mech, (x, y)):
            return False
        mech.guarding = False
        mech.move_to(x, y)
        return True

    def _climb_permitted(self, mech: Mech, target_px: tuple[float, float]) -> bool:
        """P1-A 爬升限制（无寻路简化）：目标 tile 高于当前 tile 超过 CLIMB_MAX_DELTA
        → 该段移动不可达，直接拒绝（回执沿用 dispatcher 失败惯例）。flat 恒放行。"""
        grid = self.elevation
        if grid.is_flat():
            return True
        delta = grid.elevation_at(_tile_of(*target_px)) - grid.elevation_at(mech.tile)
        return delta <= CLIMB_MAX_DELTA

    def _climb_multiplier(self, mech: Mech, target: tuple[float, float], dt: float) -> float:
        """P1-A 上坡步进耗时加成：本帧将进入 tile 高于当前 tile 时耗时 ×CLIMB_COST_PER_LEVEL^Δ
        （即步长速度取倒数 1/1.5^Δ，上坡变慢；下坡/平地 ×1.0 不加速）。

        以基础速度预测进入 tile（_predicted_move_pos，与 Mech._advance_move 同式），
        Δ≤0（平地/下坡）或仍在 tile 内 → ×1.0；flat 快路径恒 1.0（存量零改动）。
        """
        grid = self.elevation
        if grid.is_flat():
            return 1.0
        entered = _tile_of(*_predicted_move_pos(mech, target, 1.0, dt))
        if entered == mech.tile:
            return 1.0
        delta = grid.elevation_at(entered) - grid.elevation_at(mech.tile)
        if delta <= 0:
            return 1.0
        return 1.0 / CLIMB_COST_PER_LEVEL**delta

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
        # M4 技能成长：开火即记账（uses 必增，命中加记 successes）。
        if attacker.pilot is not None:
            attacker.pilot.record_shot(result.hit)
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
        """推进一帧：单位移动（含让路协商）、自动交战锁定、弹道飞行、到达结算伤害、脚本触发器评估。"""
        snapshot = self._occupancy_snapshot(dt)
        for mech in self.mechs.values():
            self._advance_with_yield(mech, dt, snapshot)
        self._auto_engage()
        still_flying: list[Projectile] = []
        for projectile in self.projectiles:
            if projectile.advance(dt):
                self._resolve_impact(projectile)
            else:
                still_flying.append(projectile)
        self.projectiles = still_flying
        if self.tick_hook is not None:
            self.tick_hook(self)

    def _advance_with_yield(self, mech: Mech, dt: float, snapshot: _OccupancySnapshot) -> None:
        """单机推进（含让路）：被阻挡时本帧冻结移动（位置与目标保持，武器/散热照常）。

        让路计数（连续被阻帧数）生命周期：被阻 +1；进入新 tile / 到达目标 /
        停止指令或无移动意图 / 阵亡 → 清零（超时阀强制通行期间不清，直至进入新 tile）。
        """
        if not mech.alive or mech.move_target is None:
            self._yield_ticks.pop(mech.id, None)
            mech.tick(dt)
            return
        if self._movement_blocked(mech, snapshot):
            self._yield_ticks[mech.id] = self._yield_ticks.get(mech.id, 0) + 1
            target = mech.move_target
            mech.move_target = None  # 本帧仅推进武器冷却/散热，移动冻结（朝向语义：位置与目标均不变）
            mech.tick(dt)
            mech.move_target = target
            return
        pre_tile = mech.tile
        # P1-A：上坡帧步长按高程差加成（flat 恒 1.0，与 Mech.tick 默认语义一致）。
        mech.tick(dt, move_speed_scale=self._climb_multiplier(mech, mech.move_target, dt))
        # 成功推进：进入新 tile 或到达目标 → 连续让路计数清零（tile 内移动保持计数，
        # 与 PyCC2「插值帧不触碰 yield_ticks」一致，保证超时阀跨 tile 边界仍生效）。
        if mech.move_target is None or mech.tile != pre_tile:
            self._yield_ticks.pop(mech.id, None)

    def _occupancy_snapshot(self, dt: float) -> _OccupancySnapshot:
        """每帧占位快照（O(n) 重建，无全局占位表）。

        - static_tiles：无移动意图的存活机甲所在 tile（硬阻挡源，阵亡不入表）；
        - movers：有移动意图机甲的当前 tile / 本帧将进入 tile / 目标 tile（互换对冲判定用）。
        快照先于本帧任何移动构建（同 PyCC2）：同帧后行机甲以前行机甲的帧前位置判定。
        """
        static_tiles: dict[tuple[int, int], str] = {}
        movers: dict[str, _MoverFrame] = {}
        for mech in self.mechs.values():
            if not mech.alive:
                continue
            target = mech.move_target
            if target is None:
                static_tiles[mech.tile] = mech.id
                continue
            movers[mech.id] = _MoverFrame(
                current_tile=mech.tile,
                next_tile=self._frame_next_tile(mech, target, dt),
                target_tile=_tile_of(*target),
            )
        return _OccupancySnapshot(static_tiles=static_tiles, movers=movers)

    def _frame_next_tile(self, mech: Mech, target: tuple[float, float], dt: float) -> tuple[int, int]:
        """预测本帧推进后所在 tile（不落位）：与 Mech._advance_move 同式并含 P1-A 高程
        加成（改动移动物理必须同步此处，否则让路判定相位漂移）。"""
        return _tile_of(*_predicted_move_pos(mech, target, self._climb_multiplier(mech, target, dt), dt))

    def _movement_blocked(self, mech: Mech, snapshot: _OccupancySnapshot) -> bool:
        """让路判定（直线插值版）：仅在本帧将进入新 tile 的瞬间产生硬阻挡。

        - 超时逃生阀：连续让路 > YIELD_TIMEOUT_TICKS → 强制通行（防死锁，对应 MC2
          "yielded for a while"）；
        - 本帧将进入的 tile 被静态存活机甲占据 → yield；
        - 互换对冲：互以对方当前 tile 为目标 tile → 双方 yield；
        - 两个移动机甲非互换 → 不互阻（穿越语义，与 PyCC2 MC2-05 一致）。
        """
        if self._yield_ticks.get(mech.id, 0) > YIELD_TIMEOUT_TICKS:
            return False
        frame = snapshot.movers[mech.id]
        if frame.next_tile != frame.current_tile:
            occupant = snapshot.static_tiles.get(frame.next_tile)
            if occupant is not None and occupant != mech.id:
                return True
        for other_id, other in snapshot.movers.items():
            if other_id == mech.id:
                continue
            if frame.target_tile == other.current_tile and other.target_tile == frame.current_tile:
                return True
        return False

    def _auto_engage(self) -> None:
        """自动交战（M7）：FREE 机甲对射程内最近敌机甲自动锁定并开火。

        - 显式目标（attack_target_id 仍有效）优先：SELF 只在无有效目标时接管，
          不覆盖玩家/指挥官已指定的目标；
        - 目标死亡/出射程 → 清除锁定，随后 tick 自动重锁；
        - HOLD（火力纪律语义）不自动锁定；
        - 仅在武器就绪/可用且非强制关机时下发 SELF 源 ATTACK_OBJECT（经 dispatcher
          与玩家/指挥官同路径），冷却/关机期间不下发以免指令日志刷屏。
        """
        if self.dispatcher is None:
            return
        for mech in self.mechs.values():
            if not mech.alive or mech.fire_mode is not FireMode.FREE:
                continue
            target = self._engagement_target(mech)
            if target is None:
                continue
            if mech.weapon.ready and mech.weapon_operational and not mech.heat.shutdown_active:
                self.dispatcher.dispatch(
                    TacticalOrder(
                        order_type=OrderType.ATTACK_OBJECT,
                        unit_ids=(mech.id,),
                        origin=OrderOrigin.SELF,
                        target_tile=None,
                        target_unit_id=target.id,
                    ),
                    self,
                )

    def _engagement_target(self, mech: Mech) -> Mech | None:
        """当前交战目标：锁定仍有效则保持；失效（死亡/出射程/不存在）则清锁并重锁最近射程内敌。"""
        locked = self.mechs.get(mech.attack_target_id) if mech.attack_target_id is not None else None
        if locked is not None and locked.alive and self._in_weapon_range(mech, locked):
            return locked
        mech.attack_target_id = None  # 目标失效 → 清除锁定（下个评估周期自动重锁）
        return self._nearest_enemy_in_range(mech)

    def _nearest_enemy_in_range(self, mech: Mech) -> Mech | None:
        """射程内最近存活敌机甲（无则 None）；与 nearest_enemy 的区别在于按武器射程过滤。"""
        best: Mech | None = None
        best_dist = float("inf")
        for other in self.mechs.values():
            if other.faction is mech.faction or not other.alive:
                continue
            dist = math.hypot(other.x - mech.x, other.y - mech.y)
            if dist <= mech.weapon.range_px and dist < best_dist:
                best, best_dist = other, dist
        return best

    @staticmethod
    def _in_weapon_range(attacker: Mech, target: Mech) -> bool:
        """射程判定复用 fire_at 同款口径（欧氏距离 ≤ 武器射程）。"""
        return math.hypot(target.x - attacker.x, target.y - attacker.y) <= attacker.weapon.range_px

    def _resolve_impact(self, projectile: Projectile) -> None:
        target = self.mechs.get(projectile.target_id)
        if target is None or not target.alive or not projectile.will_hit:
            return
        killed = target.apply_damage(projectile.location, projectile.damage)
        # M4 技能成长：击杀归属开火方驾驶员（kills 只增不减）。
        if killed:
            attacker = self.mechs.get(projectile.attacker_id)
            if attacker is not None and attacker.pilot is not None:
                attacker.pilot.record_kill()


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


def create_m4_battle(*, rng: ShotRng | None = None) -> BattleService:
    """构建 M4 AI 战局：玩家单机（固定靶位）+ 敌方双机甲（AI 指挥官部下），全员驾驶员。"""
    service = BattleService(rng=rng)
    px, py = _tile_center(*PLAYER_SPAWN_TILE)
    e1x, e1y = _tile_center(*ENEMY_SPAWN_TILE)
    e2x, e2y = _tile_center(20, 13)
    service.add_mech(
        Mech(
            "px-01",
            Faction.PLAYER,
            px,
            py,
            weapon=Weapon("ppc", PLAYER_WEAPON_DAMAGE, 420.0, 2.0),
            pilot=Pilot("player-gunner"),
        )
    )
    service.add_mech(
        Mech(
            "ex-01",
            Faction.ENEMY,
            e1x,
            e1y,
            weapon=Weapon("mlaser", ENEMY_WEAPON_DAMAGE, 360.0, 2.5),
            pilot=Pilot("enemy-veteran"),
        )
    )
    service.add_mech(
        Mech(
            "ex-02",
            Faction.ENEMY,
            e2x,
            e2y,
            weapon=Weapon("mlaser", ENEMY_WEAPON_DAMAGE, 360.0, 2.5),
            pilot=Pilot("enemy-rookie"),
        )
    )
    return service
