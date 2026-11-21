"""P2 单位谱系集成：载具/炮塔入战局与机甲互算、炮塔守卫、AI 目标优先级、salvage 口径。

Unit 协议泛化后的全链路验证（docs/P2_DESIGN_20261121.md §4/§7）：
- 载具与机甲交战互算：机甲按部位结算、载具按整体 HP 扣减，同一弹道管线；
- 炮塔守卫：射程内自动开火（_auto_engage 泛化零新增分支）、射程外沉默、
  move_mech mobile 门槛拒绝；
- AiCommander 目标优先级：攻击性目标（mobile）> 炮塔；
- salvage：载具/炮塔整体 HP 击毁即全损 → 0 RP（机甲部位残骸 >0 口径不变）。
"""

from __future__ import annotations

import pygame
import pytest

from pymc2.domain.entities.mech import Faction, FireMode, Mech, Weapon, WeaponType
from pymc2.domain.entities.mech_chassis import MechLocation
from pymc2.domain.entities.turret import Turret
from pymc2.domain.entities.vehicle import Vehicle
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType
from pymc2.services.ai_commander import AiCommander
from pymc2.services.battle_service import BattleService
from pymc2.services.order_dispatcher import OrderDispatcher
from pymc2.services.salvage_service import SalvageService


class StaticRng:
    """固定随机源（0.0 → 全命中，uniform 取中值 → 弹道确定性）。"""

    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def random(self) -> float:
        return self.value

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


def _run_until(battle: BattleService, predicate, max_frames: int = 200) -> bool:
    """逐帧推进直至谓词满足（防死循环上限兜底）。"""
    for _ in range(max_frames):
        battle.tick(0.25)
        if predicate():
            return True
    return False


class TestVehicleMechCrossCombat:
    def test_mech_shoots_vehicle_whole_hp(self) -> None:
        """机甲 → 载具：部位判定弹丸落到载具上按整体 HP 扣减（无部位传导）。"""
        battle = BattleService(rng=StaticRng(0.0))
        battle.add_mech(Mech("px-01", Faction.PLAYER, 200.0, 200.0, weapon=Weapon("ppc", 25, 420.0, 2.0)))
        vehicle = Vehicle("ev-01", Faction.ENEMY, 500.0, 200.0, hp=80)
        battle.add_mech(vehicle)
        assert battle.fire_at("px-01", "ev-01") is True
        assert _run_until(battle, lambda: not battle.projectiles)
        assert vehicle.hull == 55.0  # 80 - 25（部位判定结果不放大/不折减）
        assert vehicle.alive is True

    def test_vehicle_shoots_mech_location_damage(self) -> None:
        """载具 → 机甲：载具弹丸按部位权重结算机甲（同一管线，反向口径）。"""
        battle = BattleService(rng=StaticRng(0.0))
        battle.add_mech(Vehicle("ev-01", Faction.ENEMY, 200.0, 200.0, weapon=Weapon("autocannon", 12, 420.0, 2.0)))
        target = Mech("px-01", Faction.PLAYER, 500.0, 200.0)
        battle.add_mech(target)
        assert battle.fire_at("ev-01", "px-01") is True
        assert _run_until(battle, lambda: not battle.projectiles)
        assert target.alive is True
        # 命中必有部位扣减（StaticRng 全命中；总余量精确减 12）
        assert target.hull == pytest.approx(88.0)

    def test_missile_vehicle_volley_drops_vehicle_target(self) -> None:
        """MISSILE 载具齐射击毁载具目标：3 发小伤累计 = 整发伤害。"""
        battle = BattleService(rng=StaticRng(0.0))
        missile_vehicle = Vehicle(
            "ev-01",
            Faction.ENEMY,
            200.0,
            200.0,
            weapon=Weapon("lrm", 30, 420.0, 2.0, wtype=WeaponType.MISSILE),
        )
        battle.add_mech(missile_vehicle)
        target = Vehicle("ev-02", Faction.PLAYER, 500.0, 200.0, hp=28)
        battle.add_mech(target)
        assert battle.fire_at("ev-01", "ev-02") is True
        assert len(battle.projectiles) == 3
        assert _run_until(battle, lambda: not battle.projectiles)
        assert target.hull == 0.0 and target.alive is False


