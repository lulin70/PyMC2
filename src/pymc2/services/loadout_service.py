"""装配配置服务（W2，设计 docs/W2_DESIGN_20261128.md §3）：校验 + 保存/载入。

安全：文件名白名单 [A-Za-z0-9_-]（路径穿越防护，FR-3.1）+ 文件大小 ≤1MB（NFR）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pymc2.domain.loadout import LoadoutConfig, LoadoutError

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_JSON_BYTES = 1024 * 1024  # 1MB（NFR 安全上限）
LOADOUTS_DIR = Path.home() / ".pymc2" / "loadouts"


class LoadoutService:
    """装配配置校验与持久化（无 pygame 依赖）。"""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root if root is not None else LOADOUTS_DIR

    # -- 校验（FR-2.2）--

    def validate(self, config: LoadoutConfig) -> LoadoutError | None:
        return config.validate()

    # -- 持久化（FR-3）--

    def save(self, config: LoadoutConfig, name: str) -> LoadoutError | None:
        err = _check_name(name)
        if err is not None:
            return err
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / f"{name}.json"
            payload = json.dumps(config.to_json(), ensure_ascii=False, indent=2)
            path.write_text(payload, encoding="utf-8")
        except OSError as exc:
            return LoadoutError("FILE_ERROR", f"save failed: {exc}")
        return None

    def load(self, name: str) -> LoadoutConfig | LoadoutError:
        err = _check_name(name)
        if err is not None:
            return err
        path = self.root / f"{name}.json"
        if not path.is_file():
            return LoadoutError("FILE_ERROR", f"not found: {path}")
        try:
            if path.stat().st_size > _MAX_JSON_BYTES:
                return LoadoutError("BAD_JSON", f"file exceeds {_MAX_JSON_BYTES} bytes")
            data = json.loads(path.read_text(encoding="utf-8"))
            return LoadoutConfig.from_json(data)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError) as exc:
            return LoadoutError("BAD_JSON", f"corrupt loadout: {exc}")

    def list_saved(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.stem for p in self.root.glob("*.json") if _NAME_RE.match(p.stem))

    # -- 默认配置（FR-5.1 零破坏路径）--

    def default_config(self, chassis_kind: str = "medium") -> LoadoutConfig:
        return LoadoutConfig.default_config(chassis_kind)


def _check_name(name: str) -> LoadoutError | None:
    """文件名白名单（FR-3.1 路径穿越防护）。"""
    if not _NAME_RE.match(name):
        return LoadoutError("BAD_NAME", f"name must match [A-Za-z0-9_-]: {name!r}")
    return None
