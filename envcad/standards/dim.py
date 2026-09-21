"""尺寸公差标注 v1.1（GB/T 1800.2—2020）。

【注意：dim ≠ dimensions】本模块只做"公差"（Tolerance），不管"标注"（Annotation）。
如需坐标标注/角度标注/链式基线标注/半径直径引出，请用 `standards.dimensions`。

基于 ezdxf DimStyle override 和 MText 堆叠，实现:
  * 对称公差（±偏差）
  * 极限偏差（上标/下标堆叠）
  * 配合公差（H7/g6、H8/f7 等），全直径段查表（≤3 ~ 500mm）
  * 显式偏差参数（Agent 搜索后传入，绕过查表）

纯 ezdxf，零新依赖。
"""
from __future__ import annotations

import math
from typing import Optional, Tuple, List, Union

from ezdxf.enums import TextEntityAlignment
from ..utils import _r, _tri


# ══════════════════════════════════════════════════════════
#  GB/T 1800.2—2020 公差数据（全直径段）
# ══════════════════════════════════════════════════════════

# 基本尺寸分段 (mm): [low, high)
_DIAMETER_RANGES: List[Tuple[float, float]] = [
    (0,  3),  (3,  6),  (6,  10), (10, 18), (18, 30),
    (30, 50), (50, 80), (80, 120),(120,180),(180,250),
    (250,315),(315,400),(400,500),
]

# IT 标准公差数值 (μm)，索引对应上述直径段
_IT: dict = {
    5:  [4,    5,    6,    8,    9,    11,   13,   15,   18,   20,   23,   25,   27],
    6:  [6,    8,    9,    11,   13,   16,   19,   22,   25,   29,   32,   36,   40],
    7:  [10,   12,   15,   18,   21,   25,   30,   35,   40,   46,   52,   57,   63],
    8:  [14,   18,   22,   27,   33,   39,   46,   54,   63,   72,   81,   89,   97],
    9:  [25,   30,   36,   43,   52,   62,   74,   87,   100,  115,  130,  140,  155],
    10: [40,   48,   58,   70,   84,   100,  120,  140,  160,  185,  210,  230,  250],
}

# 轴基本偏差 (μm)；负值为上偏差(es)，正值为下偏差(ei)
# g/h 类：负 es；k~p 类：正 ei
_SHAFT_DEV: dict = {
    "g": [-2,  -4,  -5,  -6,  -7,  -9,  -10, -12, -14, -15, -17, -18, -20],
    "h": [ 0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0],
    "k": [ 0,   1,   1,   1,   2,   2,   2,   3,   3,   4,   4,   4,   5],
    "m": [ 2,   4,   6,   7,   8,   9,   11,  13,  15,  17,  20,  21,  23],
    "n": [ 4,   8,  10,  12,  15,  17,  20,  23,  27,  31,  34,  37,  40],
    "p": [ 6,  12,  15,  18,  22,  26,  32,  37,  43,  50,  56,  62,  68],
    "f": [-6, -10, -13, -16, -20, -25, -30, -36, -43, -50, -56, -62, -68],
    "e": [-14,-20, -25, -32, -40, -50, -60, -72, -85,-100,-110,-125,-135],
    "d": [-20,-30, -40, -50, -65, -80,-100,-120,-145,-170,-190,-210,-230],
}


def _find_range_idx(diameter: float) -> int:
    """根据基本直径找到尺寸段索引。"""
    for i, (lo, hi) in enumerate(_DIAMETER_RANGES):
        if lo < diameter <= hi:
            return i
    # fallback: 取最接近的段
    if diameter <= 3:
        return 0
    return len(_DIAMETER_RANGES) - 1


def _shaft_tolerance(letter: str, it_grade: int, idx: int) -> Tuple[float, float]:
    """计算轴的上下偏差 (es, ei)，单位 μm。"""
    it = _IT.get(it_grade, _IT[7])[idx]

    if letter == "j" and it_grade == 6:  # js6 对称
        half = it / 2
        return (+half, -half)
    if letter == "j" and it_grade == 7:
        half = it / 2
        return (+half, -half)

    dev = _SHAFT_DEV.get(letter)
    if dev is None:
        return (0.0, -float(it))

    d = dev[idx]

    if letter in ("g", "h", "f", "e", "d"):
        # 负向偏差：es = d，ei = d - IT
        es = float(d)
        ei = es - it
    else:
        # 正向偏差（k, m, n, p）：ei = d，es = d + IT
        ei = float(d)
        es = ei + it

    return (es, ei)


