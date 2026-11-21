"""机甲网格 8 朝向软件光栅（P4 画面补齐）：正交投影俯视 + 固定倾角 + 平面着色。

输入 ase_parser 产出的世界坐标网格（AseScene），绕 z 轴按 8 桶朝向（45° 步进）旋转，
按面深度排序（画师算法）填色，输出 (机甲, 朝向) 预渲染 Surface——调用方缓存后零
运行时成本。参数勘测见 docs/P4_DESIGN_20261121.md §4：

- 坐标系：MAX z 轴向上，y+ 为机首（北）；pygame 世界 x 东 y 南，北 = 屏幕 −y。
  bucket k 朝向 = mesh 绕 z 轴旋转 −k·45°（俯视顺时针：北→东→南→西）。
- 投影：screen_x = x；screen_up = y·sinθ + z·cosθ（θ=CAMERA_PITCH_DEG，pygame y 向下
  取负）。深度 = y·cosθ − z·sinθ（视向 d=(0, cosθ, −sinθ)，大者远先画）。
- 着色：法线双面化后 shade = ambient + diffuse·max(0, n̂·L)；面色 = 面 UV 均值采样
  纹理单点（32px 尺度下面均 1-3px，观感≈贴图）或材质 diffuse，乘 shade。
- 抗缝隙：SUPERSAMPLE 倍超采样画布逐面填充 + 同色 1px 描边封缝 → smoothscale 回
  目标尺寸（对齐官方卡 128→32 的缩放管线）。
"""

from __future__ import annotations

import math

import pygame

from pymc2.infrastructure.ase_parser import AseMeshNode, AseScene

# 朝向桶数（与 domain/entities/mech.py 的 HEADING_STEPS/heading_bucket 同口径：
# 0=北顺时针 45°/步，8 桶朝向 ↔ 8 张帧；跨层不 import，一致性由测试锁定）。
HEADING_STEPS = 8
CAMERA_PITCH_DEG = 45.0
_LIGHT_RAW = (-0.35, -0.45, 0.82)
_norm = math.sqrt(sum(c * c for c in _LIGHT_RAW))
LIGHT_DIRECTION = (_LIGHT_RAW[0] / _norm, _LIGHT_RAW[1] / _norm, _LIGHT_RAW[2] / _norm)
SHADE_AMBIENT = 0.35
SHADE_DIFFUSE = 0.75
SUPERSAMPLE = 4
_CANVAS_MARGIN_PX = 4

# 杂项网格排除（灯光体积 Spot_*/SL*、命中标记 Hit_*——非机体部件，勘测 §2 要点 3）。
EXCLUDED_NODE_PREFIXES = ("spot", "sl", "hit_")
_FALLBACK_FACE_COLOR = (160, 160, 160)

# 程序化脚影：半透明椭圆（与选中框/血条程序化风格一致），渲染层 blit 在精灵之下。
SHADOW_COLOR = (12, 16, 20)
SHADOW_ALPHA = 90
SHADOW_HEIGHT_RATIO = 0.45


def _rotated_vertices(
    vertices: tuple[tuple[float, float, float], ...], angle_rad: float
) -> list[tuple[float, float, float]]:
    """顶点绕 z 轴旋转（标准逆时针角；bucket 传负角实现俯视顺时针桶序）。"""
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    return [(x * cos_a - y * sin_a, x * sin_a + y * cos_a, z) for x, y, z in vertices]


def _screen_up(p: tuple[float, float, float], sin_pitch: float, cos_pitch: float) -> float:
    """世界点 → 投影平面高度（screen_x 恒等于 x，不另封装）。"""
    return p[1] * sin_pitch + p[2] * cos_pitch


