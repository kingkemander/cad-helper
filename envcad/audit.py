"""出图体检：把"能不能交出去"变成一条命令。

为什么必须有这个模块
--------------------
原 ``tests/verify.py`` 只查"必需标注在不在、图层对不对、实体数够不够"——
它查不到真正会让图纸报废的问题。实测这几类硬伤它**一个都报不出来**：

* 幅面虚涨：内容只有 A3 的量，图框却是 A0（打印出来线细如发丝）；
* 比例尺不自洽：标题栏写 1:100，实测图框却是 1:71；
* 内容压图框/压标题栏；
* 文字叠印（图纸上两行字粘在一起，客户一看就退）；
* 假尺寸标注：用手工线 + 文字冒充尺寸，客户在 CAD 里改不动、关联不了；
* 附表块压主体（技术要求框压住设备轮廓）。

所以把审计做成正式模块，生成后跑一次，把结论用**人话**报给用户与 Agent。

用法::

    from envcad.audit import audit_dir, format_report

    reports = audit_dir("out")
    print(format_report(reports))
    if any(not r.ok for r in reports):
        ...

命令行::

    envcad check <目录或dxf> [--strict] [--json]

``--strict``：把"拥挤但未叠印"（间距 < 0.5 字高）与"图面利用率过低"也算问题。
"""
from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

__all__ = ["SheetReport", "audit_file", "audit_dir", "format_report", "main"]

PAPER = {"A0": (1189.0, 841.0), "A1": (841.0, 594.0), "A2": (594.0, 420.0),
         "A3": (420.0, 297.0), "A4": (297.0, 210.0)}
MARGIN = {"A0": (25, 10), "A1": (25, 10), "A2": (25, 10),
          "A3": (25, 5), "A4": (25, 5)}
NUM_RE = re.compile(r"^(\d+)\.\s")


# ── 报告结构 ──────────────────────────────────────────────────────
@dataclass
class SheetReport:
    name: str
    path: str
    size: Optional[str] = None            # A0~A4
    orientation: Optional[str] = None     # 横 / 纵
    declared: Optional[int] = None        # 标题栏写的 1:N
    measured: Optional[float] = None      # 实测图框/纸面 = 比例尺
    overflow_mm: float = 0.0              # 内容越出内框（纸面 mm）
    fill_pct: float = 0.0                 # 内容包络面积 / 内框面积（%）
    title_clash: bool = False             # 内容压标题栏
    dim_count: int = 0                    # 真实 DIMENSION 实体数
    overlaps: List[Tuple[str, str, str]] = field(default_factory=list)
    crowded: List[Tuple[str, str, str]] = field(default_factory=list)
    note: str = ""
    errors: List[str] = field(default_factory=list)

    @property
    def scale_ok(self) -> bool:
        return (self.declared is not None and self.measured is not None
                and abs(self.measured - self.declared) / self.declared < 0.01)

    @property
    def sheet_ok(self) -> bool:
        return self.size is not None

    @property
    def ok(self) -> bool:
        return (self.sheet_ok and self.scale_ok and self.overflow_mm <= 0
                and not self.title_clash and not self.overlaps
                and not self.errors)


# ── 取图元 ────────────────────────────────────────────────────────
def _outer_rect(msp):
    best, area = None, -1.0
    for e in msp:
        if e.dxf.layer != "图框" or e.dxftype() != "LWPOLYLINE" or not e.closed:
            continue
        p = list(e.get_points("xy"))
        xs, ys = [q[0] for q in p], [q[1] for q in p]
        a = (max(xs) - min(xs)) * (max(ys) - min(ys))
        if a > area:
            area, best = a, (min(xs), min(ys), max(xs), max(ys))
    return best


def _declared_scale(msp) -> Optional[int]:
    for e in msp:
        if e.dxf.layer != "标题栏":
            continue
        t = e.dxf.text if e.dxftype() == "TEXT" else (
            e.text if e.dxftype() == "MTEXT" else "")
        m = re.search(r"1\s*:\s*(\d+)", str(t))
        if m:
            return int(m.group(1))
    return None


def _text_of(e) -> Optional[str]:
    if e.dxftype() == "TEXT":
        return str(e.dxf.text)
    if e.dxftype() == "MTEXT":
        return str(e.text)
    return None


def _text_box(e, frame_mod):
    rot = float(getattr(e.dxf, "rotation", 0.0) or 0.0) % 180.0
    b = frame_mod._text_extent(e)
    if b is None:
        return None
    if 45.0 < rot < 135.0:                     # 竖排/斜排文字：宽高互换
        cx, cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
        w, h = (b[2] - b[0]), (b[3] - b[1])
        return (cx - h / 2, cy - w / 2, cx + h / 2, cy + w / 2)
    return b