def lookup_fit(basic_diameter: float, fit_code: str
               ) -> Tuple[float, float, float, float]:
    """根据基本直径和配合代号查 GB/T 1800.2 公差。

    参数:
        basic_diameter: 基本尺寸 (mm)，如 20, 50
        fit_code: 配合代号，如 "H7/g6", "H8/f7"

    返回:
        (hole_es_μm, hole_ei_μm, shaft_es_μm, shaft_ei_μm)
        偏差单位均为 μm（微米）

    示例:
        lookup_fit(20, "H7/g6") → (21000, 0, -7000, -20000)
        即孔 +21μm/0，轴 -7μm/-20μm
    """
    parts = fit_code.split("/")
    hole_code = parts[0].strip()
    shaft_code = parts[1].strip() if len(parts) > 1 else ""

    idx = _find_range_idx(basic_diameter)

    # ── 孔 ──
    hole_letter = hole_code[0].upper()
    hole_it = int(hole_code[1:])

    if hole_letter == "H":
        hole_es = float(_IT.get(hole_it, _IT[7])[idx])
        hole_ei = 0.0
    else:
        hole_es = 0.0
        hole_ei = 0.0

    # ── 轴 ──
    shaft_letter = shaft_code[0].lower()
    shaft_it = int(shaft_code[1:])
    shaft_es, shaft_ei = _shaft_tolerance(shaft_letter, shaft_it, idx)

    return (hole_es, hole_ei, shaft_es, shaft_ei)


def _fmt_dev(um_value: float) -> str:
    """将 μm 偏差值格式化为 mm 字符串（带符号）。"""
    mm = um_value / 1000.0
    return f"{mm:+.3f}"


# ─── 内部辅助 ───────────────────────────────────────────

# ══════════════════════════════════════════════════════════
#  DIM 标注函数
# ══════════════════════════════════════════════════════════

def add_dim_style_tolerance(doc, dimstyle_name: str,
                             upper: str, lower: str,
                             height_factor: float = 0.7) -> str:
    """为已有 DimStyle 添加公差后缀（dimstyle override）。"""
    if dimstyle_name not in doc.dimstyles:
        return dimstyle_name

    dim = doc.dimstyles.get(dimstyle_name)
    dim.dxf.dimtol = 1
    dim.dxf.dimtp = abs(float(upper)) if upper else 0
    dim.dxf.dimtm = abs(float(lower)) if lower else 0
    dim.dxf.dimtfac = height_factor

    return dimstyle_name


def _dim_base_override(s: float) -> dict:
    """真实 DIMENSION 实体所需的样式变量基线（图纸 mm → 模型单位）。

    本引擎的模型空间按 1:1 实物尺寸作图，图纸上的 mm 量需乘出图比例倒数 s。
    """
    return {
        "dimtxt": 3.5 * s,      # 标注文字高 3.5mm（GB/T 14691）
        "dimasz": 2.5 * s,      # 箭头长度 2.5mm
        "dimexe": 2.0 * s,      # 尺寸界线超出尺寸线 2mm
        "dimexo": 1.2 * s,      # 尺寸界线起点偏移
        "dimgap": 1.5 * s,      # 文字与尺寸线间隙
        "dimdsep": ord("."),    # GB：小数点用 "."（ezdxf 默认 44=","）
        "dimzin": 8,            # 标注文字去尾零、保留前导零
        "dimtzin": 8,           # 公差文字同上（否则下偏差 0 出成 "0.000"）
    }


