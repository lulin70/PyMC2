"""P1-B 交战联动单测：获取过滤（被挡不锁定）/ 击发复验（锁定后被挡不新增弹丸、
冷却照常走、恢复即射）/ 显式 ATTACK_OBJECT 被挡失败惯例 / HUD NO LOS 提示。
（docs/P1B_DESIGN_20261121.md §2/§3；自动交战存量语义见 test_auto_engage.py）
"""

from __future__ import annotations

import pygame
import pytest

from pymc2.domain.elevation import ElevationGrid
from pymc2.domain.entities.mech import MAP_TILES, TILE_SIZE_PX, Faction, Mech, Weapon
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType, TacticalOrder
from pymc2.presentation.renderer import Renderer
from pymc2.services.battle_service import BattleService
from pymc2.services.order_dispatcher import OrderDispatcher

PLAYER_TILE = (6, 10)
WALL_TILE = (7, 10)  # (6,10)→(9,10) 直线途经 tile：1 级即挡地对地视线


class StaticRng:
    """固定随机源：random() 恒返回 value（0.0 必命中）。"""

    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value

    def uniform(self, a: float, b: float) -> float:
        return (a + b) / 2


def _grid(elev: dict[tuple[int, int], int]) -> ElevationGrid:
    rows = [[0] * MAP_TILES for _ in range(MAP_TILES)]
    for (x, y), value in elev.items():
        rows[y][x] = value
    return ElevationGrid(rows)


def _tile_center(tile: tuple[int, int]) -> tuple[float, float]:
    return (tile[0] * TILE_SIZE_PX + TILE_SIZE_PX / 2, tile[1] * TILE_SIZE_PX + TILE_SIZE_PX / 2)


def make_battle(elev: dict[tuple[int, int], int] | None = None) -> tuple[BattleService, OrderDispatcher]:
    """单机甲战局 + 已接线 dispatcher + 可选手写高程。"""
    battle = BattleService(rng=StaticRng(0.0), elevation=_grid(elev or {}))
    dispatcher = OrderDispatcher()
    battle.dispatcher = dispatcher
    battle.add_mech(Mech("px-01", Faction.PLAYER, *_tile_center(PLAYER_TILE), weapon=Weapon("ppc", 25, 420.0, 2.0)))
    return battle, dispatcher


def add_enemy(battle: BattleService, mech_id: str = "ex-01", tile: tuple[int, int] = (9, 10)) -> Mech:
    mech = Mech(mech_id, Faction.ENEMY, *_tile_center(tile), weapon=Weapon("mlaser", 15, 360.0, 2.5))
    battle.add_mech(mech)
    return mech


def player_attack(unit_id: str, target_id: str) -> TacticalOrder:
    """构造玩家显式攻击指令（origin=PLAYER）。"""
    return TacticalOrder(
        order_type=OrderType.ATTACK_OBJECT,
        unit_ids=(unit_id,),
        origin=OrderOrigin.PLAYER,
        target_tile=None,
        target_unit_id=target_id,
    )


def self_order_targets(dispatcher: OrderDispatcher, unit_id: str = "px-01") -> list[str | None]:
    """指定机甲的 SELF 源自动交战指令目标序列（旧→新；按单位过滤敌方还击）。"""
    return [
        order.target_unit_id
        for order in dispatcher.order_log
        if order.origin is OrderOrigin.SELF and order.unit_ids == (unit_id,)
    ]


def player_shots(battle: BattleService) -> list[object]:
    return [p for p in battle.projectiles if p.attacker_id == "px-01"]


class TestAcquisitionFilter:
    def test_blocked_enemy_not_locked(self) -> None:
        """山体隔开：射程内敌机甲不锁定、无 SELF 指令、无弹丸。"""
        battle, dispatcher = make_battle({WALL_TILE: 1})
        add_enemy(battle)
        battle.tick(1 / 60)
        player = battle.get("px-01")
        assert player is not None
        assert player.attack_target_id is None  # 视线被挡：不锁定
        assert self_order_targets(dispatcher) == []
        assert not battle.projectiles

    def test_locks_once_enemy_moves_out(self) -> None:
        """挪开即锁定：敌机甲挪到通视 tile → 锁定并开火。"""
        battle, dispatcher = make_battle({WALL_TILE: 1})
        enemy = add_enemy(battle)
        battle.tick(1 / 60)
        player = battle.get("px-01")
        assert player is not None
        assert player.attack_target_id is None
        enemy.x, enemy.y = _tile_center((6, 9))  # 与射手相邻：无途经 tile → 通视
        battle.tick(1 / 60)
        assert player.attack_target_id == enemy.id
        assert self_order_targets(dispatcher) == [enemy.id]
        assert len(player_shots(battle)) == 1

    def test_blocked_nearest_skipped_for_visible_farther(self) -> None:
        """两个敌机甲：被挡的更近敌跳过，锁通视的更远敌。"""
        battle, dispatcher = make_battle({WALL_TILE: 1})
        blocked_near = add_enemy(battle, "ex-near", (9, 10))  # 96px，被 (7,10) 山脊挡
        visible_far = add_enemy(battle, "ex-far", (1, 10))  # 160px，通视
        battle.tick(1 / 60)
        player = battle.get("px-01")
        assert player is not None
        assert player.attack_target_id == visible_far.id
        assert self_order_targets(dispatcher) == [visible_far.id]
        assert player.attack_target_id != blocked_near.id


