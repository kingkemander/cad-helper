"""测试4 v1.4：50m³/d 生活污水处理站成套施工图。

改进:
  * 使用 BBoxTracker 防止文字-线条遮挡
  * 增大平剖面间距（最小 5000mm 安全间距）
  * 图例与图形留足边距（≥6000mm 边距）
  * 技术说明自动避让图形区域
  * 所有坐标圆整到 0.01mm

6 张图：总平面 / 调节池 / 接触氧化池 / 斜管沉淀池 / 管道平面 / 设备材料表。
统一 A3、1:100，每张配标题栏、图例、专项技术要求。
工艺流程：格栅→调节池→提升泵→接触氧化池→沉淀池→消毒池→出水。
"""
from __future__ import annotations

import math
import os

from ..engine.dxf_base import new_drawing, save_dxf, BBoxTracker
from ..standards.frame import (FrameInfo, draw_frame, save_dxf_autofit,
                                   content_bbox)
from ..standards.annotate import (_t, draw_flow_arrow, draw_elevation,
                                  draw_pipe_diameter, _estimate_text_width)
from ..standards.legend import draw_legend
from ..standards.layout import AuxColumn
from ..standards.dim import draw_linear_dimension
from ..standards.site import (draw_north_arrow, draw_coord_grid,
                              draw_site_road, draw_site_boundary)
from ..components.pool import (RectPoolParams, draw_rect_pool_plan,
                              draw_rect_pool_section, draw_circular_pool_plan,
                              draw_circular_pool_section)
from ..components.pipe import draw_pipe
from ..components.fittings import (
    draw_valve, draw_flow_meter, draw_check_valve, _hatch, _line, _poly,
    draw_butterfly_valve, draw_diaphragm_valve, draw_globe_valve,
    draw_ball_valve, draw_sampling_valve, draw_soft_joint,
    draw_elbow, draw_tee, draw_reducer, draw_flange_pair,
    draw_instrument_symbol,
)
from ..components.env_equipment import (
    draw_self_priming_pump, draw_submersible_pump,
    draw_mixer, draw_dosing_system, draw_clo2_generator,
)
from . import draw_tech_notes
from ezdxf.enums import TextEntityAlignment

# ─── 全局常量 ─────────────────────────────────────────
INFO = FrameInfo(project="50m³/d生活污水处理站", unit="环保工程",
                 designer="envcad", date="2026.07")

LAYOUT_GAP = 6000.0     # 视图间最小间距 (mm)
MARGIN_EDGE = 3000.0    # 视图到图框的安全边距 (mm)
TECH_NOTE_W = 90.0      # 技术要求框宽度 (图纸 mm)


def _aux_col(msp, scale, tracker, gap=1500.0):
    """把附表（技术要求/图例/表格）贴到**主体包络**右侧，向下堆叠。

    旧写法一律锚 ``(x1 - TECH_NOTE_W*s, y1 - 32*s)``，即 ``draw_frame`` 返回的
    初始图框（进程默认 A2）右上角。``save_dxf_autofit`` 之后才按内容重选幅面，
    于是"左边一列平面+剖面、右边贴着 A2 右上角一张技术要求"把内容包络撑满整张
    A2 —— 最小可装幅面被抬到 A1（实测 T4-02/03/05 虚涨 4 倍以上）。
    """
    return AuxColumn(msp, content_bbox(msp, exclude_annex=True), scale,
                     gap=gap, tracker=tracker)


def _frame(doc, scale, title, no, tracker=None):
    info = FrameInfo(title=title, drawing_no=no, scale_str=f"1:{int(scale)}",
                     project=INFO.project, unit=INFO.unit,
                     designer=INFO.designer, date=INFO.date)
    x0, y0, x1, y1 = draw_frame(doc, scale, info, tracker=tracker)
    return x0, y0, x1, y1, info


def gen_t4(out_dir: str, scale: float = 100.0) -> list:
    paths = []
    paths.append(_sheet1_general(out_dir, scale))
    paths.append(_sheet2_adjustment(out_dir, scale))
    paths.append(_sheet3_contact_oxidation(out_dir, scale))
    paths.append(_sheet4_settler(out_dir, scale))
    paths.append(_sheet5_piping(out_dir, scale))
    paths.append(_sheet6_material(out_dir, scale))
    return paths


