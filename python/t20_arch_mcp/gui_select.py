"""gui_select.py - 需要"真人点选"的命令：截图/鼠标那一层的自动化。

为什么单独成模块：天正里有 7 条以上命令卡在同一件事上 —— 它们先要求「选择对象」，
而 COM 无法替 AutoCAD 完成一次真实点选（`SelectOnScreen` 也是交互式的）。
纯 MCP（只有 stdio + COM）对这类命令只能返回"请你手点"。

有桌面控制能力的宿主就多一层手：把对象句柄换成屏幕坐标 → 真点一下 → 回车 → 核对
实体增量。本模块只做这一层，不重复 `sendinput` / `screen_map` / `winmsg` 已有的活。

**能力门控（重要）**：这些函数依赖 Windows 桌面。默认不注册对应 MCP 工具，
只有宿主显式设置 `T20_HOST_GUI=1` 才暴露；没有 GUI 的宿主应当改用
`capability_report` 给出的替代路径，或提示用户换成带 GUI 能力的 AI 工具。
"""
from __future__ import annotations

import os
import time
from typing import Any

from . import acad_conn, screen_map, sendinput, winmsg

GUI_ENV = "T20_HOST_GUI"
ENTER_VK = 0x0D


def gui_enabled() -> bool:
    return os.environ.get(GUI_ENV, "").strip().lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------- #
# 纯逻辑（可单测，不碰 GUI）
# --------------------------------------------------------------------------- #
def _bbox_centre(box: Any) -> tuple[float, float] | None:
    try:
        lo, hi = box[0], box[1]
        return ((float(lo[0]) + float(hi[0])) / 2.0,
                (float(lo[1]) + float(hi[1])) / 2.0)
    except Exception:  # noqa: BLE001
        return None


def pick_point_from_geometry(kind: str, geometry: dict[str, Any]) -> tuple[float, float] | None:
    """根据几何算一个"一定落在对象上"的点。

    实测教训：**包围盒中心不能当点选点**
      * 墙是双线，中心正好落在两条线之间的空隙（AutoCAD 判定为空白）；
      * 门的包围盒含开启弧，中心也不在门框上。
    所以线段取中点、圆/弧取圆周上的点，其余才退回包围盒中心。
    """
    kind = (kind or "").upper()
    if kind.startswith("ACDB"):
        kind = kind[4:]
    if kind == "LINE" and geometry.get("start") and geometry.get("end"):
        sx, sy = geometry["start"]
        ex, ey = geometry["end"]
        return ((float(sx) + float(ex)) / 2.0, (float(sy) + float(ey)) / 2.0)
    if kind in ("CIRCLE", "ARC") and geometry.get("centre") and geometry.get("radius"):
        cx, cy = geometry["centre"]
        r = float(geometry["radius"])
        # 45 度方向，避开象限点的捕捉干扰
        return (float(cx) + r * 0.7071, float(cy) + r * 0.7071)
    centre = _bbox_centre(geometry.get("bbox"))
    return centre


def inside_all(points: list[tuple[int, int]], state: dict[str, Any]) -> bool:
    return all(screen_map.inside_view(state, p) for p in points)


# --------------------------------------------------------------------------- #
# COM 侧：把句柄翻译成屏幕坐标
# --------------------------------------------------------------------------- #
def _geometry_of(entity: Any) -> dict[str, Any]:
    name = str(getattr(entity, "ObjectName", "")).upper()
    out: dict[str, Any] = {"kind": name}
    try:
        lower, upper = entity.GetBoundingBox()
        out["bbox"] = [[float(lower[0]), float(lower[1])],
                       [float(upper[0]), float(upper[1])]]
    except Exception:  # noqa: BLE001
        out["bbox"] = None
    for kind, cast in (("LINE", "IAcadLine"), ("CIRCLE", "IAcadCircle"),
                       ("ARC", "IAcadArc")):
        if kind not in name:
            continue
        try:
            import win32com.client as win32com

            typed = win32com.CastTo(entity, cast)
            if kind == "LINE":
                out["start"] = [float(typed.StartPoint[0]), float(typed.StartPoint[1])]
                out["end"] = [float(typed.EndPoint[0]), float(typed.EndPoint[1])]
            else:
                out["centre"] = [float(typed.Center[0]), float(typed.Center[1])]
                out["radius"] = float(typed.Radius)
            break
        except Exception:  # noqa: BLE001
            continue
    return out


