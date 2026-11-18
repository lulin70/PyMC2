"""MC2AssetPack 单测：PIL 现场生成 TGA fixture 建 mini pack（真实 MC2 资产零入库）。

覆盖：load 优雅降级（root 缺失 / 无 Data / 零命中）、三目录扫描与 256 优先、
大小写不敏感（stem 与扩展名）、resolve 匹配链（精确 → 包含 → None）、模板映射
（px-01→madcat / px-02→thor / ex→urbanmech / px 前缀回退）、缩放缓存。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pygame
import pytest
from PIL import Image

from pymc2.infrastructure.mc2_assets import MC2AssetPack

SIZE_PX = (32, 32)
MADCAT_COLOR = (200, 40, 40, 255)
THOR_COLOR = (40, 200, 80, 255)
URBANMECH_COLOR = (60, 90, 220, 255)
AWESOME_COLOR = (200, 160, 40, 255)


@pytest.fixture()
def pygame_context() -> Iterator[None]:
    pygame.init()
    yield
    pygame.quit()


def write_tga(path: Path, color: tuple[int, int, int, int]) -> None:
    """现场生成 TGA fixture（纯色 32x32），不提交任何真实 MC2 资产。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", SIZE_PX, color).save(path)


@pytest.fixture()
def pack_root(tmp_path: Path) -> Path:
    """mini pack：覆盖 256/128/Art 三目录 + 大写扩展名 + 包含匹配命名。"""
    root = tmp_path / "mc2-shared-source"
    write_tga(root / "Data" / "TGL" / "256" / "MadCat.tga", MADCAT_COLOR)
    write_tga(root / "Data" / "TGL" / "128" / "THOR.TGA", THOR_COLOR)
    write_tga(root / "Data" / "Art" / "MCL_MC_UrbanMech_A.tga", URBANMECH_COLOR)
    write_tga(root / "Data" / "TGL" / "256" / "AWESOME1.tga", AWESOME_COLOR)
    return root


class TestLoad:
    def test_missing_root_returns_none(self, tmp_path: Path) -> None:
        assert MC2AssetPack.load(tmp_path / "nope") is None

    def test_root_without_data_dir_returns_none(self, tmp_path: Path) -> None:
        root = tmp_path / "empty"
        root.mkdir()
        assert MC2AssetPack.load(root) is None

    def test_data_dir_without_tga_returns_none(self, tmp_path: Path) -> None:
        root = tmp_path / "nogn"
        (root / "Data").mkdir(parents=True)
        (root / "Data" / "readme.txt").write_text("no assets here")
        assert MC2AssetPack.load(root) is None

    def test_corrupt_tga_is_skipped(self, pack_root: Path) -> None:
        broken = pack_root / "Data" / "TGL" / "256" / "broken.tga"
        broken.write_bytes(b"not-a-tga")
        pack = MC2AssetPack.load(pack_root)
        assert pack is not None
        assert pack.tga_count == 4  # 损坏文件不阻断也不计入
        assert pack.resolve_mech_sprite("broken") is None

    def test_load_indexes_all_dirs_with_stats(self, pack_root: Path) -> None:
        pack = MC2AssetPack.load(pack_root)
        assert pack is not None
        assert pack.tga_count == 4
        assert pack.root == pack_root

    def test_duplicate_stem_prefers_256_dir(self, tmp_path: Path) -> None:
        root = tmp_path / "mc2"
        write_tga(root / "Data" / "TGL" / "256" / "madcat.tga", MADCAT_COLOR)
        write_tga(root / "Data" / "TGL" / "128" / "madcat.tga", (9, 9, 9, 255))
        pack = MC2AssetPack.load(root)
        assert pack is not None
        sprite = pack.resolve_mech_sprite("px-01")
        assert sprite is not None
        assert sprite.get_at((16, 16)) == MADCAT_COLOR  # 256 高清版胜出


class TestResolveMechSprite:
    def test_px01_maps_to_madcat_exact_stem(self, pack_root: Path) -> None:
        pack = MC2AssetPack.load(pack_root)
        sprite = pack.resolve_mech_sprite("px-01")
        assert sprite is not None
        assert sprite.get_size() == SIZE_PX
        assert sprite.get_at((16, 16)) == MADCAT_COLOR

    def test_px02_maps_to_thor_via_128_dir(self, pack_root: Path) -> None:
        pack = MC2AssetPack.load(pack_root)
        sprite = pack.resolve_mech_sprite("px-02")
        assert sprite is not None
        assert sprite.get_at((16, 16)) == THOR_COLOR  # .TGA 大写扩展名 + 大写 stem 均命中

    def test_ex_mechs_map_to_urbanmech_via_containment(self, pack_root: Path) -> None:
        pack = MC2AssetPack.load(pack_root)
        for mech_id in ("ex-01", "ex-02"):
            sprite = pack.resolve_mech_sprite(mech_id)
            assert sprite is not None
            assert sprite.get_at((16, 16)) == URBANMECH_COLOR  # urbanmech ⊆ mcl_mc_urbanmech_a

    def test_px_prefix_fallback_for_hired_mechs(self, pack_root: Path) -> None:
        """M5 hire 的 px-03+ 剥编号回退 px 模板 → madcat。"""
        pack = MC2AssetPack.load(pack_root)
        sprite = pack.resolve_mech_sprite("px-03")
        assert sprite is not None
        assert sprite.get_at((16, 16)) == MADCAT_COLOR

    def test_containment_match_for_unregistered_kind(self, pack_root: Path) -> None:
        """未登记模板按原样进匹配链：awesome ⊆ awesome1。"""
        pack = MC2AssetPack.load(pack_root)
        sprite = pack.resolve_mech_sprite("awesome")
        assert sprite is not None
        assert sprite.get_at((16, 16)) == AWESOME_COLOR

    def test_unknown_kind_returns_none(self, pack_root: Path) -> None:
        pack = MC2AssetPack.load(pack_root)
        assert pack.resolve_mech_sprite("zz-09") is None
        assert pack.resolve_mech_sprite("  ") is None


class TestScaledCache:
    def test_scaled_sprite_resized_and_cached(self, pack_root: Path) -> None:
        pack = MC2AssetPack.load(pack_root)
        first = pack.scaled_mech_sprite("px-01", 16)
        assert first is not None
        assert first.get_size() == (16, 16)
        assert pack.scaled_mech_sprite("px-01", 16) is first  # 同 (kind, size) 命中缓存

    def test_scaled_miss_returns_none(self, pack_root: Path) -> None:
        pack = MC2AssetPack.load(pack_root)
        assert pack.scaled_mech_sprite("zz-09", 16) is None
