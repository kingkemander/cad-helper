"""国标审图引擎：读 DXF → 逐条比对 GB/T 50001-2017 → 出规范符合性报告。

为什么要有这个模块
------------------
``envcad check``（``audit.py``）管的是"**图纸能不能交出去**"——幅面虚涨、
比例尺不自洽、压框、文字叠印、假尺寸。它**不回答"符不符合国标"**：
一张体检全过的图，线宽可以全错、尺寸起止符号可以是箭头、汉字可以是 2mm。

本模块管的是"**图纸能不能算生产可用**"：把 GB/T 50001-2017 里**可机检**的
条款逐条落到 DXF 实体上，给出条款号 + 违规实体 + 依据，让审核有据可依。

规则从哪来
----------
条文核对源：``gf.cabr-fire.com``（中国建筑科学研究院建筑防火研究所，
GB/T 50001-2017 主编单位的公开规范库）。**只把"判据 + 条款号 + 溯源链接"
写进 ``standards_kb.json``，不整篇复制规范正文**（正文版权属发布机构）。

已核对并可机检的条款（每条都带 ``clause`` 与 ``source``）：

* §3.1.1 幅面及图框尺寸应符合标准幅面；
* §4.0.1 线宽应取自标准线宽系列，图线宽度不应小于 0.1mm；
* §4.0.3 同一张图纸内相同比例的各图样应选用相同的线宽组；
* §4.0.4 图框线宽 = b；标题栏外框线 A2/A3/A4 取 0.7b、A0/A1 取 0.5b；
* §4.0.10 图线不得与文字、数字或符号重叠；
* §5.0.2 / §5.0.3 文字字高应从标准字高系列选用（汉字不宜小于 3.5mm）；
* §5.0.7 字母及数字的字高不应小于 2.5mm；
* §8.0.1 定位轴线应用 0.25b 单点长画线绘制；
* §11.1.4 尺寸起止符号用中粗斜短线（线性尺寸应为 45° 斜短线，非箭头）；
* §13.0.1 图层名称汉字与英文字母不得混用。

**明确标为不可机检的条款**（写进 ``complianceChecks`` 且
``checkable: false``，附原因）——宁可不报，也不报错杀：
§8.0.4 轴线编号不得用 I/O/Z（要先认出"这是轴线编号圆"）、
§11.3.3 平行尺寸线间距 7~10mm（要先聚类平行尺寸链）、
§13.0.2 图层名宜选用附录B（"宜"为推荐，实务中线宽层命名普遍存在）。

术语约定
--------
* ``model mm``：DXF 里的坐标单位（本仓库按 1:1 实物 mm 绘制）。
* ``paper mm``：打印成品尺寸 = ``model mm / scale``。规范里的 mm 全部指纸面。
* ``b``：图纸基本线宽（粗实线）。由图上实际使用的最粗线宽反推。

用法::

    from envcad.gb_audit import audit_dir, format_report

    reports = audit_dir("out")
    print(format_report(reports))

命令行::

    envcad audit <目录或dxf> [--json] [--strict] [--verbose]
"""
from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

__all__ = [
    "Finding", "GbReport", "audit_dxf", "audit_dir", "format_report",
    "load_kb", "RULES", "main",
]

# ── 常量（知识库缺失时的回退，同时也是自检基准）──────────────────
PAPER: Dict[str, Tuple[float, float]] = {
    "A0": (1189.0, 841.0), "A1": (841.0, 594.0), "A2": (594.0, 420.0),
    "A3": (420.0, 297.0), "A4": (297.0, 210.0),
}
PAPER_ORDER_ASC = ("A4", "A3", "A2", "A1", "A0")

#: GB/T 50001-2017 表 4.0.1 线宽组（mm）：b → (b, 0.7b, 0.5b, 0.25b)
#: 2017 版 b 只保留 1.4/1.0/0.7/0.5；细线 0.25b 可到 0.13。
#: 注意：旧知识库把 b 系列写成 ``{2.0, 1.4, 1.0, 0.7, 0.5, 0.35}`` 是**错的**
#: （b 无 2.0；0.35 是 0.7b/0.5b 不是 b），故此处以内置表为准并回写知识库。
LINE_WIDTH_GROUPS: Dict[float, Tuple[float, float, float, float]] = {
    1.4: (1.4, 1.0, 0.7, 0.35),
    1.0: (1.0, 0.7, 0.5, 0.25),
    0.7: (0.7, 0.5, 0.35, 0.18),
    0.5: (0.5, 0.35, 0.25, 0.13),
}
B_SERIES = (1.4, 1.0, 0.7, 0.5)
MIN_LINE_WIDTH_MM = 0.1          # §4.0.1 条文说明：图线宽度不应小于 0.1mm
MIN_TEXT_CJK_MM = 3.5            # §5.0.2 表 5.0.2 汉字最小字高
MIN_TEXT_NUM_MM = 2.5            # §5.0.7 字母及数字字高下限
DIM_TICK_MIN_MM, DIM_TICK_MAX_MM = 2.0, 3.0   # §11.1.4 起止符号长 2~3mm
#: §11.1.4 后半句："半径、直径、角度与弧长的尺寸起止符号，宜用箭头表示，
#: 箭头宽度 b 不宜小于 1mm。" —— 这里取规范下限作达标线。
ARROW_WIDTH_MIN_MM = 1.0
NORTH_DIA_MM = 24.0              # §7.4.3 指北针圆直径宜 24mm

SHEET_LAYERS = ("图框", "标题栏")
SYSTEM_LAYERS = {"0", "Defpoints"}

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
LATIN_RE = re.compile(r"[A-Za-z]")

# ── 知识库 ────────────────────────────────────────────────────────
_KB_PATH = os.path.join(os.path.dirname(__file__), "standards_kb.json")
_KB_CACHE: Optional[dict] = None


def load_kb(path: Optional[str] = None) -> dict:
    """读标准知识库（带缓存）。读不到就返回空 dict，规则回退内置常量。"""
    global _KB_CACHE
    if path is None and _KB_CACHE is not None:
        return _KB_CACHE
    try:
        with open(path or _KB_PATH, "r", encoding="utf-8") as fh:
            kb = json.load(fh)
    except Exception:
        kb = {}
    if path is None:
        _KB_CACHE = kb
    return kb


def _kb_checks() -> Dict[str, dict]:
    """``complianceChecks`` 按 id 建索引。"""
    return {c.get("id"): c for c in load_kb().get("complianceChecks", [])
            if isinstance(c, dict) and c.get("id")}


