"""真实 DIMENSION 实体回归测试（v1.5.7 B/D 档）。

背景：
    改版前"尺寸标注"只是手工画线 + 文字（`L=8000` / `D=6000`），
    在 CAD 里不可关联测量、不能编辑，且与 GB/T 4458.4 不符。

    本档把关键构件（矩形池平面/剖面、圆形池平面/剖面）改为产出
    **真实 DIMENSION 实体**：箭头、尺寸线、尺寸界线、公差堆叠全部由
    标注样式原生渲染，`ezdxf.bbox` 会递归进匿名块从而被图幅选择计入。

踩过的坑（均已在实现中处理，这里逐条上锁）：
  1. `dimdsep` 默认 44（逗号）→ 必须显式 46（"."），否则 8000 出成 `8000,00`。
  2. `Tolerance.suppress_zeros` 读的是 `dimtzin`（不是 dimzin），默认 0
     → 下偏差 0 会渲染成 `0.000`。
  3. 竖向 `add_linear_dim` 必须传 `angle=90`，否则按 X 量距得 0。
  4. 直径标注前缀由 ezdxf 自动加 `Ø`（存盘转义成 `%%c`），自己再加会重复。
  5. ezdxf `RadiusDimension.add_arrow()` 对两端用同一个
     `dim_line_angle+180`，导致直径标注第二端箭头与第一端同向 → 需后处理校正。

运行：pytest tests/test_real_dimensions.py -v
"""
from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ezdxf.entities import Dimension, MText, Text      # noqa: E402
from ezdxf.math import Vec2                            # noqa: E402

from envcad.engine.dxf_base import new_drawing         # noqa: E402
from envcad.standards.dim import (                     # noqa: E402
    draw_diameter_dimension,
    draw_linear_dimension,
)
from envcad.components.pool import (                   # noqa: E402
    RectPoolParams,
    draw_circular_pool_plan,
    draw_circular_pool_section,
    draw_rect_pool_plan,
    draw_rect_pool_section,
)

SCALE = 100.0


# ── 工具 ────────────────────────────────────────────────

def _dims(msp):
    return [e for e in msp if isinstance(e, Dimension)]


def _block_texts(doc, dim):
    """取匿名块里的文字内容（渲染后的最终字符串）。"""
    out = []
    name = dim.dxf.geometry
    if name and name in doc.blocks:
        for e in doc.blocks.get(name):
            if isinstance(e, MText):
                out.append(e.text)
            elif isinstance(e, Text):
                out.append(e.dxf.text)
    return out


def _block_inserts(doc, dim):
    name = dim.dxf.geometry
    if not name or name not in doc.blocks:
        return []
    return [e for e in doc.blocks.get(name) if e.dxftype() == "INSERT"]


def _block_lines(doc, dim):
    name = dim.dxf.geometry
    if not name or name not in doc.blocks:
        return []
    return [e for e in doc.blocks.get(name) if e.dxftype() == "LINE"]


def _new(sc=SCALE):
    return new_drawing(scale=sc)


# ── 1. 线性标注是真实体、测量值正确、小数点用 "." ────────

def test_linear_dimension_is_real_entity():
    doc, _ = _new()
    msp = doc.modelspace()
    draw_linear_dimension(msp, (0, 0), (8000, 0), offset=8, scale=SCALE)

    dims = _dims(msp)
    assert len(dims) == 1, "应产出 1 个真实 DIMENSION 实体"
    assert dims[0].dxf.geometry in doc.blocks, "匿名几何块应存在"
    assert _block_texts(doc, dims[0]) == ["8000"], "实测值应为 8000"


def test_linear_dimension_vertical_uses_angle_90():
    """竖向尺寸必须 angle=90，否则按 X 量距得 0。"""
    doc, _ = _new()
    msp = doc.modelspace()
    draw_linear_dimension(msp, (0, 0), (0, 4000), offset=-8, scale=SCALE)

    (dim,) = _dims(msp)
    assert _block_texts(doc, dim) == ["4000"], "竖向标注不应量成 0"