class TestTurretGuard:
    def _guarded_battle(self) -> tuple[BattleService, OrderDispatcher, Mech]:
        battle = BattleService(rng=StaticRng(0.0))
        dispatcher = OrderDispatcher()
        battle.dispatcher = dispatcher
        player = Mech("px-01", Faction.PLAYER, 300.0, 300.0, weapon=Weapon("ppc", 25, 420.0, 2.0))
        battle.add_mech(player)
        turret = Turret(
            "et-01",
            Faction.ENEMY,
            600.0,
            300.0,
            weapon=Weapon("autocannon", 12, 400.0, 2.0),
        )
        battle.add_mech(turret)
        return battle, dispatcher, player

    def test_turret_auto_fires_in_range(self) -> None:
        """守卫开火：玩家进入射程 → 炮塔自动锁定开火（FREE 姿态 + 泛化 _auto_engage）。"""
        battle, dispatcher, player = self._guarded_battle()
        assert _run_until(battle, lambda: any(p.attacker_id == "et-01" for p in battle.projectiles))
        self_orders = [o for o in dispatcher.order_log if o.origin is OrderOrigin.SELF and o.unit_ids == ("et-01",)]
        assert self_orders and self_orders[-1].order_type is OrderType.ATTACK_OBJECT
        assert _run_until(battle, lambda: not battle.projectiles)
        assert player.hull == pytest.approx(88.0)  # 100 - 12：玩家挨炮塔一发

    def test_turret_silent_out_of_range(self) -> None:
        battle = BattleService(rng=StaticRng(0.0))
        battle.dispatcher = OrderDispatcher()
        battle.add_mech(Mech("px-01", Faction.PLAYER, 100.0, 100.0, weapon=Weapon("ppc", 25, 300.0, 2.0)))
        turret = Turret("et-01", Faction.ENEMY, 2000.0, 100.0, weapon=Weapon("autocannon", 12, 400.0, 2.0))
        battle.add_mech(turret)
        for _ in range(10):
            battle.tick(0.25)
        assert not battle.projectiles
        assert turret.attack_target_id is None

    def test_turret_move_rejected(self) -> None:
        battle, _, _ = self._guarded_battle()
        assert battle.move_mech("et-01", 200.0, 200.0) is False
        turret = battle.get("et-01")
        assert turret is not None
        assert turret.move_target is None and (turret.x, turret.y) == (600.0, 300.0)

    def test_turret_fire_mode_hold_silences(self) -> None:
        """火力纪律对炮塔同样生效（HOLD 不自动锁定）。"""
        battle, _, _ = self._guarded_battle()
        assert battle.set_fire_mode("et-01", FireMode.HOLD)
        for _ in range(10):
            battle.tick(0.25)
        assert not battle.projectiles


