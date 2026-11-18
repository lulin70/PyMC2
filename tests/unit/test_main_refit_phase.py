"""main.py M5/M6 接线单测：结算摘要与整备阶段打印（文本 UI 最小接线验收）。"""

from __future__ import annotations

import pytest

from pymc2.main import _print_settlement, _run_refit_phase
from pymc2.services.campaign_state import start_campaign
from pymc2.services.mission_chain import MissionResult
from pymc2.services.refit_service import RefitService


class TestPrintSettlement:
    def test_victory_settlement_prints_rp_breakdown(self, capsys: pytest.CaptureFixture[str]) -> None:
        state = start_campaign("t")
        state.requisition_points = 88
        result = MissionResult(
            mission_id="m1_river_crossing",
            victory=True,
            rp_reward=25,
            rp_salvage=33,
            lost_unit_ids=[],
            survivor_ids=["px-01", "px-02"],
        )
        _print_settlement(state, result)
        out = capsys.readouterr().out
        assert "m1_river_crossing: 胜利" in out
        assert "+25(奖励) +33(salvage)" in out
        assert "结余 88" in out
        assert "px-01 px-02" in out

    def test_defeat_settlement_prints_losses(self, capsys: pytest.CaptureFixture[str]) -> None:
        state = start_campaign("t")
        result = MissionResult(
            mission_id="m1_river_crossing",
            victory=False,
            rp_reward=0,
            rp_salvage=0,
            lost_unit_ids=["px-01", "px-02"],
            survivor_ids=[],
        )
        _print_settlement(state, result)
        out = capsys.readouterr().out
        assert "战败" in out
        assert "损失: px-01、px-02" in out
        assert "幸存: 无" in out


class TestRunRefitPhase:
    def test_refit_phase_repairs_and_hires_with_summary(self, capsys: pytest.CaptureFixture[str]) -> None:
        state = start_campaign("t")
        state.requisition_points = 100
        state.roster[0].take_damage(30.0)  # 缺口 30 → 修复费 15 RP
        _run_refit_phase(state, RefitService())
        out = capsys.readouterr().out
        assert "== 整备 ==" in out
        assert "修复 px-01（-15 RP → 结余 85）" in out
        assert "新购 px-03（-40 RP → 结余 45）" in out
        assert state.roster[0].hull == state.roster[0].max_hull
        assert [m.id for m in state.roster] == ["px-01", "px-02", "px-03"]
        assert state.requisition_points == 45

    def test_refit_phase_skips_full_integrity_mechs(self, capsys: pytest.CaptureFixture[str]) -> None:
        state = start_campaign("t")
        state.requisition_points = 10  # 不够 hire，无受损 → 无整备输出行
        _run_refit_phase(state, RefitService())
        out = capsys.readouterr().out
        assert out.strip() == "== 整备 =="
        assert state.requisition_points == 10
        assert len(state.roster) == 2