def test_decimal_separator_is_dot_not_comma():
    """GB 小数点为 "."；ezdxf 的 dimdsep 默认 44=逗号，必须显式改掉。"""
    doc, _ = _new()
    msp = doc.modelspace()
    draw_linear_dimension(msp, (0, 0), (1234, 0), offset=8, scale=SCALE)

    (dim,) = _dims(msp)
    texts = _block_texts(doc, dim)
    assert texts, "应产出标注文字"
    joined = " ".join(texts)
    assert "," not in joined, f"不应出现逗号小数点：{joined!r}"


def test_tolerance_suppresses_lower_zero_decimals():
    """下偏差 0 不应渲染成 0.000（dimtzin 需置 8）。"""
    doc, _ = _new()
    msp = doc.modelspace()
    draw_linear_dimension(msp, (0, 0), (120, 0), offset=6, scale=SCALE,
                          upper="+0.018", lower="0", sym=False)

    (dim,) = _dims(msp)
    joined = " ".join(_block_texts(doc, dim))
    assert "0.018" in joined, f"上偏差应出现：{joined!r}"
    assert "0.000" not in joined, f"下偏差 0 不应写成 0.000：{joined!r}"


# ── 2. 直径标注 ─────────────────────────────────────────

def test_diameter_dimension_text_has_single_prefix():
    """直径前缀由 ezdxf 自动加，自己再加会变成 ⌀%%c6000 这类重复前缀。"""
    doc, _ = _new(sc=50.0)
    msp = doc.modelspace()
    draw_diameter_dimension(msp, (0, 0), 3000, angle=135.0, scale=50.0)

    (dim,) = _dims(msp)
    assert dim.dimtype & 3 == 3 or dim.dxf.dimtype in (3, 35), "应为直径标注"
    texts = _block_texts(doc, dim)
    assert len(texts) == 1
    t = texts[0]
    assert t.count("%%c") == 1 and "Ø" not in t.replace("Ø", "", 0), t
    assert "%%c6000" == t, f"应为 %%c6000，实际 {t!r}"


def test_diameter_arrows_point_opposite_ways():
    """ezdxf 的 bug：两端箭头会同向；修正后应相差 180°。"""
    doc, _ = _new(sc=50.0)
    msp = doc.modelspace()
    draw_diameter_dimension(msp, (0, 0), 3000, angle=135.0, scale=50.0)

    (dim,) = _dims(msp)
    inserts = _block_inserts(doc, dim)
    assert len(inserts) == 2, "直径标注应有 2 个箭头"

    rots = sorted(round(float(e.dxf.get("rotation", 0.0)) % 360.0, 3)
                  for e in inserts)
    d = abs(rots[1] - rots[0])
    assert abs(d - 180.0) < 1e-6, f"两端箭头应相差 180°，实际 {rots}"


def test_diameter_arrows_tips_sit_on_circle():
    """箭头尖端应在圆周上（插入点 = 对径两点）。"""
    doc, _ = _new(sc=50.0)
    msp = doc.modelspace()
    draw_diameter_dimension(msp, (0, 0), 3000, angle=135.0, scale=50.0)

    (dim,) = _dims(msp)
    p1 = Vec2(dim.dxf.defpoint)
    p2 = Vec2(dim.dxf.defpoint4)
    assert abs((p1 - p2).magnitude - 6000) < 1.0, "defpoint 应是对径两点（=直径）"

    for ins in _block_inserts(doc, dim):
        loc = Vec2(ins.dxf.insert)
        assert abs(loc.magnitude - 3000) < 1.0, f"箭头尖应落在圆周上：{loc}"


