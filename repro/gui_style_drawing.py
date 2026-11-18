"""GUI 档功能验收：在一张新图上把"必须真人点鼠标"的天正命令逐条跑一遍。

这张图就是插件 GUI 能力的样板/样式图：
    轴网 → 墙体 → 房间 → 房间排序 → 面积统计表
    圆 → 半径标注 / 直径标注
    矩形 → 填充 → 填充面积表
    文字 → 文字加框
    单轴标注、轴网标注、墙体分段

用法：
    python repro/gui_style_drawing.py                 # 建到 %TEMP%\\gui_style.dwg 并导 PDF
    python repro/gui_style_drawing.py --out D:\\x.dwg

前提：AutoCAD + 天正已打开、窗口可见；环境变量 T20_HOST_GUI=1。
每条命令都用"实体增量 + 命令是否跑完"核对，跑不通会打印失败原因，不会静默跳过。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "python"))
os.environ.setdefault("T20_ALLOW_FOREIGN_DOC", "true")
os.environ.setdefault("T20_HOST_GUI", "1")

import pythoncom  # noqa: E402
import win32gui  # noqa: E402

pythoncom.CoInitialize()

from t20_arch_mcp import (acad_conn, gui_drive, gui_select, native_tools,  # noqa: E402
                          plot_export, sendinput, tianzheng_runner, winmsg)

TEMPLATE = r"C:\Tangent\TArchT30V2\sys19x64\ACAD.DWT"


def cad_hwnd() -> int:
    hit: list[int] = []

    def cb(h, _):
        try:
            if win32gui.IsWindowVisible(h) and "AutoCAD" in win32gui.GetWindowText(h):
                hit.append(int(h))
        except Exception:  # noqa: BLE001
            pass
        return True

    win32gui.EnumWindows(cb, None)
    if not hit:
        raise SystemExit("找不到 AutoCAD 主窗口")
    return hit[0]


def recover(pid: int, hwnd: int) -> None:
    """命令卡住时用真 ESC 解出来（不依赖 COM）。"""
    import ctypes

    for _ in range(6):
        try:
            acad_conn._com(lambda: int(acad_conn._active_document().ModelSpace.Count),
                           timeout=6)
            return
        except Exception:  # noqa: BLE001
            ctypes.windll.user32.PostMessageW(hwnd, 0x0100, 0x1B, 0)
            ctypes.windll.user32.PostMessageW(hwnd, 0x0101, 0x1B, 0)
            time.sleep(1.0)


TYPE_ALIAS = {"TCH_WALL": "TDbWall", "TCH_SPACE": "TDbSpace",
              "TCH_OPENING": "TDbOpening", "TCH_COLUMN": "TDbColumn"}


def counts(verify_type: str) -> int:
    """数某类实体。自己数，不用 tianzheng_query：后者在个别天正实体上会卡住。"""
    kind = TYPE_ALIAS.get(verify_type.upper(), verify_type)

    def impl() -> int:
        doc = acad_conn._active_document()
        msp = doc.ModelSpace
        total = int(msp.Count)
        n = 0
        for i in range(total):
            try:
                ent = msp.Item(i)
            except Exception:  # noqa: BLE001
                continue
            try:
                name = str(ent.ObjectName)
            except Exception:  # noqa: BLE001
                continue
            if kind.upper() in name.upper():
                n += 1
        return n

    return int(acad_conn.call_with_retry(
        lambda: acad_conn._com(impl, timeout=30), attempts=8, delay=1.0,
        label=f"count {verify_type}") or 0)


def reset_focus(hwnd: int) -> None:
    """退命令 + 抢回前台；天正的命令常停在自己的循环里，不退出来 COM 会一直超时。"""
    gui_drive.ensure_ready(hwnd, attempts=7)
    sendinput.ensure_focus(hwnd)
    sendinput.focus_drawing_view(hwnd)
    time.sleep(0.3)


def handles_of(kind: str) -> list[str]:
    def impl() -> list[str]:
        doc = acad_conn._active_document()
        msp = doc.ModelSpace
        out: list[str] = []
        for i in range(int(msp.Count)):
            ent = msp.Item(i)
            try:
                if kind.upper() in str(ent.ObjectName).upper():
                    out.append(str(ent.Handle))
            except Exception:  # noqa: BLE001
                continue
        return out

    return list(acad_conn._com(impl, timeout=60) or [])


def dote_axes() -> list[str]:
    def impl() -> list[str]:
        doc = acad_conn._active_document()
        msp = doc.ModelSpace
        out: list[str] = []
        for i in range(int(msp.Count)):
            ent = msp.Item(i)
            try:
                if str(ent.Layer).upper() == "DOTE":
                    out.append(str(ent.Handle))
            except Exception:  # noqa: BLE001
                continue
        return out

    return list(acad_conn._com(impl, timeout=60) or [])


def make_arc(centre: list[float], radius: float,
             start_deg: float, end_deg: float) -> str:
    import math

    def impl() -> str:
        doc = acad_conn._active_document()
        arc = doc.ModelSpace.AddArc(
            native_tools._variant_point(centre), float(radius),
            math.radians(start_deg), math.radians(end_deg))
        return str(arc.Handle)

    return str(acad_conn._com(impl, timeout=60))


def make_hatch(poly_handle: str) -> str:
    def impl() -> str:
        doc = acad_conn._active_document()
        msp = doc.ModelSpace
        poly = doc.HandleToObject(poly_handle)
        hatch = msp.AddHatch(0, "ANSI31", True)
        from win32com.client import VARIANT
        from win32com.client import pythoncom as pc

        hatch.AppendOuterLoop(VARIANT(pc.VT_ARRAY | pc.VT_DISPATCH, [poly]))
        hatch.Evaluate()
        return str(hatch.Handle)

    return str(acad_conn._com(impl, timeout=60))


def close_stale() -> list[str]:
    """关掉本脚本前几次跑剩下的 gui_style*.dwg。

    同名 dwg 若已被另一个文档打开，SaveAs 会报「保存文档时出错」。
    """
    def impl() -> list[str]:
        app = acad_conn._attach_app()
        closed: list[str] = []
        names: list[str] = []
        for i in range(int(app.Documents.Count)):
            try:
                doc = app.Documents.Item(i)
                names.append((str(doc.Name), doc))
            except Exception:  # noqa: BLE001
                continue
        for name, doc in names:
            if name.lower().startswith("gui_style") and names.__len__() > 1:
                try:
                    doc.Close(False)  # 不保存，纯临时图
                    closed.append(name)
                except Exception:  # noqa: BLE001
                    pass
        return closed

    try:
        closed = acad_conn._com(impl, timeout=60)
    except Exception:  # noqa: BLE001
        return []
    return list(closed or [])


def red_axis_screen_points(hwnd: int) -> list[list[int]]:
    """截图数红像素，给出轴网首末两根竖线的屏幕坐标。

    为什么不用 world→screen：键盘 `ZOOM` 改得了画面，但 `ActiveViewport` 读回的
    centre/viewsize 不跟着变（scale 恒 0.042），按缩放算出来的点会偏，点 1 px
    细线必然落空。所以直接看画面：竖轴取像素列直方图的第一个/最后一个峰，
    y 取前两根横轴的中点（这样两点都落在同一根横轴之间，一定命中竖线）。
    """
    from PIL import ImageGrab

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    img = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
    px = img.convert("RGB").load()
    width, height = img.size

    def is_red(p: tuple[int, int, int]) -> bool:
        r, g, b = p
        return r > 140 and g < 70 and b < 70

    cols = {x: sum(1 for y in range(150, height - 120) if is_red(px[x, y]))
            for x in range(200, width)}
    groups: list[list[int]] = []
    for x in sorted(k for k, v in cols.items() if v > 40):
        if groups and x - groups[-1][-1] <= 2:
            groups[-1].append(x)
        else:
            groups.append([x])
    vx = [sum(g) // len(g) for g in groups]

    rows = {y: sum(1 for x in range(200, width) if is_red(px[x, y]))
            for y in range(150, height - 120)}
    groups = []
    for y in sorted(k for k, v in rows.items() if v > 100):
        if groups and y - groups[-1][-1] <= 2:
            groups[-1].append(y)
        else:
            groups.append([y])
    hy = [sum(g) // len(g) for g in groups]

    if len(vx) < 2 or len(hy) < 2:
        return []
    y_click = (hy[0] + hy[1]) // 2
    return [[left + vx[0], top + y_click], [left + vx[-1], top + y_click]]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.environ.get("TEMP", "."),
                                                 "gui_style_sample.dwg"))
    ap.add_argument("--pdf", default="")
    ap.add_argument("--no-pdf", action="store_true")
    args = ap.parse_args()

    hwnd = cad_hwnd()
    pid = gui_select._pid_of(hwnd)
    recover(pid, hwnd)
    close_stale()

    final_out = os.path.abspath(args.out)
    # 工作文件名必须每次唯一：同名 dwg 若已被前一次跑剩的文档占着，
    # SaveAs 一律报「保存文档时出错」(-2145320861)。最后再复制成稳定的交付名。
    work = os.path.join(os.environ.get("TEMP", "."),
                        f"gui_style_work_{time.strftime('%H%M%S')}.dwg")
    info = acad_conn.create_test_drawing(TEMPLATE)
    time.sleep(1.5)
    # 先给新图落一个可写的文件名：带天正表格的图在"未命名 + 模板目录"状态下
    # SaveAs 会报「保存文档时出错」，先存一次就绕开了。
    # 注意：这一步不要先按 ESC —— 实测刚建图就抢焦点按 ESC，SaveAs 会失败。
    pre = native_tools.save_drawing(work)
    print(f"新图：{pre.get('document')} → {work}")
    reset_focus(hwnd)

    results: list[dict] = []

    def record(name: str, ok: bool, detail: dict | str) -> None:
        results.append({"name": name, "ok": bool(ok),
                        "detail": detail if isinstance(detail, str)
                        else json.dumps(detail, ensure_ascii=False, default=str)})
        print(("  PASS  " if ok else "  FAIL  ") + name
              + (f"  {detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False, default=str)[:150]}"))

    def record_gui(name: str, res: dict, note: str = "") -> bool:
        ok = bool(res.get("total_delta", 0) > 0)
        if res.get("status") != "done":
            ok = False
        record(name, ok, {"delta": res.get("total_delta"),
                          "status": res.get("status"), "note": note})
        if not ok:
            print(f"        · {res.get('message')}")
        return ok

    # ---------------- 1. 轴网 ----------------
    print("\n[1] 轴网 HZZW")
    axis = tianzheng_runner.run_with_dialog("HZZW", ["0,0"], verify_layer="DOTE")
    reset_focus(hwnd)   # HZZW 插完轴网会停在"再选插入点"的循环里，要 ESC 退出来
    record("轴网 HZZW", bool(axis.get("type_deltas", {}).get("LINE")),
           {"lines": axis.get("type_deltas", {}).get("LINE"), "ms": axis.get("model_space_after")})

    # ---------------- 2. 墙体 ----------------
    print("\n[2] 墙体 HZQT")
    walls_before = counts("TCH_WALL")
    for a, b in (("0,0", "6000,0"), ("6000,0", "6000,4000"),
                 ("6000,4000", "0,4000"), ("0,4000", "0,0")):
        tianzheng_runner.run_with_dialog("HZQT", [a, b], verify_type="TCH_WALL")
        reset_focus(hwnd)
    walls_after = counts("TCH_WALL")
    record("墙体 HZQT", walls_after - walls_before >= 4,
           {"before": walls_before, "after": walls_after})

    # ---------------- 3. 房间 ----------------
    print("\n[3] 房间 TUpdSpace")
    room_before = counts("TCH_SPACE")
    space = tianzheng_runner.run_with_dialog("TUpdSpace", ["ALL", "", "3000,2000"],
                                            verify_type="TCH_SPACE")
    reset_focus(hwnd)
    room_after = counts("TCH_SPACE")
    record("房间 TUpdSpace", room_after - room_before >= 1,
           {"before": room_before, "after": room_after})

    rooms = handles_of("TDbSpace")
    print(f"      房间句柄：{rooms}")

    # ---------------- 4. 房间排序（GUI） ----------------
    print("\n[4] 房间排序 TSortSpace（GUI：窗交选房间）")
    delta_room = gui_drive.run_prompt_driven(
        "TSortSpace", handles=rooms, select_mode="window", select_keyword="C",
        window_pad=60.0, zoom_fill=0.85, settle=0.45, deadline=60, expect_delta=0)
    record("房间排序 TSortSpace", delta_room.get("status") == "done",
           {"delta": delta_room.get("total_delta"), "status": delta_room.get("status")})

    # ---------------- 5. 面积统计（GUI） ----------------
    print("\n[5] 面积统计 TCountArea（GUI：窗交选房间 + 点表格位置）")
    area = gui_drive.run_prompt_driven(
        "TCountArea", handles=rooms, points=[[6300.0, 1200.0]],
        select_mode="window", select_keyword="C", window_pad=60.0,
        zoom_fill=0.85, settle=0.45, deadline=80, expect_delta=1)
    record_gui("面积统计 TCountArea", area)

    # ---------------- 6. 圆 + 半径/直径标注（GUI） ----------------
    print("\n[6] 半径/直径标注")
    circle = native_tools.draw_circle([9500, 5600], 900)
    print(f"      圆：{circle.get('handle')}")
    arc = make_arc([12500, 5600], 700, 20.0, 160.0)
    print(f"      弧：{arc}")
    rad = gui_drive.run_prompt_driven(
        "TDimRad", handles=[circle["handle"]], points=[[10900.0, 5600.0]],
        select_mode="point", window_pad=8.0, confirm_enter=False,
        zoom_fill=0.85, settle=0.45, deadline=60, expect_delta=1)
    record_gui("半径标注 TDimRad", rad)
    dia = gui_drive.run_prompt_driven(
        "TDimDia", handles=[arc], points=[[12500.0, 6400.0]],
        select_mode="point", window_pad=8.0, confirm_enter=False,
        zoom_fill=0.85, settle=0.45, deadline=60, expect_delta=1)
    record_gui("直径标注 TDimDia", dia)

    # ---------------- 7. 填充 + 填充面积（GUI） ----------------
    print("\n[7] 填充面积 THatchArea")
    poly = native_tools.draw_closed_polyline([[7200, 1600], [10200, 1600],
                                             [10200, 3400], [7200, 3400]])
    hatch_handle = make_hatch(poly["handle"])
    print(f"      填充：{hatch_handle}")
    hatch_area = gui_drive.run_prompt_driven(
        "THatchArea", handles=[hatch_handle], points=[[6300.0, -2200.0]],
        select_mode="point", window_pad=6.0, settle=0.45,
        zoom_window=[[5200.0, -5200.0], [11900.0, 4600.0]],
        deadline=70, expect_delta=1)
    record_gui("填充面积 THatchArea", hatch_area)

    # ---------------- 8. 文字 + 加框（GUI） ----------------
    print("\n[8] 文字加框 TTextAddFrame")
    txt = native_tools.add_text([0, 8000], "天正 GUI 样式图  验收样张", height=420)
    print(f"      文字：{txt.get('handle')}")
    frame = gui_drive.run_prompt_driven(
        "TTextAddFrame", handles=[txt["handle"]], select_mode="window", window_pad=60.0,
        zoom_fill=0.85, settle=0.45, deadline=60, expect_delta=1)
    record_gui("文字加框 TTextAddFrame", frame)

    # ---------------- 9. 单轴标注（GUI） ----------------
    print("\n[9] 单轴标注 TSingleAxisDim")
    axes = dote_axes()
    if axes:
        one = gui_drive.run_prompt_driven(
            "TSingleAxisDim", handles=[axes[-1]], select_mode="point",
            zoom_fill=0.85, settle=0.45, deadline=60, expect_delta=1)
        record_gui("单轴标注 TSingleAxisDim", one)
    else:
        record("单轴标注 TSingleAxisDim", False, "图里没有 DOTE 轴线")

    # ---------------- 10. 轴网标注（GUI：截图定位轴网竖线） ----------------
    print("\n[10] 轴网标注 TMultAxisDim（GUI：截图定位轴网竖线）")
    gui_drive.zoom_window_kb([-1500, -1500], [6300, 9000])
    time.sleep(1.0)
    axis_pts = red_axis_screen_points(hwnd)
    if axis_pts:
        multi = gui_select.run_select_command(
            "TMULTAXISDIM", [], screen_points=axis_pts,
            verify_type="TDbAxisLabelSet", expect_delta=1, wait=3.0)
        record("轴网标注 TMultAxisDim", multi.get("ok") is True,
               {"typed_delta": multi.get("typed_delta"),
                "total_delta": multi.get("total_delta"),
                "points": axis_pts})
        if multi.get("ok") is not True:
            print(f"        · {multi.get('message')}")
    else:
        record("轴网标注 TMultAxisDim", False, "截图里没找到红色轴网")

    # ---------------- 11. 墙体分段（GUI） ----------------
    print("\n[11] 墙体分段 TPartwall")
    wall_handles = handles_of("TDbWall")
    if wall_handles:
        seg = gui_drive.run_prompt_driven(
            "TPartwall", handles=[wall_handles[0]],
            points=[[1500.0, 0.0], [3500.0, 0.0]],
            select_mode="window", window_pad=200.0, zoom_fill=0.85, settle=0.45,
            deadline=60, expect_delta=0)
        record("墙体分段 TPartwall", seg.get("status") == "done",
               {"delta": seg.get("total_delta"), "status": seg.get("status")})
    else:
        record("墙体分段 TPartwall", False, "图里没有墙")

    # ---------------- 12. 存盘 / 导 PDF / 截图 ----------------
    print("\n[12] 存盘 + 导出")
    gui_drive.ensure_ready(hwnd, attempts=6)
    saved_ok = False
    try:
        acad_conn._com(lambda: acad_conn._active_document().Save(), timeout=120)
        saved_ok = True
    except Exception as exc:  # noqa: BLE001
        print(f"      doc.Save() 失败：{str(exc)[:100]}")
        try:
            saved_ok = bool(native_tools.save_drawing(work).get("ok"))
        except Exception as exc2:  # noqa: BLE001
            print(f"      存盘失败：{str(exc2)[:100]}")
    if saved_ok:
        for target in (final_out, final_out.replace(".dwg", f"_{time.strftime('%H%M%S')}.dwg")):
            try:
                shutil.copyfile(work, target)
                final_out = target
                break
            except Exception as exc:  # noqa: BLE001
                print(f"      复制到 {os.path.basename(target)} 失败：{exc}")
    print(f"      DWG：{final_out}  {saved_ok}")

    z = gui_drive.zoom_extents_kb(fill=0.9)
    time.sleep(0.6)
    shot = os.path.join(os.environ.get("TEMP", "."), "gui_style_view.png")
    try:
        from PIL import ImageGrab

        pane = gui_drive._pane_rect(hwnd)
        img = ImageGrab.grab(bbox=(pane["left"], pane["top"],
                                   pane["left"] + pane["width"],
                                   pane["top"] + pane["height"]), all_screens=True)
        img.save(shot)
        print(f"      截图：{shot}")
    except Exception as exc:  # noqa: BLE001
        print(f"      截图失败：{exc}")

    if not args.no_pdf:
        pdf = os.path.abspath(args.pdf or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "docs", "样式图-GUI功能验收.pdf"))
        try:
            pdf_res = plot_export.export_pdf(pdf)
            print(f"      PDF：{pdf}  {pdf_res.get('size_bytes')} 字节")
        except Exception as exc:  # noqa: BLE001
            print(f"      PDF 失败：{exc}")

    ok_all = all(r["ok"] for r in results)
    print("\n----- 汇总 -----")
    for r in results:
        print(("  PASS  " if r["ok"] else "  FAIL  ") + r["name"])
    print(f"\n共 {len(results)} 条，通过 {sum(1 for r in results if r['ok'])} 条")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
