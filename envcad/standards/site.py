"""总图制图要素 —— 指北针 / 坐标网格 / 厂区道路 / 用地红线。

## 制图依据

* GB/T 50001—2017《房屋建筑制图统一标准》第 7 章 符号 —— 指北针
* GB/T 50103—2010《总图制图标准》—— 坐标网格、道路、用地红线的线型与表示

## 与既有实现的关系

* **指北针**：此前在 ``drawings/t6_sewage_network.py``（空心三角，半径 3.5×scale）
  与 ``standards/energy_chemical/substation_pv_foundation.py``（实心三角，
  半径写死 2000 实物 mm、与比例尺无关）各手写一份，风格不一致，且都不满足
  "指针尾部宽度 = 圆直径的 1/8"的比例。此处统一实现，上述两处改为调用本模块。
* **道路**：``survey_gis/topo_symbols.sym_road`` 是**地形图**符号
  （GB/T 20257.1，落在"地形-交通"层，用于 1:500 以上测绘图），与厂区总平面的
  道路表达不是一回事，故不直接复用，另实现 :func:`draw_site_road`。
* **用地红线**：与 ``survey_gis/boundary.py`` 口径一致（粗实线）。

## 单位与比例约定

* ``scale`` 为出图比例分母（1:100 → 100.0）。
* 以"图上 mm"标注的参数为**纸面尺寸**，函数内部乘 ``scale`` 换成模型单位；
  其余参数为实物 mm。
* 全部函数返回自身占位 ``(x0, y0, x1, y1)``（模型单位），可直接喂给
  ``layout.AuxColumn`` 或 ``BBoxTracker.register``。
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from ezdxf.enums import TextEntityAlignment

from .annotate import _t, _estimate_text_width

BBox = Tuple[float, float, float, float]

# ── 图上尺寸默认值（纸面 mm）───────────────────────────────
D_NORTH_DIA = 24.0      # 指北针圆直径（GB/T 50001 §7 常用画法）
H_NORTH_LABEL = 4.0     # "N"字高
H_GRID_LABEL = 3.0      # 坐标网格注记字高
H_ROAD_LABEL = 3.0      # 道路宽度注记字高
H_BOUNDARY_LABEL = 4.0  # 用地红线注记字高

_GRID_LAYER = "网格"
_ROAD_EDGE_LAYER = "细实线"
_ROAD_CENTER_LAYER = "点画线"
_BOUNDARY_LAYER = "粗实线"


# ══════════════════════════════════════════════════════════
#  指北针（GB/T 50001—2017 §7）
# ══════════════════════════════════════════════════════════

def draw_north_arrow(msp, xy: Tuple[float, float], scale: float = 100.0, *,
                     diameter: float = D_NORTH_DIA, label: str = "N",
                     show_label: bool = True, label_h: float = H_NORTH_LABEL,
                     layer_circle: str = "细实线",
                     layer_pointer: str = "粗实线",
                     layer_label: str = "文字-标题",
                     tracker=None) -> BBox:
    """指北针。

    常用画法：细实线圆 + 实心指针，指针尖端指北、尾部在圆的下端；
    指针尾部宽度取圆直径的 1/8（直径 24mm 时即 3mm）。
    需用较大直径绘制时，尾部宽度按 1/8 自动跟随，不必另传。

    ``xy`` 为圆心（实物 mm）；``diameter`` 为图上 mm。
    返回占位 ``(x0,y0,x1,y1)``（含"N"字）。
    """
    s = float(scale)
    cx, cy = xy
    r = diameter / 2.0 * s
    tail_half = diameter / 16.0 * s      # 尾部半宽 = (直径/8)/2

    msp.add_circle((cx, cy), r, dxfattribs={"layer": layer_circle})
    # 实心指针：尖端在圆顶，尾边在圆底（SOLID 第三点自动重复成三角）
    msp.add_solid([(cx, cy + r), (cx - tail_half, cy - r),
                   (cx + tail_half, cy - r)],
                  dxfattribs={"layer": layer_pointer})

    top = cy + r
    if show_label:
        lh = label_h * s
        _t(msp, label, (cx, cy + r + lh * 1.1), lh,
           align=TextEntityAlignment.MIDDLE_CENTER, layer=layer_label)
        top = cy + r + lh * 2.2

    bb = (cx - r, cy - r, cx + r, top)
    if tracker is not None:
        tracker.register(*bb, margin=50)
    return bb


# ══════════════════════════════════════════════════════════
#  坐标网格（GB/T 50103—2010）
# ══════════════════════════════════════════════════════════

def _grid_positions(lo: float, hi: float, spacing: float) -> list:
    """从 lo 起按 spacing 步进到 hi（含两端附近），至少给出 lo、hi。"""
    if spacing <= 0 or hi <= lo:
        return [lo]
    out, n = [lo], 1
    while lo + n * spacing <= hi + 1e-6:
        out.append(lo + n * spacing)
        n += 1
        if n > 500:          # 防御：间距过小导致爆量
            break
    if out[-1] < hi - 1e-6:
        out.append(hi)
    return out


def _fmt_coord_steps(v_model: float, scale: float) -> str:
    """网格注记：给出距原点的**实物**距离（m，3 位小数）。"""
    return f"{v_model / 1000.0:.3f}"


def draw_coord_grid(msp, rect: BBox, spacing: float, scale: float = 100.0, *,
                    label_h: float = H_GRID_LABEL, show_labels: bool = True,
                    note: str = "", layer: str = _GRID_LAYER,
                    layer_text: str = "文字", tracker=None) -> BBox:
    """施工坐标网格。

    在 ``rect`` 范围内按 ``spacing``（实物 mm）绘制两组正交细线，并在上边、
    左边外侧注出各网格线到 ``rect`` 西南角的距离（实物 m，3 位小数）。

    **不自行指定 A/B 轴归属**：不同设计院对施工坐标轴的命名口径不一致，
    擅自标注会造成误解；确需轴号时用 ``note`` 显式写明起算与命名约定。

    返回占位 ``(x0,y0,x1,y1)``（含注记）。
    """
    s = float(scale)
    x0, y0, x1, y1 = rect
    lh = label_h * s

    xs = _grid_positions(x0, x1, spacing)
    ys = _grid_positions(y0, y1, spacing)
    for gx in xs:
        msp.add_line((gx, y0), (gx, y1), dxfattribs={"layer": layer})
    for gy in ys:
        msp.add_line((x0, gy), (x1, gy), dxfattribs={"layer": layer})

    tx0, ty0, tx1, ty1 = x0, y0, x1, y1
    if show_labels:
        for gx in xs:
            # 上侧注记抬到 lh*2.1：旧的 lh*1.1 使最左一列 "0.000" 与左侧
            # 最上一行 "23.500" 在西南角互压（实测叠印 221×150）。左侧注记列
            # 顶边在 y1+lh*0.8，上侧注记底边必须高过它。
            _t(msp, _fmt_coord_steps(gx - x0, s), (gx, y1 + lh * 2.1), lh,
               align=TextEntityAlignment.MIDDLE_CENTER, layer=layer_text)
        for gy in ys:
            _t(msp, _fmt_coord_steps(gy - y0, s), (x0 - lh * 0.7, gy), lh,
               align=TextEntityAlignment.MIDDLE_RIGHT, layer=layer_text)
        tx0 = x0 - lh * 0.7 - lh * 4.5      # 左侧注记估宽
        ty1 = y1 + lh * 3.0                 # 上侧注记顶边 = y1+2.1lh+0.8lh

    if note:
        _t(msp, note, (x0, ty1 + lh * 1.9), lh, layer=layer_text)
        ty1 += lh * 3.0

    bb = (tx0, ty0, tx1, ty1)
    if tracker is not None:
        tracker.register(*bb, margin=50)
    return bb


# ══════════════════════════════════════════════════════════
#  厂区道路（GB/T 50103—2010）
# ══════════════════════════════════════════════════════════

def _offset_segment(p0, p1, half: float):
    """把线段沿法向两侧各偏移 half，返回两条平行线端点对。"""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    lg = (dx * dx + dy * dy) ** 0.5
    if lg <= 0:
        return None
    nx, ny = -dy / lg * half, dx / lg * half
    return ((p0[0] + nx, p0[1] + ny), (p1[0] + nx, p1[1] + ny)), \
           ((p0[0] - nx, p0[1] - ny), (p1[0] - nx, p1[1] - ny))


def draw_site_road(msp, p0, p1, width: float, scale: float = 100.0, *,
                   code: str = "", show_center: bool = True,
                   label_h: float = H_ROAD_LABEL,
                   layer_edge: str = _ROAD_EDGE_LAYER,
                   layer_center: str = _ROAD_CENTER_LAYER,
                   tracker=None) -> Optional[BBox]:
    """厂区道路（直线段）。

    依比例尺用双线（路边线）表示，中心线用点画线；``width`` 为路宽（实物 mm），
    ``p0``/``p1`` 为道路中心线的两端（实物 mm）。
    ``code`` 非空时在路中注写（如"厂区道路 宽4.0m"）。

    返回占位；``p0`` 与 ``p1`` 重合时返回 None。
    """
    s = float(scale)
    off = _offset_segment(p0, p1, width / 2.0)
    if off is None:
        return None
    (a0, a1), (b0, b1) = off
    for pa, pb in ((a0, a1), (b0, b1)):
        msp.add_line(pa, pb, dxfattribs={"layer": layer_edge})
    if show_center:
        msp.add_line(p0, p1, dxfattribs={"layer": layer_center})

    xs = [a0[0], a1[0], b0[0], b1[0]]
    ys = [a0[1], a1[1], b0[1], b1[1]]
    bb = [min(xs), min(ys), max(xs), max(ys)]

    if code:
        mx, my = (p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        rotated = abs(dy) > abs(dx)        # 竖向道路：文字转 90°，顺着路走
        _t(msp, code, (mx, my), label_h * s,
           align=TextEntityAlignment.MIDDLE_CENTER, layer="文字",
           rotation=90.0 if rotated else 0.0)
        bb[1] = min(bb[1], my - label_h * s)
        bb[3] = max(bb[3], my + label_h * s)

    bb = tuple(bb)
    if tracker is not None:
        tracker.register(*bb, margin=50)
    return bb


def draw_site_ring_road(msp, rect: BBox, width: float, scale: float = 100.0, *,
                        code: str = "", label_h: float = H_ROAD_LABEL,
                        layer_edge: str = _ROAD_EDGE_LAYER,
                        layer_center: str = _ROAD_CENTER_LAYER,
                        tracker=None) -> Optional[BBox]:
    """环形道路（厂区消防/运输环路）。

    ``rect`` 为环路**外边线** (x0,y0,x1,y1)，向内收 ``width``（实物 mm）得内边线；
    中心线取两者中位线（点画线）。收完后内边线宽高非正时返回 None。
    """
    s = float(scale)
    x0, y0, x1, y1 = rect
    ix0, iy0, ix1, iy1 = x0 + width, y0 + width, x1 - width, y1 - width
    if ix1 <= ix0 or iy1 <= iy0:
        return None

    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True,
                       dxfattribs={"layer": layer_edge})
    msp.add_lwpolyline([(ix0, iy0), (ix1, iy0), (ix1, iy1), (ix0, iy1)], close=True,
                       dxfattribs={"layer": layer_edge})
    h = width / 2.0
    msp.add_lwpolyline([(x0 + h, y0 + h), (x1 - h, y0 + h),
                        (x1 - h, y1 - h), (x0 + h, y1 - h)], close=True,
                       dxfattribs={"layer": layer_center})

    bb = (x0, y0, x1, y1)
    if code:
        _t(msp, code, (x0 + (x1 - x0) / 2.0, y0 + width * 0.5), label_h * s,
           align=TextEntityAlignment.MIDDLE_CENTER, layer="文字")
    if tracker is not None:
        tracker.register(*bb, margin=50)
    return bb


# ══════════════════════════════════════════════════════════
#  用地红线 / 围墙（GB/T 50103—2010）
# ══════════════════════════════════════════════════════════

def draw_site_boundary(msp, rect: BBox, scale: float = 100.0, *,
                       label: str = "厂区用地红线",
                       label_h: float = H_BOUNDARY_LABEL,
                       wall_offset: Optional[float] = None,
                       layer: str = _BOUNDARY_LAYER,
                       layer_wall: str = "中实线",
                       tracker=None) -> BBox:
    """厂区用地红线（粗实线，与 ``survey_gis/boundary.py`` 口径一致）。

    ``wall_offset`` 非空时，在红线内侧再画一道围墙（中实线，实物 mm 偏移），
    并按"站区围墙"注记。
    """
    s = float(scale)
    x0, y0, x1, y1 = rect
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True,
                       dxfattribs={"layer": layer})

    lh = label_h * s
    has_wall = False
    if wall_offset:
        wx0, wy0 = x0 + wall_offset, y0 + wall_offset
        wx1, wy1 = x1 - wall_offset, y1 - wall_offset
        if wx1 > wx0 and wy1 > wy0:
            msp.add_lwpolyline([(wx0, wy0), (wx1, wy0), (wx1, wy1), (wx0, wy1)],
                               close=True, dxfattribs={"layer": layer_wall})
            has_wall = True

    # ── 注记：红线 / 围墙**排在同一行**，列在红线之上 ──
    # 旧写法把"站区围墙"贴着围墙顶边放（y1-500+1.1lh），恰好落进坐标网格上侧
    # 注记行与左侧注记列交会的西南角，实测与 "0.000" 水平间距仅 69mm，纸面上
    # 已经粘成一个词。改为一律排到网格注记之上的一整行，从左往右接排，
    # 谁都不再与网格轴注记争位。
    # 注记以基线定位、文字向上长，返回值必须按 ascent（lh*1.6）抬高。
    row_y = y1 + lh * 3.0
    x_cursor = x0
    if label:
        _t(msp, label, (x_cursor, row_y), lh,
           align=TextEntityAlignment.LEFT, layer="文字-标题")
        x_cursor += _estimate_text_width(str(label), lh) + lh * 1.2
    if has_wall:
        _t(msp, "站区围墙", (x_cursor, row_y), lh,
           align=TextEntityAlignment.LEFT, layer="文字")
    top = row_y + lh * 1.6

    bb = (x0, y0, x1, top)
    if tracker is not None:
        tracker.register(*bb, margin=50)
    return bb
