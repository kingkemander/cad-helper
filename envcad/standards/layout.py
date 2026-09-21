"""出图版面编排 —— 让附表块（技术要求/图例/表格）贴着内容走。

## 为什么需要这个模块（实测根因）

各生成器把图例、技术要求锚在 **refit 之前** 的图框角点上，例如
``legend_x = x1 - 55 * s``、``note_y = y1 - 28 * s``。这些 x1/y1 来自
``draw_frame`` 返回的初始图框（进程默认 A2）。

``save_dxf_autofit`` → ``refit_frame`` 会按内容重算最小标准幅面。此时
"锚在初始 A2 图框右上角的图例"和"锚在左上角的技术要求"一起，把内容
外包络撑到接近整整一张 A2 的宽度 —— 于是最小可装幅面被**抬到 A1**。

实测（T4-01 总平面，1:100，scale=100）：

| 版本 | 内容外包络 | 选中幅面 | 纸面填充 |
|---|---|---|---|
| 锚在初始图框角点（现状） | 581 × 332 图纸 mm | A1 (841×594) | 69% × 56% |
| 附表列贴着内容块右侧 | 295 × 235 图纸 mm | A2 (594×420) | 50% × 56% |

其中主体图形只占 180 × 195 mm。12 张成图中 9 张被抬到 A2 以上，内容
普遍只填 43%~70% —— 对 50 m³/d 的小型站成套图，A1 是不合理的幅面。

## 修法

附表块改为贴着**内容外包络**（而不是图框）堆叠：内容块右侧竖排一列，
``AuxColumn`` 逐块向下累加，块与块之间留 ``ITEM_GAP``，绝不重叠。

``draw_tech_notes`` / ``draw_legend`` 的约定正好合用：``origin`` 为左上角，
且都返回自己的占位 ``(x0, y0, x1, y1)``。``add()`` 直接吃这个返回值来
推算下一块的 y，因此不需要调用方手算块高。

## 约定

* 全部尺寸为实物 mm；``scale`` 为比例尺分母（1:100 → 100.0）。
* ``content_bbox`` 是**主体图形**的外包络，不含附表块自身。
* 本模块不移动内容、不改图框；幅面选择仍由 ``frame.refit_frame`` 负责。
"""
from __future__ import annotations

from typing import Callable, Optional, Sequence, Tuple

# 附表列与主体内容块的间距（实物 mm）
DEFAULT_GAP = 2500.0
# 附表列内相邻块的间距（实物 mm）
ITEM_GAP = 1500.0

BBox = Tuple[float, float, float, float]


def union_bbox(rects: Sequence[BBox]) -> Optional[BBox]:
    """多个矩形 (x0,y0,x1,y1) 的外包络；空输入返回 None。"""
    xs0 = [r[0] for r in rects if r is not None]
    ys0 = [r[1] for r in rects if r is not None]
    xs1 = [r[2] for r in rects if r is not None]
    ys1 = [r[3] for r in rects if r is not None]
    if not xs0:
        return None
    return (min(xs0), min(ys0), max(xs1), max(ys1))


def rect_of(x: float, y: float, w: float, h: float) -> BBox:
    """由左下角 + 宽高得到 (x0,y0,x1,y1)。"""
    return (x, y, x + w, y + h)


def _norm_bbox(bb, fallback_xy: Tuple[float, float]) -> BBox:
    """把附表函数的返回值规整成 (x0,y0,x1,y1)。

    附表函数按约定返回外框，但第三方/历史函数可能返回 None —— 此时退化为
    锚点处一个零尺寸矩形，至少不会让调用方崩掉。
    """
    if bb is None:
        x, y = fallback_xy
        return (x, y, x, y)
    try:
        x0, y0, x1, y1 = (float(v) for v in bb)
    except (TypeError, ValueError):
        x, y = fallback_xy
        return (x, y, x, y)
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


class AuxColumn:
    """沿主体内容块右侧向下堆叠附表块。

    用法::

        col = AuxColumn(msp, site_bbox, s, gap=2500)
        col.add(draw_tech_notes, "总平面技术要求", notes, width=90)
        col.add(draw_legend, legend_items, col_widths=(16, 28, 32))
        aux_bbox = col.bbox          # 整列占位，交给 tracker/幅面计算

    说明：``add()`` 的第一个位置参数是附表绘制函数，其签名约定为
    ``fn(msp, origin, scale, *args, **kwargs)`` 且 ``origin`` 为左上角。
    """

    def __init__(self, msp, content_bbox: BBox, scale: float, *,
                 gap: float = DEFAULT_GAP, item_gap: float = ITEM_GAP,
                 x: Optional[float] = None, top: Optional[float] = None,
                 tracker=None):
        self.msp = msp
        self.scale = scale
        self.gap = gap
        self.item_gap = item_gap
        if content_bbox is None:
            # 主体还没有图形（空图/仅附表）：退化为以原点为内容块
            content_bbox = (0.0, 0.0, 0.0, 0.0)
        cx0, cy0, cx1, cy1 = content_bbox
        # 默认：贴在内容块右侧、与内容块顶对齐
        self.x = (cx1 + gap) if x is None else x
        self.top = cy1 if top is None else top
        self.tracker = tracker
        self.content_bbox = content_bbox
        self.blocks = []
        self._y: Optional[float] = None
        self._bbox: Optional[BBox] = None

    # ── 排列 ──────────────────────────────────────────────
    def add(self, fn: Callable, *args, **kwargs) -> BBox:
        """放一块附表，返回该块占位；自动与上一块保持 item_gap。"""
        y = self.top if self._y is None else self._y - self.item_gap
        bb = _norm_bbox(fn(self.msp, (self.x, y), self.scale, *args, **kwargs),
                        (self.x, y))
        self.blocks.append(bb)
        self._y = bb[1]                      # 下一块从这个块底继续往下
        self._bbox = bb if self._bbox is None else union_bbox([self._bbox, bb])
        return bb

    # ── 查询 ──────────────────────────────────────────────
    @property
    def bbox(self) -> Optional[BBox]:
        """整列占位（含全部已放块）。"""
        return self._bbox

    @property
    def bottom(self) -> Optional[float]:
        """已放块的最低 y。"""
        return self._y

    def content_and_aux_bbox(self) -> Optional[BBox]:
        """主体内容 + 附表列的合并外包络（可直接喂给幅面选择）。"""
        if self._bbox is None:
            return self.content_bbox
        return union_bbox([self.content_bbox, self._bbox])
