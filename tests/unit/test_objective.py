"""W1 任务 2：Objective 系统单元测试。"""

from __future__ import annotations

from pymc2.domain.scripting.objective import (
    Objective,
    ObjectiveSet,
    ObjectiveStatus,
    ObjectiveType,
)


class TestObjectiveDataModel:
    """验证 Objective 数据类字段约束。"""

    def test_objective_mutable_status_update(self):
        """Objective 可变，支持 status 状态更新（ScriptEngine._update_objectives 需要）。"""
        obj = Objective(
            id="obj_01",
            type=ObjectiveType.PRIMARY,
            description="Destroy enemy base",
            trigger_id="trigger_01",
            status=ObjectiveStatus.PENDING,
        )
        # Objective 设计为可变（非 frozen），允许 status 字段更新
        obj.status = ObjectiveStatus.COMPLETED
        assert obj.status == ObjectiveStatus.COMPLETED

    def test_objective_type_enum(self):
        """ObjectiveType 三分类枚举值。"""
        assert ObjectiveType.PRIMARY.value == "primary"
        assert ObjectiveType.SECONDARY.value == "secondary"
        assert ObjectiveType.BONUS.value == "bonus"

    def test_objective_status_enum(self):
        """ObjectiveStatus 状态机枚举值。"""
        assert ObjectiveStatus.PENDING.value == "pending"
        assert ObjectiveStatus.COMPLETED.value == "completed"
        assert ObjectiveStatus.FAILED.value == "failed"


class TestObjectiveSetVictoryJudgment:
    """验证 ObjectiveSet 胜负判定 API（W1 设计 §2.1.3）。"""

    def test_all_primary_completed_true(self):
        """所有 PRIMARY 完成 → all_primary_completed() 返回 True。"""
        objectives = (
            Objective(
                "p1",
                ObjectiveType.PRIMARY,
                "Primary 1",
                None,
                ObjectiveStatus.COMPLETED,
            ),
            Objective(
                "p2",
                ObjectiveType.PRIMARY,
                "Primary 2",
                None,
                ObjectiveStatus.COMPLETED,
            ),
            Objective(
                "s1",
                ObjectiveType.SECONDARY,
                "Secondary 1",
                None,
                ObjectiveStatus.PENDING,
            ),
        )
        obj_set = ObjectiveSet(objectives=objectives)
        assert obj_set.all_primary_completed() is True

    def test_all_primary_completed_false_pending(self):
        """存在 PRIMARY 未完成 → all_primary_completed() 返回 False。"""
        objectives = (
            Objective(
                "p1",
                ObjectiveType.PRIMARY,
                "Primary 1",
                None,
                ObjectiveStatus.COMPLETED,
            ),
            Objective(
                "p2",
                ObjectiveType.PRIMARY,
                "Primary 2",
                None,
                ObjectiveStatus.PENDING,
            ),
        )
        obj_set = ObjectiveSet(objectives=objectives)
        assert obj_set.all_primary_completed() is False

    def test_all_primary_completed_no_primary(self):
        """无 PRIMARY 目标 → all_primary_completed() 返回 False。"""
        objectives = (
            Objective(
                "s1",
                ObjectiveType.SECONDARY,
                "Secondary 1",
                None,
                ObjectiveStatus.COMPLETED,
            ),
        )
        obj_set = ObjectiveSet(objectives=objectives)
        assert obj_set.all_primary_completed() is False

    def test_any_primary_failed_true(self):
        """任意 PRIMARY 失败 → any_primary_failed() 返回 True。"""
        objectives = (
            Objective(
                "p1",
                ObjectiveType.PRIMARY,
                "Primary 1",
                None,
                ObjectiveStatus.COMPLETED,
            ),
            Objective(
                "p2",
                ObjectiveType.PRIMARY,
                "Primary 2",
                None,
                ObjectiveStatus.FAILED,
            ),
        )
        obj_set = ObjectiveSet(objectives=objectives)
        assert obj_set.any_primary_failed() is True

    def test_any_primary_failed_false(self):
        """无 PRIMARY 失败 → any_primary_failed() 返回 False。"""
        objectives = (
            Objective(
                "p1",
                ObjectiveType.PRIMARY,
                "Primary 1",
                None,
                ObjectiveStatus.COMPLETED,
            ),
            Objective(
                "p2",
                ObjectiveType.PRIMARY,
                "Primary 2",
                None,
                ObjectiveStatus.PENDING,
            ),
            Objective(
                "s1",
                ObjectiveType.SECONDARY,
                "Secondary 1",
                None,
                ObjectiveStatus.FAILED,
            ),
        )
        obj_set = ObjectiveSet(objectives=objectives)
        assert obj_set.any_primary_failed() is False

    def test_any_primary_failed_no_primary(self):
        """无 PRIMARY 目标 → any_primary_failed() 返回 False。"""
        objectives = (
            Objective(
                "s1",
                ObjectiveType.SECONDARY,
                "Secondary 1",
                None,
                ObjectiveStatus.FAILED,
            ),
        )
        obj_set = ObjectiveSet(objectives=objectives)
        assert obj_set.any_primary_failed() is False


