"""GUI 档逐命令验收：提示驱动跑一遍所有"要鼠标"的天正命令，当场核对实体增量。

用法：
  python gui_drive_check.py <dwg副本>              # 全跑
  python gui_drive_check.py <dwg副本> --only=TDimRad,TCountArea
  python gui_drive_check.py <dwg副本> --demo       # 先补几条演示实体（圆/文字/填充/车位）

每条命令都会打印：命中的提示、每一步做了什么、实体增量。失败就停在原地，
把真实提示原样打出来，方便改规则而不是瞎猜。
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "python"))
os.environ.setdefault("T20_ALLOW_FOREIGN_DOC", "true")
os.environ["T20_HOST_GUI"] = "1"

import pythoncom  # noqa: E402
import win32com.client as win32com  # noqa: E402

from t20_arch_mcp import (acad_conn, gui_drive, gui_select, native_tools,  # noqa: E402
                          tianzheng_query, winmsg)

PROGIDS = ("AutoCAD.Application", "AutoCAD.Application.25.1",
           "AutoCAD.Application.25.0", "AutoCAD.Application.24.1",
           "AutoCAD.Application.24.0")


def attach():
    errors = []
    for progid in PROGIDS:
        try:
            return acad_conn._typed(win32com.GetActiveObject(progid))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{progid}: {exc}")
    raise SystemExit("连不上 AutoCAD。详情：" + " | ".join(errors[:2]))


def handles_of(doc, needle: str) -> list[str]:
    out: list[str] = []
    msp = doc.ModelSpace
    for _ in range(2):
        out = []
        for i in range(int(msp.Count)):
            try:
                ent = acad_conn.call_with_retry(lambda idx=i: msp.Item(idx),
                                                attempts=5, delay=0.3)
                name = str(acad_conn.call_with_retry(lambda e=ent: e.ObjectName,
                                                     attempts=5, delay=0.3)).upper()
                if needle in name:
                    out.append(str(acad_conn.call_with_retry(lambda e=ent: e.Handle,
                                                             attempts=5, delay=0.3)))
            except Exception:
                continue
        if out:
            break
    return out


def dote_axes(doc) -> list[dict]:
    out: list[dict] = []
    msp = doc.ModelSpace
    for i in range(int(msp.Count)):
        try:
            ent = acad_conn.call_with_retry(lambda idx=i: msp.Item(idx), attempts=5)
            if str(ent.ObjectName).upper() != "ACDBLINE":
                continue
            if str(ent.Layer).upper() != "DOTE":
                continue
            typed = win32com.CastTo(ent, "IAcadLine")
            sp, ep = typed.StartPoint, typed.EndPoint
            out.append({"handle": str(ent.Handle),
                        "x1": float(sp[0]), "y1": float(sp[1]),
                        "x2": float(ep[0]), "y2": float(ep[1])})
        except Exception:
            continue
    return out


def histo() -> dict:
    return tianzheng_query.entity_type_histogram()["types"]


def show(tag: str, result: dict) -> None:
    print(f"\n=== {tag} · {result.get('command')} ===")
    print(json.dumps({k: result.get(k) for k in
                      ("ok", "status", "total_delta", "typed_delta", "before", "after",
                       "transcript", "message")},
                     ensure_ascii=False, indent=2, default=str))
    for step in result.get("steps") or []:
        print("   ", json.dumps(step, ensure_ascii=False, default=str))
    if not result.get("ok"):
        print("    tail:", (result.get("tail_after") or "")[-200:])


def _retry(fn, attempts: int = 8, delay: float = 0.8):
    last = None
    for _ in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(delay)
    raise last


def ensure_demo(app, doc) -> dict:
    """在空白处补几条演示实体：圆、文字、闭合矩形（填充）。"""
    time.sleep(2.0)
    made: dict = {}
    made["circle"] = _retry(lambda: native_tools.draw_circle([12000, 5200], 900))
    made["text"] = _retry(lambda: native_tools.add_text([12000, 4200], "样式图 GUI 验收",
                                                        height=350))
    rect = [[12000 - 1500, 3000], [12000 + 1500, 3000],
            [12000 + 1500, 1500], [12000 - 1500, 1500]]
    made["hatch_boundary"] = native_tools.draw_closed_polyline(rect)

    msp = doc.ModelSpace
    try:
        hatch = acad_conn.call_with_retry(
            lambda: msp.AddHatch(0, "ANSI31", True), attempts=6, delay=0.6,
            label="AddHatch")
        outer = [acad_conn.call_with_retry(
            lambda i=i: msp.Item(i), attempts=6) for i in range(int(msp.Count))]
        boundary = [e for e in outer if str(e.Handle) == str(made["hatch_boundary"]["handle"])]
        acad_conn.call_with_retry(
            lambda: hatch.AppendOuterLoop(boundary), attempts=6, delay=0.6,
            label="AppendOuterLoop")
        acad_conn.call_with_retry(lambda: hatch.Evaluate(), attempts=6, delay=0.6,
                                  label="Evaluate")
        made["hatch"] = {"ok": True, "handle": str(hatch.Handle)}
    except Exception as exc:  # noqa: BLE001
        made["hatch"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    acad_conn.call_with_retry(app.ZoomExtents, attempts=8, delay=1.0, label="ZoomExtents")
    acad_conn.wait_until_quiescent(app, timeout=20.0)
    return made


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("用法：python gui_drive_check.py <dwg副本> [--only=A,B] [--demo]")
    path = sys.argv[1]
    only = ""
    for arg in sys.argv[2:]:
        if arg.startswith("--only="):
            only = arg.split("=", 1)[1]
    wanted = [c.strip() for c in only.split(",") if c.strip()]

    app = attach()
    acad_conn.wait_until_quiescent(app, timeout=45.0)
    doc = acad_conn.call_with_retry(lambda: app.Documents.Open(path), attempts=30,
                                    delay=1.0, label="open")
    acad_conn.wait_until_quiescent(app, timeout=30.0)

    results: dict[str, dict] = {}
    try:
        print("doc:", acad_conn.doc_name(doc, "?"))
        demo = {}
        if "--demo" in sys.argv[2:]:
            demo = ensure_demo(app, doc)
            print("demo:", json.dumps(demo, ensure_ascii=False, default=str))

        spaces = handles_of(doc, "TDBSPACE")
        walls = handles_of(doc, "TDBWALL")
        texts = handles_of(doc, "TEXT")
        circles = handles_of(doc, "CIRCLE")
        hatches = handles_of(doc, "HATCH")
        axes = dote_axes(doc)
        print(f"spaces={len(spaces)} walls={len(walls)} texts={len(texts)} "
              f"circles={len(circles)} hatches={len(hatches)} axes={len(axes)}")

        recipes: dict[str, dict] = {}

        if spaces:
            center = [1000, 700]
            recipes["TCountArea"] = dict(command="TCountArea", handles=spaces,
                                         points=[center], select_mode="window",
                                         zoom_pad=0.45,
                                         verify_type="", expect_delta=1)
            recipes["TSortSpace"] = dict(command="TSortSpace", handles=spaces,
                                         select_mode="window", zoom_pad=0.45,
                                         verify_type="", expect_delta=0)
        if circles:
            recipes["TDimDia"] = dict(command="TDimDia", handles=circles[:1],
                                      points=[[13200, 6100]], verify_type="", expect_delta=1)
        if hatches:
            recipes["THatchArea"] = dict(command="THatchArea", handles=hatches[:1],
                                         points=[[12000, 2000]], verify_type="", expect_delta=0)
        if texts:
            recipes["TTextAddFrame"] = dict(command="TTextAddFrame", handles=texts[:1],
                                            verify_type="", expect_delta=1)
        if walls:
            wall = walls[0]
            base = _wall_centre(app, wall)
            recipes["TPartwall"] = dict(command="TPartwall", handles=[wall],
                                        points=[base], verify_type="", expect_delta=1)
        if axes:
            recipes["TSingleAxisDim"] = dict(command="TSingleAxisDim",
                                             handles=[axes[0]["handle"]],
                                             verify_type="", expect_delta=1)

        for key, spec in recipes.items():
            if wanted and key not in wanted:
                continue
            try:
                result = gui_drive.run_prompt_driven(**spec)
            except Exception as exc:  # noqa: BLE001
                import traceback

                traceback.print_exc()
                result = {"ok": False, "status": "exception", "command": spec["command"],
                          "message": f"{type(exc).__name__}: {exc}"}
            results[key] = result
            show(key, result)
            # 每条之间退回到干净状态，别把上一条的残留带进下一条
            sendinput_reset(app)

        print("\n----- 汇总 -----")
        for key, result in results.items():
            print(("  PASS  " if result.get("ok") else "  FAIL  ") + key
                  + f"  status={result.get('status')} delta={result.get('total_delta')}")
        if wanted and any(not r.get("ok") for r in results.values()):
            sys.exit(1)
    finally:
        try:
            acad_conn.call_with_retry(lambda: doc.Close(False), attempts=40,
                                      delay=1.0, label="close")
            print("closed without saving")
        except Exception as exc:  # noqa: BLE001
            print("close failed:", exc)


def sendinput_reset(app) -> None:
    from t20_arch_mcp import sendinput

    try:
        hwnd = int(app.HWND)
        sendinput.ensure_focus(hwnd)
        sendinput.focus_drawing_view(hwnd)
        sendinput.send_escape(2)
        winmsg.cancel_dialogs(gui_select._pid_of(hwnd), titles=("",))
    except Exception:
        pass


def _wall_centre(app, handle: str) -> list[float]:
    info = gui_drive.centre_of(handle)
    return list(info["world"])


if __name__ == "__main__":
    pythoncom.CoInitialize()
    main()

