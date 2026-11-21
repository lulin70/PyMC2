"""MC2AssetPack 单测：PIL 现场生成 TGA fixture 建 mini pack（真实 MC2 资产零入库）。

覆盖：load 优雅降级（root 缺失 / 无 Data / 零命中）、三目录扫描与 256 优先、
大小写不敏感（stem 与扩展名）、resolve 匹配链（精确 → 包含 → None）、模板映射
（px-01→madcat / px-02→thor / ex→urbanmech / px 前缀回退）、缩放缓存、
default_pack_root 三级探测（env → 缓存目录 → None，HOME/env 全程 monkeypatch，
不读本机真实资产仓库）。
Wave V1 地形合成：ground/overlay/environment 三类索引、resolve_ground 确定性选张
（seed 取模、道路序列按帧号排序、Cement 城镇地形不暴露）、地面纹理 alpha 强制
不透明（MC2 管线 alpha=高光通道）、overlay 原生 alpha 蒙版保留、resolve_environment
别名 + _x/_dam 摧毁变体派生、ground-only pack 可加载。
Wave V2 受损变体：resolve_mech_sprite damaged 参数（dam/x 变体词派生、未命中回退
基名、(kind, damaged) 缓存分键、scaled 三元组缓存）。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pygame
import pytest
from PIL import Image

from pymc2.infrastructure.mc2_assets import ASSETS_ENV_VAR, MC2AssetPack, default_pack_root

SIZE_PX = (32, 32)
MADCAT_COLOR = (200, 40, 40, 255)
MADCAT_DAM_COLOR = (120, 40, 30, 255)
MADCAT_X_COLOR = (60, 30, 25, 255)
THOR_COLOR = (40, 200, 80, 255)
URBANMECH_COLOR = (60, 90, 220, 255)
AWESOME_COLOR = (200, 160, 40, 255)
# P2 单位谱系 fixture 色板（vehicle→storm / 受损变体 stormdam）
STORM_COLOR = (90, 110, 130, 255)
STORM_DAM_COLOR = (60, 70, 85, 255)
# Wave V1 fixture 色板
DETAIL_COLORS = [(150, 100, 60, 255), (90, 120, 70, 255), (40, 80, 120, 255)]
ROAD_FRAME_COLORS = [(140 + i, 120, 90, 255) for i in range(6)]  # 每帧异色验证帧号排序
TREE_COLOR = (30, 110, 40, 255)
TREE_X_COLOR = (70, 60, 50, 255)
HOUSE_DAM_COLOR = (110, 105, 95, 255)
JUNGLE_X_COLOR = (55, 65, 30, 255)
WRECK_COLOR = (120, 120, 120, 255)


@pytest.fixture()
def pygame_context() -> Iterator[None]:
    pygame.init()
    yield
    pygame.quit()


def write_tga(path: Path, color: tuple[int, int, int, int]) -> None:
    """现场生成 TGA fixture（纯色 32x32），不提交任何真实 MC2 资产。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", SIZE_PX, color).save(path)


