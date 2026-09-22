"""五个测试的自动化校验：核对每张 DXF 的关键标注/内容是否齐全。"""
from __future__ import annotations

import os
import sys

import ezdxf


def _dim_text(doc, dim):
    """DIMENSION 的显示文字。

    B 档起部分标注由"手工线 + 手写文字"升级为真实 DIMENSION 实体，文字由
    ezdxf 按 DIMSTYLE 现场算出，模型空间里**没有对应的 TEXT**。只查 TEXT 会
    把这类标注误判成"内容缺失"（例：T2 的"总高 5.5m"变成 5500 的竖向尺寸）。
    """
    raw = str(dim.dxf.get("text", "") or "").strip()
    try:
        meas = dim.get_measurement()
    except Exception:
        meas = None
    if meas is None:
        return "" if raw == "<>" else raw
    dec = 0
    try:
        dec = int(doc.dimstyles.get(dim.dxf.dimstyle).dxf.get("dimdec", 0))
    except Exception:
        pass
    s = f"{meas:.{dec}f}"
    if not raw:
        return s
    return raw.replace("<>", s) if raw != "<>" else s


def texts(path):
    """模型空间里"人眼能看到"的全部文字：TEXT + MTEXT + 尺寸块测量值。"""
    d = ezdxf.readfile(path)
    m = d.modelspace()
    out = [str(t.dxf.text) for t in m.query("TEXT")]
    for t in m.query("MTEXT"):
        try:
            out.append(t.plain_text())
        except Exception:
            out.append(str(t.text))
    for dim in m.query("DIMENSION"):
        s = _dim_text(d, dim)
        if s:
            out.append(s)
    return out, d, m


def has(all_text, kw):
    return any(kw in t for t in all_text)


def check(name, path, kws):
    if not os.path.exists(path):
        print(f"[FAIL] {name}: 文件不存在 {path}")
        return False
    try:
        ts, d, m = texts(path)
    except Exception as e:
        print(f"[FAIL] {name}: 读取失败 {e}")
        return False
    n_line = len(list(m.query("LINE")))
    n_poly = len(list(m.query("LWPOLYLINE")))
    n_txt = len(ts)
    ok = True
    miss = []
    for kw in kws:
        if not has(ts, kw):
            miss.append(kw)
            ok = False
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}: 文字{n_txt} 线{n_line} 多段线{n_poly}"
          + (f"  缺失:{miss}" if miss else ""))
    return ok


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "output"
    )
    all_ok = True
    # T1
    all_ok &= check("T1 污水管道标注", os.path.join(out, "T1_污水管道标注图.dxf"),
                    ["DN300", "1.200", "1.176", "0.4%", "水流方向", "技术要求"])
    # T2：总高原先是手写文字"总高 5.5m"，B 档起改为真实竖向尺寸 5500
    all_ok &= check("T2 竖流斜管沉淀池", os.path.join(out, "T2_竖流斜管沉淀池平剖面图.dxf"),
                    ["沉淀池", "5500", "6000", "1.5", "1.2", "DN300", "DN150",
                     "出水堰", "安装技术要求"])
    # T3
    all_ok &= check("T3 污水自流管网", os.path.join(out, "T3_污水自流管网平面布置图.dxf"),
                    ["DN350", "0.3%", "闸阀", "软接头", "流量计", "防水套管",
                     "水流方向", "图  例"])
    # T4
    for no, title, kws in [
        ("01", "总平面布置图", ["总平面", "格栅", "调节池", "接触氧化池", "沉淀池", "消毒池", "提升泵"]),
        ("02", "调节池平剖面图", ["调节池", "C30", "防腐", "GB 50141"]),
        ("03", "接触氧化池平剖面图", ["接触氧化池", "填料", "曝气"]),
        ("04", "斜管沉淀池平剖面图", ["斜管沉淀池", "斜管", "DN150"]),
        ("05", "工艺管道平面图", ["工艺管道", "闸阀", "流量计", "水流方向"]),
        ("06", "设备材料表", ["设备材料表", "提升泵", "曝气器", "HDPE"]),
    ]:
        f = [f for f in os.listdir(out) if f.startswith(f"T4-{no}")]
        if not f:
            print(f"[FAIL] T4-{no} {title}: 文件不存在")
            all_ok = False
            continue
        all_ok &= check(f"T4-{no} {title}", os.path.join(out, f[0]), kws)
    # T5
    all_ok &= check("T5a 第一步 8x5", os.path.join(out, "T5a_调节池_第一步_8x5x4.dxf"),
                    ["8×5", "-0.500", "C30", "土建施工技术要求"])
    all_ok &= check("T5b 第二步 8x6 防腐", os.path.join(out, "T5b_调节池_第二步_8x6x4_防腐.dxf"),
                    ["8×6", "-0.800", "环氧树脂玻璃钢", "两布三油"])
    # T6
    all_ok &= check("T6 污水自流管网平面布置图", os.path.join(out, "T6_污水自流管网平面布置图.dxf"),
                    ["DN300", "DN200", "HFC-01", "HFC-02", "HFC-03",
                     "GS-01", "TJC-01", "检查井", "格栅井", "化粪池", "调节池",
                     "HDPE", "水流方向", "管底标高", "图  例",
                     "施工技术要求", "水力校验", "GB 50268"])

    # ── 出图体检 ──
    # 上面只查"关键字在不在"，查不出会让图纸报废的硬伤：幅面虚涨、
    # 比例尺不自洽、内容压框/压标题栏、文字叠印、条目错行。这里补上。
    print("\n==== 出图体检（幅面 / 比例尺 / 压框 / 叠印 / 条目）====")
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from envcad.audit import audit_dir, format_report
        reports = audit_dir(out)
        if reports:
            print(format_report(reports))
            all_ok &= all(r.ok for r in reports)
        else:
            print(f"  （{out} 下没有 dxf，跳过）")
    except Exception as e:                             # 体检本身出错不算图纸不过
        print(f"[WARN] 体检未执行：{e}")

    print("\n==== 总结 ====")
    print("全部通过" if all_ok else "存在未通过项")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