def _shade_of(normal: tuple[float, float, float]) -> float:
    """平面着色系数：法线双面化（面向相机）后按固定光照方向漫反射。"""
    nx, ny, nz = normal
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length <= 1e-9:
        return SHADE_AMBIENT
    nx, ny, nz = nx / length, ny / length, nz / length
    # 视向到相机 v = (0, −cosθ, sinθ)：法线背离相机时翻转（官方文件面序双面容忍）。
    pitch_rad = math.radians(CAMERA_PITCH_DEG)
    dot_camera = -ny * math.cos(pitch_rad) + nz * math.sin(pitch_rad)
    if dot_camera <= 0.0:
        nx, ny, nz = -nx, -ny, -nz
    lambert = max(0.0, nx * LIGHT_DIRECTION[0] + ny * LIGHT_DIRECTION[1] + nz * LIGHT_DIRECTION[2])
    return min(1.0, SHADE_AMBIENT + SHADE_DIFFUSE * lambert)


def _face_color(
    uvs: tuple[tuple[float, float], ...],
    texture: pygame.Surface | None,
    diffuses: tuple[tuple[float, float, float], ...],
    mtlid: int,
    shade: float,
) -> tuple[int, int, int]:
    """面色：纹理按面 UV 均值单点采样（v 翻转：MAX v=0 底部），透明像素/无纹理回退
    材质 diffuse；乘 shade 后逐通道 clamp。"""
    base: tuple[int, int, int] | None = None
    if texture is not None and uvs:
        u = sum(uv[0] for uv in uvs) / len(uvs)
        v = sum(uv[1] for uv in uvs) / len(uvs)
        width, height = texture.get_size()
        px = min(width - 1, max(0, int(u * width)))
        py = min(height - 1, max(0, int((1.0 - v) * height)))
        sampled = texture.get_at((px, py))
        if sampled.a >= 128:
            base = (sampled.r, sampled.g, sampled.b)
    if base is None and diffuses:
        material = diffuses[mtlid] if 0 <= mtlid < len(diffuses) else diffuses[0]
        base = (int(material[0] * 255), int(material[1] * 255), int(material[2] * 255))
    if base is None:
        base = _FALLBACK_FACE_COLOR
    return (
        min(255, max(0, int(base[0] * shade))),
        min(255, max(0, int(base[1] * shade))),
        min(255, max(0, int(base[2] * shade))),
    )


def _face_uvs(node: AseMeshNode, face_index: int) -> tuple[tuple[float, float], ...]:
    """面索引 → 三个 tvert 的 UV 值（索引越界/缺 UV 清单返回空 → 回退面色）。"""
    if face_index >= len(node.face_uvs):
        return ()
    uvs: list[tuple[float, float]] = []
    for idx in node.face_uvs[face_index]:
        if 0 <= idx < len(node.uvs):
            uvs.append(node.uvs[idx])
    return tuple(uvs)


def _node_faces(
    node: AseMeshNode,
    rotated: list[tuple[float, float, float]],
    sin_pitch: float,
    cos_pitch: float,
    texture: pygame.Surface | None,
    diffuses: tuple[tuple[float, float, float], ...],
) -> list[tuple[float, tuple[tuple[float, float], ...], tuple[int, int, int]]]:
    """单节点全部有效面 → (深度, 投影三点, 颜色) 条目（深度大者远）。"""
    entries: list[tuple[float, tuple[tuple[float, float], ...], tuple[int, int, int]]] = []
    projected = [(v[0], _screen_up(v, sin_pitch, cos_pitch)) for v in rotated]
    for face_index, face in enumerate(node.faces):
        if not (0 <= face.a < len(rotated) and 0 <= face.b < len(rotated) and 0 <= face.c < len(rotated)):
            continue
        a, b, c = rotated[face.a], rotated[face.b], rotated[face.c]
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        normal = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
        shade = _shade_of(normal)
        depth = (a[1] + b[1] + c[1]) / 3.0 * cos_pitch - (a[2] + b[2] + c[2]) / 3.0 * sin_pitch
        color = _face_color(_face_uvs(node, face_index), texture, diffuses, face.mtlid, shade)
        entries.append((depth, (projected[face.a], projected[face.b], projected[face.c]), color))
    return entries


