"""F5 实测：build_plan 内部的轴网标注，看 TDbAxisLabelSet 真的有没有 +1。

用法：
    1) 手动打开 AutoCAD + 天正（T20/T30），打开一张**已经有轴网**的图
    2) python repro/f5_axis_check.py

它自己从 DOTE 图层的 LINE 里取最外侧两根竖轴的真实坐标（不盲给点），
再用 build_plan 的 kind="axis" 标一次，前后对比实体类型直方图。

给用户图纸开权限：脚本内临时设 T20_ALLOW_FOREIGN_DOC=true，
只影响本进程，不会把用户的图纸永久登记成"服务器所有"。
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "python"))
os.environ.setdefault("T20_ALLOW_FOREIGN_DOC", "true")

import pythoncom  # noqa: E402
import win32com.client as win32com  # noqa: E402
from win32com.client import CastTo  # noqa: E402

from t20_arch_mcp import scene_build, tianzheng_query  # noqa: E402

PROGIDS = ("AutoCAD.Application", "AutoCAD.Application.25.1",
           "AutoCAD.Application.25.0", "AutoCAD.Application.24.1",
           "AutoCAD.Application.24.0")


def attach():
    errors = []
    for progid in PROGIDS:
        try:
            return win32com.GetActiveObject(progid)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{progid}: {exc}")
    raise SystemExit(
        "连不上 AutoCAD（先手动打开 AutoCAD + 天正）。详情：" + " | ".join(errors[:2]))


def read_dote_lines(doc) -> list[dict]:
    """读 DOTE 图层的 LINE 端点。必须用强类型 IAcadLine，晚绑定读不到 StartPoint。"""
    msp = doc.ModelSpace
    out = []
    for i in range(msp.Count):
        ent = msp.Item(i)
        try:
            if str(ent.Layer).upper() != "DOTE" or str(ent.ObjectName) != "AcDbLine":
                continue
            line = CastTo(ent, "IAcadLine")
            start, end = tuple(line.StartPoint), tuple(line.EndPoint)
        except Exception:
            continue
        out.append({"handle": str(ent.Handle), "start": start, "end": end})
    return out


def pick_axis_points(lines: list[dict]) -> tuple[str, str, str]:
    """取最外侧两根竖轴，在两条线的公共 y 区间内取中点，返回两个 'x,y' 字符串。"""
    verticals = [ln for ln in lines if abs(ln["start"][0] - ln["end"][0]) < 1e-6]
    if len(verticals) < 2:
        raise SystemExit(f"竖轴不足两根（只有 {len(verticals)} 根），没法标轴网")
    verticals.sort(key=lambda ln: ln["start"][0])
    first, last = verticals[0], verticals[-1]
    y_lo = max(min(first["start"][1], first["end"][1]),
               min(last["start"][1], last["end"][1]))
    y_hi = min(max(first["start"][1], first["end"][1]),
               max(last["start"][1], last["end"][1]))
    if y_hi <= y_lo:
        raise SystemExit("两根竖轴没有公共长度，无法取到同一条线上的点")
    y = (y_lo + y_hi) / 2.0
    note = (f"竖轴 {first['start'][0]:.2f} → {last['start'][0]:.2f}，取 y={y:.2f}")
    return f"{first['start'][0]!r},{y!r}", f"{last['start'][0]!r},{y!r}", note


def axis_label_count() -> tuple[int, dict]:
    """数轴号集，并带上实际类名直方图。

    MEASURED：轴号集的类名是 TDbAxisLabelSet，直方图会把它归一成
    TDBAXISLABELSET —— 直接去直方图里找 "TCH_AXIS_LABEL" 会永远得到 0，
    所以计数必须走 count_entities 的别名匹配（TCH_AXIS_LABEL ↔ TDbAxisLabelSet）。
    """
    counted = tianzheng_query.count_entities("TCH_AXIS_LABEL")
    hist = tianzheng_query.entity_type_histogram()["types"]
    return int(counted["count"]), hist


def main() -> None:
    app = attach()
    doc = app.ActiveDocument
    print(f"document : {doc.Name}")

    lines = read_dote_lines(doc)
    print(f"DOTE lines: {len(lines)}")
    if not lines:
        raise SystemExit("这张图没有 DOTE 轴线。先用「绘制轴网 HZZW」建一个再跑。")

    p1, p2, note = pick_axis_points(lines)
    print(f"取点     : {p1}  →  {p2}   ({note})")

    before, hist_before = axis_label_count()
    print(f"标注前   : TCH_AXIS_LABEL ≈ {before}   直方图 {hist_before}")

    if "--inspect" in sys.argv:
        print("--inspect：只看几何和现状，不发送任何命令。")
        return

    result = scene_build.build_plan({
        "stage": "dimensions",
        "dimensions": [{"kind": "axis", "p1": [float(p1.split(",")[0]),
                                               float(p1.split(",")[1])],
                        "p2": [float(p2.split(",")[0]),
                               float(p2.split(",")[1])]}],
    }, timeout=180.0)

    after, hist_after = axis_label_count()
    print("build_plan 返回：")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print(f"标注后   : TCH_AXIS_LABEL ≈ {before} → {after}  (delta {after - before})")
    print(f"           直方图 {hist_after}")

    if after > before:
        print("结论：通过 —— 轴号集真的多出来了。")
        return
    print("结论：未通过 —— 没有实体增量。看上面的 axis_labels / dimension_errors。")
    sys.exit(1)


if __name__ == "__main__":
    pythoncom.CoInitialize()
    main()
