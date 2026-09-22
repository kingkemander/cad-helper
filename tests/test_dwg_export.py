"""`envcad dwg` 护栏测试：转换器注册表、版本归一、MTEXT 降级、导出失败路径。

设计原则：**不依赖某个特定转换器**。机器上没装转换器时，涉及真实转换的用例
自动 skip，而注册表/降级/报错这些纯逻辑仍然必须通过 —— 它们才是换 CAD 时
最容易坏的地方。
"""

from __future__ import annotations

import math

import ezdxf
import pytest
from ezdxf.tools.text_size import text_size

from envcad.engine import dwg_export as D


# ------------------------------------------------------------------ 注册表
def test_every_backend_has_required_meta():
    for name, meta in D.BACKENDS.items():
        for key in ("probe", "label", "versions", "default", "keeps_dimension",
                    "install"):
            assert key in meta, f"{name} 缺 {key}"
        assert meta["versions"], f"{name} 没有支持的版本"
        assert meta["default"] in meta["versions"], (
            f"{name} 默认版本 {meta['default']} 不在支持列表里")


def test_default_backend_is_a_supported_version():
    """默认版本写错会让 export_dwg 一上手就抛 ValueError。"""
    for name in D.BACKENDS:
        assert D.normalize_version(None, name) == D.BACKENDS[name]["default"]


def test_priority_covers_all_backends():
    """加了新后端却忘了进 _PRIORITY，auto 就永远挑不到它。"""
    assert set(D._PRIORITY) == set(D.BACKENDS)


def test_available_backends_returns_subset_of_priority():
    got = D.available_backends()
    assert isinstance(got, list)
    assert set(got) <= set(D._PRIORITY)
    # 顺序必须与优先级一致（保真度高的在前）
    assert got == [n for n in D._PRIORITY if n in got]


def test_detect_backend_is_first_available():
    avail = D.available_backends()
    assert D.detect_backend() == (avail[0] if avail else None)


def test_keeps_dimension_flag_only_for_real_cad_backends():
    """只有真正原生 CAD 才保得住 DIMENSION 智能对象；LibreDWG 保不住。"""
    assert D.BACKENDS["oda"]["keeps_dimension"] is True
    assert D.BACKENDS["com"]["keeps_dimension"] is True
    assert D.BACKENDS["libredwg"]["keeps_dimension"] is False


def test_backend_help_mentions_every_backend():
    txt = D.backend_help()
    for name in D.BACKENDS:
        assert D.BACKENDS[name]["label"] in txt


# ------------------------------------------------------------------ 版本归一
@pytest.mark.parametrize("raw,expected", [
    ("2000", "r2000"), ("r2000", "r2000"), ("R2000", "r2000"),
    ("2004", "r2004"), ("r14", "r14"), ("14", "r14"), ("r12", "r12"),
])
def test_normalize_version_libredwg(raw, expected):
    assert D.normalize_version(raw, "libredwg") == expected


@pytest.mark.parametrize("raw,expected", [
    ("2018", "2018"), ("r2018", "2018"), ("2013", "2013"),
])
def test_normalize_version_oda(raw, expected):
    assert D.normalize_version(raw, "oda") == expected


def test_normalize_version_rejects_unsupported():
    """LibreDWG 只到 r2004；写 2018 必须明确报错，不能默默降级。"""
    with pytest.raises(ValueError, match="不支持"):
        D.normalize_version("2018", "libredwg")


def test_normalize_version_blank_falls_back_to_default():
    assert D.normalize_version("", "libredwg") == "r2000"
    assert D.normalize_version(None, "oda") == "2018"


# ------------------------------------------- 失败路径（不依赖任何转换器）
def test_export_missing_file_reports_clearly(tmp_path):
    ok, msg = D.export_dwg(str(tmp_path / "不存在.dxf"))
    assert not ok and "不存在" in msg


def test_export_unknown_backend_reports_clearly(tmp_path):
    f = tmp_path / "a.dxf"
    f.write_text("0\nEOF\n")
    ok, msg = D.export_dwg(str(f), backend="不存在的后端")
    assert not ok and "未知后端" in msg


