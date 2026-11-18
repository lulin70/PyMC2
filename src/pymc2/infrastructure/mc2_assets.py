"""本地 MechCommander 2 资产加载管线（可选注入，未加载时全程序化绘制）。

许可红线（微软 2006 Shared Source 许可，务必遵守）：
- MC2 原版资产**仅限用户本机加载**用于学习与开发验证；
- **不打包进 PyMC2 仓库、不再分发、不用于任何商业用途**；
- 仓库内只允许测试用自造 TGA fixture（PIL 现场生成），任何真实 MC2 资产零入库。

资产格式（已对照官方 Shared Source 勘明）：
- ``Data/TGL/256/*.tga`` / ``Data/TGL/128/*.tga``：对象精灵（约 2300 张，256 优先取高清版）；
- ``Data/Art/MCL_MC_{MadCat,Thor,UrbanMech,Jaegermech}_{A,B}.tga``：机甲立绘；
- ``Data/Objects/*.fit``：机甲/物体定义（本模块不消费）。

匹配链：索引键为小写文件名 stem；resolve 按精确 stem → stem 包含关系
（"awesome" 命中 "awesome1"、"madcat" 命中 "mcl_mc_madcat_a"，大小写不敏感）→ None
（渲染层回退程序化色块）。root 不存在 / 无 Data 目录 / 扫描零命中 → ``load`` 返回 None，
调用方优雅降级，渲染输出与未加载状态完全一致。
"""

from __future__ import annotations

import re
from pathlib import Path

import pygame
from PIL import Image

# 扫描目录（相对 Data/）：256 精灵优先于 128（同 stem 保留先命中者），Art 立绘兜底。
SPRITE_DIR_256 = Path("TGL/256")
SPRITE_DIR_128 = Path("TGL/128")
ART_DIR = Path("Art")

# 项目机甲模板 → MC2 精灵名（小写 stem，可调）：渲染层以 mech.id 作为 chassis_kind 查询。
# 未登记的 id 先剥尾部编号回退模板前缀（px-03 → px），仍无命中则按原样进匹配链。
CHASSIS_SPRITE_NAMES: dict[str, str] = {
    "px-01": "madcat",
    "px-02": "thor",
    "px": "madcat",  # M5 hire 产出的 px-03+ 统一回退 MadCat
    "ex": "urbanmech",
}

_ID_SUFFIX_RE = re.compile(r"-?\d+$")


def _sprite_name_candidates(chassis_kind: str) -> tuple[str, ...]:
    """chassis_kind → MC2 精灵名候选：映射表精确 → 剥 id 后缀回退 → 原样透传。"""
    kind = chassis_kind.strip().lower()
    name = CHASSIS_SPRITE_NAMES.get(kind)
    if name is not None:
        return (name,)
    stem = _ID_SUFFIX_RE.sub("", kind)
    name = CHASSIS_SPRITE_NAMES.get(stem)
    if name is not None:
        return (name,)
    return (kind,)  # 自造资产/扩展类型：按原样参与精确与包含匹配


def _load_surface(path: Path) -> pygame.Surface | None:
    """PIL 读 TGA → pygame RGBA Surface（有显示面时 convert_alpha 提速）；损坏文件跳过。"""
    try:
        with Image.open(path) as image:
            rgba = image.convert("RGBA")
    except Exception:  # noqa: BLE001 - 单张损坏 TGA 跳过，不阻断整包加载
        return None
    surface = pygame.image.frombuffer(rgba.tobytes(), rgba.size, "RGBA")
    if pygame.display.get_surface() is not None:
        surface = surface.convert_alpha()
    return surface


class MC2AssetPack:
    """本地 MC2 资产索引：小写文件名 stem → pygame.Surface（含解析/缩放缓存）。"""

    def __init__(self, root: Path, sprites: dict[str, pygame.Surface]) -> None:
        self.root = root
        self._sprites = sprites
        self.tga_count = len(sprites)  # pack 统计：成功索引的 TGA 数量
        self._resolve_cache: dict[str, pygame.Surface | None] = {}
        self._scaled_cache: dict[tuple[str, int], pygame.Surface | None] = {}

    @classmethod
    def load(cls, root: Path | str) -> MC2AssetPack | None:
        """扫描本地 MC2 checkout；root 无效 / 无 Data 目录 / 零命中 → None（优雅降级）。"""
        base = Path(root)
        data = base / "Data"
        if not data.is_dir():
            return None
        sprites: dict[str, pygame.Surface] = {}
        for rel_dir in (SPRITE_DIR_256, SPRITE_DIR_128, ART_DIR):
            directory = data / rel_dir
            if not directory.is_dir():
                continue
            for path in sorted(directory.iterdir()):
                if not path.is_file() or path.suffix.lower() != ".tga":
                    continue
                stem = path.stem.lower()
                if stem in sprites:
                    continue  # 256 优先：同 stem 高清版已收录
                surface = _load_surface(path)
                if surface is not None:
                    sprites[stem] = surface
        if not sprites:
            return None
        return cls(base, sprites)

    def resolve_mech_sprite(self, chassis_kind: str) -> pygame.Surface | None:
        """机甲模板/名 → 精灵：精确 stem → 包含匹配（大小写不敏感）→ None；结果缓存。"""
        key = chassis_kind.strip().lower()
        if not key:
            return None  # 空 kind：包含匹配会退化（"" 命中一切），直接回退程序化
        if key in self._resolve_cache:
            return self._resolve_cache[key]
        sprite = self._lookup(_sprite_name_candidates(key))
        self._resolve_cache[key] = sprite
        return sprite

    def scaled_mech_sprite(self, chassis_kind: str, size_px: int) -> pygame.Surface | None:
        """解析并缩放到目标尺寸（逐 (kind, size) 缓存，避免逐帧 smoothscale）。"""
        cache_key = (chassis_kind.strip().lower(), size_px)
        if cache_key in self._scaled_cache:
            return self._scaled_cache[cache_key]
        sprite = self.resolve_mech_sprite(chassis_kind)
        scaled: pygame.Surface | None = sprite
        if sprite is not None and sprite.get_size() != (size_px, size_px):
            scaled = pygame.transform.smoothscale(sprite, (size_px, size_px))
        self._scaled_cache[cache_key] = scaled
        return scaled

    def _lookup(self, names: tuple[str, ...]) -> pygame.Surface | None:
        """按候选名匹配索引：先精确，再包含（最短 stem 优先，结果确定）。"""
        for name in names:
            surface = self._sprites.get(name)
            if surface is not None:
                return surface
        for name in names:
            matches = [stem for stem in self._sprites if name in stem]
            if matches:
                return self._sprites[min(matches, key=lambda s: (len(s), s))]
        return None