# ═══════════════ 图1：总平面布置图 ═══════════════

# 站区（用地红线内）几何，单位实物 mm
SITE_REDLINE_W = 24000.0      # 红线东西向长度
SITE_REDLINE_H = 23500.0      # 红线南北向长度
SITE_ROAD_W = 4000.0          # 厂区道路宽（兼作消防通道，GB 50016 ≥4m）
SITE_ROAD_OFF = 1000.0        # 道路外边线到红线的距离
SITE_UNIT_DX = 5500.0         # 构筑物块左下角相对红线西南角的偏移（西/南侧让出道路）
SITE_UNIT_DY = 5500.0


def _sheet1_general(out_dir, scale):
    doc, _, tracker = new_drawing(scale, return_tracker=True)
    msp = doc.modelspace()
    x0, y0, x1, y1, info = _frame(doc, scale, "总平面布置图", "T4-01", tracker=tracker)
    s = scale

    # ── 站区定位 ──
    # 全部内容按"红线西南角 SX/SY + 站区局部坐标"摆放。
    # 关键：附表（图例/技术要求）**不**锚在图框角点上。旧写法用 x1/y1 定位，
    # 而 x1/y1 来自 refit 之前的初始图框（进程默认 A2），附表于是把内容外包络
    # 撑到近整张 A2 宽，最小可装幅面被抬到 A1（实测 581×332 图纸 mm，纸面仅填
    # 69%×56%，而主体图形只占 180×195）。改锚到内容块后实测落到 A2。
    SX = x0 + MARGIN_EDGE + 4000
    SY = y0 + 6000

    # ── 用地红线 + 站区围墙（GB/T 50103）──
    bnd_bb = draw_site_boundary(msp, (SX, SY, SX + SITE_REDLINE_W, SY + SITE_REDLINE_H), s,
                                label="厂区用地红线", wall_offset=500.0, tracker=tracker)

    # ── 厂区道路（南侧进厂路 + 西侧通道，L 形 4m 宽）──
    rx0, ry0 = SX + SITE_ROAD_OFF, SY + SITE_ROAD_OFF
    rx1 = SX + SITE_REDLINE_W - SITE_ROAD_OFF
    ry1 = SY + SITE_REDLINE_H - SITE_ROAD_OFF
    half_road = SITE_ROAD_W / 2.0
    draw_site_road(msp, (rx0, ry0 + half_road), (rx1, ry0 + half_road),
                   SITE_ROAD_W, s, code="厂区道路 宽4.0m", tracker=tracker)
    draw_site_road(msp, (rx0 + half_road, ry0), (rx0 + half_road, ry1),
                   SITE_ROAD_W, s, tracker=tracker)

    # ── 施工坐标网格（间距 4m）──
    # 注记不在这里落笔：网格注记与红线注记都左对齐在 SX，两行高度 300/400 而
    # y 只差 30（实测叠印 70%）。改为先取网格自身占位，稍后与红线注记**叠行**放置。
    grid_bb = draw_coord_grid(msp, (SX, SY, SX + SITE_REDLINE_W, SY + SITE_REDLINE_H),
                              4000.0, s, tracker=tracker)

    # ── 坐标网格注记：紧接红线注记之上另起一行 ──
    _t(msp, "坐标网格：单位 m，自红线西南角起算（西南角为 0.000）",
       (SX, max(bnd_bb[3], grid_bb[3]) + 1.2 * s), 3.0 * s,
       layer="文字", tracker=tracker)

    # ── 构筑物布局（站区局部坐标；净距 1500 ≥ 技术要求 800）──
    ux0, uy0 = SX + SITE_UNIT_DX, SY + SITE_UNIT_DY
    units = [
        (0, 0, 2000, 1000, "格栅井"),
        (0, 2500, 8000, 5000, "调节池"),
        (0, 9000, 6000, 4000, "接触氧化池"),
        (0, 14500, 4000, 3000, "消毒池"),
        (12000, 2500, 6000, 6000, "斜管沉淀池"),
    ]
    for dx, dy, w, h, name in units:
        ux, uy = ux0 + dx, uy0 + dy
        _poly(msp, [(ux, uy), (ux + w, uy), (ux + w, uy + h), (ux, uy + h)], "池体-壁")
        # 池名必须居中落在自己池内：池体外框已登记进 tracker，若开避让，
        # 居中点必然被判为"已占用"，文字被推到池外（实测 5 个池名统一上移
        # 1600，格栅井名甚至整个跑到矩形上方）。位置是设计要求，故 avoid=False
        # ——只登记占位、不避让（与表格单元格同一约定）。
        _t(msp, name, (ux + w / 2, uy + h / 2), 4 * s,
           align=TextEntityAlignment.MIDDLE_CENTER, layer="文字-标题",
           tracker=tracker, avoid=False)
    unit_bbox = (ux0, uy0, ux0 + 18000, uy0 + 17500)

    # ── 流程箭头（格栅→调节→提升泵→接触氧化→沉淀→消毒→出水）──
    # chain 每项 (sx, sy, ex, ey, 标签) 是**站区局部坐标下的起止点**。
    # 注意 draw_flow_arrow 会把 direction 归一化，只画 length*scale 长，
    # 因此这里必须按实际段长换算 length，否则所有箭头一律 800mm（实测"出水"
    # 段只画到 x≈4500 就断，远未到指定终点 17000）。
    # 末段"出水"自消毒池向东出至红线内：旧写法竖直向上 1500，箭尾越过红线
    # 1000（红线顶在站区局部 y=18000），注记也飘到图外，并压住坐标网格注记。
    chain = [
        (1000, 1000, 1000, 2500, "→"),
        (1000, 7500, 1000, 9000, "→"),
        (6000, 11500, 12000, 5500, "→沉淀"),
        (12000, 8000, 4000, 15500, "→消毒"),
        (4000, 16000, 17000, 16000, "→出水"),
    ]
    for sx, sy, ex, ey, lbl in chain:
        seg = math.hypot(ex - sx, ey - sy)
        draw_flow_arrow(msp, (ux0 + sx, uy0 + sy),
                        (ex - sx, ey - sy), scale,
                        length=seg / scale, label=lbl, tracker=tracker)

    # 提升泵标记（调节池与接触氧化池之间的 1500 净距内，即工艺流程上的提升工位）
    # 必须 avoid=False：净距是预留的，且竖向坐标网格线正落在 x=ux0+2500 上，
    # 开避让会被推走——实测被推入接触氧化池内（y 22190），平面图上把"提升泵"
    # 标到了生化池里。位置按设计给定，只登记占位。
    _t(msp, "提升泵", (ux0 + 3000, uy0 + 8250), 3 * s,
       layer="文字-标题", tracker=tracker, avoid=False)

    # ── 定位尺寸（GB/T 50103：总尺寸 + 坐标网格定位）──
    draw_linear_dimension(msp, (SX, SY), (SX + SITE_REDLINE_W, SY),
                          offset=7.0, scale=s, layer="尺寸标注", tracker=tracker)
    draw_linear_dimension(msp, (SX, SY), (SX, SY + SITE_REDLINE_H),
                          offset=7.0, scale=s, layer="尺寸标注", tracker=tracker)

    # ── 附表列：贴着构筑物块右侧竖排，避免撑大内容包络 ──
    col = AuxColumn(msp, unit_bbox, s, gap=2500.0, tracker=tracker)
    col.add(draw_tech_notes, "总平面技术要求",
            ["构筑物布置遵循工艺流程，自流段坡度>=0.3%。",
             "提升泵后管道为压力流，管径 DN80~DN150。",
             "构筑物间距满足施工与检修要求，>=800mm。",
             "厂区地面标高 0.000，事故排放口标高 -0.500。"],
            width=TECH_NOTE_W)
    col.add(draw_legend,
            [("pipe_solid", "工艺管路", "按图"),
             ("valve", "阀门", "按图"),
             ("arrow_flow", "水流方向", "顺工艺"),
             ("elevation", "标高", "m")],
            col_widths=(18, 32, 30))

    # ── 指北针（GB/T 50001 §7）：摆在附表列下方空白区，不额外撑大包络 ──
    if col.bbox is not None:
        draw_north_arrow(msp, (col.bbox[0] + 1500.0, col.bbox[1] - 4000.0), s,
                         tracker=tracker)

    path = save_dxf_autofit(doc, os.path.join(out_dir, "T4-01_总平面布置图.dxf"), scale, info, tracker)
    return path


