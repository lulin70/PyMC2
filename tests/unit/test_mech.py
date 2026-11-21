"""Mech 实体单测：移动、冷却推进、伤害致死、朝向量化（P4）。"""

from __future__ import annotations

import math

import pytest

from pymc2.domain.entities.mech import HEADING_STEPS, Faction, Mech, Weapon, heading_bucket
from pymc2.infrastructure.mesh_render import HEADING_STEPS as RENDER_HEADING_STEPS


def make_mech(x: float = 100.0, y: float = 100.0, *, hull: int = 100) -> Mech:
    return Mech("m-01", Faction.PLAYER, x, y, hull=hull, weapon=Weapon("ppc", 25, 420.0, 2.0))


class TestMovement:
    def test_move_to_sets_target(self) -> None:
        mech = make_mech()
        mech.move_to(196.0, 100.0)
        assert mech.move_target == (196.0, 100.0)

    def test_tick_moves_toward_target_at_fixed_speed(self) -> None:
        mech = make_mech()
        mech.move_to(292.0, 100.0)  # 距离 192px，速度 96px/s
        mech.tick(1.0)
        assert mech.position == (196.0, 100.0)
        assert mech.move_target == (292.0, 100.0)

    def test_tick_arrives_snaps_and_clears_target(self) -> None:
        mech = make_mech()
        mech.move_to(196.0, 100.0)  # 距离 96px，一步到位
        mech.tick(1.0)
        assert mech.position == (196.0, 100.0)
        assert mech.move_target is None

    def test_tick_moves_along_diagonal(self) -> None:
        mech = make_mech()
        mech.move_to(196.0, 196.0)  # 对角线距离约 135.8px
        mech.tick(1.0)
        assert mech.position[0] == pytest.approx(167.85, abs=0.5)
        assert mech.position[1] == pytest.approx(167.85, abs=0.5)

    def test_dead_mech_does_not_move(self) -> None:
        mech = make_mech()
        mech.move_to(196.0, 100.0)
        mech.take_damage(100)
        mech.tick(1.0)
        assert mech.position == (100.0, 100.0)

    def test_tile_derived_from_pixel(self) -> None:
        mech = make_mech(208.0, 336.0)
        assert mech.tile == (6, 10)


class TestHeading:
    """P4 朝向：8 桶量化表、移动方向更新、静止保持；与渲染帧数常量同口径。"""

    def test_bucket_table_clockwise_from_north(self) -> None:
        # (dx, dy) → bucket：北 0 顺时针 45°/步（pygame 世界 y 南为正，北 = −y）。
        assert heading_bucket(0.0, -1.0) == 0  # 北
        assert heading_bucket(1.0, -1.0) == 1  # 东北
        assert heading_bucket(1.0, 0.0) == 2  # 东
        assert heading_bucket(1.0, 1.0) == 3  # 东南
        assert heading_bucket(0.0, 1.0) == 4  # 南
        assert heading_bucket(-1.0, 1.0) == 5  # 西南
        assert heading_bucket(-1.0, 0.0) == 6  # 西
        assert heading_bucket(-1.0, -1.0) == 7  # 西北

    def test_bucket_boundaries_round_to_nearest(self) -> None:
        assert heading_bucket(0.1, -1.0) == 0  # 22.5° 扇区内归北
        assert heading_bucket(1.0, -0.1) == 2  # 67.5° 开外归东
        assert heading_bucket(0.0, 0.0) == 0  # 零向量恒北

    def test_bucket_always_in_range(self) -> None:
        for deg in range(-180, 181, 5):
            rad = math.radians(deg)
            bucket = heading_bucket(math.sin(rad), -math.cos(rad))
            assert 0 <= bucket < HEADING_STEPS

    def test_initial_heading_north(self) -> None:
        assert make_mech().heading == 0

    def test_movement_updates_heading(self) -> None:
        mech = make_mech()
        mech.move_to(100.0, 40.0)  # 北移（y 减小）
        mech.tick(0.5)
        assert mech.heading == 0
        mech2 = make_mech()
        mech2.move_to(160.0, 100.0)  # 东移
        mech2.tick(0.5)
        assert mech2.heading == 2

    def test_heading_kept_after_arrival(self) -> None:
        mech = make_mech()
        mech.move_to(160.0, 100.0)  # 东移一步到位
        mech.tick(1.0)
        assert mech.move_target is None
        assert mech.heading == 2  # 到达吸附帧保持最后移动方向
        mech.tick(1.0)  # 静止 tick 不改朝向
        assert mech.heading == 2

    def test_dead_mech_heading_untouched(self) -> None:
        mech = make_mech()
        mech.move_to(160.0, 100.0)
        mech.take_damage(1000)
        mech.tick(1.0)
        assert mech.heading == 0

    def test_heading_steps_match_render_frames(self) -> None:
        """跨层一致性锁：domain 朝向桶数 = 光栅帧数（8 桶 ↔ 8 帧，不 import 靠测试锁定）。"""
        assert HEADING_STEPS == RENDER_HEADING_STEPS == 8


class TestWeaponCooldown:
    def test_weapon_ready_by_default(self) -> None:
        assert make_mech().weapon.ready is True

    def test_cooldown_progresses_with_tick(self) -> None:
        mech = make_mech()
        mech.weapon.cd_remaining = 2.0
        mech.tick(0.5)
        assert mech.weapon.cd_remaining == pytest.approx(1.5)
        assert mech.weapon.ready is False

    def test_cooldown_never_negative(self) -> None:
        mech = make_mech()
        mech.weapon.cd_remaining = 0.3
        mech.tick(1.0)
        assert mech.weapon.cd_remaining == 0.0
        assert mech.weapon.ready is True


class TestDamage:
    def test_take_damage_reduces_hull(self) -> None:
        mech = make_mech()
        killed = mech.take_damage(30)
        assert mech.hull == 70
        assert mech.alive is True
        assert killed is False

    def test_damage_clamps_at_zero_and_dies(self) -> None:
        mech = make_mech()
        killed = mech.take_damage(1000)
        assert mech.hull == 0
        assert mech.alive is False
        assert killed is True

    def test_exact_lethal_damage_kills(self) -> None:
        mech = make_mech()
        killed = mech.take_damage(100)
        assert mech.hull == 0
        assert mech.alive is False
        assert killed is True

    def test_surviving_damage_keeps_alive(self) -> None:
        mech = make_mech()
        mech.take_damage(99)
        assert mech.hull == 1
        assert mech.alive is True

    def test_hull_ratio(self) -> None:
        mech = make_mech()
        assert mech.hull_ratio == pytest.approx(1.0)
        mech.take_damage(75)
        assert mech.hull_ratio == pytest.approx(0.25)