class TestFireGate:
    def _locked_battle(self) -> tuple[BattleService, OrderDispatcher, Mech, Mech]:
        """通视锁定战局：山脊在 (11,10)（锁定后敌挪入其背后即被挡）。

        首 tick 双方互射各一发（StaticRng(0.0) 必命中）；后续断言按 attacker_id 过滤。
        """
        battle, dispatcher = make_battle({(11, 10): 1})
        enemy = add_enemy(battle)
        battle.tick(1 / 60)
        player = battle.get("px-01")
        assert player is not None
        assert player.attack_target_id == enemy.id
        assert player.weapon.cd_remaining == 2.0  # 已击发：进入冷却
        return battle, dispatcher, player, enemy

    def test_locked_then_blocked_suppresses_fire(self) -> None:
        """击发复验：锁定后目标挪入山脊背后 → 不新增弹丸、锁定保持、SELF 指令不下发。"""
        battle, dispatcher, player, enemy = self._locked_battle()
        shots_after_lock = len(player_shots(battle))
        enemy.x, enemy.y = _tile_center((13, 10))  # 挪入山脊 (11,10) 背后
        player.weapon.cd_remaining = 0.0  # 冷却已过：若无视线复验将立即再射
        battle.tick(1 / 60)
        assert player.attack_target_id == enemy.id  # 锁定保持（被挡不清锁）
        assert self_order_targets(dispatcher) == [enemy.id]  # 被挡期间不下发（防日志刷屏）
        assert len(player_shots(battle)) == shots_after_lock  # 不新增弹丸
        assert player.weapon.cd_remaining == 0.0  # 未击发 → 不写冷却

    def test_cooldown_keeps_ticking_while_blocked(self) -> None:
        """冷却照常走：被挡期间冷却计时照常推进（与让路「武器照常推进」同款语义）。"""
        battle, _dispatcher, player, enemy = self._locked_battle()
        enemy.x, enemy.y = _tile_center((13, 10))
        player.weapon.cd_remaining = 1.0
        battle.tick(0.5)
        assert player.weapon.cd_remaining == pytest.approx(0.5)

    def test_fires_again_once_line_restored(self) -> None:
        """视线恢复：冷却已过 → 下个评估 tick 立即再射（无需重锁）。"""
        battle, _dispatcher, player, enemy = self._locked_battle()
        enemy.x, enemy.y = _tile_center((13, 10))
        player.weapon.cd_remaining = 0.0
        battle.tick(1 / 60)
        assert len(player_shots(battle)) == 1  # 被挡：不新增
        enemy.x, enemy.y = _tile_center((9, 10))  # 挪回通视位
        battle.tick(1 / 60)
        assert len(player_shots(battle)) == 2  # 视线恢复：立即再射

    def test_direct_fire_at_blocked_target_returns_false(self) -> None:
        """fire_at 咽喉直验：被挡目标手动开火返回 False（无弹丸/无冷却/无产热）。"""
        battle, _dispatcher = make_battle({WALL_TILE: 1})
        enemy = add_enemy(battle)
        player = battle.get("px-01")
        assert player is not None
        assert battle.fire_at("px-01", enemy.id) is False
        assert not battle.projectiles
        assert player.weapon.cd_remaining == 0.0
        assert player.heat.heat == 0.0