# ═══════════════ 图2：调节池平剖面图 ═══════════════

def _sheet2_adjustment(out_dir, scale):
    doc, _, tracker = new_drawing(scale, return_tracker=True)
    msp = doc.modelspace()
    x0, y0, x1, y1, info = _frame(doc, scale, "调节池平剖面图", "T4-02", tracker=tracker)
    s = scale

    p = RectPoolParams(length=8000, width=5000, depth=4000, wall_thick=250,
                       material="C30钢筋混凝土", top_elev=0.000, bottom_elev=-4.000,
                       inlet_il=-0.500, outlet_il=-1.200, water_level=-0.300, name="调节池")

    # 底部优先：先定剖面位置，再在上方放平面（避免剖面包络溢出图框底部）
    SECTION_EST = p.depth + p.bottom_thick + 3500   # 池体+底板+标高标注
    sec_ox = x0 + MARGIN_EDGE + 1000
    sec_oy = y0 + MARGIN_EDGE + 2500                 # 底部留标高空间
    # 平面图（剖面上方）
    plan_ox = x0 + MARGIN_EDGE + 1000
    plan_oy = sec_oy + SECTION_EST + LAYOUT_GAP
    plan_bbox = draw_rect_pool_plan(msp, (plan_ox, plan_oy), p, scale, tracker=tracker)
    draw_rect_pool_section(msp, sec_ox, sec_oy, p, scale, tracker=tracker)

    # 视图标签
    _t(msp, "平面图", (plan_bbox[0] + (plan_bbox[2] - plan_bbox[0]) / 2, plan_oy + 8 * s),
       3.5 * s, layer="文字-标题", tracker=tracker)
    _t(msp, "1-1 剖面图", (sec_ox + 4000, sec_oy + 8 * s), 3.5 * s,
       layer="文字-标题", tracker=tracker)

    # 技术要求（右侧，错开剖面标高）
    _aux_col(msp, s, tracker).add(
                    draw_tech_notes,
                    "调节池技术要求",
                    ["池体 C30 钢筋混凝土，抗渗 P6，壁厚 250mm。",
                     "进水管内底标高 -0.500，出水管内底标高 -1.200。",
                     "池底设 1% 坡度坡向吸水坑，坑深 300mm。",
                     "内壁做环氧树脂玻璃钢防腐（两布三油）。",
                     "施工及验收执行 GB 50141—2008。"],
                    width=TECH_NOTE_W,
                    tracker=tracker)

    return save_dxf_autofit(doc, os.path.join(out_dir, "T4-02_调节池平剖面图.dxf"), scale, info, tracker)


