# -*- coding: utf-8 -*-
"""能源化工模块的制图规范回归（变电站电气总平面）。

这块此前完全没有测试，所以"指北针半径写死 2000 模型 mm"这类比例尺错误
才能长期存在：1:200 时直径只有 20mm、1:100 时变 40mm，图纸比例一改符号就
不合规格；指针还画成尖端在圆外（尖在 ny+nr*1.25、尾边在 ny-nr*0.75），
不是 GB/T 50001—2017 §7 的常用画法。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from envcad.engine.dxf_base import new_drawing
from envcad.standards.energy_chemical import _common as C
from envcad.standards.energy_chemical import substation_pv_foundation as M
from envcad.standards.site import D_NORTH_DIA

# 与 draw_substation_plan 的默认值一致
SITE_W, SITE_D, FENCE = 60000.0, 42000.0, 2000.0
SITE_SCALE = 200.0


def _plan(scale=SITE_SCALE, x=0.0, y=0.0):
    doc, _dim = new_drawing(scale)
    msp = doc.modelspace()
    M.draw_substation_plan(msp, x, y, scale)
    return msp


def _north_circle(msp, scale):
    """指北针圆 = 半径恰为 ``D_NORTH_DIA/2*scale`` 的那个圆。"""
    want = D_NORTH_DIA / 2.0 * scale
    hits = [e for e in msp if e.dxftype() == "CIRCLE"
            and abs(e.dxf.radius - want) < 1e-6]
    assert len(hits) == 1, (
        f"1:{int(scale)} 应恰好有一个半径 {want:.0f} 的指北针圆，"
        f"实际 {len(hits)} 个（半径写死后比例尺一改就不合规格）")
    return hits[0]


# ── 指北针 ────────────────────────────────────────────────

@pytest.mark.parametrize("scale", [100.0, 200.0, 500.0])
def test_north_arrow_diameter_is_paper_sized(scale):
    """直径必须锚在纸面 mm，随比例尺自动缩放（GB/T 50001 §7）。"""
    c = _north_circle(_plan(scale), scale)
    assert c.dxf.radius * 2.0 / scale == pytest.approx(D_NORTH_DIA)


def test_north_arrow_pointer_spans_the_circle():
    """指针尖端在圆顶、尾边在圆底——旧写法尖端在圆外、尾边在圆内。"""
    scale = SITE_SCALE
    msp = _plan(scale)
    c = _north_circle(msp, scale)
    cx, cy = c.dxf.center.x, c.dxf.center.y
    r = c.dxf.radius

    # 找出落在圆附近的那块实心指针
    best, best_d = None, None
    for e in msp.query("SOLID"):
        pts = [(e.dxf.vtx0.x, e.dxf.vtx0.y), (e.dxf.vtx1.x, e.dxf.vtx1.y),
               (e.dxf.vtx2.x, e.dxf.vtx2.y)]
        d = max(abs(px - cx) for px, _ in pts)
        if d <= r + 1e-6 and (best_d is None or d < best_d):
            best, best_d = pts, d
    assert best, "没找到指北针的实心指针"

    ys = [py for _, py in best]
    assert max(ys) == pytest.approx(cy + r, abs=1e-6), "指针尖端不在圆顶"
    assert min(ys) == pytest.approx(cy - r, abs=1e-6), "指针尾边不在圆底"

    tail_half = D_NORTH_DIA / 16.0 * scale       # 尾部半宽 = (直径/8)/2
    base = [px for px, py in best if py < cy]
    assert max(base) - min(base) == pytest.approx(2 * tail_half, rel=1e-6)


def test_north_arrow_stays_inside_the_fence():
    """整个符号（含 "N" 字）都要落在围墙线以内。"""
    scale = SITE_SCALE
    msp = _plan(scale)
    c = _north_circle(msp, scale)
    r = c.dxf.radius
    fx, fy = SITE_W + FENCE, SITE_D + FENCE       # 围墙内角

    assert c.dxf.center.x + r <= fx
    assert c.dxf.center.y + r <= fy
    assert c.dxf.center.x - r >= 0 and c.dxf.center.y - r >= 0

    labels = [e for e in msp if e.dxftype() == "TEXT" and e.dxf.text.strip() == "N"]
    assert len(labels) == 1, "指北针应有且仅有一个 N 字"
    n = labels[0]
    assert n.dxf.insert.x == pytest.approx(c.dxf.center.x)
    assert n.dxf.insert.y - float(n.dxf.height) / 2.0 > c.dxf.center.y + r, \
        "N 字应压在指针尖上方，不能盖住指针"
    assert n.dxf.insert.y + float(n.dxf.height) / 2.0 <= fy, "N 字越出围墙"


# ── 出图体检 ──────────────────────────────────────────────

def test_plan_has_no_text_overlap(tmp_path):
    """站号/站名两行间距必须过得了出图体检的叠印判定。

    旧写法两行相距 14-7=7mm，而体检按 1.6 倍字高估文字外框，需要
    0.8×(5+4)=7.2mm —— 实差 0.2mm 就被判成"文字叠印"，整张图过不了
    ``envcad check`` 这道交付门槛。
    """
    from envcad.audit import audit_dir

    doc, _dim = new_drawing(SITE_SCALE)
    C.add_a3_frame(doc, SITE_SCALE, title="变电站电气总平面布置图",
                   drawing_no="DQ-01")
    M.draw_substation_plan(doc.modelspace(), 0.0, 0.0, SITE_SCALE)
    doc.saveas(str(tmp_path / "DQ-01.dxf"))

    rep = audit_dir(str(tmp_path))[0]
    assert rep.errors == [], rep.errors
    assert rep.overlaps == [], rep.overlaps


def test_pv_and_wtg_modes_are_callable():
    """同一入口的另外两个 mode 不能因为改动而崩。"""
    scale = 200.0
    for mode in ("pv", "wtg"):
        doc, _dim = new_drawing(scale)
        M.draw_substation_pv_foundation(doc.modelspace(), 0.0, 0.0, scale,
                                        mode=mode)
