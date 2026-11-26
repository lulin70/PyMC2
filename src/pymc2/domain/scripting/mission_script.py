"""MissionScript（M6）：脚本加载（JSON → 触发器表）+ 逐帧评估引擎。

- load_mission_script(path)：JSON 文件加载，语法/结构/白名单错误统一包装为
  MissionScriptError（信息含来源与原因，存档损坏不静默）。
- mission_script_from_dict：dict 级加载（测试与内嵌脚本复用）；顶层可选 "elevation"
  键（P1-A）：显式 40×40 行数组或 "hills" 关键字 → ElevationGrid 覆盖，缺失/非法 →
  None（不覆盖，等效全零默认；容错不抛错，见 docs/P1A_DESIGN_20261121.md §4）。
- ScriptEngine：照 PyCC2 TriggerSystem 精神（registration order + once/cooldown 门控），
  经 battle.tick_hook 由 battle_service.tick 驱动逐帧评估；返回本帧触发的 trigger_id。
- ScriptContext：一次脚本会话的观察结果（消息/RP/增援/结局），胜负以
  victory/defeat 动作写 outcome 表达。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pymc2.domain.elevation import ElevationGrid, generate_hills
from pymc2.domain.scripting.objective import ObjectiveSet  # W1 任务 2：目标系统集成
from pymc2.domain.scripting.trigger import (
    ACTION_KINDS,
    CONDITION_KINDS,
    REQUIRED_ACTION_PARAMS,
    REQUIRED_CONDITION_PARAMS,
    Trigger,
    TriggerAction,
    TriggerCondition,
    apply_action,
    evaluate_condition,
)

if TYPE_CHECKING:
    from pymc2.services.battle_service import BattleService
    from pymc2.services.campaign_state import CampaignState


class MissionScriptError(ValueError):
    """任务脚本加载错误：JSON 语法 / 结构缺失 / 未知 kind / 必需参数缺失。"""


@dataclass
class ScriptContext:
    """脚本会话上下文：战局 + 可选战役状态 + 观察结果（消息/RP/增援/结局）。"""

    battle: BattleService
    campaign: CampaignState | None = None
    spawned_unit_ids: list[str] = field(default_factory=list)
    granted_rp: int = 0
    messages: list[str] = field(default_factory=list)
    outcome: str | None = None


@dataclass(frozen=True)
class MissionScript:
    """任务脚本：触发器表（登记序 = 执行序）；胜负由 victory/defeat 动作表达。

    elevation（P1-A 可选）：任务脚本高程覆盖；None = 无覆盖（战局保持地形默认）。
    objectives（W1 任务 2）：目标系统；None = 无目标（战局无目标面板 + 胜负由 Trigger 显式决定）。
    """

    script_id: str
    triggers: tuple[Trigger, ...]
    elevation: ElevationGrid | None = None
    objectives: ObjectiveSet | None = None  # W1 任务 2：HUD 目标 + 自动胜负判定

    @property
    def victory_triggers(self) -> tuple[Trigger, ...]:
        """含 victory 动作的触发器（观察用）。"""
        return tuple(t for t in self.triggers if any(a.kind == "victory" for a in t.actions))

    @property
    def defeat_triggers(self) -> tuple[Trigger, ...]:
        """含 defeat 动作的触发器（观察用）。"""
        return tuple(t for t in self.triggers if any(a.kind == "defeat" for a in t.actions))


# ------------------------------------------------------------------ JSON 加载


def load_mission_script(path: Path) -> MissionScript:
    """从 JSON 文件加载任务脚本；解析错误包装为带来源信息的 MissionScriptError。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MissionScriptError(f"cannot read mission script {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MissionScriptError(f"invalid JSON in {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    try:
        return mission_script_from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise MissionScriptError(f"invalid mission script {path}: {exc}") from exc


def mission_script_from_dict(data: Any) -> MissionScript:
    """dict → MissionScript：结构校验（缺字段/未知 kind/缺必需参数均显式报错）。

    elevation 为可选键且独立容错（非法 → None 不覆盖），不参与严格校验。
    """
    if not isinstance(data, dict):
        raise TypeError("top level must be a JSON object")
    script_id = data["script_id"]
    triggers_raw = data["triggers"]
    if not isinstance(triggers_raw, list):
        raise TypeError("'triggers' must be a JSON array")
    triggers = tuple(_parse_trigger(entry, index) for index, entry in enumerate(triggers_raw))
    return MissionScript(script_id=str(script_id), triggers=triggers, elevation=_parse_elevation(data.get("elevation")))


def _parse_elevation(data: Any) -> ElevationGrid | None:
    """P1-A 可选 elevation 键解析（容错）：显式 40×40 行数组或 "hills" 关键字 → 网格；
    缺失/None/非法（类型错误、形状错误、未知关键字）→ None（不覆盖，等效全零默认，
    且保证 CLI --terrain hills 不被容错路径清零）。"""
    if data is None:
        return None
    try:
        if isinstance(data, str):
            return generate_hills() if data == "hills" else None
        if isinstance(data, list):
            return ElevationGrid(data)
    except (TypeError, ValueError):
        return None
    return None


def _parse_trigger(entry: Any, index: int) -> Trigger:
    """单触发器解析：id/condition/actions 必需，once/cooldown 可选。"""
    if not isinstance(entry, dict):
        raise TypeError(f"triggers[{index}] must be a JSON object")
    where = f"triggers[{index}]({entry.get('id', '?')})"
    condition = _parse_condition(entry["condition"], where)
    actions_raw = entry["actions"]
    if not isinstance(actions_raw, list):
        raise TypeError(f"{where}: 'actions' must be a JSON array")
    actions = tuple(_parse_action(action, f"{where}.actions[{i}]") for i, action in enumerate(actions_raw))
    return Trigger(
        trigger_id=str(entry["id"]),
        condition=condition,
        actions=actions,
        once=bool(entry.get("once", True)),
        cooldown_ticks=int(entry.get("cooldown_ticks", 0)),
    )


def _parse_condition(data: Any, where: str) -> TriggerCondition:
    """条件解析：kind 白名单 + 必需参数校验。"""
    if not isinstance(data, dict):
        raise TypeError(f"{where}: condition must be a JSON object")
    kind = str(data["kind"])
    if kind not in CONDITION_KINDS:
        raise ValueError(f"{where}: unknown condition kind {kind!r} (known: {sorted(CONDITION_KINDS)})")
    params = dict(data.get("params", {}))
    missing = [key for key in REQUIRED_CONDITION_PARAMS[kind] if key not in params]
    if missing:
        raise KeyError(f"{where}: condition {kind!r} missing params {missing}")
    return TriggerCondition(kind=kind, params=params)


def _parse_action(data: Any, where: str) -> TriggerAction:
    """动作解析：kind 白名单 + 必需参数校验。"""
    if not isinstance(data, dict):
        raise TypeError(f"{where}: action must be a JSON object")
    kind = str(data["kind"])
    if kind not in ACTION_KINDS:
        raise ValueError(f"{where}: unknown action kind {kind!r} (known: {sorted(ACTION_KINDS)})")
    params = dict(data.get("params", {}))
    missing = [key for key in REQUIRED_ACTION_PARAMS[kind] if key not in params]
    if missing:
        raise KeyError(f"{where}: action {kind!r} missing params {missing}")
    return TriggerAction(kind=kind, params=params)


# ------------------------------------------------------------------ 评估引擎


class ScriptEngine:
    """触发器评估引擎：逐帧评估脚本触发器（once/cooldown 门控），驱动任务事件。"""

    def __init__(self, script: MissionScript, context: ScriptContext) -> None:
        self.script = script
        self.context = context
        self.tick_count = 0
        self._fire_counts: dict[str, int] = {}
        self._last_fire_ticks: dict[str, int] = {}

    def attach(self, battle: BattleService) -> None:
        """挂到战局：battle_service.tick 每帧末尾回调本引擎（触发器评估驱动点）。"""
        battle.tick_hook = self.tick

    def tick(self, battle: BattleService) -> list[str]:
        """评估一帧：按登记序评估触发器，返回本帧触发的 trigger_id（PyCC2 模式）。

        W1 任务 2：每帧末尾更新目标状态 + 自动胜负判定（Trigger 触发 → Objective 完成）。
        """
        self.tick_count += 1
        fired: list[str] = []
        for trigger in self.script.triggers:
            if trigger.once and self._fire_counts.get(trigger.trigger_id, 0) > 0:
                continue
            last_fire = self._last_fire_ticks.get(trigger.trigger_id)
            if (
                trigger.cooldown_ticks > 0
                and last_fire is not None
                and self.tick_count - last_fire < trigger.cooldown_ticks
            ):
                continue
            if not evaluate_condition(trigger.condition, battle, self.tick_count):
                continue
            for action in trigger.actions:
                apply_action(action, self.context)
            self._fire_counts[trigger.trigger_id] = self._fire_counts.get(trigger.trigger_id, 0) + 1
            self._last_fire_ticks[trigger.trigger_id] = self.tick_count
            fired.append(trigger.trigger_id)

        # W1 任务 2：每帧末尾更新目标状态 + 自动胜负判定
        self._update_objectives(battle)

        return fired

    def fire_count(self, trigger_id: str) -> int:
        """指定触发器的累计触发次数（验收/观察用）。"""
        return self._fire_counts.get(trigger_id, 0)

    def _update_objectives(self, battle: BattleService) -> None:
        """W1 任务 2：每 tick 更新目标状态 + 自动胜负判定。

        流程：
            1. 检查每个 PENDING 目标的关联 Trigger 是否触发
            2. Trigger 触发 → Objective.status = COMPLETED
            3. 所有 PRIMARY 完成 → ScriptContext.outcome = "victory"
            4. 任意 PRIMARY 失败 → ScriptContext.outcome = "defeat"
        """
        if self.script.objectives is None:
            return  # 无目标系统 → 跳过（胜负由 Trigger 显式 victory/defeat 动作决定）

        from pymc2.domain.scripting.objective import ObjectiveStatus

        # 更新目标状态（Trigger 触发 → Objective 完成）
        for obj in self.script.objectives.objectives:
            if obj.status != ObjectiveStatus.PENDING:
                continue  # 已完成/失败 → 跳过（状态不可逆）

            # W1 简化：obj.trigger_id 匹配同名 Trigger ID，Trigger 累计触发次数 >0 → Objective 完成
            if obj.trigger_id and self.fire_count(obj.trigger_id) > 0:
                obj.status = ObjectiveStatus.COMPLETED

        # 自动胜负判定（PRIMARY 目标驱动）
        if self.script.objectives.all_primary_completed():
            self.context.outcome = "victory"  # 覆盖 ScriptContext.outcome（与 victory 动作等价）
        if self.script.objectives.any_primary_failed():
            self.context.outcome = "defeat"  # 覆盖 ScriptContext.outcome（与 defeat 动作等价）