def _lookup(entity_handles: list[str]) -> dict[str, Any]:
    doc = acad_conn._active_document()
    msp = doc.ModelSpace
    wanted = {str(h).strip().upper() for h in entity_handles}
    found: dict[str, Any] = {}
    for index in range(int(msp.Count)):
        ent = acad_conn.call_with_retry(lambda i=index: msp.Item(i), attempts=3)
        try:
            handle = str(ent.Handle).upper()
        except Exception:  # noqa: BLE001
            continue
        if handle in wanted:
            found[handle] = ent
            if len(found) == len(wanted):
                break
    return found


def _plan_impl(spec: dict[str, Any]) -> dict[str, Any]:
    doc = acad_conn._active_document()
    hwnd = int(spec["hwnd"])
    state = screen_map.view_state_for_doc(doc, hwnd)
    if not state:
        return {"ok": False, "error": "读不到视图变换（ActiveViewport 取不到值），先 ZOOM 全图后重试"}

    points: list[dict[str, Any]] = []
    for item in spec["picks"]:
        if "world" in item and item["world"]:
            wx, wy = float(item["world"][0]), float(item["world"][1])
            points.append({"source": "world", "world": [wx, wy],
                           "screen": screen_map.world_to_screen(state, wx, wy)})
            continue
        handle = str(item.get("handle", "")).strip().upper()
        ent = spec.get("_found", {}).get(handle)
        if ent is None:
            points.append({"source": "handle", "handle": handle, "screen": None,
                           "error": "模型空间里找不到这个句柄"})
            continue
        geom = _geometry_of(ent)
        world = pick_point_from_geometry(geom["kind"], geom)
        if world is None:
            points.append({"source": "handle", "handle": handle, "screen": None,
                           "error": "算不出可点的位置（无包围盒）"})
            continue
        points.append({"source": "handle", "handle": handle, "kind": geom["kind"],
                       "world": [world[0], world[1]],
                       "screen": screen_map.world_to_screen(state, world[0], world[1])})

    screens = [tuple(p["screen"]) for p in points if p.get("screen")]
    labels: list[dict[str, Any]] = []
    for world in spec.get("labels", []) or []:
        wx, wy = float(world[0]), float(world[1])
        labels.append({"world": [wx, wy],
                       "screen": screen_map.world_to_screen(state, wx, wy)})
    if labels and not all(l.get("screen") for l in labels):
        return {"ok": False, "error": "定位点算不出屏幕坐标，先检查坐标是否在视图内"}

    return {
        "ok": all(p.get("screen") for p in points),
        "pid": int(spec.get("pid") or 0),
        "points": points,
        "labels": labels,
        "view": {"left": state["left"], "top": state["top"],
                 "width": state["width"], "height": state["height"],
                 "scale": round(state["scale"], 6)},
        "all_inside": inside_all(screens, state) if screens else False,
    }


def _count_impl(spec: dict[str, Any]) -> dict[str, Any]:
    """数实体总量 / 某类实体数量。

    这里坚持"自己遍历 ObjectName"，不走 `tianzheng_query.count_entities`：后者在
    个别天正实体上会卡住整个 COM 调用（实测一次 TCH_WALL 直接超时 25 s，把随后的
    调用全堵死）。读名字是最便宜的属性，读不到就跳过。
    """
    doc = acad_conn._active_document()
    total = 0
    msp = None
    try:
        msp = doc.ModelSpace
        total = int(msp.Count)
    except Exception:  # noqa: BLE001
        total = 0
    out: dict[str, Any] = {"total": total}
    kind = str(spec.get("kind") or "")
    if kind and msp is not None:
        n = 0
        for i in range(total):
            try:
                name = str(msp.Item(i).ObjectName)
            except Exception:  # noqa: BLE001
                continue
            if kind.upper() in name.upper():
                n += 1
        out["typed"] = n
    return out


