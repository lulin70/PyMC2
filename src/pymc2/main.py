"""PyMC2 入口：M2 指令层（框选 → 右键移动/攻击 → F 键火力姿态循环）。

游戏循环体拆为 create_game / handle_event / run_frame，供 e2e 测试以 dummy
驱动 + event.post 复用同一套逻辑。玩家全部战术指令以 TacticalOrder
（origin=PLAYER）经 OrderDispatcher 下发，指令日志可序列化、可回放。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pygame

from pymc2.domain.entities.mech import MAP_SIZE_PX, TILE_SIZE_PX, Faction, FireMode
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType, TacticalOrder
from pymc2.presentation.renderer import WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX, Renderer
from pymc2.services.battle_service import BattleService, create_m2_battle
from pymc2.services.order_dispatcher import OrderDispatcher

FIRE_MODE_KEY = pygame.K_f
MAX_FRAME_DT_S = 0.1
CLICK_MAX_DRAG_PX = 4.0  # 拖拽位移低于该阈值视为单击（保留 M1 单击选中路径）


@dataclass
class Camera:
    """视口左上角的世界坐标。"""

    x: float = 0.0
    y: float = 0.0


@dataclass
class Game:
    """一局游戏的全部运行时状态。"""

    screen: pygame.Surface
    service: BattleService
    renderer: Renderer
    dispatcher: OrderDispatcher = field(default_factory=OrderDispatcher)
    camera: Camera = field(default_factory=Camera)
    drag_start: tuple[float, float] | None = None
    drag_current: tuple[float, float] | None = None
    frames: int = 0
    running: bool = True


def create_game(screen: pygame.Surface, *, service: BattleService | None = None) -> Game:
    """组装一局 M2 战斗（service 可注入以便测试固定随机源）。"""
    return Game(
        screen=screen,
        service=service if service is not None else create_m2_battle(),
        renderer=Renderer(screen),
    )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _world_pos(game: Game, screen_pos: tuple[int, int]) -> tuple[float, float]:
    """屏幕坐标 → 世界坐标（加上相机偏移）。"""
    return (screen_pos[0] + game.camera.x, screen_pos[1] + game.camera.y)


def update_camera(game: Game) -> None:
    """相机跟随主选中机甲，钳制在地图范围内。"""
    mech = game.service.selected
    if mech is None:
        return
    max_x = max(0.0, MAP_SIZE_PX - game.screen.get_width())
    max_y = max(0.0, MAP_SIZE_PX - game.screen.get_height())
    game.camera.x = _clamp(mech.x - game.screen.get_width() / 2, 0.0, max_x)
    game.camera.y = _clamp(mech.y - game.screen.get_height() / 2, 0.0, max_y)


def _finish_selection_drag(game: Game, end_pos: tuple[float, float]) -> None:
    """结束左键操作：位移极小视为单击（单选），否则按矩形框选（多选）。"""
    start = game.drag_start
    game.drag_start = None
    game.drag_current = None
    if start is None:
        return
    drag_px = math.hypot(end_pos[0] - start[0], end_pos[1] - start[1])
    if drag_px <= CLICK_MAX_DRAG_PX:
        game.service.select_at(end_pos[0], end_pos[1])
    else:
        game.service.select_in_rect(start[0], start[1], end_pos[0], end_pos[1])


def _issue_context_order(game: Game, world: tuple[float, float]) -> None:
    """右键：点中敌方单位 → ATTACK_OBJECT；否则 → MOVE_TO_POINT（tile 中心）。"""
    unit_ids = tuple(game.service.selected_ids)
    if not unit_ids:
        return
    enemy = game.service.unit_at(world[0], world[1], faction=Faction.ENEMY)
    if enemy is not None:
        order = TacticalOrder(
            order_type=OrderType.ATTACK_OBJECT,
            unit_ids=unit_ids,
            origin=OrderOrigin.PLAYER,
            target_tile=None,
            target_unit_id=enemy.id,
        )
    else:
        tile = (int(world[0]) // TILE_SIZE_PX, int(world[1]) // TILE_SIZE_PX)
        order = TacticalOrder(
            order_type=OrderType.MOVE_TO_POINT,
            unit_ids=unit_ids,
            origin=OrderOrigin.PLAYER,
            target_tile=tile,
            target_unit_id=None,
        )
    game.dispatcher.dispatch(order, game.service)


def _cycle_fire_mode(game: Game) -> None:
    """F 键：按主选中机甲姿态循环 FREE→HOLD→FREE，作用于全部选中单位。"""
    primary = game.service.selected
    if primary is None:
        return
    next_order_type = OrderType.FIRE_FREE if primary.fire_mode is FireMode.HOLD else OrderType.FIRE_HOLD
    order = TacticalOrder(
        order_type=next_order_type,
        unit_ids=tuple(game.service.selected_ids),
        origin=OrderOrigin.PLAYER,
        target_tile=None,
        target_unit_id=None,
    )
    game.dispatcher.dispatch(order, game.service)


def handle_event(game: Game, event: pygame.event.Event) -> None:
    """处理单个输入事件（世界坐标 = 屏幕坐标 + 相机偏移）。"""
    if event.type == pygame.QUIT:
        game.running = False
        return
    if event.type == pygame.MOUSEMOTION:
        if game.drag_start is not None:
            game.drag_current = _world_pos(game, event.pos)
        return
    if event.type == pygame.MOUSEBUTTONDOWN:
        if event.button == 1:
            game.drag_start = _world_pos(game, event.pos)
            game.drag_current = game.drag_start
        elif event.button == 3:
            _issue_context_order(game, _world_pos(game, event.pos))
        return
    if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
        _finish_selection_drag(game, _world_pos(game, event.pos))
        return
    if event.type == pygame.KEYDOWN and event.key == FIRE_MODE_KEY:
        _cycle_fire_mode(game)


def run_frame(game: Game, dt: float) -> None:
    """处理输入、推进一帧并渲染。"""
    for event in pygame.event.get():
        handle_event(game, event)
    game.service.tick(dt)
    update_camera(game)
    drag_rect: tuple[float, float, float, float] | None = None
    if game.drag_start is not None and game.drag_current is not None:
        x0, y0 = game.drag_start
        x1, y1 = game.drag_current
        drag_rect = (x0, y0, x1, y1)
    game.renderer.render(game.service, game.camera.x, game.camera.y, drag_rect=drag_rect)
    pygame.display.flip()
    game.frames += 1


def main() -> int:
    """窗口主循环入口。"""
    pygame.init()
    try:
        screen = pygame.display.set_mode((WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX))
        pygame.display.set_caption("PyMC2 - M2 Command Layer")
        game = create_game(screen)
        clock = pygame.time.Clock()
        while game.running:
            dt = min(clock.tick(60) / 1000.0, MAX_FRAME_DT_S)
            run_frame(game, dt)
    finally:
        pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
