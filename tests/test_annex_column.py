"""附表列（AuxColumn）与主体包络（content_bbox）回归测试。

背景（v1.5.9/v1.5.10 修的两个系统性出图问题）：

1. **幅面虚涨**：各生成器把图例/技术要求/表格锚在 ``draw_frame`` 返回的
   *初始图框*（进程默认 A2）角点上，例如 ``(x1 - 90*s, y1 - 25*s)``。
   ``save_dxf_autofit`` 之后才按内容重选幅面，于是"锚在初始图框角点"的附表
   把内容外包络撑开 —— 实测 56 张成套图幅面虚涨，多张被迫从 A3 抬到 A0。
2. **附表块内文字漏排除**：附表块的外框线在 ``附表`` 层，块内文字却画在
   ``文字`` / ``文字-标题`` 层。只按图层排除，量出来的"主体包络"仍含附表
   文字，于是附表列又叠到主体上（实测技术特性表被压住右列）。

因此这里锁死三条不变量：
  * ``content_bbox(exclude_annex=True)`` 必须同时排除附表外框**和块内文字**；
  * ``AuxColumn`` 必须按 item_gap 顺序向下堆叠、不互相重叠，且贴主体右侧；
  * 成套图纸（t7~t13）每张都必须落在**标准幅面且不大于 A3**，内容不越内框。
"""
import importlib
import os

import ezdxf
import pytest

from envcad.standards.frame import (
    AUX_LAYER, PAPER_BASE, MARGIN, TITLE_SCALE,
    annex_regions, content_bbox,
)
from envcad.standards.layout import AuxColumn
from envcad.drawings import draw_tech_notes, aux_column


# ── 工具 ──────────────────────────────────────────────────────────
def _outer_rect(msp):
    """图框外框（图框层最大闭合多段线）。"""
    best, area = None, -1.0
    for e in msp:
        if e.dxf.layer != "图框" or e.dxftype() != "LWPOLYLINE" or not e.closed:
            continue
        p = list(e.get_points("xy"))
        xs, ys = [q[0] for q in p], [q[1] for q in p]
        a = (max(xs) - min(xs)) * (max(ys) - min(ys))
        if a > area:
            area, best = a, (min(xs), min(ys), max(xs), max(ys))
    return best


def _declared_scale(msp):
    """标题栏里写的 1:N。"""
    import re
    for e in msp:
        if e.dxf.layer != "标题栏":
            continue
        t = e.dxf.text if e.dxftype() == "TEXT" else (
            e.text if e.dxftype() == "MTEXT" else "")
        m = re.search(r"1\s*:\s*(\d+)", str(t))
        if m:
            return int(m.group(1))
    return None


def _sheet_of(msp):
    """(幅面, 横/纵, 自洽比例尺) —— 不自洽返回 (None, None, None)。"""
    o, d = _outer_rect(msp), _declared_scale(msp)
    if not o or not d:
        return None, None, None
    w, h = o[2] - o[0], o[3] - o[1]
    for pn, (pw, ph) in PAPER_BASE.items():
        for orient, (aw, ah) in (("横", (pw, ph)), ("纵", (ph, pw))):
            k = w / aw
            if abs(k - h / ah) / max(k, h / ah) < 0.004 and abs(k - d) / d < 0.01:
                return pn, orient, k
    return None, None, None


# ── 1. 附表块（外框 + 块内文字）必须能被整体排除 ──────────────────
def test_annex_regions_groups_box_and_grid():
    """一个附表块（外框 + 若干表格线）应聚成 1 个区域。"""
    msp = ezdxf.new().modelspace()
    msp.add_lwpolyline([(0, 0), (40000, 0), (40000, 20000), (0, 20000)],
                       close=True, dxfattribs={"layer": AUX_LAYER})
    # 表格线（与外框相交 → 同一块）
    msp.add_line((0, 10000), (40000, 10000), dxfattribs={"layer": AUX_LAYER})
    msp.add_line((20000, 0), (20000, 20000), dxfattribs={"layer": AUX_LAYER})
    assert len(annex_regions(msp)) == 1