def _send_command_impl(command: str) -> dict[str, Any]:
    """注意：不要用这个发"选择类"命令。

    `Document.SendCommand` 是**同步**的，命令在等鼠标点选时会一直挂着，直到
    点完才返回 —— 于是"发命令"这一步就把调用线程堵死，后面根本轮不到去点。
    实测踩过：TDimRad 发出后 120 s 超时，CAD 停在"选择对象"。
    这类命令要用键盘输入（`_type_command`），让 AutoCAD 自己进入提示状态。
    """
    doc = acad_conn._active_document()
    doc.SendCommand(command.rstrip("\n") + "\n")
    return {"ok": True, "sent": command}


def _type_command(hwnd: int, command: str, settle: float = 0.25) -> None:
    """把命令当键盘敲进去：窗口聚焦 → 绘图区聚焦 → 打字 → 回车。"""
    sendinput.ensure_focus(hwnd)
    sendinput.focus_drawing_view(hwnd)
    time.sleep(settle)
    sendinput.type_unicode(command.strip(), settle=0.05)
    sendinput.press_key(ENTER_VK, settle=0.2)


def _pickfirst_impl(_spec: dict[str, Any]) -> dict[str, Any]:
    doc = acad_conn._active_document()
    try:
        count = int(doc.PickfirstSelectionSet.Count)
        handles = [str(i.Handle) for i in doc.PickfirstSelectionSet]
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"读不到 PickfirstSelectionSet：{exc}"}
    return {"ok": True, "count": count, "handles": handles}


def _read_pickfirst_retry(attempts: int = 6, delay: float = 0.3) -> dict[str, Any]:
    """读当前预选集合，容忍 AutoCAD 短暂拒收。

    实测：点在空白处时 AutoCAD 进入「指定对角点」窗口选择，COM 会被拒到按 ESC 为止；
    所以这里多试几次，读不到就交给调用方 ESC 掉这一轮。
    """
    last = ""
    for _ in range(attempts):
        try:
            return acad_conn._com(_pickfirst_impl, {}, timeout=12)
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
            time.sleep(delay)
    return {"ok": False, "error": f"读不到 PickfirstSelectionSet：{last}"}


def screen_view_ok(state: dict[str, Any] | None, px: int, py: int) -> bool:
    if not state:
        return True
    try:
        return bool(screen_map.inside_view(state, (px, py)))
    except Exception:  # noqa: BLE001
        return True


def _view_state_impl(spec: dict[str, Any]) -> dict[str, Any]:
    doc = acad_conn._active_document()
    state = screen_map.view_state_for_doc(doc, int(spec["hwnd"]))
    return {"ok": bool(state), "state": state}


def _state_now(hwnd: int) -> dict[str, Any] | None:
    got = acad_conn._com(_view_state_impl, {"hwnd": int(hwnd)}, timeout=60)
    return got.get("state") if got.get("ok") else None


def _candidates_impl(spec: dict[str, Any]) -> dict[str, Any]:
    """给一个对象的包围盒，算出"从中心往外"的一串候选点（世界+屏幕）。

    天正房间(TDBSPACE)唯一的几何信息就是包围盒：房间名和面积文字落在哪儿不知道，
    框选又选不中（实测窗口选择对这些自定义对象无效）。所以只能试：先点中心，
    不行就按环形网格往外挪，直到某一个点真的选中了它。
    """
    doc = acad_conn._active_document()
    state = screen_map.view_state_for_doc(doc, int(spec["hwnd"]))
    if not state:
        return {"ok": False, "error": "读不到视图变换（先 ZOOM 全图）"}
    found = _lookup([spec["handle"]])
    ent = found.get(str(spec["handle"]).upper())
    if ent is None:
        return {"ok": False, "error": "模型空间里没有这个句柄"}
    geom = _geometry_of(ent)
    box = geom.get("bbox")
    if not box:
        return {"ok": False, "error": "这个对象没有包围盒"}
    lo, hi = box
    w, h = float(hi[0]) - float(lo[0]), float(hi[1]) - float(lo[1])
    grid = int(spec.get("grid") or 5)
    fractions = [(0.5, 0.5)]
    for ring in range(1, grid):
        step = 1.0 / (grid + 1)
        for i in range(grid + 2):
            for j in range(grid + 2):
                fx, fy = i * step, j * step
                if 0.05 < fx < 0.95 and 0.05 < fy < 0.95:
                    dist = ((fx - 0.5) ** 2 + (fy - 0.5) ** 2) ** 0.5
                    fractions.append((round(fx, 3), round(fy, 3)))
        fractions = [fractions[0]] + sorted(
            {f for f in fractions[1:]},
            key=lambda f: ((f[0] - 0.5) ** 2 + (f[1] - 0.5) ** 2) ** 0.5)
        break
    out: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for fx, fy in fractions:
        wx = float(lo[0]) + w * fx
        wy = float(lo[1]) + h * fy
        screen = tuple(screen_map.world_to_screen(state, wx, wy))
        if screen in seen:
            continue
        seen.add(screen)
        out.append({"world": [wx, wy], "screen": list(screen)})
    return {"ok": True, "kind": geom.get("kind"), "bbox": box, "candidates": out}