def _scan_text_conflicts(msp, frame_mod, sheet_layers) -> Tuple[list, list]:
    """返回 (叠印列表, 拥挤列表)；每项 (文件内序号说明, 文本1, 文本2)。"""
    items = []
    for e in msp:
        t = _text_of(e)
        if t is None or not t.strip():
            continue
        if e.dxf.layer in sheet_layers:
            continue
        b = _text_box(e, frame_mod)
        if b:
            items.append((t, float(getattr(e.dxf, "height", 2.5) or 2.5), b))

    overlaps, crowded = [], []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            t1, h1, a = items[i]
            t2, h2, b = items[j]
            w = min(a[2], b[2]) - max(a[0], b[0])
            h = min(a[3], b[3]) - max(a[1], b[1])
            if w > 0 and h > 0:
                overlaps.append((t1[:26], t2[:26], f"重叠 {w:.0f}×{h:.0f}mm"))
            else:
                gx = max(a[0] - b[2], b[0] - a[2])
                gy = max(a[1] - b[3], b[1] - a[3])
                if max(gx, gy) < min(h1, h2) * 0.5:
                    crowded.append((t1[:26], t2[:26], f"间距 {max(gx, gy):.0f}mm"))
    return overlaps, crowded


def _check_note_blocks(msp, aux_layer) -> str:
    """编号条目（'1. xxx'）必须等距、顺序正确、落在同一个附表框内。"""
    items = []
    for e in msp:
        if e.dxftype() != "TEXT":
            continue
        m = NUM_RE.match(e.dxf.text)
        if m:
            items.append((int(m.group(1)), e.dxf.insert.x, e.dxf.insert.y,
                          e.dxf.text, float(e.dxf.height)))
    if not items:
        return "无编号条目"

    boxes = []
    for e in msp:
        if e.dxf.layer == aux_layer and e.dxftype() == "LWPOLYLINE" and e.closed:
            p = list(e.get_points("xy"))
            xs, ys = [q[0] for q in p], [q[1] for q in p]
            boxes.append((min(xs), min(ys), max(xs), max(ys)))

    items.sort(key=lambda r: (round(r[1] / 5.0), -r[2]))
    groups, cur = [], [items[0]]
    for it in items[1:]:
        if abs(it[1] - cur[-1][1]) <= 5.0:
            cur.append(it)
        else:
            groups.append(cur)
            cur = [it]
    groups.append(cur)

    problems, nblk = [], 0
    for g in groups:
        # 同一列可能上下叠放多个条目块：按"编号从 1 重新开始"切开
        g = sorted(g, key=lambda r: (round(r[1] / 5.0), -r[2]))
        subs, cf = [], []
        for it in g:
            if cf and it[0] <= cf[-1][0]:
                subs.append(cf)
                cf = []
            cf.append(it)
        if cf:
            subs.append(cf)

        for sub in subs:
            if len(sub) < 2:
                continue
            nblk += 1
            sub.sort(key=lambda r: -r[2])
            gaps = [round(sub[i][2] - sub[i + 1][2], 3) for i in range(len(sub) - 1)]
            if max(gaps) - min(gaps) > 1.0:
                problems.append(f"行距不均 {gaps}")
            seq = [r[0] for r in sub]
            if seq != sorted(seq):
                problems.append(f"编号错序 {seq}")
            hmax = max(r[4] for r in sub)
            if min(gaps) < hmax * 1.05:
                problems.append(f"行距{min(gaps):.0f} < 字高{hmax:.0f}")
            if boxes and not any(
                all(b[0] - 1 <= r[1] and r[2] <= b[3] + 1 for r in sub)
                for b in boxes
            ):
                problems.append("条目未落在附表框内")
    return "; ".join(problems) if problems else f"{nblk} 块等距内嵌"