def test_content_bbox_excludes_annex_box_and_inner_text():
    """★ 核心回归：块内文字画在 文字 层，也必须随附表一起被排除。"""
    msp = ezdxf.new().modelspace()
    msp.add_line((0, 0), (5000, 3000))                     # 主体
    note_bb = draw_tech_notes(msp, (20000, 12000), 100.0, "技术要求",
                              ["条目一。", "条目二。"], width=90.0)

    with_aux = content_bbox(msp, exclude_annex=False)
    body = content_bbox(msp, exclude_annex=True)

    # 附表参与时能覆盖到附表块右下角
    assert with_aux[2] >= note_bb[2] - 1e-6
    # 排除后只剩主体（附表外框 + 块内文字都不算）
    assert body == pytest.approx((0.0, 0.0, 5000.0, 3000.0), abs=1.0), body


def test_content_bbox_none_when_only_annex():
    """空图/仅附表时返回 None（调用方需自行退化，不许崩）。"""
    msp = ezdxf.new().modelspace()
    draw_tech_notes(msp, (20000, 12000), 100.0, "技术要求", ["条目一。"], width=90.0)
    assert content_bbox(msp, exclude_annex=True) is None
    assert content_bbox(msp, exclude_annex=False) is not None


# ── 2. AuxColumn 排列 ────────────────────────────────────────────
def test_aux_column_stacks_below_and_right_of_body():
    msp = ezdxf.new().modelspace()
    msp.add_line((0, 0), (5000, 3000))
    col = AuxColumn(msp, content_bbox(msp, exclude_annex=True), 100.0, gap=1500.0)
    a = col.add(draw_tech_notes, "技术要求", ["条目一。", "条目二。"], width=90.0)
    b = col.add(draw_tech_notes, "校验报告", ["校验一。"], width=90.0)

    assert a[0] >= 5000.0 + 1500.0 - 1e-6        # 贴在主体右侧
    assert b[3] <= a[1] + 1e-6                   # 第二块在首块下方
    assert not (a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3])
    assert col.bbox[0] == pytest.approx(a[0])
    assert col.bbox[3] == pytest.approx(a[3])


def test_aux_column_none_content_degrades_without_crash():
    """仅附表的图：content_bbox 为 None 时退化，不许抛异常。"""
    msp = ezdxf.new().modelspace()
    col = AuxColumn(msp, None, 100.0)
    col.add(draw_tech_notes, "技术要求", ["条目一。"], width=90.0)
    assert col.bbox is not None


def test_aux_column_helper_reuses_one_column_per_sheet():
    """同一张图多次 aux_column() 必须复用同一列，否则块会叠在一起。"""
    msp = ezdxf.new().modelspace()
    msp.add_line((0, 0), (5000, 3000))
    assert aux_column(msp, 100.0) is aux_column(msp, 100.0)


# ── 3. 成套图纸幅面/内框回归 ─────────────────────────────────────
_T_SHEETS = [
    ("t7_baghouse", "gen_baghouse"),
    ("t8_uasb", "gen_uasb"),
    ("t9_spray_tower", "gen_spray_tower"),
    ("t10_activated_carbon", "gen_activated_carbon"),
    ("t11_chimney", "gen_chimney"),
    ("t12_duct", "gen_duct"),
    ("t13_fan", "gen_fan"),
]
_ORDER = ["A0", "A1", "A2", "A3", "A4"]        # 由大到小
# 只装"一张表 / 一张单线流程图"的图纸，幅面必须紧凑：
# 这类图内容极小，一旦出现 A2/A1/A0 就说明附表又锚回了初始图框角点。
_SHORT_SHEET_CAP = {
    "技术特性表": "A4",
    "设备材料表": "A4",
    "工艺流程图": "A3",
}


