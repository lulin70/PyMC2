"""PyMC2 入口：M5 战役演示（任务链+整备闭环）+ M6 脚本驱动 + M4 AI 指挥官。

游戏循环体拆为 create_game / handle_event / run_frame，供 e2e 测试以 dummy
驱动 + event.post 复用同一套逻辑。玩家全部战术指令以 TacticalOrder
（origin=PLAYER）经 OrderDispatcher 下发；AiCommander 每帧经 update()
按 N tick 间隔产出指令，与玩家指令同源同路径。

M5/M6 演示循环（main）：双侧 AI 指挥官自动攻防；首关挂示例任务脚本
（增援/RP 奖励/胜负/战报消息）；战斗结束打印结算与整备摘要（文本 UI，
完整整备屏不在本里程碑范围），整备操作走 RefitService 真实调用。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import pygame

from pymc2.domain.entities.mech import MAP_SIZE_PX, TILE_SIZE_PX, Faction, FireMode
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType, TacticalOrder
from pymc2.domain.scripting.mission_script import (
    ScriptContext,
    ScriptEngine,
    load_mission_script,
)
from pymc2.presentation.renderer import WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX, Renderer
from pymc2.services.ai_commander import AiCommander
from pymc2.services.battle_service import BattleService, create_m2_battle
from pymc2.services.campaign_state import CampaignState, start_campaign
from pymc2.services.mission_chain import MissionChain, MissionResult
from pymc2.services.order_dispatcher import OrderDispatcher
from pymc2.services.refit_service import HIRE_COST_RP, RefitService

FIRE_MODE_KEY = pygame.K_f
MAX_FRAME_DT_S = 0.1
CLICK_MAX_DRAG_PX = 4.0  # 拖拽位移低于该阈值视为单击（保留 M1 单击选中路径）
MAX_RETRIES_PER_MISSION = 3  # demo 同任务连续战败上限（防自动演示死循环）


def _default_script_path() -> Path:
    """示例任务脚本路径（仓库 data/missions，与 cwd 无关）。"""
    return Path(__file__).resolve().parents[2] / "data" / "missions" / "m1_river_crossing.json"


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
    commander: AiCommander | None = None  # M4：注入则启用 AI 指挥官决策循环
    camera: Camera = field(default_factory=Camera)
    drag_start: tuple[float, float] | None = None
    drag_current: tuple[float, float] | None = None
    frames: int = 0
    running: bool = True


def create_game(
    screen: pygame.Surface,
    *,
    service: BattleService | None = None,
    commander: AiCommander | None = None,
) -> Game:
    """组装一局战斗（service/commander 可注入以便测试固定随机源与 AI 开关）。"""
    return Game(
        screen=screen,
        service=service if service is not None else create_m2_battle(),
        renderer=Renderer(screen),
        commander=commander,
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
    if game.commander is not None:
        game.commander.update(game.service, game.dispatcher)
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


def _print_settlement(state: CampaignState, result: MissionResult) -> None:
    """打印单任务结算摘要（文本 UI，M5 最小接线）。"""
    lost = "、".join(result.lost_unit_ids) if result.lost_unit_ids else "无"
    verdict = "胜利" if result.victory else "战败"
    print(f"== 任务结算 {result.mission_id}: {verdict} ==")
    print(
        f"   RP +{result.rp_reward}(奖励) +{result.rp_salvage}(salvage)"
        f" → 结余 {state.requisition_points} | 损失: {lost} | 幸存: {' '.join(result.survivor_ids) or '无'}"
    )


def _run_refit_phase(state: CampaignState, refit: RefitService) -> None:
    """整备阶段：修复全部受损机甲 → RP 充足时购入一台新机甲；打印整备摘要。"""
    print("== 整备 ==")
    for mech in list(state.roster):
        cost = refit.repair_cost(mech)
        if cost > 0 and refit.repair(state, mech.id):
            print(f"   修复 {mech.id}（-{cost} RP → 结余 {state.requisition_points}）")
    hired = refit.hire(state)
    if hired is not None:
        print(f"   新购 {hired.id}（-{HIRE_COST_RP} RP → 结余 {state.requisition_points}）")


def main() -> int:
    """M5/M6 演示主循环：双侧 AI 自动攻防 + 首关任务脚本 + 结算/整备摘要输出。"""
    pygame.init()
    try:
        screen = pygame.display.set_mode((WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX))
        pygame.display.set_caption("PyMC2 - M5 Campaign / M6 Mission Script")
        clock = pygame.time.Clock()
        state = start_campaign()
        chain = MissionChain()
        refit = RefitService()
        script = load_mission_script(_default_script_path())
        player_ai = AiCommander(Faction.PLAYER)
        enemy_ai = AiCommander(Faction.ENEMY)
        retries = 0
        failed_mission_id: str | None = None
        while chain.has_next(state):
            mission = chain.current_def(state)
            if mission.mission_id != failed_mission_id:
                retries = 0  # 换关重置重试计数
            roster_ids = " ".join(m.id for m in state.roster) or "无"
            print(f"== 任务开始 {mission.mission_id}: {mission.title}（名册 {roster_ids}）==")
            battle = chain.start_mission(state)
            context = ScriptContext(battle=battle, campaign=state)
            engine = ScriptEngine(script, context)
            if state.mission_index == 0:
                engine.attach(battle)  # 示例脚本叙事对应首关（增援/RP 奖励/胜负）
            game = create_game(screen, service=battle)
            while (
                game.running
                and context.outcome is None
                and not chain.is_victory(battle)
                and not chain.is_defeat(battle)
            ):
                player_ai.update(battle, game.dispatcher)
                enemy_ai.update(battle, game.dispatcher)
                dt = min(clock.tick(60) / 1000.0, MAX_FRAME_DT_S)
                run_frame(game, dt)
            if not game.running:
                return 0  # QUIT：直接退出，不做结算
            for message in context.messages:
                print(f"   [战报] {message}")
            result = chain.complete_mission(state, battle)
            _print_settlement(state, result)
            if result.victory:
                _run_refit_phase(state, refit)
            else:
                retries += 1
                failed_mission_id = result.mission_id
                if retries >= MAX_RETRIES_PER_MISSION:
                    print("== 战役失败：同任务连续战败 3 次，演示结束 ==")
                    return 0
        print(f"== 战役 {state.campaign_id} 完成：全 {len(chain.missions)} 任务结算 ==")
        return 0
    finally:
        pygame.quit()


if __name__ == "__main__":
    raise SystemExit(main())
