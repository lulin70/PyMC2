"""BattleService 集成测试：move → tick → 到位；fire → tick → 掉血。"""

from __future__ import annotations

from pymc2.domain.entities.mech import Faction, Mech, Weapon
from pymc2.services.battle_service import BattleService, create_m1_battle


class StaticRng:
    """固定随机源：random() 恒返回 value（0.0 必命中，1.0 必未命中）。"""

    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


def make_battle(rng_value: float = 0.0) -> BattleService:
    return create_m1_battle(rng=StaticRng(rng_value))


class TestSelection:
    def test_select_player_mech_at_spawn(self) -> None:
        service = make_battle()
        assert service.select_at(208.0, 336.0) is True
        assert service.selected_id == "px-01"
        assert service.selected is service.get("px-01")

    def test_cannot_select_enemy_mech(self) -> None:
        service = make_battle()
        assert service.select_at(592.0, 336.0) is False
        assert service.selected_id is None

    def test_select_outside_hitbox_fails(self) -> None:
        service = make_battle()
        assert service.select_at(208.0 + 40.0, 336.0) is False


class TestMovement:
    def test_move_order_completes_over_ticks(self) -> None:
        service = make_battle()
        player = service.get("px-01")
        assert player is not None
        assert service.move_mech("px-01", 464.0, 336.0) is True
        for _ in range(20):
            service.tick(0.25)
            if player.move_target is None:
                break
        assert player.move_target is None
        assert player.position == (464.0, 336.0)

    def test_move_unknown_mech_fails(self) -> None:
        assert make_battle().move_mech("nope", 10.0, 10.0) is False


class TestFiring:
    def test_fire_then_tick_reduces_hull(self) -> None:
        service = make_battle(rng_value=0.0)
        enemy = service.get("ex-01")
        assert enemy is not None
        assert service.fire_at("px-01", "ex-01") is True
        assert len(service.projectiles) == 1
        for _ in range(10):
            service.tick(0.25)
            if not service.projectiles:
                break
        assert not service.projectiles
        assert enemy.hull == 75

    def test_miss_deals_no_damage(self) -> None:
        service = make_battle(rng_value=1.0)
        enemy = service.get("ex-01")
        assert enemy is not None
        assert service.fire_at("px-01", "ex-01") is True
        for _ in range(10):
            service.tick(0.25)
            if not service.projectiles:
                break
        assert enemy.hull == 100

    def test_second_fire_blocked_by_cooldown(self) -> None:
        service = make_battle()
        enemy = service.get("ex-01")
        assert enemy is not None
        assert service.fire_at("px-01", "ex-01") is True
        assert service.fire_at("px-01", "ex-01") is False  # 冷却中
        assert len(service.projectiles) == 1
        service.tick(2.0)  # 冷却结束；第一发弹丸（飞行约 0.8s）已命中
        assert not service.projectiles
        assert enemy.hull == 75
        assert service.fire_at("px-01", "ex-01") is True
        assert len(service.projectiles) == 1

    def test_fire_out_of_range_fails(self) -> None:
        service = make_battle()
        far = Mech("ex-far", Faction.ENEMY, 1200.0, 336.0, weapon=Weapon("mlaser", 15, 360.0, 2.5))
        service.add_mech(far)
        assert service.fire_at("px-01", "ex-far") is False
        assert not service.projectiles

    def test_fire_at_dead_target_fails(self) -> None:
        service = make_battle()
        enemy = service.get("ex-01")
        assert enemy is not None
        enemy.take_damage(200)
        assert service.fire_at("px-01", "ex-01") is False

    def test_dead_attacker_cannot_fire(self) -> None:
        service = make_battle()
        player = service.get("px-01")
        assert player is not None
        player.take_damage(200)
        assert service.fire_at("px-01", "ex-01") is False

    def test_nearest_enemy(self) -> None:
        service = make_battle()
        near = Mech("ex-near", Faction.ENEMY, 260.0, 336.0, weapon=Weapon("mlaser", 15, 360.0, 2.5))
        service.add_mech(near)
        player = service.get("px-01")
        assert player is not None
        assert service.nearest_enemy(player) is near

    def test_tick_on_empty_battle_is_safe(self) -> None:
        BattleService().tick(0.016)
