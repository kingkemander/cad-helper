"""DXF → PNG 预览：让用户"看得见图"。

为什么必须有这个模块
--------------------
envcad 的交付物是 DXF。但 DXF 在对话窗口、聊天工具、邮件里**都打不开**——
用户拿到一堆文件路径，却不知道自己到底画出来了什么。实测在 SpaceAgents /
终端场景里，用户的第一反应永远是"图呢？"。

所以出图链路必须是 `生成 → 体检 → **预览** → 交付`：
生成完立刻渲染成 PNG 贴给用户看，用户点头了再谈交付。

用法::

    from envcad.preview import render_dxf, render_dir, contact_sheet

    render_dxf("out/T2.dxf", "out/预览/T2.png")          # 单张
    render_dir("out", "out/预览")                        # 整个目录
    contact_sheet(["a.png", "b.png"], "预览-全部.png")   # 拼一张缩略图

命令行::

    envcad preview <目录或dxf> [--out DIR] [--dpi 150] [--contact]

要点
----
* **必须** ``ColorPolicy.BLACK``：默认配色按 ACI 上色，白底黑线才是施工图。
* 画幅取**图框**长宽比并按图框裁切，否则 matplotlib 会把整张纸留大片白边。
* 中文字体：TEXT 走 DXF 里的 HZ 样式，渲染端由 matplotlib 提供字形。
  缺 CJK 字体时汉字会变成方框，故 :func:`check_cjk_font` 会提前告警。
"""
from __future__ import annotations

import glob
import os
from typing import List, Optional, Sequence, Tuple

__all__ = [
    "render_dxf", "render_dir", "contact_sheet", "check_cjk_font",
    "apply_cjk_font",
    "frame_extent", "PreviewError",
]


class PreviewError(RuntimeError):
    """预览失败（缺依赖 / 文件读不了 / 无内容）。"""


# ── 依赖 ──────────────────────────────────────────────────────────
def _imports():
    try:
        import ezdxf  # noqa: F401
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from ezdxf.addons.drawing import RenderContext, Frontend
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
        from ezdxf.addons.drawing.config import Configuration, ColorPolicy
    except ImportError as e:                          # pragma: no cover
        raise PreviewError(
            f"预览需要 matplotlib 与 ezdxf：pip install matplotlib ezdxf（{e}）"
        ) from e
    return plt, RenderContext, Frontend, MatplotlibBackend, Configuration, ColorPolicy


_CJK_CANDIDATES = (
    "PingFang SC", "Hiragino Sans GB", "Heiti SC", "Songti SC", "STHeiti",
    "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Source Han Sans SC",
    "WenQuanYi Zen Hei", "AR PL UMing CN", "Droid Sans Fallback",
)


def check_cjk_font() -> Optional[str]:
    """返回可用的中文字体名；没有则返回 None（汉字会渲染成方框）。"""
    try:
        from matplotlib import font_manager
    except ImportError:                               # pragma: no cover
        return None
    have = {f.name for f in font_manager.fontManager.ttflist}
    for name in _CJK_CANDIDATES:
        if name in have:
            return name
    return None


def apply_cjk_font() -> Optional[str]:
    """把中文字体设进 matplotlib 全局 rcParams，返回所用字体名。

    ezdxf 的绘图后端会按 DXF 文字样式自己解析字体，图纸本体通常没问题；但凡由
    **我们**用 matplotlib 画的文字（总览图的图名、标题）走默认 sans-serif，
    在 macOS 上就是 DejaVu Sans —— 汉字全部渲染成豆腐块。实测总览图 8 个图名
    全是方框。所以这里显式设一次全局字体。
    """
    try:
        import matplotlib
    except ImportError:                               # pragma: no cover
        return None
    name = check_cjk_font()
    if name:
        cur = list(matplotlib.rcParams.get("font.sans-serif", []))
        matplotlib.rcParams["font.sans-serif"] = [name] + [c for c in cur if c != name]
        matplotlib.rcParams["font.family"] = "sans-serif"
    matplotlib.rcParams["axes.unicode_minus"] = False
    return name


