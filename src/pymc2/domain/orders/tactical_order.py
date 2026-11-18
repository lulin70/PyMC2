"""TacticalOrder DSL：MC2 tacordr.h 式类型化战术指令对象（M2）。

设计参照 MechCommander 2 的 TacticalOrder（tacordr.h）：
- 指令是纯数据对象（frozen dataclass），不含执行逻辑，由服务层分发执行；
- origin 区分指令来源（玩家 / 指挥官 AI / 单位自主），便于回放与 AI 复盘；
- 指令可序列化（to_dict/from_dict），为回放系统保留通道。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class OrderOrigin(Enum):
    """指令来源三分类（MC2：玩家指令 / AI 指挥官 / 单位自主行为）。"""

    PLAYER = "player"
    COMMANDER = "commander"
    SELF = "self"


class OrderType(Enum):
    """战术指令类型：M2 实现以下 7 种，枚举结构为后续里程碑预留扩展。"""

    MOVE_TO_POINT = "move_to_point"
    ATTACK_OBJECT = "attack_object"
    HOLD_FIRE = "hold_fire"
    FIRE_FREE = "fire_free"
    FIRE_HOLD = "fire_hold"
    GUARD = "guard"
    STOP = "stop"


_NEEDS_TARGET_TILE = frozenset({OrderType.MOVE_TO_POINT})
_NEEDS_TARGET_UNIT = frozenset({OrderType.ATTACK_OBJECT})


@dataclass(frozen=True)
class TacticalOrder:
    """一条类型化战术指令：下发给 unit_ids 全部成员，payload 按指令类型二选一。"""

    order_type: OrderType
    unit_ids: tuple[str, ...]
    origin: OrderOrigin
    target_tile: tuple[int, int] | None
    target_unit_id: str | None
    issued_tick: int = 0

    def __post_init__(self) -> None:
        if not self.unit_ids:
            raise ValueError("TacticalOrder.unit_ids 不能为空")
        if self.order_type in _NEEDS_TARGET_TILE and self.target_tile is None:
            raise ValueError(f"{self.order_type.value} 需要 target_tile")
        if self.order_type in _NEEDS_TARGET_UNIT and self.target_unit_id is None:
            raise ValueError(f"{self.order_type.value} 需要 target_unit_id")

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON 兼容字典（指令日志 / 回放用）。"""
        return {
            "order_type": self.order_type.value,
            "unit_ids": list(self.unit_ids),
            "origin": self.origin.value,
            "target_tile": list(self.target_tile) if self.target_tile is not None else None,
            "target_unit_id": self.target_unit_id,
            "issued_tick": self.issued_tick,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TacticalOrder:
        """从 to_dict 产物还原指令（与原对象按值相等）。"""
        raw_tile = data.get("target_tile")
        target_tile = (int(raw_tile[0]), int(raw_tile[1])) if raw_tile is not None else None
        return cls(
            order_type=OrderType(data["order_type"]),
            unit_ids=tuple(data["unit_ids"]),
            origin=OrderOrigin(data["origin"]),
            target_tile=target_tile,
            target_unit_id=data.get("target_unit_id"),
            issued_tick=int(data.get("issued_tick", 0)),
        )
