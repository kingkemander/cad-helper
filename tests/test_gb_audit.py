# -*- coding: utf-8 -*-
"""审图引擎（GB/T 50001—2017）护栏测试。

这里只锁**已经踩过坑**的地方，不追求覆盖全部检查项。每条都对应一次真实误判
或漏判，注释里写清"不这么写会怎样"。

运行：pytest tests/test_gb_audit.py -v
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import ezdxf                                             # noqa: E402

from envcad.engine.dxf_base import new_drawing           # noqa: E402
from envcad.standards import styles as S                 # noqa: E402
from envcad.standards.styles import (                    # noqa: E402
    ARCHTICK_UNIT_LEN,
    ARROW_LEN_MM,
    ARROW_WIDTH_MM,
    CLOSEDFILLED_WIDTH_PER_LEN,
    TICK_LEN_MM,
    archtick_dimasz,
    arrow_dimasz,
)
import envcad.gb_audit as G                              # noqa: E402

S_SCALE = 50.0


# ── 工具 ────────────────────────────────────────────────

def _dim_doc(kind: str, override: dict, scale: float = S_SCALE):
    """造一张只含一个真实 DIMENSION 的文档，override 原样落到实体上。

    必须走 ``DimStyleOverride`` 渲染（``d.render()``），override 才会生效；
    对 ``d.dimension.render()``（实体方法）调 render 会丢掉 override，
    起止符就退回样式值——这正是定位"override 到底有没有用"时的分水岭。
    """
    doc, _ = new_drawing(scale=scale)
    name = S.setup_dimstyles(doc, scale)
    msp = doc.modelspace()
    if kind == "linear":
        d = msp.add_linear_dim(base=(0, 8 * scale), p1=(0, 0), p2=(8000, 0),
                               angle=0, dimstyle=name, override=dict(override),
                               text="<>")
    else:
        d = msp.add_diameter_dim(center=(0, 0), radius=3000, angle=135.0,
                                 dimstyle=name, override=dict(override),
                                 text="<>")
    d.render()
    return doc


def _facts(doc, tmp_path, scale=S_SCALE):
    """存盘重读后构造 SheetFacts，并把比例尺标成"已知"。"""
    p = os.path.join(str(tmp_path), "t.dxf")
    doc.saveas(p)
    doc2 = ezdxf.readfile(p)
    f = G.SheetFacts(path=p, name="t.dxf", doc=doc2, msp=doc2.modelspace())
    f.declared = int(scale)
    f.scale, f.scale_known = float(scale), True
    return f


def _rules(f):
    return sorted({x.rule_id for x in G.check_dim_tick(f)})


TICK_OV = {"dimblk": "_ARCHTICK", "dimasz": archtick_dimasz(S_SCALE)}
ARROW_OV = {"dimblk": S.ARROW_BLOCK, "dimasz": arrow_dimasz(S_SCALE)}
#: 旧代码的写法：dimasz 直接取 2.5*scale。斜短线实画 √2 倍 = 3.54mm，超上限。
OLD_TICK_OV = {"dimblk": "_ARCHTICK", "dimasz": 2.5 * S_SCALE}
#: 旧代码的箭头：dimasz=2.5*scale → 纸面宽只有 0.82mm，低于 §11.1.4b 的 1mm。
OLD_ARROW_OV = {"dimblk": S.ARROW_BLOCK, "dimasz": 2.5 * S_SCALE}


# ── 1. 起止符长度/宽度的换算常量 ────────────────────────

def test_closedfilled_ratio_matches_ezdxf():
    """硬编码的 宽/长 比必须和 ezdxf 的 ``_CLOSEDFILLED`` 块一致。

    这个比值决定"箭头宽度"的换算（b = 比值 × dimasz）。ezdxf 升级改了块几何
    而我们没跟着改，箭头宽度就会算错，且错得很隐蔽（还是能过审图）。
    """
    doc = ezdxf.new("AC1032", setup=True)
    blk = doc.blocks.get("_CLOSEDFILLED")
    pts = [p for e in blk for p in e.vertices()]
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    ratio = (max(ys) - min(ys)) / (max(xs) - min(xs))
    assert ratio == pytest.approx(CLOSEDFILLED_WIDTH_PER_LEN, abs=1e-6), \
        "ezdxf 的 _CLOSEDFILLED 几何变了，请同步 styles.CLOSEDFILLED_WIDTH_PER_LEN"


def test_archtick_block_is_diagonal_of_sqrt2():
    """``_ARCHTICK`` 块内长必须是 √2，否则"画出长度"的反算全错。"""
    doc = ezdxf.new("AC1032")
    assert S.ensure_archtick_block(doc) == S.TICK_BLOCK
    blk = doc.blocks.get(S.TICK_BLOCK)
    assert len(blk) == 1
    pts = [(p[0], p[1]) for p in blk[0].get_points("xy")]
    length = ((pts[1][0] - pts[0][0]) ** 2 + (pts[1][1] - pts[0][1]) ** 2) ** 0.5
    assert length == pytest.approx(ARCHTICK_UNIT_LEN)


def test_dimasz_is_not_the_drawn_length():
    """``dimasz`` 只是块插入比例：斜短线画出长 = √2×dimasz，箭头 = 1.0×dimasz。"""
    assert archtick_dimasz(S_SCALE) * ARCHTICK_UNIT_LEN == \
        pytest.approx(TICK_LEN_MM * S_SCALE)
    assert arrow_dimasz(S_SCALE) == pytest.approx(ARROW_LEN_MM * S_SCALE)
    # 箭头按"宽度下限"反算：宽 = 比值 × 长度 = 1mm
    assert arrow_dimasz(S_SCALE) * CLOSEDFILLED_WIDTH_PER_LEN / S_SCALE == \
        pytest.approx(ARROW_WIDTH_MM)


# ── 2. 生成侧：线性=斜短线、径向=箭头（§11.1.4 两句话分别对应）──

@pytest.mark.parametrize("scale", [50.0, 100.0])
def test_linear_dim_uses_slash_of_2_5mm(tmp_path, scale):
    """线性尺寸画 45° 斜短线，纸面长度落在 §11.1.4 的 2~3mm。"""
    doc, _ = new_drawing(scale=scale)
    msp = doc.modelspace()
    from envcad.standards.dim import draw_linear_dimension
    draw_linear_dimension(msp, (0, 0), (8000, 0), offset=8, scale=scale)
    (dim,) = [e for e in msp if e.dxftype() == "DIMENSION"]

    ins = [e for e in doc.blocks.get(dim.dxf.geometry) if e.dxftype() == "INSERT"]
    assert ins, "线性标注应插入起止符块"
    assert ins[0].dxf.name == S.TICK_BLOCK
    drawn_mm = ins[0].dxf.xscale * ARCHTICK_UNIT_LEN / scale
    assert 2.0 <= drawn_mm <= 3.0, f"斜短线纸面长 {drawn_mm:.3f}mm 不在 2~3mm"
    assert drawn_mm == pytest.approx(TICK_LEN_MM)


@pytest.mark.parametrize("scale", [50.0, 100.0])
def test_diameter_dim_uses_arrow_of_1mm_width(tmp_path, scale):
    """★ 直径尺寸按 §11.1.4 后半句用**箭头**，不是斜短线；宽度 b ≥ 1mm。

    条文说明："一般情况下均用斜短线，圆弧的直径、半径等用箭头。"
    早先把 ``dimblk=_ARCHTICK`` 设在了**基础样式**上，直径标注一起被带偏，
    等于对直径尺寸用错了起止符号。
    """
    doc, _ = new_drawing(scale=scale)
    msp = doc.modelspace()
    from envcad.standards.dim import draw_diameter_dimension
    draw_diameter_dimension(msp, (0, 0), 3000, angle=135.0, scale=scale)
    (dim,) = [e for e in msp if e.dxftype() == "DIMENSION"]

    ins = [e for e in doc.blocks.get(dim.dxf.geometry) if e.dxftype() == "INSERT"]
    assert ins, "直径标注应有起止符"
    assert ins[0].dxf.name != S.TICK_BLOCK, "直径尺寸不应用 45° 斜短线"
    assert ins[0].dxf.name == S.ARROW_BLOCK
    width_mm = ins[0].dxf.xscale * CLOSEDFILLED_WIDTH_PER_LEN / scale
    assert width_mm >= 1.0 - 0.01, \
        f"箭头宽 b={width_mm:.3f}mm 低于 §11.1.4b 的 1mm"
    assert width_mm == pytest.approx(ARROW_WIDTH_MM, abs=0.01)


def test_two_families_use_separate_dimstyles():
    """两类起止符必须落成两个样式：一个样式的 dimblk 装不下两种块。"""
    doc, _ = new_drawing(scale=S_SCALE)
    lin = S.setup_dimstyles(doc, S_SCALE)
    rad = S.setup_arrow_dimstyle(doc, S_SCALE)
    assert lin != rad
    assert doc.dimstyles.get(lin).dxf.dimblk == S.TICK_BLOCK
    assert doc.dimstyles.get(rad).dxf.dimblk == S.ARROW_BLOCK
    assert doc.dimstyles.get(lin).dxf.dimasz != doc.dimstyles.get(rad).dxf.dimasz


def test_arrow_block_created_on_demand():
    """裸 ``ezdxf.new()`` 没有标准箭头块，设样式前必须先补出来。"""
    doc = ezdxf.new("AC1032")
    assert S.ARROW_BLOCK not in doc.blocks
    assert S.ensure_arrow_block(doc) == S.ARROW_BLOCK
    assert S.ARROW_BLOCK in doc.blocks


# ── 3. 块名归一：三种写法都得认（否则合规图被判违规）──

@pytest.mark.parametrize("raw,expect", [
    ("_ARCHTICK", "archtick"),
    ("ARCHTICK", "archtick"),      # ezdxf override() 返回的符号名
    ("archtick", "archtick"),
    ("", "closedfilled"),          # 实心闭合箭头的符号名就是空串
    ("_CLOSEDFILLED", "closedfilled"),
    ("CLOSEDFILLED", "closedfilled"),
    (None, "closedfilled"),
])
def test_norm_arrow_name(raw, expect):
    assert G._norm_arrow_name(raw) == expect


def test_resolve_block_tries_symbol_and_block_names(tmp_path):
    """按符号名也能找到块：``ARCHTICK``/``""`` 都要能落到实际块上。"""
    doc = ezdxf.new("AC1032")
    S.ensure_archtick_block(doc)
    S.ensure_arrow_block(doc)
    assert G._resolve_block(doc, "ARCHTICK") is not None
    assert G._resolve_block(doc, "_ARCHTICK") is not None
    assert G._resolve_block(doc, "") is not None            # → _CLOSEDFILLED
    assert G._resolve_block(doc, "_CLOSEDFILLED") is not None
    assert G._resolve_block(doc, "不存在的块") is None


def test_arrow_width_uses_bbox_height_not_longest_edge():
    """箭头宽度 = 包围盒高，不是最长边（最长边是斜边 1.0135）。"""
    doc = ezdxf.new("AC1032")
    S.ensure_arrow_block(doc)
    dx, dy = G._block_extent(doc, S.ARROW_BLOCK)
    assert dx == pytest.approx(1.0)
    assert dy == pytest.approx(CLOSEDFILLED_WIDTH_PER_LEN, abs=1e-6)
    assert dy < 1.0, "别把斜边当宽度：最长边 1.013 会算出偏大的 b"


# ── 4. 审图判据：合规不报、违规必报 ──────────────────────

def test_compliant_linear_and_diameter_pass(tmp_path):
    assert _rules(_facts(_dim_doc("linear", TICK_OV), tmp_path)) == []
    assert _rules(_facts(_dim_doc("diameter", ARROW_OV), tmp_path)) == []


def test_old_slash_length_3_54mm_is_flagged(tmp_path):
    """旧写法 dimasz=2.5×scale 画出的斜短线是 3.54mm，超 §11.1.4 上限。"""
    rules = _rules(_facts(_dim_doc("linear", OLD_TICK_OV), tmp_path))
    assert "GB50001-11.1.4-DIMSIZE" in rules


def test_too_short_slash_is_flagged(tmp_path):
    ov = {"dimblk": "_ARCHTICK", "dimasz": 60.0}     # 纸面 1.70mm
    assert "GB50001-11.1.4-DIMSIZE" in _rules(_facts(_dim_doc("linear", ov), tmp_path))


def test_old_arrow_width_0_82mm_is_flagged(tmp_path):
    """旧写法箭头宽 0.82mm，低于 §11.1.4b 的 b ≥ 1mm。"""
    rules = _rules(_facts(_dim_doc("diameter", OLD_ARROW_OV), tmp_path))
    assert "GB50001-11.1.4-ARROWW" in rules


def test_linear_with_arrow_is_flagged(tmp_path):
    """线性尺寸误用箭头 → DIMTICK（§11.1.4 前半句）。"""
    ov = {"dimblk": S.ARROW_BLOCK, "dimasz": arrow_dimasz(S_SCALE)}
    assert "GB50001-11.1.4-DIMTICK" in _rules(_facts(_dim_doc("linear", ov), tmp_path))


def test_linear_with_default_arrow_symbol_is_flagged(tmp_path):
    """空符号名代表实心闭合箭头，不能因"取到空值"就退回样式当成斜短线。"""
    ov = {"dimblk": "", "dimasz": arrow_dimasz(S_SCALE)}
    assert "GB50001-11.1.4-DIMTICK" in _rules(_facts(_dim_doc("linear", ov), tmp_path))


def test_diameter_with_slash_is_flagged(tmp_path):
    """直径尺寸误用斜短线 → ARROWW（§11.1.4 后半句）。"""
    assert "GB50001-11.1.4-ARROWW" in _rules(_facts(_dim_doc("diameter", TICK_OV), tmp_path))


def test_dimtsz_oblique_stroke_counts_as_slash(tmp_path):
    """``dimtsz != 0`` 时 AutoCAD 直接用它画斜短线，不该被判成"用了箭头"。"""
    ov = {"dimblk": "", "dimasz": arrow_dimasz(S_SCALE), "dimtsz": 2.5 * S_SCALE}
    assert _rules(_facts(_dim_doc("linear", ov), tmp_path)) == []
    # 但长度仍要判：dimtsz 折算到纸面只有 1.77mm，低于 2mm
    ov2 = {"dimblk": "", "dimasz": arrow_dimasz(S_SCALE), "dimtsz": 1.77 * S_SCALE}
    assert "GB50001-11.1.4-DIMSIZE" in _rules(_facts(_dim_doc("linear", ov2), tmp_path))


# ── 5. 比例尺未知时不得按 1:1 判纸面尺寸 ────────────────

def test_unknown_scale_skips_paper_length_judgement(tmp_path):
    """★ 比例尺取不到时 ``scale`` 只是占位的 1.0，不代表图纸真是 1:1。

    不设这道闸，模型单位会被直接当纸面 mm 比大小：dimasz=88.388 的合规斜短线
    会算成"125.0mm"被误判超长——典型的"把取不到当默认值"式误杀。
    """
    doc = _dim_doc("linear", TICK_OV)
    p = os.path.join(str(tmp_path), "t.dxf")
    doc.saveas(p)
    doc2 = ezdxf.readfile(p)
    f = G.SheetFacts(path=p, name="t.dxf", doc=doc2, msp=doc2.modelspace())
    f.scale, f.scale_known = 1.0, False          # 未测到、标题栏也没声明
    assert _rules(f) == [], "比例尺未知时应跳过纸面尺寸判断，不该报 125.0mm"


def test_facts_flags_scale_known(tmp_path):
    """``_facts`` 要把"比例尺是否真的可知"记下来。"""
    doc, _ = new_drawing(scale=S_SCALE)
    p = os.path.join(str(tmp_path), "no_titleblock.dxf")
    doc.saveas(p)
    f = G._facts(p)
    assert f.scale_known is False, "没有标题栏声明值时不该当已知"


# ── 6. 规则号与知识库一一对应 ───────────────────────────

def test_rule_ids_registered_in_kb():
    """代码里能产出的规则号，知识库 ``complianceChecks`` 必须都有登记。"""
    known = {r.id for r in G.RULES} | {e for r in G.RULES for e in r.emits}
    kb = {c.get("id") for c in G.load_kb().get("complianceChecks", [])}
    assert known - kb == set(), f"代码有规则、知识库缺条目：{sorted(known - kb)}"


def test_no_orphan_checkable_rules_in_kb():
    """知识库声明可机检的条款，代码里必须有对应实现（否则是空头承诺）。"""
    known = {r.id for r in G.RULES} | {e for r in G.RULES for e in r.emits}
    orphan = [c.get("id") for c in G.load_kb().get("complianceChecks", [])
              if c.get("checkable") and c.get("id") not in known]
    assert orphan == [], f"知识库声明可机检但代码未实现：{orphan}"


def test_arrow_width_rule_is_registered():
    """§11.1.4b 是这一轮新增的判据，别只写在代码里、忘了进知识库。"""
    ids = {c.get("id") for c in G.load_kb().get("complianceChecks", [])}
    assert "GB50001-11.1.4-ARROWW" in ids
