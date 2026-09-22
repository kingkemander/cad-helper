"""图纸生成器：五个验收测试。

每个 gen_tN(out_dir, **params) 生成一张 DXF 并返回路径，参数化支持迭代修改。
"""
from __future__ import annotations

import weakref

from ezdxf.enums import TextEntityAlignment

from ..standards.annotate import _t, draw_flow_arrow


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


# ── 工艺流程图布局 ────────────────────────────────────────────────
# 流程图曾经是"5 个 34×24mm 的小框横排 + 说明框贴右侧另起一列"：内容包络被拉成
# 一条 250×40mm 的细长条（说明框再占 95mm 宽），选幅面只能挑到 A3，实测占幅
# 6.8%~8.3% —— 一页标准图上只有右上角一小排框。这里改成：框宽按 A4 横的可用宽度
# 自动分配、框高加大到 55mm，说明框移到流程图**正下方并撑满同宽**，让内容包络
# 接近 A4 可用区，占幅从 8% 提到 45% 上下。
FLOW_TARGET_W_MM = 250.0   # 目标内容宽（A4 横内框去标题栏后可用 255mm，留余量）
FLOW_BOX_H_MM = 55.0       # 框高（旧值 24mm，只占纸高 11%）
FLOW_GAP_MM = 12.0         # 框间距 = 箭头长度
FLOW_TEXT_MM = 4.0         # 框内标签字高（标准字高 3.5mm 的略放大）


def draw_flow_chain(msp, origin, stages, scale: float, tracker=None,
                    layer: str = "工艺"):
    """画一排"方框 → 箭头"的工艺流程图，框宽按 :data:`FLOW_TARGET_W_MM` 均分。

    框数变化时框宽自动适配（不写死 34mm），保证任何张数的流程都落在同一张幅面
    里。``origin`` 是整排的左下角。返回流程图外接矩形 ``(x0, y0, x1, y1)``，
    供调用方把说明框贴到它正下方。
    """
    n = len(stages)
    if n <= 0:
        return (origin[0], origin[1], origin[0], origin[1])
    gap = FLOW_GAP_MM * scale
    bw = (FLOW_TARGET_W_MM * scale - (n - 1) * gap) / n
    bh = FLOW_BOX_H_MM * scale
    x0, y0 = origin
    for i, st in enumerate(stages):
        cx = x0 + i * (bw + gap)
        msp.add_lwpolyline([(cx, y0), (cx + bw, y0), (cx + bw, y0 + bh),
                            (cx, y0 + bh)],
                           close=True, dxfattribs={"layer": layer})
        _t(msp, st, (cx + bw / 2, y0 + bh / 2), FLOW_TEXT_MM * scale,
           align=TextEntityAlignment.MIDDLE_CENTER, layer="文字", tracker=tracker)
        if i < n - 1:
            draw_flow_arrow(msp, (cx + bw, y0 + bh / 2), (gap, 0), scale,
                            length=FLOW_GAP_MM, label="", tracker=tracker)
    return (x0, y0, x0 + n * bw + (n - 1) * gap, y0 + bh)


def flow_notes_below(msp, flow, scale: float, title: str, notes: list,
                     tracker=None, gap_mm: float = 12.0):
    """把说明框放在流程图正下方，并撑满流程图同宽。

    返回说明框矩形。``gap_mm`` 是流程图底边到说明框顶边（纸面 mm）。
    """
    sx0, _sy0, sx1, _sy1 = flow
    w = (sx1 - sx0) / scale
    return draw_tech_notes(msp, (sx0, flow[1] - gap_mm * scale), scale,
                           title, notes, width=w, tracker=tracker)


# 每张图一个附表列（按 modelspace 缓存，弱引用避免拖住文档）
_AUX_COLUMNS = weakref.WeakKeyDictionary()


