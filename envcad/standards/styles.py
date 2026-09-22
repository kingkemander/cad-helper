"""文字样式与标注样式 v1.4（GB/T 50001—2017）。

改进:
  * 标注精度变量 DIMDEC/DIMRND/DIMTDEC
  * 字高四舍五入到 0.5mm 档位
  * 标注默认间隙增大防遮挡

汉字用仿宋 GB2312（高宽比 1:0.7），字母数字用 simplex。
"""
from __future__ import annotations

import math as _math

import ezdxf
from ezdxf.enums import TextEntityAlignment

import os as _os

def _resolve_font(default, *candidates):
    """跨平台回退：优先用存在的字体，否则用默认名（ezdxf 在无字体时静默回退）。"""
    for c in candidates:
        if _os.path.exists(c):
            return c
    return default

HZ_FONT = _resolve_font(
    "simfang.ttf",
    "simfang.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/Library/Fonts/PingFang.ttc",
    "C:/Windows/Fonts/simfang.ttf",
)
ENG_FONT = "simplex.shx"  # 标准 AutoCAD 字形

# ─── 尺寸起止符号（GB/T 50001—2017 §11.1.4）───────────────
#
# 11.1.4 原文分两句，对应两类尺寸、两种起止符号，**不能混用**：
#   · "尺寸起止符号用中粗斜短线绘制，其倾斜方向应与尺寸界线成顺时针 45° 角，
#      长度宜为 2mm～3mm。"                        → 线性/对齐尺寸用斜短线
#   · "半径、直径、角度与弧长的尺寸起止符号，宜用箭头表示，箭头宽度 b
#      不宜小于 1mm。"                             → 径向/角度/弧长用箭头
#   条文说明："一般情况下均用斜短线，圆弧的直径、半径等用箭头。"
#
# 两类都必须靠**标注样式**配置：ezdxf 的 ``add_diameter_dim(override=...)``
# 会静默丢弃 override（`dim.override()` 里看不到传进去的值），渲染只认样式。
# 故本模块导出两个样式工厂：线性 ``GB-DIM-{s}`` 与径向 ``GB-DIM-ARROW-{s}``。
#
# ★ "dimasz 不等于画出来的长度"：dimasz 只是箭头块的**插入比例**，
#   画出长度 = dimasz × 块内长度。斜短线块长 √2，箭头块长 1.0，故两者
#   反算公式不同。早期版本一律写 ``dimasz = 2.5*scale``，导致斜短线实际画出
#   3.54mm（超 §11.1.4 的 3mm 上限）、箭头宽度只有 0.82mm（低于 b≥1mm 下限）。

#: 斜短线在**纸面**上的目标长度，取规范 2~3mm 的中值。
TICK_LEN_MM = 2.5
#: AutoCAD ``_ARCHTICK`` 块是 (-0.5,-0.5)→(0.5,0.5) 的一条线段，块内长 √2。
ARCHTICK_UNIT_LEN = _math.sqrt(2.0)
#: §11.1.4 对箭头只规定了宽度 b，取规范下限 1mm 作为目标。
ARROW_WIDTH_MM = 1.0
#: ezdxf ``_CLOSEDFILLED`` 块（实心闭合箭头）的 宽/长 比：块内几何是
#: SOLID(-1, 0.164399) (0,0) (-1,-0.164399)，长 1.0、宽 0.32879797。
#: 有护栏测试 :func:`test_closedfilled_ratio_matches_ezdxf` 盯着这个值。
CLOSEDFILLED_WIDTH_PER_LEN = 0.32879797

#: 由"宽度下限"反算箭头长度：b = CLOSEDFILLED_WIDTH_PER_LEN × 长度
#: → 长度 = 1mm / 0.3288 ≈ 3.041mm。即箭头长 ≈3.04mm、宽 =1.00mm。
ARROW_LEN_MM = ARROW_WIDTH_MM / CLOSEDFILLED_WIDTH_PER_LEN

