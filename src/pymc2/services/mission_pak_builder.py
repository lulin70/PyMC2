"""官方任务 .pak 战局构建（P3 内容驱动）：解析结果 → 单位布置 + 高程网格。

映射规则（docs/P3_DESIGN_20261121.md §3）：

- **单位布置**（.fit [PartN] → object2.pak 类号分派）：2→Mech、3→Vehicle、21→Turret，
  其余类跳过（记录）；``Active/Exists=0`` 跳过；``PlayerPart`` 或 team 0 → Faction.PLAYER，
  其余 → Faction.ENEMY；id = ``f"{name.lower()}-{part序号:02d}"``；武器/HP 走实体缺省
  （Mech ppc 25/420、Vehicle autocannon/hp80、Turret hp60）；MC2 damage 字段实测恒 0
  → 忽略（§6.8）。
- **pak packet 1 静态物**：仅类号 21（炮塔）入局（id ``turret-{序号:02d}``；team<0 的
  中立炮塔按 ENEMY 守卫——保证守卫交战语义可用，§6.7）；建筑/树/桥跳过。
- **高程**：packet 0 tile 高程 → 40×40 **裁剪窗**（最大覆盖滑窗：确定性穷举全部窗位，
  取「入窗单位数最多，平局玩家方多者优先，再取西北角靠前」的窗；无候选时地图中心）
  → 窗内 min-max 线性量化 0..ELEVATION_MAX（max==min → 全 0）→ ``ElevationGrid(rows)``；
  窗外单位跳过并记入 skipped_units（§6.2 固有失配）。
- **像素坐标**：MC2 128 world units/tile = PyMC2 32 px/tile（×0.25）；MC2 tile
  (row, col) → PyMC2 tile (col−c0, row−r0)，单位落 tile 中心。
- **类型表缺失降级**（§6.9）：object2.pak 不存在 → parts 全按 Mech 布置、pak 静态物
  全跳过（返回值 ``degraded=True`` 留痕，不抛错阻断试玩）。

两段式布置：先收集全部候选（unit + MC2 tile），再按候选全体质心定**单一**裁剪窗，
最后统一落位/跳越界（窗口不随布置顺序漂移）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pymc2.domain.elevation import ELEVATION_MAX, ElevationGrid
from pymc2.domain.entities.mech import MAP_TILES, TILE_SIZE_PX, Faction, Mech, Weapon
from pymc2.domain.entities.turret import Turret
from pymc2.domain.entities.unit import Unit
from pymc2.domain.entities.vehicle import Vehicle
from pymc2.domain.systems.ballistic import ShotRng
from pymc2.infrastructure.mission_pak import (
    OBJECT_CLASS_MECH,
    OBJECT_CLASS_TURRET,
    OBJECT_CLASS_VEHICLE,
    FitPart,
    MissionTerrain,
    ObjectTypeDef,
    load_mission_fit,
    load_mission_pak,
    load_object_types,
)
from pymc2.services.battle_service import BattleService

PLAYER_MECH_WEAPON = Weapon("ppc", 25, 420.0, 2.0)  # 与 M1/M2 战局同口径的缺省直射武器


class MissionPakBuildError(ValueError):
    """战局构建失败（任务数据缺布置单位、地形小于裁剪窗等硬失败）。"""


@dataclass(frozen=True)
class MissionPakBattle:
    """pak 战局构建结果：service + 布置统计（战报/断言用）。"""

    service: BattleService
    units_placed: int
    skipped_units: tuple[str, ...]  # 越界/未入局单位说明（战报打印）
    degraded: bool  # 类型表缺失降级（§6.9）
    elevation_min: float  # 裁剪窗原始高程范围（米，量化输入）
    elevation_max: float
    crop_origin: tuple[int, int]  # 裁剪窗左上 MC2 tile (row, col)


def build_battle_from_mission_pak(
    pak_path: Path,
    fit_path: Path,
    object_types_path: Path | None,
    *,
    rng: ShotRng | None = None,
) -> MissionPakBattle:
    """官方任务 .pak（+.fit 布置 + object2.pak 类型表）→ 可玩战局。"""
    mission = load_mission_pak(pak_path)
    parts: tuple[FitPart, ...] = load_mission_fit(fit_path) if fit_path.exists() else ()
    types: dict[int, ObjectTypeDef] | None = (
        load_object_types(object_types_path) if object_types_path is not None and object_types_path.exists() else None
    )

    # --- 第一段：收集候选（unit + MC2 tile + 战报标签），窗口在此之后定死 ---
    candidates: list[tuple[Unit, tuple[int, int], str]] = []
    notices: list[str] = []  # 未入局原因（inactive/不支持类号），战报留痕
    for part in parts:
        if not part.active:
            notices.append(f"part{part.part_index}: inactive")
            continue
        definition = types.get(part.object_number) if types is not None else None
        unit = _build_part_unit(part, definition)
        if unit is None:
            notices.append(
                f"part{part.part_index}: unsupported object class {definition.object_class if definition else '?'}"
            )
            continue
        candidates.append((unit, mission.terrain.world_to_tile(part.x, part.y), f"part{part.part_index}"))
    if types is not None:  # 类型表缺失降级：静态物全跳过（§6.9）
        for index, placement in enumerate(mission.objects):
            definition = types.get(placement.obj_type_num)
            if definition is None or definition.object_class != OBJECT_CLASS_TURRET:
                continue
            faction = Faction.PLAYER if placement.team_id == 0 else Faction.ENEMY
            candidates.append(
                (
                    Turret(f"turret-{index:02d}", faction, 0.0, 0.0),
                    mission.terrain.world_to_tile(placement.x, placement.y),
                    f"turret-{index:02d}",
                )
            )

    window = _crop_window(mission, [(unit.faction is Faction.PLAYER, tile) for unit, tile, _ in candidates])

    # --- 第二段：统一落位（tile 中心）或跳过越界 ---
    service = BattleService(rng=rng)
    skipped: list[str] = list(notices)
    for unit, (row, col), label in candidates:
        local_row = row - window[0]
        local_col = col - window[1]
        if not (0 <= local_col < MAP_TILES and 0 <= local_row < MAP_TILES):
            skipped.append(f"{label}: outside 40x40 crop window (mc2 tile ({row}, {col}))")
            continue
        unit.x = local_col * TILE_SIZE_PX + TILE_SIZE_PX / 2
        unit.y = local_row * TILE_SIZE_PX + TILE_SIZE_PX / 2
        service.add_mech(unit)
    if not service.mechs:
        raise MissionPakBuildError(f"{pak_path}: no units placed (all skipped or out of window)")

    service.elevation = quantize_window(mission, window)
    min_elevation, max_elevation = _window_min_max(mission, window)
    return MissionPakBattle(
        service=service,
        units_placed=len(service.mechs),
        skipped_units=tuple(skipped),
        degraded=types is None,
        elevation_min=min_elevation,
        elevation_max=max_elevation,
        crop_origin=window,
    )


def _build_part_unit(part: FitPart, definition: ObjectTypeDef | None) -> Unit | None:
    """part 类号分派 → Unit（类型表缺失全按 Mech 降级，§6.9）；不可布置类返回 None。"""
    object_class = definition.object_class if definition is not None else OBJECT_CLASS_MECH
    name = (definition.name if definition is not None else "") or "mech"
    unit_id = f"{name.lower()}-{part.part_index:02d}"
    faction = Faction.PLAYER if (part.player or part.team_id == 0) else Faction.ENEMY
    if object_class == OBJECT_CLASS_MECH:
        return Mech(unit_id, faction, 0.0, 0.0, weapon=PLAYER_MECH_WEAPON)
    if object_class == OBJECT_CLASS_VEHICLE:
        return Vehicle(unit_id, faction, 0.0, 0.0)
    if object_class == OBJECT_CLASS_TURRET:
        return Turret(unit_id, faction, 0.0, 0.0)
    return None


def _crop_window(mission: MissionTerrain, marked_tiles: list[tuple[bool, tuple[int, int]]]) -> tuple[int, int]:
    """40×40 裁剪窗左上角 (row0, col0)：**最大覆盖滑窗**（确定性穷举）。

    官方地图 59..119 tile/边 vs PyMC2 固定 40×40（§6.2 固有失配）：穷举全部窗位取
    「入窗单位数最多 → 平局玩家方入窗多者优先 → 西北角靠前」的窗（无候选时地图中心），
    保证 MC2 分散编制（如 mc2_01 玩家/步兵/车队分集群）尽量整群入窗。
    """
    if mission.terrain.tiles_per_side < MAP_TILES:
        raise MissionPakBuildError(f"mission map too small: {mission.terrain.tiles_per_side} tiles < {MAP_TILES}")
    max_origin = mission.terrain.tiles_per_side - MAP_TILES
    if not marked_tiles:
        return (max_origin // 2, max_origin // 2)
    best_origin = (0, 0)
    best_key = (-1, -1)
    for row0 in range(max_origin + 1):
        for col0 in range(max_origin + 1):
            inside = players = 0
            for is_player, (row, col) in marked_tiles:
                if row0 <= row < row0 + MAP_TILES and col0 <= col < col0 + MAP_TILES:
                    inside += 1
                    players += 1 if is_player else 0
            if (inside, players) > best_key:
                best_key = (inside, players)
                best_origin = (row0, col0)
    return best_origin


def _window_min_max(mission: MissionTerrain, window: tuple[int, int]) -> tuple[float, float]:
    """裁剪窗内原始高程范围（米）。"""
    row0, col0 = window
    values = [value for row in mission.terrain.rows[row0 : row0 + MAP_TILES] for value in row[col0 : col0 + MAP_TILES]]
    return (min(values), max(values))


def quantize_window(mission: MissionTerrain, window: tuple[int, int]) -> ElevationGrid:
    """裁剪窗高程 → ElevationGrid：窗内 min-max 线性量化 0..ELEVATION_MAX（round 后钳制）。"""
    row0, col0 = window
    min_value, max_value = _window_min_max(mission, window)
    span = max_value - min_value
    rows: list[list[int]] = []
    for row in mission.terrain.rows[row0 : row0 + MAP_TILES]:
        rows.append([_quantize(value, min_value, span) for value in row[col0 : col0 + MAP_TILES]])
    return ElevationGrid(rows)


def _quantize(value: float, min_value: float, span: float) -> int:
    """单值量化：span==0 → 0；否则 round((value−min)/span×ELEVATION_MAX)（ElevationGrid 再钳制）。"""
    if span <= 0:
        return 0
    return int(round((value - min_value) / span * ELEVATION_MAX))