def test_export_unavailable_backend_gives_install_hint(tmp_path):
    """com 后端在非 Windows 上必须给出安装指引，而不是抛异常。"""
    f = tmp_path / "a.dxf"
    f.write_text("0\nEOF\n")
    ok, msg = D.export_dwg(str(f), backend="com")
    if not D.backend_available("com"):
        assert not ok
        assert D.BACKENDS["com"]["label"] in msg


def test_export_bad_version_is_reported_not_raised(tmp_path):
    """版本写错要走返回值，不能把 ValueError 抛给上层（deliver 会崩）。"""
    doc = ezdxf.new("AC1015")
    doc.modelspace().add_line((0, 0), (10, 0))
    f = tmp_path / "v.dxf"
    doc.saveas(f)
    ok, msg = D.export_dwg(str(f), backend="libredwg", version="2018")
    assert not ok and "不支持" in msg


def test_export_dir_returns_per_file_status(tmp_path):
    n, results = D.export_dir(str(tmp_path), str(tmp_path / "out"),
                              backend="libredwg")
    assert n == 0 and results == []


# --------------------------------------------------------------- MTEXT 降级
def _doc_with_mtext(rotation: float, attachment: int = 5, text: str = "6000"):
    doc = ezdxf.new("AC1015")
    blk = doc.blocks.new("*D1")
    blk.add_mtext(text, dxfattribs={
        "char_height": 3.5, "rotation": rotation,
        "attachment_point": attachment, "insert": (100.0, 50.0), "style": "Standard",
    })
    return doc


@pytest.mark.parametrize("rotation", [0.0, 90.0, 315.0])
def test_downgrade_removes_all_mtext(rotation):
    """核心保证：降级后文档里不能再有 MTEXT —— LibreDWG 就是死在这上面。"""
    doc = _doc_with_mtext(rotation)
    n = D._downgrade_mtext(doc)
    assert n == 1
    left = [e for b in doc.blocks for e in b if e.dxftype() == "MTEXT"]
    assert left == []


@pytest.mark.parametrize("rotation", [0.0, 90.0, 315.0])
def test_downgrade_preserves_text_content_and_height(rotation):
    """直径符号必须留成 %%c：老 CAD 的字体未必有 Ø 的字形。"""
    doc = _doc_with_mtext(rotation, text="%%c6000")
    D._downgrade_mtext(doc)
    txts = [e for b in doc.blocks for e in b if e.dxftype() == "TEXT"]
    assert len(txts) == 1
    assert txts[0].dxf.text == "%%c6000"
    assert txts[0].dxf.height == pytest.approx(3.5)
    assert txts[0].dxf.rotation == pytest.approx(rotation)


def test_mtext_content_restores_dxf_control_codes():
    """Ø/°/± 要还原成 %%c/%%d/%%p（字体无关），其余格式码正常解析。"""
    doc = ezdxf.new("AC1015")
    blk = doc.blocks.new("*D2")
    blk.add_mtext("Ø100 ±0.5 90°", dxfattribs={"char_height": 3.5, "insert": (0, 0)})
    mt = [e for b in doc.blocks for e in b if e.dxftype() == "MTEXT"][0]
    got = D._mtext_to_text_content(mt)
    assert "%%c100" in got and "%%p0.5" in got and "%%d" in got
    assert "Ø" not in got and "±" not in got


@pytest.mark.parametrize("rotation", [0.0, 90.0, 315.0])
def test_downgrade_keeps_center_within_tolerance(rotation):
    """MTEXT 是"锚点"定位，TEXT 是"基线左端"定位；折算错了尺寸文字会飘走。

    容差取 5% 字高 —— 尺寸文字偏出这个量级，图纸上就能眼看出来。
    """
    doc = _doc_with_mtext(rotation, attachment=5)
    mtext = [e for b in doc.blocks for e in b if e.dxftype() == "MTEXT"][0]
    target = (mtext.dxf.insert.x, mtext.dxf.insert.y)

    D._downgrade_mtext(doc)
    t = [e for b in doc.blocks for e in b if e.dxftype() == "TEXT"][0]

    rad = math.radians(rotation)
    ts = text_size(t)
    ins = (t.dxf.insert.x, t.dxf.insert.y)
    center = (
        ins[0] + (ts.width / 2) * math.cos(rad) - (ts.cap_height / 2) * math.sin(rad),
        ins[1] + (ts.width / 2) * math.sin(rad) + (ts.cap_height / 2) * math.cos(rad),
    )
    err = math.dist(center, target)
    assert err <= 0.05 * 3.5, f"旋转 {rotation}° 时中心偏移 {err:.4f}（超 5% 字高）"


