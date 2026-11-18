"""任务链（M5）：三个敌方编制/初始位置递进的关卡定义与开局/结算编排。

MC2 参照：campaign 层 mission 列表。每关复用 create_m4_battle 模式做编制变体：
- 名册（roster）即己方编制：开局对每台名册机甲做快照复制参战（战斗副本与名册
  对象隔离，结算时由 carryover 回写）；pilot rank 与部位装甲状态由此天然携带。
- 胜利判定：敌方全灭（含增援）；失败判定：己方全灭。
- 结算：胜利发任务奖励 + salvage；失败仅 salvage，任务指针不推进（重试当前关）。
"""

from __future__ import annotations

from dataclasses import dataclass

from pymc2.domain.entities.mech import TILE_SIZE_PX, Faction, Mech, Weapon
from pymc2.domain.entities.pilot import Pilot
from pymc2.services.battle_service import BattleService, ShotRng
from pymc2.services.campaign_state import CampaignState, mech_from_dict, mech_to_dict
from pymc2.services.refit_service import RefitService
from pymc2.services.salvage_service import SalvageService

ENEMY_WEAPON_DAMAGE = 15  # 与 create_m4_battle 敌方武器口径一致

PLAYER_SPAWN_ROW_TILE_Y = 10
PLAYER_SPAWN_BASE_TILE_X = 6
PLAYER_SPAWN_STEP_TILE_X = 2


@dataclass(frozen=True)
class EnemyUnitSpec:
    """敌方编制单元：出生 tile + 武器参数（复用 create_m4_battle 机甲模板）。"""

    unit_id: str
    tile: tuple[int, int]
    hull: int = 100
    weapon_damage: int = ENEMY_WEAPON_DAMAGE
    weapon_range_px: float = 360.0
    weapon_cooldown_s: float = 2.5
    pilot_name: str | None = None


@dataclass(frozen=True)
class MissionDef:
    """单关定义：标题 / RP 奖励 / 敌方编制（位置与数量逐关递进）。"""

    mission_id: str
    title: str
    rp_reward: int
    enemies: tuple[EnemyUnitSpec, ...]


MISSION_CHAIN: tuple[MissionDef, ...] = (
    MissionDef(
        mission_id="m1_river_crossing",
        title="渡河突破口",
        rp_reward=25,
        enemies=(EnemyUnitSpec("ex-01", (18, 10)),),
    ),
    MissionDef(
        mission_id="m2_ridge_ambush",
        title="山脊伏击",
        rp_reward=30,
        enemies=(
            EnemyUnitSpec("ex-01", (20, 10)),
            EnemyUnitSpec("ex-02", (22, 13)),
        ),
    ),
    MissionDef(
        mission_id="m3_final_assault",
        title="决战高墙",
        rp_reward=40,
        enemies=(
            EnemyUnitSpec("ex-01", (24, 10)),
            EnemyUnitSpec("ex-02", (26, 8)),
            EnemyUnitSpec("ex-03", (26, 12), pilot_name="enemy-veteran"),
        ),
    ),
)


@dataclass
class MissionResult:
    """单关结算结果：胜负 / RP 构成（奖励+salvage）/ 损失与幸存名册。"""

    mission_id: str
    victory: bool
    rp_reward: int
    rp_salvage: int
    lost_unit_ids: list[str]
    survivor_ids: list[str]


def _tile_center(tile_x: int, tile_y: int) -> tuple[float, float]:
    return (tile_x * TILE_SIZE_PX + TILE_SIZE_PX / 2, tile_y * TILE_SIZE_PX + TILE_SIZE_PX / 2)


class MissionChain:
    """任务链编排：开局按名册布置己方 + 按关卡定义布置敌方；结算推进任务指针。"""

    def __init__(self, missions: tuple[MissionDef, ...] = MISSION_CHAIN) -> None:
        self.missions = missions
        self._salvage = SalvageService()
        self._refit = RefitService()

    # ------------------------------------------------------------------ 任务指针

    def has_next(self, state: CampaignState) -> bool:
        """还有未完成的任务（mission_index 未越界）。"""
        return state.mission_index < len(self.missions)

    def current_def(self, state: CampaignState) -> MissionDef:
        """当前任务定义；越界显式报错（战役已完成）。"""
        if not self.has_next(state):
            raise IndexError(f"campaign {state.campaign_id!r} complete: no mission at index {state.mission_index}")
        return self.missions[state.mission_index]

    # ------------------------------------------------------------------ 开局

    def start_mission(self, state: CampaignState, *, rng: ShotRng | None = None) -> BattleService:
        """构建当前任务战局：名册快照复制参战（状态携带）+ 关卡敌方编制。"""
        mission = self.current_def(state)
        service = BattleService(rng=rng)
        for slot, roster_mech in enumerate(state.roster):
            combat_mech = mech_from_dict(mech_to_dict(roster_mech))
            combat_mech.x, combat_mech.y = _tile_center(
                PLAYER_SPAWN_BASE_TILE_X + slot * PLAYER_SPAWN_STEP_TILE_X, PLAYER_SPAWN_ROW_TILE_Y
            )
            service.add_mech(combat_mech)
        for spec in mission.enemies:
            x, y = _tile_center(*spec.tile)
            service.add_mech(
                Mech(
                    spec.unit_id,
                    Faction.ENEMY,
                    x,
                    y,
                    hull=spec.hull,
                    weapon=Weapon("mlaser", spec.weapon_damage, spec.weapon_range_px, spec.weapon_cooldown_s),
                    pilot=Pilot(spec.pilot_name) if spec.pilot_name is not None else None,
                )
            )
        return service

    # ------------------------------------------------------------------ 胜负判定

    @staticmethod
    def is_victory(battle: BattleService) -> bool:
        """敌全灭（至少曾有敌机甲，且当前无存活敌机甲）。"""
        enemies = [m for m in battle.mechs.values() if m.faction is Faction.ENEMY]
        return bool(enemies) and not any(m.alive for m in enemies)

    @staticmethod
    def is_defeat(battle: BattleService) -> bool:
        """己方全灭（至少曾有己方机甲，且当前无存活己方机甲）。"""
        allies = [m for m in battle.mechs.values() if m.faction is Faction.PLAYER]
        return bool(allies) and not any(m.alive for m in allies)

    # ------------------------------------------------------------------ 结算

    def complete_mission(self, state: CampaignState, battle: BattleService) -> MissionResult:
        """结算当前任务：salvage 入账 + 幸存者携带回写；胜利再发奖励并推进指针。"""
        mission = self.current_def(state)
        victory = self.is_victory(battle)
        rp_salvage = self._salvage.collect(state, battle)
        lost = [m.id for m in battle.mechs.values() if m.faction is Faction.PLAYER and not m.alive]
        survivors = self._refit.carryover_survivors(state, battle)
        rp_reward = mission.rp_reward if victory else 0
        if victory:
            state.requisition_points += rp_reward
            state.mission_index += 1
        return MissionResult(
            mission_id=mission.mission_id,
            victory=victory,
            rp_reward=rp_reward,
            rp_salvage=rp_salvage,
            lost_unit_ids=lost,
            survivor_ids=survivors,
        )
