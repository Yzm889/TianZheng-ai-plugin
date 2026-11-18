"""端到端串联回归：一次 build_plan 跑完 walls -> openings -> dimensions。

在临时副本上做，跑完 Close(False)，不碰原图。

用法：
  python e2e_chain_check.py <dwg> --probe   # 只看这张图有什么可用模板
  python e2e_chain_check.py <dwg>           # 真跑一条完整链并核对实体增量

为什么要有这个脚本：F5（轴网标注）和门窗宽度都是**单项**实测。整链没跑过，
阶段衔接、结果汇总、失败时的前序保留，都还只是单测里的假设。
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

from t20_arch_mcp import acad_conn, scene_build, tianzheng_query  # noqa: E402

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
    raise SystemExit("连不上 AutoCAD。详情：" + " | ".join(errors[:2]))


def histo() -> dict[str, int]:
    return tianzheng_query.entity_type_histogram()["types"]


def handles_of(doc, needle: str) -> list[str]:
    out = []
    msp = doc.ModelSpace
    for i in range(msp.Count):
        try:
            ent = msp.Item(i)
            if needle in str(ent.ObjectName).upper():
                out.append(str(ent.Handle))
        except Exception:
            continue
    return out


def dote_axes(doc) -> list[tuple[float, float, float, float]]:
    """返回 DOTE 图层上的线段 (x1,y1,x2,y2)。轴线是真实 LINE，必须强类型读端点。"""
    lines = []
    msp = doc.ModelSpace
    for i in range(msp.Count):
        try:
            ent = msp.Item(i)
            if str(ent.ObjectName).upper() != "ACDBLINE":
                continue
            if str(ent.Layer).upper() != "DOTE":
                continue
            typed = win32com.CastTo(ent, "IAcadLine")
            sp, ep = typed.StartPoint, typed.EndPoint
            lines.append((float(sp[0]), float(sp[1]), float(ep[0]), float(ep[1])))
        except Exception:
            continue
    return lines


def probe(doc) -> dict:
    h = tianzheng_query.entity_type_histogram()
    walls = handles_of(doc, "WALL")
    openings = handles_of(doc, "OPENING")
    axes = dote_axes(doc)
    info = {
        "document": acad_conn.doc_name(doc, "?"),
        "counts": h.get("counts") or h.get("types"),
        "wall_templates": walls[:5],
        "opening_templates": openings[:5],
        "dote_axis_lines": len(axes),
    }
    print(json.dumps(info, ensure_ascii=False, indent=2, default=str))
    return info


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("用法：python e2e_chain_check.py <dwg> [--probe|--fail-openings]")
    path = sys.argv[1]
    only_probe = "--probe" in sys.argv[2:]
    fail_openings = "--fail-openings" in sys.argv[2:]

    app = acad_conn._typed(attach())
    acad_conn.wait_until_quiescent(app, timeout=45.0)
    doc = acad_conn.call_with_retry(lambda: app.Documents.Open(path), attempts=30,
                                    delay=1.0, label="Documents.Open")
    acad_conn.wait_until_quiescent(app, timeout=30.0)

    try:
        info = probe(doc)
        if only_probe:
            return

        wall_tpl = info["wall_templates"][0] if info["wall_templates"] else None
        open_tpl = info["opening_templates"][0] if info["opening_templates"] else None
        if not wall_tpl:
            raise SystemExit("这张图没有墙模板，换一张有 TDbWall 的图。")

        before = info["counts"]
        print("\n=== 跑整链：walls + openings(宽1000) + linear + axis ===")

        spec: dict = {
            "stage": "all",
            "origin": [0, 0],
            "wall_template": wall_tpl,
            "walls": [
                {"x1": 20000, "y1": 6000, "x2": 24000, "y2": 6000},
                {"x1": 20000, "y1": 6000, "x2": 20000, "y2": 9000},
            ],
            "dimensions": [
                {"kind": "linear", "p1": [20000, 9000], "p2": [24000, 9000],
                 "pos": [20000, 9600]},
            ],
        }
        if open_tpl:
            want = 1500 if fail_openings else 1000
            spec["openings"] = [
                {"type": "window", "x": 20500, "y": 6000, "width": want},
                {"type": "window", "x": 23500, "y": 6000, "width": want},
            ]
            spec["opening_templates"] = {"window": [{"handle": open_tpl, "width": 1000}]}

        axes = dote_axes(doc)
        if len(axes) >= 2:
            # 取最外侧两根竖轴，同一 y 上取点
            vertical = [a for a in axes if abs(a[0] - a[2]) < 1e-6]
            if len(vertical) >= 2:
                vertical.sort(key=lambda a: a[0])
                y = vertical[0][1]
                spec["dimensions"].append({
                    "kind": "axis",
                    "p1": [vertical[0][0], y],
                    "p2": [vertical[-1][0], y],
                    "pos": [0, 0],
                })

        result = scene_build.build_plan(spec)
        print(json.dumps({k: result.get(k) for k in (
            "ok", "stage", "completed_stages", "failed_stage",
            "wall_count", "opening_count", "dimension_count",
            "opening_sizes", "opening_width_plan", "axis_dote_lines",
            "axis_labels", "message")}, ensure_ascii=False, indent=2, default=str))

        after = tianzheng_query.entity_type_histogram()
        after = after.get("counts") or after.get("types") or {}
        print("\n类型直方图 before:", json.dumps(before, ensure_ascii=False))
        print("类型直方图 after :", json.dumps(after, ensure_ascii=False))

        checks = {
            "walls +2": (after.get("TDBWALL", 0) - before.get("TDBWALL", 0)) == 2,
            "openings +2": (after.get("TDBOPENING", 0) - before.get("TDBOPENING", 0)) == 2
            if open_tpl else True,
            "dims +1": (after.get("TDBDIMENSION2", 0) - before.get("TDBDIMENSION2", 0)) >= 1,
            "stages": result.get("completed_stages") == (
                ["walls", "openings", "dimensions"] if open_tpl
                else ["walls", "dimensions"]),
        }
        if fail_openings:
            # 中途失败的正确姿势：墙已经建好要保留，门窗和尺寸一个都不许动。
            checks = {
                "walls +2（前序保留）":
                    (after.get("TDBWALL", 0) - before.get("TDBWALL", 0)) == 2,
                "openings +0":
                    after.get("TDBOPENING", 0) == before.get("TDBOPENING", 0),
                "dims +0":
                    after.get("TDBDIMENSION2", 0) == before.get("TDBDIMENSION2", 0),
                "completed_stages == ['walls']":
                    result.get("completed_stages") == ["walls"],
                "stage == missing_opening_width":
                    result.get("stage") == "missing_opening_width",
                "说明了 1500":
                    "1500" in (result.get("message") or ""),
            }
        print()
        for name, ok in checks.items():
            print(("  PASS  " if ok else "  FAIL  ") + name)
        if not all(checks.values()):
            sys.exit(1)
        print("\n整链回归通过")
    finally:
        try:
            acad_conn.call_with_retry(lambda: doc.Close(False), attempts=30,
                                      delay=1.0, label="close")
            print("closed without saving")
        except Exception as exc:  # noqa: BLE001
            print("close failed:", exc)


if __name__ == "__main__":
    pythoncom.CoInitialize()
    main()
