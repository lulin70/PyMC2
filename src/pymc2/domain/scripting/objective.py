"""任务目标系统：三分类目标（PRIMARY/SECONDARY/BONUS）+ 胜负判定。

W1 任务 2：HUD 层目标展示 + 自动胜负判定（PRIMARY 全完成 → 胜利，任意失败 → 失败）。
目标状态由 ScriptEngine tick 更新（关联 Trigger 触发状态）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ObjectiveType(str, Enum):
    """目标类型三分类。"""

    PRIMARY = "primary"  # 主要目标（全部完成 → 胜利）
    SECONDARY = "secondary"  # 次要目标（可选）
    BONUS = "bonus"  # 奖励目标（隐藏/可选）


class ObjectiveStatus(str, Enum):
    """目标状态（单向状态机）。"""

    PENDING = "pending"  # 未完成
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 失败


@dataclass
class Objective:
    """单个目标（可变状态）。

    状态机：PENDING → COMPLETED/FAILED（单向，不可逆）。
    trigger_id：关联的 Trigger ID（W1 简化：同名 Trigger 触发 → 目标完成）。
    """

    id: str  # 目标唯一 ID（JSON 中指定）
    type: ObjectiveType  # 目标类型（PRIMARY/SECONDARY/BONUS）
    description: str  # 显示文本（HUD 面板）
    trigger_id: str | None = None  # 关联 Trigger ID（W1 简化，W2+ 支持复杂条件）
    status: ObjectiveStatus = ObjectiveStatus.PENDING  # 当前状态


@dataclass
class ObjectiveSet:
    """任务目标集合（内置胜负判定逻辑）。"""

    objectives: list[Objective]

    def all_primary_completed(self) -> bool:
        """所有主要目标完成 → 胜利条件。"""
        primaries = [obj for obj in self.objectives if obj.type == ObjectiveType.PRIMARY]
        if not primaries:
            return False  # 无 PRIMARY 目标 → 不触发胜利
        return all(obj.status == ObjectiveStatus.COMPLETED for obj in primaries)

    def any_primary_failed(self) -> bool:
        """任何主要目标失败 → 失败条件。"""
        return any(obj.status == ObjectiveStatus.FAILED for obj in self.objectives if obj.type == ObjectiveType.PRIMARY)

    def by_id(self, obj_id: str) -> Objective | None:
        """按 ID 查找目标。"""
        return next((obj for obj in self.objectives if obj.id == obj_id), None)

    def by_type(self, obj_type: ObjectiveType) -> list[Objective]:
        """按类型筛选目标（HUD 分组渲染）。"""
        return [obj for obj in self.objectives if obj.type == obj_type]