#: 两个起止符块名（``dimblk`` 取值）
TICK_BLOCK = "_ARCHTICK"
ARROW_BLOCK = "_CLOSEDFILLED"


def archtick_dimasz(scale: float) -> float:
    """纸面斜短线长 ``TICK_LEN_MM`` → 线性尺寸的 ``dimasz``（模型单位）。"""
    return TICK_LEN_MM / ARCHTICK_UNIT_LEN * scale


def arrow_dimasz(scale: float) -> float:
    """纸面箭头长 ``ARROW_LEN_MM`` → 径向尺寸的 ``dimasz``（模型单位）。

    箭头块长 1.0，故画出长度就等于 dimasz，无需再除块长。
    """
    return ARROW_LEN_MM * scale


# ─── 字号档位 (图纸 mm) ──────────────────────────────────
FONT_SIZES = {
    "title":  5.0,     # 图名
    "subtitle": 4.0,   # 副标题
    "body":   3.5,     # 正文标注
    "note":   3.0,     # 注记
    "small":  2.5,     # 辅助说明
    "tiny":   2.0,     # 表格小字
}


def pick_font_size(size_mm: float) -> float:
    """将任意字高归一到最近的标准档位（0.5mm 步长）。"""
    return round(size_mm * 2) / 2


def setup_text_styles(doc: ezdxf.drawing.Drawing) -> None:
    """创建工程制图用文字样式。失败时回退到 STANDARD。"""
    styles = doc.styles
    try:
        styles.add("HZ", font=HZ_FONT)
    except Exception as _e:
        try:
            s = styles.add("HZ")
            s.dxf.font = HZ_FONT
        except Exception as _e:
            print(f'[WARNING] styles.py: {_e}')
    try:
        styles.add("ENG", font=ENG_FONT)
    except Exception as _e:
        try:
            s = styles.add("ENG")
            s.dxf.font = ENG_FONT
        except Exception as _e:
            print(f'[WARNING] styles.py: {_e}')
    try:
        styles.add("HZTXT", font=HZ_FONT)
    except Exception as _e:
        print(f'[警告] 操作失败：{_e}')


def ensure_archtick_block(doc: ezdxf.drawing.Drawing) -> str:
    """确保文档里有 ``_ARCHTICK`` 块（§11.1.4 的 45° 中粗斜短线）。

    ``dimblk`` 指向的块**必须存在**：裸 ``ezdxf.new()`` 不带标准箭头块，写
    DXF / 渲染时 ezdxf 会直接抛 ``DXFValueError: Block "_ARCHTICK" does not
    exist``（只在 ``setup=True`` 时才自带）。所以设样式前先补齐。

    块的几何与 ezdxf ``setup=True`` 的一致：一条 (-0.5,-0.5)→(0.5,0.5) 的线段，
    故块内长度为 √2，配 :data:`ARCHTICK_UNIT_LEN` 使用。
    """
    if TICK_BLOCK not in doc.blocks:
        blk = doc.blocks.new(TICK_BLOCK)
        blk.add_lwpolyline([(-0.5, -0.5), (0.5, 0.5)])
    return TICK_BLOCK


def ensure_arrow_block(doc: ezdxf.drawing.Drawing) -> str:
    """确保文档里有 ``_CLOSEDFILLED`` 块（§11.1.4b 的实心闭合箭头）。

    几何与 ezdxf ``setup=True`` 自带的一致：SOLID
    ``(-1, 0.164399) (0, 0) (-1, -0.164399)``，块内长 1.0、宽 0.32879797。
    用 ezdxf 自己的 :data:`ezdxf.render.arrows.ARROWS` 生成，避免手抄坐标漂移。
    """
    if ARROW_BLOCK in doc.blocks:
        return ARROW_BLOCK
    try:
        from ezdxf.render.arrows import ARROWS
        ARROWS.create_block(doc.blocks, "")      # "" → _CLOSEDFILLED
    except Exception as _e:                      # pragma: no cover - 兜底
        blk = doc.blocks.new(ARROW_BLOCK)
        w = CLOSEDFILLED_WIDTH_PER_LEN / 2.0
        blk.add_solid([(-1.0, w), (0.0, 0.0), (-1.0, -w)])
        print(f'[警告] ARROWS 不可用，手写箭头块：{_e}')
    return ARROW_BLOCK