# ═══════════════ 图3：接触氧化池平剖面图 ═══════════════

def _sheet3_contact_oxidation(out_dir, scale):
    doc, _, tracker = new_drawing(scale, return_tracker=True)
    msp = doc.modelspace()
    x0, y0, x1, y1, info = _frame(doc, scale, "接触氧化池平剖面图", "T4-03", tracker=tracker)
    s = scale

    p = RectPoolParams(length=6000, width=4000, depth=4000, wall_thick=250,
                       material="C30钢筋混凝土", top_elev=0.000, bottom_elev=-4.000,
                       inlet_il=-1.200, outlet_il=-1.250, water_level=-0.300, name="接触氧化池")

    # 底部优先：先定剖面位置，再在上方放平面
    SECTION_EST3 = p.depth + p.bottom_thick + 3500
    sec_ox3 = x0 + MARGIN_EDGE + 1000
    sec_oy3 = y0 + MARGIN_EDGE + 2500
    # 平面图（剖面上方）
    plan_ox3 = x0 + MARGIN_EDGE + 1000
    plan_oy3 = sec_oy3 + SECTION_EST3 + LAYOUT_GAP
    plan_bbox3 = draw_rect_pool_plan(msp, (plan_ox3, plan_oy3), p, scale, tracker=tracker)

    # 填料区（平面虚线框 → 放在池内）
    fx0, fy0 = plan_ox3 + 300, plan_oy3 + 300
    fw, fh = p.length - 600, p.width - 600
    _poly(msp, [(fx0, fy0), (fx0 + fw, fy0), (fx0 + fw, fy0 + fh), (fx0, fy0 + fh)], "虚线")
    _t(msp, "组合填料", (fx0 + fw / 2, fy0 + fh / 2), 3 * s,
       align=TextEntityAlignment.MIDDLE_CENTER, layer="文字", tracker=tracker)

    # 剖面图（已在 init 区确定 sec_ox3/sec_oy3）
    sec_bbox = draw_rect_pool_section(msp, sec_ox3, sec_oy3, p, scale, tracker=tracker)

    # 曝气管（剖面图内）
    aer_y = sec_oy3 - p.depth + 1500  # 距顶 1.5m
    _line(msp, (sec_ox3 + p.wall_thick, aer_y), (sec_ox3 + p.wall_thick + p.length, aer_y), "管道-给水")
    _t(msp, "曝气管", (sec_ox3 + p.wall_thick + p.length / 2, aer_y + 2 * s),
       2.5 * s, layer="文字", tracker=tracker)

    # 标签
    _t(msp, "平面图", (plan_ox3 + p.length / 2 + p.wall_thick, plan_oy3 + 8 * s),
       3.5 * s, layer="文字-标题", tracker=tracker)
    _t(msp, "1-1 剖面图", (sec_ox3 + p.length / 2 + p.wall_thick, sec_oy3 + 8 * s),
       3.5 * s, layer="文字-标题", tracker=tracker)

    _aux_col(msp, s, tracker).add(
                    draw_tech_notes,
                    "接触氧化池技术要求",
                    ["池体 C30 钢筋混凝土，抗渗 P6，壁厚 250mm。",
                     "填料采用组合填料，填充率 70%，安装高度 3.0m。",
                     "底部设微孔曝气器，曝气量 0.6m /(m h)。",
                     "气水比 8:1~12:1，DO 控制 2~3mg/L。",
                     "施工及验收执行 GB 50141—2008。"],
                    width=TECH_NOTE_W,
                    tracker=tracker)

    return save_dxf_autofit(doc, os.path.join(out_dir, "T4-03_接触氧化池平剖面图.dxf"), scale, info, tracker)


