"""3DSMAX ASCII 场景（.ase）解析器：提取可渲染网格清单（P4 画面补齐）。

格式勘测见 docs/P4_DESIGN_20261121.md §2（实测 MadCat.ase，4579 行）：
- ``*GEOMOBJECT`` 块 = 可渲染网格节点：``*NODE_NAME`` + ``*NODE_TM`` + ``*MESH``；
- ``*MESH`` 顶点为**世界坐标**（导出已烘焙节点变换，无需重建骨骼层级）；
- 动画场景每帧一个 ``*MESH`` 子块（``*TIMEVALUE`` 采样），本解析器只取 TIMEVALUE 0
  （或首个）MESH 块——单静态帧满足 8 朝向预渲染，逐帧动画不在 P4 范围；
- ``*HELPEROBJECT``（骨骼/哑助手）不产节点；灯光体积/命中标记等杂项网格由
  渲染管线按节点名过滤（mesh_render.EXCLUDED_NODE_PREFIXES）。

许可红线不变：.ase 仅从本机 ``~/.pymc2/mc2-assets`` 加载（零入库），仓库内只有
测试用自造 fixture 文本。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ASE_EXPORT_MAGIC = "*3DSMAX_ASCIIEXPORT"

_FACE_RE = re.compile(r"A:\s*(-?\d+)\s+B:\s*(-?\d+)\s+C:\s*(-?\d+)")
_MTLID_RE = re.compile(r"\*MESH_MTLID\s+(-?\d+)")
_VERTEX_RE = re.compile(r"^\*MESH_VERTEX\s+(-?\d+)\s+(\S+)\s+(\S+)\s+(\S+)")
_TVERT_RE = re.compile(r"^\*MESH_TVERT\s+(-?\d+)\s+(\S+)\s+(\S+)")
_TFACE_RE = re.compile(r"^\*MESH_TFACE\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)")
_DIFFUSE_RE = re.compile(r"^\*MATERIAL_DIFFUSE\s+(\S+)\s+(\S+)\s+(\S+)")
_TIMEVALUE_RE = re.compile(r"^\s*\*TIMEVALUE\s+(\d+)", re.MULTILINE)
_MESH_OPEN_RE = re.compile(r"^\*MESH\s*\{")


class AseParseError(Exception):
    """解析结构性失败（缺导出头 / 无可渲染网格）。"""


@dataclass(frozen=True)
class AseFace:
    """三角面：三个顶点索引 + 材质槽（*MESH_MTLID，缺省 0）。"""

    a: int
    b: int
    c: int
    mtlid: int = 0


@dataclass(frozen=True)
class AseMeshNode:
    """单个可渲染网格节点：世界坐标顶点 + 面清单 + UV（MAX v=0 在底部）。"""

    name: str
    vertices: tuple[tuple[float, float, float], ...]
    faces: tuple[AseFace, ...]
    uvs: tuple[tuple[float, float], ...]
    face_uvs: tuple[tuple[int, int, int], ...]


@dataclass(frozen=True)
class AseScene:
    """解析结果：网格节点清单（仅含网格节点）+ 材质 diffuse 色 + 帧数统计。"""

    nodes: tuple[AseMeshNode, ...]
    material_diffuses: tuple[tuple[float, float, float], ...]
    frame_count: int


def _parse_face_line(stripped: str) -> AseFace | None:
    """*MESH_FACE 行 → AseFace（A/B/C 索引 + MTLID；坏行 None 跳过）。

    ``*MESH_FACENORMAL`` 前缀带 ``N`` 与本模式不匹配，天然区分。"""
    if not stripped.startswith("*MESH_FACE"):
        return None
    match = _FACE_RE.search(stripped)
    if match is None:
        return None
    mtlid_match = _MTLID_RE.search(stripped)
    return AseFace(
        a=int(match.group(1)),
        b=int(match.group(2)),
        c=int(match.group(3)),
        mtlid=int(mtlid_match.group(1)) if mtlid_match is not None else 0,
    )


class _MeshBlock:
    """单个 *MESH 子块的累积状态（顶点/面/UV 行按前缀分流）。"""

    def __init__(self) -> None:
        self.timevalue: int | None = None
        self.vertices: list[tuple[float, float, float]] = []
        self.faces: list[AseFace] = []
        self.uvs: list[tuple[float, float]] = []
        self.face_uvs: list[tuple[int, int, int]] = []

    def feed(self, stripped: str) -> None:
        tv = re.match(r"^\*TIMEVALUE\s+(\d+)", stripped)
        if tv is not None:
            self.timevalue = int(tv.group(1))
            return
        vertex = _VERTEX_RE.match(stripped)
        if vertex is not None:
            self.vertices.append((float(vertex.group(2)), float(vertex.group(3)), float(vertex.group(4))))
            return
        face = _parse_face_line(stripped)
        if face is not None:
            self.faces.append(face)
            return
        tvert = _TVERT_RE.match(stripped)
        if tvert is not None:
            self.uvs.append((float(tvert.group(2)), float(tvert.group(3))))
            return
        tface = _TFACE_RE.match(stripped)
        if tface is not None:
            self.face_uvs.append((int(tface.group(2)), int(tface.group(3)), int(tface.group(4))))

    def build(self, name: str) -> AseMeshNode | None:
        """网格非空才产节点（空网格助手节点跳过）。"""
        if not self.vertices or not self.faces:
            return None
        return AseMeshNode(
            name=name,
            vertices=tuple(self.vertices),
            faces=tuple(self.faces),
            uvs=tuple(self.uvs),
            face_uvs=tuple(self.face_uvs),
        )


def _first_mesh_block(block: str) -> _MeshBlock | None:
    """GEOMOBJECT 块文本 → TIMEVALUE 0（或首个）*MESH 子块的累积结果。

    括号深度相对 MESH 开括号计数（嵌套 *MESH_VERTEX_LIST 等子块正确消化）；
    动画场景的后续 *MESH 子块不覆盖首采集（单静态帧语义）。"""
    first: _MeshBlock | None = None
    zero: _MeshBlock | None = None
    current: _MeshBlock | None = None
    depth = 0
    for raw in block.splitlines():
        stripped = raw.strip()
        if current is None:
            if _MESH_OPEN_RE.match(stripped) is not None:
                current = _MeshBlock()
                depth = stripped.count("{") - stripped.count("}")
            continue
        depth += stripped.count("{") - stripped.count("}")
        if depth <= 0:
            if first is None:
                first = current
            if current.timevalue == 0 and zero is None:
                zero = current
            current = None
            continue
        current.feed(stripped)
    return zero if zero is not None else first


def _node_name(block: str) -> str:
    match = re.search(r'\*NODE_NAME\s+"([^"]+)"', block)
    return match.group(1) if match is not None else ""


def parse_ase(path: Path) -> AseScene:
    """解析 .ase 文本 → AseScene（网格节点清单，世界坐标顶点）。

    结构性失败（缺导出头 / 无网格节点）抛 AseParseError；坏行逐条跳过
    （官方文件含杂项段不致命）。frame_count 为全场景 TIMEVALUE 去重数
    （动画规模统计，本切片不消费逐帧采样）。"""
    text = path.read_text(encoding="latin-1")
    if ASE_EXPORT_MAGIC not in text:
        raise AseParseError(f"missing {ASE_EXPORT_MAGIC} header: {path}")
    material_diffuses = tuple(
        (float(m.group(1)), float(m.group(2)), float(m.group(3)))
        for m in (_DIFFUSE_RE.match(line.strip()) for line in text.splitlines())
        if m is not None
    )
    nodes: list[AseMeshNode] = []
    for block in re.split(r"\*GEOMOBJECT\s*\{", text)[1:]:
        mesh = _first_mesh_block(block)
        if mesh is None:
            continue
        node = mesh.build(_node_name(block))
        if node is not None:
            nodes.append(node)
    if not nodes:
        raise AseParseError(f"no renderable mesh nodes: {path}")
    frame_count = len(set(_TIMEVALUE_RE.findall(text)))
    return AseScene(nodes=tuple(nodes), material_diffuses=material_diffuses, frame_count=max(frame_count, 1))