def ensure_dimstyle(doc, scale: float, name: str = "") -> str:
    """返回一个可用的标注样式名。

    优先用调用方指定名 → 否则 `GB-DIM-{scale}`（缺失则现场创建）。
    裸 ezdxf 文档（无 HZ 文字样式）也能安全使用：会先补文字样式。
    """
    from .styles import setup_text_styles, setup_dimstyles

    if "HZ" not in doc.styles:
        try:
            setup_text_styles(doc)
        except Exception:
            pass

    want = name or f"GB-DIM-{int(scale)}"
    if want in doc.dimstyles:
        return want
    try:
        got = setup_dimstyles(doc, scale)
        if got in doc.dimstyles:
            return got
    except Exception:
        pass
    return "Standard" if "Standard" in doc.dimstyles else want


def _apply_tolerance(ov: dict, upper, lower, sym: bool) -> None:
    """把上/下偏差转成 DIMSTYLE 级公差变量（ezdxf 原生堆叠，勿用文字覆盖）。"""
    def num(v):
        try:
            return abs(float(str(v).lstrip("+")))
        except (TypeError, ValueError):
            return 0.0

    u, l = num(upper), num(lower)
    if sym and upper:
        ov.update(dimtol=2, dimtp=u, dimtm=u)
    else:
        ov.update(dimtol=1, dimtp=u, dimtm=l)

    dec = 2
    for v in (upper, lower):
        t = str(v).lstrip("+")
        if "." in t:
            dec = max(dec, len(t.split(".")[1]))
    ov.update(dimtdec=dec, dimtfac=1.0)


def draw_linear_dimension(msp, p1: Tuple[float, float], p2: Tuple[float, float],
                          offset: float = 10.0, scale: float = 100.0,
                          dimstyle: str = "", text: str = "",
                          upper: str = "", lower: str = "", sym: bool = False,
                          layer: str = "尺寸标注", tracker=None,
                          angle: Optional[float] = None):
    """用**真实 DIMENSION 实体**绘制线性标注（GB/T 4458.4）。

    与手工画线相比：产出可被 CAD 识别、可关联测量的 DIMENSION 实体，
    箭头/尺寸线/尺寸界线/公差堆叠全部由标注样式原生渲染，且
    `ezdxf.bbox` 会递归进其匿名块，幅面重选时能正确计入占位。
    """
    s = float(scale)
    x1, y1 = _r(*p1)
    x2, y2 = _r(*p2)
    off = offset * s

    dx, dy = x2 - x1, y2 - y1
    horizontal = abs(dx) >= abs(dy)
    if angle is None:
        angle = 0.0 if horizontal else 90.0

    # base = 尺寸线经过的点，决定标注放在构件哪一侧
    if horizontal:
        base = ((x1 + x2) / 2, min(y1, y2) - off)
    else:
        base = (min(x1, x2) - off, (y1 + y2) / 2)

    style = ensure_dimstyle(msp.doc, s, dimstyle)
    ov = _dim_base_override(s)
    if upper or lower:
        _apply_tolerance(ov, upper, lower, sym)

    dim = msp.add_linear_dim(
        base=base, p1=(x1, y1), p2=(x2, y2), angle=angle,
        dimstyle=style, override=ov,
        text=text if text else "<>",
        dxfattribs={"layer": layer},
    )
    dim.render()

    mx, my = base
    if tracker is not None:
        tracker.register(mx - 6 * s, my - 4 * s,
                         mx + 6 * s, my + 6 * s, margin=30)
    return (mx, my)