# ═══════════════ 图4：斜管沉淀池平剖面图 ═══════════════

def _sheet4_settler(out_dir, scale):
    doc, _, tracker = new_drawing(scale, return_tracker=True)
    msp = doc.modelspace()
    x0, y0, x1, y1, info = _frame(doc, scale, "斜管沉淀池平剖面图", "T4-04", tracker=tracker)
    s = scale

    # 圆形池平面（左半区）
    plan_cx = x0 + MARGIN_EDGE + 4000
    plan_cy = y0 + 12000
    plan_bbox = draw_circular_pool_plan(msp, (plan_cx, plan_cy), 6000, 250, scale,
                                        name="斜管沉淀池", inlet_dn=300, sludge_dn=150,
                                        tracker=tracker)

    # 剖面（右半区，平面对齐）
    sec_ox = plan_bbox[2] + LAYOUT_GAP
    sec_oy = y0 + 22000
    draw_circular_pool_section(msp, sec_ox, sec_oy, 6000, 5.5, 1.5, 1.2, 250, scale,
                               top_elev=0.300, name="斜管沉淀池",
                               inlet_dn=300, sludge_dn=150, tracker=tracker)

    _t(msp, "平面图", (plan_cx, plan_bbox[1] - 5 * s), 3.5 * s,
       layer="文字-标题", tracker=tracker)
    _t(msp, "1-1 剖面图", (sec_ox + 3000, sec_oy + 8 * s), 3.5 * s,
       layer="文字-标题", tracker=tracker)

    _aux_col(msp, s, tracker).add(
                    draw_tech_notes,
                    "斜管沉淀池技术要求",
                    ["池体 C30 钢筋混凝土，抗渗 P6，壁厚 250mm。",
                     "斜管蜂窝填料 80，倾角 60，斜管区高 1.2m。",
                     "中心进水管 DN300，排泥管 DN150。",
                     "周边三角出水堰，堰口高差<=2mm。",
                     "施工及验收执行 GB 50141—2008。"],
                    width=TECH_NOTE_W,
                    tracker=tracker)

    return save_dxf_autofit(doc, os.path.join(out_dir, "T4-04_斜管沉淀池平剖面图.dxf"), scale, info, tracker)


