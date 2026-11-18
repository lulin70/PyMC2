"""战斗结算（M5）：敌方摧毁机甲 → salvage 折算 RP 入账。

MC2 参照：战后 salvage（回收摧毁机甲残骸换取物资）。折算口径：按部位完好度
（总余量/出厂总余量）线性折算基准值；打空的机甲（残骸全空）折算 0 RP。
幸存者回写与阵亡移除属整备闭环（refit_service.carryover_survivors），本服务只管 RP。
"""

from __future__ import annotations

from pymc2.domain.entities.mech import Faction, Mech
from pymc2.services.battle_service import BattleService
from pymc2.services.campaign_state import CampaignState

SALVAGE_BASE_RP = 45


class SalvageService:
    """salvage 结算：摧毁敌方机甲按完好度折算 RP 并入账。"""

    @staticmethod
    def salvage_value(mech: Mech) -> int:
        """单台摧毁机甲折算 RP：剩余完好度比例 × 基准值（四舍五入，全空 → 0）。"""
        if mech.max_hull <= 0:
            return 0
        return round(mech.hull / mech.max_hull * SALVAGE_BASE_RP)

    def collect(self, state: CampaignState, battle: BattleService) -> int:
        """结算当前战局：累计全部已摧毁敌方机甲折算值并入账，返回本次入账 RP。"""
        earned = sum(
            self.salvage_value(mech)
            for mech in battle.mechs.values()
            if mech.faction is Faction.ENEMY and not mech.alive
        )
        state.requisition_points += earned
        return earned
