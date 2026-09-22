"""国标图层定义（GB/T 17450 图线 + 环保工程常用图层）。

线宽约定（出图 mm）：粗 0.5 / 中 0.35 / 细 0.18。
ezdxf lineweight 单位为 1/100 mm。

**基本线宽 b = 0.5mm**（GB/T 50001—2017 §4.0.1 的 b 系列为 1.4/1.0/0.7/0.5）。
表 4.0.4 里图框与标题栏各线的宽度都以 b 的倍数给出，见下方 ``FRAME_*`` 常量。
"""
from __future__ import annotations

import ezdxf

#: 基本线宽 b（出图 mm）。图框线、粗实线均取此值。
BASE_LINE_WIDTH_MM = 0.5

# DXF 允许的线宽枚举（1/100mm）。
# 公差是"四舍五入到最近合法值"——DXF 不接受任意小数值。
_DXF_LINEWEIGHTS = (0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60,
                    70, 80, 90, 100, 106, 120, 140, 158, 200, 211)


def mm_to_lineweight(mm: float) -> int:
    """把出图线宽（mm）圆整到 DXF 的合法线宽枚举（1/100mm）。

    GB/T 50001—2017 表 4.0.4 的 0.35b 在 b=0.5 时为 0.175mm，不是标准线宽
    系列里的数；DXF 也不接受 17.5。这里取最近的合法值 0.18mm（=18）。
    """
    target = mm * 100.0
    return min(_DXF_LINEWEIGHTS, key=lambda v: (abs(v - target), v))


# 表 4.0.4 图框和标题栏线的宽度（出图 mm；A2/A3/A4）
FRAME_LINEWEIGHT_MM = BASE_LINE_WIDTH_MM                  # 图框线 = b
MARK_LINEWEIGHT_MM = 0.7 * BASE_LINE_WIDTH_MM             # 对中标志 = 标题栏外框线 = 0.7b
TITLE_OUTER_LINEWEIGHT_MM = MARK_LINEWEIGHT_MM            # 标题栏外框线 = 0.7b
TRIM_LINEWEIGHT_MM = 0.35 * BASE_LINE_WIDTH_MM            # 幅面线 = 标题栏分格线 = 0.35b
# A0/A1 幅面：外框线/对中标志 0.5b，幅面线/分格线 0.25b
BIG_MARK_LINEWEIGHT_MM = 0.5 * BASE_LINE_WIDTH_MM
BIG_TRIM_LINEWEIGHT_MM = 0.25 * BASE_LINE_WIDTH_MM

# (图层名, ACI颜色, 线型, 线宽1/100mm)
LAYER_DEFS = [
    # —— 基本图线 ——
    ("粗实线",        7, "CONTINUOUS", 50),   # 主轮廓
    ("中实线",        7, "CONTINUOUS", 35),   # 次轮廓
    ("细实线",        7, "CONTINUOUS", 18),   # 辅助、表格
    ("细实线-尺寸",   3, "CONTINUOUS", 18),   # 尺寸线（绿）
    ("细实线-辅助",   8, "CONTINUOUS", 18),
    ("虚线",          2, "DASHED",    35),    # 不可见轮廓（黄）
    ("点画线",        1, "CENTER",    18),    # 中心线/对称线（红）
    ("中心线",        1, "CENTER",    18),    # 中心线别名（兼容 mechanical 等模块）
    ("双点画线",      6, "PHANTOM",   18),    # 假想线
    # —— 工程图专用 ——
    # 图框线 = 基本线宽 b（GB/T 50001—2017 表 4.0.4）。
    # 注意：幅面线（纸边）与对中标志虽然同在图框层，但按表 4.0.4 应更细/更粗，
    # 二者由 frame.draw_frame 用**实体级 lineweight** 单独设定，不靠图层默认值。
    ("图框",          7, "CONTINUOUS", 50),  # 图框线 = b = 0.5mm（表4.0.4）
    # 标题栏：整块（框线+分格+文字）独占一层。
    # 原因：出图前要按内容重选幅面并重画图框，若标题栏文字混在"文字"/"文字-标题"
    # 层，重画时会残留旧标题栏（实测 T1 残留 11 条文字，导致内容包络虚胖一倍）。
    ("标题栏",        7, "CONTINUOUS", 35),
    # 附表：技术要求框 / 技术特性表 / 设备材料表 / 图例框。
    # 独立于"图框"层的原因：图框是标准幅面边界，会在出图前按内容重选幅面而重画；
    # 附表是内容的一部分，必须参与内容外包络计算（否则会被图框切掉）。
    ("附表",          7, "CONTINUOUS", 18),
    ("文字",          7, "CONTINUOUS", 18),
    ("文字-标题",     7, "CONTINUOUS", 35),
    ("剖面线",        7, "CONTINUOUS", 18),
    # 网格 0.09mm 低于 GB/T 50001—2017 §4.0.1 条文说明的"图线宽度不应小于
    # 0.1mm"下限，改用 0.13mm（b=0.5 线宽组里的 0.25b）。
    ("网格",          8, "CONTINUOUS", 13),
    # —— 环保工艺 ——
    ("管道-污水",     4, "CONTINUOUS", 50),   # 青
    ("管道-给水",     5, "CONTINUOUS", 50),   # 蓝
    ("管道-加药",     6, "CONTINUOUS", 35),
    ("池体-壁",       7, "CONTINUOUS", 50),
    ("池体-水",       4, "CONTINUOUS", 18),
    ("设备",          2, "CONTINUOUS", 35),
    ("阀门",          1, "CONTINUOUS", 35),
    ("标高",          3, "CONTINUOUS", 18),
    ("图例",          7, "CONTINUOUS", 18),
    ("流向",          1, "CONTINUOUS", 35),
]