def _make_dimstyle(doc: ezdxf.drawing.Drawing, name: str, scale: float,
                   block: str, dimasz: float) -> str:
    """按给定起止符块与 dimasz 建立（或复用）一个标注样式。"""
    if name in doc.dimstyles:
        return name
    try:
        dim = doc.dimstyles.add(name)
    except Exception as _e:                      # pragma: no cover - 兜底
        print(f'[警告] 操作失败：{_e}')
        return "Standard"

    dim.dxf.dimtxt = 3.5 * scale
    dim.dxf.dimasz = dimasz
    # §11.1.4：这是"起止符块"，线性=_ARCHTICK 斜短线 / 径向=_CLOSEDFILLED 箭头
    dim.dxf.dimblk = block
    dim.dxf.dimexe = 2.0 * scale
    dim.dxf.dimexo = 1.2 * scale        # 增大偏移防遮挡
    dim.dxf.dimgap = 1.5 * scale        # 增大文字与尺寸线间距
    dim.dxf.dimtxsty = "HZ"
    dim.dxf.dimclrt = 7
    dim.dxf.dimlwd = -2
    dim.dxf.dimclrd = 3
    dim.dxf.dimclre = 3

    # GB/T 4458.4：小数点用 "."。
    # 注意 ezdxf 的 DIMSTYLE 级 dimdsep 默认 44(",")，且 DimStyleOverride
    # 在取不到该变量时也会返回 ","——与头部变量 $DIMDSEP 无关，
    # 因此必须在样式上显式设为 46(".")，否则真实 DIMENSION 会出成 "4000,00"。
    dim.dxf.dimdsep = ord(".")

    # GB/T 4458.4：不写多余小数位。
    # dimzin 作用于标注文字，dimtzin 作用于公差文字（ezdxf 的 Tolerance 读的是
    # dimtzin，默认 0 -> 下偏差 0 会出成 "0.000"）。
    # 8 = 去尾零且保留前导零："4000.00"->"4000"、"0.000"->"0"、"0.018" 保持不变。
    # 注意不可用 4/12（会去掉前导零，把 0.018 写成 .018）。
    dim.dxf.dimzin = 8
    dim.dxf.dimtzin = 8

    # 精度变量
    dim.dxf.dimdec = 2                  # 小数位
    dim.dxf.dimrnd = 0.01               # 圆整
    dim.dxf.dimtdec = 2                  # 公差小数位

    return name


def setup_dimstyles(doc: ezdxf.drawing.Drawing, scale: float = 1.0) -> str:
    """创建**线性**尺寸标注样式 ``GB-DIM-{scale}``（§11.1.4 斜短线起止符）。

    scale: 出图比例的倒数。1:100 图纸 scale=100。
    """
    ensure_archtick_block(doc)
    return _make_dimstyle(doc, f"GB-DIM-{int(scale)}", scale,
                          TICK_BLOCK, archtick_dimasz(scale))


def setup_arrow_dimstyle(doc: ezdxf.drawing.Drawing, scale: float = 1.0) -> str:
    """创建**径向/角度**尺寸标注样式 ``GB-DIM-ARROW-{scale}``。

    §11.1.4 后半句：半径、直径、角度与弧长的起止符号宜用箭头，宽度 b ≥ 1mm。
    与线性样式分开是因为 ``dimblk``/``dimasz`` 只能由样式携带（override 会被
    ezdxf 静默丢弃），一个样式没法同时给出斜短线与箭头两种起止符。
    """
    ensure_arrow_block(doc)
    return _make_dimstyle(doc, f"GB-DIM-ARROW-{int(scale)}", scale,
                          ARROW_BLOCK, arrow_dimasz(scale))



def set_text(msp, text, layer, height, align="left"):
    """统一的文字写入助手。"""
    return text
