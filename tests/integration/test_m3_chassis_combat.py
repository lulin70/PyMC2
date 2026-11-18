"""M3 集成：fire_at → 部位伤害 → 武器失效链路；热量接线（产热/精度惩罚/强制关机）。"""

from __future__ import annotations

import pytest

from pymc2.domain.entities.mech import Faction, Mech, Weapon
from pymc2.domain.entities.mech_chassis import MechLocation, make_medium_chassis
from pymc2.services.battle_service import BattleService, create_m3_battle


class StaticRng:
    """固定随机源：random() 恒返回 value。"""

    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


class QueueRng:
    """脚本化随机源：按序吐出 values（循环），供部位命中定向判定。"""

    def __init__(self, values: list[float]) -> None:
        self._values = list(values)
        self._index = 0

    def random(self) -> float:
        value = self._values[self._index % len(self._values)]
        self._index += 1
        return value

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


def settle(battle: BattleService, *, max_ticks: int = 20) -> None:
    """推进至全部弹丸落地。"""
    for _ in range(max_ticks):
        battle.tick(0.25)
        if not battle.projectiles:
            return


class TestLocationDamageChain:
    def test_fire_routes_damage_to_rolled_location(self) -> None:
        """QueueRng([0.0, 0.60])：0.0 命中、0.60 落 LA 桶（累计分布 LA 区间）。"""
        battle = create_m3_battle(rng=QueueRng([0.0, 0.60]))
        enemy = battle.get("ex-01")
        assert enemy is not None
        assert battle.fire_at("px-01", "ex-01") is True
        settle(battle)
        # 25 伤害沿传导链 LA→RA→LL 摧毁三部位，余 4 打在 RL 装甲（不触及 CT）
        assert enemy.chassis.destroyed_locations == frozenset({MechLocation.LA, MechLocation.RA, MechLocation.LL})
        assert enemy.chassis.is_destroyed(MechLocation.CT) is False
        assert enemy.hull == 56.0  # 中型底盘 81 - 25（总量精确扣减）
        assert enemy.alive is True

    def test_precise_arm_hit_disables_mounted_weapon(self) -> None:
        battle = BattleService(rng=QueueRng([0.0, 0.60]))
        battle.add_mech(Mech("a", Faction.PLAYER, 0.0, 0.0, weapon=Weapon("light", 7, 400.0, 1.0)))
        battle.add_mech(
            Mech(
                "b",
                Faction.ENEMY,
                100.0,
                0.0,
                chassis=make_medium_chassis(),
                weapon=Weapon("armgun", 5, 300.0, 1.0, mounted_location=MechLocation.LA),
            )
        )
        target = battle.get("b")
        assert target is not None
        assert battle.fire_at("a", "b") is True
        settle(battle)
        # 7 伤害恰好打穿 LA（armor 4 + structure 3），无溢出
        assert target.chassis.is_destroyed(MechLocation.LA) is True
        assert target.chassis.is_destroyed(MechLocation.RA) is False
        assert target.weapon_operational is False
        assert target.alive is True
        assert target.hull == 74.0
        # 武器失效：目标开火被拒
        assert battle.fire_at("b", "a") is False

    def test_ct_destruction_kills_via_fire_chain(self) -> None:
        battle = create_m3_battle(rng=StaticRng(0.0))  # 恒命中、部位恒 CT
        enemy = battle.get("ex-01")
        assert enemy is not None
        assert battle.fire_at("px-01", "ex-01") is True
        settle(battle)
        assert enemy.chassis.is_destroyed(MechLocation.CT) is True
        assert enemy.chassis.destroyed_locations == frozenset({MechLocation.CT})
        assert enemy.alive is False
        assert enemy.hull == 56.0  # 81 - 25（CT 21 + 溢出 4 打在 LT 装甲）

    def test_fire_at_dead_target_rejected_after_chassis_death(self) -> None:
        battle = create_m3_battle(rng=StaticRng(0.0))
        assert battle.fire_at("px-01", "ex-01") is True
        settle(battle)
        assert battle.fire_at("px-01", "ex-01") is False


class TestHeatWiring:
    def test_fire_adds_weapon_heat(self) -> None:
        battle = create_m3_battle(rng=StaticRng(0.0))
        player = battle.get("px-01")
        assert player is not None
        assert battle.fire_at("px-01", "ex-01") is True
        assert player.heat.heat == pytest.approx(5.0)

    def test_overheat_reduces_hit_probability(self) -> None:
        """r=0.6：正常 p=0.8125 命中；heat≥80 惩罚 p=0.56875 未命中。"""
        normal = BattleService(rng=StaticRng(0.6))
        normal.add_mech(Mech("a", Faction.PLAYER, 0.0, 0.0, weapon=Weapon("ppc", 25, 400.0, 1.0)))
        normal.add_mech(Mech("b", Faction.ENEMY, 100.0, 0.0))
        assert normal.fire_at("a", "b") is True
        assert normal.projectiles[0].will_hit is True

        overheated = BattleService(rng=StaticRng(0.6))
        hot = Mech("a", Faction.PLAYER, 0.0, 0.0, weapon=Weapon("ppc", 25, 400.0, 1.0))
        hot.heat.add_heat(80.0)
        overheated.add_mech(hot)
        overheated.add_mech(Mech("b", Faction.ENEMY, 100.0, 0.0))
        assert overheated.fire_at("a", "b") is True
        assert overheated.projectiles[0].will_hit is False

    def test_shutdown_rejects_fire_and_move_then_recovers(self) -> None:
        battle = create_m3_battle(rng=StaticRng(0.0))
        player = battle.get("px-01")
        assert player is not None
        player.heat.add_heat(100.0)
        assert player.heat.shutdown_active is True
        assert battle.fire_at("px-01", "ex-01") is False
        assert battle.move_mech("px-01", 400.0, 336.0) is False
        assert battle.fire_at("px-01", "ex-01") is False  # 关机期内冷却中亦被拒
        battle.tick(2.0)  # 只散热：heat 100 → 99，关机解除
        assert player.heat.shutdown_active is False
        assert player.heat.heat == pytest.approx(99.0)
        assert battle.move_mech("px-01", 400.0, 336.0) is True
        assert battle.fire_at("px-01", "ex-01") is True

    def test_repeated_fire_accumulates_heat_toward_shutdown(self) -> None:
        battle = BattleService(rng=StaticRng(0.0))
        battle.add_mech(Mech("a", Faction.PLAYER, 0.0, 0.0, weapon=Weapon("ppc", 25, 400.0, 0.0)))
        battle.add_mech(Mech("b", Faction.ENEMY, 100.0, 0.0))
        for _ in range(3):
            assert battle.fire_at("a", "b") is True  # 冷却 0：三连发，弹丸未落地不散热
        attacker = battle.get("a")
        assert attacker is not None
        assert attacker.heat.heat == pytest.approx(15.0)  # 3 × 5 产热