def test_diameter_body_points_to_center():
    """箭头在圆周上、箭体朝圆心（即尖端指向圆周外）。"""
    doc, _ = _new(sc=50.0)
    msp = doc.modelspace()
    draw_diameter_dimension(msp, (0, 0), 3000, angle=135.0, scale=50.0)

    (dim,) = _dims(msp)
    for ins in _block_inserts(doc, dim):
        loc = Vec2(ins.dxf.insert)
        rot = float(ins.dxf.get("rotation", 0.0))
        # 箭头块 tip 在原点、箭体沿 -X；箭体方向 = angle(-X) + rot
        body = math.radians(180.0 + rot)
        body_vec = Vec2(math.cos(body), math.sin(body))
        inward = (Vec2(0, 0) - loc).normalize()
        assert body_vec.dot(inward) > 0.99, (
            f"箭体未朝圆心：loc={loc} rot={rot} body={body_vec} inward={inward}"
        )


def test_diameter_dimline_connects_arrow_bases():
    """尺寸线两端应落在两个箭头的根部（修正前有一端多伸出一个箭头长）。"""
    sc = 50.0
    doc, _ = _new(sc=sc)
    msp = doc.modelspace()
    draw_diameter_dimension(msp, (0, 0), 3000, angle=135.0, scale=sc)

    (dim,) = _dims(msp)
    arrow_len = float(_block_inserts(doc, dim)[0].dxf.get("xscale", 0.0))
    assert arrow_len > 0

    lines = _block_lines(doc, dim)
    p1 = Vec2(dim.dxf.defpoint)
    p2 = Vec2(dim.dxf.defpoint4)
    expect = {tuple(round(v, 1) for v in (p1 - (p1).normalize() * arrow_len)),
              tuple(round(v, 1) for v in (p2 - (p2).normalize() * arrow_len))}

    # 至少有一条线，其两个端点分别等于两个箭头根部（即尺寸线）
    found = False
    for ln in lines:
        ends = {tuple(round(v, 1) for v in Vec2(ln.dxf.start)),
                tuple(round(v, 1) for v in Vec2(ln.dxf.end))}
        if ends == expect:
            found = True
            break
    assert found, f"尺寸线端点应为箭头根部 {expect}，实际 {[ (tuple(Vec2(l.dxf.start)), tuple(Vec2(l.dxf.end))) for l in lines ]}"


# ── 3. 构件接入 ─────────────────────────────────────────

def test_rect_pool_plan_and_section_emit_dims():
    doc, _ = _new()
    msp = doc.modelspace()
    p = RectPoolParams(name="调节池", length=8000, width=5000, depth=4000,
                       wall_thick=250, bottom_thick=300)
    draw_rect_pool_plan(msp, (0, 9000), p, SCALE)
    draw_rect_pool_section(msp, 0, 0, p, SCALE)

    got = sorted(t for d in _dims(msp) for t in _block_texts(doc, d))
    assert got == ["4000", "5000", "8000", "8000"], got


def test_circular_pool_plan_and_section_emit_dims():
    doc, _ = _new()
    msp = doc.modelspace()
    draw_circular_pool_plan(msp, (0, 20000), 6000, 250, SCALE, name="沉淀池")
    draw_circular_pool_section(msp, 0, 0, 6000, 5.5, 1.5, 1.2, 250, SCALE)

    got = sorted(t for d in _dims(msp) for t in _block_texts(doc, d))
    assert got == ["%%c6000", "5500", "6000"], got


# ── 4. D 档：t5 生成器不再因 info 未定义而崩 ──────────────

def test_t5_generators_do_not_raise(tmp_path):
    from envcad.drawings.t5_adjustment_pool import gen_t5

    out = gen_t5(str(tmp_path), SCALE)
    assert len(out) == 2, f"应产出 2 张图，实际 {len(out)}"
    for path in out:
        assert os.path.exists(path), f"未生成：{path}"


def test_t5_output_has_real_dimensions(tmp_path):
    import ezdxf
    from envcad.drawings.t5_adjustment_pool import gen_t5

    for path in gen_t5(str(tmp_path), SCALE):
        doc = ezdxf.readfile(path)
        dims = [e for e in doc.modelspace() if isinstance(e, Dimension)]
        assert len(dims) == 4, f"{os.path.basename(path)} 应有 4 个真实标注，实际 {len(dims)}"