# ── 单张体检 ──────────────────────────────────────────────────────
def audit_file(path: str) -> SheetReport:
    import ezdxf
    from .standards import frame as F

    rep = SheetReport(name=os.path.basename(path), path=path)
    try:
        msp = ezdxf.readfile(path).modelspace()
    except Exception as e:
        rep.errors.append(f"打不开：{e}")
        return rep

    rep.declared = _declared_scale(msp)
    outer = _outer_rect(msp)
    if not outer:
        rep.errors.append("没有图框（图框层无闭合多段线）")
        return rep

    w, h = outer[2] - outer[0], outer[3] - outer[1]
    for pn, (pw, ph) in PAPER.items():
        for orient, (aw, ah) in (("横", (pw, ph)), ("纵", (ph, pw))):
            k = w / aw
            if (abs(k - h / ah) / max(k, h / ah) < 0.004
                    and rep.declared and abs(k - rep.declared) / rep.declared < 0.01):
                rep.size, rep.orientation, rep.measured = pn, orient, k
                break
        if rep.size:
            break

    rep.dim_count = sum(1 for e in msp if e.dxftype() == "DIMENSION")
    try:
        rep.note = _check_note_blocks(msp, F.AUX_LAYER)
    except Exception as e:
        rep.note = f"条目检查异常：{e}"

    sheet_layers = set(F.SHEET_LAYERS)
    rep.overlaps, rep.crowded = _scan_text_conflicts(msp, F, sheet_layers)

    if rep.size:
        a, c = MARGIN.get(rep.size, (25, 10))
        k = rep.measured
        ix0, iy0 = outer[0] + a * k, outer[1] + c * k
        ix1, iy1 = outer[2] - c * k, outer[3] - c * k
        ct = [e for e in msp if e.dxf.layer not in sheet_layers]
        if ct:
            from ezdxf import bbox as B
            cb = B.extents(ct)
            rep.overflow_mm = max(ix0 - cb.extmin.x, iy0 - cb.extmin.y,
                                  cb.extmax.x - ix1, cb.extmax.y - iy1) / k
            rep.overflow_mm = float(max(0.0, rep.overflow_mm))
            # 图面利用率：幅面"合法"不等于"用得住"。实测有的图占了标准 A4、
            # 比例尺也对，但内容只铺满纸宽的 29%（表格）甚至纸高的 11%（流程图）
            # ——打印出来就是一页大白纸角落里有张小图。这是警告，不是硬伤：
            # 标准幅面本就不一定贴合内容长宽比，所以不参与 ok 判定。
            aw, ah = cb.extmax.x - cb.extmin.x, cb.extmax.y - cb.extmin.y
            iw, ih = ix1 - ix0, iy1 - iy0
            if iw > 0 and ih > 0:
                rep.fill_pct = float(round(100.0 * aw * ah / (iw * ih), 1))
        tb = [e for e in msp if e.dxf.layer == "标题栏"]
        if tb and ct:
            from ezdxf import bbox as B
            cb, tbb = B.extents(ct), B.extents(tb)
            rep.title_clash = not (
                cb.extmax.x <= tbb.extmin.x or cb.extmin.x >= tbb.extmax.x
                or cb.extmax.y <= tbb.extmin.y or cb.extmin.y >= tbb.extmax.y)
    else:
        rep.errors.append(
            f"图幅/比例尺不自洽（标题栏 1:{rep.declared or '?'}，"
            f"实测图框 {w:.0f}×{h:.0f}）")
    return rep


def audit_dir(root: str, pattern: str = "*.dxf") -> List[SheetReport]:
    files = (sorted(glob.glob(os.path.join(root, pattern)))
             if os.path.isdir(root) else [root])
    return [audit_file(f) for f in files]