# ═══════════════ 图5：工艺管道平面图 ═══════════════

def _sheet5_piping(out_dir, scale):
    doc, _, tracker = new_drawing(scale, return_tracker=True)
    msp = doc.modelspace()
    x0, y0, x1, y1, info = _frame(doc, scale, "工艺管道平面图", "T4-05", tracker=tracker)
    s = scale

    # 主工艺管线 — 增大节点间距
    py = y0 + 12000
    seg_start = x0 + MARGIN_EDGE + 2000
    seg_xs = [seg_start, seg_start + 5000, seg_start + 10000,
              seg_start + 15000, seg_start + 20000, seg_start + 25000,
              seg_start + 30000, seg_start + 35000]
    seg = [(x, py) for x in seg_xs]

    for i in range(len(seg) - 1):
        draw_pipe(msp, seg[i], seg[i + 1], dn=150, scale=scale,
                  style="single", layer="管道-污水")

    # 管件（v1.5 扩展：多种阀门类型）
    draw_butterfly_valve(msp, seg[1], scale, "h", actuator="pneumatic",
                         label="气动蝶阀")
    draw_flow_meter(msp, seg[2], scale, "h", label="电磁流量计")
    draw_valve(msp, (seg_start + 7500, py), scale, "h", label="闸阀")
    draw_diaphragm_valve(msp, seg[3], scale, "h", fail_mode="nc",
                         lined=True, label="衬胶隔膜阀")
    draw_check_valve(msp, seg[4], scale, "h")
    draw_globe_valve(msp, seg[5], scale, "h", label="截止阀")
    draw_ball_valve(msp, seg[6], scale, "h", label="球阀")

    # 取样阀（支管）
    draw_sampling_valve(msp, (seg_start + 17500, py), scale, "h",
                        label="取样阀")

    # 软接头
    draw_soft_joint(msp, (seg_start + 7500, py), scale, "h",
                    layer="管道-污水")

    # 标注（上下交错防重叠）
    draw_pipe_diameter(msp, seg[2], "DN150", scale, leader_dir=(0, 1), tracker=tracker)
    draw_elevation(msp, seg[0], "-1.200", scale, side="left", level=0, tracker=tracker)
    draw_elevation(msp, seg[-1], "-1.236", scale, side="right", level=0, tracker=tracker)
    draw_flow_arrow(msp, (seg_start + 17500, py - 12 * s), (1, 0), scale,
                    length=20, label="水流方向", tracker=tracker)

    # 构筑物名称（管线上方）
    # 旧写法贴在 py+8*s：与管件自身的标记（隔膜阀的 NC）和仪表符号挤在同一行，
    # 实测"接触氧化池"和阀门的 "NC" 落在同一点 (22500,13800)。上抬到仪表行
    # （py+14*s）之上另起一行，并居中于节点。
    names = ["格栅", "调节池", "提升泵", "接触氧化池", "沉淀池",
             "过滤", "消毒池", "出水"]
    for (px, _), n in zip(seg, names):
        _t(msp, n, (px, py + 26 * s), 3 * s,
           align=TextEntityAlignment.MIDDLE_CENTER, layer="文字-标题",
           tracker=tracker)

    # 仪表符号（PH计、ORP、液位计）
    instr_y = py + 14 * s
    draw_instrument_symbol(msp, (seg_start + 2500, instr_y), scale,
                           tag="PH-101", instr_type="ph",
                           mounting="field", label="PH计")
    draw_instrument_symbol(msp, (seg_start + 22500, instr_y), scale,
                           tag="ORP-101", instr_type="orp",
                           mounting="panel", label="ORP仪")
    draw_instrument_symbol(msp, (seg_start + 32500, instr_y), scale,
                           tag="LT-101", instr_type="level",
                           mounting="dcs", label="液位计")

    # 加药点（PAC/PAM）
    dose_y = py - 10 * s
    _line(msp, (seg_start + 12500, py), (seg_start + 12500, dose_y),
          "管道-污水")
    _t(msp, "PAC投加", (seg_start + 12500 + 2 * s, dose_y), 2.5 * s,
       align=TextEntityAlignment.MIDDLE_LEFT, layer="文字")
    _line(msp, (seg_start + 27500, py), (seg_start + 27500, dose_y),
          "管道-污水")
    _t(msp, "PAM投加", (seg_start + 27500 + 2 * s, dose_y), 2.5 * s,
       align=TextEntityAlignment.MIDDLE_LEFT, layer="文字")

    _col5 = _aux_col(msp, s, tracker)
    _col5.add(draw_legend,
                [("pipe_solid", "污水管", "DN150 UPVC"),
                 ("valve_butterfly", "气动蝶阀", "DN150"),
                 ("valve_diaphragm_lined", "衬胶隔膜阀", "DN150"),
                 ("valve_globe", "截止阀", "DN100"),
                 ("valve_ball", "球阀", "DN80"),
                 ("valve_check", "止回阀", "DN150"),
                 ("valve_sampling", "取样阀", "DN25"),
                 ("flow_meter", "电磁流量计", "DN150"),
                 ("soft_joint", "橡胶软接头", "DN150"),
                 ("instr_field", "就地仪表", "PH/ORP"),
                 ("arrow_flow", "水流方向", "顺工艺")],
                tracker=tracker)

    _col5.add(draw_tech_notes, "管道技术要求",
                    ["工艺管材 UPVC，承插粘接；压力流采用碳钢衬塑。",
                     "重力流段坡度 0.3%，压力流段按泵扬程配置。",
                     "管道穿墙设刚性防水套管，翼环厚度≥6mm。",
                     "阀门：气动蝶阀用于工艺主管，衬胶隔膜阀用于加药管。",
                     "仪表：PH/ORP/液位 均设就地显示+远传至PLC。",
                     "施工及验收执行 GB 50268—2008。"],
                    width=TECH_NOTE_W,
                    tracker=tracker)

    return save_dxf_autofit(doc, os.path.join(out_dir, "T4-05_工艺管道平面图.dxf"), scale, info, tracker)