def write_tga_masked(path: Path, opaque_color: tuple[int, int, int, int], opaque_width: int) -> None:
    """现场生成带形状蒙版的 overlay TGA：左侧 opaque_width 列不透明、右侧全透明。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", SIZE_PX, (0, 0, 0, 0))
    for x in range(min(opaque_width, SIZE_PX[0])):
        for y in range(SIZE_PX[1]):
            image.putpixel((x, y), opaque_color)
    image.save(path)


@pytest.fixture()
def pack_root(tmp_path: Path) -> Path:
    """mini pack：覆盖 256/128/Art 三目录 + 大写扩展名 + 包含匹配命名。"""
    root = tmp_path / "mc2-shared-source"
    write_tga(root / "Data" / "TGL" / "256" / "MadCat.tga", MADCAT_COLOR)
    write_tga(root / "Data" / "TGL" / "128" / "THOR.TGA", THOR_COLOR)
    write_tga(root / "Data" / "Art" / "MCL_MC_UrbanMech_A.tga", URBANMECH_COLOR)
    write_tga(root / "Data" / "TGL" / "256" / "AWESOME1.tga", AWESOME_COLOR)
    return root


@pytest.fixture()
def terrain_root(tmp_path: Path) -> Path:
    """地形 mini pack：3 张 Detail + Cement（城镇不暴露）+ Water、乱序写入的
    2lanedirt0000-0005 道路序列、树/房/摧毁变体环境精灵。"""
    root = tmp_path / "mc2-terrain"
    ground = root / "Data" / "Textures" / "64"
    for i, color in enumerate(DETAIL_COLORS):
        write_tga(ground / f"Detail_{(i + 1) * 10000}.tga", color)
    write_tga(ground / "Cement_1.tga", (120, 120, 120, 255))
    write_tga(ground / "Water_ocean.tga", (30, 60, 90, 255))
    overlays = root / "Data" / "Textures" / "64Overlays"
    for i in (5, 2, 0, 4, 1, 3):  # 乱序写入：验证按帧号排序
        write_tga(overlays / f"2lanedirt{i:04d}.TGA", ROAD_FRAME_COLORS[i])
    tgl = root / "Data" / "TGL" / "128"
    write_tga(tgl / "A_Pine&Palm.tga", TREE_COLOR)
    write_tga(tgl / "A_Pine&PalmX.tga", TREE_X_COLOR)
    write_tga(tgl / "A_GreenHouseDam.tga", HOUSE_DAM_COLOR)
    write_tga(tgl / "A_JungletreeAX.tga", JUNGLE_X_COLOR)
    write_tga(tgl / "A_MechDam.tga", WRECK_COLOR)
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


class TestDamagedMechVariant:
    """Wave V2 受损变体：damaged=True 派生 dam/x 变体 stem，未命中回退基名候选链。"""

    @pytest.fixture()
    def damaged_root(self, tmp_path: Path) -> Path:
        """基名 + Dam/X 双变体 mini pack（真资产零入库）。"""
        root = tmp_path / "mc2-damaged"
        write_tga(root / "Data" / "TGL" / "128" / "MadCat.tga", MADCAT_COLOR)
        write_tga(root / "Data" / "TGL" / "128" / "MadCatDam.tga", MADCAT_DAM_COLOR)
        write_tga(root / "Data" / "TGL" / "128" / "MadCatX.tga", MADCAT_X_COLOR)
        return root

    def test_base_lookup_unchanged_by_variant_files(self, damaged_root: Path) -> None:
        """damaged=False 保持基名链：变体文件在包内也不劫持基名解析。"""
        pack = MC2AssetPack.load(damaged_root)
        sprite = pack.resolve_mech_sprite("px-01")
        assert sprite is not None
        assert sprite.get_at((16, 16)) == MADCAT_COLOR

    def test_damaged_hits_dam_stem_first(self, damaged_root: Path) -> None:
        """damaged=True 先查变体词（dam 优先于 x）：命中 MadCatDam。"""
        pack = MC2AssetPack.load(damaged_root)
        sprite = pack.resolve_mech_sprite("px-01", damaged=True)
        assert sprite is not None
        assert sprite.get_at((16, 16)) == MADCAT_DAM_COLOR

    def test_damaged_hits_x_stem_without_dam_file(self, tmp_path: Path) -> None:
        """包内只有 X 变体：damaged=True 回退候选第二个变体词命中 MadCatX。"""
        root = tmp_path / "mc2-x-only"
        write_tga(root / "Data" / "TGL" / "128" / "MadCat.tga", MADCAT_COLOR)
        write_tga(root / "Data" / "TGL" / "128" / "MadCatX.tga", MADCAT_X_COLOR)
        pack = MC2AssetPack.load(root)
        sprite = pack.resolve_mech_sprite("px-01", damaged=True)
        assert sprite is not None
        assert sprite.get_at((16, 16)) == MADCAT_X_COLOR

    def test_damaged_falls_back_to_base_when_no_variant(self, pack_root: Path) -> None:
        """无变体文件：damaged=True 与 damaged=False 返回同一基名精灵（回退语义）。"""
        pack = MC2AssetPack.load(pack_root)
        damaged = pack.resolve_mech_sprite("px-01", damaged=True)
        base = pack.resolve_mech_sprite("px-01")
        assert damaged is not None and damaged is base

    def test_damaged_cache_isolated_from_base(self, damaged_root: Path) -> None:
        """(kind, damaged) 分键缓存：先查 damaged 再查基名不串结果。"""
        pack = MC2AssetPack.load(damaged_root)
        damaged = pack.resolve_mech_sprite("px-01", damaged=True)
        base = pack.resolve_mech_sprite("px-01")
        assert damaged is not None and base is not None
        assert damaged is pack.resolve_mech_sprite("px-01", damaged=True)
        assert base is not damaged

    def test_damaged_unknown_kind_returns_none(self, pack_root: Path) -> None:
        pack = MC2AssetPack.load(pack_root)
        assert pack.resolve_mech_sprite("zz-09", damaged=True) is None

    def test_scaled_damaged_sprite_resized_and_cached(self, damaged_root: Path) -> None:
        """scaled 带 damaged 维度：(kind, damaged, size) 三元组缓存互不污染。"""
        pack = MC2AssetPack.load(damaged_root)
        damaged = pack.scaled_mech_sprite("px-01", 16, damaged=True)
        assert damaged is not None
        assert damaged.get_size() == (16, 16)
        assert damaged.get_at((8, 8)) == MADCAT_DAM_COLOR
        assert pack.scaled_mech_sprite("px-01", 16, damaged=True) is damaged
        base = pack.scaled_mech_sprite("px-01", 16)
        assert base is not None and base is not damaged


class TestUnitSprite:
    """P2 单位谱系精灵：resolve_unit_sprite（vehicle→storm / turret→.ase 外观名恒未命中）。

    官方 TankG.fit AppearanceName='storm' → Data/TGL/128/Storm.tga 实测命中（受损变体
    StormDam 同名派生）；作战炮塔官方仅 .ase 动画外观、无静态 TGA → 程序化回退。
    """

    @pytest.fixture()
    def unit_root(self, tmp_path: Path) -> Path:
        root = tmp_path / "mc2-units"
        tgl = root / "Data" / "TGL" / "128"
        write_tga(tgl / "Storm.tga", STORM_COLOR)
        write_tga(tgl / "StormDam.tga", STORM_DAM_COLOR)
        return root

    def test_vehicle_hits_storm_stem(self, unit_root: Path) -> None:
        pack = MC2AssetPack.load(unit_root)
        sprite = pack.resolve_unit_sprite("vehicle")
        assert sprite is not None
        assert sprite.get_at((16, 16)) == STORM_COLOR

    def test_vehicle_damaged_hits_dam_variant(self, unit_root: Path) -> None:
        """damaged=True 派生 storm+dam → 命中官方 StormDam.tga（载具受损变体）。"""
        pack = MC2AssetPack.load(unit_root)
        sprite = pack.resolve_unit_sprite("vehicle", damaged=True)
        assert sprite is not None
        assert sprite.get_at((16, 16)) == STORM_DAM_COLOR

    def test_vehicle_damaged_falls_back_to_base_without_variant(self, tmp_path: Path) -> None:
        root = tmp_path / "mc2-vehicle-only"
        write_tga(root / "Data" / "TGL" / "128" / "Storm.tga", STORM_COLOR)
        pack = MC2AssetPack.load(root)
        damaged = pack.resolve_unit_sprite("vehicle", damaged=True)
        assert damaged is not None and damaged is pack.resolve_unit_sprite("vehicle")

    def test_turret_stem_misses_static_index(self, unit_root: Path) -> None:
        """官方作战炮塔无静态 TGA：turret stem 恒未命中 → None（渲染层程序化回退）。"""
        pack = MC2AssetPack.load(unit_root)
        assert pack.resolve_unit_sprite("turret") is None
        assert pack.resolve_unit_sprite("turret", damaged=True) is None

    def test_unknown_kind_passthrough_miss_returns_none(self, unit_root: Path) -> None:
        pack = MC2AssetPack.load(unit_root)
        assert pack.resolve_unit_sprite("infantry") is None
        assert pack.resolve_unit_sprite("") is None

    def test_scaled_unit_sprite_resized_and_cached(self, unit_root: Path) -> None:
        pack = MC2AssetPack.load(unit_root)
        scaled = pack.scaled_unit_sprite("vehicle", 16)
        assert scaled is not None
        assert scaled.get_size() == (16, 16)
        assert scaled.get_at((8, 8)) == STORM_COLOR
        assert pack.scaled_unit_sprite("vehicle", 16) is scaled
        damaged = pack.scaled_unit_sprite("vehicle", 16, damaged=True)
        assert damaged is not None and damaged is not scaled


class TestDefaultPackRoot:
    """默认根探测三级：env 覆盖 → ~/.pymc2/mc2-assets（存在才用）→ None。"""

    def test_env_var_wins_over_cache_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_root = tmp_path / "env-root"
        env_root.mkdir()
        home = tmp_path / "home"
        (home / ".pymc2" / "mc2-assets").mkdir(parents=True)
        monkeypatch.setenv(ASSETS_ENV_VAR, str(env_root))
        monkeypatch.setenv("HOME", str(home))
        assert default_pack_root() == env_root

    def test_home_cache_dir_used_when_env_unset(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ASSETS_ENV_VAR, raising=False)
        home = tmp_path / "home"
        cache = home / ".pymc2" / "mc2-assets"
        cache.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home))
        assert default_pack_root() == cache

    def test_env_var_returned_even_if_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """env 为显式覆盖：目录尚不存在也原样返回（失败由 load 降级提示呈现）。"""
        monkeypatch.setenv(ASSETS_ENV_VAR, str(tmp_path / "not-yet-created"))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        assert default_pack_root() == tmp_path / "not-yet-created"

    def test_returns_none_when_no_candidate(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ASSETS_ENV_VAR, raising=False)
        monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
        assert default_pack_root() is None


def ground_color(pack: MC2AssetPack, kind: str, seed: int) -> tuple[int, int, int, int]:
    surface = pack.resolve_ground(kind, seed)
    assert surface is not None
    return surface.get_at((16, 16))


class TestGroundResolve:
    """resolve_ground：确定性选张 + 道路序列帧号排序 + 城镇地形不暴露。"""

    def test_load_counts_ground_overlay_and_sprites(self, terrain_root: Path) -> None:
        pack = MC2AssetPack.load(terrain_root)
        assert pack is not None
        assert pack.has_ground_assets
        # 5 ground + 6 overlay + 5 env = 16
        assert pack.tga_count == 16

    def test_grass_pool_only_detail_series(self, terrain_root: Path) -> None:
        """grass 池只收 Detail 系列：Cement（城镇地形）与 undefined 不入池。"""
        pack = MC2AssetPack.load(terrain_root)
        assert pack is not None
        assert len(pack.ground_pool("grass")) == 3

    def test_resolve_ground_deterministic_and_cycling(self, terrain_root: Path) -> None:
        """同 seed 同结果（同一 Surface 对象）；seed 取模循环选张。"""
        pack = MC2AssetPack.load(terrain_root)
        assert pack is not None
        assert pack.resolve_ground("grass", 7) is pack.resolve_ground("grass", 7)
        for seed in range(6):
            assert ground_color(pack, "grass", seed) == DETAIL_COLORS[seed % 3]

    def test_road_sequence_sorted_by_frame_number(self, terrain_root: Path) -> None:
        """road 池按帧号排序（fixture 乱序写入）：seed k 命中第 k 帧。"""
        pack = MC2AssetPack.load(terrain_root)
        assert pack is not None
        assert len(pack.ground_pool("road")) == 6
        for seed in range(6):
            assert ground_color(pack, "road", seed) == ROAD_FRAME_COLORS[seed]

    def test_water_single_tile(self, terrain_root: Path) -> None:
        pack = MC2AssetPack.load(terrain_root)
        assert pack is not None
        assert ground_color(pack, "water", 0) == (30, 60, 90, 255)

    def test_unknown_or_town_kind_returns_none(self, terrain_root: Path) -> None:
        """未登记 kind 与 Cement 城镇地形（V2+）均回退 None。"""
        pack = MC2AssetPack.load(terrain_root)
        assert pack is not None
        assert pack.resolve_ground("cement") is None
        assert pack.resolve_ground("lava") is None

    def test_ground_tiles_forced_opaque(self, tmp_path: Path) -> None:
        """地面纹理 alpha 是高光通道：fixture 写 alpha=48，加载后强制 255。"""
        root = tmp_path / "glossy"
        path = root / "Data" / "Textures" / "64" / "Detail_10000.tga"
        path.parent.mkdir(parents=True)
        Image.new("RGBA", SIZE_PX, (150, 100, 60, 48)).save(path)
        pack = MC2AssetPack.load(root)
        assert pack is not None
        surface = pack.resolve_ground("grass", 0)
        assert surface is not None
        assert surface.get_at((0, 0)) == (150, 100, 60, 255)

    def test_overlays_keep_native_alpha_mask(self, tmp_path: Path) -> None:
        """道路 overlay alpha 是形状蒙版：不透明区保持 alpha、透明区保持 0。"""
        root = tmp_path / "masked"
        write_tga_masked(root / "Data" / "Textures" / "64Overlays" / "2lanedirt0000.TGA", (140, 120, 90, 200), 16)
        pack = MC2AssetPack.load(root)
        assert pack is not None
        surface = pack.resolve_ground("road", 0)
        assert surface is not None
        assert surface.get_at((4, 16)) == (140, 120, 90, 200)  # 路体：原生 alpha 保留
        assert surface.get_at((24, 16))[3] == 0  # 路体外：蒙版透明

    def test_ground_only_pack_loads_and_degrades_mech(self, tmp_path: Path) -> None:
        """只有地面资产的 pack 也算有效加载：机甲解析回退 None，地面可用。"""
        root = tmp_path / "ground-only"
        write_tga(root / "Data" / "Textures" / "64" / "Detail_10000.tga", DETAIL_COLORS[0])
        pack = MC2AssetPack.load(root)
        assert pack is not None
        assert pack.resolve_mech_sprite("px-01") is None
        assert pack.resolve_ground("grass", 0) is not None

    def test_empty_ground_index_returns_none(self, pack_root: Path) -> None:
        """无地面资产的 pack：ground 解析 None（渲染层保持程序化色块）。"""
        pack = MC2AssetPack.load(pack_root)
        assert pack is not None
        assert not pack.has_ground_assets
        assert pack.resolve_ground("grass") is None


class TestScaledGroundTile:
    def test_scaled_and_cached(self, terrain_root: Path) -> None:
        pack = MC2AssetPack.load(terrain_root)
        assert pack is not None
        first = pack.scaled_ground_tile("grass", 0, 16)
        assert first is not None
        assert first.get_size() == (16, 16)
        assert pack.scaled_ground_tile("grass", 0, 16) is first

    def test_seed_normalized_before_cache(self, terrain_root: Path) -> None:
        """seed 取模归一化后进缓存：seed 3 与 seed 0 命中同一张（池长 3）。"""
        pack = MC2AssetPack.load(terrain_root)
        assert pack is not None
        assert pack.scaled_ground_tile("grass", 3, 16) is pack.scaled_ground_tile("grass", 0, 16)

    def test_miss_returns_none(self, pack_root: Path) -> None:
        pack = MC2AssetPack.load(pack_root)
        assert pack is not None
        assert pack.scaled_ground_tile("grass", 0, 16) is None


class TestResolveEnvironment:
    """resolve_environment：别名直译 + _x/_dam 变体派生 + 包含匹配。"""

    def test_tree_alias_hits_pine_palm(self, terrain_root: Path) -> None:
        pack = MC2AssetPack.load(terrain_root)
        assert pack is not None
        sprite = pack.resolve_environment("tree")
        assert sprite is not None
        assert sprite.get_at((16, 16)) == TREE_COLOR

    def test_x_suffix_hits_destroyed_variant(self, terrain_root: Path) -> None:
        """tree_x → a_pine&palmx；jungle_tree_x 在基名缺失时仍命中 X 变体。"""
        pack = MC2AssetPack.load(terrain_root)
        assert pack is not None
        tree_x = pack.resolve_environment("tree_x")
        assert tree_x is not None
        assert tree_x.get_at((16, 16)) == TREE_X_COLOR
        jungle_x = pack.resolve_environment("jungle_tree_x")
        assert jungle_x is not None
        assert jungle_x.get_at((16, 16)) == JUNGLE_X_COLOR

    def test_greenhouse_maps_to_damaged_variant(self, terrain_root: Path) -> None:
        """A_GreenHouse 官方缺卡片：greenhouse 与 greenhouse_dam 都指向受损变体。"""
        pack = MC2AssetPack.load(terrain_root)
        assert pack is not None
        for kind in ("greenhouse", "greenhouse_dam"):
            sprite = pack.resolve_environment(kind)
            assert sprite is not None
            assert sprite.get_at((16, 16)) == HOUSE_DAM_COLOR

    def test_mech_wreck_alias(self, terrain_root: Path) -> None:
        pack = MC2AssetPack.load(terrain_root)
        assert pack is not None
        sprite = pack.resolve_environment("mech_wreck")
        assert sprite is not None
        assert sprite.get_at((16, 16)) == WRECK_COLOR

    def test_case_insensitive_and_unknown(self, terrain_root: Path) -> None:
        pack = MC2AssetPack.load(terrain_root)
        assert pack is not None
        assert pack.resolve_environment("TREE") is not None
        assert pack.resolve_environment("zzz") is None
        assert pack.resolve_environment("  ") is None

    def test_scaled_environment_sprite_cached(self, terrain_root: Path) -> None:
        pack = MC2AssetPack.load(terrain_root)
        assert pack is not None
        first = pack.scaled_environment_sprite("tree", 16)
        assert first is not None
        assert first.get_size() == (16, 16)
        assert pack.scaled_environment_sprite("tree", 16) is first
        assert pack.scaled_environment_sprite("zzz", 16) is None