# ── 人话报告 ──────────────────────────────────────────────────────
def format_report(reports: Sequence[SheetReport], *, strict: bool = False,
                  verbose: bool = False) -> str:
    lines = []
    bad = 0
    for r in reports:
        flags = []
        if not r.sheet_ok:
            flags.append("幅面不符标准")
        if not r.scale_ok:
            flags.append("比例尺不自洽")
        if r.overflow_mm > 0:
            flags.append(f"压框 {r.overflow_mm:.0f}mm")
        if r.title_clash:
            flags.append("压标题栏")
        if r.overlaps:
            flags.append(f"文字叠印 {len(r.overlaps)} 处")
        if strict and r.crowded:
            flags.append(f"文字拥挤 {len(r.crowded)} 处")
        if strict and 0 < r.fill_pct < 12.0:
            flags.append(f"图面只用 {r.fill_pct:.0f}%")
        if r.errors:
            flags.extend(r.errors)
        if flags:
            bad += 1

        size = f"{r.size}({r.orientation})" if r.size else "—"
        scale = f"1:{int(round(r.measured))}" if r.measured else "—"
        mark = "✗" if flags else "✓"
        lines.append(
            f"  {mark} {r.name:<38} {size:<8} 声明1:{r.declared or '?':<5} "
            f"实测{scale:<7} 占幅{r.fill_pct:>5.1f}% 真实尺寸{r.dim_count:<3} {r.note}")
        if flags:
            lines.append(f"      ⚠ {'；'.join(flags)}")
        if verbose:
            for t1, t2, extra in r.overlaps:
                lines.append(f"        叠印 {t1!r} × {t2!r}  {extra}")
            for t1, t2, extra in r.crowded:
                lines.append(f"        拥挤 {t1!r} × {t2!r}  {extra}")

    n = len(reports)
    verdict = ("全部通过，可交付人工审核" if bad == 0
               else f"{bad} / {n} 张有问题，先修再交付")
    lines.append("")
    lines.append(f"  共 {n} 张 → {verdict}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="envcad check", description="出图体检：幅面/比例尺/压框/叠印/条目")
    ap.add_argument("path", help="目录或单个 dxf")
    ap.add_argument("--strict", action="store_true",
                    help="把'拥挤但未叠印'也算问题")
    ap.add_argument("--verbose", "-v", action="store_true", help="逐条列出问题")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    reports = audit_dir(args.path)
    if not reports:
        print(f"没有找到 dxf：{args.path}")
        return 2
    if args.json:
        print(json.dumps([{
            "name": r.name, "size": r.size, "orientation": r.orientation,
            "declared": r.declared, "measured": r.measured,
            "overflow_mm": round(r.overflow_mm, 1),
            "fill_pct": r.fill_pct,
            "title_clash": r.title_clash, "dim": r.dim_count,
            "overlaps": r.overlaps, "note": r.note,
            "errors": r.errors, "ok": r.ok,
        } for r in reports], ensure_ascii=False, indent=2))
    else:
        print(format_report(reports, strict=args.strict, verbose=args.verbose))
    return 0 if all(r.ok for r in reports) else 1


# —————————————— 交付收尾：体检 + 预览 + 清单 ——————————————

def _as_path_list(dxfs) -> List[str]:
    """把 dxfs 归一成路径列表。

    各生成器返回值不统一（``gen_t1`` 返回单个 str，``gen_t4`` 返回 list），
    调用方也很容易直接把单个路径传进来。若直接 ``len(dxfs)``，传 str 会数出
    **字符数**（实测把 1 张图报成 154 张），传 Path 会数出路径段数。
    """
    if dxfs is None:
        return []
    if isinstance(dxfs, (str, bytes, os.PathLike)):
        return [os.fspath(dxfs)]
    return [os.fspath(p) for p in dxfs]


def deliver(out_dir: str, dxfs=None,
            *, preview: bool = True, dpi: int = 150) -> str:
    """出图后的交付收尾，返回可直接打印/粘贴的交付说明。

    只回 dxf 路径对用户等于没交付——用户手里没有 CAD 时看不了 dxf。这里把
    "文件生成成功"接到"图纸能交付"：先跑体检（有问题照实写出来但不抛异常，
    最终由人工审核拍板），再落全套 PNG + 总览图，最后给出交付清单。
    刻意不 raise：体检/预览失败只应降级为提示，不能把已生成的图纸吞掉。
    """
    lines: List[str] = []
    reports = []
    try:
        reports = audit_dir(out_dir)
    except Exception as exc:                        # 体检不能阻断交付
        lines.append(f"[提示] 体检未能完成：{exc}")
    else:
        if reports:
            lines.append(format_report(reports))

    pngs: List[str] = []
    if preview:
        try:
            from .preview import render_dir, check_cjk_font, PreviewError
        except Exception as exc:
            lines.append(f"[提示] 预览模块不可用：{exc}")
        else:
            if not check_cjk_font():
                lines.append("[提示] 未找到中文字体，预览里的汉字可能显示为方框。")
            try:
                pngs = render_dir(out_dir, os.path.join(out_dir, "预览"),
                                  dpi=dpi, contact=True)
            except PreviewError as exc:
                lines.append(f"[提示] 预览未能生成（{exc}）；"
                             "装 matplotlib 后可自动出图：pip install matplotlib")
            except Exception as exc:
                lines.append(f"[提示] 预览未能生成：{exc}")

    n_dxf = len(_as_path_list(dxfs)) if dxfs is not None else len(reports)
    lines.append("")
    lines.append("— 交付清单（可直接发给用户）" + "—" * 20)
    lines.append(f"图纸目录：{os.path.abspath(out_dir)}")
    if pngs:
        shots = [p for p in pngs if "预览-全部" not in p]
        lines.append(f"DXF {n_dxf} 张，预览 PNG {len(pngs)} 张")
        lines.append(f"单张预览：{os.path.abspath(shots[0])}（共 {len(shots)} 张）")
        allpng = os.path.join(out_dir, "预览", "预览-全部.png")
        if os.path.exists(allpng):
            lines.append(f"总览图：{os.path.abspath(allpng)}")
    else:
        lines.append(f"DXF {n_dxf} 张（未出预览）")
    lines.append("提示：dxf 需 CAD 打开，务必连同预览 PNG 一起交付。")
    return "\n".join(lines)


if __name__ == "__main__":                              # pragma: no cover
    raise SystemExit(main())