def probe_pick(handle: str, grid: int = 5, settle: float = 0.3) -> dict[str, Any]:
    """试出一张"点到就能选中这个对象"的屏幕点。

    做法：逐个候选点真点一下，读 `PickfirstSelectionSet`；选中了就记下来并 ESC 清空。
    因为每个对象只要一个点，结果由调用方缓存，别每次命令都重算。
    """
    if not gui_enabled():
        raise acad_conn.AcadError(
            f"宿主没有桌面控制能力（{GUI_ENV} 未开启）。这条命令需要真实鼠标点选。")
    app = acad_conn._attach_app()
    hwnd = _hwnd_of(app)
    plan = acad_conn._com(_candidates_impl,
                          {"hwnd": hwnd, "handle": str(handle), "grid": int(grid)},
                          timeout=90)
    if not plan.get("ok"):
        return {"ok": False, "handle": handle, "error": plan.get("error")}

    sendinput.ensure_focus(hwnd)
    sendinput.focus_drawing_view(hwnd)
    time.sleep(settle)
    state = _state_now(hwnd)
    tried: list[dict[str, Any]] = []
    for candidate in plan["candidates"]:
        px, py = candidate["screen"]
        if state and not screen_view_ok(state, px, py):
            continue
        sendinput.click(int(px), int(py), settle=settle)
        sel = _read_pickfirst_retry()
        hit = str(handle).upper() in [str(h).upper() for h in (sel.get("handles") or [])]
        tried.append({"screen": candidate["screen"], "world": candidate["world"],
                      "hit": hit, "count": sel.get("count"),
                      "handles": sel.get("handles")})
        sendinput.send_escape(2, settle=0.18)
        if hit:
            return {"ok": True, "handle": handle, "kind": plan.get("kind"),
                    "point": candidate, "tries": len(tried), "attempts": tried}
    return {"ok": False, "handle": handle, "error": "所有候选点都没点中",
            "tries": None, "attempts": tried}


# --------------------------------------------------------------------------- #
# 对外：点一条命令
# --------------------------------------------------------------------------- #
def _hwnd_of(app: Any) -> int:
    try:
        return int(app.HWND)
    except Exception as exc:  # noqa: BLE001
        raise acad_conn.AcadError(f"拿不到 AutoCAD 主窗口句柄：{exc}") from exc


def _pid_of(hwnd: int) -> int:
    try:
        import ctypes

        pid = ctypes.c_ulong(0)
        ctypes.windll.user32.GetWindowThreadProcessId(
            ctypes.c_void_p(hwnd), ctypes.byref(pid))
        return int(pid.value)
    except Exception:  # noqa: BLE001
        return 0