def _fix_diameter_arrow2(doc, dim, center: Tuple[float, float]) -> bool:
    """修正 ezdxf 直径标注 defpoint4 一侧箭头与另一端同向的问题。

    `ezdxf.render.dim_radius.RadiusDimension.add_arrow()` 用**同一个**
    `dim_line_angle (+180)` 计算两端箭头旋转，而直径标注的两端在对径位置，
    于是第二端箭头朝向反了（两端箭头同向）。这里按几何关系重算该箭头旋转，
    并把被它带偏的尺寸线端点、引出线端点一并校正。

    仅在检测到"两端箭头同向"时动手；文字在圆内时 ezdxf 本就对称，直接放行。
    返回是否做了修正。
    """
    name = dim.dxf.geometry
    if not name or name not in doc.blocks:
        return False
    blk = doc.blocks.get(name)

    from ezdxf.math import Vec2

    try:
        p1 = Vec2(dim.dxf.defpoint)
        p2 = Vec2(dim.dxf.defpoint4)
    except Exception:
        return False
    ctr = Vec2(_r(*center))

    inserts, lines = [], []
    for e in blk:
        t = e.dxftype()
        if t == "INSERT":
            inserts.append(e)
        elif t == "LINE":
            lines.append(e)

    def _near(a: Vec2, b: Vec2, tol: float) -> bool:
        return (a - b).magnitude <= tol

    span = max((p1 - ctr).magnitude, 1.0)
    tol = span * 1e-4

    at_p1 = [e for e in inserts if _near(Vec2(e.dxf.insert), p1, tol)]
    at_p2 = [e for e in inserts if _near(Vec2(e.dxf.insert), p2, tol)]
    if len(at_p1) != 1 or len(at_p2) != 1:
        return False

    a1, a2 = at_p1[0], at_p2[0]
    rot1 = float(a1.dxf.get("rotation", 0.0))
    rot2 = float(a2.dxf.get("rotation", 0.0))
    # 两端箭头应相差 180°；差值接近 0/360 即为同向 bug
    delta = abs(((rot2 - rot1) % 360.0) - 180.0)
    if delta < 90.0:
        return False

    arrow_len = float(a2.dxf.get("xscale", 0.0) or 0.0)
    if arrow_len <= 0.0:
        return False

    u = (p2 - ctr)
    if u.magnitude <= 0.0:
        return False
    u = u.normalize()

    # 箭头尖端仍在 p2，箭体应指向圆心：rot = angle(-u) - 180
    a2.dxf.rotation = math.degrees(math.atan2(-u.y, -u.x)) - 180.0

    old_cp = p2 + u * arrow_len      # ezdxf 用错误旋转算出的旧连接点
    new_cp = p2 - u * arrow_len      # 修正后的箭头根部

    for ln in lines:
        for attr, other_attr in (("start", "end"), ("end", "start")):
            p = Vec2(ln.dxf.get(attr))
            if not _near(p, old_cp, tol):
                continue
            o = Vec2(ln.dxf.get(other_attr))
            # 另一端在圆心对侧 → 尺寸线；否则是引向文字的引出线
            if (o - ctr).dot(u) < 0.0:
                ln.dxf.set(attr, new_cp)
            else:
                ln.dxf.set(attr, p2)
            break
    return True


def draw_diameter_dimension(msp, center: Tuple[float, float], radius: float,
                            angle: float = 45.0, scale: float = 100.0,
                            dimstyle: str = "", text: str = "",
                            prefix: str = "",
                            layer: str = "尺寸标注", tracker=None):
    """用**真实 DIMENSION 实体**绘制直径标注（⌀，GB/T 4458.4）。

    与线性标注的差别：直径 DIMENSION 的 defpoint/defpoint4 是圆上对径两点。
    直径前缀**不要自己加**——`ezdxf.render.dim_diameter` 的 `DiameterDimension`
    会自动带上 `Ø`（存盘时转义成 DXF 惯例的 `%%c`），自己再加会出现
    `⌀%%c6000` 这类重复前缀。
    """
    s = float(scale)
    cx, cy = _r(*center)
    r_ = float(radius)

    style = ensure_dimstyle(msp.doc, s, dimstyle)
    ov = _dim_base_override(s)

    # 首个 "<>" 会被替换为实测直径；prefix 仅在需要非 ⌀ 前缀时才传
    label = text if text else (prefix + "<>")

    dim = msp.add_diameter_dim(
        center=(cx, cy), radius=r_, angle=angle,
        dimstyle=style, override=ov,
        text=label, dxfattribs={"layer": layer},
    )
    dim.render()
    try:
        # add_diameter_dim 返回的是 DimStyleOverride，需取回 DIMENSION 实体本体
        _fix_diameter_arrow2(msp.doc, getattr(dim, "dimension", dim), (cx, cy))
    except Exception:
        pass  # 修正失败不影响主几何：直径标注本身仍可用

    # 文字大致落在背离圆心 45° 方向、距圆心约 0.6r 处
    rad = math.radians(angle)
    tx = cx + r_ * 0.6 * math.cos(rad)
    ty = cy + r_ * 0.6 * math.sin(rad)
    if tracker is not None:
        tracker.register(tx - 6 * s, ty - 4 * s,
                         tx + 6 * s, ty + 6 * s, margin=30)
    return (tx, ty)


