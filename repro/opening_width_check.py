"""门窗宽度区分模板 —— 真实图纸验证（在临时副本上做，不碰原文件）。

验证两条路径：
  A. 声明宽度 1000 + 句柄 4C4(宽1000) -> 真的克隆出一个，且成品的包围盒宽度仍是 1000
  B. 要 1500 宽、但图里没有 1500 宽的模板 -> 一个都不克隆，直接返回"请建 1500 的门窗"

用法：python opening_width_check.py <dwg临时副本路径>
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

from t20_arch_mcp import acad_conn, scene_build  # noqa: E402

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


def opening_stats(doc) -> tuple[int, list[str]]:
    msp = doc.ModelSpace
    handles = []
    for i in range(msp.Count):
        try:
            ent = msp.Item(i)
            if "OPENING" in str(ent.ObjectName).upper():
                handles.append(str(ent.Handle))
        except Exception:
            continue
    return len(handles), handles


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("用法：python opening_width_check.py <dwg路径>")
    path = sys.argv[1]

    app = acad_conn._typed(attach())
    acad_conn.wait_until_quiescent(app, timeout=45.0)
    doc = acad_conn.call_with_retry(lambda: app.Documents.Open(path), attempts=30,
                                    delay=1.0, label="Documents.Open")
    acad_conn.wait_until_quiescent(app, timeout=30.0)
    print("opened:", acad_conn.doc_name(doc, "?"))

    try:
        before, handles = opening_stats(doc)
        print(f"开口前 TDbOpening = {before}  句柄 {handles}")
        if not handles:
            raise SystemExit("这张图没有 TDbOpening 模板，无法验证。")

        template = handles[0]

        print("\n--- A. 声明宽度 1000，用模板", template, "---")
        a = scene_build.build_plan({
            "stage": "openings",
            "opening_templates": {"window": [{"handle": template, "width": 1000}]},
            "openings": [{"type": "window", "x": 5000, "y": 5000, "width": 1000}],
        })
        print(json.dumps(a, ensure_ascii=False, indent=2, default=str))
        mid, handles2 = opening_stats(doc)
        print(f"开口后 TDbOpening = {mid}（增量 {mid - before}），新句柄 {sorted(set(handles2) - set(handles))}")

        print("\n--- B. 要 1500 宽，但模板只有 1000 ---")
        b = scene_build.build_plan({
            "stage": "openings",
            "opening_templates": {"window": [{"handle": template, "width": 1000}]},
            "openings": [{"type": "window", "x": 8000, "y": 5000, "width": 1500}],
        })
        print(json.dumps({k: b.get(k) for k in
                          ("ok", "stage", "missing_widths", "need_user_action", "message")},
                         ensure_ascii=False, indent=2, default=str))
        after, _ = opening_stats(doc)
        print(f"拒绝后 TDbOpening = {after}（应为 {mid}，即一个都没多）")

        ok_a = a.get("opening_count") == 1 and mid == before + 1
        ok_b = b.get("stage") == "missing_opening_width" and after == mid
        print("\n结论：A", "通过" if ok_a else "未通过",
              "· B", "通过" if ok_b else "未通过")
        if not (ok_a and ok_b):
            sys.exit(1)
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
