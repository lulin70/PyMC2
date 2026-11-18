"""Trigger DSL（M6）：declarative 条件-动作对，JSON 可加载。

条件五种（MC2 TriggerSystem 情形的最小集）：
- tick_reached        {tick: N}                     战局时钟到达 N 帧
- enemy_destroyed     {count?: N}                   累计摧毁敌机甲 ≥ N（缺省 = 敌全灭）
- unit_below_hull     {unit_id, ratio}              该机甲完好率 < ratio（死亡亦触发）
- unit_in_region      {unit_id, rect: [x0,y0,x1,y1]} 机甲 tile 位于矩形区域内（tile 坐标）
- allies_destroyed    {count?: N}                   累计损毁己方机甲 ≥ N（缺省 = 己方全灭）

动作五种：
- spawn_reinforcement {faction, mechs: [{id, tile, hull?, damage?...}]}  空投增援
- victory / defeat    {}                            设置战局结局（outcome）
- grant_rp            {amount: N}                   RP 奖励（入账战役状态，若有）
- show_message        {text: str}                   战报消息

battle_service.tick 经 tick_hook 驱动引擎逐帧评估（mission_script.ScriptEngine）；
本模块保持 declarative + 纯求值函数，对 services 层仅 TYPE_CHECKING 引用（无运行时环）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pymc2.domain.entities.mech import TILE_SIZE_PX, Faction, Mech, Weapon
from pymc2.domain.entities.pilot import Pilot

if TYPE_CHECKING:
    from pymc2.domain.scripting.mission_script import ScriptContext
    from pymc2.services.battle_service import BattleService

CONDITION_KINDS: frozenset[str] = frozenset(
    {"tick_reached", "enemy_destroyed", "unit_below_hull", "unit_in_region", "allies_destroyed"}
)
ACTION_KINDS: frozenset[str] = frozenset({"spawn_reinforcement", "victory", "defeat", "grant_rp", "show_message"})

REQUIRED_CONDITION_PARAMS: dict[str, tuple[str, ...]] = {
    "tick_reached": ("tick",),
    "enemy_destroyed": (),
    "unit_below_hull": ("unit_id", "ratio"),
    "unit_in_region": ("unit_id", "rect"),
    "allies_destroyed": (),
}
REQUIRED_ACTION_PARAMS: dict[str, tuple[str, ...]] = {
    "spawn_reinforcement": ("faction", "mechs"),
    "victory": (),
    "defeat": (),
    "grant_rp": ("amount",),
    "show_message": ("text",),
}

OUTCOME_VICTORY = "victory"
OUTCOME_DEFEAT = "defeat"


@dataclass(frozen=True)
class TriggerCondition:
    """声明式条件：kind 白名单 + 参数包（loader 校验 kind 与必需参数）。"""

    kind: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TriggerAction:
    """声明式动作：kind 白名单 + 参数包。"""

    kind: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Trigger:
    """触发器：条件满足 → 依序执行动作；once/cooldown 门控照 PyCC2 TriggerSystem 精神。

    once=True 触发一次后永久沉默；cooldown_ticks>0 触发后 N 帧内不再触发
    （0 = 条件持续满足时每帧触发）。
    """

    trigger_id: str
    condition: TriggerCondition
    actions: tuple[TriggerAction, ...]
    once: bool = True
    cooldown_ticks: int = 0


# ------------------------------------------------------------------ 条件求值


def destroyed_count(battle: BattleService, faction: Faction) -> int:
    """指定阵营的累计损毁机甲数（阵亡实体保留在 battle.mechs 中）。"""
    return sum(1 for mech in battle.mechs.values() if mech.faction is faction and not mech.alive)


def evaluate_condition(condition: TriggerCondition, battle: BattleService, tick_count: int) -> bool:
    """求值单个条件（battle 上下文 + 引擎帧计数）。未知 kind 显式报错。"""
    kind = condition.kind
    params = condition.params
    if kind == "tick_reached":
        return tick_count >= int(params["tick"])
    if kind == "enemy_destroyed":
        return _destroyed_threshold(battle, Faction.ENEMY, params)
    if kind == "allies_destroyed":
        return _destroyed_threshold(battle, Faction.PLAYER, params)
    if kind == "unit_below_hull":
        mech = battle.mechs.get(str(params["unit_id"]))
        return mech is not None and mech.hull_ratio < float(params["ratio"])
    if kind == "unit_in_region":
        mech = battle.mechs.get(str(params["unit_id"]))
        if mech is None:
            return False
        x0, y0, x1, y1 = (int(v) for v in params["rect"])
        tx, ty = mech.tile
        return x0 <= tx <= x1 and y0 <= ty <= y1
    raise ValueError(f"unknown condition kind: {kind!r}")


def _destroyed_threshold(battle: BattleService, faction: Faction, params: dict[str, Any]) -> bool:
    """阵营损毁阈值：有 count → 累计损毁 ≥ count；无 count → 该阵营全灭。"""
    destroyed = destroyed_count(battle, faction)
    if "count" in params:
        return destroyed >= int(params["count"])
    total = sum(1 for mech in battle.mechs.values() if mech.faction is faction)
    return total > 0 and destroyed == total


# ------------------------------------------------------------------ 动作执行


def apply_action(action: TriggerAction, context: ScriptContext) -> None:
    """执行单个动作（写 ScriptContext，可回写战役状态）。未知 kind 显式报错。"""
    kind = action.kind
    params = action.params
    if kind == "spawn_reinforcement":
        _spawn_reinforcement(params, context)
        return
    if kind == "victory":
        context.outcome = OUTCOME_VICTORY
        return
    if kind == "defeat":
        context.outcome = OUTCOME_DEFEAT
        return
    if kind == "grant_rp":
        amount = int(params["amount"])
        context.granted_rp += amount
        if context.campaign is not None:
            context.campaign.requisition_points += amount
        return
    if kind == "show_message":
        context.messages.append(str(params["text"]))
        return
    raise ValueError(f"unknown action kind: {kind!r}")


def _spawn_reinforcement(params: dict[str, Any], context: ScriptContext) -> None:
    """空投增援：按 mechs 快照列表生成机甲入局（id 撞车显式报错）。"""
    faction = Faction(str(params["faction"]))
    battle = context.battle
    for spec in params["mechs"]:
        unit_id = str(spec["id"])
        if unit_id in battle.mechs:
            raise ValueError(f"reinforcement id already exists: {unit_id!r}")
        tile_x, tile_y = (int(v) for v in spec["tile"])
        mech = Mech(
            unit_id,
            faction,
            tile_x * TILE_SIZE_PX + TILE_SIZE_PX / 2,
            tile_y * TILE_SIZE_PX + TILE_SIZE_PX / 2,
            hull=int(spec.get("hull", 100)),
            weapon=Weapon(
                "mlaser",
                int(spec.get("damage", 15)),
                float(spec.get("range_px", 360.0)),
                float(spec.get("cooldown_s", 2.5)),
            ),
            pilot=Pilot(str(spec["pilot_name"])) if spec.get("pilot_name") is not None else None,
        )
        battle.add_mech(mech)
        context.spawned_unit_ids.append(unit_id)
