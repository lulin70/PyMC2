"""战斗结算（M5）：敌方摧毁单位 → salvage 折算 RP 入账。

MC2 参照：战后 salvage（回收摧毁机甲残骸换取物资）。折算口径：按部位完好度
（总余量/出厂总余量）线性折算基准值；打空的机甲（残骸全空）折算 0 RP。
幸存者回写与阵亡移除属整备闭环（refit_service.carryover_survivors），本服务只管 RP。

P2 单位谱系（docs/P2_DESIGN_20261121.md §4）：折算按 Unit 协议泛化——载具/炮塔
整体 HP 击毁即全损（hull=0）→ 击毁折算 0 RP（机甲部位残骸 >0 的口径不变）。
"""

from __future__ import annotations

from pymc2.domain.entities.mech import Faction
from pymc2.domain.entities.unit import Unit
from pymc2.services.battle_service import BattleService
from pymc2.services.campaign_state import CampaignState

SALVAGE_BASE_RP = 45


class SalvageService:
    """salvage 结算：摧毁敌方单位按完好度折算 RP 并入账。"""

    @staticmethod
    def salvage_value(unit: Unit) -> int:
        """单台摧毁单位折算 RP：剩余完好度比例 × 基准值（四舍五入，全空 → 0）。"""
        if unit.max_hull <= 0:
            return 0
        return round(unit.hull / unit.max_hull * SALVAGE_BASE_RP)

    def collect(self, state: CampaignState, battle: BattleService) -> int:
        """结算当前战局：累计全部已摧毁敌方机甲折算值并入账，返回本次入账 RP。"""
        earned = sum(
            self.salvage_value(mech)
            for mech in battle.mechs.values()
            if mech.faction is Faction.ENEMY and not mech.alive
        )
        state.requisition_points += earned
        return earned
