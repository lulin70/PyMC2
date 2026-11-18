"""任务脚本化（M6）：Trigger DSL（条件-动作）+ MissionScript 加载与逐帧评估引擎。

参照 PyCC2 TriggerSystem（registry + once/cooldown 门控）与 MC2 ABLT/ 任务脚本精神，
自建 declarative dataclass 语法，JSON 可加载（loader 在 mission_script.py）。
"""

from pymc2.domain.scripting.mission_script import (
    MissionScript,
    MissionScriptError,
    ScriptContext,
    ScriptEngine,
    load_mission_script,
    mission_script_from_dict,
)
from pymc2.domain.scripting.trigger import (
    ACTION_KINDS,
    CONDITION_KINDS,
    Trigger,
    TriggerAction,
    TriggerCondition,
    apply_action,
    evaluate_condition,
)

__all__ = [
    "ACTION_KINDS",
    "CONDITION_KINDS",
    "MissionScript",
    "MissionScriptError",
    "ScriptContext",
    "ScriptEngine",
    "Trigger",
    "TriggerAction",
    "TriggerCondition",
    "apply_action",
    "evaluate_condition",
    "load_mission_script",
    "mission_script_from_dict",
]
