"""出图体检（audit）与出图预览（preview）的护栏测试。

这两个模块解决同一个问题的两面：**"文件生成成功" ≠ "图纸能交付"**。

* ``verify.py`` 只查"必需标注在不在"，查不出幅面虚涨 / 比例尺不自洽 / 压框 /
  压标题栏 / 文字叠印 —— 这些恰恰是会让客户直接退单的硬伤。
* 多数用户根本打不开 ``.dxf``，所以交付必须带 PNG 预览。

因此这里锁死两件事：
  1. 体检要**能报出**这些硬伤（而不是无脑 pass），且对真实图纸不误报；
  2. 缺 matplotlib 时 `preview` 要给出可理解的错误，而不是崩在栈里。
"""
import os

import ezdxf
import pytest

from envcad.audit import audit_dir, audit_file, format_report, main


# ── 合成图纸工具 ──────────────────────────────────────────────────
def _sheet(msp, paper, scale, *, title_text="1:50", content=True,
           title_box=True, beyond=None):
    """造一张"图框 + 标题栏 (+ 内容)"的最小图纸，尺寸按 1:scale 放大。

    ``beyond``：把一条内容文字放到内框右侧以外多少 mm（纸面量），用来造压框。
    """
    pw, ph = paper
    w, h = pw * scale, ph * scale
    msp.add_lwpolyline([(0, 0), (w, 0), (w, h), (0, h)],
                       close=True, dxfattribs={"layer": "图框"})
    msp.add_text(title_text, height=5 * scale,
                 dxfattribs={"layer": "标题栏"}).set_placement((w * 0.8, h * 0.05))
    if title_box:
        msp.add_lwpolyline([(w * 0.7, 0), (w, 0), (w, h * 0.1), (w * 0.7, h * 0.1)],
                           close=True, dxfattribs={"layer": "标题栏"})
    if content:
        msp.add_text("合计 DN300", height=3 * scale,
                     dxfattribs={"layer": "文字"}).set_placement(
            (w * 0.1, h * 0.5))
    if beyond is not None:
        msp.add_text("越界文字", height=3 * scale,
                     dxfattribs={"layer": "文字"}).set_placement(
            (w - beyond * scale, h * 0.5))
    return w, h


def _write(tmp_path, name, builder):
    doc = ezdxf.new("R2010", setup=True)
    builder(doc.modelspace())
    p = os.path.join(str(tmp_path), name)
    doc.saveas(p)
    return p


# ── 体检：对真实图纸不误报 ────────────────────────────────────────
def test_real_drawings_pass(tmp_path):
    """真实生成的 T1/T2 必须体检通过，且幅面/比例尺与标题栏自洽。"""
    from envcad.drawings import t1_sewage_pipe, t2_settler

    out = str(tmp_path)
    t1_sewage_pipe.gen_t1(out)
    t2_settler.gen_t2(out, scale=50)

    reports = audit_dir(out)
    assert len(reports) == 2
    for r in reports:
        assert r.ok, format_report(reports, verbose=True)
        assert r.size in ("A3", "A4", "A2"), r.size
        assert r.scale_ok
        assert r.overflow_mm == 0
        assert not r.title_clash


def test_real_t2_has_real_dimensions(tmp_path):
    """T2 的"总高"已由手写文字升级为真实 DIMENSION 实体（真尺寸可关联）。"""
    from envcad.drawings import t2_settler

    t2_settler.gen_t2(str(tmp_path), scale=50)
    r = audit_dir(str(tmp_path))[0]
    assert r.dim_count >= 3


# ── 体检：要能报出硬伤 ────────────────────────────────────────────
def test_detects_scale_mismatch(tmp_path):
    """标题栏写 1:50、图框实际是 1:1 —— 必须报"幅面/比例尺不自洽"。"""
    p = _write(tmp_path, "mismatch.dxf",
               lambda m: _sheet(m, (297.0, 210.0), 1.0, title_text="1:50"))
    r = audit_file(p)
    assert not r.ok
    assert r.size is None
    assert any("不自洽" in e for e in r.errors)


def test_detects_content_outside_inner_frame(tmp_path):
    """内容越过内框 —— 必须报压框（纸面 mm > 0）。"""
    # A4 横 1:50，内框右边距 5mm；把文字放到距外框 1mm 处 → 越界约 4mm
    p = _write(tmp_path, "overflow.dxf",
               lambda m: _sheet(m, (297.0, 210.0), 50.0,
                                title_text="1:50", beyond=1.0))
    r = audit_file(p)
    assert r.size == "A4"
    assert r.overflow_mm > 0, format_report([r], verbose=True)
    assert not r.ok


def test_detects_title_block_clash(tmp_path):
    """内容压到标题栏 —— 必须报压标题栏。"""
    def build(m):
        w, h = _sheet(m, (297.0, 210.0), 50.0, title_text="1:50",
                      content=False)
        # 标题栏框在右下角，内容直接压在它上面
        m.add_text("压住标题栏的文字", height=3 * 50,
                   dxfattribs={"layer": "文字"}).set_placement(
            (w * 0.8, h * 0.05))
    p = _write(tmp_path, "clash.dxf", build)
    r = audit_file(p)
    assert r.title_clash
    assert not r.ok


