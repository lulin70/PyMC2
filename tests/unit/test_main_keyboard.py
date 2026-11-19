"""键盘操控（用户反馈：Mac 上鼠标移动操作不便）+ A 键托管 单元测试。

- 键盘移动：_dispatch_formation_move 走服务层真路径（BattleService +
  OrderDispatcher，无 pygame 显示依赖）——每机自身 tile + 方向×KEYBOARD_MOVE_TILES
  平行平移；堆叠同 tile 目标撞点时横向逐格错开；越界钳制；无选中/阵亡不下发。
- 键位表：方向键四方向 + W/S/D（A 归托管开关，左移依赖方向键 ←）。
- A 键托管：交互模式切换 AiCommander(PLAYER) on/off；demo_mode=True 时无效。
- HUD：AUTO: ON 与键位提示按 render 参数追加（捕获 font.render 文本断言）。
"""

from __future__ import annotations

import pygame

import pymc2.main as main_module
from pymc2.domain.entities.mech import MAP_TILES, TILE_SIZE_PX, Faction, Mech, Weapon
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType
from pymc2.presentation.renderer import Renderer
from pymc2.services.ai_commander import AiCommander
from pymc2.services.battle_service import BattleService
from pymc2.services.order_dispatcher import OrderDispatcher


def _tile_center(tile: tuple[int, int]) -> tuple[float, float]:
    return (tile[0] * TILE_SIZE_PX + TILE_SIZE_PX / 2, tile[1] * TILE_SIZE_PX + TILE_SIZE_PX / 2)


def _mech(mech_id: str, tile: tuple[int, int]) -> Mech:
    return Mech(mech_id, Faction.PLAYER, *_tile_center(tile), weapon=Weapon("ppc", 25, 420.0, 2.0))


def _service_with(*tiles: tuple[int, int]) -> BattleService:
    service = BattleService()
    for index, tile in enumerate(tiles):
        service.add_mech(_mech(f"px-{index + 1:02d}", tile))
    service.selected_ids = [mech.id for mech in service.mechs.values()]
    return service


class _StubGame:
    """_toggle_auto_command 所需最小游戏状态（鸭子类型，免 pygame 显示）。"""

    def __init__(self) -> None:
        self.demo_mode = False
        self.player_commander: AiCommander | None = None


class TestKeyMapping:
    """键位表：方向键四方向全支持；字母区仅 W/S/D（A 归托管，左移依赖 ←）。"""

    def test_keyboard_move_tiles_constant(self) -> None:
        assert main_module.KEYBOARD_MOVE_TILES == 5

    def test_arrow_keys_cover_four_directions(self) -> None:
        assert main_module.MOVE_KEY_DIRECTIONS[pygame.K_UP] == (0, -1)
        assert main_module.MOVE_KEY_DIRECTIONS[pygame.K_DOWN] == (0, 1)
        assert main_module.MOVE_KEY_DIRECTIONS[pygame.K_LEFT] == (-1, 0)
        assert main_module.MOVE_KEY_DIRECTIONS[pygame.K_RIGHT] == (1, 0)

    def test_letter_zone_only_wsd(self) -> None:
        assert main_module.MOVE_KEY_DIRECTIONS[pygame.K_w] == (0, -1)
        assert main_module.MOVE_KEY_DIRECTIONS[pygame.K_s] == (0, 1)
        assert main_module.MOVE_KEY_DIRECTIONS[pygame.K_d] == (1, 0)
        assert pygame.K_a not in main_module.MOVE_KEY_DIRECTIONS  # A 归托管开关
        assert pygame.K_a == main_module.AUTO_COMMAND_KEY