# ── 报告结构 ──────────────────────────────────────────────────────
@dataclass
class Finding:
    """一条不符合项。"""
    rule_id: str
    dim: str
    clause: str
    std: str
    severity: str            # error / warning
    message: str
    samples: List[str] = field(default_factory=list)
    count: int = 0

    def head(self) -> str:
        tag = "✗" if self.severity == "error" else "!"
        n = f" ×{self.count}" if self.count else ""
        return f"{tag} [{self.std} §{self.clause}｜{self.dim}] {self.message}{n}"


@dataclass
class GbReport:
    name: str = ""
    path: str = ""
    size: Optional[str] = None
    orientation: Optional[str] = None
    scale: Optional[float] = None
    declared: Optional[int] = None
    base_b: Optional[float] = None
    findings: List[Finding] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    n_checks: int = 0

    @property
    def error_findings(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.error_findings and not self.errors

    def status_line(self) -> str:
        size = f"{self.size}({self.orientation})" if self.size else "—"
        scale = f"1:{int(round(self.scale))}" if self.scale else "—"
        b = f"{self.base_b:g}mm" if self.base_b else "—"
        mark = "✓" if self.ok else "✗"
        n_e = len(self.error_findings)
        n_w = len(self.findings) - n_e
        tail = f"不合规 {n_e} 项" + (f"，提示 {n_w} 项" if n_w else "")
        if not self.findings and not self.errors:
            tail = "符合"
        return (f"  {mark} {self.name:<38} {size:<8} {scale:<7} b={b:<7} {tail}")


# ── 现场事实（一次性从 DXF 抽出来，规则只读它）──────────────────────
@dataclass
class SheetFacts:
    path: str
    name: str
    doc: Any
    msp: Any
    declared: Optional[int] = None
    size: Optional[str] = None
    orientation: Optional[str] = None
    scale: Optional[float] = None          # 实测比例尺分母（优先），否则声明值
    #: 比例尺是否真的可知（实测到或标题栏声明了）。False 时 ``scale`` 只是
    #: 占位的 1.0，任何"模型 mm ÷ scale 后与规范 mm 比大小"的判断都应跳过。
    scale_known: bool = False
    base_b: Optional[float] = None         # 反推出的基本线宽
    used_layers: Set[str] = field(default_factory=set)
    layer_lw: Dict[str, float] = field(default_factory=dict)   # 仅图上用到的层
    #: 图层表里**已定义**的全部线宽（含未被本图使用的层）。
    #: b 是图纸/项目的设定参数，不该随"这张图恰好没画粗线"而变，
    #: 所以反推 b 用定义值，判"实际画出来的线"才用 layer_lw。
    defined_lw: Dict[str, float] = field(default_factory=dict)


def _frame_rects(msp) -> List[Tuple[float, Tuple[float, float, float, float], Any]]:
    """图框层上的闭合多段线，按面积从大到小排：``[(area, bbox, entity), ...]``。

    ``draw_frame`` 在图框层上画**两条**闭合多段线：外层是**幅面线**（纸边，
    表 4.0.4 的 0.25b / 0.35b），内层是**图框线**（b）。要分别判它们各自的
    线宽，就必须先把这两条分开——只看图层线宽会把两者混为一谈，于是"幅面线
    本来就该细"会被误判成"图框线超细"。
    """
    out: List[Tuple[float, Tuple[float, float, float, float], Any]] = []
    for e in msp:
        if e.dxf.layer != "图框" or e.dxftype() != "LWPOLYLINE" or not e.closed:
            continue
        try:
            pts = list(e.get_points("xy"))
        except Exception:
            continue
        if not pts:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        area = (max(xs) - min(xs)) * (max(ys) - min(ys))
        out.append((area, (min(xs), min(ys), max(xs), max(ys)), e))
    out.sort(key=lambda t: -t[0])
    return out


def _frame_rect(msp):
    """图框层最大闭合多段线 bbox —— 幅面外边框（用于反推幅面与比例尺）。"""
    rects = _frame_rects(msp)
    return rects[0][1] if rects else None


def _declared_scale(msp) -> Optional[int]:
    """标题栏里写的 1:N。"""
    for e in msp:
        if e.dxf.layer != "标题栏":
            continue
        t = _text_of(e)
        if not t:
            continue
        m = re.search(r"1\s*:\s*(\d+)", t)
        if m:
            return int(m.group(1))
    return None


def _text_of(e) -> Optional[str]:
    if e.dxftype() == "TEXT":
        return str(e.dxf.text)
    if e.dxftype() == "MTEXT":
        return str(e.text)
    return None


def _text_height(e) -> Optional[float]:
    """文字高度（model mm）。"""
    try:
        if e.dxftype() == "TEXT":
            return float(e.dxf.height)
        if e.dxftype() == "MTEXT":
            return float(e.dxf.char_height)
    except Exception:
        return None
    return None


def _detect_sheet(msp, declared: Optional[int]):
    """由图框外框反推 (幅面, 方向, 比例尺分母)。

    只靠长宽比会误判——A 系列全是 √2，A3 与 A2 形状相同。所以有声明比例尺时
    用它把 k 钉死；没有时按由小到大取第一个形状吻合的幅面。
    """
    outer = _frame_rect(msp)
    if not outer:
        return None, None, None
    w, h = outer[2] - outer[0], outer[3] - outer[1]
    if w <= 0 or h <= 0:
        return None, None, None
    for size in PAPER_ORDER_ASC:
        pw, ph = PAPER[size]
        for orient, (aw, ah) in (("横", (pw, ph)), ("纵", (ph, pw))):
            k = w / aw
            if k <= 0:
                continue
            if abs(k - h / ah) / max(k, h / ah) >= 0.004:
                continue
            if declared and abs(k - declared) / declared >= 0.01:
                continue
            if not declared and size != "A4":
                # 无声明时只信最小的那个，避免把 A3 判成 A0
                continue
            return size, orient, k
    return None, None, None


def _resolve_lineweight(e, doc) -> Optional[float]:
    """实体有效线宽（mm）；未显式设置返回 None。"""
    try:
        lw = e.dxf.get("lineweight", -1)
    except Exception:
        lw = -1
    if lw is not None and lw >= 0:
        return lw / 100.0
    return _layer_lineweight(doc, e.dxf.layer)


def _layer_lineweight(doc, name: str) -> Optional[float]:
    try:
        ly = doc.layers.get(name)
    except Exception:
        return None
    if ly is None:
        return None
    lw = ly.dxf.get("lineweight", -3)
    if lw is None or lw < 0:
        return None
    return lw / 100.0


def _infer_base_b(used_lw: Sequence[float]) -> Optional[float]:
    """反推基本线宽 b = 图上最粗的那条"合规粗线"的线宽。

    定义上 b 就是**粗实线**的线宽，所以"取落在 b 系列 (1.4/1.0/0.7/0.5) 里的
    最粗实测线宽"最稳。

    为什么不能按"覆盖最多实测线宽"打分：0.18mm 这条细线只出现在 b=0.7 的
    线宽组里，于是 b=0.5 的图（0.5/0.35/0.18）会被 0.18 拉去误判成 b=0.7，
    连带 0.35mm 的标题栏外框线被误判（0.7b of 0.5 = 0.35 ✓，0.7b of 0.7 =
    0.5 ✗）。表 4.0.1 注 2 明确允许"各不同线宽中的细线统一采用较细线宽组的
    细线"，所以细线的存在**不能**反证 b。只有图上没有合规粗线时才退回打分法。
    """
    if not used_lw:
        return None
    hits = [lw for lw in set(used_lw)
            if any(abs(lw - b) <= 0.01 for b in B_SERIES)]
    if hits:
        return max(hits)
    best, best_score = None, -1
    for b in B_SERIES:
        row = LINE_WIDTH_GROUPS[b]
        score = sum(1 for lw in set(used_lw)
                    if any(abs(lw - r) <= 0.01 for r in row))
        if score > best_score:
            best, best_score = b, score
    return best


def _facts(path: str) -> SheetFacts:
    import ezdxf
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    f = SheetFacts(path=path, name=os.path.basename(path), doc=doc, msp=msp)
    f.declared = _declared_scale(msp)
    f.size, f.orientation, measured = _detect_sheet(msp, f.declared)
    f.scale = float(measured or f.declared or 1.0)
    # ★ 比例尺是"实测优先、否则用标题栏声明值、都没有才当 1:1"。最后那种
    #   情况下 f.scale==1.0 只是占位，**不代表图纸真是 1:1**；凡是要把模型
    #   单位折算成纸面 mm 再和规范数值比大小的检查，都必须先看这个开关，
    #   否则会把"比例尺未知"当成"1:1"来判，直接报错杀（宁可不报也不误杀）。
    f.scale_known = bool(measured or f.declared)

    for e in msp:
        f.used_layers.add(e.dxf.layer)
    for name in f.used_layers:
        lw = _layer_lineweight(doc, name)
        if lw is not None:
            f.layer_lw[name] = lw
    for ly in doc.layers:
        try:
            lw = ly.dxf.get("lineweight", -3)
        except Exception:
            continue
        if lw is not None and lw >= 0:
            f.defined_lw[ly.dxf.name] = lw / 100.0
    # 实体级显式线宽也要算进去
    ent_lw: List[float] = []
    for e in msp:
        try:
            lw = e.dxf.get("lineweight", -1)
        except Exception:
            continue
        if lw is not None and lw >= 0:
            ent_lw.append(lw / 100.0)
    f.base_b = _infer_base_b(list(f.defined_lw.values()) + ent_lw)
    return f


# ── 规则注册表 ────────────────────────────────────────────────────
@dataclass
class Rule:
    id: str
    dim: str
    clause: str
    std: str
    severity: str
    fn: Callable[[SheetFacts], List[Finding]]
    title: str = ""
    #: 同一函数还会产出的其它规则号（各自在知识库里都应有独立条目）
    emits: Tuple[str, ...] = ()


RULES: List[Rule] = []


def rule(rid: str, dim: str, clause: str, severity: str,
         title: str, std: str = "GB/T 50001-2017",
         emits: Tuple[str, ...] = ()):
    def deco(fn):
        RULES.append(Rule(rid, dim, clause, std, severity, fn, title, emits))
        return fn
    return deco


def _row(b: float) -> Tuple[float, float, float, float]:
    """取基本线宽 b 对应的标准线宽组 ``(b, 0.7b, 0.5b, 0.25b)``。

    **必须查表，不能算乘法**：国标表 4.0.1 里 0.7b 取的是**标准系列里的圆整值**
    —— b=0.7 时 0.7b 记作 0.5mm，而不是 0.49mm。按 0.49 去比会把合规的 0.5
    判成违规（或反之），差 0.01mm 就足以误杀。
    """
    return LINE_WIDTH_GROUPS[b]


#: 可用的圆整线宽（GB/T 50001-2017 表 4.0.1 各列出现过的值）。
#: 表 4.0.4 的 "0.35b" 不属于任何一列——b=0.5 时为 0.175mm，须圆整到 0.18。
_ROUNDED_LW = (0.13, 0.18, 0.25, 0.35, 0.5, 0.7, 1.0, 1.4, 2.0)


def _round_lw(v: float) -> float:
    """把"b 的倍数"线宽圆整到标准系列里最接近的值。"""
    return min(_ROUNDED_LW, key=lambda x: (abs(x - v), x))


def _kb_severity(rid: str, fallback: str) -> str:
    """让知识库能覆盖代码里的默认严重度（知识库是权威配置）。"""
    c = _kb_checks().get(rid)
    if c and c.get("severity") in ("error", "warning"):
        return c["severity"]
    return fallback


# ── §3 图纸幅面 ───────────────────────────────────────────────────
@rule("GB50001-3.1.1-SHEET", "Sheet", "3.1.1", "error",
      "图纸幅面应为标准幅面")
def check_sheet_size(f: SheetFacts) -> List[Finding]:
    if f.size:
        return []
    outer = _frame_rect(f.msp)
    got = (f"{outer[2] - outer[0]:.0f}×{outer[3] - outer[1]:.0f}"
           if outer else "无图框")
    return [Finding(
        "GB50001-3.1.1-SHEET", "Sheet", "3.1.1", "GB/T 50001-2017",
        _kb_severity("GB50001-3.1.1-SHEET", "error"),
        f"图框不是标准幅面（实测 {got}，文档声明 1:{f.declared or '?'}）")]


# ── §4 图线 ───────────────────────────────────────────────────────
@rule("GB50001-4.0.1-LINEWIDTH", "Line", "4.0.1", "error",
      "线宽应取自标准线宽系列且不小于 0.1mm")
def check_line_widths(f: SheetFacts) -> List[Finding]:
    allowed = {round(v, 3)
               for row in LINE_WIDTH_GROUPS.values() for v in row}
    bad = []
    for name, lw in sorted(f.layer_lw.items()):
        if lw < MIN_LINE_WIDTH_MM - 1e-9:
            bad.append(f"图层「{name}」{lw:g}mm < 下限 {MIN_LINE_WIDTH_MM}mm")
        elif round(lw, 3) not in allowed:
            bad.append(f"图层「{name}」{lw:g}mm 不在线宽系列")
    if not bad:
        return []
    return [Finding(
        "GB50001-4.0.1-LINEWIDTH", "Line", "4.0.1", "GB/T 50001-2017",
        _kb_severity("GB50001-4.0.1-LINEWIDTH", "error"),
        "线宽不在标准线宽系列内", bad[:6], len(bad))]


@rule("GB50001-4.0.3-LWGROUP", "Line", "4.0.3", "warning",
      "同一图纸相同比例的各图样应选用同一线宽组")
def check_line_width_group(f: SheetFacts) -> List[Finding]:
    """只有"图上并存两个基本线宽 b"才算真违规。

    表 4.0.1 注 2 允许各不同线宽中的细线统一采用较细线宽组的细线，所以
    0.18mm 细线出现在 b=0.5 的图上**不算**违规；图上并列两个粗线（如 0.7
    与 0.5）才是"选了两个线宽组"，这是 §4.0.3 要禁止的。
    """
    hits = sorted({lw for lw in f.layer_lw.values()
                   if any(abs(lw - b) <= 0.01 for b in B_SERIES)}, reverse=True)
    if len(hits) < 2:
        return []
    return [Finding(
        "GB50001-4.0.3-LWGROUP", "Line", "4.0.3", "GB/T 50001-2017",
        _kb_severity("GB50001-4.0.3-LWGROUP", "warning"),
        f"图上并存 {len(hits)} 个基本线宽 b，应统一为一个线宽组",
        [f"b={b:g}mm" for b in hits], len(hits))]


@rule("GB50001-4.0.4-FRAME-LW", "Line", "4.0.4", "error",
      "图框线宽应为基本线宽 b；标题栏外框线 A2/A3/A4 取 0.7b、A0/A1 取 0.5b",
      emits=("GB50001-4.0.4-TITLE-LW", "GB50001-4.0.4-TRIM-LW",
             "GB50001-4.0.4-MARK-LW"))
def check_frame_line_width(f: SheetFacts) -> List[Finding]:
    """表 4.0.4 把"图框和标题栏线"分成四类，各有各的线宽：

    ==================  ==========  ============
    名称                A0、A1      A2、A3、A4
    ==================  ==========  ============
    图框线              b           b
    标题栏外框线/对中标志  0.5b        0.7b
    标题栏分格线/幅面线    0.25b       0.35b
    ==================  ==========  ============

    旧实现只查图层线宽，把"幅面线本来就该是细线"和"图框线必须是 b"混在
    "图框"一个层上判，结论必然错。这里改成**按实体判**：图框层的两条闭合
    多段线里，小的是图框线（要 b）、大的是幅面线（要 0.25b/0.35b），其余
    直线是对中标志（要 0.5b/0.7b）。
    """
    if not f.base_b:
        return []
    out: List[Finding] = []
    b = f.base_b
    row = _row(b)
    size = f.size or "A3"
    big = size in ("A0", "A1")
    # 标题栏外框线 / 对中标志：0.5b（A0/A1）、0.7b（A2/A3/A4）
    mark_want, mark_label = (row[2], "0.5b") if big else (row[1], "0.7b")
    # 标题栏分格线 / 幅面线：0.25b（A0/A1）、0.35b（A2/A3/A4）
    # 注意 0.35b 不在表 4.0.1 的四列里（b=0.5 时 = 0.175mm），须圆整到 0.18。
    trim_want = _round_lw((0.25 if big else 0.35) * b)
    trim_label = "0.25b" if big else "0.35b"

    def _sev(rid: str, fallback: str) -> str:
        return _kb_severity(rid, fallback)

    rects = _frame_rects(f.msp)
    if not rects:
        return out

    def _add(rid: str, msg: str, sample: Optional[str] = None,
             sev: str = "error") -> None:
        out.append(Finding(
            rid, "Line", "4.0.4", "GB/T 50001-2017", _sev(rid, sev), msg,
            [sample] if sample else []))

    if len(rects) >= 2:
        inner, outer = rects[1], rects[0]
        lw_in = _resolve_lineweight(inner[2], f.doc)
        if lw_in is not None and abs(lw_in - b) > 0.01:
            _add("GB50001-4.0.4-FRAME-LW",
                 f"图框线宽 {lw_in:g}mm，应为基本线宽 b={b:g}mm（表4.0.4）")
        lw_out = _resolve_lineweight(outer[2], f.doc)
        if lw_out is not None and abs(lw_out - trim_want) > 0.02:
            _add("GB50001-4.0.4-TRIM-LW",
                 f"幅面线宽 {lw_out:g}mm，{size} 幅面宜为 "
                 f"{trim_label}={trim_want:g}mm（表4.0.4）", sev="warning")
    else:
        # 只有一条闭合线（精简图/老图）：按图框线判，别拿幅面线的细去卡它
        lw = _resolve_lineweight(rects[0][2], f.doc)
        if lw is not None and abs(lw - b) > 0.01:
            _add("GB50001-4.0.4-FRAME-LW",
                 f"图框线宽 {lw:g}mm，应为基本线宽 b={b:g}mm（表4.0.4）")

    # 对中标志：图框层上除两条闭合多段线之外的直线
    marks = [e for e in f.msp
             if e.dxf.layer == "图框" and e.dxftype() == "LINE"]
    if marks:
        lws = {_resolve_lineweight(e, f.doc) for e in marks}
        bad = sorted(v for v in lws if v is not None and abs(v - mark_want) > 0.02)
        if bad:
            _add("GB50001-4.0.4-MARK-LW",
                 f"对中标志线宽 {', '.join(f'{v:g}mm' for v in bad)}，{size} "
                 f"幅面宜为 {mark_label}={mark_want:g}mm（表4.0.4）",
                 sev="warning")

    tb = f.layer_lw.get("标题栏")
    if tb is not None and abs(tb - mark_want) > 0.01:
        out.append(Finding(
            "GB50001-4.0.4-TITLE-LW", "Line", "4.0.4", "GB/T 50001-2017",
            _kb_severity("GB50001-4.0.4-TITLE-LW", "warning"),
            f"标题栏外框线宽 {tb:g}mm，{size} 幅面应为 "
            f"{mark_label}={mark_want:g}mm（表4.0.4）"))
    return out


@rule("GB50001-4.0.10-OVERLAP", "Line", "4.0.10", "error",
      "图线不得与文字、数字或符号重叠")
def check_line_text_overlap(f: SheetFacts) -> List[Finding]:
    try:
        from .standards import frame as F
        from .audit import _scan_text_conflicts
    except Exception:
        return []
    try:
        overlaps, _ = _scan_text_conflicts(f.msp, F, set(SHEET_LAYERS))
    except Exception:
        return []
    if not overlaps:
        return []
    samples = [f"{a!r} × {b!r}" for a, b, _ in overlaps[:5]]
    return [Finding(
        "GB50001-4.0.10-OVERLAP", "Line", "4.0.10", "GB/T 50001-2017",
        _kb_severity("GB50001-4.0.10-OVERLAP", "error"),
        "文字相互重叠（图线压字同源）", samples, len(overlaps))]


# ── §5 字体 ───────────────────────────────────────────────────────
@rule("GB50001-5.0.2-TEXTH", "Text", "5.0.2", "error",
      "文字字高应从标准字高系列选用（汉字不宜小于 3.5mm）",
      emits=("GB50001-5.0.7-TEXTH",))
def check_text_heights(f: SheetFacts) -> List[Finding]:
    """只查「图样及说明」——标题栏另按 §3.2.2 由工程自定，不计入。

    §3.2.2 明确标题栏的尺寸/格式/分区由工程需要确定，故标题栏内的
    2.2~2.8mm 小字不算违规；图样标注里的汉字小于 3.5mm 才是硬伤。
    """
    scale = f.scale or 1.0
    cjk_bad, num_bad = [], []
    for e in f.msp:
        if e.dxf.layer in SHEET_LAYERS:
            continue
        t = _text_of(e)
        h = _text_height(e)
        if t is None or h is None or not t.strip():
            continue
        mm = h / scale
        if CJK_RE.search(t):
            if mm < MIN_TEXT_CJK_MM - 0.01:
                cjk_bad.append(f"{t[:16]!r} {mm:.1f}mm")
        elif mm < MIN_TEXT_NUM_MM - 0.01:
            num_bad.append(f"{t[:16]!r} {mm:.1f}mm")

    out: List[Finding] = []
    if cjk_bad:
        out.append(Finding(
            "GB50001-5.0.2-TEXTH", "Text", "5.0.2", "GB/T 50001-2017",
            _kb_severity("GB50001-5.0.2-TEXTH", "error"),
            f"汉字字高小于 {MIN_TEXT_CJK_MM}mm（纸面）",
            cjk_bad[:5], len(cjk_bad)))
    if num_bad:
        out.append(Finding(
            "GB50001-5.0.7-TEXTH", "Text", "5.0.7", "GB/T 50001-2017",
            _kb_severity("GB50001-5.0.7-TEXTH", "error"),
            f"字母/数字字高小于 {MIN_TEXT_NUM_MM}mm（纸面）",
            num_bad[:5], len(num_bad)))
    return out


@rule("GB50001-5.0.3-FONTS", "Text", "5.0.3", "warning",
      "同一图纸字体种类不应超过两种")
def check_font_kinds(f: SheetFacts) -> List[Finding]:
    used = set()
    for e in f.msp:
        t = _text_of(e)
        if t is None:
            continue
        try:
            used.add(e.dxf.get("style", "Standard"))
        except Exception:
            pass
    if len(used) <= 2:
        return []
    return [Finding(
        "GB50001-5.0.3-FONTS", "Text", "5.0.3", "GB/T 50001-2017",
        _kb_severity("GB50001-5.0.3-FONTS", "warning"),
        "同一图纸使用的文字样式超过两种", sorted(used), len(used))]


@rule("GB50001-5.0.10-LEADING0", "Text", "5.0.10", "warning",
      "数字小于 1 时应写出个位的 0")
def check_leading_zero(f: SheetFacts) -> List[Finding]:
    bad = []
    for e in f.msp:
        t = _text_of(e)
        if not t:
            continue
        # 形如 ".5" / "-.25" / "=.75"：小数点前紧跟非数字
        m = re.search(r"(?<![\d.])-?\.(\d+)", t)
        if m:
            bad.append(repr(t[:24]))
    if not bad:
        return []
    return [Finding(
        "GB50001-5.0.10-LEADING0", "Text", "5.0.10", "GB/T 50001-2017",
        _kb_severity("GB50001-5.0.10-LEADING0", "warning"),
        "小于 1 的数字缺个位 0（应写 0.5 而非 .5）", bad[:5], len(bad))]


# ── §8 定位轴线 ───────────────────────────────────────────────────
@rule("GB50001-8.0.1-AXISLINE", "AxisGrid", "8.0.1", "error",
      "定位轴线应用 0.25b 单点长画线绘制")
def check_axis_linetype(f: SheetFacts) -> List[Finding]:
    """轴线层必须是点画线（CENTER 类）且线宽落在"细线"区间内。

    §8.0.1 的标称值是 0.25b，但表 4.0.1 注 2 允许同一张图的细线统一采用
    较细线宽组的细线，而表 4.0.4 又强制 A2/A3/A4 的幅面线 = 0.35b
    （b=0.5 时 = 0.175 → 0.18mm），因此 0.18mm 本身就是这张图里的合法细线。
    这里只判"是不是细线"——比 0.25b 还细（可能低于可打印下限）、或粗到
    0.5b（会被读成中实线、与轮廓线混淆）才算问题，标称值仅作参考。

    只在图上确实存在轴线层时才判——没画轴线的图不该因此被扣。
    """
    if not f.base_b:
        return []
    lo = _round_lw(0.25 * f.base_b)      # 细线下限 / 标称值
    hi = _round_lw(0.5 * f.base_b)       # 细线上限（到这就成中实线了）
    bad = []
    for name in sorted(f.used_layers):
        if not any(k in name for k in ("轴线", "点画线", "中心线")):
            continue
        try:
            ly = f.doc.layers.get(name)
            lt = str(ly.dxf.linetype).upper() if ly is not None else "?"
        except Exception:
            lt = "?"
        if not any(k in lt for k in ("CENTER", "DASHDOT", "PHANTOM")):
            bad.append(f"图层「{name}」线型 {lt}，应为点画线")
        lw = f.layer_lw.get(name)
        if lw is None:
            continue
        if lw < lo - 0.02:
            bad.append(f"图层「{name}」{lw:g}mm 偏细，标称 0.25b={lo:g}mm")
        elif lw >= hi - 0.02:
            bad.append(f"图层「{name}」{lw:g}mm 偏粗（≥0.5b={hi:g}mm），"
                       f"易与中实线混淆，标称 0.25b={lo:g}mm")
    if not bad:
        return []
    return [Finding(
        "GB50001-8.0.1-AXISLINE", "AxisGrid", "8.0.1", "GB/T 50001-2017",
        _kb_severity("GB50001-8.0.1-AXISLINE", "error"),
        "定位轴线画法不符", bad[:6], len(bad))]


# ── §11 尺寸标注 ──────────────────────────────────────────────────
_DIMLINEAR_TYPES = {0, 1, 6}      # 线性/对齐/坐标 —— 用斜短线
#: §11.1.4 后半句：半径/直径/角度/弧长 —— 用箭头（b ≥ 1mm）
_DIMRADIAL_TYPES = {2, 3, 4, 5}

#: 实心闭合箭头在 AutoCAD 里的"符号名"就是空字符串，对应 ezdxf 块 ``_CLOSEDFILLED``。
_CLOSED_FILLED_BLOCK = "_CLOSEDFILLED"


def _norm_arrow_name(name) -> str:
    """把 ``dimblk`` 的各种写法归一成小写符号名。

    同一个起止符在 DXF 里有三种写法，判等前必须先归一，否则会把合规图纸判违规：

    * 块名 —— ``_ARCHTICK``（``setup_dimstyles`` 写进去、DIMSTYLE 里存的值）；
    * AutoCAD 符号名 —— ``ARCHTICK``（ezdxf ``Dimension.override()`` 返回的形式，
      前导下划线被 ``arrow_name()`` 去掉了）；
    * **空符号名** —— ``""`` 表示 AutoCAD 默认的实心闭合箭头，块名是
      ``_CLOSEDFILLED``（``block_name("") == "_CLOSEDFILLED"``）。

    归一规则：空 → ``"closedfilled"``；否则去前导下划线转小写。
    """
    n = str(name or "").strip()
    if not n:
        return "closedfilled"
    return n.lstrip("_").lower() or "closedfilled"


def _arrow_block_candidates(name) -> List[str]:
    """按符号名列出可能的块名候选（在 ``doc.blocks`` 里逐个试）。"""
    sym = _norm_arrow_name(name)
    raw = str(name or "").strip()
    out: List[str] = []
    for c in (raw, "_" + sym, sym, sym.upper(), "_" + sym.upper()):
        if c and c not in out:
            out.append(c)
    if sym == "closedfilled":
        for c in (_CLOSED_FILLED_BLOCK, "_ClosedFilled"):
            if c not in out:
                out.append(c)
    return out


def _resolve_block(doc, name: str):
    """按 ``dimblk`` 符号名找到真正的块（找不到返回 None）。"""
    for c in _arrow_block_candidates(name):
        try:
            if c in doc.blocks:
                return doc.blocks.get(c)
        except Exception:
            continue
    return None


def _block_stroke_len(doc, name: str) -> Optional[float]:
    """箭头块里那条线段的长度（块自身单位）。

    §11.1.4 要的是**画出来的斜短线长度**，不是 dimasz。AutoCAD 的
    ``_ARCHTICK`` 块是一条 (-0.5,-0.5)→(0.5,0.5) 的线段，长度 √2；尺寸匿名块
    以 ``scale=dimasz`` 插入它，所以实际长度 = √2 × dimasz。直接拿 dimasz 当
    "起止符长度"会漏判（实测 dimasz 2.5mm 在图上画出来是 3.54mm，已超 2~3mm）。
    """
    blk = _resolve_block(doc, name)
    if blk is None:
        return None
    best = 0.0
    for e in blk:
        try:
            if e.dxftype() == "LINE":
                a, b = e.dxf.start, e.dxf.end
                best = max(best, ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5)
            elif e.dxftype() in ("LWPOLYLINE", "POLYLINE"):
                pts = [(p[0], p[1]) for p in e.get_points("xy")]
                for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                    best = max(best, ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5)
            elif e.dxftype() in ("SOLID", "TRACE"):
                pts = [(p[0], p[1]) for p in
                       (e.dxf.vtx0, e.dxf.vtx1, e.dxf.vtx2, e.dxf.get("vtx3", e.dxf.vtx2))]
                for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                    best = max(best, ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5)
        except Exception:
            continue
    return best or None


def _block_extent(doc, name: str):
    """块内几何的包围盒尺寸 ``(dx, dy)``（块自身单位）。

    箭头块的"宽度"要用**包围盒高度**、不能用最长线段：``_CLOSEDFILLED``
    是 SOLID ``(-1, ±0.1644) (0,0)``，最长边 1.0135 是斜边，而 b 要的是
    垂直于尺寸线的宽 0.3288。斜短线的长度则相反，用 :func:`_block_stroke_len`。
    """
    blk = _resolve_block(doc, name)
    if blk is None:
        return None
    xs, ys = [], []
    for e in blk:
        try:
            if e.dxftype() == "LINE":
                pts = [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
            elif e.dxftype() in ("LWPOLYLINE", "POLYLINE"):
                pts = [(p[0], p[1]) for p in e.get_points("xy")]
            elif e.dxftype() in ("SOLID", "TRACE"):
                pts = [(p[0], p[1]) for p in
                       (e.dxf.vtx0, e.dxf.vtx1, e.dxf.vtx2,
                        e.dxf.get("vtx3", e.dxf.vtx2))]
            else:
                continue
            xs += [p[0] for p in pts]
            ys += [p[1] for p in pts]
        except Exception:
            continue
    if not xs:
        return None
    return (max(xs) - min(xs), max(ys) - min(ys))


def _dim_arrow_block(f: "SheetFacts", e) -> str:
    """取某标注**实际生效**的起止符符号名（``""`` = 实心闭合箭头）。

    不能直接读 ``override().get("dimblk")`` 再对空值回退到样式：

    * 实心闭合箭头的符号名**就是空字符串**（块名 ``_CLOSEDFILLED``），
      于是"override 指定了空箭头"与"override 没这项"值一样，回退就会把
      它误读成样式里的 ``_ARCHTICK``；
    * 还要照顾 ``dimsah``/``dimblk1``/``dimblk2``（两端不同箭头）与
      ``dimtsz != 0``（不用箭头块）这两种情况。

    ezdxf 的 ``DimStyleOverride.get_arrow_names()`` 正好把这些规则都实现了，
    且它内部 ``get()`` 同样是"override 优先、样式兜底"，故直接用它。
    """
    try:
        names = e.override().get_arrow_names()
        return str(names[0] or "")
    except Exception:
        pass
    try:
        ds = f.doc.dimstyles.get(e.dxf.get("dimstyle", "Standard"))
        if ds is not None:
            return str(ds.dxf.get("dimblk", "") or "")
    except Exception:
        pass
    return ""


def _dim_var(f: "SheetFacts", e, var: str, default: float = 0.0) -> float:
    """取某标注的样式变量：实体 override 优先，其次标注样式。"""
    try:
        ov = e.override()
        v = ov.get(var)
    except Exception:
        v = None
    if v in (None, ""):
        try:
            ds = f.doc.dimstyles.get(e.dxf.get("dimstyle", "Standard"))
            v = ds.dxf.get(var) if ds else None
        except Exception:
            v = None
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@rule("GB50001-11.1.4-DIMTICK", "Dimension", "11.1.4", "error",
      "尺寸起止符号用中粗斜短线（线性尺寸不得用箭头）",
      emits=("GB50001-11.1.4-DIMSIZE", "GB50001-11.1.4-ARROWW"))
def check_dim_tick(f: SheetFacts) -> List[Finding]:
    bad, sizes, arrow_bad, narrow = [], [], [], []
    for e in f.msp.query("DIMENSION"):
        try:
            base = int(e.dxf.get("dimtype", 0)) & 0x0F
        except Exception:
            base = 0
        label = _dim_text(e) or "—"
        tsz = _dim_var(f, e, "dimtsz")
        blk = _dim_arrow_block(f, e)
        sym = _norm_arrow_name(blk)

        if base not in _DIMLINEAR_TYPES:
            # §11.1.4 后半句 + 条文说明："圆弧的直径、半径等用箭头"。
            # 径向/角度尺寸要的是实心箭头，且宽度 b ≥ 1mm，故单独判。
            if base not in _DIMRADIAL_TYPES:
                continue
            if tsz > 0:
                continue      # 用 dimtsz 画起止符的，不按箭头块判，宁可不报
            if sym == "archtick":
                arrow_bad.append(f"标注「{label}」用了 45° 斜短线，应为箭头")
                continue
            ext = _block_extent(f.doc, blk)
            if not ext or ext[1] <= 0:
                continue          # 块取不到 / 非箭头几何：宁可不报
            asz = _dim_var(f, e, "dimasz")
            if asz <= 0 or not f.scale_known:
                continue      # 比例尺未知时折算不出纸面宽度，不判
            width = asz * ext[1] / (f.scale or 1.0)
            if width < ARROW_WIDTH_MIN_MM - 0.01:
                narrow.append(f"标注「{label}」箭头宽 b={width:.2f}mm")
            continue

        # ── 线性/对齐/坐标：§11.1.4 前半句，45° 中粗斜短线 ──
        # dimtsz != 0 时 AutoCAD 直接用它当斜短线长度（不再插箭头块），
        # 这本身就是斜短线画法，不能因为 dimblk 是空名就判成箭头。
        if tsz <= 0 and sym != "archtick":
            bad.append(f"标注「{label}」起止符号={blk or '实心闭合箭头'}")
        # 斜短线在纸面上的实际长度（2~3mm，§11.1.4）：
        #   - dimtsz != 0 → 长度就是 dimtsz；
        #   - 斜短线块 → 长度 = 块内线段长 × dimasz。
        # 两种情况都不是时（比如线性标注错用了箭头），长度无从谈起，
        # DIMTICK 已经把"用错起止符"报出来了，不再重复报长度。
        # 比例尺未知时折算不出纸面长度，宁可不报也不误杀。
        if not f.scale_known:
            continue
        scale = f.scale or 1.0
        if tsz > 0:
            sizes.append(tsz / scale)
        elif sym == "archtick":
            asz = _dim_var(f, e, "dimasz")
            if asz > 0:
                unit = _block_stroke_len(f.doc, blk) or 1.0
                sizes.append(asz * unit / scale)

    out: List[Finding] = []
    if bad:
        out.append(Finding(
            "GB50001-11.1.4-DIMTICK", "Dimension", "11.1.4", "GB/T 50001-2017",
            _kb_severity("GB50001-11.1.4-DIMTICK", "error"),
            "线性尺寸起止符号不是 45° 中粗斜短线（应设 dimblk=_ARCHTICK）",
            bad[:5], len(bad)))
    if sizes:
        off = [f"{v:.1f}mm" for v in sizes
               if not (DIM_TICK_MIN_MM - 0.01 <= v <= DIM_TICK_MAX_MM + 0.01)]
        if off:
            out.append(Finding(
                "GB50001-11.1.4-DIMSIZE", "Dimension", "11.1.4",
                "GB/T 50001-2017",
                _kb_severity("GB50001-11.1.4-DIMTICK", "error"),
                f"起止符号长度超出 {DIM_TICK_MIN_MM:g}~{DIM_TICK_MAX_MM:g}mm",
                off[:5], len(off)))
    if arrow_bad or narrow:
        msg = (f"径向尺寸起止符号应为箭头（宽度 b ≥ {ARROW_WIDTH_MIN_MM:g}mm）")
        out.append(Finding(
            "GB50001-11.1.4-ARROWW", "Dimension", "11.1.4", "GB/T 50001-2017",
            _kb_severity("GB50001-11.1.4-DIMTICK", "error"),
            msg, (arrow_bad + narrow)[:5], len(arrow_bad) + len(narrow)))
    return out



def _dim_text(e) -> str:
    try:
        return str(e.dxf.get("text", "") or "")
    except Exception:
        return ""


# ── §13 图层 ──────────────────────────────────────────────────────
@rule("GB50001-13.0.1-LAYERNAME", "CADLayer", "13.0.1", "error",
      "图层名称汉字与英文字母不得混用")
def check_layer_naming(f: SheetFacts) -> List[Finding]:
    bad = []
    for name in sorted(f.used_layers):
        if name in SYSTEM_LAYERS:
            continue
        if CJK_RE.search(name) and LATIN_RE.search(name):
            bad.append(f"图层「{name}」混用汉字与英文字母")
    if not bad:
        return []
    return [Finding(
        "GB50001-13.0.1-LAYERNAME", "CADLayer", "13.0.1", "GB/T 50001-2017",
        _kb_severity("GB50001-13.0.1-LAYERNAME", "error"),
        "图层名混用汉字与英文字母（§13.0.1-2）", bad[:6], len(bad))]


# ── 引擎 ──────────────────────────────────────────────────────────
def audit_dxf(path: str) -> GbReport:
    """审一张图：逐条跑 ``RULES``，返回符合性报告。**不抛异常**。"""
    rep = GbReport(name=os.path.basename(path), path=path)
    try:
        f = _facts(path)
    except Exception as e:                                  # 读取失败也要出报告
        rep.errors.append(f"打不开：{e}")
        return rep

    rep.size, rep.orientation = f.size, f.orientation
    rep.scale, rep.declared, rep.base_b = f.scale, f.declared, f.base_b

    for r in RULES:
        rep.n_checks += 1
        try:
            for fd in r.fn(f) or []:
                fd.severity = _kb_severity(r.id, fd.severity or r.severity)
                rep.findings.append(fd)
        except Exception as e:                              # 单条规则坏掉不拖垮全图
            rep.errors.append(f"规则 {r.id} 执行失败：{e}")
    return rep


def audit_dir(root: str, pattern: str = "*.dxf") -> List[GbReport]:
    files = (sorted(glob.glob(os.path.join(root, pattern)))
             if os.path.isdir(root) else [root])
    return [audit_dxf(p) for p in files]


# ── 人话报告 ──────────────────────────────────────────────────────
def format_report(reports: Sequence[GbReport], *, verbose: bool = False,
                  only: Optional[str] = None) -> str:
    """``only='error'`` 时只列不合规项，隐藏提示。"""
    if not reports:
        return "没有找到 dxf。"

    agg: Dict[str, int] = {}
    meta: Dict[str, Tuple[str, str, str]] = {}
    lines: List[str] = []
    n_bad = 0
    for r in reports:
        if not r.ok:
            n_bad += 1
        lines.append(r.status_line())
        for e in r.errors:
            lines.append(f"      ⚠ {e}")
        for fd in r.findings:
            if only == "error" and fd.severity != "error":
                continue
            agg[fd.rule_id] = agg.get(fd.rule_id, 0) + 1
            meta.setdefault(fd.rule_id, (fd.std, fd.clause, fd.dim))
            lines.append(f"      {fd.head()}")
            if verbose:
                for s in fd.samples:
                    lines.append(f"          · {s}")

    n = len(reports)
    lines.append("")
    if agg:
        lines.append("  不合规条款汇总（条款 → 涉及图纸数）：")
        for rid, cnt in sorted(agg.items(), key=lambda kv: -kv[1]):
            std, clause, dim = meta.get(rid, ("?", "?", "?"))
            lines.append(f"    {cnt:>3}/{n} 张  {std} §{clause}｜{dim}  {rid}")
        lines.append("")
    verdict = ("全部通过国标符合性审核，可进入人工审核" if n_bad == 0
               else f"{n_bad} / {n} 张存在不符合项，先修再交付")
    lines.append(f"  共 {n} 张 → {verdict}")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────
def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="envcad audit",
        description="国标审图：按 GB/T 50001-2017 逐条检查 DXF")
    ap.add_argument("path", nargs="?", help="目录或单个 dxf")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--verbose", "-v", action="store_true", help="列出违规样本")
    ap.add_argument("--only", choices=["error", "warning"], default=None,
                    help="只显示某一严重度")
    ap.add_argument("--rules", action="store_true", help="列出全部已实现规则")
    args = ap.parse_args(argv)

    if args.rules:
        checks = _kb_checks()
        total_ids = sum(1 + len(r.emits) for r in RULES)
        missing = []
        print(f"已实现检查函数 {len(RULES)} 个，覆盖规则号 {total_ids} 条：")
        for r in RULES:
            if r.id not in checks:
                missing.append(r.id)
            print(f"  {r.id:<34} §{r.clause:<7} {r.dim:<10} {r.severity:<8} {r.title}"
                  + ("" if r.id in checks else "   [知识库缺条目]"))
            for eid in r.emits:
                kb_mark = "知识库已登记" if eid in checks else "[知识库缺条目]"
                if eid not in checks:
                    missing.append(eid)
                print(f"  {eid:<34} §{r.clause:<7} {r.dim:<10} "
                      f"由上一函数一并产出（{kb_mark}）")
        known = {r.id for r in RULES} | {e for r in RULES for e in r.emits}
        orphan = [c.get("id") for c in load_kb().get("complianceChecks", [])
                  if c.get("checkable") and c.get("id") not in known]
        if missing:
            print(f"\n⚠ 代码有规则、知识库缺条目：{missing}")
        if orphan:
            print(f"⚠ 知识库声明可机检、代码无对应函数：{orphan}")
        if not missing and not orphan:
            print("\n✓ 代码规则号与知识库 complianceChecks 一一对应")
        print("\n知识库声明的不可机检条款（宁可不报，也不报错杀）：")
        for c in load_kb().get("complianceChecks", []):
            if c.get("checkable") is False:
                print(f"  {c.get('id'):<34} §{c.get('clause', '?'):<7} "
                      f"{(c.get('reason') or '')[:64]}")
        return 0

    if not args.path:
        ap.error("需要给出目录或 dxf（或加 --rules 只列规则）")
    reports = audit_dir(args.path)
    if not reports:
        print(f"没有找到 dxf：{args.path}")
        return 2
    if args.json:
        print(json.dumps([{
            "name": r.name, "size": r.size, "orientation": r.orientation,
            "scale": r.scale, "declared": r.declared, "base_b": r.base_b,
            "ok": r.ok, "errors": r.errors,
            "findings": [{
                "rule_id": f.rule_id, "clause": f.clause, "std": f.std,
                "dim": f.dim, "severity": f.severity, "count": f.count,
                "message": f.message, "samples": f.samples,
            } for f in r.findings],
        } for r in reports], ensure_ascii=False, indent=2))
    else:
        print(format_report(reports, verbose=args.verbose, only=args.only))
    return 0 if all(r.ok for r in reports) else 1


if __name__ == "__main__":                                  # pragma: no cover
    raise SystemExit(main())
