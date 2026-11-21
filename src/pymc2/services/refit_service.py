"""任务间整备（M5）：repair（RP→装甲修复）/ hire（RP→新机甲）/ carryover（状态携带）。

MC2 参照：logistics 层整备厂。规则口径：
- repair：修复到出厂满值，花费 = 缺口完好度 × 0.5 RP/点（向上取整）；RP 不足则
  拒绝（不做部分修复）。修复目标按 MechChassis.from_total(max_hull) 标准模板重铺
  （名册机甲均由该模板构造，出厂分配精确还原；异构底盘会被重排到模板口径）。
- hire：固定价购入新机甲（hull 100 中型底盘 + rookie 驾驶员），id 沿 px-NN 递增。
- carryover：战斗副本的存活者状态序列化回写名册（rank/装甲携带），阵亡者移除。
"""

from __future__ import annotations

import math

from pymc2.domain.entities.mech import Faction, Mech
from pymc2.domain.entities.mech_chassis import BodyPart, MechChassis
from pymc2.domain.entities.pilot import Pilot
from pymc2.services.battle_service import BattleService
from pymc2.services.campaign_state import CampaignState, mech_from_dict, mech_to_dict

REPAIR_RP_PER_INTEGRITY_POINT = 0.5
HIRE_COST_RP = 40
HIRE_HULL = 100
ROSTER_UNIT_PREFIX = "px-"


class RefitService:
    """整备服务：repair / hire / carryover_survivors（无状态方法集合）。"""

    @staticmethod
    def repair_cost(mech: Mech) -> int:
        """修复到满所需 RP（缺口完好度 × 单价，向上取整；满血为 0）。"""
        missing = mech.max_hull - mech.hull
        if missing <= 0:
            return 0
        return math.ceil(missing * REPAIR_RP_PER_INTEGRITY_POINT)

    def repair(self, state: CampaignState, mech_id: str) -> bool:
        """修复名册机甲到出厂满值；RP 不足 / 已满血 / 未知 id 时拒绝并返回 False。"""
        mech = next((m for m in state.roster if m.id == mech_id), None)
        if mech is None:
            return False
        cost = self.repair_cost(mech)
        if cost <= 0 or state.requisition_points < cost:
            return False
        state.requisition_points -= cost
        restored = MechChassis.from_total(mech.max_hull)
        for location, part in restored.parts.items():
            mech.chassis.parts[location] = BodyPart(part.armor, part.structure)
        return True

    def hire(self, state: CampaignState) -> Mech | None:
        """购入新机甲（+ rookie 驾驶员）进名册尾部；RP 不足返回 None 不扣款。"""
        if state.requisition_points < HIRE_COST_RP:
            return None
        state.requisition_points -= HIRE_COST_RP
        mech_id = f"{ROSTER_UNIT_PREFIX}{self._next_roster_number(state):02d}"
        mech = Mech(
            mech_id,
            Faction.PLAYER,
            0.0,
            0.0,
            hull=HIRE_HULL,
            pilot=Pilot(f"recruit-{mech_id}"),
        )
        state.roster.append(mech)
        return mech

    @staticmethod
    def carryover_survivors(state: CampaignState, battle: BattleService) -> list[str]:
        """结算携带：存活参战机甲状态回写名册（阵亡移除）；返回幸存 id（参战序）。

        P2：isinstance 门槛——名册/整备闭环保持 Mech-only（载具/炮塔为战局内资产，
        序列化与 repair 均按底盘模板，不跨关携带；存量战局全 mech，恒 True 零改动）。
        """
        survivors: list[str] = []
        carried: list[Mech] = []
        for mech in battle.mechs.values():
            if mech.faction is not Faction.PLAYER:
                continue
            if isinstance(mech, Mech) and mech.alive:
                carried.append(mech_from_dict(mech_to_dict(mech)))
                survivors.append(mech.id)
        state.roster = carried
        return survivors

    @staticmethod
    def _next_roster_number(state: CampaignState) -> int:
        """名册编号递增：解析既有 px-NN 的最大编号 +1，无解析项时按数量兜底。"""
        highest = 0
        for mech in state.roster:
            if mech.id.startswith(ROSTER_UNIT_PREFIX):
                suffix = mech.id[len(ROSTER_UNIT_PREFIX) :]
                if suffix.isdigit():
                    highest = max(highest, int(suffix))
        return max(highest, len(state.roster)) + 1
