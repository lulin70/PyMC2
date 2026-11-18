"""PyMC2 入口：M1 垂直切片（左键选中 → 右键移动 → F 键开火）。

游戏循环体拆为 create_game / handle_event / run_frame，供 e2e 测试以 dummy
驱动 + event.post 复用同一套逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pygame

from pymc2.domain.entities.mech import MAP_SIZE_PX
from pymc2.presentation.renderer import WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX, Renderer
from pymc2.services.battle_service import BattleService, create_m1_battle

FIRE_KEY = pygame.K_f
MAX_FRAME_DT_S = 0.1


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
    camera: Camera = field(default_factory=Camera)
    frames: int = 0
    running: bool = True


def create_game(screen: pygame.Surface, *, service: BattleService | None = None) -> Game:
    """组装一局 M1 战斗（service 可注入以便测试固定随机源）。"""
    return Game(
        screen=screen,
        service=service if service is not None else create_m1_battle(),
        renderer=Renderer(screen),
    )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def update_camera(game: Game) -> None:
    """相机跟随选中机甲，钳制在地图范围内。"""
    mech = game.service.selected
    if mech is None:
        return
    max_x = max(0.0, MAP_SIZE_PX - game.screen.get_width())
    max_y = max(0.0, MAP_SIZE_PX - game.screen.get_height())
    game.camera.x = _clamp(mech.x - game.screen.get_width() / 2, 0.0, max_x)
    game.camera.y = _clamp(mech.y - game.screen.get_height() / 2, 0.0, max_y)


def handle_event(game: Game, event: pygame.event.Event) -> None:
    """处理单个输入事件（世界坐标 = 屏幕坐标 + 相机偏移）。"""
    if event.type == pygame.QUIT:
        game.running = False
        return
    if event.type == pygame.MOUSEBUTTONDOWN:
        world_x = event.pos[0] + game.camera.x
        world_y = event.pos[1] + game.camera.y
        if event.button == 1:
            game.service.select_at(world_x, world_y)
        elif event.button == 3:
            mech = game.service.selected
            if mech is not None:
                game.service.move_mech(mech.id, world_x, world_y)
        return
    if event.type == pygame.KEYDOWN and event.key == FIRE_KEY:
        mech = game.service.selected
        if mech is not None and mech.alive:
            target = game.service.nearest_enemy(mech)
            if target is not None:
                game.service.fire_at(mech.id, target.id)


def run_frame(game: Game, dt: float) -> None:
    """处理输入、推进一帧并渲染。"""
    for event in pygame.event.get():
        handle_event(game, event)
    game.service.tick(dt)
    update_camera(game)
    game.renderer.render(game.service, game.camera.x, game.camera.y)
    pygame.display.flip()
    game.frames += 1


def main() -> int:
    """窗口主循环入口。"""
    pygame.init()
    try:
        screen = pygame.display.set_mode((WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX))
        pygame.display.set_caption("PyMC2 - M1 Vertical Slice")
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