@pytest.mark.parametrize("attachment", [1, 2, 3, 4, 5, 6, 7, 8, 9])
def test_downgrade_handles_all_attachments(attachment):
    """9 种 attachment 都要能折算，不能只有中间那个能用。"""
    doc = _doc_with_mtext(0.0, attachment=attachment)
    assert D._downgrade_mtext(doc) == 1
    assert [e for b in doc.blocks for e in b if e.dxftype() == "TEXT"]


def test_downgrade_multiline_mtext_collapses_content():
    """多行 MTEXT 降级成单行 TEXT 会丢换行 —— 记下这个已知损失。"""
    doc = _doc_with_mtext(0.0, text="第一行\\P第二行")
    D._downgrade_mtext(doc)
    t = [e for b in doc.blocks for e in b if e.dxftype() == "TEXT"][0]
    assert "第一行" in t.dxf.text and "第二行" in t.dxf.text


def test_prepare_copy_keeps_source_untouched(tmp_path):
    """降级只作用于临时副本；交付用的 DXF 必须原样不动。"""
    doc = _doc_with_mtext(90.0)
    src = tmp_path / "src.dxf"
    doc.saveas(src)
    before = src.read_bytes()

    work = tmp_path / "work"
    work.mkdir()
    D._prepare_libredwg_copy(str(src), str(work))

    assert src.read_bytes() == before, "源 DXF 被改动了"
    copy = work / "src.dxf"
    assert copy.exists()
    d2 = ezdxf.readfile(copy)
    assert not [e for b in d2.blocks for e in b if e.dxftype() == "MTEXT"]


def test_prepare_copy_downgrades_to_ac1015(tmp_path):
    """回归：副本必须是 AC1015。

    保留 AC1032(R2018) 时，LibreDWG 会把中文读成乱码
    （「斜管沉淀池」→「æ–œç®¡æ²‰æ」），因为 R2018 的 DXF 文本是 UTF-8 原样存的。
    """
    doc = ezdxf.new("AC1032")
    doc.modelspace().add_text("斜管沉淀池", dxfattribs={"height": 3.5})
    src = tmp_path / "cn.dxf"
    doc.saveas(src)

    work = tmp_path / "work"
    work.mkdir()
    copy = D._prepare_libredwg_copy(str(src), str(work))

    d2 = ezdxf.readfile(copy)
    assert d2.dxfversion == "AC1015", f"副本版本是 {d2.dxfversion}，中文会乱码"


def test_prepare_copy_preserves_chinese_content(tmp_path):
    """降级不能把中文弄丢。

    注意 AC1015 里中文以 `\\U+659c` 这种标准 R2000 转义写出（不是原样 UTF-8）——
    这正是 LibreDWG 能正确识别的原因；AutoCAD 也会把它还原成「斜」。
    ezdxf 自己读回来不解转义，所以这里断言的是"没丢失"，而不是"字面相等"。
    """
    doc = ezdxf.new("AC1032")
    doc.modelspace().add_text("斜管沉淀池", dxfattribs={"height": 3.5})
    src = tmp_path / "cn2.dxf"
    doc.saveas(src)
    work = tmp_path / "w2"
    work.mkdir()
    copy = D._prepare_libredwg_copy(str(src), str(work))
    d2 = ezdxf.readfile(copy)
    texts = [e.dxf.text for e in d2.modelspace() if e.dxftype() == "TEXT"]
    assert len(texts) == 1
    got = texts[0]
    # 「斜管沉淀池」= U+659C U+7BA1 U+6C89 U+6DC0 U+6C60
    assert got == "\\U+659c\\U+7ba1\\U+6c89\\U+6dc0\\U+6c60", (
        f"中文编码形式变了：{got!r}")