# ═══════════════ 图6：设备材料表 ═══════════════

def _table_text_w(text, h):
    """表格列宽专用字宽（比 ``annotate._estimate_text_width`` 更保守）。

    后者按 CJK 0.85em / ASCII 0.50em 估宽再乘 1.15 安全系数（≈ CJK 0.98em /
    ASCII 0.58em）。实测出图字体下上标、单位符号一类仍会超出格子。表格里
    "压字"比"列宽富余"严重得多，故这里取 CJK 1.00em / ASCII 0.62em。
    """
    w = 0.0
    for ch in str(text):
        w += h * (1.0 if ord(ch) > 127 else 0.65)
    return w


def _sheet6_material(out_dir, scale):
    doc, _, tracker = new_drawing(scale, return_tracker=True)
    msp = doc.modelspace()
    x0, y0, x1, y1, info = _frame(doc, scale, "设备材料表", "T4-06", tracker=tracker)
    s = scale

    rows = [
        ("1", "机械格栅", "B=600 b=5mm N=0.75kW", "台", "1"),
        ("2", "潜水提升泵", "WQ15-7-1.1 Q=15m³/h H=7m", "台", "2"),
        ("3", "立式多级泵", "CDL4-80 Q=4m³/h H=80m", "台", "2"),
        ("4", "罗茨鼓风机", "SSR50 Q=2.5m³/min P=39.2kPa", "台", "2"),
        ("5", "桨叶式搅拌机", "N=2.2kW 桨径600", "台", "2"),
        ("6", "潜水搅拌机", "QJB1.5/6 N=1.5kW", "台", "1"),
        ("7", "PAC加药装置", "500L 一用一备 计量泵", "套", "1"),
        ("8", "PAM加药装置", "1000L 一体化 含搅拌", "套", "1"),
        ("9", "二氧化氯发生器", "500g/h 复合型", "套", "1"),
        ("10", "气动蝶阀", "DN150 PN10 气动", "个", "4"),
        ("11", "衬胶隔膜阀", "DN100 气动常闭", "个", "3"),
        ("12", "不锈钢截止阀", "DN80 PN16", "个", "6"),
        ("13", "球阀", "DN50 UPVC", "个", "8"),
        ("14", "止回阀", "DN150", "个", "2"),
        ("15", "电磁流量计", "DN150 一体式", "台", "1"),
        ("16", "PH/ORP计", "工业在线式", "套", "2"),
        ("17", "超声波液位计", "0-5m 4-20mA", "台", "3"),
        ("18", "微孔曝气器", "Φ215 盘式", "套", "40"),
        ("19", "组合填料", "Φ150 L=3000", "m³", "20"),
        ("20", "蜂窝斜管", "Φ80 L=1000 PP", "m²", "30"),
        ("21", "UPVC管", "DN150", "m", "60"),
        ("22", "碳钢衬塑管", "DN100", "m", "30"),
        ("23", "HDPE双壁波纹管", "DN300 SN8", "m", "12"),
        ("24", "刚性防水套管", "DN150", "套", "6"),
        ("25", "橡胶软接头", "DN150", "个", "4"),
        ("26", "三角堰板", "304不锈钢", "m", "18"),
    ]

    ox, oy = x0 + MARGIN_EDGE + 2000, y1 - 6000

    # ── 列宽按内容实算 ──
    # 旧写法把列宽写死（8/24/36/10/10 图纸 mm），"SSR50 Q=2.5m³/min P=39.2kPa"
    # 一类长规格串超出 36mm 列宽，于是触发 `_t` 的碰撞避让、把文字推离单元格，
    # 整表白读。现按每列实际最大字宽定宽，并给单元格统一关掉避让（avoid=False）。
    headers = ["序号", "名称", "规格", "单位", "数量"]
    cell_h = 2.6 * s
    head_h = 3.2 * s
    col_pad = 8.0 * s                      # 单元格净空（左右各 4mm）
    cols = []
    for i, hd in enumerate(headers):
        w = max([_table_text_w(hd, head_h)] +
                [_table_text_w(r[i], cell_h) for r in rows])
        cols.append(max(w + col_pad, 8 * s))
    rh = 5.5 * s
    title_h = 7 * s
    total_w = sum(cols)

    # 表头
    cx = ox
    for i, h in enumerate(headers):
        _t(msp, h, (cx + cols[i] / 2, oy - title_h / 2 + 0.5 * s), head_h,
           align=TextEntityAlignment.MIDDLE_CENTER, layer="文字-标题",
           tracker=tracker, avoid=False)
        cx += cols[i]

    msp.add_lwpolyline([(ox, oy), (ox + total_w, oy), (ox + total_w, oy - title_h),
                        (ox, oy - title_h)], close=True, dxfattribs={"layer": "附表"})

    for j in range(1, len(headers)):
        xx = ox + sum(cols[:j])
        msp.add_line((xx, oy), (xx, oy - title_h - len(rows) * rh), dxfattribs={"layer": "附表"})

    # 数据行
    for r, row in enumerate(rows):
        ry = oy - title_h - r * rh
        msp.add_line((ox, ry), (ox + total_w, ry), dxfattribs={"layer": "附表"})
        cx = ox
        for i, val in enumerate(row):
            _t(msp, val, (cx + cols[i] / 2, ry - rh / 2 + 0.5 * s), cell_h,
               align=TextEntityAlignment.MIDDLE_CENTER, layer="文字",
               tracker=tracker, avoid=False)
            cx += cols[i]

    # 底线
    bottom_y = oy - title_h - len(rows) * rh
    msp.add_line((ox, bottom_y), (ox + total_w, bottom_y), dxfattribs={"layer": "附表"})
    msp.add_line((ox, oy - title_h), (ox, oy - title_h - len(rows) * rh), dxfattribs={"layer": "附表"})

    # 表名居中于表格（旧写法居中于 refit 前的整张图框，表名被甩到表格右侧很远）
    _t(msp, "设备材料表", (ox + total_w / 2, bottom_y - 6 * s), 5 * s,
       align=TextEntityAlignment.MIDDLE_CENTER, layer="文字-标题",
       tracker=tracker, avoid=False)

    return save_dxf_autofit(doc, os.path.join(out_dir, "T4-06_设备材料表.dxf"), scale, info, tracker)
