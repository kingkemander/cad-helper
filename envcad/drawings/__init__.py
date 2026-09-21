"""图纸生成器：五个验收测试。

每个 gen_tN(out_dir, **params) 生成一张 DXF 并返回路径，参数化支持迭代修改。
"""
from __future__ import annotations

from ezdxf.enums import TextEntityAlignment

from ..standards.annotate import _t


def draw_tech_notes(msp, origin, scale: float, title: str, notes: list,
                    width: float = 80.0, line_h: float = 6.0,
                    tracker=None):
    """绘制技术要求框（标题 + 编号条目）。origin=左上角。v1.4 支持碰撞检测。"""
    s = scale
    ox, oy = origin
    title_h = 7 * s
    rh = line_h * s
    total_h = title_h + len(notes) * rh + 2 * s
    w = width * s
    # 外框
    x0, y0 = ox, oy - total_h
    x1, y1 = ox + w, oy
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True,
                       dxfattribs={"layer": "附表"})
    # 注册外框占用（供外部标注避让）
    if tracker is not None:
        tracker.register(x0, y0, x1, y1, margin=50)
    # 标题 + 条目
    # 注：框内文字为框内精确定位，不参与碰撞避让
    # （否则会被自身外框注册区顶出框外，导致错行）；外框已注册，外部标注自会避让。
    msp.add_line((x0, y1 - title_h), (x1, y1 - title_h), dxfattribs={"layer": "附表"})
    # 标题与条目一律用**垂直居中**对齐（MIDDLE_CENTER / MIDDLE_LEFT），
    # 不要用 LEFT/BASELINE + 手工 +0.5s 偏移：_t 估的框高是 height*1.6，
    # 基线对齐时文字框整体坐在基线之上，标题框底边 = y1-5.8s、首条框顶边
    # = y1-5.5s，实测每张图的"技术要求"标题都与第 1 条叠印 30mm（1:100 下
    # 0.3mm，纸面上已经粘字，而 15% 面积阈值筛不出来）。
    # 居中后标题框 y1-6.3s..y1-0.7s、首条框 y1-12s..y1-8s，各留 1.7s 净距。
    _t(msp, title, ((x0 + x1) / 2, y1 - title_h / 2), 3.5 * s,
       align=TextEntityAlignment.MIDDLE_CENTER, layer="文字-标题")
    for i, note in enumerate(notes):
        ry = y1 - title_h - (i + 0.5) * rh
        _t(msp, f"{i+1}. {note}", (x0 + 2 * s, ry), 2.5 * s,
           align=TextEntityAlignment.MIDDLE_LEFT, layer="文字")
    return (x0, y0, x1, y1)


def draw_note(msp, origin, scale: float, text: str, height: float = 3.5):
    """单行说明文字。"""
    _t(msp, text, origin, height * scale, layer="文字")


_MC = TextEntityAlignment.MIDDLE_CENTER


def draw_spec_table(msp, origin, scale: float, title: str, rows: list,
                    tracker=None):
    """技术特性表（项目/参数/单位 三列）。origin=右上角。rows=[(项目,参数,单位)]"""
    s = scale
    ox, oy = origin
    cols = [40 * s, 28 * s, 16 * s]
    rh = 6.5 * s
    th = 8 * s
    total_w = sum(cols)
    headers = ["项目", "参数", "单位"]
    n = len(rows)
    y_top = oy
    _t(msp, title, (ox - total_w / 2, y_top + 4 * s), 3.6 * s, align=_MC,
       layer="文字-标题", tracker=tracker)
    y = y_top
    cx = ox - total_w
    for i, h in enumerate(headers):
        _t(msp, h, (cx + cols[i] / 2, y - th / 2 + 0.5 * s), 3 * s, align=_MC,
           layer="文字-标题", tracker=tracker)
        cx += cols[i]
    msp.add_lwpolyline([(ox - total_w, y), (ox, y), (ox, y - th), (ox - total_w, y - th)],
                       close=True, dxfattribs={"layer": "附表"})
    for j in range(1, len(headers)):
        xx = ox - total_w + sum(cols[:j])
        msp.add_line((xx, y), (xx, y - th - n * rh), dxfattribs={"layer": "附表"})
    for r, row in enumerate(rows):
        ry = y - th - r * rh
        msp.add_line((ox - total_w, ry), (ox, ry), dxfattribs={"layer": "附表"})
        cx = ox - total_w
        for i, val in enumerate(row):
            _t(msp, val, (cx + cols[i] / 2, ry - rh / 2 + 0.5 * s), 2.4 * s,
               align=_MC, layer="文字", tracker=tracker)
            cx += cols[i]
    yb = y - th - n * rh
    msp.add_line((ox - total_w, yb), (ox, yb), dxfattribs={"layer": "附表"})
    msp.add_line((ox - total_w, y - th), (ox - total_w, yb), dxfattribs={"layer": "附表"})
    msp.add_line((ox, y - th), (ox, yb), dxfattribs={"layer": "附表"})


def draw_material_table(msp, origin, scale: float, rows: list, tracker=None):
    """设备材料表（序号/名称/规格/单位/数量 五列）。origin=左上角。"""
    s = scale
    ox, oy = origin
    cols = [8 * s, 26 * s, 34 * s, 9 * s, 9 * s]
    rh = 6 * s
    th = 8 * s
    total_w = sum(cols)
    headers = ["序号", "名称", "规格型号", "单位", "数量"]
    n = len(rows)
    cx = ox
    for i, h in enumerate(headers):
        _t(msp, h, (cx + cols[i] / 2, oy - th / 2 + 0.5 * s), 3 * s, align=_MC,
           layer="文字-标题", tracker=tracker)
        cx += cols[i]
    msp.add_lwpolyline([(ox, oy), (ox + total_w, oy), (ox + total_w, oy - th),
                        (ox, oy - th)], close=True, dxfattribs={"layer": "附表"})
    for j in range(1, len(headers)):
        xx = ox + sum(cols[:j])
        msp.add_line((xx, oy), (xx, oy - th - n * rh), dxfattribs={"layer": "附表"})
    for r, row in enumerate(rows):
        ry = oy - th - r * rh
        msp.add_line((ox, ry), (ox + total_w, ry), dxfattribs={"layer": "附表"})
        cx = ox
        for i, val in enumerate(row):
            _t(msp, val, (cx + cols[i] / 2, ry - rh / 2 + 0.5 * s), 2.4 * s,
               align=_MC, layer="文字", tracker=tracker)
            cx += cols[i]
    yb = oy - th - n * rh
    msp.add_line((ox, yb), (ox + total_w, yb), dxfattribs={"layer": "附表"})
    msp.add_line((ox, oy - th), (ox, yb), dxfattribs={"layer": "附表"})
    msp.add_line((ox + total_w, oy - th), (ox + total_w, yb), dxfattribs={"layer": "附表"})
