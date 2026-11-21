"""mesh_render 单测：合成场景 8 朝向帧渲染与方向可辨性（真 MC2 资产零入库）。"""

from __future__ import annotations

import pygame

from pymc2.domain.entities.mech import HEADING_STEPS
from pymc2.infrastructure.ase_parser import AseFace, AseMeshNode, AseScene
from pymc2.infrastructure.mesh_render import (
    SHADOW_ALPHA,
    SUPERSAMPLE,
    mech_shadow_surface,
    render_heading_frames,
)

CANVAS = 64


def arrow_scene(*, extra_node: AseMeshNode | None = None) -> AseScene:
    """TestMech 箭头场景（尖朝 +y 北；地面平面三角 + 小立面，与 helpers fixture 同构）。"""
    arrow = AseMeshNode(
        name="tm_arrow",
        vertices=((-8.0, -12.0, 0.0), (8.0, -12.0, 0.0), (0.0, 16.0, 0.0), (0.0, 0.0, 2.0)),
        faces=(AseFace(0, 1, 2, 0), AseFace(0, 2, 3, 1)),
        uvs=((0.5, 0.5), (0.5, 0.5), (0.5, 0.5)),
        face_uvs=((0, 1, 2), (0, 2, 1)),
    )
    nodes = (arrow,) if extra_node is None else (arrow, extra_node)
    return AseScene(nodes=nodes, material_diffuses=((0.5, 0.5, 0.5),), frame_count=1)


def render_small(scene: AseScene) -> tuple[pygame.Surface, ...]:
    """128px 原生帧 → 32px 缩放帧（对齐渲染层真实缩放管线，便于像素统计）。"""
    frames = render_heading_frames(scene, None, CANVAS)
    return tuple(pygame.transform.smoothscale(frame, (32, 32)) for frame in frames)


def opaque_centroid(surface: pygame.Surface) -> tuple[float, float]:
    """不透明像素质心（列,行）——方向可辨性断言用（画布全局归一下的相对偏移）。"""
    total = sum_x = sum_y = 0
    for y in range(surface.get_height()):
        for x in range(surface.get_width()):
            if surface.get_at((x, y)).a > 0:
                total += 1
                sum_x += x
                sum_y += y
    if total == 0:
        return (0.0, 0.0)
    return (sum_x / total, sum_y / total)


class TestRenderHeadingFrames:
    def test_renders_eight_frames_same_size(self) -> None:
        frames = render_heading_frames(arrow_scene(), None, CANVAS)
        assert len(frames) == HEADING_STEPS
        assert all(frame.get_size() == (CANVAS, CANVAS) for frame in frames)
        assert all(
            any(frame.get_at((x, y)).a > 0 for x in range(0, CANVAS, 4) for y in range(0, CANVAS, 4))
            for frame in frames
        )

    def test_supersampled_canvas_downscaled(self) -> None:
        """输出画布为请求尺寸（内部 SUPERSAMPLE 超采样只影响栅格质量）。"""
        frame = render_heading_frames(arrow_scene(), None, 48)[0]
        assert frame.get_size() == (48, 48)
        assert SUPERSAMPLE > 1  # 超采样常量在位（抗缝隙管线前提）

    def test_opposite_headings_differ(self) -> None:
        frames = render_small(arrow_scene())
        north = pygame.image.tobytes(frames[0], "RGBA")
        south = pygame.image.tobytes(frames[4], "RGBA")
        east = pygame.image.tobytes(frames[2], "RGBA")
        assert north != south  # 对向朝向画面互异
        assert north != east

    def test_directional_centroid_shifts_with_heading(self) -> None:
        """方向可辨性（画布 8 帧全局归一 → 以质心相对偏移断言，非画布半区质量）：
        箭头网格尖朝桶向、宽底在后 → 面积质心偏向尾侧，对向桶质心偏移互异。"""
        frames = render_small(arrow_scene())
        col2, row2 = opaque_centroid(frames[2])
        col6, row6 = opaque_centroid(frames[6])
        # 面积质心偏向宽底（尾侧）：东向帧（尖朝东、底在西）质心列 < 西向帧。
        assert col2 < col6
        col0, row0 = opaque_centroid(frames[0])
        col4, row4 = opaque_centroid(frames[4])
        # 南向帧（尖朝下、底在上）质心行号更小（偏上）→ 与北向帧互异（朝向可辨）。
        assert row4 < row0

    def test_texture_sampling_colors_faces(self) -> None:
        """提供纹理时面色取自纹理采样（uniform 紫 → 面 ≈ 紫×shade，区别于材质灰）。"""
        pygame.init()
        texture = pygame.Surface((8, 8), pygame.SRCALPHA)
        texture.fill((230, 60, 230, 255))
        frames = render_heading_frames(arrow_scene(), texture, CANVAS)
        sampled = [frame.get_at((x, y)) for frame in frames for x in range(0, CANVAS, 4) for y in range(0, CANVAS, 4)]
        opaque = [c for c in sampled if c.a > 0]
        assert opaque, "texture path should render opaque faces"
        assert any(c.r > 150 and c.b > 150 and c.g < 120 for c in opaque)
        pygame.quit()

    def test_excluded_node_prefixes_skipped(self) -> None:
        """灯光体积/命中标记网格（Spot_*/SL*/Hit_* 前缀）不入光栅。"""
        spotlight = AseMeshNode(
            name="Spotlight_MC",
            vertices=((-90.0, -95.0, 0.0), (90.0, -95.0, 0.0), (0.0, -17.0, 35.0)),
            faces=(AseFace(0, 1, 2, 0),),
            uvs=(),
            face_uvs=(),
        )
        frames_with = render_heading_frames(arrow_scene(extra_node=spotlight), None, CANVAS)
        frames_without = render_heading_frames(arrow_scene(), None, CANVAS)
        assert [pygame.image.tobytes(f, "RGBA") for f in frames_with] == [
            pygame.image.tobytes(f, "RGBA") for f in frames_without
        ]
        only_excluded = AseScene(nodes=(spotlight,), material_diffuses=(), frame_count=1)
        assert render_heading_frames(only_excluded, None, CANVAS) == ()

    def test_out_of_range_face_indices_tolerated(self) -> None:
        """面索引越界（坏数据）：该面跳过不崩溃，有效面照常渲染。"""
        node = AseMeshNode(
            name="tm_bad",
            vertices=((0.0, 0.0, 0.0), (8.0, 0.0, 0.0), (0.0, 8.0, 0.0)),
            faces=(AseFace(0, 1, 99, 0), AseFace(0, 1, 2, 0)),
            uvs=(),
            face_uvs=(),
        )
        scene = AseScene(nodes=(node,), material_diffuses=((0.5, 0.5, 0.5),), frame_count=1)
        frames = render_heading_frames(scene, None, 32)
        assert len(frames) == HEADING_STEPS


class TestMechShadowSurface:
    def test_size_and_alpha(self) -> None:
        pygame.init()
        shadow = mech_shadow_surface(32)
        assert shadow.get_size() == (32, max(1, int(32 * 0.45)))
        center = shadow.get_at((16, shadow.get_height() // 2))
        assert center.a == SHADOW_ALPHA
        assert (center.r, center.g, center.b) == (12, 16, 20)
        pygame.quit()
