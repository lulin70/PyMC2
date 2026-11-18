"""交互/演示模式 CLI 单测 + AI 指挥官阵营边界（玩家机甲不受敌方/受控方越界指挥）。"""

from __future__ import annotations

from pathlib import Path

from pymc2.domain.entities.mech import Faction
from pymc2.domain.orders.tactical_order import OrderOrigin
from pymc2.main import parse_args
from pymc2.services.ai_commander import AiCommander
from pymc2.services.battle_service import create_m2_battle


class TestParseArgs:
    def test_default_is_interactive(self) -> None:
        """无参数 = 交互模式（demo 关、资产未指定）。"""
        args = parse_args([])
        assert args.demo is False
        assert args.mc2_assets is None

    def test_demo_flag_switches_to_auto_demo(self) -> None:
        assert parse_args(["--demo"]).demo is True

    def test_mc2_assets_path_is_parsed(self) -> None:
        args = parse_args(["--mc2-assets", "/tmp/mc2-shared-source"])
        assert args.mc2_assets == Path("/tmp/mc2-shared-source")


class TestCommanderFactionBoundary:
    """交互模式边界：敌方指挥官只为己方机甲产出指令，绝不控制玩家机甲。"""

    def test_enemy_commander_never_controls_player_mechs(self) -> None:
        orders = AiCommander(Faction.ENEMY).propose(create_m2_battle())
        assert orders  # 敌方存活 → 至少产出一条指令
        for order in orders:
            assert order.origin is OrderOrigin.COMMANDER
            assert set(order.unit_ids).isdisjoint({"px-01", "px-02"})

    def test_player_commander_never_controls_enemy_mechs(self) -> None:
        """--demo 双侧 AI 时玩家侧指挥官同样只控己方（对称边界）。"""
        orders = AiCommander(Faction.PLAYER).propose(create_m2_battle())
        assert orders
        for order in orders:
            assert order.origin is OrderOrigin.COMMANDER
            assert set(order.unit_ids).isdisjoint({"ex-01"})
