"""总平面图流向箭头：必须沿构筑物之间的通道走正交折线。

回归的事故现场是 T4-01：两条直连池体进出口的**长斜线**横切整张图面、贴壁穿过
通道，读图时看不出水从哪进哪出（两条斜线还在沉淀池前交叉）。这里锁两件事：

1. ``draw_flow_path`` 本身只画正交折线，末端恰好一个箭头；
2. T4-01 里没有任何流向线穿过池体（池体落在通道上、线沿着通道走）。
"""

import os

import ezdxf
import pytest

from envcad.engine.dxf_base import new_drawing
from envcad.standards.annotate import draw_flow_path


# ─── draw_flow_path 本体 ────────────────────────────────

def _flow_entities(doc):
    msp = doc.modelspace()
    lines = [e for e in msp if e.dxftype() == "LINE"
             and e.dxf.layer == "流向"]
    solids = [e for e in msp if e.dxftype() == "SOLID"
              and e.dxf.layer == "流向"]
    return lines, solids


def test_flow_path_draws_orthogonal_legs_with_one_arrowhead():
    doc, _, tracker = new_drawing(100.0, return_tracker=True)
    msp = doc.modelspace()
    # 一个 Z 形路径：东 → 南 → 东
    draw_flow_path(msp, [(0, 0), (3000, 0), (3000, -4000), (6000, -4000)],
                   100.0, label="出水", tracker=tracker)

    lines, solids = _flow_entities(doc)
    assert len(lines) == 3, f"折线段数 {len(lines)} != 3"
    assert len(solids) == 1, f"箭头数 {len(solids)} != 1"
    for ln in lines:
        a, b = ln.dxf.start, ln.dxf.end
        diagonal = abs(a.x - b.x) > 1e-6 and abs(a.y - b.y) > 1e-6
        assert not diagonal, f"折线里出现斜段 ({a.x},{a.y})->({b.x},{b.y})"

    # 标签落在末段中点外侧，不能贴着箭头尖（贴尖会落进目标构筑物里）
    labels = [e for e in msp if e.dxftype() == "TEXT" and e.dxf.text == "出水"]
    assert len(labels) == 1
    ins = labels[0].dxf.insert
    assert ins.x == pytest.approx(4500.0, abs=1.0), "标签未贴末段中点"
    assert ins.y == pytest.approx(-4000.0 + 4 * 100.0, abs=1.0)


def test_flow_path_short_input_is_safe():
    """折点不足两个时不应抛异常（生成端可能因为缺参数退化）。"""
    doc, _, _ = new_drawing(100.0, return_tracker=True)
    msp = doc.modelspace()
    assert draw_flow_path(msp, [(0, 0)], 100.0) == (0, 0)
    assert draw_flow_path(msp, [], 100.0) is None
    assert not _flow_entities(doc)[0]


# ─── T4-01 总平面布置图 ─────────────────────────────────

def _rect_overlap(p0, p1, rect, eps=1.0):
    """线段落在矩形（内缩 eps）里的参数区间，无交集返回 None。"""
    x0, y0, x1, y1 = rect[0] + eps, rect[1] + eps, rect[2] - eps, rect[3] - eps
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, p0[0] - x0), (dx, x1 - p0[0]),
                 (-dy, p0[1] - y0), (dy, y1 - p0[1])):
        if abs(p) < 1e-12:
            if q < 0:
                return None
        else:
            t = q / p
            if p < 0:
                if t > t1:
                    return None
                if t > t0:
                    t0 = t
            else:
                if t < t0:
                    return None
                if t < t1:
                    t1 = t
    return (t0, t1) if t1 - t0 > 1e-9 else None


def _wwtp_flow_lines(tmp_path):
    from envcad.drawings import t4_wwtp
    t4_wwtp.gen_t4(str(tmp_path))
    msp = ezdxf.readfile(
        os.path.join(str(tmp_path), "T4-01_总平面布置图.dxf")).modelspace()
    walls, flow = [], []
    for e in msp:
        if e.dxftype() != "LWPOLYLINE":
            continue
        p = list(e.get_points("xy"))
        xs, ys = [q[0] for q in p], [q[1] for q in p]
        if e.dxf.layer == "池体-壁" \
                and (max(xs) - min(xs)) > 1000 and (max(ys) - min(ys)) > 500:
            walls.append((min(xs), min(ys), max(xs), max(ys)))
    for e in msp:
        if e.dxftype() == "LINE" and e.dxf.layer == "流向":
            a, b = e.dxf.start, e.dxf.end
            flow.append(((a.x, a.y), (b.x, b.y)))
    return walls, flow


def test_wwtp_general_flow_never_cuts_through_pools(tmp_path):
    """流向线不许穿池体——长斜线横切正是要修的硬伤。"""
    walls, flow = _wwtp_flow_lines(tmp_path)
    assert len(walls) == 5, f"总平面池体数 {len(walls)} != 5"
    assert flow, "总平面图没有任何流向线"
    for a, b in flow:
        for r in walls:
            assert _rect_overlap(a, b, r) is None, \
                f"流向线 ({a[0]:.0f},{a[1]:.0f})->({b[0]:.0f},{b[1]:.0f}) 穿过池体 {r}"


def test_wwtp_general_flow_has_no_diagonal_legs(tmp_path):
    """长斜线全部改走通道后，流向层应该只剩正交段。"""
    _, flow = _wwtp_flow_lines(tmp_path)
    bad = [(a, b) for a, b in flow
           if abs(a[0] - b[0]) > 1e-6 and abs(a[1] - b[1]) > 1e-6]
    assert not bad, f"仍有斜向流向线 {bad}"


def test_wwtp_general_flow_labels_sit_in_corridors(tmp_path):
    """'进水/提升/沉淀/消毒'是通道上的设计位置，不许落进池体。"""
    walls, _ = _wwtp_flow_lines(tmp_path)
    msp = ezdxf.readfile(
        os.path.join(str(tmp_path), "T4-01_总平面布置图.dxf")).modelspace()
    want = {"进水", "提升", "沉淀", "消毒"}
    found = set()
    for e in msp.query("TEXT"):
        txt = e.dxf.text.strip()
        if txt not in want:
            continue
        found.add(txt)
        p = e.dxf.insert
        for r in walls:
            inside = (r[0] < p.x < r[2]) and (r[1] < p.y < r[3])
            assert not inside, f"流向注记 {txt!r} 落在池体 {r} 内"
    # 防止断言在"标签被改名/漏画"时静默通过（旧版标签带箭头符号，会全部漏掉）
    assert found == want, f"流向注记缺失 {want - found}"


def test_wwtp_material_sheet_is_portrait_and_well_used(tmp_path):
    """5 列 × 26 行材料表是"高瘦"形：应走竖排，别在横纸上留一片空。"""
    from envcad.audit import audit_dir
    from envcad.drawings import t4_wwtp
    t4_wwtp.gen_t4(str(tmp_path))
    rep = {r.name: r for r in audit_dir(str(tmp_path))}["T4-06_设备材料表.dxf"]
    # SheetReport.orientation 用的是"横/纵"
    assert rep.orientation == "纵", f"材料表幅面方向 {rep.orientation}"
    assert rep.fill_pct >= 35.0, f"材料表图面利用率只有 {rep.fill_pct}%"