class TestObjectiveSetFiltering:
    """验证 ObjectiveSet 分类筛选方法（by_type）。"""

    def test_by_type_primary(self):
        """by_type(PRIMARY) 返回 PRIMARY 类型目标。"""
        objectives = [
            Objective("p1", ObjectiveType.PRIMARY, "P1", None, ObjectiveStatus.PENDING),
            Objective("s1", ObjectiveType.SECONDARY, "S1", None, ObjectiveStatus.PENDING),
            Objective("p2", ObjectiveType.PRIMARY, "P2", None, ObjectiveStatus.PENDING),
        ]
        obj_set = ObjectiveSet(objectives=objectives)
        primary = obj_set.by_type(ObjectiveType.PRIMARY)
        assert len(primary) == 2
        assert all(obj.type == ObjectiveType.PRIMARY for obj in primary)

    def test_by_type_secondary(self):
        """by_type(SECONDARY) 返回 SECONDARY 类型目标。"""
        objectives = [
            Objective("p1", ObjectiveType.PRIMARY, "P1", None, ObjectiveStatus.PENDING),
            Objective("s1", ObjectiveType.SECONDARY, "S1", None, ObjectiveStatus.PENDING),
            Objective("s2", ObjectiveType.SECONDARY, "S2", None, ObjectiveStatus.PENDING),
        ]
        obj_set = ObjectiveSet(objectives=objectives)
        secondary = obj_set.by_type(ObjectiveType.SECONDARY)
        assert len(secondary) == 2
        assert all(obj.type == ObjectiveType.SECONDARY for obj in secondary)

    def test_by_type_bonus(self):
        """by_type(BONUS) 返回 BONUS 类型目标。"""
        objectives = [
            Objective("p1", ObjectiveType.PRIMARY, "P1", None, ObjectiveStatus.PENDING),
            Objective("b1", ObjectiveType.BONUS, "B1", None, ObjectiveStatus.PENDING),
        ]
        obj_set = ObjectiveSet(objectives=objectives)
        bonus = obj_set.by_type(ObjectiveType.BONUS)
        assert len(bonus) == 1
        assert bonus[0].type == ObjectiveType.BONUS

    def test_by_id_found(self):
        """by_id() 按 ID 查找目标成功。"""
        objectives = [
            Objective("p1", ObjectiveType.PRIMARY, "P1", None, ObjectiveStatus.PENDING),
            Objective("s1", ObjectiveType.SECONDARY, "S1", None, ObjectiveStatus.PENDING),
        ]
        obj_set = ObjectiveSet(objectives=objectives)
        found = obj_set.by_id("s1")
        assert found is not None
        assert found.id == "s1"
        assert found.type == ObjectiveType.SECONDARY

    def test_by_id_not_found(self):
        """by_id() 未找到目标返回 None。"""
        objectives = [
            Objective("p1", ObjectiveType.PRIMARY, "P1", None, ObjectiveStatus.PENDING),
        ]
        obj_set = ObjectiveSet(objectives=objectives)
        found = obj_set.by_id("nonexistent")
        assert found is None