def click_targets(handles: list[str], points: list[list[float]] | None = None,
                  settle: float = 0.35) -> dict[str, Any]:
    """只做点选并回读 PickfirstSelectionSet —— 用来验证"点得准不准"。"""
    if not gui_enabled():
        raise acad_conn.AcadError(
            f"宿主没有桌面控制能力（{GUI_ENV} 未开启）。这条命令需要真实鼠标点选。")
    app = acad_conn._attach_app()
    hwnd = _hwnd_of(app)
    pid = _pid_of(hwnd)
    picks = [{"handle": h} for h in handles] + [{"world": p} for p in (points or [])]
    found = acad_conn._com(_lookup, handles) if handles else {}

    plan = acad_conn._com(_plan_impl, {"hwnd": hwnd, "pid": pid, "picks": picks,
                                       "_found": found}, timeout=90)
    if not plan.get("ok"):
        return {"ok": False, "stage": "plan", "plan": plan,
                "message": "有点算不出屏幕坐标，先检查句柄是否存在、对象是否在模型空间。"}
    if not plan.get("all_inside"):
        return {"ok": False, "stage": "viewport",
                "message": "目标不在当前视图内。先 ZOOM 全图（或缩小）再点，避免点到视图外。"}

    sendinput.ensure_focus(hwnd)
    sendinput.focus_drawing_view(hwnd)
    time.sleep(settle)
    clicked = []
    for point in plan["points"]:
        px, py = point["screen"]
        sendinput.click(int(px), int(py), settle=settle)
        clicked.append(point)
    selection = acad_conn._com(_pickfirst_impl, {})
    return {"ok": True, "pid": pid, "clicked": clicked, "selection": selection}


def run_select_command(command: str, handles: list[str],
                       points: list[list[float]] | None = None,
                       label_points: list[list[float]] | None = None,
                       verify_type: str = "", expect_delta: int = 1,
                       settle: float = 0.5, wait: float = 2.5) -> dict[str, Any]:
    """发一条"要选对象"的命令 → 逐点点选 → 回车 →（可选）点定位点 → 核对增量。

    label_points 是选完对象之后第二步要点的位置（半径标注的"尺寸线位置"、面积统计的
    "表格插入点"…）。不会再补回车：命令结束后一个回车会重复上一条命令。
    """
    if not gui_enabled():
        raise acad_conn.AcadError(
            f"宿主没有桌面控制能力（{GUI_ENV} 未开启）。这条命令需要真实鼠标点选。")
    if not command.strip():
        raise acad_conn.AcadError("command 不能为空")

    app = acad_conn._attach_app()
    hwnd = _hwnd_of(app)
    pid = _pid_of(hwnd)
    picks = [{"handle": h} for h in handles] + [{"world": p} for p in (points or [])]
    found = acad_conn._com(_lookup, handles) if handles else {}
    plan = acad_conn._com(_plan_impl, {"hwnd": hwnd, "pid": pid, "picks": picks,
                                       "labels": label_points or [],
                                       "_found": found}, timeout=90)
    if not plan.get("ok"):
        return {"ok": False, "stage": "plan", "plan": plan,
                "message": "有点算不出屏幕坐标，先检查句柄是否存在、对象是否在模型空间。"}
    if not plan.get("all_inside"):
        return {"ok": False, "stage": "viewport",
                "message": "目标不在当前视图内。先 ZOOM 全图（或缩小）再点，避免点到视图外。"}

    tail_before = winmsg.command_tail(pid, 300) if pid else ""
    before = acad_conn._com(_count_impl, {"kind": verify_type})
    _type_command(hwnd, command)
    time.sleep(wait)

    clicked = []
    for point in plan["points"]:
        px, py = point["screen"]
        sendinput.click(int(px), int(py), settle=settle)
        clicked.append(point)
    sendinput.press_key(ENTER_VK, settle=0.2)
    time.sleep(wait)

    label_clicks = []
    for label in plan.get("labels") or []:
        px, py = label["screen"]
        sendinput.click(int(px), int(py), settle=settle)
        label_clicks.append(label)
    if label_clicks:
        time.sleep(wait)

    after = acad_conn._com(_count_impl, {"kind": verify_type})
    tail_after = winmsg.command_tail(pid, 400) if pid else ""
    total_delta = int(after.get("total", 0)) - int(before.get("total", 0))
    typed_delta = None
    if verify_type and after.get("typed") is not None and before.get("typed") is not None:
        typed_delta = int(after["typed"]) - int(before["typed"])

    ok = (typed_delta if typed_delta is not None else total_delta) >= expect_delta
    return {
        "ok": bool(ok),
        "command": command,
        "clicked": clicked,
        "label_clicks": label_clicks,
        "before": before,
        "after": after,
        "total_delta": total_delta,
        "typed_delta": typed_delta,
        "expect_delta": expect_delta,
        "verify_type": verify_type,
        "tail_before": tail_before,
        "tail_after": tail_after,
        "message": None if ok else "命令发出去并点了，但没有达到预期的实体增量，看 tail_after 的提示。",
    }