class TestExplicitAttackCommand:
    def test_attack_blocked_target_fails_per_convention(self) -> None:
        """显式 ATTACK_OBJECT 被挡：指令入账、dispatch False、锁不落（AI 重发即延迟）。"""
        battle, dispatcher = make_battle({WALL_TILE: 1})
        enemy = add_enemy(battle)
        orders_before = len(dispatcher.order_log)
        assert dispatcher.dispatch(player_attack("px-01", enemy.id), battle) is False
        assert len(dispatcher.order_log) == orders_before + 1  # 失败惯例：指令仍入日志
        player = battle.get("px-01")
        assert player is not None
        assert player.attack_target_id is None  # 锁不落（延迟到视线恢复）
        assert not battle.projectiles
        assert player.weapon.cd_remaining == 0.0

    def test_attack_visible_target_succeeds(self) -> None:
        """通视目标显式攻击：正常击发并落锁（存量语义不变）。"""
        battle, dispatcher = make_battle()
        enemy = add_enemy(battle)
        assert dispatcher.dispatch(player_attack("px-01", enemy.id), battle) is True
        player = battle.get("px-01")
        assert player is not None
        assert player.attack_target_id == enemy.id
        assert len(battle.projectiles) == 1


class _RecordingFont:
    """记录 render 文本的字体包装（委托真字体；断言 HUD 状态行内容）。"""

    def __init__(self, real: pygame.font.Font, texts: list[str]) -> None:
        self._real = real
        self._texts = texts

    def render(self, text: str, *args: object, **kwargs: object) -> pygame.Surface:
        self._texts.append(text)
        return self._real.render(text, *args, **kwargs)  # type: ignore[arg-type]


@pytest.fixture()
def screen() -> pygame.Surface:
    pygame.init()
    try:
        yield pygame.display.set_mode((1280, 720))
    finally:
        pygame.quit()


def _hud_texts(renderer: Renderer) -> list[str]:
    texts: list[str] = []
    assert renderer._font is not None
    renderer._font = _RecordingFont(renderer._font, texts)  # type: ignore[assignment]
    return texts


class TestHudNoLos:
    def test_blocked_lock_shows_no_los(self, screen: pygame.Surface) -> None:
        """锁定目标视线被挡 → 状态行追加 NO LOS。"""
        renderer = Renderer(screen, elevation=_grid({WALL_TILE: 1}))
        texts = _hud_texts(renderer)
        battle, _dispatcher = make_battle({WALL_TILE: 1})
        enemy = add_enemy(battle)
        player = battle.get("px-01")
        assert player is not None
        battle.selected_ids = [player.id]
        player.attack_target_id = enemy.id  # 直置锁定态（被挡）
        renderer.render(battle, 0, 0)
        assert any("NO LOS" in t for t in texts)

    def test_clear_lock_no_no_los(self, screen: pygame.Surface) -> None:
        """锁定目标通视 → 不追加 NO LOS（ELEV 等存量段保持）。"""
        renderer = Renderer(screen, elevation=_grid({WALL_TILE: 1}))
        texts = _hud_texts(renderer)
        battle, _dispatcher = make_battle({WALL_TILE: 1})
        enemy = add_enemy(battle, tile=(3, 10))  # 通视目标
        player = battle.get("px-01")
        assert player is not None
        battle.selected_ids = [player.id]
        player.attack_target_id = enemy.id
        renderer.render(battle, 0, 0)
        assert all("NO LOS" not in t for t in texts)
        assert any("ELEV" in t for t in texts)

    def test_flat_never_shows_no_los(self, screen: pygame.Surface) -> None:
        """flat 快路径恒通 → 永不追加（存量 flat 帧逐字节不变的 HUD 侧保证）。"""
        renderer = Renderer(screen)
        texts = _hud_texts(renderer)
        battle, _dispatcher = make_battle()
        enemy = add_enemy(battle)
        player = battle.get("px-01")
        assert player is not None
        battle.selected_ids = [player.id]
        player.attack_target_id = enemy.id
        renderer.render(battle, 0, 0)
        assert all("NO LOS" not in t for t in texts)

    def test_unselected_or_dead_target_no_no_los(self, screen: pygame.Surface) -> None:
        """未选中 / 目标阵亡 → 不追加 NO LOS。"""
        renderer = Renderer(screen, elevation=_grid({WALL_TILE: 1}))
        texts = _hud_texts(renderer)
        battle, _dispatcher = make_battle({WALL_TILE: 1})
        enemy = add_enemy(battle)
        player = battle.get("px-01")
        assert player is not None
        player.attack_target_id = enemy.id
        renderer.render(battle, 0, 0)  # 未选中
        assert all("NO LOS" not in t for t in texts)
        battle.selected_ids = [player.id]
        enemy.take_damage(10**6)  # 目标阵亡
        renderer.render(battle, 0, 0)
        assert all("NO LOS" not in t for t in texts)