def aux_column(msp, scale: float, tracker=None, gap: float = 1500.0,
               exclude_annex: bool = True):
    """取本图（modelspace）的附表列；同一张图多次调用复用同一列。

    各图纸生成器把图例、技术要求、校验报告分写在不同的 ``_sN_xxx`` 函数里，
    这里按 modelspace 缓存一列，各函数只管 ``aux_column(msp, s, tracker).add(...)``
    就能自上而下顺次堆叠，不必互相传列对象、也不必调用方手算块高。

    附表贴的是**主体包络**右侧（``frame.content_bbox``），不是
    ``draw_frame`` 返回的初始图框角点 —— 后者会让内容包络被附表撑满整张默认
    A2 图框，最小可装幅面因此被整体抬高（实测 12 张验收图全部虚涨 1.04~4.78 倍）。
    """
    from ..standards.frame import content_bbox
    from ..standards.layout import AuxColumn
    col = _AUX_COLUMNS.get(msp)
    if col is None:
        col = AuxColumn(msp, content_bbox(msp, exclude_annex=exclude_annex), scale,
                        gap=gap, tracker=tracker)
        _AUX_COLUMNS[msp] = col
    return col


_MC = TextEntityAlignment.MIDDLE_CENTER

# 设备材料表的目标表宽与行高（名义纸面 mm，不随比例尺缩放）
# 旧值：列宽 8+26+34+9+9=86mm、行高 6mm → 贴在 A4 上只占纸宽 29%
MATERIAL_TABLE_W_MM = 220.0
MATERIAL_ROW_H_MM = 8.0
MATERIAL_HEAD_H_MM = 9.0
_MAT_FIXED_MM = (12.0, 12.0, 14.0)     # 序号 / 单位 / 数量 的固定列宽


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


def draw_material_table(msp, origin, scale: float, rows: list, tracker=None,
                        width: float = MATERIAL_TABLE_W_MM,
                        row_h: float = MATERIAL_ROW_H_MM,
                        head_h: float = MATERIAL_HEAD_H_MM):
    """设备材料表（序号/名称/规格/单位/数量 五列）。origin=左上角。

    列宽按 ``width``（单位：图纸 mm）分配，不再写死 86mm。表格**不按比例尺
    缩放**（行高/字高都是名义纸面尺寸），所以没有理由吊在 1:100 的小尺寸上：
    实测旧写法 86×68mm 的表贴在 A4 上只占纸宽 29%、占幅 13%，一页纸上只有
    一小块表格。余宽按 1:1.4 分给"名称/规格型号"两列（规格串最长）。
    """
    s = scale
    ox, oy = origin
    c_no, c_u, c_q = _MAT_FIXED_MM
    spare = max(width - (c_no + c_u + c_q), 80.0)
    cols = [c_no * s, spare / 2.4 * s, spare * 1.4 / 2.4 * s, c_u * s, c_q * s]
    rh = row_h * s
    th = head_h * s
    total_w = sum(cols)
    headers = ["序号", "名称", "规格型号", "单位", "数量"]
    n = len(rows)
    cx = ox
    for i, h in enumerate(headers):
        # 单元格位置由行列决定，只登记占位、不参与碰撞避让（见 _t 的 avoid）
        _t(msp, h, (cx + cols[i] / 2, oy - th / 2 + 0.5 * s), 3.4 * s, align=_MC,
           layer="文字-标题", tracker=tracker, avoid=False)
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
            _t(msp, val, (cx + cols[i] / 2, ry - rh / 2 + 0.5 * s), 3.0 * s,
               align=_MC, layer="文字", tracker=tracker, avoid=False)
            cx += cols[i]
    yb = oy - th - n * rh
    msp.add_line((ox, yb), (ox + total_w, yb), dxfattribs={"layer": "附表"})
    msp.add_line((ox, oy - th), (ox, yb), dxfattribs={"layer": "附表"})
    msp.add_line((ox + total_w, oy - th), (ox + total_w, yb), dxfattribs={"layer": "附表"})
    return (ox, yb, ox + total_w, oy)