def draw_dimension(msp, p1: Tuple[float, float], p2: Tuple[float, float],
                   offset: float = 10.0, scale: float = 100.0,
                   dimstyle: str = "Standard",
                   text: str = "", upper: str = "", lower: str = "",
                   sym: bool = False,
                   layer: str = "尺寸标注",
                   tracker=None):
    """绘制带公差的线性标注（真实 DIMENSION 实体，失败回退手工画线）。

    参数:
        p1, p2: 标注起止点
        offset: 尺寸线偏移距离（图纸 mm）
        scale: 出图比例倒数
        text: 自定义标注文字（空则自动计算距离）
        upper: 上偏差（如 "+0.018"）
        lower: 下偏差（如 "0"）
        sym: True = 对称公差（用 ± 符号）
    """
    try:
        return draw_linear_dimension(
            msp, p1, p2, offset=offset, scale=scale,
            dimstyle=("" if dimstyle in ("", "Standard") else dimstyle),
            text=text, upper=upper, lower=lower, sym=sym,
            layer=layer, tracker=tracker)
    except Exception as _e:
        print(f"[WARNING] dim.py: 真实 DIMENSION 生成失败，回退手工标注：{_e}")
        return _draw_dimension_manual(
            msp, p1, p2, offset=offset, scale=scale, dimstyle=dimstyle,
            text=text, upper=upper, lower=lower, sym=sym,
            layer=layer, tracker=tracker)


def _draw_dimension_manual(msp, p1: Tuple[float, float], p2: Tuple[float, float],
                           offset: float = 10.0, scale: float = 100.0,
                           dimstyle: str = "Standard",
                           text: str = "", upper: str = "", lower: str = "",
                           sym: bool = False,
                           layer: str = "尺寸标注",
                           tracker=None):
    """手工画线版线性标注（回退路径，不产 DIMENSION 实体）。"""
    s = scale
    x1, y1 = _r(*p1)
    x2, y2 = _r(*p2)

    off = offset * s

    dx, dy = x2 - x1, y2 - y1
    if abs(dx) > abs(dy):
        p3 = (x1, y1 - off)
        p4 = (x2, y2 - off)
    else:
        p3 = (x1 - off, y1)
        p4 = (x2 - off, y2)

    msp.add_line(p3, p4, dxfattribs={"layer": layer})
    msp.add_line(p1, p3, dxfattribs={"layer": layer})
    msp.add_line(p2, p4, dxfattribs={"layer": layer})

    mx = (p3[0] + p4[0]) / 2
    my = (p3[1] + p4[1]) / 2
    txt_h = 3.0 * s

    dist_mm = math.hypot(x2 - x1, y2 - y1)
    base_text = text if text else f"{dist_mm:.1f}"

    if sym and upper:
        tol_text = f"{base_text}±{upper.lstrip('+')}"
        t = msp.add_text(tol_text, dxfattribs={
            "layer": "文字", "height": txt_h, "style": "ENG",
        })
        t.set_placement((mx, my + 1.5 * s),
                        align=TextEntityAlignment.MIDDLE_CENTER)
    elif upper or lower:
        upper_clean = upper.lstrip("+") or "0"
        lower_clean = lower.lstrip("+") or "0"

        tol_str = f"{base_text}\\S{upper_clean}^{lower_clean};"
        t = msp.add_mtext(tol_str, dxfattribs={
            "layer": "文字", "style": "ENG", "char_height": txt_h,
        })
        t.set_location(insert=(mx, my + 1.5 * s),
                       attachment_point=5)
        t.dxf.width = 15 * s
    else:
        t = msp.add_text(base_text, dxfattribs={
            "layer": "文字", "height": txt_h, "style": "ENG",
        })
        t.set_placement((mx, my + 1.5 * s),
                        align=TextEntityAlignment.MIDDLE_CENTER)

    if tracker is not None:
        tracker.register(mx - 6 * s, my - 4 * s,
                         mx + 6 * s, my + 6 * s, margin=30)

    return (mx, my)