# --------------------------------------------------- 真实转换（有转换器才跑）
needs_backend = pytest.mark.skipif(
    not D.available_backends(),
    reason="本机没有任何 DXF→DWG 转换器")


@needs_backend
def test_real_export_produces_nonempty_dwg(tmp_path):
    doc = ezdxf.new("AC1015")
    m = doc.modelspace()
    m.add_text("测试", dxfattribs={"height": 3.5, "rotation": 90})
    blk = doc.blocks.new("*D1")
    blk.add_mtext("6000", dxfattribs={
        "char_height": 3.5, "rotation": 90, "attachment_point": 5,
        "insert": (10.0, 10.0), "style": "Standard"})
    src = tmp_path / "t.dxf"
    doc.saveas(src)

    out = tmp_path / "t.dwg"
    ok, msg = D.export_dwg(str(src), str(out), backend=D.detect_backend())
    assert ok, msg
    assert out.exists() and out.stat().st_size > 0


@needs_backend
def test_real_export_auto_picks_a_backend(tmp_path):
    doc = ezdxf.new("AC1015")
    doc.modelspace().add_line((0, 0), (10, 0))
    src = tmp_path / "a.dxf"
    doc.saveas(src)
    ok, msg = D.export_dwg(str(src), backend="auto")
    assert ok, msg


@needs_backend
def test_real_export_dir_counts_successes(tmp_path):
    doc = ezdxf.new("AC1015")
    doc.modelspace().add_line((0, 0), (10, 0))
    for name in ("a.dxf", "b.dxf"):
        doc.saveas(tmp_path / name)
    n, results = D.export_dir(str(tmp_path), str(tmp_path / "out"))
    assert n == 2 and len(results) == 2


@needs_backend
def test_real_export_survives_rotated_mtext(tmp_path):
    """回归：带旋转的 MTEXT 曾让 LibreDWG 直接 READ ERROR 拒掉整个文件。"""
    backend = D.detect_backend()
    if D.BACKENDS[backend]["keeps_dimension"]:
        pytest.skip("该后端不受 MTEXT 旋转问题影响")
    doc = ezdxf.new("AC1015")
    blk = doc.blocks.new("*D1")
    blk.add_mtext("%%c6000", dxfattribs={
        "char_height": 3.5, "rotation": 315.0, "attachment_point": 5,
        "insert": (100.0, 50.0), "style": "Standard"})
    src = tmp_path / "rot.dxf"
    doc.saveas(src)
    ok, msg = D.export_dwg(str(src), backend=backend)
    assert ok, f"旋转 MTEXT 又把它卡住了：{msg}"


requires_dwgread = pytest.mark.skipif(
    not __import__("shutil").which("dwgread"),
    reason="没有 dwgread，无法回读 DWG 校验")


@requires_dwgread
@needs_backend
def test_real_export_keeps_chinese_readable(tmp_path):
    """端到端回归：中文在 DWG 里不能变成乱码。

    这是最容易悄悄坏的一环 —— 转换"成功"了、文件也生成了，但打开全是
    「æ–œç®¡æ²‰æ」这种乱码，光看退出码根本发现不了。
    """
    import subprocess as _sp

    doc = ezdxf.new("AC1032")
    doc.modelspace().add_text("斜管沉淀池", dxfattribs={"height": 3.5})
    src = tmp_path / "zh.dxf"
    doc.saveas(src)

    out = tmp_path / "zh.dwg"
    ok, msg = D.export_dwg(str(src), str(out), backend=D.detect_backend())
    assert ok, msg

    dump = _sp.run(["dwgread", "-O", "JSON", str(out)],
                   capture_output=True).stdout
    assert b"\xe6\x96\x9c" in dump, "DWG 里找不到『斜』的 UTF-8 字节"
    assert "斜管沉淀池" in dump.decode("utf-8", "replace")
