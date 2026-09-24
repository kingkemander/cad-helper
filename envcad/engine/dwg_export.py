"""DXF → DWG 导出：转换器可插拔，不绑定特定 CAD。

为什么要"可插拔"
----------------
DWG 是闭源格式，Python 生态没有能直接写 DWG 的库（ezdxf 只能写 DXF）。
落地的办法都是"借一个本机已装的转换器"。而各人机器上的转换器不同：

  oda        ODA File Converter —— 官方免费工具，支持 DWG 2000~2018，跨平台，
             业界事实标准。**首选**。需自行下载安装（ODA 官网，免费注册）。
  libredwg   GNU LibreDWG 的 dxf2dwg —— 免费、部分系统已自带，
             但只支持到 r2004，且读不了"带旋转的 MTEXT"（见下方 _downgrade_mtext）。
  com        Windows 上已安装的 AutoCAD/ZWCAD/GstarCAD/BricsCAD，
             走 COM 自动化另存（原 multicad_bridge 的路径），仅 Windows。

以后换成别的 CAD，只需在 BACKENDS 里加一条 + 写一个小函数，
`envcad dwg` 与上层 `deliver` 都不用动。

关于 LibreDWG 的"带旋转 MTEXT"坑
--------------------------------
实测 GNU LibreDWG 0.14 的 DXF 读入器遇到 `code 50`（MTEXT 旋转）会直接
`READ ERROR` 拒绝整个文件。而"真实 DIMENSION 实体"生成的匿名块
（`*D1`/`*D2`…）里的尺寸文字正好是带旋转的 MTEXT —— 于是图纸一有尺寸标注
就转不了。

绕法：导出前把 MTEXT 降级为 TEXT（TEXT 的旋转它认）。降级会按 MTEXT 的
attachment 重新折算 TEXT 的插入点，尽量不挪位。**只作用于 DWG 导出的临时
副本，交付用的 DXF 不动。**

转换质量实测对比（同一批 12 张图 / 24 个标注，round-trip 回读 get_measurement）：
  oda        测量值损坏 0/24 (0%)，输出 AC1032(R2018)，且文件更小（21 KB vs 30 KB）
  libredwg   测量值损坏 11/24 (46%)，输出 AC1015(R2000)
=> 装了 ODA 就用 ODA，不要再拿 LibreDWG 的结果当"正常损耗"解释。

LibreDWG 的代价（实测 0.14）：
  - DIMENSION 实体**保留**，匿名显示块 `*Dn` 也在 —— 打开 DWG 看图是对的，
    尺寸文字可读，**不会**炸成普通线和文字（早期文档如此描述，实测不成立，已更正）。
  - 但约 **46%（11/24）** 的标注会**丢驱动点**：`get_measurement()` 归零或漂移。
    后果是"看图没事、**一改就出问题**"——在 CAD 里拉伸/移动标注不会联动重算，
    任何程序去读测量值会读到 0。受影响 7/7 张带标注的图。
  - 只写到 r2004。

要求标注**可编辑、可联动**，请用 ODA 或 Windows 上的原生 CAD。
交付件仍应以 DXF 为准；DWG 只作看图/送审副本。
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
from typing import Callable

__all__ = [
    "BACKENDS",
    "available_backends",
    "detect_backend",
    "export_dwg",
    "export_dir",
    "backend_help",
    "normalize_version",
]

# ---------------------------------------------------------------- 后端注册表
# 名字 -> (探测可执行文件, 说明)
BACKENDS: dict[str, dict] = {
    "oda": {
        "probe": [
            "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
            "ODAFileConverter",
        ],
        "label": "ODA File Converter",
        "versions": ("2018", "2013", "2010", "2007", "2004", "2000", "r14", "r12"),
        "default": "2018",
        "keeps_dimension": True,
        "install": "https://www.opendesign.com/guestfiles/oda_file_converter（免费注册后下载）",
    },
    "libredwg": {
        "probe": ["dxf2dwg"],
        "label": "GNU LibreDWG (dxf2dwg)",
        "versions": ("r2004", "r2000", "r14", "r12"),
        "default": "r2000",
        "keeps_dimension": False,
        "install": "macOS: brew install libredwg ／ Debian: apt install libredwg-tools",
    },
    "com": {
        "probe": [],  # Windows 专用，运行期再判
        "label": "本机 CAD（AutoCAD/ZWCAD/GstarCAD/BricsCAD，COM 另存）",
        "versions": ("2018", "2013", "2010", "2007", "2004", "2000"),
        "default": "2018",
        "keeps_dimension": True,
        "install": "Windows 上安装 AutoCAD / 中望 / 浩辰 / BricsCAD（需 pywin32）",
    },
}

# 探测优先级：保真度高的在前
_PRIORITY = ("oda", "libredwg", "com")


# ---------------------------------------------------------------- 探测
def _probe_one(names: list[str]) -> str | None:
    for n in names:
        if os.path.isabs(n):
            if os.path.exists(n):
                return n
        else:
            p = shutil.which(n)
            if p:
                return p
    return None


def backend_available(name: str) -> bool:
    """该后端此刻是否可用。"""
    if name == "com":
        try:  # 延迟导入，避免非 Windows 平台 import 失败
            from win32com.client import Dispatch
        except Exception:
            return False
        return callable(Dispatch)  # 有 pywin32 才可能走 COM
    meta = BACKENDS.get(name)
    if not meta:
        return False
    return _probe_one(meta["probe"]) is not None


def available_backends() -> list[str]:
    """按优先级返回当前可用的后端名。"""
    return [n for n in _PRIORITY if backend_available(n)]


def detect_backend() -> str | None:
    """自动挑一个后端：优先级 oda > libredwg > com。"""
    got = available_backends()
    return got[0] if got else None


def backend_help() -> str:
    """没有任何后端时，给用户的安装指引。"""
    lines = ["当前机器上没有找到可用的 DXF→DWG 转换器。可任选一个：", ""]
    for n in _PRIORITY:
        meta = BACKENDS[n]
        lines.append(f"  · {meta['label']}   安装：{meta['install']}")
    lines += [
        "",
        "说明：DWG 是闭源格式，任何方案都要借一个本机转换器；",
        "      不装转换器时，交付 DXF 也可以在 AutoCAD/中望/浩辰里直接打开。",
    ]
    return "\n".join(lines)


def normalize_version(version: str | None, backend: str) -> str:
    """把用户写的版本号归一到该后端认的写法。"""
    meta = BACKENDS[backend]
    if not version:
        return meta["default"]
    v = str(version).strip().lower().lstrip("r")
    for valid in meta["versions"]:
        if v == valid.lower().lstrip("r"):
            return valid
    raise ValueError(
        f"{backend} 不支持 DWG 版本 {version!r}；可选：{', '.join(meta['versions'])}"
    )


# ------------------------------------------------------- MTEXT → TEXT 降级
# MTEXT attachment_point -> (水平系数, 垂直系数)
#   水平：0=左 0.5=中 1=右（相对宽度）；垂直：0=底 0.5=中 1=顶（相对字高）
_ATTACH = {
    1: (0.0, 1.0), 2: (0.5, 1.0), 3: (1.0, 1.0),
    4: (0.0, 0.5), 5: (0.5, 0.5), 6: (1.0, 0.5),
    7: (0.0, 0.0), 8: (0.5, 0.0), 9: (1.0, 0.0),
}

# 还原回 DXF 控制码：这三个符号用控制码比用字面字符可靠 ——
# 老 CAD 的字体未必含 Ø/° 的字形，而 %%c/%%d/%%p 是字体无关的。
_CTRL_BACK = {"Ø": "%%c", "⌀": "%%c", "°": "%%d", "±": "%%p"}


def _mtext_to_text_content(mtext) -> str:
    """MTEXT 内容 -> 等价的单行 TEXT 内容。

    先让 ezdxf 把所有 MTEXT 内联格式码解析干净（多行、字体切换、堆叠分数…），
    再把 Ø/°/± 还原成 DXF 控制码。

    代价：多行 MTEXT 会变成一行（换行折成空格）—— TEXT 天生单行。
    尺寸标注文字都是单行，实际不受影响。
    """
    try:
        s = mtext.plain_text()
    except Exception:
        s = mtext.dxf.text
    s = " ".join(s.split())  # 折掉换行/多余空白
    for ch, code in _CTRL_BACK.items():
        s = s.replace(ch, code)
    return s


def _downgrade_mtext(doc) -> int:
    """把全文档（含块定义）里的 MTEXT 换成等位的 TEXT。返回转换条数。

    TEXT 的插入点是"基线左端"，而 MTEXT 的插入点是 attachment 指定的锚点，
    所以要把锚点反算成基线左端，并让 TEXT 绕自身插入点旋转 —— 这样旋转过的
    尺寸文字（如 315°、90°）才不会飘走。
    """
    from ezdxf.tools.text_size import mtext_size

    n = 0
    for blk in doc.blocks:
        for e in list(blk):
            if e.dxftype() != "MTEXT":
                continue
            try:
                w = mtext_size(e).total_width
            except Exception:
                # 量不出来就退回"按字高估宽"，宁粗不崩
                w = len(e.text) * e.dxf.char_height * 0.7

            h = float(e.dxf.char_height)
            rot = math.radians(float(e.dxf.get("rotation", 0.0) or 0.0))
            ax, ay = _ATTACH.get(int(e.dxf.get("attachment_point", 1)), (0.0, 1.0))

            # 锚点偏移：从锚点指向"基线左端"的局部向量
            dx = -ax * w
            dy = -ay * h
            # 随文字一起旋转
            ox = dx * math.cos(rot) - dy * math.sin(rot)
            oy = dx * math.sin(rot) + dy * math.cos(rot)

            blk.add_text(
                _mtext_to_text_content(e),
                height=h,
                rotation=float(e.dxf.get("rotation", 0.0) or 0.0),
                dxfattribs={
                    "insert": (e.dxf.insert.x + ox, e.dxf.insert.y + oy),
                    "layer": e.dxf.layer,
                    "style": e.dxf.get("style", "Standard"),
                },
            )
            blk.delete_entity(e)
            n += 1
    return n


def _prepare_libredwg_copy(dxf_path: str, workdir: str) -> str:
    """给 LibreDWG 造一个它能读的副本（MTEXT→TEXT + 降到 AC1015）。返回副本路径。

    为什么必须降到 AC1015、且**不能**改成 utf-8 编码
    -------------------------------------------------
    R2018(AC1032) 的 DXF 里中文按 UTF-8 原样存；实测 LibreDWG 0.14 读它会把
    中文变成乱码（「斜管沉淀池」→「æ–œç®¡æ²‰æ」）。

    降到 AC1015(R2000) 后，ezdxf 默认用 cp1252 编码写文件，中文写成
    `\\U+659c` 这种 **R2000 标准 unicode 转义** —— LibreDWG 能正确还原，
    AutoCAD 也按规范还原。实测这条路中文 100% 保住。

    ⚠️ 不要"顺手"把这里改成 `encoding="utf-8"`：实测那样写虽然文件里是
    真 UTF-8 中文，但 **LibreDWG 会把中文整段丢掉**，比乱码更糟。

    副作用：该副本若用 ezdxf 渲染，`\\U+xxxx` 会被当成字面量画出来（一片
    "U+6dc0"）。这是 ezdxf 渲染器不解转义所致，**与交付的 DWG 无关** ——
    DWG 里存的是正确中文。副本只是中转文件，不会交付给用户。
    """
    import ezdxf

    doc = ezdxf.readfile(dxf_path)
    _downgrade_mtext(doc)
    doc.dxfversion = "AC1015"
    out = os.path.join(workdir, os.path.basename(dxf_path))
    doc.saveas(out)
    return out


# ---------------------------------------------------------------- 各后端实现
def _convert_libredwg(dxf: str, dwg: str, version: str, workdir: str) -> tuple[bool, str]:
    exe = _probe_one(BACKENDS["libredwg"]["probe"])
    if not exe:
        return False, "未找到 dxf2dwg"

    copy = _prepare_libredwg_copy(dxf, workdir)
    if os.path.exists(dwg):
        os.remove(dwg)
    cmd = [exe, "-y", "--as", version, "-o", dwg, copy]
    p = subprocess.run(cmd, capture_output=True, text=True)
    log = (p.stdout or "") + (p.stderr or "")

    if not os.path.exists(dwg) or os.path.getsize(dwg) == 0:
        return False, f"dxf2dwg 转换失败：{log.strip()[:300]}"
    if "READ ERROR" in log:
        return False, f"dxf2dwg 读入失败（该 DXF 含它不支持的构造）：{log.strip()[:300]}"
    return True, dwg


def _convert_oda(dxf: str, dwg: str, version: str, workdir: str) -> tuple[bool, str]:
    """ODA File Converter 是"整目录"式的：入目录 → 出目录，需同名进出。"""
    exe = _probe_one(BACKENDS["oda"]["probe"])
    if not exe:
        return False, "未找到 ODA File Converter"

    in_dir = os.path.join(workdir, "in")
    out_dir = os.path.join(workdir, "out")
    os.makedirs(in_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)

    base = os.path.basename(dxf)
    shutil.copy2(dxf, os.path.join(in_dir, base))
    # 参数：<in> <out> <版本> <类型> <递归> <审计> [过滤]
    ver = "ACAD" + version.lstrip("rR")
    cmd = [exe, in_dir, out_dir, ver, "DWG", "0", "1", base]
    p = subprocess.run(cmd, capture_output=True, text=True)

    produced = os.path.join(out_dir, os.path.splitext(base)[0] + ".dwg")
    if not os.path.exists(produced):
        log = ((p.stdout or "") + (p.stderr or "")).strip()[:300]
        return False, f"ODA 转换失败（未产出文件）：{log}"
    os.makedirs(os.path.dirname(os.path.abspath(dwg)), exist_ok=True)
    shutil.move(produced, dwg)
    return True, dwg


def _convert_com(dxf: str, dwg: str, version: str, workdir: str) -> tuple[bool, str]:
    from ..engine.multicad_bridge import dxf_to_dwg as _com_convert

    ok, msg = _com_convert(dxf, dwg, version=version)
    return (True, msg) if ok else (False, msg)


_CONVERTERS: dict[str, Callable[..., tuple[bool, str]]] = {
    "libredwg": _convert_libredwg,
    "oda": _convert_oda,
    "com": _convert_com,
}


# ---------------------------------------------------------------- 对外 API
def export_dwg(
    dxf_path: str,
    dwg_path: str | None = None,
    *,
    backend: str = "auto",
    version: str | None = None,
) -> tuple[bool, str]:
    """把单个 DXF 转成 DWG。返回 (是否成功, 产物路径或错误说明)。

    Args:
        dxf_path: 源 DXF
        dwg_path: 目标 DWG（默认同目录同名 .dwg）
        backend: "auto" 或 BACKENDS 里的名字
        version: DWG 版本；不传则用该后端默认值
    """
    dxf_path = os.path.abspath(dxf_path)
    if not os.path.exists(dxf_path):
        return False, f"DXF 不存在：{dxf_path}"

    if backend == "auto":
        picked = detect_backend()
        if not picked:
            return False, backend_help()
        backend = picked

    if backend not in _CONVERTERS:
        return False, f"未知后端 {backend!r}；可用：{', '.join(_PRIORITY)}"
    if not backend_available(backend):
        meta = BACKENDS[backend]
        return False, f"后端 {meta['label']} 不可用。安装：{meta['install']}"

    try:
        ver = normalize_version(version, backend)
    except ValueError as e:
        return False, str(e)

    if dwg_path is None:
        dwg_path = os.path.splitext(dxf_path)[0] + ".dwg"
    dwg_path = os.path.abspath(dwg_path)
    os.makedirs(os.path.dirname(dwg_path), exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="envcad_dwg_") as workdir:
        try:
            return _CONVERTERS[backend](dxf_path, dwg_path, ver, workdir)
        except Exception as e:  # 转换器再糙也不该让上层崩
            return False, f"{type(e).__name__}: {e}"


def export_dir(
    dxf_dir: str,
    out_dir: str | None = None,
    *,
    backend: str = "auto",
    version: str | None = None,
) -> tuple[int, list[tuple[str, bool, str]]]:
    """整目录批量转 DWG。返回 (成功数, [(文件名, 成功与否, 说明)])。"""
    import glob

    files = sorted(glob.glob(os.path.join(dxf_dir, "**", "*.dxf"), recursive=True))
    if out_dir is None:
        out_dir = dxf_dir
    results: list[tuple[str, bool, str]] = []
    ok_n = 0
    for f in files:
        target = os.path.join(out_dir, os.path.basename(os.path.splitext(f)[0] + ".dwg"))
        ok, msg = export_dwg(f, target, backend=backend, version=version)
        if ok:
            ok_n += 1
        results.append((os.path.basename(f), ok, msg))
    return ok_n, results