@pytest.mark.parametrize("mod,fn", _T_SHEETS)
def test_sheet_set_fits_standard_paper_not_bigger_than_a3(tmp_path, mod, fn):
    """每张图：标准幅面 + 声明比例尺自洽 + 内容不越内框 + 幅面 ≤ A3。

    A3 是 C 档收敛目标：这 7 套图都是单体设备/流程图，虚涨到 A2/A1/A0 说明
    附表又锚回了初始图框角点（本测试即该回归的护栏）。
    """
    from ezdxf import bbox as B
    gen = getattr(importlib.import_module(f"envcad.drawings.{mod}"), fn)
    paths = gen(str(tmp_path), level="C")
    assert len(paths) == 8, paths

    for p in paths:
        name = os.path.basename(p)
        msp = ezdxf.readfile(p).modelspace()
        size, orient, k = _sheet_of(msp)
        assert size is not None, f"{name} 图幅/比例尺不自洽"
        # _ORDER 由大到小：索引 >= A2 的索引即"不大于 A2"。
        # 这 7 套都是单体设备图，正常最大 A2；出现 A1/A0 即附表把幅面撑爆。
        assert _ORDER.index(size) >= _ORDER.index("A2"), \
            f"{name} 幅面 {size} 超出 A2（幅面虚涨回归）"
        # 表/流程图这类小内容图纸卡更紧的阈值
        for key, cap in _SHORT_SHEET_CAP.items():
            if key in name:
                assert _ORDER.index(size) >= _ORDER.index(cap), \
                    f"{name} 幅面 {size} 超出 {cap}（附表把幅面撑大了）"
                break

        # 内容（不含图框/标题栏）不得越出**实际图框**内缩后的内框
        # （图框可平移到任意原点，不能按纸面原点算）
        o = _outer_rect(msp)
        a, c = MARGIN.get(size, (25.0, 10.0))
        ix0, iy0 = o[0] + a * k, o[1] + c * k
        ix1, iy1 = o[2] - c * k, o[3] - c * k
        ct = [e for e in msp if e.dxf.layer not in ("图框", "标题栏")]
        cb = B.extents(ct)
        pad = 200.0                                  # 容差 2mm@1:100
        assert cb.extmin.x >= ix0 - pad, f"{name} 左侧压内框"
        assert cb.extmin.y >= iy0 - pad, f"{name} 下侧压内框"
        assert cb.extmax.x <= ix1 + pad, f"{name} 右侧压内框"
        assert cb.extmax.y <= iy1 + pad, f"{name} 上侧压内框"
        assert o[2] - o[0] > 0 and o[3] - o[1] > 0
        # 标题栏随幅面缩放，不允许缺失
        assert any(e.dxf.layer == "标题栏" for e in msp), f"{name} 缺标题栏"


def test_flow_diagram_boxes_are_paper_sized(tmp_path):
    """★ 流程图的框宽必须按**纸面尺寸**定，不许按默认图框宽度自适应。

    旧写法把 5 个框拉伸到默认 A2 的可用宽度（约 439mm × 60mm 一个框），
    流程图自己就把幅面顶到 A2/A1。改后 5 个框总宽 ≈ 5*34+4*8 = 202mm。
    """
    from envcad.drawings import t7_baghouse
    t7_baghouse.gen_baghouse(str(tmp_path), level="C")
    msp = ezdxf.readfile(
        os.path.join(str(tmp_path), "BH-07_工艺流程图.dxf")).modelspace()

    boxes = []
    for e in msp:
        if e.dxf.layer != "工艺" or e.dxftype() != "LWPOLYLINE":
            continue
        p = list(e.get_points("xy"))
        xs, ys = [q[0] for q in p], [q[1] for q in p]
        w, h = (max(xs) - min(xs)) / 100.0, (max(ys) - min(ys)) / 100.0
        if 10 < w < 60 and 10 < h < 40:              # 流程框（纸面 mm）
            boxes.append((min(xs), w, h))
    assert len(boxes) == 5, f"流程框数 {len(boxes)} != 5"
    for _, w, h in boxes:
        assert w == pytest.approx(34.0, abs=0.5), f"流程框宽 {w}mm ≠ 34mm"
        assert h == pytest.approx(24.0, abs=0.5), f"流程框高 {h}mm ≠ 24mm"
    total = sum(w for _, w, _ in boxes)
    assert total < 200.0, f"5 个流程框总宽 {total}mm 撑爆幅面"