# ── 取景 ──────────────────────────────────────────────────────────
def frame_extent(msp) -> Optional[Tuple[float, float, float, float]]:
    """图框外框（图框层最大闭合多段线）；没有则退回全部内容包络。"""
    from .standards.frame import SHEET_LAYERS
    best, area = None, -1.0
    for e in msp:
        if e.dxf.layer != "图框" or e.dxftype() != "LWPOLYLINE" or not e.closed:
            continue
        p = list(e.get_points("xy"))
        xs, ys = [q[0] for q in p], [q[1] for q in p]
        a = (max(xs) - min(xs)) * (max(ys) - min(ys))
        if a > area:
            area, best = a, (min(xs), min(ys), max(xs), max(ys))
    if best:
        return best
    try:
        from ezdxf import bbox as B
        ct = [e for e in msp if e.dxf.layer not in set(SHEET_LAYERS)]
        ext = B.extents(ct or list(msp))
        return (ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y)
    except Exception:
        return None


# ── 渲染 ──────────────────────────────────────────────────────────
def render_dxf(path: str, out_png: Optional[str] = None, *,
               dpi: int = 150, width_in: float = 16.0,
               crop: bool = True, pad_mm: float = 3.0) -> str:
    """把单个 DXF 渲染成 PNG，返回 PNG 路径。

    ``crop=True`` 时按图框裁切（等比、无留白）；``pad_mm`` 是裁切外扩。
    """
    plt, RenderContext, Frontend, MatplotlibBackend, Configuration, ColorPolicy = _imports()
    apply_cjk_font()
    import ezdxf

    if not os.path.exists(path):
        raise PreviewError(f"文件不存在：{path}")
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()

    if out_png is None:
        out_png = os.path.splitext(path)[0] + ".png"
    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)

    bb = frame_extent(msp) if crop else None
    if bb:
        x0, y0, x1, y1 = bb
        w, h = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    else:
        w, h = 4.0, 3.0

    fig = plt.figure(figsize=(width_in, max(1.0, width_in * h / w)))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    try:
        Frontend(RenderContext(doc), MatplotlibBackend(ax),
                 config=Configuration(color_policy=ColorPolicy.BLACK)
                 ).draw_layout(msp, finalize=True)
    except Exception as e:
        plt.close(fig)
        raise PreviewError(f"渲染失败 {os.path.basename(path)}：{e}") from e

    if bb:
        # finalize 会自己 autoscale，这里按图框收一次框，消掉整张纸的留白
        ax.set_xlim(x0 - pad_mm, x1 + pad_mm)
        ax.set_ylim(y0 - pad_mm, y1 + pad_mm)
        ax.set_aspect("equal", adjustable="box")

    fig.savefig(out_png, dpi=dpi, facecolor="white")
    plt.close(fig)
    return out_png


def render_dir(root: str, out_dir: Optional[str] = None, *,
               dpi: int = 150, pattern: str = "*.dxf",
               contact: bool = False) -> List[str]:
    """目录下所有 DXF → PNG。``contact=True`` 另出一张拼图总览。"""
    files = sorted(glob.glob(os.path.join(root, pattern)))
    if not files:
        raise PreviewError(f"目录下没有 DXF：{root}")
    if out_dir is None:
        out_dir = os.path.join(root, "预览")
    pngs = []
    for f in files:
        name = os.path.splitext(os.path.basename(f))[0]
        pngs.append(render_dxf(f, os.path.join(out_dir, name + ".png"), dpi=dpi))
    if contact and len(pngs) > 1:
        contact_sheet(pngs, os.path.join(out_dir, "预览-全部.png"))
    return pngs


def contact_sheet(pngs: Sequence[str], out_png: str, *,
                  cols: Optional[int] = None, dpi: int = 110,
                  cell_in: float = 6.0) -> str:
    """把多张预览拼成一张总览图（一套 8 张图一眼看全）。"""
    plt, *_ = _imports()
    apply_cjk_font()          # 图名是中文，不设字体就是豆腐块
    import matplotlib.image as mpimg

    n = len(pngs)
    if cols is None:
        cols = min(4, n) if n > 1 else 1
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(cols * cell_in, rows * cell_in * 0.72))
    axes = [axes] if rows * cols == 1 else list(getattr(axes, "ravel")())
    for ax in axes:
        ax.set_axis_off()
    for ax, p in zip(axes, pngs):
        try:
            ax.imshow(mpimg.imread(p))
            ax.set_title(os.path.splitext(os.path.basename(p))[0],
                         fontsize=8, pad=2)
        except Exception:
            pass
    fig.tight_layout()
    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    fig.savefig(out_png, dpi=dpi, facecolor="white")
    plt.close(fig)
    return out_png
