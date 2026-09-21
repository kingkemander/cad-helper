"""国标图框与标题栏（GB/T 14689—2008 幅面 + GB/T 50001—2017 标题栏）。

支持 A0~A4 任意图幅与横式/纵式，默认 A2 横式，向后兼容。

约定：modelspace 按 1:1 实际尺寸（mm）绘制实物；图框按出图比例放大
（A3 横式 = 420×297，乘以 scale）。出图 1:1 即得正确比例图纸。

图幅选择（二选一）：
  * 显式：``FrameInfo(size="A1", orientation="landscape")``；
  * 进程级默认：调用前用 ``set_default_paper_size("A2")`` /
    ``set_default_orientation("portrait")`` 设置，所有未显式指定尺寸的
    绘图自动采用。未设置时回退到 A2 横式。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from ezdxf.enums import TextEntityAlignment

from ..engine.dxf_base import save_dxf

# ── 标准图幅（GB/T 14689）：(长边, 短边) mm，含装订边 ──
PAPER_BASE = {
    "A0": (1189.0, 841.0),
    "A1": (841.0, 594.0),
    "A2": (594.0, 420.0),
    "A3": (420.0, 297.0),
    "A4": (297.0, 210.0),
}
# 历史别名，保留以兼容旧调用（survey_gis 等直接引用 A3_W/A3_H）
A3_W, A3_H = PAPER_BASE["A3"]

# ── 标准留边（GB/T 14689-2008 留装订边）：(a装订边, c其他三边) mm ──
#   a 全幅面 = 25；c: A0/A1/A2 = 10，A3/A4 = 5
#   优先从本地标准知识库 standards_kb.json 读取（见 _load_margins_from_kb），
#   知识库缺字段或不可用时回退到下方内置常量。
MARGIN = {
    "A0": (25.0, 10.0),
    "A1": (25.0, 10.0),
    "A2": (25.0, 10.0),
    "A3": (25.0, 5.0),
    "A4": (25.0, 5.0),
}
# 历史别名（兼容旧调用）
MARGIN_L = 25.0   # 装订边 a
MARGIN_O = 10.0   # 其余边 c（A2 基准值）
TITLE_W, TITLE_H = 180.0, 56.0  # 标题栏基准尺寸（× tb 缩放）

# 幅面搜索顺序：由小到大，取第一个装得下的
PAPER_ORDER = ("A4", "A3", "A2", "A1", "A0")

# 内容与内框之间的最小留白（图纸 mm）
PAD_PAPER_MM = 6.0

# 附表图层：技术要求框 / 技术特性表 / 设备材料表 / 图例框。
# 与"图框"层分开：图框是幅面边界（出图前会重选重画），附表是内容的一部分。
AUX_LAYER = "附表"

# 图幅家具层：图幅边界 + 标题栏。出图前整层删除重画，且不计入内容外包络。
SHEET_LAYERS = ("图框", "标题栏")

# ── 优先从本地标准知识库补齐留边（GB/T 14689）──
_KB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "standards_kb.json")


def _load_margins_from_kb():
    """从本地标准知识库 standards_kb.json 读取 GB/T 14689 留边，补齐
    a(装订边)/c(其他三边) 的逐幅面数值；缺失或解析失败时静默回退内置常量。
    """
    try:
        with open(_KB_PATH, "r", encoding="utf-8") as fh:
            kb = json.load(fh)
    except Exception:
        return
    for r in kb.get("rules", {}).get("Sheet", []):
        if str(r.get("std", "")).startswith("GB/T 14689"):
            p = r.get("param", {})
            a = float(p.get("border_a_mm", 25.0))
            c_map = p.get("border_c_mm", {})
            for size in PAPER_BASE:
                if isinstance(c_map, dict):
                    c = float(c_map.get(size, 10.0))
                else:
                    c = float(c_map)
                MARGIN[size] = (a, c)
            return


_load_margins_from_kb()

# 标题栏随图幅放大系数（大图标题栏放大更清晰，小图不喧宾夺主）
TITLE_SCALE = {"A0": 1.8, "A1": 1.5, "A2": 1.25, "A3": 1.0, "A4": 1.0}

# 进程级默认图幅/方向（CLI 通过 set_default_* 设置；未设置则用 A2 横式）
_DEFAULT_PAPER_SIZE = "A2"
_DEFAULT_ORIENTATION = "landscape"


def set_default_paper_size(size: str) -> None:
    """设置进程级默认图幅（"A0"~"A4"）。非法值忽略。"""
    global _DEFAULT_PAPER_SIZE
    if str(size).upper() in PAPER_BASE:
        _DEFAULT_PAPER_SIZE = str(size).upper()


def get_default_paper_size() -> str:
    return _DEFAULT_PAPER_SIZE


def set_default_orientation(orientation: str) -> None:
    """设置进程级默认方向：'landscape'（横式，默认）或 'portrait'（纵式）。"""
    global _DEFAULT_ORIENTATION
    o = str(orientation).lower()
    if o in ("landscape", "portrait"):
        _DEFAULT_ORIENTATION = o


def get_default_orientation() -> str:
    return _DEFAULT_ORIENTATION


def _resolve_sheet(size, orientation):
    """返回 (W, H, tb_scale, size)，W/H 为图幅长/短边按方向展开后的 mm，
    size 为实际采用的幅面代号。

    size/orientation 为空时回退到进程级默认（再回退 _DEFAULT_PAPER_SIZE 横式）。
    """
    s = (size or "").upper() or _DEFAULT_PAPER_SIZE
    if s not in PAPER_BASE:
        s = _DEFAULT_PAPER_SIZE
    o = (orientation or "").lower() or _DEFAULT_ORIENTATION
    long, short = PAPER_BASE[s]
    W, H = (long, short) if o == "landscape" else (short, long)
    return W, H, TITLE_SCALE.get(s, 1.0), s


@dataclass
class FrameInfo:
    title: str = "未命名图纸"
    drawing_no: str = "ENV-00"
    scale_str: str = "1:100"
    designer: str = ""
    checker: str = ""
    auditor: str = ""
    project: str = "环保工程"
    unit: str = "设计单位"
    date: str = "2026.07"
    size: str = None        # 图幅：A0~A4；None=进程级默认（默认 A2）
    orientation: str = None  # 方向：landscape 横式 / portrait 纵式；None=默认
    overflow_mm: float = 0.0  # 出图自检：内容超出内框的量（mm），0=未超出


def draw_frame(doc, scale: float, info: FrameInfo, tracker=None, origin=(0.0, 0.0)):
    """绘制国标图框 + 标题栏，返回内框范围 (x0,y0,x1,y1)（实物坐标系）。

    v1.5: 图幅由 ``info.size`` / ``info.orientation`` 或进程级默认
    （``set_default_paper_size`` / ``set_default_orientation``）决定，
    默认 A2 横式，向后兼容。
    v1.6: 新增 ``origin`` —— 图框整体平移量。出图前按内容重选幅面时，
    把图框摆到内容外侧即可，内容坐标无需改动。
    """
    msp = doc.modelspace()
    W, H, tb, s = _resolve_sheet(info.size, info.orientation)
    W, H = W * scale, H * scale
    a, c = MARGIN.get(s, (MARGIN_L, MARGIN_O))
    ml, mo = a * scale, c * scale
    ox, oy = origin

    def _P(x, y):
        return (x + ox, y + oy)

    # 外框（图幅边界，细实线）
    msp.add_lwpolyline([_P(0, 0), _P(W, 0), _P(W, H), _P(0, H)], close=True,
                       dxfattribs={"layer": "图框"})
    # 内框（图框线，粗实线）
    x0, y0 = ml, mo
    x1, y1 = W - mo, H - mo
    msp.add_lwpolyline([_P(x0, y0), _P(x1, y0), _P(x1, y1), _P(x0, y1)], close=True,
                       dxfattribs={"layer": "图框"})
    # 对中标志（四边中点小三角，可选，便于折叠定位）
    _center_marks(msp, x0 + ox, y0 + oy, x1 + ox, y1 + oy, scale)
    # 标题栏（右下角，向左上展开）；随图幅缩放系数 tb 等比放大
    _draw_title_block(msp, x1 + ox, y0 + oy, scale, info, tracker, tb)
    # 注册图框边距区域（仅四周留白，不占绘图区，避免假碰撞）
    if tracker is not None:
        # 左装订边、右/上/下留白边
        tracker.register(ox, oy, ox + ml, oy + H, margin=50)              # 左边距
        tracker.register(ox + W - mo, oy, ox + W, oy + H, margin=50)      # 右边距
        tracker.register(ox + ml, oy + H - mo, ox + W - mo, oy + H, margin=50)  # 上边距
        tracker.register(ox + ml, oy, ox + W - mo, oy + mo, margin=50)    # 下边距
    return (x0 + ox, y0 + oy, x1 + ox, y1 + oy)


def _sheet_inner(size, orientation, scale):
    """返回 (W, H, tb, inner)：图幅实际尺寸、标题栏缩放系数、内框坐标。

    W/H 已乘 scale；inner=(ix0,iy0,ix1,iy1)。留边按 GB/T 14689 逐幅面取值
    （a=25；c：A0/A1/A2=10，A3/A4=5）。
    """
    long_, short_ = PAPER_BASE[size]
    if (orientation or "landscape").lower() == "portrait":
        W, H = short_ * scale, long_ * scale
    else:
        W, H = long_ * scale, short_ * scale
    a, c = MARGIN.get(size, (MARGIN_L, MARGIN_O))
    tb = TITLE_SCALE.get(size, 1.0)
    inner = (a * scale, c * scale, W - c * scale, H - c * scale)
    return W, H, tb, inner


def _title_block_size(size, scale):
    """标题栏 (宽, 高)：180×56 mm，按 scale 与幅面系数 tb 等比放大。"""
    ts = scale * TITLE_SCALE.get(size, 1.0)
    return TITLE_W * ts, TITLE_H * ts


def _title_block_rect(size, scale, inner):
    """标题栏矩形（贴内框右下角）。"""
    tw, th = _title_block_size(size, scale)
    _ix0, iy0, ix1, _iy1 = inner
    return (ix1 - tw, iy0, ix1, iy0 + th)


def _text_extent(e):
    """TEXT/MTEXT 的真实包围盒（按字宽估算）。

    旧实现只取插入点，长文字伸出图框也不会被发现；这里按 halign/valign
    还原对齐方式后估算范围，宁可略大不可偏小。
    """
    from .annotate import _estimate_text_width
    t = e.dxftype()
    if t == "TEXT":
        content, h, ip = e.dxf.text, float(e.dxf.height), e.dxf.insert
        halign = int(getattr(e.dxf, "halign", 0) or 0)
        valign = int(getattr(e.dxf, "valign", 0) or 0)
    else:  # MTEXT
        content, h, ip = e.text, float(e.dxf.char_height), e.dxf.insert
        halign = int(getattr(e.dxf, "attachment_point", 1) or 1)
        valign = 2 if halign in (1, 2, 3) else 3
        halign = 1 if halign in (1, 2, 3) else 0
    w = _estimate_text_width(str(content), h)
    th = h * 1.6
    if halign == 1:      # 中
        x0, x1 = ip.x - w / 2, ip.x + w / 2
    elif halign == 2:    # 右
        x0, x1 = ip.x - w, ip.x
    else:                # 左
        x0, x1 = ip.x, ip.x + w
    if valign == 2:      # 中
        y0, y1 = ip.y - th / 2, ip.y + th / 2
    elif valign == 3:    # 上
        y0, y1 = ip.y - th, ip.y
    else:                # 基线/底
        y0, y1 = ip.y, ip.y + th
    return (x0, y0, x1, y1)


def _entity_extent(e):
    """单实体包围盒 (x0,y0,x1,y1)；不可测量时返回 None。"""
    t = e.dxftype()
    if t in ("TEXT", "MTEXT"):
        try:
            return _text_extent(e)
        except Exception:
            pass
    try:
        if t == "LWPOLYLINE":
            pts = list(e.get_points("xy"))
            if pts:
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                return (min(xs), min(ys), max(xs), max(ys))
        elif t == "LINE":
            return (min(e.dxf.start.x, e.dxf.end.x), min(e.dxf.start.y, e.dxf.end.y),
                    max(e.dxf.start.x, e.dxf.end.x), max(e.dxf.start.y, e.dxf.end.y))
        elif t in ("ARC", "CIRCLE"):
            c, r = e.dxf.center, e.dxf.radius
            return (c.x - r, c.y - r, c.x + r, c.y + r)
        b = e.bbox()
        if b:
            return (b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y)
    except Exception:
        pass
    try:
        ip = e.dxf.insert
        return (ip.x, ip.y, ip.x, ip.y)
    except Exception:
        return None


def _content_bbox(msp, scale=1.0):
    """内容外包络 (xmin,ymin,xmax,ymax)。

    只排除图框层与标题栏层（标准幅面边界 + 标题栏，出图前会按内容重选并重画）。
    ``附表`` 层（技术要求框/表格/图例框）属内容，必须计入——否则它们会被
    图框切掉，这正是旧版"框线贴边/内容出框"的成因。
    """
    xmin = ymin = 1e18
    xmax = ymax = -1e18
    for e in list(msp):
        if e.dxf.layer in SHEET_LAYERS:
            continue
        b = _entity_extent(e)
        if b is None:
            continue
        xmin, ymin = min(xmin, b[0]), min(ymin, b[1])
        xmax, ymax = max(xmax, b[2]), max(ymax, b[3])
    if xmax < xmin or ymax < ymin:
        return (0.0, 0.0, 1.0, 1.0)
    return (xmin, ymin, xmax, ymax)


def _page_fit(size, orientation, scale, bbox, reserve_title=True):
    """内容外包络（允许平移摆放）能否装进该幅面内框。"""
    inner = _sheet_inner(size, orientation, scale)[3]
    pad = PAD_PAPER_MM * scale
    avail_w = (inner[2] - inner[0]) - 2 * pad
    avail_h = (inner[3] - inner[1]) - 2 * pad
    if reserve_title:
        avail_h -= _title_block_size(size, scale)[1]
    return (bbox[2] - bbox[0]) <= avail_w and (bbox[3] - bbox[1]) <= avail_h


def _page_for_content(bbox, scale, orientation="landscape"):
    """选幅面：先按"给标题栏让位"选，实在放不下再按"仅须装进内框"选。

    返回 (size, orientation, reserved)；reserved=True 表示已为标题栏留位。
    """
    orders = ["landscape", "portrait"] if not orientation else [orientation]
    for reserved in (True, False):
        for o in orders:
            for sz in PAPER_ORDER:
                if _page_fit(sz, o, scale, bbox, reserved):
                    return sz, o, reserved
    return "A0", (orientation or "landscape"), False


def choose_paper_for_content(bbox, scale, orientation="landscape"):
    """按内容外包络选能装下的最小标准幅面，返回 (size, orientation)。"""
    sz, o, _res = _page_for_content(bbox, scale, orientation)
    return sz, o


def _place_origin(bbox, size, orientation, scale, reserved):
    """算出图框原点：让内容在"可用绘图区"内居中。

    可用绘图区 = 内框去掉四周留白，若给标题栏让位则再抬掉底部标题栏高度。
    这里**只移动图框，不平移内容**：图框坐标本身没有语义，摆放自由；
    内容不动可避免 MTEXT/INSERT 平移错位，也保证后续追加的实体
    （如一键标注流程里后加的 GD&T 符号）仍落在原坐标上。
    """
    inner = _sheet_inner(size, orientation, scale)[3]
    pad = PAD_PAPER_MM * scale
    tb_h = _title_block_size(size, scale)[1] if reserved else 0.0
    ux0, uy0 = inner[0] + pad, inner[1] + tb_h + pad
    ux1, uy1 = inner[2] - pad, inner[3] - pad
    cx, cy = (ux0 + ux1) / 2, (uy0 + uy1) / 2
    bcx, bcy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    # 图框原点 = 内容中心 − 可用区中心（把可用区中心搬到内容中心上）
    ox = bcx - cx
    oy = bcy - cy
    return (ox, oy)


def _erase_layer(msp, layer):
    """删除某图层上的全部实体，返回删除数量。"""
    n = 0
    for e in list(msp):
        try:
            if e.dxf.layer == layer:
                msp.delete_entity(e)
                n += 1
        except Exception:
            continue
    return n


def _erase_sheet(msp):
    """删除图幅家具层（图框 + 标题栏），出图前重画。"""
    return sum(_erase_layer(msp, L) for L in SHEET_LAYERS)


def _overflow(bbox, size, orientation, scale, origin):
    """内容相对内框的超出量（>0 = 压框；含标题栏占位）。"""
    inner = _sheet_inner(size, orientation, scale)[3]
    ox, oy = origin
    ix0, iy0 = inner[0] + ox, inner[1] + oy
    ix1, iy1 = inner[2] + ox, inner[3] + oy
    return max(ix0 - bbox[0], iy0 - bbox[1], bbox[2] - ix1, bbox[3] - iy1)


def _report(size, o, reserved, overflow_px, scale):
    """出图自检信息（供 CLI 提示与出图方核对）。"""
    return {
        "size": size,
        "orientation": o,
        "title_reserved": reserved,
        "overflow_mm": round(max(0.0, overflow_px) / (scale or 1.0), 1),
    }


def draw_frame_at(doc, scale, info, bbox, tracker=None):
    """按内容外包络选标准幅面，并把图框摆到内容外侧后绘制。

    v1.6：不再"把图框贴着内容画"。旧做法得到的是非标准幅面（长宽比不是
    A 系列的 √2），标题栏声明的比例尺随之失真——实测 T4-02 标 1:100
    实为 1:47、T6 标 1:200 实为 1:99。现在改为：按内容选最小标准幅面，
    图框整体平移到内容外侧；内容坐标不动。
    返回内框范围 (x0, y0, x1, y1)。
    """
    size, o, reserved = _page_for_content(
        bbox, scale, (info.orientation or _DEFAULT_ORIENTATION))
    info.size, info.orientation = size, o
    origin = _place_origin(bbox, size, o, scale, reserved)
    r = _report(size, o, reserved, _overflow(bbox, size, o, scale, origin), scale)
    info.overflow_mm = r["overflow_mm"]
    return draw_frame(doc, scale, info, tracker=tracker, origin=origin)


def refit_frame(doc, scale, info, tracker=None, orientation=None):
    """保存前调用：按内容重选标准幅面并重画图框（内容坐标不动）。

    v1.6：图幅、比例尺、留边、标题栏四者一致；图幅恒为标准 GB/T 14689
    幅面。A0 仍装不下时保留 A0，并把超出量写进 ``info.overflow_mm``。
    返回 (size, orientation)。
    """
    msp = doc.modelspace()
    bbox = _content_bbox(msp, scale)
    want = orientation or info.orientation or _DEFAULT_ORIENTATION
    size, o, reserved = _page_for_content(bbox, scale, want)
    info.size, info.orientation = size, o
    origin = _place_origin(bbox, size, o, scale, reserved)
    _erase_sheet(msp)
    x0, y0, x1, y1 = draw_frame(doc, scale, info, tracker=tracker, origin=origin)
    r = _report(size, o, reserved, _overflow(bbox, size, o, scale, origin), scale)
    info.overflow_mm = r["overflow_mm"]
    if not reserved or info.overflow_mm > 0:
        print(f"  [图幅] {size}-{o}：内容超出内框 {info.overflow_mm:.0f}mm(纸面)，"
              f"建议加大比例尺分母或采用加长幅面")
    return size, o


def save_dxf_autofit(doc, path, scale, info, tracker=None, orientation=None):
    """save_dxf 的自适应封装：保存前先 refit_frame，保证图幅与内容匹配。"""
    refit_frame(doc, scale, info, tracker, orientation)
    return save_dxf(doc, path)


def _center_marks(msp, x0, y0, x1, y1, s):
    mid_w = (x0 + x1) / 2
    mid_h = (y0 + y1) / 2
    L = 5 * s
    for (cx, cy, dx, dy) in [
        (mid_w, y1, L, L),   # 上
        (mid_w, y0, L, -L),  # 下
        (x0, mid_h, -L, L),  # 左
        (x1, mid_h, L, L),   # 右
    ]:
        msp.add_line((cx - dx / 2, cy), (cx + dx / 2, cy), dxfattribs={"layer": "图框"})
        msp.add_line((cx, cy - dy / 2), (cx, cy + dy / 2), dxfattribs={"layer": "图框"})


def _draw_title_block(msp, rx, by, s, info: FrameInfo, tracker=None, tb: float = 1.0):
    """标题栏右下角位于 (rx, by)，向左上展开 180×56（×scale×tb）。

    v1.5: tb 为图幅缩放系数（大图标题栏放大，小图不变），所有内部几何
    与字高均按 ts = s * tb 等比缩放，保持图框比例正确。
    """
    ts = s * tb
    tw, th = TITLE_W * ts, TITLE_H * ts
    lx, ty = rx - tw, by + th  # 左上角
    # 标题栏整体（框线 + 分格 + 文字）独占"标题栏"层：
    # 出图前要按内容重选幅面重画图框，整层删除才能保证不留残影。
    LB = "标题栏"
    # 外框（粗实线）
    msp.add_lwpolyline([(lx, by), (rx, by), (rx, ty), (lx, ty)], close=True,
                       dxfattribs={"layer": LB})
    # 注册标题栏区域
    if tracker is not None:
        tracker.register(lx, by, rx, ty, margin=50)
    # —— 分格 ——
    # 主分界：图名区(左96) | 签字区(36) | 单位区(28) | 比例图号区(20)
    c1 = lx + 96 * ts      # 图名 | 签字
    c2 = lx + 132 * ts     # 签字 | 单位
    c3 = lx + 160 * ts     # 单位 | 比例图号
    hmid = by + 28 * ts    # 上下分界
    for x in (c1, c2, c3):
        msp.add_line((x, by), (x, ty), dxfattribs={"layer": LB})
    msp.add_line((lx, hmid), (c1, hmid), dxfattribs={"layer": LB})
    # 比例/图号 上下分界
    msp.add_line((c3, by + 14 * ts), (rx, by + 14 * ts), dxfattribs={"layer": LB})
    # 签字区三行（上半格 28~56 内均分，与签字文字行对应）
    for i in (1, 2):
        y = by + (28 + 28 / 3 * i) * ts
        msp.add_line((c1, y), (c2, y), dxfattribs={"layer": LB})

    # —— 文字 ——
    H = ts  # 字高基数（随图幅缩放）
    # 图名（大字，居中，上半格 28~56）
    _text(msp, info.title, ((lx + c1) / 2, by + 42 * ts), 5 * H,
          align=TextEntityAlignment.MIDDLE_CENTER, layer=LB,
          tracker=tracker)
    # 项目名（下半格 0~28 居中）
    _text(msp, info.project, ((lx + c1) / 2, by + 14 * ts), 3 * H,
          align=TextEntityAlignment.MIDDLE_CENTER, layer=LB,
          tracker=tracker)
    # 签字三行
    rows = [("设计", info.designer), ("校核", info.checker), ("审核", info.auditor)]
    rh = 28 / 3 * ts
    for i, (lbl, name) in enumerate(rows):
        cy = by + th - rh * (i + 0.5)
        _text(msp, lbl, (c1 + 4 * ts, cy), 2.5 * H, layer=LB, tracker=tracker)
        _text(msp, name, (c1 + 14 * ts, cy), 2.5 * H, layer=LB, tracker=tracker)
    # 单位
    _text(msp, info.unit, ((c2 + c3) / 2, (by + ty) / 2), 2.8 * H,
          align=TextEntityAlignment.MIDDLE_CENTER, layer=LB, tracker=tracker)
    # 比例
    _text(msp, "比例", (c3 + 2 * ts, by + 21 * ts), 2.2 * H, layer=LB, tracker=tracker)
    _text(msp, info.scale_str, ((c3 + rx) / 2, by + 18 * ts), 3 * H,
          align=TextEntityAlignment.MIDDLE_CENTER, layer=LB, tracker=tracker)
    # 图号
    _text(msp, "图号", (c3 + 2 * ts, by + 7 * ts), 2.2 * H, layer=LB, tracker=tracker)
    _text(msp, info.drawing_no, ((c3 + rx) / 2, by + 4 * ts), 3 * H,
          align=TextEntityAlignment.MIDDLE_CENTER, layer=LB, tracker=tracker)
    # 日期（下半格 0~28 居中，签字区下方）
    _text(msp, info.date, ((c1 + c2) / 2, by + 14 * ts), 2.5 * H,
          align=TextEntityAlignment.MIDDLE_CENTER, layer=LB, tracker=tracker)


def _text(msp, content, point, height, align=TextEntityAlignment.LEFT, layer="文字",
          tracker=None):
    """图框文字写入 v1.5 — 使用修复版文字宽度估算。"""
    if not content:
        return
    t = msp.add_text(content, dxfattribs={"layer": layer, "height": height, "style": "HZ"})
    t.set_placement(point, align=align)
    if tracker is not None:
        # 注册文字区域（使用修复版估算逻辑）
        from .annotate import _estimate_text_width
        px, py = point
        tw = _estimate_text_width(content, height)
        th = height * 1.6  # 修复：增大行高
        if align in (TextEntityAlignment.MIDDLE_CENTER,):
            bx0, by0 = px - tw / 2, py - th / 2
            bx1, by1 = px + tw / 2, py + th / 2
        elif align in (TextEntityAlignment.MIDDLE_LEFT, TextEntityAlignment.LEFT):
            bx0, by0 = px, py - th / 2
            bx1, by1 = px + tw, py + th / 2
        else:
            bx0, by0 = px - tw / 2, py - th / 2
            bx1, by1 = px + tw / 2, py + th / 2
        tracker.register(bx0, by0, bx1, by1, margin=height * 0.5)
    return t
