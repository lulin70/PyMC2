"""交互/演示模式 CLI 单测 + AI 指挥官阵营边界（玩家机甲不受敌方/受控方越界指挥）。

另含 main 资产加载接线：显式 --mc2-assets 优先于自动发现（env → ~/.pymc2/mc2-assets），
全部用 monkeypatch 隔离（不读本机真实 MC2 资产仓库，fixture 为 PIL 现场生成 TGA）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from pymc2.domain.entities.mech import Faction
from pymc2.domain.orders.tactical_order import OrderOrigin
from pymc2.infrastructure.mc2_assets import ASSETS_ENV_VAR, MC2AssetPack
from pymc2.main import _load_asset_pack, parse_args
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


@pytest.fixture()
def mini_pack_root(tmp_path: Path) -> Path:
    """单张 TGA 的 mini pack（PIL 现场生成，真实 MC2 资产零入库）。"""
    root = tmp_path / "mc2-mini"
    sprite_dir = root / "Data" / "TGL" / "256"
    sprite_dir.mkdir(parents=True)
    Image.new("RGBA", (8, 8), (200, 40, 40, 255)).save(sprite_dir / "madcat.tga")
    return root


@pytest.fixture()
def isolated_asset_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """资产探测隔离：env 清空 + HOME 指向空目录（绝不读本机真实 ~/.pymc2）。"""
    monkeypatch.delenv(ASSETS_ENV_VAR, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "void-home"))


class TestLoadAssetPack:
    """main 资产加载接线：显式路径 > 自动发现；成功摘要/降级提示/静默三态。"""

    def test_explicit_path_beats_auto_discovery(
        self, tmp_path: Path, mini_pack_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        explicit_root = tmp_path / "explicit-mini"
        (explicit_root / "Data" / "TGL" / "256").mkdir(parents=True)
        Image.new("RGBA", (8, 8), (40, 200, 80, 255)).save(explicit_root / "Data" / "TGL" / "256" / "thor.tga")
        # env 指向另一个有效 pack：显式路径必须胜出
        monkeypatch.setenv(ASSETS_ENV_VAR, str(mini_pack_root))
        pack = _load_asset_pack(explicit_root)
        assert pack is not None
        assert pack.root == explicit_root
        assert pack.tga_count == 1
        out = capsys.readouterr().out
        assert f"[PyMC2] MC2 资产已加载：{explicit_root}（1 张，机甲精灵已启用）" in out

    def test_auto_discovery_via_env(
        self, mini_pack_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv(ASSETS_ENV_VAR, str(mini_pack_root))
        monkeypatch.setenv("HOME", "/nonexistent-pymc2-home")
        pack = _load_asset_pack(None)
        assert pack is not None
        assert pack.root == mini_pack_root
        assert "MC2 资产已加载" in capsys.readouterr().out

    def test_invalid_candidate_degrades_with_message(
        self, tmp_path: Path, isolated_asset_env: None, capsys: pytest.CaptureFixture[str]
    ) -> None:
        empty = tmp_path / "no-data"
        empty.mkdir()
        assert _load_asset_pack(empty) is None
        assert "降级程序化绘制" in capsys.readouterr().out

    def test_no_candidate_stays_silent(self, isolated_asset_env: None, capsys: pytest.CaptureFixture[str]) -> None:
        assert _load_asset_pack(None) is None
        assert capsys.readouterr().out == ""  # 无候选：静默程序化绘制（维持原无参行为）

    def test_auto_discovery_via_home_cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ASSETS_ENV_VAR, raising=False)
        home = tmp_path / "home"
        cache = home / ".pymc2" / "mc2-assets"
        cache.mkdir(parents=True)
        (cache / "Data" / "TGL" / "256").mkdir(parents=True)
        Image.new("RGBA", (8, 8), (200, 40, 40, 255)).save(cache / "Data" / "TGL" / "256" / "madcat.tga")
        monkeypatch.setenv("HOME", str(home))
        pack = _load_asset_pack(None)
        assert pack is not None
        assert pack.root == cache
        assert isinstance(pack, MC2AssetPack)