def test_detects_uneven_note_spacing(tmp_path):
    """编号条目行距不均（历史上"说明框错行"事故）—— 必须报出来。"""
    def build(m):
        w, h = _sheet(m, (297.0, 210.0), 50.0, title_text="1:50",
                      content=False)
        m.add_lwpolyline([(w * 0.05, h * 0.05), (w * 0.5, h * 0.05),
                          (w * 0.5, h * 0.6), (w * 0.05, h * 0.6)],
                         close=True, dxfattribs={"layer": "附表"})
        for i, dy in enumerate([0.0, -3 * 50, -3 * 50 - 12 * 50]):   # 第 1↔2 小、2↔3 大
            m.add_text(f"{i + 1}. 第{i + 1}条技术要求",
                       height=3 * 50,
                       dxfattribs={"layer": "文字"}).set_placement(
                (w * 0.07, h * 0.5 + dy))
    p = _write(tmp_path, "notes.dxf", build)
    r = audit_file(p)
    assert "行距" in r.note, r.note


def test_missing_frame_is_error(tmp_path):
    """没有图框层闭合多段线 —— 明确报错，而不是静默通过。"""
    def build(m):
        m.add_text("孤立文字", height=3,
                   dxfattribs={"layer": "文字"}).set_placement((0, 0))
    p = _write(tmp_path, "noframe.dxf", build)
    r = audit_file(p)
    assert not r.ok
    assert any("没有图框" in e for e in r.errors)


def test_broken_file_is_error(tmp_path):
    p = os.path.join(str(tmp_path), "broken.dxf")
    with open(p, "w", encoding="utf-8") as f:
        f.write("这不是 dxf")
    r = audit_file(p)
    assert not r.ok
    assert any("打不开" in e for e in r.errors)


# ── 报告与返回码 ──────────────────────────────────────────────────
def test_empty_dir_returns_code_2(tmp_path):
    assert main([str(tmp_path)]) == 2


def test_main_returns_1_on_bad_sheet(tmp_path):
    _write(tmp_path, "mismatch.dxf",
           lambda m: _sheet(m, (297.0, 210.0), 1.0, title_text="1:50"))
    assert main([str(tmp_path)]) == 1


def test_report_is_human_readable(tmp_path):
    """报告要给人看：有勾叉、有"能不能交付"的结论、有张数。"""
    from envcad.drawings import t1_sewage_pipe

    t1_sewage_pipe.gen_t1(str(tmp_path))
    txt = format_report(audit_dir(str(tmp_path)))
    assert "✓" in txt
    assert "可交付人工审核" in txt
    assert "共 1 张" in txt


def test_fill_ratio_is_reported(tmp_path):
    """图面利用率：幅面"合法"不等于"用得住"，必须量化报出来。

    实测成套图里，设备材料表只占纸宽 29%、流程图只占纸高 11% —— 打印出来
    是一页大白纸角落里有张小图。这是警告不是硬伤（标准幅面本身不一定贴合
    内容长宽比），所以不参与 ok 判定，但报告里必须有数。
    """
    from envcad.drawings import t1_sewage_pipe

    t1_sewage_pipe.gen_t1(str(tmp_path))
    t1_sewage_pipe.gen_t1(str(tmp_path))
    r1 = audit_dir(str(tmp_path))[0]
    assert 0 < r1.fill_pct < 100
    assert r1.ok                      # 利用率低不判死
    assert "占幅" in format_report([r1])

    # 造一张"大纸上只有一行小字"的图：--strict 下必须点出来
    p = _write(tmp_path, "sparse.dxf",
               lambda m: _sheet(m, (297.0, 210.0), 50.0, title_text="1:50"))
    r2 = audit_file(p)
    assert r2.ok, "利用率低是警告，不是硬伤"
    assert r2.fill_pct < 2
    assert "图面只用" in format_report([r2], strict=True)
    assert "图面只用" not in format_report([r2])


# ── 预览 ──────────────────────────────────────────────────────────
mpl = pytest.importorskip("matplotlib", reason="预览是可选依赖（matplotlib）")


def test_preview_renders_png(tmp_path):
    from envcad.drawings import t1_sewage_pipe
    from envcad.preview import frame_extent, render_dxf, render_dir

    out = str(tmp_path / "dwg")
    os.makedirs(out, exist_ok=True)
    t1_sewage_pipe.gen_t1(out)
    dxf = os.path.join(out, "T1_污水管道标注图.dxf")

    msp = ezdxf.readfile(dxf).modelspace()
    ext = frame_extent(msp)
    assert ext is not None and ext[2] > ext[0] and ext[3] > ext[1]

    png = render_dxf(dxf, os.path.join(str(tmp_path), "T1.png"), dpi=80)
    assert os.path.getsize(png) > 2000


def test_preview_dir_and_contact_sheet(tmp_path):
    from envcad.drawings import t1_sewage_pipe, t2_settler
    from envcad.preview import render_dir

    out = str(tmp_path / "dwg")
    os.makedirs(out, exist_ok=True)
    t1_sewage_pipe.gen_t1(out)
    t2_settler.gen_t2(out, scale=50)

    pngs = render_dir(out, os.path.join(str(tmp_path), "prev"),
                      dpi=70, contact=True)
    assert len(pngs) == 2
    assert all(os.path.getsize(p) > 2000 for p in pngs)
    assert os.path.getsize(os.path.join(str(tmp_path), "prev",
                                        "预览-全部.png")) > 2000


def test_preview_empty_dir_raises(tmp_path):
    from envcad.preview import PreviewError, render_dir

    with pytest.raises(PreviewError):
        render_dir(str(tmp_path))