class TestFormationMove:
    """键盘编队移动：每机自身 tile + 方向×N（平行平移），撞点横向错开，越界钳制。"""

    def test_single_mech_moves_own_tile_plus_delta(self) -> None:
        service = _service_with((6, 10))
        dispatched = main_module._dispatch_formation_move(service, OrderDispatcher(), 1, 0)
        mech = service.get("px-01")
        assert dispatched == 1
        assert mech is not None
        assert mech.move_target == _tile_center((11, 10))  # (6,10) + 右×5

    def test_group_moves_parallel_with_distinct_targets(self) -> None:
        service = _service_with((6, 10), (8, 10))
        dispatcher = OrderDispatcher()
        dispatched = main_module._dispatch_formation_move(service, dispatcher, 0, -1)
        a, b = service.get("px-01"), service.get("px-02")
        assert dispatched == 2
        assert a is not None and b is not None
        assert a.move_target == _tile_center((6, 5))  # 各自 tile + 上×5 → 队形保持
        assert b.move_target == _tile_center((8, 5))
        moves = [o for o in dispatcher.order_log if o.order_type is OrderType.MOVE_TO_POINT]
        assert {o.target_tile for o in moves} == {(6, 5), (8, 5)}
        assert all(o.origin is OrderOrigin.PLAYER for o in moves)

    def test_stacked_mechs_staggered_laterally(self) -> None:
        """堆叠同 tile → 目标撞点 → 沿移动方向横向逐格错开（防触发让路超时逃生阀）。"""
        service = _service_with((6, 10), (6, 10))
        dispatched = main_module._dispatch_formation_move(service, OrderDispatcher(), 1, 0)
        a, b = service.get("px-01"), service.get("px-02")
        assert dispatched == 2
        assert a is not None and b is not None
        assert a.move_target == _tile_center((11, 10))
        assert b.move_target == _tile_center((11, 9))  # 横向错开 1 格
        assert a.move_target != b.move_target

    def test_target_clamped_to_map_bounds(self) -> None:
        service = _service_with((MAP_TILES - 2, 10))
        main_module._dispatch_formation_move(service, OrderDispatcher(), 1, 0)
        mech = service.get("px-01")
        assert mech is not None
        assert mech.move_target == _tile_center((MAP_TILES - 1, 10))

    def test_no_selection_dispatches_nothing(self) -> None:
        service = _service_with((6, 10))
        service.selected_ids = []
        dispatcher = OrderDispatcher()
        assert main_module._dispatch_formation_move(service, dispatcher, 1, 0) == 0
        assert dispatcher.order_log == ()

    def test_dead_mech_skipped(self) -> None:
        service = _service_with((6, 10), (8, 10))
        dead = service.get("px-01")
        assert dead is not None
        dead.take_damage(10_000)
        dispatched = main_module._dispatch_formation_move(service, OrderDispatcher(), 1, 0)
        assert dispatched == 1
        alive = service.get("px-02")
        assert alive is not None
        assert alive.move_target == _tile_center((13, 10))


class TestToggleAutoCommand:
    """A 键托管：交互模式切换玩家侧 AiCommander on/off；--demo（demo_mode=True）无效。"""

    def test_toggle_on_attaches_player_commander(self) -> None:
        game = _StubGame()
        main_module._toggle_auto_command(game)  # type: ignore[arg-type]
        commander = game.player_commander
        assert isinstance(commander, AiCommander)
        assert commander.faction is Faction.PLAYER

    def test_toggle_off_detaches_without_clearing_orders(self) -> None:
        game = _StubGame()
        main_module._toggle_auto_command(game)  # type: ignore[arg-type]
        main_module._toggle_auto_command(game)  # type: ignore[arg-type]
        assert game.player_commander is None

    def test_toggle_is_a_reversible_switch(self) -> None:
        game = _StubGame()
        for expected in (True, False, True):
            main_module._toggle_auto_command(game)  # type: ignore[arg-type]
            assert (game.player_commander is not None) is expected

    def test_noop_in_demo_mode(self) -> None:
        game = _StubGame()
        game.demo_mode = True
        main_module._toggle_auto_command(game)  # type: ignore[arg-type]
        assert game.player_commander is None


class _RecordingFont:
    """包装 pygame 字体：记录 render 文本后委托真字体（Font.render 属性只读，不能原地替换）。"""

    def __init__(self, real: pygame.font.Font, texts: list[str]) -> None:
        self._real = real
        self._texts = texts

    def render(self, text: str, *args: object, **kwargs: object) -> pygame.Surface:
        self._texts.append(text)
        return self._real.render(text, *args, **kwargs)  # type: ignore[arg-type]


class TestHudStatusLine:
    """HUD 状态行：托管激活追加 AUTO: ON；键位提示按 show_hints 开关。"""

    def _hud_texts(self, *, auto_command: bool, show_hints: bool) -> list[str]:
        pygame.init()
        try:
            screen = pygame.Surface((640, 480))
            renderer = Renderer(screen)
            assert renderer._font is not None
            texts: list[str] = []
            renderer._font = _RecordingFont(renderer._font, texts)  # type: ignore[assignment]
            renderer.render(
                _service_with((6, 10)),
                0.0,
                0.0,
                auto_command=auto_command,
                show_hints=show_hints,
            )
            return texts
        finally:
            pygame.quit()

    def test_auto_on_and_hints_appended(self) -> None:
        texts = self._hud_texts(auto_command=True, show_hints=True)
        assert any("AUTO: ON" in t for t in texts)
        assert any("MOVE: ARROWS/W S D" in t and "FIRE: F" in t and "AUTO: A" in t for t in texts)

    def test_auto_off_and_hints_hidden(self) -> None:
        texts = self._hud_texts(auto_command=False, show_hints=False)
        assert all("AUTO: ON" not in t for t in texts)
        assert all("ARROWS" not in t for t in texts)
        assert any(t.startswith("FIRE: ") for t in texts)  # 既有状态行内容不变
