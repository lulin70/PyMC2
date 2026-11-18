"""驾驶员技能模型（M4）：use/success 双计数成长。

参照 PyCC2 Wave 2 的 VeteranSkillStats 模式（MC2 Warrior numSkillUses/
numSkillSuccesses 双计数），与 PyCC2 不同的是本波直接接入弹道消费点：
- PilotSkill：技能枚举，gunnery 起步；dict 结构按技能扩展（piloting 等预留）。
- SkillStats：单技能的双计数（uses 开火次数 / successes 命中次数）。
- Pilot：挂载于 Mech.pilot（可选，缺省无驾驶员 → 弹道技能系数中性 1.0）。
  gunnery_rank 为纯派生值：min(5.0, kills*0.25 + hit_rate*2.0)，缓慢成长封顶。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

MAX_GUNNERY_RANK = 5.0
KILL_RANK_WEIGHT = 0.25
HIT_RATE_RANK_WEIGHT = 2.0


class PilotSkill(Enum):
    """驾驶员技能：gunnery（射击）起步，后续里程碑可扩展（piloting 等）。"""

    GUNNERY = "gunnery"


@dataclass(slots=True)
class SkillStats:
    """单技能 use/success 双计数（MC2 numSkillUses 模式）：命中率按比值派生。"""

    uses: int = 0
    successes: int = 0


class Pilot:
    """驾驶员：技能成长表 + 击杀计数；rank 为派生值，无额外可变状态。"""

    def __init__(self, name: str = "unnamed") -> None:
        self.name = name
        self.kills: int = 0
        self.skill_stats: dict[PilotSkill, SkillStats] = {PilotSkill.GUNNERY: SkillStats()}

    def record_shot(self, hit: bool) -> None:
        """记录一次开火：uses 必增；命中时 successes 同增（双计数成长源）。"""
        stats = self.skill_stats.setdefault(PilotSkill.GUNNERY, SkillStats())
        stats.uses += 1
        if hit:
            stats.successes += 1

    def record_kill(self) -> None:
        """记录一次击杀（kills 只增不减，rank 的单调上升分量）。"""
        self.kills += 1

    @property
    def gunnery_hit_rate(self) -> float:
        """gunnery 命中率（successes / uses）；未开火时 0.0。"""
        stats = self.skill_stats.get(PilotSkill.GUNNERY)
        if stats is None or stats.uses == 0:
            return 0.0
        return stats.successes / stats.uses

    @property
    def gunnery_rank(self) -> float:
        """派生技能 rank（0.0-5.0）：kills*0.25 + hit_rate*2.0，封顶 5.0。

        击杀分量单调不减；命中率分量随双计数比值波动（全部命中场景下单调）。
        """
        return min(MAX_GUNNERY_RANK, self.kills * KILL_RANK_WEIGHT + self.gunnery_hit_rate * HIT_RATE_RANK_WEIGHT)

    # ------------------------------------------------------------------ 序列化（M5 战役携带）

    def to_dict(self) -> dict[str, Any]:
        """可 JSON 化快照：姓名 + kills + 各技能 use/success 双计数（M5 任务间携带源）。"""
        return {
            "name": self.name,
            "kills": self.kills,
            "skill_stats": {
                skill.value: {"uses": stats.uses, "successes": stats.successes}
                for skill, stats in self.skill_stats.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Pilot:
        """从快照重建驾驶员；未知技能键显式报错（存档损坏不静默吞掉）。"""
        pilot = cls(str(data["name"]))
        pilot.kills = int(data["kills"])
        for key, stats in dict(data["skill_stats"]).items():
            pilot.skill_stats[PilotSkill(key)] = SkillStats(
                uses=int(stats["uses"]),
                successes=int(stats["successes"]),
            )
        return pilot
