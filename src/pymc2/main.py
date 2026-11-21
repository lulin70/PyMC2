"""PyMC2 入口：M5 战役演示（任务链+整备闭环）+ M6 脚本驱动 + M4 AI 指挥官。

游戏循环体拆为 create_game / handle_event / run_frame，供 e2e 测试以 dummy
驱动 + event.post 复用同一套逻辑。玩家全部战术指令以 TacticalOrder
（origin=PLAYER）经 OrderDispatcher 下发；AiCommander 每帧经 update()
按 N tick 间隔产出指令，与玩家指令同源同路径。自动交战（M7）：FREE 姿态机甲
对射程内最近敌机甲自动锁定开火，由 BattleService.tick 产出 origin=SELF 指令
经同一 dispatcher 下发（显式指定目标优先，HOLD 火力纪律不自动开火）。

两种模式（命令行）：
- 默认 = **交互模式**：真人可玩。玩家侧机甲不经 AiCommander（框选/右键移动/
  F 火力姿态走既有 handle_event 输入链路，指令 origin=PLAYER 经
  OrderDispatcher 下发；开火交给自动交战），敌方保留 AiCommander goal 分解；
  键盘操控（用户反馈：Mac 上鼠标移动不便）：方向键/WASD 选中组沿方向移动
  KEYBOARD_MOVE_TILES 格（编队平行偏移；单击一段、长按连续续段），Q 键托管
  开关（挂上/解除玩家侧 AiCommander，辅助决策语义：手动指令覆盖 AI，
  下一决策周期 AI 可能重新接管）；
- ``--demo`` = 双侧 AI 自动演示（原 main 行为）：玩家侧也由 AiCommander 指挥
  （Q 键托管无效）。

两种模式均沿用 M5 任务链 + 首关任务脚本 + 结算/整备文本摘要。
MC2 资产默认自动发现（env ``PYMC2_MC2_ASSETS`` → ``~/.pymc2/mc2-assets``），
``--mc2-assets <本地路径>`` 显式覆盖；未发现/加载失败时降级程序化绘制
（仅限本机学习，不打包不再分发）。
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pygame

from pymc2.domain.elevation import ElevationGrid, generate_hills
from pymc2.domain.entities.mech import MAP_SIZE_PX, MAP_TILES, TILE_SIZE_PX, Faction, FireMode
from pymc2.domain.orders.tactical_order import OrderOrigin, OrderType, TacticalOrder
from pymc2.domain.scripting.mission_script import (
    ScriptContext,
    ScriptEngine,
    load_mission_script,
)
from pymc2.infrastructure.mc2_assets import MC2AssetPack, default_pack_root
from pymc2.presentation.renderer import WINDOW_HEIGHT_PX, WINDOW_WIDTH_PX, Renderer, plan_decorations
from pymc2.services.ai_commander import AiCommander
from pymc2.services.battle_service import BattleService, create_m2_battle
from pymc2.services.campaign_state import CampaignState, start_campaign
from pymc2.services.mission_chain import MissionChain, MissionResult
from pymc2.services.mission_pak_builder import MissionPakBuildError, build_battle_from_mission_pak
from pymc2.services.order_dispatcher import OrderDispatcher
from pymc2.services.refit_service import HIRE_COST_RP, RefitService

FIRE_MODE_KEY = pygame.K_f
AUTO_COMMAND_KEY = pygame.K_q  # 托管开关（用户反馈：Q 键，A 回归左移；交互模式专属；--demo 双侧 AI 已就位，无效）
# 键盘移动键位（用户反馈：Mac 上鼠标移动操作不便）：方向键四方向全支持；
# WASD 全向（Q 键托管让出 A 后四键齐备）。KEYDOWN 触发单击一段；
# 长按由 run_frame 每帧轮询续段（_poll_held_move）。
MOVE_KEY_DIRECTIONS: dict[int, tuple[int, int]] = {
    pygame.K_UP: (0, -1),
    pygame.K_DOWN: (0, 1),
    pygame.K_LEFT: (-1, 0),
    pygame.K_RIGHT: (1, 0),
    pygame.K_w: (0, -1),
    pygame.K_s: (0, 1),
    pygame.K_a: (-1, 0),
    pygame.K_d: (1, 0),
}
KEYBOARD_MOVE_TILES = 5  # 单次按键沿方向移动的格数（模块常量，可调）
MAX_FRAME_DT_S = 0.1
CLICK_MAX_DRAG_PX = 4.0  # 拖拽位移低于该阈值视为单击（保留 M1 单击选中路径）
MAX_RETRIES_PER_MISSION = 3  # demo 同任务连续战败上限（防自动演示死循环）
SETTLEMENT_CONFIRM_TIMEOUT_S = 6.0  # 结算画面停留上限（按键/点击可提前继续）
SETTLEMENT_CONTINUE_KEYS = frozenset({pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE})


def _default_script_path() -> Path:
    """示例任务脚本路径（仓库 data/missions，与 cwd 无关）。"""
    return Path(__file__).resolve().parents[2] / "data" / "missions" / "m1_river_crossing.json"


def parse_args(argv: Sequence[str] = ()) -> argparse.Namespace:
    """命令行参数：默认交互模式（真人手操 vs 敌方 AI），``--demo`` 双侧 AI 自动演示。"""
    parser = argparse.ArgumentParser(
        prog="pymc2",
        description=(
            "PyMC2 实时战术（MechCommander 2 复刻学习项目）。"
            "默认交互模式：框选 → 右键移动 → 射程内自动交战开火 → F 火力姿态；"
            "--demo 切双侧 AI 自动演示。"
        ),
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="双侧 AI 自动演示（玩家侧机甲也由 AiCommander 指挥，无人交互）",
    )
    parser.add_argument(
        "--mc2-assets",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "显式指定本地 MechCommander 2 Shared Source checkout 路径（含 Data/ 目录）；"
            "缺省自动发现（env PYMC2_MC2_ASSETS → ~/.pymc2/mc2-assets），未发现则程序化绘制；"
            "仅限本机学习加载、不打包不再分发"
        ),
    )
    parser.add_argument(
        "--terrain",
        choices=("flat", "hills"),
        default="flat",
        help="地形高程（P1-A）：flat=全零平面（默认，存量战局不变）；hills=确定性丘陵"
        "（terrace 渲染 + 移动代价 + 爬升限制；任务脚本 elevation 键可覆盖）",
    )
    parser.add_argument(
        "--mission-pak",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "P3 内容驱动试玩：官方 MC2 任务 .pak 路径（如 ~/.pymc2/mc2-assets/Data/Missions/"
            "tut_01.pak，只读本机缓存）——解析地形高程与单位布置构建单场战局"
            "（同名 .fit 布置 + ../Objects/object2.pak 类型表自动推导）；"
            "缺省关闭，走存量战役链"
        ),
    )
    return parser.parse_args(list(argv))


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
    player_commander: AiCommander | None = None  # Q 键托管：玩家侧 AI 指挥官（None=真人手操）
    demo_mode: bool = False  # --demo 标记：Q 键托管无效（双侧 AI 已就位）
    camera: Camera = field(default_factory=Camera)
    drag_start: tuple[float, float] | None = None
    drag_current: tuple[float, float] | None = None
    frames: int = 0
    running: bool = True
    # Wave V3 面板槽位内容：任务名 + RP（None=槽位不画；RP 每帧由 main 回写，
    # grant_rp 触发器中途入账在面板可见）。
    mission_id: str | None = None
    requisition_points: int | None = None


def create_game(
    screen: pygame.Surface,
    *,
    service: BattleService | None = None,
    commander: AiCommander | None = None,
    asset_pack: MC2AssetPack | None = None,
    demo_mode: bool = False,
    mission_id: str | None = None,
    requisition_points: int | None = None,
) -> Game:
    """组装一局战斗（service/commander/asset_pack 可注入以便测试固定随机源与 AI/资产开关）。

    自动交战接线：service.dispatcher 指向本局 dispatcher——BattleService.tick 产出的
    SELF 源自动交战指令与玩家/指挥官指令同源同路径（无需接线的直构战局保持关闭）。
    mission_id/requisition_points：V3 面板槽位内容（默认 None → 槽位不画）。
    """
    battle = service if service is not None else create_m2_battle()
    game = Game(
        screen=screen,
        service=battle,
        # P1-A：Renderer 高程单一数据源（service.elevation；flat/未注入 → 渲染零改动）。
        renderer=Renderer(screen, asset_pack=asset_pack, elevation=battle.elevation),
        commander=commander,
        demo_mode=demo_mode,
        mission_id=mission_id,
        requisition_points=requisition_points,
    )
    game.service.dispatcher = game.dispatcher
    if asset_pack is not None:
        # 环境点缀（Wave V1）：非道路空 tile 确定性撒点（避开出生格）；纯装饰——
        # 不阻挡移动不参与命中，对齐让路协商语义。无资产包时保持原状零改动。
        occupied = {mech.tile for mech in game.service.mechs.values()}
        game.renderer.set_decorations(plan_decorations(occupied))
    return game


def _load_asset_pack(explicit_root: Path | None) -> MC2AssetPack | None:
    """加载 MC2 资产包：``--mc2-assets`` 显式路径优先，否则自动发现（env → ~/.pymc2）。

    加载成功打印摘要；候选无效/零命中维持降级提示；完全无候选则静默（程序化绘制）。
    """
    root = explicit_root if explicit_root is not None else default_pack_root()
    if root is None:
        return None
    asset_pack = MC2AssetPack.load(root)
    if asset_pack is not None:
        print(f"[PyMC2] MC2 资产已加载：{root}（{asset_pack.tga_count} 张，机甲精灵已启用）")
    else:
        print(f"[PyMC2] {root} 下未找到有效 Data/TGL 或 Data/Art 资产，降级程序化绘制")
    return asset_pack


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
    """右键：一律 MOVE_TO_POINT（tile 中心）。

    点中敌方机甲同样移动过去——开火交给自动交战（射程内自动锁定），右键不再承担
    攻击语义（用户反馈：开火应自动，不要右键点敌人）。
    """
    unit_ids = tuple(game.service.selected_ids)
    if not unit_ids:
        return
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


def _toggle_auto_command(game: Game) -> None:
    """Q 键托管（交互模式专属）：挂上/解除玩家侧 AiCommander（goal 分解：后撤>集火>占区）。

    解除不清除已下发指令——移动/攻击执行完自然停止；托管期间手动指令仍可下达
    （辅助决策语义：指令覆盖 AI 决策，下一决策周期 AI 可能重新接管）。
    ``--demo`` 下无效：双侧 AI 已就位，再挂玩家侧指挥官会双重决策。
    """
    if game.demo_mode:
        return
    if game.player_commander is None:
        game.player_commander = AiCommander(Faction.PLAYER)
    else:
        game.player_commander = None


def _clamp_tile(tile: tuple[int, int]) -> tuple[int, int]:
    """tile 坐标钳制在地图内（键盘位移可能越出边界）。"""
    return (max(0, min(MAP_TILES - 1, tile[0])), max(0, min(MAP_TILES - 1, tile[1])))


def _dispatch_formation_move(service: BattleService, dispatcher: OrderDispatcher, dx: int, dy: int) -> int:
    """键盘移动：选中组沿方向移动 KEYBOARD_MOVE_TILES 格（编队平行偏移）。

    每机以**自身当前 tile** 为基点 + 方向×KEYBOARD_MOVE_TILES → 队形平行平移、
    目标天然互异（标准 RTS 相对队形移动）；仅当多机堆叠同 tile 导致目标撞点时，
    按移动方向横向逐格交替错开（0,+1,-1,+2,-2…，避免全挤同一点触发让路超时
    逃生阀）；目标越界钳制到地图内。返回下发指令数。
    """
    candidates = [service.get(uid) for uid in service.selected_ids]
    mechs = [m for m in candidates if m is not None and m.alive]
    if not mechs:
        return 0
    perp_x, perp_y = dy, -dx  # 移动方向旋转 -90°：横向错开方向
    offsets = [0]
    for k in range(1, len(mechs)):
        offsets.extend((k, -k))
    used: set[tuple[int, int]] = set()
    dispatched = 0
    for mech in mechs:
        base = (mech.tile[0] + dx * KEYBOARD_MOVE_TILES, mech.tile[1] + dy * KEYBOARD_MOVE_TILES)
        tile = _clamp_tile(base)
        for offset in offsets:
            candidate = _clamp_tile((base[0] + perp_x * offset, base[1] + perp_y * offset))
            if candidate not in used:
                tile = candidate
                break
        used.add(tile)
        dispatcher.dispatch(
            TacticalOrder(
                order_type=OrderType.MOVE_TO_POINT,
                unit_ids=(mech.id,),
                origin=OrderOrigin.PLAYER,
                target_tile=tile,
                target_unit_id=None,
            ),
            service,
        )
        dispatched += 1
    return dispatched


def handle_event(game: Game, event: pygame.event.Event) -> None:
    """处理单个输入事件（世界坐标 = 屏幕坐标 + 相机偏移）。

    ``PYMC2_INPUT_DEBUG=1`` 时向 stdout 回显全部输入事件与下发的指令，
    用于诊断真机输入链路（事件未到达 vs 指令下发失败）。
    """
    debug = os.environ.get("PYMC2_INPUT_DEBUG") == "1"
    if debug and event.type in (
        pygame.MOUSEBUTTONDOWN,
        pygame.MOUSEBUTTONUP,
        pygame.KEYDOWN,
    ):
        print(
            f"[input] type={event.type} pos={getattr(event, 'pos', None)} "
            f"key={getattr(event, 'key', None)} button={getattr(event, 'button', None)}",
            flush=True,
        )
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
    if event.type == pygame.KEYDOWN:
        if event.key == FIRE_MODE_KEY:
            _cycle_fire_mode(game)
        elif event.key == AUTO_COMMAND_KEY:
            _toggle_auto_command(game)
        elif event.key in MOVE_KEY_DIRECTIONS:
            dx, dy = MOVE_KEY_DIRECTIONS[event.key]
            _dispatch_formation_move(game.service, game.dispatcher, dx, dy)


def _poll_held_move(game: Game) -> None:
    """长按连续移动（用户反馈：按一次只走一段太短）：每帧轮询按住的移动键续段。

    语义：主选中机甲上一段走完（move_target 为空）且移动键仍按住 → 立即续发
    下一段（编队平行偏移，与单击同函数同语义）——按住期间段间无缝，松开即停。
    单击路径仍走 handle_event KEYDOWN（按下首帧轮询因目标未空自动跳过，不重发）。
    """
    primary = game.service.selected
    if primary is None or primary.move_target is not None:
        return
    pressed = pygame.key.get_pressed()
    for key, (dx, dy) in MOVE_KEY_DIRECTIONS.items():
        if pressed[key]:
            _dispatch_formation_move(game.service, game.dispatcher, dx, dy)
            return


def run_frame(game: Game, dt: float) -> None:
    """处理输入、推进一帧并渲染。"""
    for event in pygame.event.get():
        handle_event(game, event)
    _poll_held_move(game)
    if game.commander is not None:
        game.commander.update(game.service, game.dispatcher)
    if game.player_commander is not None:  # Q 键托管：玩家侧 AI 与敌方 AI 同源同路径
        game.player_commander.update(game.service, game.dispatcher)
    game.service.tick(dt)
    update_camera(game)
    drag_rect: tuple[float, float, float, float] | None = None
    if game.drag_start is not None and game.drag_current is not None:
        x0, y0 = game.drag_start
        x1, y1 = game.drag_current
        drag_rect = (x0, y0, x1, y1)
    game.renderer.render(
        game.service,
        game.camera.x,
        game.camera.y,
        drag_rect=drag_rect,
        auto_command=game.player_commander is not None,
        show_hints=not game.demo_mode,
        mission_id=game.mission_id,
        requisition_points=game.requisition_points,
    )
    pygame.display.flip()
    game.frames += 1


def _apply_terrain(battle_service: BattleService, terrain: str, script_elevation: ElevationGrid | None) -> None:
    """P1-A 地形高程接线：CLI --terrain hills 挂确定性丘陵；任务脚本 elevation 键
    显式声明时后挂覆盖（脚本优先于 CLI）；flat 且脚本无覆盖 → 保持全零平面
    （存量战局零改动）。"""
    if terrain == "hills":
        battle_service.elevation = generate_hills()
    if script_elevation is not None:
        battle_service.elevation = script_elevation


def _default_object_types_path(pak_path: Path) -> Path:
    """P3 类型表推导：任务 .pak 的 ../Objects/object2.pak（缓存布局约定，存在才用）。"""
    return pak_path.parent.parent / "Objects" / "object2.pak"


def _run_mission_pak_session(
    args: argparse.Namespace,
    screen: pygame.Surface,
    asset_pack: MC2AssetPack | None,
    clock: pygame.time.Clock,
) -> int:
    """P3 内容驱动试玩：官方任务 .pak 构建单场战局（不走战役链/整备，§6.10）。

    玩家侧交互/Demo 与存量主循环同款；victory/defeat 谓词复用 MissionChain 静态口径；
    无敌方单位时提示沙盒试玩（QUIT 退出）。"""
    fit_path = args.mission_pak.with_suffix(".fit")
    object_types_path = _default_object_types_path(args.mission_pak)
    if not fit_path.exists():
        print(f"[PyMC2] 任务布置文件缺失：{fit_path}（.pak 旁应有同名 .fit）")
        return 1
    try:
        result = build_battle_from_mission_pak(args.mission_pak, fit_path, object_types_path)
    except (MissionPakBuildError, ValueError) as exc:
        print(f"[PyMC2] 任务 .pak 解析失败：{exc}")
        return 1
    battle = result.service
    print(
        f"[PyMC2] 任务 .pak 已加载：{args.mission_pak.name}"
        f"（单位 {result.units_placed} / 高程 {result.elevation_min:.0f}..{result.elevation_max:.0f}m"
        f" → 量化 0..3 / 裁剪窗 origin={result.crop_origin}）"
    )
    for notice in result.skipped_units[:8]:
        print(f"   [跳过] {notice}")
    if len(result.skipped_units) > 8:
        print(f"   [跳过] …共 {len(result.skipped_units)} 条")
    if result.degraded:
        print("[PyMC2] 未找到 object2.pak 类型表：单位全按机甲降级布置（无载具/炮塔分派）")
    enemies = [unit for unit in battle.mechs.values() if unit.faction is Faction.ENEMY]
    if not enemies:
        print("[PyMC2] 战局无敌方单位：沙盒试玩（QUIT 退出）")
    player_ai = AiCommander(Faction.PLAYER) if args.demo else None
    enemy_ai = AiCommander(Faction.ENEMY)
    mode_label = "AI Demo" if args.demo else "Interactive"
    pygame.display.set_caption(f"PyMC2 - Mission Pak ({args.mission_pak.stem}) [{mode_label}]")
    game = create_game(screen, service=battle, asset_pack=asset_pack, demo_mode=args.demo)
    while game.running and not MissionChain.is_victory(battle) and not MissionChain.is_defeat(battle):
        if player_ai is not None:
            player_ai.update(battle, game.dispatcher)
        enemy_ai.update(battle, game.dispatcher)
        dt = min(clock.tick(60) / 1000.0, MAX_FRAME_DT_S)
        run_frame(game, dt)
    if not game.running:
        return 0
    victory = MissionChain.is_victory(battle)
    title = "MISSION ACCOMPLISHED" if victory else "MISSION FAILED"
    print(f"== 任务 .pak 战局结束：{title} ==")
    _show_settlement(game, title, (args.mission_pak.stem.upper(), "PRESS ENTER / SPACE / CLICK TO EXIT"), clock)
    return 0


def _print_settlement(state: CampaignState, result: MissionResult) -> None:
    """打印单任务结算摘要（文本 UI，M5 最小接线）。"""
    lost = "、".join(result.lost_unit_ids) if result.lost_unit_ids else "无"
    verdict = "胜利" if result.victory else "战败"
    print(f"== 任务结算 {result.mission_id}: {verdict} ==")
    print(
        f"   RP +{result.rp_reward}(奖励) +{result.rp_salvage}(salvage)"
        f" → 结余 {state.requisition_points} | 损失: {lost} | 幸存: {' '.join(result.survivor_ids) or '无'}"
    )


def _defeat_is_terminal(state: CampaignState, result: MissionResult) -> bool:
    """战败终局判定：名册全灭后无法再战——战役失败终局，不再重试空名册关卡。

    旧逻辑缺陷：全灭后重试以 0 台己方机甲开局，is_defeat 要求「至少曾有己方
    机甲」永不触发，只能靠连续战败 3 次兜底（软锁，用户真机踩中）。
    """
    return not result.victory and not state.roster


def _settlement_overlay(state: CampaignState, result: MissionResult) -> tuple[str, tuple[str, ...]]:
    """窗口内结算画面文案（ASCII，对齐 HUD 提示行惯例）。"""
    title = "MISSION ACCOMPLISHED" if result.victory else "MISSION FAILED"
    lost = " ".join(result.lost_unit_ids) or "none"
    lines = (
        f"{result.mission_id}  RP +{result.rp_reward} reward +{result.rp_salvage} salvage = {state.requisition_points}",
        f"LOSSES: {lost}",
        "PRESS ENTER / SPACE / CLICK TO CONTINUE",
    )
    return title, lines


def _show_settlement(
    game: Game,
    title: str,
    lines: tuple[str, ...],
    clock: pygame.time.Clock,
    *,
    timeout_s: float = SETTLEMENT_CONFIRM_TIMEOUT_S,
) -> None:
    """窗口内结算呈现：重绘战场帧 + 遮罩摘要，回车/空格/点击继续，超时自动继续。

    QUIT 事件置 game.running=False 交由调用方收口（与战斗循环同一退出语义）。
    """
    deadline = time.monotonic() + timeout_s
    while game.running and time.monotonic() < deadline:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                game.running = False
                return
            if event.type == pygame.MOUSEBUTTONDOWN:
                return
            if event.type == pygame.KEYDOWN and event.key in SETTLEMENT_CONTINUE_KEYS:
                return
        update_camera(game)
        game.renderer.render(game.service, game.camera.x, game.camera.y)
        game.renderer.draw_settlement_overlay(title, lines)
        pygame.display.flip()
        clock.tick(60)


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


def main(argv: Sequence[str] = ()) -> int:
    """默认交互模式：玩家手操己方机甲 vs 敌方 AI 指挥官；``--demo`` 切双侧 AI 自动演示。"""
    args = parse_args(argv)
    asset_pack = _load_asset_pack(args.mc2_assets)
    pygame.init()
    try:
        screen = pygame.display.set_mode((WINDOW_WIDTH_PX, WINDOW_HEIGHT_PX))
        clock = pygame.time.Clock()
        if args.mission_pak is not None:  # P3 内容驱动试玩（缺省关闭，存量路径不变）
            return _run_mission_pak_session(args, screen, asset_pack, clock)
        mode_label = "AI Demo" if args.demo else "Interactive"
        pygame.display.set_caption(f"PyMC2 - M5 Campaign / M6 Mission Script ({mode_label})")
        state = start_campaign()
        chain = MissionChain()
        refit = RefitService()
        script = load_mission_script(_default_script_path())
        # 交互模式玩家侧不挂指挥官（指令仅来自真人输入 origin=PLAYER）；--demo 保留双侧 AI。
        player_ai = AiCommander(Faction.PLAYER) if args.demo else None
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
            _apply_terrain(battle, args.terrain, script.elevation)  # P1-A：地形高程（CLI + 脚本覆盖）
            context = ScriptContext(battle=battle, campaign=state)
            engine = ScriptEngine(script, context)
            if state.mission_index == 0:
                engine.attach(battle)  # 示例脚本叙事对应首关（增援/RP 奖励/胜负）
            game = create_game(screen, service=battle, asset_pack=asset_pack, demo_mode=args.demo)
            game.mission_id = mission.mission_id  # V3 面板任务名槽位
            while (
                game.running
                and context.outcome is None
                and not chain.is_victory(battle)
                and not chain.is_defeat(battle)
            ):
                if player_ai is not None:
                    player_ai.update(battle, game.dispatcher)
                enemy_ai.update(battle, game.dispatcher)
                game.requisition_points = state.requisition_points  # V3 面板 RP 槽位（每帧回写）
                dt = min(clock.tick(60) / 1000.0, MAX_FRAME_DT_S)
                run_frame(game, dt)
            if not game.running:
                return 0  # QUIT：直接退出，不做结算
            for message in context.messages:
                print(f"   [战报] {message}")
            result = chain.complete_mission(state, battle)
            _print_settlement(state, result)
            _show_settlement(game, *_settlement_overlay(state, result), clock)
            if not game.running:
                return 0
            if result.victory:
                _run_refit_phase(state, refit)
            elif _defeat_is_terminal(state, result):
                print("== 战役失败：名册全灭，战役终局 ==")
                return 0
            else:
                retries += 1
                failed_mission_id = result.mission_id
                if retries >= MAX_RETRIES_PER_MISSION:
                    print("== 战役失败：同任务连续战败 3 次，演示结束 ==")
                    return 0
        print(f"== 战役 {state.campaign_id} 完成：全 {len(chain.missions)} 任务结算 ==")
        _show_settlement(
            game,
            "CAMPAIGN COMPLETE",
            (f"ALL {len(chain.missions)} MISSIONS SETTLED", "PRESS ENTER / SPACE / CLICK TO EXIT"),
            clock,
        )
        return 0
    finally:
        pygame.quit()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
