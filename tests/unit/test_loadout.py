"""W2 装配配置模型测试（FR-1/FR-2，docs/W2_TEST_PLAN_20261128.md §1）。"""

from __future__ import annotations

import pytest

from pymc2.domain.entities.mech import Faction, Mech
from pymc2.domain.entities.mech_chassis import MechLocation
from pymc2.domain.loadout import (
    ARMOR_CAPS,
    TIER_TABLE,
    TONNAGE_MAX,
    LoadoutConfig,
    WeaponSlotConfig,
    tier_for_hull,
)


def _medium_config() -> LoadoutConfig:
    return LoadoutConfig(
        chassis_kind="medium",
        armor_allocation={loc: 1 for loc in ARMOR_CAPS},
        weapons=[
            WeaponSlotConfig("PPC", "ct"),
            WeaponSlotConfig("Small Laser", "ra"),
        ],
    )


class TestLoadoutRoundtrip:
    """FR-1.2：JSON 序列化 roundtrip 逐字段相等。"""

    def test_roundtrip_identity(self):
        cfg = _medium_config()
        restored = LoadoutConfig.from_json(cfg.to_json())
        assert restored == cfg

    def test_roundtrip_weapons_order(self):
        cfg = _medium_config()
        restored = LoadoutConfig.from_json(cfg.to_json())
        assert [(w.spec_name, w.location) for w in restored.weapons] == [(w.spec_name, w.location) for w in cfg.weapons]


class TestTonnage:
    """FR-2.1：吨位汇总 = 底盘基准 + 装甲×0.5 + Σ武器。"""

    def test_total_tonnage_computation(self):
        cfg = _medium_config()
        armor_tons = sum(cfg.armor_allocation.values()) * 0.5
        weapon_tons = 6.0 + 2.0  # PPC 6t + Small Laser 2t
        assert cfg.total_tonnage() == pytest.approx(40.0 + armor_tons + weapon_tons)

    def test_tier_boundaries(self):
        """4 档吨位映射边界（hull=120/121/240/241）。"""
        assert tier_for_hull(120) == "light"
        assert tier_for_hull(121) == "medium"
        assert tier_for_hull(240) == "heavy"
        assert tier_for_hull(241) == "assault"

    def test_tonnage_max_table(self):
        assert TONNAGE_MAX == {
            "light": 35.0,
            "medium": 55.0,
            "heavy": 75.0,
            "assault": 100.0,
        }


class TestValidation:
    """FR-2.2：错误对象非 raise。"""

    def test_valid_config_returns_none(self):
        assert _medium_config().validate() is None

    def test_overweight_rejected(self):
        cfg = LoadoutConfig(
            chassis_kind="light",
            armor_allocation={loc: cap for loc, cap in ARMOR_CAPS.items()},
            weapons=[WeaponSlotConfig("AC-10", "ct")],
        )
        err = cfg.validate()
        assert err is not None
        assert err.code == "OVERWEIGHT"
        assert err.detail["total"] > err.detail["max"]

    def test_unknown_weapon(self):
        cfg = _medium_config()
        cfg.weapons.append(WeaponSlotConfig("Ghost Gun", "ct"))
        err = cfg.validate()
        assert err is not None and err.code == "UNKNOWN_WEAPON"

    def test_bad_location(self):
        cfg = _medium_config()
        cfg.weapons.append(WeaponSlotConfig("PPC", "wing"))
        err = cfg.validate()
        assert err is not None and err.code == "BAD_LOCATION"

    def test_armor_cap(self):
        cfg = _medium_config()
        cfg.armor_allocation["head"] = ARMOR_CAPS["head"] + 1
        err = cfg.validate()
        assert err is not None and err.code == "ARMOR_CAP"


class TestApply:
    """FR-1.3/FR-5：apply 后 Mech 与配置一致。"""

    def test_apply_armor_and_weapons(self):
        cfg = _medium_config()
        mech = Mech("px-01", Faction.PLAYER, 0.0, 0.0)
        cfg.apply(mech)
        for loc_str, points in cfg.armor_allocation.items():
            assert mech.chassis.parts[MechLocation(loc_str)].armor == pytest.approx(points)
        assert len(mech.weapons) == 2
        assert [w.name for w in mech.weapons] == ["PPC", "Small Laser"]

    def test_default_config_aligns_chassis_factory(self):
        """FR-6.1：默认路径与底盘工厂行为一致（零破坏）。"""
        cfg = LoadoutConfig.default_config("medium")
        assert cfg.validate() is None
        mech = Mech("px-01", Faction.PLAYER, 0.0, 0.0)
        cfg.apply(mech)
        assert mech.weapons[0].name == "Standard"


class TestTierTable:
    """设计 §2 吨位表单点性。"""

    def test_tier_table_monotonic(self):
        caps = [cap for cap, _t, _b in TIER_TABLE]
        assert caps == sorted(caps)
