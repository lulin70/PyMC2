"""W2 战斗接入测试（FR-5，docs/W2_TEST_PLAN_20261128.md §1/§2）。

单武器行为一致（零破坏钉）+ 多武器独立 CD（FR-5.3 / 2.1 残留核销）。
"""

from __future__ import annotations

from pymc2.domain.entities.mech import Faction, Mech, WeaponType
from pymc2.domain.entities.mech_chassis import MechLocation
from pymc2.domain.loadout import ARMOR_CAPS, LoadoutConfig, WeaponSlotConfig
from pymc2.services.battle_service import BattleService


def _config(*weapons: tuple[str, str]) -> LoadoutConfig:
    return LoadoutConfig(
        chassis_kind="medium",
        armor_allocation={loc: min(4, cap) for loc, cap in ARMOR_CAPS.items()},
        weapons=[WeaponSlotConfig(name, loc) for name, loc in weapons],
    )


class TestWeaponsProperty:
    """FR-5 存量兼容：单武器恒等退化。"""

    def test_single_weapon_identity(self):
        mech = Mech("px-01", Faction.PLAYER, 0.0, 0.0)
        assert mech.weapons == (mech.weapon,)


class TestMultiWeaponCD:
    """FR-5.3 / 2.1 残留核销：多槽位独立 CD。"""

    def test_independent_cooldowns(self):
        cfg = _config(("Small Laser", "ra"), ("Small Laser", "la"))
        mech = Mech("px-01", Faction.PLAYER, 0.0, 0.0)
        cfg.apply(mech)
        assert len(mech.weapons) == 2
        w0, w1 = mech.weapons
        # 强制两武器同时就绪
        w0.cd_remaining = 0.0
        w1.cd_remaining = 0.0
        battle = BattleService()
        battle.add_mech(mech)
        target = Mech("ex-01", Faction.ENEMY, 100.0, 0.0)
        battle.add_mech(target)
        # 首次开火：选择器命中 w0（首个 ready），仅 w0 进 CD

        ok = battle.fire_at("px-01", "ex-01")
        assert ok
        assert w0.cd_remaining > 0.0
        assert w1.cd_remaining == 0.0  # 独立 CD：未被击发者不受影响


class TestBallisticDispatch:
    """FR-5.2：PPC（ENERGY）弹道分派。"""

    def test_ppc_energy_type(self):
        cfg = _config(("PPC", "ct"))
        mech = Mech("px-01", Faction.PLAYER, 0.0, 0.0)
        cfg.apply(mech)
        assert mech.weapon.wtype is WeaponType.ENERGY


class TestArmorApply:
    """apply 后 8 部位装甲逐值相等（FR-1.3）。"""

    def test_all_locations_applied(self):
        cfg = _config(("Standard", "ct"))
        mech = Mech("px-01", Faction.PLAYER, 0.0, 0.0)
        cfg.apply(mech)
        for loc_str, points in cfg.armor_allocation.items():
            assert mech.chassis.parts[MechLocation(loc_str)].armor == points
