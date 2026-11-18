"""战役状态（M5）：RP 账本 + 名册 + 任务指针，含 Pilot/底盘/机甲三档序列化。

MC2 参照：logistics 层的战役持久状态（RP 点数、mech 名册、任务进度）。本波最小实现：
- CampaignState：campaign_id / mission_index / requisition_points / roster（整备层 Mech 对象）。
- Pilot 序列化在 domain/entities/pilot.py（to_dict/from_dict，M4 遗留项补齐）。
- 底盘序列化只携带各部位 armor/structure 余量（任务间携带的核心口径）；武器槽与
  热量为战斗内临时状态，不入档（新任务由 Mech 构造重新挂载/归零）。
- max_hull 单独入档：受损底盘的 total_integrity 已低于出厂值，重建时必须恢复
  hull_ratio 的分母口径（AI PRESERVE_FORCE 判定依赖）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pymc2.domain.entities.mech import Faction, Mech, Weapon
from pymc2.domain.entities.mech_chassis import BodyPart, MechChassis, MechLocation
from pymc2.domain.entities.pilot import Pilot

CAMPAIGN_ID_DEMO = "demo"
STARTING_REQUISITION_POINTS = 30
STARTING_ROSTER_HULL = 100

# 起始名册武器（口径与 create_m2_battle 双机甲一致）。
PLAYER_PPC = ("ppc", 25, 420.0, 2.0)
PLAYER_MLASER = ("mlaser", 15, 380.0, 1.5)


# ------------------------------------------------------------------ 底盘 / 机甲序列化


def chassis_to_dict(chassis: MechChassis) -> dict[str, Any]:
    """底盘快照：八部位 armor/structure 余量（部位键用枚举 value，JSON 友好）。"""
    return {
        "parts": {
            location.value: {"armor": part.armor, "structure": part.structure}
            for location, part in chassis.parts.items()
        }
    }


def chassis_from_dict(data: dict[str, Any]) -> MechChassis:
    """从快照重建底盘（武器槽为空，由 mech_from_dict 重新挂载）。"""
    parts = {
        MechLocation(key): BodyPart(armor=float(part["armor"]), structure=float(part["structure"]))
        for key, part in dict(data["parts"]).items()
    }
    missing = [location.value for location in MechLocation if location not in parts]
    if missing:
        raise ValueError(f"chassis snapshot missing locations: {', '.join(missing)}")
    return MechChassis(parts)


def weapon_to_dict(weapon: Weapon) -> dict[str, Any]:
    """武器静态参数快照（冷却余量为战斗内状态，不入档）。"""
    return {
        "name": weapon.name,
        "damage": weapon.damage,
        "range_px": weapon.range_px,
        "cooldown_s": weapon.cooldown_s,
        "mounted_location": weapon.mounted_location.value,
        "heat_per_shot": weapon.heat_per_shot,
    }


def weapon_from_dict(data: dict[str, Any]) -> Weapon:
    """从快照重建武器（挂载部位缺省 CT）。"""
    return Weapon(
        name=str(data["name"]),
        damage=int(data["damage"]),
        range_px=float(data["range_px"]),
        cooldown_s=float(data["cooldown_s"]),
        mounted_location=MechLocation(str(data.get("mounted_location", MechLocation.CT.value))),
        heat_per_shot=float(data.get("heat_per_shot", 5.0)),
    )


def mech_to_dict(mech: Mech) -> dict[str, Any]:
    """机甲整备快照：标识/位置/武器/底盘余量/驾驶员/max_hull（M5 名册入档单元）。"""
    return {
        "id": mech.id,
        "faction": mech.faction.value,
        "x": mech.x,
        "y": mech.y,
        "max_hull": mech.max_hull,
        "weapon": weapon_to_dict(mech.weapon),
        "chassis": chassis_to_dict(mech.chassis),
        "pilot": mech.pilot.to_dict() if mech.pilot is not None else None,
    }


def mech_from_dict(data: dict[str, Any]) -> Mech:
    """从快照重建机甲；max_hull 恢复后 hull_ratio 口径与受损前一致。"""
    mech = Mech(
        mech_id=str(data["id"]),
        faction=Faction(str(data["faction"])),
        x=float(data["x"]),
        y=float(data["y"]),
        weapon=weapon_from_dict(dict(data["weapon"])),
        chassis=chassis_from_dict(dict(data["chassis"])),
        pilot=Pilot.from_dict(dict(data["pilot"])) if data.get("pilot") is not None else None,
    )
    mech.max_hull = int(data["max_hull"])
    return mech


# ------------------------------------------------------------------ 战役状态


@dataclass
class CampaignState:
    """战役持久状态：RP 账本、名册（整备层 Mech）、当前任务指针。"""

    campaign_id: str
    mission_index: int = 0
    requisition_points: int = 0
    roster: list[Mech] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """战役存档快照（名册逐台序列化）。"""
        return {
            "campaign_id": self.campaign_id,
            "mission_index": self.mission_index,
            "requisition_points": self.requisition_points,
            "roster": [mech_to_dict(mech) for mech in self.roster],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CampaignState:
        """从存档重建战役状态。"""
        return cls(
            campaign_id=str(data["campaign_id"]),
            mission_index=int(data["mission_index"]),
            requisition_points=int(data["requisition_points"]),
            roster=[mech_from_dict(entry) for entry in data["roster"]],
        )


def make_roster_mech(mech_id: str, weapon_spec: tuple[str, int, float, float], pilot_name: str) -> Mech:
    """起始名册机甲工厂：hull 100 中型底盘 + 指定武器 + 驾驶员（roster 顺序即参战顺序）。"""
    name, damage, range_px, cooldown_s = weapon_spec
    return Mech(
        mech_id,
        Faction.PLAYER,
        0.0,
        0.0,
        hull=STARTING_ROSTER_HULL,
        weapon=Weapon(name, damage, range_px, cooldown_s),
        pilot=Pilot(pilot_name),
    )


def start_campaign(campaign_id: str = CAMPAIGN_ID_DEMO) -> CampaignState:
    """起始战役：RP 30 + 双机甲名册（px-01 重炮 / px-02 中炮，均带 rookie 驾驶员）。"""
    return CampaignState(
        campaign_id=campaign_id,
        requisition_points=STARTING_REQUISITION_POINTS,
        roster=[
            make_roster_mech("px-01", PLAYER_PPC, "player-gunner"),
            make_roster_mech("px-02", PLAYER_MLASER, "player-rookie"),
        ],
    )
