"""W2 装配 IO 服务测试（FR-3，docs/W2_TEST_PLAN_20261128.md §1）。"""

from __future__ import annotations

import json
from pathlib import Path

from pymc2.domain.loadout import ARMOR_CAPS, LoadoutConfig, WeaponSlotConfig
from pymc2.services.loadout_service import LoadoutService


def _service(tmp_path: Path) -> LoadoutService:
    return LoadoutService(root=tmp_path / "loadouts")


def _config() -> LoadoutConfig:
    return LoadoutConfig(
        chassis_kind="medium",
        armor_allocation={loc: min(4, cap) for loc, cap in ARMOR_CAPS.items()},
        weapons=[WeaponSlotConfig("PPC", "ct")],
    )


class TestSaveLoad:
    """FR-3.2：跨实例 roundtrip 逐字段一致。"""

    def test_save_load_roundtrip(self, tmp_path: Path):
        svc = _service(tmp_path)
        cfg = _config()
        assert svc.save(cfg, "sniper") is None
        loaded = svc.load("sniper")
        assert isinstance(loaded, LoadoutConfig)
        assert loaded == cfg

    def test_list_saved_sorted(self, tmp_path: Path):
        svc = _service(tmp_path)
        svc.save(_config(), "b_rifle")
        svc.save(_config(), "a_laser")
        assert svc.list_saved() == ["a_laser", "b_rifle"]


class TestNameSecurity:
    """FR-3.1：文件名白名单（路径穿越防护）。"""

    def test_reject_path_traversal(self, tmp_path: Path):
        svc = _service(tmp_path)
        assert svc.save(_config(), "../evil").code == "BAD_NAME"
        assert svc.load("../evil").code == "BAD_NAME"

    def test_reject_spaces(self, tmp_path: Path):
        svc = _service(tmp_path)
        assert svc.save(_config(), "my loadout").code == "BAD_NAME"


class TestCorruptInput:
    """FR-3.3：损坏输入不崩溃（3 用例）。"""

    def test_truncated_json(self, tmp_path: Path):
        svc = _service(tmp_path)
        svc.root.mkdir(parents=True)
        (svc.root / "trunc.json").write_text('{"chassis_kind": "medi', encoding="utf-8")
        assert svc.load("trunc").code == "BAD_JSON"

    def test_empty_file(self, tmp_path: Path):
        svc = _service(tmp_path)
        svc.root.mkdir(parents=True)
        (svc.root / "empty.json").write_text("", encoding="utf-8")
        assert svc.load("empty").code == "BAD_JSON"

    def test_wrong_field_type(self, tmp_path: Path):
        svc = _service(tmp_path)
        svc.root.mkdir(parents=True)
        payload = json.dumps({"chassis_kind": ["not", "a", "string"], "armor_allocation": {}, "weapons": []})
        (svc.root / "badtype.json").write_text(payload, encoding="utf-8")
        assert svc.load("badtype").code == "BAD_JSON"

    def test_missing_file(self, tmp_path: Path):
        svc = _service(tmp_path)
        assert svc.load("nonexistent").code == "FILE_ERROR"


class TestDefaultConfig:
    """FR-5.1：默认配置路径。"""

    def test_default_config_valid(self, tmp_path: Path):
        svc = _service(tmp_path)
        for tier in ("light", "medium", "heavy", "assault"):
            cfg = svc.default_config(tier)
            assert cfg.validate() is None
