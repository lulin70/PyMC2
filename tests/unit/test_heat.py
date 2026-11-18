"""HeatSystem 单测（M3）：热量累积/散热/钳制、精度阈值、强制 shutdown。"""

from __future__ import annotations

import pytest

from pymc2.domain.systems.heat import (
    ACCURACY_PENALTY_MULTIPLIER,
    COOLING_RATE_PER_S,
    MAX_HEAT,
    SHUTDOWN_DURATION_S,
    HeatSystem,
)


class TestAccumulation:
    def test_starts_cool(self) -> None:
        assert HeatSystem().heat == 0.0

    def test_add_heat_accumulates(self) -> None:
        heat = HeatSystem()
        heat.add_heat(5.0)
        heat.add_heat(10.0)
        assert heat.heat == pytest.approx(15.0)

    def test_add_heat_clamps_at_max(self) -> None:
        heat = HeatSystem()
        heat.add_heat(150.0)
        assert heat.heat == MAX_HEAT

    def test_add_heat_clamps_at_zero(self) -> None:
        heat = HeatSystem()
        heat.add_heat(5.0)
        heat.add_heat(-10.0)
        assert heat.heat == 0.0


class TestCooling:
    def test_cools_at_base_rate(self) -> None:
        heat = HeatSystem()
        heat.add_heat(10.0)
        heat.tick_cooling(2.0)
        assert heat.heat == pytest.approx(10.0 - COOLING_RATE_PER_S * 2.0)

    def test_cooling_floors_at_zero(self) -> None:
        heat = HeatSystem()
        heat.add_heat(1.0)
        heat.tick_cooling(10.0)
        assert heat.heat == 0.0

    def test_extreme_ambient_still_floors_at_zero(self) -> None:
        heat = HeatSystem()
        heat.add_heat(1.0)
        heat.tick_cooling(10.0, ambient_modifier=100.0)
        assert heat.heat == 0.0

    def test_ambient_modifier_reduces_cooling(self) -> None:
        """环境加成预留接口：热区（负值）压低净散热率。"""
        heat = HeatSystem()
        heat.add_heat(10.0)
        heat.tick_cooling(2.0, ambient_modifier=-COOLING_RATE_PER_S)
        assert heat.heat == pytest.approx(10.0)

    def test_ambient_modifier_boosts_cooling(self) -> None:
        heat = HeatSystem()
        heat.add_heat(10.0)
        heat.tick_cooling(2.0, ambient_modifier=0.5)
        assert heat.heat == pytest.approx(10.0 - (COOLING_RATE_PER_S + 0.5) * 2.0)


class TestAccuracyThreshold:
    def test_below_threshold_has_no_penalty(self) -> None:
        heat = HeatSystem()
        heat.add_heat(79.9)
        assert heat.accuracy_multiplier == 1.0

    def test_at_threshold_is_penalized(self) -> None:
        heat = HeatSystem()
        heat.add_heat(80.0)
        assert heat.accuracy_multiplier == pytest.approx(ACCURACY_PENALTY_MULTIPLIER)

    def test_above_threshold_is_penalized(self) -> None:
        heat = HeatSystem()
        heat.add_heat(95.0)
        assert heat.accuracy_multiplier == pytest.approx(0.7)


class TestShutdown:
    def test_reaching_max_heat_triggers_shutdown(self) -> None:
        heat = HeatSystem()
        heat.add_heat(MAX_HEAT)
        assert heat.shutdown_active is True
        assert heat.shutdown_remaining == pytest.approx(SHUTDOWN_DURATION_S)

    def test_near_max_heat_does_not_trigger(self) -> None:
        heat = HeatSystem()
        heat.add_heat(99.9)
        assert heat.shutdown_active is False

    def test_cooling_continues_during_shutdown(self) -> None:
        heat = HeatSystem()
        heat.add_heat(MAX_HEAT)
        heat.tick_cooling(1.0)
        assert heat.shutdown_active is True
        assert heat.heat == pytest.approx(MAX_HEAT - COOLING_RATE_PER_S)

    def test_shutdown_recovers_after_duration(self) -> None:
        heat = HeatSystem()
        heat.add_heat(MAX_HEAT)
        heat.tick_cooling(SHUTDOWN_DURATION_S)
        assert heat.shutdown_active is False
        assert heat.heat == pytest.approx(MAX_HEAT - COOLING_RATE_PER_S * SHUTDOWN_DURATION_S)

    def test_shutdown_timer_never_negative(self) -> None:
        heat = HeatSystem()
        heat.add_heat(MAX_HEAT)
        heat.tick_cooling(10.0)
        assert heat.shutdown_remaining == 0.0
