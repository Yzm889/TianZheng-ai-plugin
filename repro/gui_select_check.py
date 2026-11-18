"""GUI 点选实测：真实鼠标点击 + 实体验证（在临时副本上做）。

A. click_targets：点 2 个房间，回读 PickfirstSelectionSet，确认点准了
B. run_select_command：半径标注 TDimRad —— 先选圆、再点尺寸线位置，核对尺寸增量

（以前试过的 文字加框 TTextAddFrame 其实是**对话框**类命令，不是纯选择类，
  点了会把 CAD 卡在模态对话框里，所以换成 TDimRad。）

用法：python gui_select_check.py <dwg副本>
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "python"))
os.environ.setdefault("T20_ALLOW_FOREIGN_DOC", "true")
os.environ["T20_HOST_GUI"] = "1"

import pythoncom  # noqa: E402
import win32com.client as win32com  # noqa: E402

from t20_arch_mcp import acad_conn, gui_select, native_tools, tianzheng_query  # noqa: E402

PROGIDS = ("AutoCAD.Application", "AutoCAD.Application.25.1",
           "AutoCAD.Application.25.0", "AutoCAD.Application.24.1",
           "AutoCAD.Application.24.0")


def handles_of(doc, needle: str) -> list[str]:
    """按类名收集句柄。晚绑定读 ModelSpace.Item 会偶发被拒，必须多重试。"""
    out: list[str] = []
    msp = doc.ModelSpace
    for _ in range(2):
        out = []
        for i in range(int(msp.Count)):
            try:
                ent = acad_conn.call_with_retry(lambda idx=i: msp.Item(idx),
                                                attempts=6, delay=0.3)
                name = str(acad_conn.call_with_retry(lambda e=ent: e.ObjectName,
                                                     attempts=6, delay=0.3)).upper()
                if needle in name:
                    out.append(str(acad_conn.call_with_retry(lambda e=ent: e.Handle,
                                                             attempts=6, delay=0.3)))
            except Exception:
                continue
        if out:
            break
    return out


def histo() -> dict:
    return tianzheng_query.entity_type_histogram()["types"]


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("用法：python gui_select_check.py <dwg>")
    src = sys.argv[1]

    app = None
    for progid in PROGIDS:
        try:
            app = acad_conn._typed(win32com.GetActiveObject(progid))
            break
        except Exception:  # noqa: BLE001
            continue
    if app is None:
        raise SystemExit("连不上 AutoCAD")

    acad_conn.wait_until_quiescent(app, timeout=45.0)
    doc = acad_conn.call_with_retry(lambda: app.Documents.Open(src), attempts=30,
                                    delay=1.0, label="open")
    acad_conn.wait_until_quiescent(app, timeout=30.0)

    try:
        print("doc:", acad_conn.doc_name(doc, "?"))
        spaces = handles_of(doc, "TDBSPACE")
        print("spaces:", spaces)
        if len(spaces) < 2:
            raise SystemExit("这张图没有足够的房间，换一张。")

        acad_conn.call_with_retry(app.ZoomExtents, attempts=8, delay=1.0,
                                  label="ZoomExtents")
        acad_conn.wait_until_quiescent(app, timeout=20.0)

        print("\n--- A. 点 2 个房间，看选中了没有 ---")
        a = gui_select.click_targets(spaces[:2])
        sel = a.get("selection") or {}
        print(json.dumps({"ok": a.get("ok"), "selection": sel}, ensure_ascii=False,
                         indent=2, default=str))
        wanted = {h.upper() for h in spaces[:2]}
        hit = [h for h in (sel.get("handles") or []) if h.upper() in wanted]
        ok_a = bool(a.get("ok")) and len(hit) == 2 and int(sel.get("count") or 0) >= 2
        print(f"A 通过={ok_a} · 命中 {hit}")

        print("\n--- B. 半径标注 TDimRad（选择类命令）---")
        before = histo()
        circle = native_tools.draw_circle([3200, 3200], 900)
        print("画的圆：", json.dumps(circle, ensure_ascii=False, default=str))
        acad_conn.call_with_retry(app.ZoomExtents, attempts=8, delay=1.0,
                                  label="ZoomExtents2")
        acad_conn.wait_until_quiescent(app, timeout=20.0)

        b = gui_select.run_select_command("TDimRad", [circle["handle"]],
                                          label_points=[[4100, 4050]],
                                          expect_delta=1)
        print(json.dumps({k: b.get(k) for k in
                          ("ok", "command", "clicked", "label_clicks", "before",
                           "after", "total_delta", "tail_after", "message")},
                         ensure_ascii=False, indent=2, default=str))
        after = histo()
        print("类型直方图 before:", json.dumps(before, ensure_ascii=False))
        print("类型直方图 after :", json.dumps(after, ensure_ascii=False))
        ok_b = bool(b.get("ok")) and int(b.get("total_delta") or 0) >= 1

        print("\n结论：A", "通过" if ok_a else "未通过",
              "· B", "通过" if ok_b else "未通过")
        if not (ok_a and ok_b):
            sys.exit(1)
    finally:
        try:
            acad_conn.call_with_retry(lambda: doc.Close(False), attempts=40,
                                      delay=1.0, label="close")
            print("closed without saving")
        except Exception as exc:  # noqa: BLE001
            print("close failed:", exc)
            print("（若有对话框卡住，先手动按 ESC 或用 cleanup 脚本清对话框）")


if __name__ == "__main__":
    pythoncom.CoInitialize()
    main()
