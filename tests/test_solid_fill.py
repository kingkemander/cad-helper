# -*- coding: utf-8 -*-
"""实心三角（SOLID）填充的回归护栏。

背景
----
ezdxf 的 ``Solid.vertices()`` 用 ``vtx3 != vtx2`` 判断"这是四边形还是三角形"，
只要是四边形就套用 SOLID 的标准顶点重排 ``0,1,2,3 -> 0,1,3,2``。于是这种写法：

    msp.add_solid(pts + [pts[0]])      # vtx3 == vtx0

会被当成四边形，重排后得到 ``[p0, p1, p0, p2]`` —— 一条"去了又回"的路径，
闭合面积恒为 0，**渲染出来是空白**。DXF 本身没有报错、``check`` 也看不出来，
但交付给客户的预览 PNG 里箭头/焊缝符号/堰齿/标高符号全部消失。

68 张实体设备图里曾有 62 个 SOLID 中招（12 张图），例如 UASB-06 出水堰的
21 个 90° 三角堰齿整排不可见——图上写着"三角堰板（90°齿形）"，堰齿却一个没有。

正确写法是让 vtx3 == vtx2（ezdxf 自动补），即只传 3 个点：

    msp.add_solid(pts)                 # 3 参 -> vtx3 = vtx2 -> 正常三角
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import ezdxf  # noqa: E402

import envcad.standards.dimensions as D  # noqa: E402
import envcad.utils as U  # noqa: E402
from envcad.standards.energy_chemical import _common as C  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# 源码级护栏：add_solid(X + [X[0]]) 这个写法在任何文件里都不允许再出现
_BAD_CALL = re.compile(r"add_solid\(\s*\w+\s*\+\s*\[\s*\w+\s*\[\s*0\s*\]\s*\]")


def _zero_area_solids(msp):
    """返回 msp 里闭合面积为 0 的 SOLID 列表（这些在渲染时是空白的）。"""
    out = []
    for e in msp.query("SOLID"):
        v = [(p.x, p.y) for p in e.wcs_vertices()]
        if len(v) < 3:
            out.append(e)
            continue
        a = abs(sum(v[i][0] * v[(i + 1) % len(v)][1]
                    - v[(i + 1) % len(v)][0] * v[i][1]
                    for i in range(len(v))) / 2.0)
        if a < 1e-9:
            out.append(e)
    return out


def _msp():
    return ezdxf.new(setup=True).modelspace()


# --------------------------------------------------------------------------
# 源码级：禁止再把首点补到末尾
# --------------------------------------------------------------------------

def test_no_source_uses_trailing_first_point_idiom():
    """全仓库不得再用 ``add_solid(pts + [pts[0]])`` 写法。"""
    offenders = []
    for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "envcad")):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as fh:
                for i, line in enumerate(fh, 1):
                    if _BAD_CALL.search(line):
                        offenders.append(f"{os.path.relpath(path, ROOT)}:{i}")
    assert not offenders, (
        "以下位置仍用 add_solid(X + [X[0]])，vtx3==vtx0 会让 SOLID 渲染成空白：\n  "
        + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------
# 运行时：每个共享 helper 产出的 SOLID 都必须有真实面积
# --------------------------------------------------------------------------

def test_utils_tri_renders_filled():
    m = _msp()
    U._tri(m, (0.0, 0.0), (1.0, 0.0), 10.0, "粗实线")
    solids = list(m.query("SOLID"))
    assert solids, "utils._tri 没有产出 SOLID"
    assert not _zero_area_solids(m), "utils._tri 的箭头是零面积，预览里会消失"


def test_dimensions_small_arrow_renders_filled():
    m = _msp()
    D._small_arrow(m, (0.0, 0.0), (1.0, 0.0), 1.0, "细实线")
    assert m.query("SOLID"), "dimensions._small_arrow 没有产出 SOLID"
    assert not _zero_area_solids(m), "dimensions._small_arrow 箭头零面积"


def test_solid_tri_renders_filled():
    m = _msp()
    C.solid_tri(m, [(0.0, 0.0), (-3.0, -5.0), (3.0, -5.0)])
    assert m.query("SOLID"), "solid_tri 没有产出 SOLID"
    assert not _zero_area_solids(m), "solid_tri 产出零面积三角"


def test_solid_tri_area_matches_input_triangle():
    """solid_tri 的填充面积应等于传入三角形的面积（底 6 高 5 → 15）。"""
    m = _msp()
    C.solid_tri(m, [(0.0, 0.0), (-3.0, -5.0), (3.0, -5.0)])
    e = list(m.query("SOLID"))[0]
    v = [(p.x, p.y) for p in e.wcs_vertices()]
    area = abs(sum(v[i][0] * v[(i + 1) % len(v)][1]
                   - v[(i + 1) % len(v)][0] * v[i][1]
                   for i in range(len(v))) / 2.0)
    assert area == pytest.approx(15.0), f"填充面积 {area}，应为 15"


def test_pro_dim_elevation_renders_filled():
    """标高符号（倒三角）此前也是零面积。"""
    from envcad.standards.pro_dim import ProDim

    m = _msp()
    ProDim(m).elevation(0.0, 0.0, "-0.600", direction="down")
    assert m.query("SOLID"), "ProDim.elevation 没有产出 SOLID"
    assert not _zero_area_solids(m), "标高符号零面积，预览里会消失"


# --------------------------------------------------------------------------
# 端到端：受影响最重的一张图（UASB 出水堰）堰齿必须真的画出来
# --------------------------------------------------------------------------

def test_uasb_weir_teeth_are_filled(tmp_path):
    """UASB 出水堰的 90° 三角堰齿必须有非零面积（此前整排 21 个不可见）。"""
    from envcad.drawings import t8_uasb

    paths = t8_uasb.gen_uasb(str(tmp_path), level="C")
    if isinstance(paths, str):
        paths = [paths]
    weir = [p for p in paths if "出水堰" in os.path.basename(p)]
    assert weir, f"没找到出水堰图纸，实际产物：{paths}"

    doc = ezdxf.readfile(weir[0])
    bad = _zero_area_solids(doc.modelspace())
    assert not bad, f"{os.path.basename(weir[0])} 里仍有 {len(bad)} 个零面积 SOLID"