class TestAiTargetPriority:
    def test_mobile_target_preferred_over_weaker_turret(self) -> None:
        """攻击性目标（载具）> 炮塔：炮塔更弱（hull 20 < 100）也不优先。"""
        battle = BattleService()
        attacker = Mech("ex-01", Faction.ENEMY, 400.0, 300.0)
        battle.add_mech(attacker)
        vehicle = Vehicle("ev-01", Faction.PLAYER, 500.0, 300.0, hp=100)
        battle.add_mech(vehicle)
        turret = Turret("pt-01", Faction.PLAYER, 600.0, 300.0, hp=20)
        battle.add_mech(turret)
        commander = AiCommander(Faction.ENEMY)
        orders = commander.propose(battle)
        attack_orders = [o for o in orders if o.order_type is OrderType.ATTACK_OBJECT]
        assert attack_orders and attack_orders[0].target_unit_id == "ev-01"

    def test_all_mech_ordering_unchanged(self) -> None:
        """全机甲战局排序与存量口径一致（mobile 恒 True 退化回 hull 最低优先）。"""
        battle = BattleService()
        attacker = Mech("ex-01", Faction.ENEMY, 400.0, 300.0)
        battle.add_mech(attacker)
        weak = Mech("px-01", Faction.PLAYER, 500.0, 300.0)
        weak.take_damage(60.0)  # hull 40（等比摊扣，不摧毁部位）：最弱且存活
        strong = Mech("px-02", Faction.PLAYER, 600.0, 300.0)
        battle.add_mech(weak)
        battle.add_mech(strong)
        commander = AiCommander(Faction.ENEMY)
        orders = commander.propose(battle)
        attack_orders = [o for o in orders if o.order_type is OrderType.ATTACK_OBJECT]
        assert attack_orders and attack_orders[0].target_unit_id == "px-01"

    def test_turret_own_side_gets_guard_fallback(self) -> None:
        """己方炮塔不后撤不占区：fallback GUARD 原地死守。"""
        battle = BattleService()
        turret = Turret("et-01", Faction.ENEMY, 600.0, 300.0)
        battle.add_mech(turret)
        commander = AiCommander(Faction.ENEMY)
        orders = commander.propose(battle)
        assert len(orders) == 1
        assert orders[0].order_type is OrderType.GUARD and orders[0].unit_ids == ("et-01",)


class TestSalvageProfiles:
    def test_destroyed_vehicle_salvage_zero(self) -> None:
        """载具整体 HP 击毁即全损 → 0 RP（机甲口径不变，见机甲对照）。"""
        vehicle = Vehicle("ev-01", Faction.ENEMY, 0.0, 0.0, hp=80)
        vehicle.apply_damage(MechLocation.CT, 80.0)
        assert vehicle.alive is False
        assert SalvageService.salvage_value(vehicle) == 0

    def test_destroyed_turret_salvage_zero(self) -> None:
        turret = Turret("et-01", Faction.ENEMY, 0.0, 0.0)
        turret.apply_damage(MechLocation.CT, 60.0)
        assert SalvageService.salvage_value(turret) == 0

    def test_destroyed_mech_with_residue_positive(self) -> None:
        """机甲对照：CT 摧毁致死但其他部位残骸 >0 → salvage >0（存量口径）。"""
        mech = Mech("ex-01", Faction.ENEMY, 0.0, 0.0)
        mech.apply_damage(MechLocation.CT, 40.0)  # CT 透甲摧毁 → alive False，溢出未清空全身
        assert mech.alive is False and mech.hull > 0
        assert SalvageService.salvage_value(mech) > 0


class TestVehicleSpriteRender:
    """资产命中渲染链（PIL fixture pack，真资产零入库）：载具精灵经 resolve_unit_sprite 命中。"""

    def test_vehicle_sprite_resolved_and_blitted(self, tmp_path) -> None:
        from PIL import Image

        from pymc2.infrastructure.mc2_assets import MC2AssetPack
        from pymc2.presentation.renderer import Renderer

        root = tmp_path / "mc2-units"
        tgl = root / "Data" / "TGL" / "128"
        tgl.mkdir(parents=True)
        storm = (90, 110, 130, 255)
        Image.new("RGBA", (32, 32), storm).save(tgl / "Storm.tga")
        pack = MC2AssetPack.load(root)
        assert pack is not None

        pygame.init()
        try:
            screen = pygame.Surface((320, 320))
            battle = BattleService()
            vehicle = Vehicle("ev-01", Faction.ENEMY, 160.0, 160.0, hp=80)
            battle.add_mech(vehicle)
            renderer = Renderer(screen, asset_pack=pack)
            sprite = renderer._unit_sprite(vehicle)
            assert sprite is not None  # vehicle→storm 命中（否则程序化回退）
            renderer.render(battle, 0.0, 0.0)
            assert screen.get_at((160, 160))[:3] == storm[:3]  # 精灵 blit 覆盖程序化方块
        finally:
            pygame.quit()