# 标准线型定义（dash-gap 序列，单位 mm）
LINETYPE_DEFS = {
    "DASHED":  ([2.0, -1.0], "__ __ __"),
    "CENTER":  ([4.0, -1.0, 0.5, -1.0], "____ . ____"),
    "PHANTOM": ([4.0, -1.0, 0.5, -1.0, 0.5, -1.0], "____ . ____ . ____"),
}


def _ensure_linetype(doc, name: str) -> None:
    """确保线型存在，失败回退 CONTINUOUS。"""
    if name == "CONTINUOUS":
        return
    # 修复：使用正确的 API 检查线型是否存在
    try:
        if doc.linetypes.has_entry(name):
            return
    except Exception as _e:
        # 兼容旧版 ezdxf
        try:
            if name in doc.linetypes:
                return
        except Exception as _e:
            print(f'[WARNING] layers.py: {_e}')
    if name not in LINETYPE_DEFS:
        return
    pattern, desc = LINETYPE_DEFS[name]
    try:
        doc.linetypes.add(name, pattern, desc)
    except Exception as _e:
        # ezdxf 不同版本 API 差异，二次尝试
        try:
            doc.linetypes.add(name, pattern=pattern, description=desc)
        except Exception as _e:
            # 最终兜底
            try:
                doc.linetypes.add(name, [2.5, -1.0], desc)
            except Exception as _e:
                print(f'[WARNING] layers.py: {_e}')


def setup_layers(doc: ezdxf.document.Drawing) -> None:
    """创建全部国标图层与线型。"""
    # 先建线型
    for lt in ("DASHED", "CENTER", "PHANTOM"):
        _ensure_linetype(doc, lt)
    # 再建图层
    for name, color, ltype, lw in LAYER_DEFS:
        if name in doc.layers:
            continue
        try:
            layer = doc.layers.add(name)
            layer.dxf.color = color
            # 修复：正确验证线型可用性
            if ltype == "CONTINUOUS":
                layer.dxf.linetype = "CONTINUOUS"
            else:
                # 检查线型是否真正可用
                try:
                    if doc.linetypes.has_entry(ltype):
                        layer.dxf.linetype = ltype
                    else:
                        # 尝试强制添加
                        _ensure_linetype(doc, ltype)
                        if doc.linetypes.has_entry(ltype):
                            layer.dxf.linetype = ltype
                        else:
                            layer.dxf.linetype = "CONTINUOUS"
                except Exception as _e:
                    # 兼容旧版
                    layer.dxf.linetype = "CONTINUOUS"
            layer.dxf.lineweight = lw
        except Exception as _e:
            print(f'[WARNING] layers.py: {_e}')


def layer_of(doc, name: str) -> str:
    """安全取图层名：不存在则回退到 细实线。"""
    return name if name in doc.layers else "细实线"
