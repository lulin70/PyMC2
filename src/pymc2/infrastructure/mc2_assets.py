"""本地 MechCommander 2 资产加载管线（可选注入，未加载时全程序化绘制）。

许可红线（微软 2006 Shared Source 许可，务必遵守）：
- MC2 原版资产**仅限用户本机加载**用于学习与开发验证；
- **不打包进 PyMC2 仓库、不再分发、不用于任何商业用途**；
- 仓库内只允许测试用自造 TGA fixture（PIL 现场生成），任何真实 MC2 资产零入库。

资产格式（已对照官方 Shared Source 勘明）：
- ``Data/TGL/256/*.tga`` / ``Data/TGL/128/*.tga``：对象精灵（约 2300 张，256 优先取高清版），
  含机甲精灵与 ``A_*`` 环境物件精灵（真 alpha 抠图）；
- ``Data/Art/MCL_MC_{MadCat,Thor,UrbanMech,Jaegermech}_{A,B}.tga``：机甲立绘；
- ``Data/Art/MCUI_*.tga``：UI 图集（Wave V3）——底部面板框架部件的 UV 取块源；
- ``Data/Art/buttonlayout1280.fit``：in-game UI 布局定义（FITini 文本，Wave V3）——
  ``[Statics]`` 部件定位（画布坐标 + 图集 UV 取块）与 ``[ResourcePoints]/[HelpText]``
  文本槽位；只解析面板渲染消费的段落，按钮/图标段落不解析（无点击交互语义）；
- ``Data/Textures/64/*.tga``：地面基础纹理（Wave V1）——**alpha 通道是高光/细节
  通道而非透明度**（Water_ocean 全 0、Detail 系列约 48，均非抠图），加载时强制
  置不透明，否则按 alpha 合成会近全黑；
- ``Data/Textures/Defaults/*.tga``：官方缺省水面族（V11 扫描扩展，同 64 的 opaque
  语义）——Water_{Ocean,Lake,Desert,Stormy} 入 water 族池；Mine/Wake/feet 等杂项
  不命中任何族仅入索引不进池；
- ``Data/Textures/Random_Maps/*.jpg``：随机地图生物群系整图（V11 地表观感主来源，
  1536² JPEG）——按固定 4×4 网格确定性派生 128px tile 帧进地表族池（草=Forest、
  泥=Desert、岩=Volcanic/Lunar/Tundra），不落盘派生产物；
- ``Data/Textures/64Overlays/*.tga``：道路/覆盖瓦片序列（Wave V1）——alpha 是
  形状蒙版（路体外 ≤140 软边、路体外 0），按原生 alpha 合成；
- ``Data/Objects/*.fit``：机甲/物体定义（FITini 文本，由 ``fit_parser`` 独立解析，
  本模块不自动扫描——项目机甲 id 体系与 .fit Name 不同名）。

匹配链：索引键为小写文件名 stem；resolve 按精确 stem → stem 包含关系
（"awesome" 命中 "awesome1"、"madcat" 命中 "mcl_mc_madcat_a"，大小写不敏感）→ None
（渲染层回退程序化色块）。root 不存在 / 无 Data 目录 / 扫描零命中 → ``load`` 返回 None，
调用方优雅降级，渲染输出与未加载状态完全一致。

Wave V1/V11 地形合成接口：
- ``ground_pool(terrain_kind)``：V11 起按族分类（grass/dirt/rock/water/cement，
  ``GROUND_FAMILY_KEYWORDS`` 命名规则驱动）——grass=Random_Maps Forest 派生帧、
  dirt=Desert 派生帧、rock=Volcanic/Lunar/Tundra 派生帧、water=Water 系列、
  road=64Overlays 的 2lanedirt 序列（按帧号排序）；缺族回退 detail 系列（旧缓存
  行为兼容），Cement 城镇地形不对外暴露；``resolve_ground(terrain_kind, seed)`` 按
  seed 取模选帧（同 seed 同结果，帧内确定性）；Water 仅留接口不接渲染；
- ``resolve_environment(kind)``：``A_*`` 物件精灵，kind 经别名表（tree/jungle_tree/
  greenhouse/mech_wreck 等）→ ``_x``/``_dam`` 后缀派生 Dam/X 摧毁变体 → 包含匹配；
- 机甲 ``resolve_mech_sprite`` 匹配链零改动；damaged=True 时派生 dam/x 变体 stem
  （Wave V2，未命中回退基名——官方真资产无机甲受损静态 TGA）。

默认根目录（用户反馈：去掉路径门槛）：``default_pack_root()`` 按
env ``PYMC2_MC2_ASSETS`` → ``~/.pymc2/mc2-assets`` 顺序探测候选（其余不做），
存在且 ``load`` 非 None 即用。缓存目录若为 git 仓库（含 .git）无碍——load 只扫
Data/TGL、Data/Art、Data/Textures 五个子目录，仓库根的 .git 等元数据不进入扫描。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import pygame
from PIL import Image

from pymc2.infrastructure.ase_parser import AseParseError, parse_ase
from pymc2.infrastructure.mesh_render import HEADING_STEPS, render_heading_frames

# 扫描目录（相对 Data/）：256 精灵优先于 128（同 stem 保留先命中者），Art 立绘兜底；
# Textures/64 地面基础纹理 + Textures/Defaults 缺省水面族 + Textures/Random_Maps
# 生物群系整图（V11 地表族池）+ Textures/64Overlays 道路覆盖序列（Wave V1 地形合成）。
SPRITE_DIR_256 = Path("TGL/256")
SPRITE_DIR_128 = Path("TGL/128")
ART_DIR = Path("Art")
GROUND_DIR = Path("Textures/64")
DEFAULTS_DIR = Path("Textures/Defaults")
BIOME_DIR = Path("Textures/Random_Maps")
OVERLAY_DIR = Path("Textures/64Overlays")

# 默认资产根探测：环境变量优先，其次本机缓存目录（稀疏 checkout 由用户自行放置）。
ASSETS_ENV_VAR = "PYMC2_MC2_ASSETS"
CACHE_ASSETS_SUBPATH = Path(".pymc2") / "mc2-assets"

# 项目机甲模板 → MC2 精灵名（小写 stem，可调）：渲染层以 mech.id 作为 chassis_kind 查询。
# 未登记的 id 先剥尾部编号回退模板前缀（px-03 → px），仍无命中则按原样进匹配链。
CHASSIS_SPRITE_NAMES: dict[str, str] = {
    "px-01": "madcat",
    "px-02": "thor",
    "px": "madcat",  # M5 hire 产出的 px-03+ 统一回退 MadCat
    "ex": "urbanmech",
}

_ID_SUFFIX_RE = re.compile(r"-?\d+$")

# V11 地表纹理族分类（关键词首命中归族，来源勘测见 docs/V11_GROUND_POOL_DESIGN_20261121.md
# §2/§4）：Random_Maps 生物群系整图 + Defaults 水面 + 64 的 Cement/Water 按官方命名族
# 写死；Cement 城镇地形族分类后仍不对外暴露（ground_pool 恒空池，V2+ 再接）。
GROUND_FAMILY_KEYWORDS: tuple[tuple[str, str], ...] = (
    # water 首位：Defaults/Water_Desert.tga（官方沙漠水面）不得被 dirt 的 desert 误收
    ("water", "water"),
    ("grass", "forest"),
    ("dirt", "desert"),
    ("rock", "volcanic"),
    ("rock", "lunar"),
    ("rock", "tundra"),
    ("cement", "cement"),
)
# 旧缓存回退族：Detail_10000~40000（官方 detail 噪声叠加层，V1 起 grass 池成员）。
# startswith 精确前缀，"default_detail" 等含 detail 子串的杂项不误入。
LEGACY_GROUND_PREFIX = "detail"
ROAD_SEQUENCE_PREFIX = "2lanedirt"
GROUND_TERRAIN_KINDS = ("grass", "dirt", "rock", "road", "water")

# 生物群系整图 → 地表 tile 帧派生参数（确定性，无随机数）：4×4 网格均匀取样，
# 帧边长 ≤128px（1536² 整图 → 16 帧 128px；小图按比例缩格）。
BIOME_FRAME_GRID = 4
BIOME_FRAME_MAX_PX = 128

# 道路覆盖序列 stem 解析：小写 stem = 前缀 + 帧号（如 2lanedirt0003）。
_OVERLAY_STEM_RE = re.compile(r"^(.+?)(\d+)$")

# 环境物件 kind → 精灵 stem 别名（小写）。A_GreenHouse 官方缺卡片，greenhouse 直指
# 受损变体 A_GreenHouseDam；X/Dam 摧毁变体本波只暴露接口不接渲染（V2 做）。
ENVIRONMENT_ALIASES: dict[str, str] = {
    "tree": "a_pine&palm",
    "jungle_tree": "a_jungletreea",
    "jungle_tree_b": "a_jungletreeb",
    "dead_tree": "a_deadtreesl1",
    "greenhouse": "a_greenhousedam",
    "mech_wreck": "a_mechdam",
}
# 摧毁/受损变体后缀：kind 以 _x/_dam 结尾时派生「基名+后缀」与「基名本身」两个候选
# （a_pine&palm + x → a_pine&palmx；a_greenhouse + dam → a_greenhousedam）。
ENVIRONMENT_VARIANT_SUFFIXES = ("_x", "_dam")

# Wave V2 机甲受损变体：damaged=True 时按候选名直拼变体词派生（madcat → madcatdam /
# madcatx，对齐 A_* 物件 X/Dam 命名约定）。远端官方资产无机甲受损静态 TGA（受损外观
# 走 .ase 动画，见 docs/V2_DESIGN_20261119.md §1.1），派生命中依赖 fixture/未来资产，
# 未命中回退基名候选链（渲染输出与现状一致）。
MECH_DAMAGED_VARIANT_WORDS = ("dam", "x")

# P2 单位谱系（docs/P2_DESIGN_20261121.md §5）：kind → MC2 精灵 stem（小写）。
# vehicle 实测命中 Data/TGL/128/Storm.tga（TankG.fit ObjectTypeNum=3、
# AppearanceName='storm'，受损变体 StormDam 同名派生）；turret 指向官方作战炮塔
# 外观名（仅 .ase 动画外观、无静态 TGA → 恒未命中 → 程序化回退，P4 .ase 接入后自然命中）。
UNIT_SPRITE_NAMES: dict[str, str] = {
    "vehicle": "storm",
    "turret": "mediumautocannonturret",
}

# P4 机甲 8 朝向帧：Data/TGL/<Stem>.ase（3DSMAX ASCII 场景，文件名与 AppearanceName/
# 精灵 stem 同名）经 mesh_render 预渲染 8 朝向（0=北顺时针 45°/步）。纹理取索引内
# ``stem+"rgb"`` 静态卡（如 MadcatRGB.tga）按面 UV 采样着色；帧原生画布 128px
# （对齐官方卡分辨率），渲染层按格子尺寸 smoothscale。
ASE_DIR = Path("TGL")
ASE_FRAME_CANVAS_PX = 128
MECH_ASE_TEXTURE_SUFFIX = "rgb"

# Wave V3 UI 面板：buttonlayout1280.fit（相对 Data/ 的布局定义路径）与解析参数。
# 官方布局设计画布 1024×768（[HiresOffsets] 在 1280 宽屏下 x+128 居中、底边锚定）。
BUTTON_LAYOUT_FIT_RELPATH = Path("Art/buttonlayout1280.fit")
_BUTTON_SECTION_RE = re.compile(r"^\[(\w+)\]")
_BUTTON_ENTRY_RE = re.compile(r"^(?:l|st)\s+(\w+)\s*=\s*(.*)$")
_BUTTON_STATIC_PREFIX = "Static"
_DROPTEAM_FIRST = "Static10"  # 首个 dropteam palette（选中机甲槽位锚点）
_DROPTEAM_SECOND = "Static11"  # 相邻槽位（推算 stride）


@dataclass(frozen=True)
class PanelPiece:
    """面板部件：画布坐标 dest（可负，xtra 补边越出基础画布）+ 图集 UV 裁切面。"""

    name: str
    dest: tuple[int, int]
    surface: pygame.Surface


@dataclass(frozen=True)
class ButtonLayout:
    """buttonlayout .fit 解析结果：面板部件集 + 文本槽位锚点（画布坐标）。

    pieces 空元组视为解析失败（渲染层回退现状 HUD）；canvas_origin/size 为部件
    bbox（预合成画布）；dropteam_origin 为首个 palette 槽位、stride 为相邻槽位
    x 间距（缺锚点时槽位为 None，渲染层逐槽跳过）。
    """

    pieces: tuple[PanelPiece, ...]
    canvas_origin: tuple[int, int]
    canvas_size: tuple[int, int]
    rp_text_pos: tuple[int, int] | None
    help_text_pos: tuple[int, int] | None
    dropteam_origin: tuple[int, int] | None
    dropteam_stride: int
    compalette_top_pos: tuple[int, int] | None


def parse_fit_sections(text: str) -> dict[str, dict[str, int | str]]:
    """FITini 文本 → 扁平段字典（段名 → 键值表）；只取面板消费的 l/st 两类字段。

    ``//`` 注释剥离、空行跳过；值尽量转 int（含 0x 十六进制），失败保留去引号
    字符串；非 l/st 前缀行（b 布尔等）忽略。任何输入都不抛错（坏行逐条跳过）。
    """
    sections: dict[str, dict[str, int | str]] = {}
    current: dict[str, int | str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.split("//", 1)[0].strip()
        if not line:
            continue
        section_match = _BUTTON_SECTION_RE.match(line)
        if section_match is not None:
            current = {}
            sections[section_match.group(1)] = current
            continue
        if current is None:
            continue
        entry_match = _BUTTON_ENTRY_RE.match(line)
        if entry_match is None:
            continue
        key, raw_value = entry_match.group(1), entry_match.group(2).strip()
        try:
            current[key] = int(raw_value, 0)
        except ValueError:
            current[key] = raw_value.strip('"')
    return sections


def _static_sections(sections: dict[str, dict[str, int | str]]) -> list[dict[str, int | str]]:
    """按 Static0..N 连续编号收集静态部件段（官方布局编号无空洞，遇缺即止）。"""
    result: list[dict[str, int | str]] = []
    index = 0
    while True:
        section = sections.get(f"{_BUTTON_STATIC_PREFIX}{index}")
        if section is None:
            return result
        result.append(section)
        index += 1


def _section_pos(section: dict[str, int | str], x_key: str, y_key: str) -> tuple[int, int] | None:
    """段内两个坐标键 → 整数二元组；缺任一键返回 None。"""
    x_value, y_value = section.get(x_key), section.get(y_key)
    if isinstance(x_value, int) and isinstance(y_value, int):
        return (x_value, y_value)
    return None


def _build_button_layout(
    sections: dict[str, dict[str, int | str]],
    sheets: dict[str, pygame.Surface],
) -> ButtonLayout | None:
    """布局段字典 + 图集索引 → ButtonLayout；零部件不可解析 → None（渲染层回退）。

    部件级容错：零尺寸/缺 FileName/图集未命中/UV 越界逐个跳过；FileName 归一化
    （剥 ``.tga`` 后缀 + 小写）对齐官方布局里的大小写混排引用。"""
    pieces: list[PanelPiece] = []
    for index, section in enumerate(_static_sections(sections)):
        width, height = section.get("Width"), section.get("Height")
        file_name = section.get("FileName")
        dest = _section_pos(section, "XLocation", "YLocation")
        if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
            continue
        if not isinstance(file_name, str) or dest is None:
            continue
        stem = file_name.lower().removesuffix(".tga")
        sheet = sheets.get(stem)
        if sheet is None:
            continue
        u, v = section.get("UNormal", 0), section.get("VNormal", 0)
        u_width = section.get("UWidth", width)
        v_height = section.get("VHeight", height)
        if not isinstance(u, int) or not isinstance(v, int):
            continue
        if not isinstance(u_width, int) or not isinstance(v_height, int):
            continue
        area = pygame.Rect(u, v, u_width, v_height).clip(pygame.Rect(0, 0, sheet.get_width(), sheet.get_height()))
        if area.width <= 0 or area.height <= 0:
            continue
        pieces.append(PanelPiece(name=f"{_BUTTON_STATIC_PREFIX}{index}", dest=dest, surface=sheet.subsurface(area)))
    if not pieces:
        return None
    xs = [piece.dest[0] for piece in pieces]
    ys = [piece.dest[1] for piece in pieces]
    min_x, min_y = min(xs), min(ys)
    canvas_size = (
        max(x + piece.surface.get_width() for x, piece in zip(xs, pieces, strict=True)) - min_x,
        max(y + piece.surface.get_height() for y, piece in zip(ys, pieces, strict=True)) - min_y,
    )
    dropteam_origin = _section_pos(sections.get(_DROPTEAM_FIRST, {}), "XLocation", "YLocation")
    stride = 160
    second = _section_pos(sections.get(_DROPTEAM_SECOND, {}), "XLocation", "YLocation")
    if dropteam_origin is not None and second is not None:
        stride = second[0] - dropteam_origin[0]
    return ButtonLayout(
        pieces=tuple(pieces),
        canvas_origin=(min_x, min_y),
        canvas_size=canvas_size,
        rp_text_pos=_section_pos(sections.get("ResourcePoints", {}), "RPTextLeft", "RPTextTop"),
        help_text_pos=_section_pos(sections.get("HelpText", {}), "HelpTextLeft", "HelpTextBottom"),
        dropteam_origin=dropteam_origin,
        dropteam_stride=stride,
        compalette_top_pos=_section_pos(sections.get("Static8", {}), "XLocation", "YLocation"),
    )


def default_pack_root() -> Path | None:
    """默认 MC2 资产根目录探测：env ``PYMC2_MC2_ASSETS`` → ``~/.pymc2/mc2-assets`` → None。

    仅做候选探测、不做加载：调用方以 ``MC2AssetPack.load`` 验证（None 即降级程序化绘制）。
    env 值为显式覆盖（即使目录尚不存在也原样返回，失败由 load 的降级提示呈现）；
    缓存目录须实际存在。
    """
    env_root = os.environ.get(ASSETS_ENV_VAR)
    if env_root:
        return Path(env_root)
    cache_root = Path.home() / CACHE_ASSETS_SUBPATH
    if cache_root.is_dir():
        return cache_root
    return None


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


def ground_family(stem: str) -> str | None:
    """小写 stem → 地表纹理族（V11）：关键词表首命中 → detail 旧缓存回退族 → None。

    None（undefined 品红占位、Mine/Wake/feet/default_detail 杂项）不入任何地表池。"""
    key = stem.strip().lower()
    for family, keyword in GROUND_FAMILY_KEYWORDS:
        if keyword in key:
            return family
    if key.startswith(LEGACY_GROUND_PREFIX):
        return LEGACY_GROUND_PREFIX
    return None


def _derive_biome_frames(path: Path) -> tuple[pygame.Surface, ...]:
    """生物群系整图 → 固定网格 tile 帧（V11）：``BIOME_FRAME_GRID²`` 个均匀取样块，
    帧边长 = min(BIOME_FRAME_MAX_PX, w//grid, h//grid)（小图自动缩格），行主序排列；
    派生确定性（同图同帧序，无随机数），损坏/零尺寸文件返回空元组（不阻断加载）。"""
    try:
        with Image.open(path) as image:
            rgba = image.convert("RGBA")
    except Exception:  # noqa: BLE001 - 单张损坏 JPG 跳过，不阻断整包加载
        return ()
    width, height = rgba.size
    side = min(BIOME_FRAME_MAX_PX, width // BIOME_FRAME_GRID, height // BIOME_FRAME_GRID)
    if side <= 0:
        return ()
    xs = [(column * (width - side)) // (BIOME_FRAME_GRID - 1) for column in range(BIOME_FRAME_GRID)]
    ys = [(row * (height - side)) // (BIOME_FRAME_GRID - 1) for row in range(BIOME_FRAME_GRID)]
    frames: list[pygame.Surface] = []
    for y in ys:
        for x in xs:
            patch = rgba.crop((x, y, x + side, y + side))
            frames.append(pygame.image.frombuffer(patch.tobytes(), patch.size, "RGBA"))
    return tuple(frames)


def _load_surface(path: Path, *, opaque: bool = False) -> pygame.Surface | None:
    """PIL 读 TGA → pygame RGBA Surface（有显示面时 convert_alpha 提速）；损坏文件跳过。

    opaque=True（地面基础纹理）：alpha 通道在 MC2 管线中是高光/细节而非透明度，
    强制置 255，避免按 alpha 合成把纹理压近全黑。
    """
    try:
        with Image.open(path) as image:
            rgba = image.convert("RGBA")
            if opaque:
                rgba.putalpha(255)
    except Exception:  # noqa: BLE001 - 单张损坏 TGA 跳过，不阻断整包加载
        return None
    surface = pygame.image.frombuffer(rgba.tobytes(), rgba.size, "RGBA")
    if pygame.display.get_surface() is not None:
        surface = surface.convert_alpha()
    return surface


class MC2AssetPack:
    """本地 MC2 资产索引：小写文件名 stem → pygame.Surface（含解析/缩放缓存）。

    三类索引：``_sprites``（TGL/Art 对象精灵：机甲 + 环境）、``_ground_tiles``
    （Textures/64 地面基础纹理，强制不透明）、``_overlay_sequences``（64Overlays
    覆盖序列：前缀 → 按帧号排序的 Surface 元组，原生 alpha）。
    """

    def __init__(
        self,
        root: Path,
        sprites: dict[str, pygame.Surface],
        ground_tiles: dict[str, pygame.Surface] | None = None,
        overlay_sequences: dict[str, tuple[pygame.Surface, ...]] | None = None,
        ground_sequences: dict[str, tuple[pygame.Surface, ...]] | None = None,
    ) -> None:
        self.root = root
        self._sprites = sprites
        self._ground_tiles = ground_tiles if ground_tiles is not None else {}
        self._overlay_sequences = overlay_sequences if overlay_sequences is not None else {}
        # V11 生物群系整图派生帧索引：stem → 固定网格 tile 帧（Random_Maps JPG 派生）。
        self._ground_sequences = ground_sequences if ground_sequences is not None else {}
        self.tga_count = (
            len(sprites) + len(self._ground_tiles) + sum(len(frames) for frames in self._overlay_sequences.values())
        )  # pack 统计：成功索引的 TGA 数量
        self._resolve_cache: dict[tuple[str, bool], pygame.Surface | None] = {}
        self._scaled_cache: dict[tuple[str, bool, int], pygame.Surface | None] = {}
        self._unit_resolve_cache: dict[tuple[str, bool], pygame.Surface | None] = {}
        self._unit_scaled_cache: dict[tuple[str, bool, int], pygame.Surface | None] = {}
        # P4 机甲朝向帧：stem → 8 帧原生 Surface（None=负缓存）/ (kind, heading, size) → 缩放帧。
        self._ase_frames: dict[str, tuple[pygame.Surface, ...] | None] = {}
        self._ase_scaled: dict[tuple[str, int, int], pygame.Surface | None] = {}
        self._ground_pools: dict[str, tuple[pygame.Surface, ...]] = {}
        self._ground_scaled_cache: dict[tuple[str, int, int], pygame.Surface | None] = {}
        self._env_resolve_cache: dict[str, pygame.Surface | None] = {}
        self._env_scaled_cache: dict[tuple[str, int], pygame.Surface | None] = {}
        self._button_layout: ButtonLayout | None = None
        self._button_layout_loaded = False
        # W1 任务 1：pak texture_id → Textures/64/*.tga 路径索引（triple fallback）
        self._texture_index: dict[int, Path] = {}
        self._undefined_tga_path: Path | None = None  # undefined.tga fallback sprite

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
        ground_tiles: dict[str, pygame.Surface] = {}
        overlay_frames: dict[str, list[tuple[int, pygame.Surface]]] = {}
        for rel_dir, opaque in ((GROUND_DIR, True), (DEFAULTS_DIR, True), (OVERLAY_DIR, False)):
            directory = data / rel_dir
            if not directory.is_dir():
                continue
            for path in sorted(directory.iterdir()):
                if not path.is_file() or path.suffix.lower() != ".tga":
                    continue
                surface = _load_surface(path, opaque=opaque)
                if surface is None:
                    continue
                stem = path.stem.lower()
                if opaque:
                    ground_tiles[stem] = surface
                    continue
                match = _OVERLAY_STEM_RE.match(stem)
                if match is not None:
                    overlay_frames.setdefault(match.group(1), []).append((int(match.group(2)), surface))
        overlay_sequences = {
            prefix: tuple(surface for _, surface in sorted(frames)) for prefix, frames in overlay_frames.items()
        }
        ground_sequences: dict[str, tuple[pygame.Surface, ...]] = {}
        biome_directory = data / BIOME_DIR
        if biome_directory.is_dir():
            for path in sorted(biome_directory.iterdir()):
                if not path.is_file() or path.suffix.lower() != ".jpg":
                    continue
                frames = _derive_biome_frames(path)
                if frames:
                    ground_sequences[path.stem.lower()] = frames
        # W1 任务 1：扫描 Textures/64/ 建立 texture_id → tga_path 索引
        texture_index: dict[int, Path] = {}
        undefined_tga_path: Path | None = None
        ground_dir_path = data / GROUND_DIR  # Textures/64
        if ground_dir_path.is_dir():
            for tga_path in sorted(ground_dir_path.iterdir()):
                if not tga_path.is_file() or tga_path.suffix.lower() != ".tga":
                    continue
                stem = tga_path.stem.lower()
                # undefined.tga 单独记录（官方 fallback sprite）
                if stem == "undefined":
                    undefined_tga_path = tga_path
                    continue
                # 文件名 → texture_id 映射（实测 mc2_01.pak 分布后确认规则）
                # 假设：{texture_id}.tga 或 {name}_{texture_id}.tga
                try:
                    texture_id = int(stem.split("_")[-1])
                    texture_index[texture_id] = tga_path
                except ValueError:
                    continue  # 非数字 stem 跳过（杂项文件，不入索引）

        if not sprites and not ground_tiles and not ground_sequences and not overlay_sequences:
            return None
        pack = cls(base, sprites, ground_tiles, overlay_sequences, ground_sequences)
        pack._texture_index = texture_index
        pack._undefined_tga_path = undefined_tga_path
        return pack

    def resolve_mech_sprite(self, chassis_kind: str, *, damaged: bool = False) -> pygame.Surface | None:
        """机甲模板/名 → 精灵：精确 stem → 包含匹配（大小写不敏感）→ None；结果缓存。

        damaged=True（Wave V2）：候选名先派生受损变体（基名直拼 dam/x 变体词）进同一
        匹配链，未命中回退基名候选（真资产无变体时输出与 damaged=False 一致）。
        """
        key = chassis_kind.strip().lower()
        if not key:
            return None  # 空 kind：包含匹配会退化（"" 命中一切），直接回退程序化
        cache_key = (key, damaged)
        if cache_key in self._resolve_cache:
            return self._resolve_cache[cache_key]
        candidates = _sprite_name_candidates(key)
        if damaged:
            variants = tuple(name + word for name in candidates for word in MECH_DAMAGED_VARIANT_WORDS)
            sprite = self._lookup(variants + candidates)
        else:
            sprite = self._lookup(candidates)
        self._resolve_cache[cache_key] = sprite
        return sprite

    def scaled_mech_sprite(self, chassis_kind: str, size_px: int, *, damaged: bool = False) -> pygame.Surface | None:
        """解析并缩放到目标尺寸（逐 (kind, damaged, size) 缓存，避免逐帧 smoothscale）。"""
        cache_key = (chassis_kind.strip().lower(), damaged, size_px)
        if cache_key in self._scaled_cache:
            return self._scaled_cache[cache_key]
        sprite = self.resolve_mech_sprite(chassis_kind, damaged=damaged)
        scaled: pygame.Surface | None = sprite
        if sprite is not None and sprite.get_size() != (size_px, size_px):
            scaled = pygame.transform.smoothscale(sprite, (size_px, size_px))
        self._scaled_cache[cache_key] = scaled
        return scaled

    def resolve_unit_sprite(self, kind: str, *, damaged: bool = False) -> pygame.Surface | None:
        """载具/炮塔 kind → 精灵（P2）：UNIT_SPRITE_NAMES 映射 stem；damaged=True 派生
        dam/x 变体（storm → stormdam 命中官方 StormDam.tga），未命中回退基名候选；
        未知 kind 原样进匹配链 → 全未命中返回 None（渲染层程序化回退）。结果缓存。"""
        key = kind.strip().lower()
        if not key:
            return None  # 空 kind：包含匹配会退化（"" 命中一切），直接回退程序化
        cache_key = (key, damaged)
        if cache_key in self._unit_resolve_cache:
            return self._unit_resolve_cache[cache_key]
        name = UNIT_SPRITE_NAMES.get(key, key)
        candidates = (name,)
        if damaged:
            variants = tuple(name + word for word in MECH_DAMAGED_VARIANT_WORDS)
            sprite = self._lookup(variants + candidates)
        else:
            sprite = self._lookup(candidates)
        self._unit_resolve_cache[cache_key] = sprite
        return sprite

    def scaled_unit_sprite(self, kind: str, size_px: int, *, damaged: bool = False) -> pygame.Surface | None:
        """解析并缩放到目标尺寸（逐 (kind, damaged, size) 缓存，避免逐帧 smoothscale）。"""
        cache_key = (kind.strip().lower(), damaged, size_px)
        if cache_key in self._unit_scaled_cache:
            return self._unit_scaled_cache[cache_key]
        sprite = self.resolve_unit_sprite(kind, damaged=damaged)
        scaled: pygame.Surface | None = sprite
        if sprite is not None and sprite.get_size() != (size_px, size_px):
            scaled = pygame.transform.smoothscale(sprite, (size_px, size_px))
        self._unit_scaled_cache[cache_key] = scaled
        return scaled

    def scaled_mech_heading_sprite(self, chassis_kind: str, heading: int, size_px: int) -> pygame.Surface | None:
        """机甲 kind + 朝向桶 → 预渲染朝向帧（P4）：候选 stem（同静态卡映射链）命中
        ``Data/TGL/<stem>.ase`` 时返回该朝向帧的缩放 Surface；未命中（无 .ase 文件 /
        解析失败 / 无网格）→ None（渲染层回退存量静态卡路径，输出不变）。

        缓存三级：``_ase_frames``（stem → 8 帧原生，None 负缓存防重复扫描）、
        ``_ase_scaled``（(kind, heading, size) → 帧结果）。lazy 构建（首次调用才扫
        Data/TGL），无资产环境恒 None。"""
        key = chassis_kind.strip().lower()
        if not key:
            return None
        cache_key = (key, heading, size_px)
        if cache_key in self._ase_scaled:
            return self._ase_scaled[cache_key]
        frame: pygame.Surface | None = None
        for name in _sprite_name_candidates(key):
            frames = self._ase_frame_set(name)
            if frames:
                frame = frames[heading % HEADING_STEPS]
                if frame.get_size() != (size_px, size_px):
                    frame = pygame.transform.smoothscale(frame, (size_px, size_px))
                break
        self._ase_scaled[cache_key] = frame
        return frame

    def _ase_frame_set(self, stem: str) -> tuple[pygame.Surface, ...] | None:
        """stem → 8 朝向原生帧（lazy 解析 + 预渲染，结果/负缓存均驻留）。"""
        cached = self._ase_frames.get(stem)
        if cached is not None:
            return cached
        if stem in self._ase_frames:
            return None  # 已负缓存（无 .ase / 解析失败）
        frames: tuple[pygame.Surface, ...] | None = None
        ase_path = self._find_ase(stem)
        if ase_path is not None:
            try:
                scene = parse_ase(ase_path)
                frames = render_heading_frames(
                    scene, self._sprites.get(stem + MECH_ASE_TEXTURE_SUFFIX), ASE_FRAME_CANVAS_PX
                )
            except (AseParseError, OSError, ValueError):  # 单文件损坏跳过 → 回退静态卡
                frames = None
            if frames is not None and len(frames) != HEADING_STEPS:
                frames = None  # 帧数不足按未命中处理（朝向选帧需全量 8 帧）
        self._ase_frames[stem] = frames
        return frames

    def _find_ase(self, stem: str) -> Path | None:
        """Data/TGL 下按 stem 大小写不敏感精确匹配 ``<stem>.ase``（无则 None）。"""
        directory = self.root / "Data" / ASE_DIR
        if not directory.is_dir():
            return None
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.suffix.lower() == ".ase" and path.stem.lower() == stem:
                return path
        return None

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

    @property
    def has_ground_assets(self) -> bool:
        """是否收录地面/覆盖资产（渲染层据此切换纹理地形与程序化色块）。"""
        return bool(self._ground_tiles) or bool(self._overlay_sequences) or bool(self._ground_sequences)

    @property
    def button_layout(self) -> ButtonLayout | None:
        """buttonlayout1280.fit 解析结果（lazy 缓存）：布局缺失/零部件零解析 → None。

        图集查找走 _sprites 索引（Data/Art 扫描既有收录 MCUI_* 图集）；结果缓存
        （fit 文件与图集在 pack 生命周期内不变，重复访问零开销）。"""
        if self._button_layout_loaded:
            return self._button_layout
        self._button_layout_loaded = True
        fit_path = self.root / "Data" / BUTTON_LAYOUT_FIT_RELPATH
        if not fit_path.is_file():
            return None
        sections = parse_fit_sections(fit_path.read_text(encoding="utf-8", errors="replace"))
        self._button_layout = _build_button_layout(sections, self._sprites)
        return self._button_layout

    def _family_pool(self, family: str) -> tuple[pygame.Surface, ...]:
        """族 → 候选帧元组（V11）：该族生物群系 JPG 派生帧（stem 排序 + 帧行主序）在前，
        该族 TGA（stem 排序）在后；来源缺省返回空元组（由调用方决定回退链）。"""
        sequences = tuple(
            frame
            for stem in sorted(self._ground_sequences)
            if ground_family(stem) == family
            for frame in self._ground_sequences[stem]
        )
        tiles = tuple(self._ground_tiles[stem] for stem in sorted(self._ground_tiles) if ground_family(stem) == family)
        return sequences + tiles

    def ground_pool(self, terrain_kind: str) -> tuple[pygame.Surface, ...]:
        """terrain_kind → 候选纹理元组（V11 族池）：grass=Forest 派生帧（缺族回退
        detail 系列）、dirt=Desert 派生帧（缺族回退 detail 系列）、rock=Volcanic/
        Lunar/Tundra 派生帧、water=Water 系列（64 + Defaults）、road=2lanedirt 按
        帧号；Cement 城镇地形与未登记 kind 恒空池。回退链保证旧缓存（仅 Detail/
        Cement）行为与 V1 逐像素一致（结果确定，seed 取模选张帧内确定性）。"""
        kind = terrain_kind.strip().lower()
        cached = self._ground_pools.get(kind)
        if cached is not None:
            return cached
        pool: tuple[pygame.Surface, ...] = ()
        if kind == "grass":
            pool = self._family_pool("grass") or self._family_pool(LEGACY_GROUND_PREFIX)
        elif kind == "dirt":
            pool = self._family_pool("dirt") or self._family_pool(LEGACY_GROUND_PREFIX)
        elif kind == "rock":
            pool = self._family_pool("rock")
        elif kind == "water":
            pool = self._family_pool("water")
        elif kind == "road":
            pool = self._overlay_sequences.get(ROAD_SEQUENCE_PREFIX, ())
        self._ground_pools[kind] = pool
        return pool

    def ground_tile_by_index(
        self,
        texture_id: int,
        terrain_type: int,
        fallback_kind: str = "grass",
    ) -> pygame.Surface | None:
        """pak texture_id → Textures/64/ TGA（查表优先）→ triple fallback → None。

        W1 任务 1：pak packet 0 textureData 字段（u32@16）→ 官方纹理优先渲染，
        triple fallback chain：
            1. texture_id in _texture_index → load TGA → force alpha=255 → scale to 64px
            2. texture_id not in _texture_index → load "undefined.tga" (stem="undefined")
            3. "undefined.tga" missing → ground_pool(fallback_kind)[0] (JPG pool first frame)
            4. ground_pool empty → None (renderer draws solid color tile)

        Args:
            texture_id: pak packet 0 textureData 字段（u32）
            terrain_type: pak packet 0 terrainType 字段（u32，当前版本未消费）
            fallback_kind: JPG 池回退族（grass/dirt/rock/water/cement）

        Returns:
            纹理 Surface（64×64 px 缩放），优先 TGA；TGA 不存在时回退 ground_pool；
            ground_pool 返回空列表时返回 None（调用方兜底程序化色块）。
        """
        # 路径 1：texture_id 查表 → Textures/64/*.tga
        tga_path = self._texture_index.get(texture_id)
        if tga_path is not None:
            surface = _load_surface(tga_path, opaque=True)  # alpha=255 强制不透明
            if surface is not None:
                # 缩放到 64×64 px（统一 tile 渲染尺寸）
                if surface.get_size() != (64, 64):
                    surface = pygame.transform.scale(surface, (64, 64))
                return surface

        # 路径 2：undefined.tga fallback
        if self._undefined_tga_path is not None and self._undefined_tga_path.is_file():
            surface = _load_surface(self._undefined_tga_path, opaque=True)
            if surface is not None:
                if surface.get_size() != (64, 64):
                    surface = pygame.transform.scale(surface, (64, 64))
                return surface

        # 路径 3：JPG 池回退（V1.1 程序化池）
        pool = self.ground_pool(fallback_kind)
        if pool:
            return pool[0]  # 取首帧（确定性回退）

        # 路径 4：None（渲染层回退程序化色块）
        return None

    def resolve_ground(self, terrain_kind: str, seed: int = 0) -> pygame.Surface | None:
        """terrain_kind → 地面纹理：候选池按 ``seed % len`` 确定性选张（同 seed 同结果，
        帧内确定性）；池空（未注入资产/未登记 kind）→ None（渲染层回退程序化色块）。"""
        pool = self.ground_pool(terrain_kind)
        if not pool:
            return None
        return pool[seed % len(pool)]

    def scaled_ground_tile(self, terrain_kind: str, seed: int, size_px: int) -> pygame.Surface | None:
        """解析并缩放到 tile 绘制尺寸（按 (kind, 归一化 seed, size) 缓存，避免逐帧 smoothscale）。"""
        pool = self.ground_pool(terrain_kind)
        if not pool:
            return None
        normalized = seed % len(pool)
        cache_key = (terrain_kind.strip().lower(), normalized, size_px)
        if cache_key in self._ground_scaled_cache:
            return self._ground_scaled_cache[cache_key]
        surface = pool[normalized]
        scaled: pygame.Surface | None = surface
        if surface.get_size() != (size_px, size_px):
            scaled = pygame.transform.smoothscale(surface, (size_px, size_px))
        self._ground_scaled_cache[cache_key] = scaled
        return scaled

    def resolve_environment(self, kind: str) -> pygame.Surface | None:
        """环境物件 kind → 精灵：别名表（tree/greenhouse/mech_wreck 等）→ ``_x``/``_dam``
        变体派生 → 包含匹配（最短 stem 优先）→ None；结果缓存。"""
        key = kind.strip().lower()
        if not key:
            return None
        if key in self._env_resolve_cache:
            return self._env_resolve_cache[key]
        sprite = self._lookup_environment(self._environment_candidates(key))
        self._env_resolve_cache[key] = sprite
        return sprite

    def scaled_environment_sprite(self, kind: str, size_px: int) -> pygame.Surface | None:
        """解析并缩放到装饰绘制尺寸（逐 (kind, size) 缓存）；未命中 → None。"""
        cache_key = (kind.strip().lower(), size_px)
        if cache_key in self._env_scaled_cache:
            return self._env_scaled_cache[cache_key]
        sprite = self.resolve_environment(kind)
        scaled: pygame.Surface | None = sprite
        if sprite is not None and sprite.get_size() != (size_px, size_px):
            scaled = pygame.transform.smoothscale(sprite, (size_px, size_px))
        self._env_scaled_cache[cache_key] = scaled
        return scaled

    def _environment_candidates(self, key: str) -> tuple[str, ...]:
        """kind → 候选 stem 序列：别名直译；``_x``/``_dam`` 后缀按 MC2 命名约定
        追加「基名+变体词」（去下划线直拼：tree_x → a_pine&palmx）与「基名本身」
        两个变体候选（greenhouse_dam 命中受损变体、tree_x 命中摧毁变体）。"""
        stem = ENVIRONMENT_ALIASES.get(key, key)
        candidates = [stem]
        for suffix in ENVIRONMENT_VARIANT_SUFFIXES:
            if key.endswith(suffix):
                base = ENVIRONMENT_ALIASES.get(key[: -len(suffix)], key[: -len(suffix)])
                candidates.append(base + suffix.lstrip("_"))
                candidates.append(base)
        return tuple(candidates)

    def _lookup_environment(self, candidates: tuple[str, ...]) -> pygame.Surface | None:
        """环境精灵匹配：先精确，再包含（最短 stem 优先，结果确定）。"""
        for stem in candidates:
            surface = self._sprites.get(stem)
            if surface is not None:
                return surface
        for stem in candidates:
            matches = [s for s in self._sprites if stem in s]
            if matches:
                return self._sprites[min(matches, key=lambda s: (len(s), s))]
        return None
