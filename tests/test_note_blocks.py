"""编号条目块（技术要求/施工说明）排版回归测试。

背景（v1.5 修复）：
    `draw_tech_notes` 曾先把外框整块 register 进 BBoxTracker，再往框内写条目
    文字并传 tracker，导致 `_t` 的碰撞避让认为"框内处处被占"，把每一行文字
    朝 8 个候选方向乱撞逃出框外——表现为行距不均（850/250/1200/1900）、
    编号错序、甚至有文字跑到框外。

    正确约定（与 `legend.draw_legend` 一致）：
    框内文字为框内精确定位，不参与碰撞避让；只把外框注册给外部标注避让。

运行：pytest tests/test_note_blocks.py -v
"""
from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from envcad.engine.dxf_base import new_drawing          # noqa: E402
from envcad.drawings import draw_tech_notes             # noqa: E402

NUM_RE = re.compile(r"^(\d+)\.\s")
SCALE = 100.0
LINE_H = 6.0 * SCALE        # draw_tech_notes 默认 line_h=6.0


def _numbered(msp):
    """取出 'N. xxx' 条目文字，按 y 从高到低返回 [(num, x, y, height)]。"""
    out = []
    for e in msp:
        if e.dxftype() != "TEXT":
            continue
        m = NUM_RE.match(e.dxf.text)
        if m:
            out.append((int(m.group(1)), e.dxf.insert.x, e.dxf.insert.y, e.dxf.height))
    out.sort(key=lambda r: -r[2])
    return out


def _build(tracker_on: bool):
    doc, _, tracker = new_drawing(SCALE, return_tracker=True)
    msp = doc.modelspace()
    notes = ["构筑物布置遵循工艺流程，自流段坡度>=0.3%。",
             "提升泵后管道为压力流，管径 DN80~DN150。",
             "构筑物间距满足施工与检修要求，>=800mm。",
             "厂区地面标高 0.000，事故排放口标高 -0.500。"]
    box = draw_tech_notes(msp, (1000.0, 20000.0), SCALE, "总平面技术要求",
                          notes, width=80.0, tracker=tracker if tracker_on else None)
    return msp, box


# ══════════════════════════════════════════════════════════════
# 1. 等距 —— 行距必须严格等于 line_h*scale，不得被避让打乱
# ══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("tracker_on", [False, True])
def test_numbered_notes_uniform_spacing(tracker_on):
    msp, _ = _build(tracker_on)
    rows = _numbered(msp)
    assert len(rows) == 4, f"应有 4 条编号条目，实际 {len(rows)}"
    gaps = [round(rows[i][2] - rows[i + 1][2], 6) for i in range(len(rows) - 1)]
    assert max(gaps) - min(gaps) < 1e-6, f"行距不均：{gaps}（期望全为 {LINE_H}）"
    assert all(abs(g - LINE_H) < 1e-6 for g in gaps), f"行距 {gaps} != {LINE_H}"


# ══════════════════════════════════════════════════════════════
# 2. 顺序 —— 编号必须自上而下递增
# ══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("tracker_on", [False, True])
def test_numbered_notes_in_order(tracker_on):
    msp, _ = _build(tracker_on)
    seq = [r[0] for r in _numbered(msp)]
    assert seq == [1, 2, 3, 4], f"编号错序：{seq}"


# ══════════════════════════════════════════════════════════════
# 3. 内嵌 —— 每条文字都必须落在自己的 附表 外框内
# ══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("tracker_on", [False, True])
def test_numbered_notes_inside_box(tracker_on):
    msp, box = _build(tracker_on)
    bx0, by0, bx1, by1 = box
    for num, x, y, h in _numbered(msp):
        assert bx0 <= x <= bx1, f"条目 {num} 水平越界 x={x} 不在 [{bx0},{bx1}]"
        assert by0 <= y <= by1, f"条目 {num} 纵向越界 y={y} 不在 [{by0},{by1}]（框外/错行）"


# ══════════════════════════════════════════════════════════════
# 4. 行距大于字高 —— 不重叠
# ══════════════════════════════════════════════════════════════

def test_numbered_notes_no_overlap():
    msp, _ = _build(True)
    rows = _numbered(msp)
    hmax = max(r[3] for r in rows)
    gaps = [rows[i][2] - rows[i + 1][2] for i in range(len(rows) - 1)]
    assert min(gaps) > hmax * 1.05, f"行距 {min(gaps)} 与字高 {hmax} 过近，会重叠"


# ══════════════════════════════════════════════════════════════
# 5. 外框仍须注册 —— 外部标注要能避让整块说明区
# ══════════════════════════════════════════════════════════════

def test_box_still_registered_for_others():
    doc, _, tracker = new_drawing(SCALE, return_tracker=True)
    msp = doc.modelspace()
    x0, y0, x1, y1 = draw_tech_notes(msp, (1000.0, 20000.0), SCALE, "技术要求",
                                     ["甲", "乙"], width=80.0, tracker=tracker)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    assert tracker.is_occupied(cx, cy, cx + 1, cy + 1), \
        "说明区外框未注册 —— 外部标注会压到说明框上"
