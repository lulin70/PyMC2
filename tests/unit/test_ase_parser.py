"""ase_parser 单测：合成 .ase 文本覆盖解析字段与容错（真 MC2 资产零入库）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers.ase_fixture import write_arrow_ase, write_two_frame_ase
from pymc2.infrastructure.ase_parser import AseParseError, parse_ase


class TestParseAse:
    def test_parses_nodes_vertices_faces(self, tmp_path: Path) -> None:
        path = tmp_path / "TestMech.ase"
        write_arrow_ase(path)
        scene = parse_ase(path)
        assert [node.name for node in scene.nodes] == ["tm_arrow"]
        node = scene.nodes[0]
        assert node.vertices == (
            (-8.0, -12.0, 0.0),
            (8.0, -12.0, 0.0),
            (0.0, 16.0, 0.0),
            (0.0, 0.0, 2.0),
        )
        assert [(face.a, face.b, face.c) for face in node.faces] == [(0, 1, 2), (0, 2, 3)]
        assert [face.mtlid for face in node.faces] == [0, 1]

    def test_parses_uvs_and_materials(self, tmp_path: Path) -> None:
        path = tmp_path / "TestMech.ase"
        write_arrow_ase(path)
        scene = parse_ase(path)
        node = scene.nodes[0]
        assert node.uvs[0] == (0.5, 0.5)
        assert node.face_uvs == ((0, 1, 2), (0, 2, 1))
        assert scene.material_diffuses == ((0.5, 0.5, 0.5),)
        assert scene.frame_count == 1

    def test_missing_header_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.ase"
        path.write_text("*SOMETHING_ELSE {\n}\n", encoding="ascii")
        with pytest.raises(AseParseError, match="header"):
            parse_ase(path)

    def test_no_mesh_nodes_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "helper_only.ase"
        path.write_text(
            '*3DSMAX_ASCIIEXPORT\t200\n*HELPEROBJECT {\n\t*NODE_NAME "joint_root"\n}\n',
            encoding="ascii",
        )
        with pytest.raises(AseParseError, match="no renderable mesh"):
            parse_ase(path)

    def test_helperobject_not_a_node(self, tmp_path: Path) -> None:
        path = tmp_path / "TestMech.ase"
        write_arrow_ase(path)  # 内含 *HELPEROBJECT joint_root
        scene = parse_ase(path)
        assert all(node.name != "joint_root" for node in scene.nodes)

    def test_face_without_mtlid_defaults_zero(self, tmp_path: Path) -> None:
        path = tmp_path / "TestMech.ase"
        write_arrow_ase(path, with_mtlid=False)
        scene = parse_ase(path)
        assert all(face.mtlid == 0 for face in scene.nodes[0].faces)

    def test_crlf_line_endings(self, tmp_path: Path) -> None:
        path = tmp_path / "TestMech.ase"
        write_arrow_ase(path)
        path.write_bytes(path.read_text(encoding="ascii").replace("\n", "\r\n").encode("ascii"))
        scene = parse_ase(path)
        assert len(scene.nodes) == 1
        assert len(scene.nodes[0].vertices) == 4

    def test_animation_scene_takes_timevalue_zero(self, tmp_path: Path) -> None:
        """动画场景（同一 GEOMOBJECT 两个 *MESH 子块）：取 TIMEVALUE 0 帧而非后续采样。"""
        path = tmp_path / "TestMechWalk.ase"
        write_two_frame_ase(path)
        scene = parse_ase(path)
        assert scene.frame_count == 2
        assert [node.name for node in scene.nodes] == ["tm_frame"]
        assert scene.nodes[0].vertices == ((-8.0, -12.0, 0.0), (8.0, -12.0, 0.0))  # TIMEVALUE 0 块

    def test_latin1_high_bytes_tolerated(self, tmp_path: Path) -> None:
        path = tmp_path / "TestMech.ase"
        write_arrow_ase(path)
        raw = path.read_text(encoding="ascii").replace("tm_arrow", "TestMëch")
        path.write_bytes(raw.encode("latin-1"))
        scene = parse_ase(path)
        assert scene.nodes[0].name == "TestMëch"
