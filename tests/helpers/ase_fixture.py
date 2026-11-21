"""P4 测试助手：合成 .ase 文本 fixture 与同构网格场景（真 MC2 资产零入库）。

TestMech 箭头网格（自造数据，非官方文件逐字内容）：地面平面三角箭头（尖朝北/+y）
+ 三角尾翼，2 面 2 材质槽；配套 uniform 色纹理 fixture（PIL 生成 TGA）。供
ase_parser / mesh_render / mc2_assets / 渲染集成 / e2e 切片共用。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

# 箭头 fixture 的配套纹理色（uniform 单色：面色 = 纹理色 × shade，断言可预算）。
ARROW_TEXTURE_COLOR = (230, 60, 230)
# 静态卡 fixture 色（回退路径断言用，与箭头纹理色互异）。
STATIC_CARD_COLOR = (60, 200, 90)
# 地面平面三角法线朝上 → lambert = L_z ≈ 0.8165 → shade = 0.35 + 0.75 × 0.8165 ≈ 0.962。
GROUND_FACE_SHADE = 0.35 + 0.75 * 0.8165


def write_arrow_ase(path: Path, *, timevalue: int = 0, with_mtlid: bool = True) -> None:
    """合成 TestMech 箭头 .ase 文本（尖朝 +y 北；2 面；TIMEVALUE 可指定）。"""
    mtlid_suffix = "\t*MESH_MTLID 0" if with_mtlid else ""
    mtlid_suffix2 = "\t*MESH_MTLID 1" if with_mtlid else ""
    text = (
        "*3DSMAX_ASCIIEXPORT\t200\n"
        '*COMMENT "Synthetic fixture TestMech (not real MC2 data)"\n'
        "*MATERIAL_LIST {\n"
        "\t*MATERIAL_COUNT 1\n"
        "\t*MATERIAL 0 {\n"
        '\t\t*MATERIAL_NAME "TestMech"\n'
        "\t\t*MATERIAL_DIFFUSE 0.5000\t0.5000\t0.5000\n"
        "\t}\n"
        "}\n"
        '*HELPEROBJECT {\n\t*NODE_NAME "joint_root"\n}\n'
        "*GEOMOBJECT {\n"
        '\t*NODE_NAME "tm_arrow"\n'
        "\t*NODE_TM {\n"
        '\t\t*NODE_NAME "tm_arrow"\n'
        "\t\t*TM_POS 0.0000\t0.0000\t0.0000\n"
        "\t}\n"
        "\t*MESH {\n"
        f"\t\t*TIMEVALUE {timevalue}\n"
        "\t\t*MESH_NUMVERTEX 4\n"
        "\t\t*MESH_NUMFACES 2\n"
        "\t\t*MESH_VERTEX_LIST {\n"
        "\t\t\t*MESH_VERTEX 0\t-8.0000\t-12.0000\t0.0000\n"
        "\t\t\t*MESH_VERTEX 1\t8.0000\t-12.0000\t0.0000\n"
        "\t\t\t*MESH_VERTEX 2\t0.0000\t16.0000\t0.0000\n"
        "\t\t\t*MESH_VERTEX 3\t0.0000\t0.0000\t2.0000\n"
        "\t\t}\n"
        "\t\t*MESH_FACE_LIST {\n"
        "\t\t\t*MESH_FACE    0:    A:    0 B:    1 C:    2 AB:    1 BC:    0 CA:    1"
        f"{mtlid_suffix}\n"
        "\t\t\t*MESH_FACE    1:    A:    0 B:    2 C:    3 AB:    0 BC:    1 CA:    1"
        f"{mtlid_suffix2}\n"
        "\t\t}\n"
        "\t\t*MESH_NUMTVERTEX 3\n"
        "\t\t*MESH_TVERTLIST {\n"
        "\t\t\t*MESH_TVERT 0\t0.5000\t0.5000\t0.0000\n"
        "\t\t\t*MESH_TVERT 1\t0.5000\t0.5000\t0.0000\n"
        "\t\t\t*MESH_TVERT 2\t0.5000\t0.5000\t0.0000\n"
        "\t\t}\n"
        "\t\t*MESH_NUMTVFACES 2\n"
        "\t\t*MESH_TFACELIST {\n"
        "\t\t\t*MESH_TFACE 0\t0\t1\t2\n"
        "\t\t\t*MESH_TFACE 1\t0\t2\t1\n"
        "\t\t}\n"
        "\t}\n"
        "}\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="ascii")


def write_arrow_texture(path: Path, color: tuple[int, int, int] = ARROW_TEXTURE_COLOR) -> None:
    """箭头配套 uniform 色纹理 TGA（面 UV 均值采样命中同一像素 → 断言色可预算）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (16, 16), (*color, 255)).save(path)


def write_static_card(path: Path, color: tuple[int, int, int] = STATIC_CARD_COLOR) -> None:
    """静态卡 fixture TGA（32×32 uniform，回退路径像素断言用）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (32, 32), (*color, 255)).save(path)


def write_two_frame_ase(path: Path) -> None:
    """动画场景 fixture：同一 GEOMOBJECT 内两个 *MESH 子块（TIMEVALUE 0/160）。

    TIMEVALUE 0 块 = 4 顶点箭头；TIMEVALUE 160 块 = 2 顶点（内容互异以断言「取首帧」）。"""
    text = (
        "*3DSMAX_ASCIIEXPORT\t200\n"
        "*MATERIAL_LIST {\n\t*MATERIAL_COUNT 0\n}\n"
        "*GEOMOBJECT {\n"
        '\t*NODE_NAME "tm_frame"\n'
        "\t*MESH {\n"
        "\t\t*TIMEVALUE 0\n"
        "\t\t*MESH_NUMVERTEX 2\n\t\t*MESH_NUMFACES 1\n"
        "\t\t*MESH_VERTEX_LIST {\n"
        "\t\t\t*MESH_VERTEX 0\t-8.0000\t-12.0000\t0.0000\n"
        "\t\t\t*MESH_VERTEX 1\t8.0000\t-12.0000\t0.0000\n"
        "\t\t}\n"
        "\t\t*MESH_FACE_LIST {\n"
        "\t\t\t*MESH_FACE    0:    A:    0 B:    1 C:    1 AB:    1 BC:    1 CA:    1\t*MESH_MTLID 0\n"
        "\t\t}\n"
        "\t}\n"
        "\t*MESH {\n"
        "\t\t*TIMEVALUE 160\n"
        "\t\t*MESH_NUMVERTEX 2\n\t\t*MESH_NUMFACES 1\n"
        "\t\t*MESH_VERTEX_LIST {\n"
        "\t\t\t*MESH_VERTEX 0\t1.0000\t1.0000\t1.0000\n"
        "\t\t\t*MESH_VERTEX 1\t2.0000\t2.0000\t2.0000\n"
        "\t\t}\n"
        "\t\t*MESH_FACE_LIST {\n"
        "\t\t\t*MESH_FACE    0:    A:    0 B:    1 C:    1 AB:    1 BC:    1 CA:    1\t*MESH_MTLID 0\n"
        "\t\t}\n"
        "\t}\n"
        "}\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="ascii")