def _canvas_transform(extents: tuple[float, float, float, float], canvas_px: int) -> tuple[float, float, float]:
    """全局投影 bbox → (scale, origin_cx, origin_cy) 单位坐标 → 画布像素仿射参数。

    内容 bbox 中心水平居中（blit center 对齐语义）；screen_up 取负后顶部贴 margin；
    8 帧共用同一变换 → 帧间零漂移。"""
    min_x, max_x, min_up, max_up = extents
    content_w = max(max_x - min_x, 1e-6)
    content_h = max(max_up - min_up, 1e-6)
    scale = (canvas_px - 2 * _CANVAS_MARGIN_PX) / max(content_w, content_h)
    origin_cx = canvas_px / 2.0 - (min_x + max_x) / 2.0 * scale
    origin_cy = _CANVAS_MARGIN_PX + max_up * scale  # canvas_y = origin_cy − up·scale
    return scale, origin_cx, origin_cy


def render_heading_frames(
    scene: AseScene,
    texture: pygame.Surface | None,
    canvas_px: int,
    *,
    steps: int = HEADING_STEPS,
) -> tuple[pygame.Surface, ...]:
    """AseScene → steps 张朝向帧 Surface（k=0 北起顺时针 bucket 序，共用画布零漂移）。

    管线：逐 bucket 旋转 → 投影 → 面深度排序（画师算法）→ 超采样画布填色（同色
    描边封缝）→ smoothscale 回目标尺寸。节点名命中 EXCLUDED_NODE_PREFIXES 的杂项
    网格（灯光/命中标记）不入光栅。"""
    pitch_rad = math.radians(CAMERA_PITCH_DEG)
    sin_pitch, cos_pitch = math.sin(pitch_rad), math.cos(pitch_rad)
    body_nodes = tuple(node for node in scene.nodes if not node.name.lower().startswith(EXCLUDED_NODE_PREFIXES))
    if not body_nodes:
        return ()
    step_angle = 2 * math.pi / steps
    step_faces: list[list[tuple[float, tuple[tuple[float, float], ...], tuple[int, int, int]]]] = []
    extents = (math.inf, -math.inf, math.inf, -math.inf)
    for step in range(steps):
        entries: list[tuple[float, tuple[tuple[float, float], ...], tuple[int, int, int]]] = []
        for node in body_nodes:
            rotated = _rotated_vertices(node.vertices, -step * step_angle)
            entries.extend(_node_faces(node, rotated, sin_pitch, cos_pitch, texture, scene.material_diffuses))
        for _, points, _ in entries:
            for px, up in points:
                extents = (
                    min(extents[0], px),
                    max(extents[1], px),
                    min(extents[2], up),
                    max(extents[3], up),
                )
        step_faces.append(entries)
    scale, origin_cx, origin_cy = _canvas_transform(extents, canvas_px)
    ss = SUPERSAMPLE
    frames: list[pygame.Surface] = []
    for entries in step_faces:
        canvas = pygame.Surface((canvas_px * ss, canvas_px * ss), pygame.SRCALPHA)
        for _depth, points, color in sorted(entries, key=lambda e: -e[0]):  # 远→近
            points_px = [((origin_cx + px * scale) * ss, (origin_cy - up * scale) * ss) for px, up in points]
            pygame.draw.polygon(canvas, color, points_px)
            pygame.draw.polygon(canvas, color, points_px, 1)  # 同色描边封相邻面缝隙
        if canvas.get_size() != (canvas_px, canvas_px):
            canvas = pygame.transform.smoothscale(canvas, (canvas_px, canvas_px))
        frames.append(canvas)
    return tuple(frames)


def mech_shadow_surface(size_px: int) -> pygame.Surface:
    """程序化半透明椭圆脚影（宽 = size_px，高 = 45%）：渲染层 blit 在精灵之下。"""
    height = max(1, int(size_px * SHADOW_HEIGHT_RATIO))
    surface = pygame.Surface((size_px, height), pygame.SRCALPHA)
    pygame.draw.ellipse(surface, (*SHADOW_COLOR, SHADOW_ALPHA), surface.get_rect())
    return surface