# ══════════════════════════════════════════════════════════
#  配合公差标注（支持查表 + 显式传入）
# ══════════════════════════════════════════════════════════

def draw_fit_annotation(msp, point, base_size: str, fit_code: str,
                         scale: float = 100.0,
                         leader_dir: Tuple[float, float] = (1, 1),
                         layer: str = "尺寸标注",
                         # 显式偏差 (mm)，Agent 搜索后传入，绕过查表
                         hole_es: Optional[float] = None,
                         hole_ei: Optional[float] = None,
                         shaft_es: Optional[float] = None,
                         shaft_ei: Optional[float] = None,
                         basic_diameter: Optional[float] = None,
                         tracker=None):
    """绘制配合公差引出标注。

    参数:
        point: 标注点
        base_size: 基本尺寸文本（如 "φ20"）
        fit_code: 配合代号（如 "H7/g6", "H8/f7"）

    ── 查表模式（默认）──
        自动从 base_size 解析直径，查 GB/T 1800.2 表。
        覆盖 ≤3 ~ 500mm 全直径段。

    ── 显式模式（Agent 搜索后使用）──
        传入 hole_es/hole_ei/shaft_es/shaft_ei（单位 mm），
        将完全绕过查表。
        示例: hole_es=0.021, hole_ei=0, shaft_es=-0.007, shaft_ei=-0.020

    basic_diameter: 基本直径 mm（查表用；默认从 base_size 解析）
    """
    s = scale
    tx, ty = _r(*point)

    parts = fit_code.split("/")
    hole = parts[0] if len(parts) > 0 else ""
    shaft = parts[1] if len(parts) > 1 else ""

    # ── 确定偏差值 ──
    if all(v is not None for v in [hole_es, hole_ei, shaft_es, shaft_ei]):
        # 显式模式：Agent 搜索后传入，直接使用
        hes, hei = hole_es, hole_ei       # type: ignore
        ses, sei = shaft_es, shaft_ei     # type: ignore
    else:
        # 查表模式
        if basic_diameter is None:
            # 尝试从 base_size 中提取直径
            import re
            nums = re.findall(r'\d+\.?\d*', base_size)
            basic_diameter = float(nums[0]) if nums else 10.0

        h_es_um, h_ei_um, s_es_um, s_ei_um = lookup_fit(
            basic_diameter, fit_code)
        hes = h_es_um / 1000.0
        hei = h_ei_um / 1000.0
        ses = s_es_um / 1000.0
        sei = s_ei_um / 1000.0

    # ── 指引线 ──
    dx, dy = leader_dir
    L = 12 * s
    bx = tx + dx * L
    by = ty + dy * L
    msp.add_line((tx, ty), (bx, by), dxfattribs={"layer": layer})

    h_len = 8 * s
    hx = bx + (h_len if dx >= 0 else -h_len)
    msp.add_line((bx, by), (hx, by), dxfattribs={"layer": layer})

    # ── 配合标注文字 ──
    txt_h = 2.8 * s
    fit_text = f"{base_size}{fit_code}"
    txt_dir = 1 if dx >= 0 else -1
    txt_x = hx + txt_dir * 2 * s
    txt_y = by + 1.5 * s

    t = msp.add_text(fit_text, dxfattribs={
        "layer": "文字", "height": txt_h, "style": "ENG",
    })
    align = TextEntityAlignment.LEFT if dx >= 0 else TextEntityAlignment.RIGHT
    t.set_placement((txt_x, txt_y), align=align)

    # ── 详细公差值 ──
    detail_y = by - 0.5 * s
    detail_text = (f"孔 ES{_fmt_dev(hes)} EI{_fmt_dev(hei)}  "
                   f"轴 es{_fmt_dev(ses)} ei{_fmt_dev(sei)}")
    t = msp.add_text(detail_text, dxfattribs={
        "layer": "文字", "height": 2.0 * s, "style": "ENG",
    })
    t.set_placement((txt_x, detail_y), align=align)

    if tracker is not None:
        tracker.register(tx - 2 * s, by - 6 * s,
                         txt_x + 15 * s, txt_y + 6 * s, margin=30)

    return (txt_x, txt_y)
