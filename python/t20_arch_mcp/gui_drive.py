"""gui_drive.py - 驱动"要鼠标"的天正命令：脚本化动作 + 用 COM 探活判进度。

为什么不是"读提示再决定"：实测 AutoCAD 的 F2 命令历史**只在一条提示被回答之后**
才整行落盘。命令等待输入时 `command_tail` 只能读到命令名回显（`TCOUNTAREA`），
拿不到「请选择需要统计面积的对象」这种当前问题。所以靠读提示来逐步分支是行不通的。

可靠的信号是 **COM 是否被拒**：AutoCAD 在等输入时拒绝一切 COM 调用
（RPC_E_CALL_REJECTED / worker 超时），命令一结束立刻恢复。于是驱动方式变成：

    发命令 → 点对象 → 回车 → 点坐标 → 回车兜底 → 用"COM 是否恢复"判断命令结束
                                        → 命令结束后核对实体增量

每条命令真正问过什么，事后从 `transcript`（历史里新增的那一段）能完整读出来，
所以第一次跑完把 transcript 记进文档，后续就有了准确的参数顺序。

能力门控：和 `gui_select` 一致，只有 `T20_HOST_GUI=1` 才允许跑。
"""
from __future__ import annotations

import re
import time
from typing import Any

from . import acad_conn, gui_select, screen_map, sendinput, winmsg

ENTER_VK = 0x0D

# 命令自己报错/拒绝时，没必要继续往下点
FATAL_HINTS = (
    "选择无效", "没有选中", "找不到", "无效的", "距离基线太远",
    "未知命令", "需要点或选项", "不能为空",
)

# 提示 -> 动作。只用于事后解读 transcript（和单测），不用于实时驱动。
PROMPT_RULES: tuple[tuple[str, str], ...] = (
    (r"是否为该对象|是否删除|是否替换|是否更新|是否继续", "accept"),
    (r"选择|拾取|选取|选中", "select"),
    (r"指定|插入点|基点|位置|点取|下一点|第二点|起点|终点", "point"),
    (r"输入|数值|间距|角度|字高|高度|宽度|编号|名称|表头", "text"),
)


def _tail_prompt(tail: str) -> str:
    """最后一条 `命令:` 之后的部分（命令结束后为空）。"""
    if not tail:
        return ""
    return tail.rsplit("命令:", 1)[-1].strip()


def classify_prompt(prompt: str, command: str = "") -> dict[str, Any]:
    """解读一段提示（用于事后分析和单测，不用于实时驱动）。"""
    text = (prompt or "").strip()
    if not text:
        return {"action": "idle", "reason": "命令已结束（末尾是 命令:）"}
    if "*取消*" in text:
        return {"action": "idle", "reason": "命令被取消"}
    flat = re.sub(r"\s+", "", text).upper().lstrip("*")
    if command and flat == re.sub(r"\s+", "", command).upper():
        return {"action": "echo", "reason": "只是命令回显"}
    for hint in FATAL_HINTS:
        if hint in text:
            return {"action": "error", "reason": f"命令报错：{text[-60:]}",
                    "matched": hint}
    for pattern, action in PROMPT_RULES:
        if re.search(pattern, text):
            return {"action": action, "reason": text[-60:], "matched": pattern}
    return {"action": "unknown", "reason": f"没见过的提示：{text[-60:]}"}


# --------------------------------------------------------------------------- #
# 坐标：全部在 COM 工作线程里算
# --------------------------------------------------------------------------- #
def _targets_impl(spec: dict[str, Any]) -> dict[str, Any]:
    """视图变换 + 句柄 → 世界点 → 屏幕点。

    Document / entity 都是工作线程里的代理，拿回主线程再读属性会报
    “应用程序调用一个已为另一线程整理的接口”，所以整段必须留在同一线程。
    `spec["state"]` 可以直接给一个外部算好的变换（ZOOM W 之后用它自算）。
    """
    doc = acad_conn._active_document()
    state = spec.get("state") or screen_map.view_state_for_doc(doc, int(spec["hwnd"]))
    if not state:
        return {"ok": False, "error": "读不到视图变换（先 ZOOM 全图）"}
    found = gui_select._lookup(spec.get("targets") or [])
    out: list[dict[str, Any]] = []
    for handle in spec.get("targets") or []:
        ent = found.get(str(handle).strip().upper())
        if ent is None:
            out.append({"handle": handle, "screen": None, "error": "模型空间里没有这个句柄"})
            continue
        geom = gui_select._geometry_of(ent)
        world = gui_select.pick_point_from_geometry(geom["kind"], geom)
        if world is None:
            out.append({"handle": handle, "screen": None, "error": "算不出可点位置"})
            continue
        screen = screen_map.world_to_screen(state, world[0], world[1])
        item = {"handle": handle, "kind": geom["kind"], "world": [world[0], world[1]],
                "bbox": geom.get("bbox"), "screen": list(screen)}
        box = geom.get("bbox")
        pad = float(spec.get("window_pad") or 3.0)
        if box:
            c1 = (float(box[0][0]) - pad, float(box[0][1]) - pad)
            c2 = (float(box[1][0]) + pad, float(box[1][1]) + pad)
            corners = [list(screen_map.world_to_screen(state, *c1)),
                       list(screen_map.world_to_screen(state, *c2))]
            # 角点必须落在绘图窗内，否则点到状态栏/工具栏上，命令白跑一趟；
            # 同时要离对象本身再退开几个像素——实测角点压在文字笔画上时，
            # 天正报“窗口说明无效”，整条命令就卡住了。
            margin = 10
            grow = 12
            cx = (corners[0][0] + corners[1][0]) / 2.0
            cy = (corners[0][1] + corners[1][1]) / 2.0
            for corner in corners:
                corner[0] += grow if corner[0] >= cx else -grow
                corner[1] += grow if corner[1] >= cy else -grow
            x_lo, x_hi = state["left"] + margin, state["left"] + state["width"] - margin
            y_lo, y_hi = state["top"] + margin, state["top"] + state["height"] - margin
            for corner in corners:
                corner[0] = int(min(max(corner[0], x_lo), x_hi))
                corner[1] = int(min(max(corner[1], y_lo), y_hi))
            item["corners"] = corners
            item["corners_clamped"] = True
        out.append(item)
    points: list[dict[str, Any]] = []
    for world in spec.get("points") or []:
        wx, wy = float(world[0]), float(world[1])
        points.append({"world": [wx, wy],
                       "screen": list(screen_map.world_to_screen(state, wx, wy))})
    return {"ok": True, "targets": out, "points": points,
            "view": {"left": state["left"], "top": state["top"],
                     "width": state["width"], "height": state["height"],
                     "scale": round(state["scale"], 6)}}


def _plan_run(hwnd: int, handles: list[str],
              points: list[list[float]] | None = None,
              window_pad: float = 3.0,
              state: dict[str, Any] | None = None) -> dict[str, Any]:
    return acad_conn._com(_targets_impl,
                          {"hwnd": int(hwnd), "targets": list(handles or []),
                           "points": [list(p) for p in (points or [])],
                           "window_pad": float(window_pad),
                           "state": state}, timeout=90)


def _zoom_impl(spec: dict[str, Any]) -> dict[str, Any]:
    """把视图缩放到这些对象所在的范围。

    实测：`Application.ZoomWindow` 返回成功但**画面不动**；`SendCommand("_.ZOOM …")`
    会停在"指定窗口的角点"上等鼠标（同步调用直接堵死）。真正有效的是直接设
    `ActiveViewport.Center`（二维数组）和 `Height`（世界单位）——纯属性赋值，不触发
    任何交互命令。放大后一个房间从十几像素变成两百多像素，点击才可靠。
    """
    doc = acad_conn._active_document()
    found = gui_select._lookup(spec.get("targets") or [])
    lo = [float("inf"), float("inf")]
    hi = [float("-inf"), float("-inf")]
    for ent in found.values():
        try:
            b = ent.GetBoundingBox()
        except Exception:  # noqa: BLE001
            continue
        lo[0] = min(lo[0], float(b[0][0]))
        lo[1] = min(lo[1], float(b[0][1]))
        hi[0] = max(hi[0], float(b[1][0]))
        hi[1] = max(hi[1], float(b[1][1]))
    if not found or lo[0] == float("inf"):
        return {"ok": False, "error": "找不到要缩放的目标"}

    import pythoncom
    import win32com.client as win32com

    pad = float(spec.get("pad") or 0.35)
    w = max(hi[0] - lo[0], 1.0) * (1.0 + pad * 2)
    h = max(hi[1] - lo[1], 1.0) * (1.0 + pad * 2)
    cx = (lo[0] + hi[0]) / 2.0
    cy = (lo[1] + hi[1]) / 2.0
    try:
        vp = doc.ActiveViewport
        vp.Center = win32com.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8,
                                     (float(cx), float(cy)))
        vp.Height = float(h)
        height = float(vp.Height)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"设置视图失败：{exc}"}
    return {"ok": True, "center": [cx, cy], "height": height,
            "span": [w, h], "entities": len(found)}


def zoom_to_targets(handles: list[str], pad: float = 0.35) -> dict[str, Any]:
    return acad_conn._com(_zoom_impl, {"targets": list(handles), "pad": float(pad)},
                          timeout=90)


def _pane_rect(hwnd: int) -> dict[str, Any]:
    return acad_conn._com(
        lambda: screen_map.view_state(
            acad_conn._active_document(),
            screen_map.doc_view_window(acad_conn._active_document(), int(hwnd)) or 0),
        timeout=60) or {}


def zoom_window_kb(p1: list[float], p2: list[float],
                   settle: float = 0.6) -> dict[str, Any]:
    """用键盘走 `ZOOM W`，并用"窗口 + 窗格像素"自算屏幕变换。

    为什么不用视图属性/`ZoomWindow`：实测 `ActiveViewport.Center/Height` 与
    `Application.ZoomWindow` 都能"设置成功"，但画面并不会跟着变，按它们算出来的
    屏幕点在放大后全是错的。`ZOOM W` 是 AutoCAD 自己的命令，输入两点坐标即可
    （不需要鼠标），缩放后的画面严格等于给定的窗口，于是变换可以自己算：
        scale = min(窗格宽/世界宽, 窗格高/世界高)
    返回的 state 直接喂给 `_plan_run(..., state=...)`。
    """
    app = acad_conn._attach_app()
    hwnd = gui_select._hwnd_of(app)
    pane = _pane_rect(hwnd)
    if not pane.get("width") or not pane.get("height"):
        return {"ok": False, "error": "读不到绘图窗格的像素尺寸"}
    sendinput.ensure_focus(hwnd)
    sendinput.focus_drawing_view(hwnd)
    sendinput.send_escape(1, settle=0.15)

    x1, y1 = float(p1[0]), float(p1[1])
    x2, y2 = float(p2[0]), float(p2[1])
    lo_x, hi_x = min(x1, x2), max(x1, x2)
    lo_y, hi_y = min(y1, y2), max(y1, y2)
    if hi_x - lo_x < 1e-6 or hi_y - lo_y < 1e-6:
        return {"ok": False, "error": "缩放窗口太小"}

    sendinput.type_unicode("_.ZOOM", settle=0.03)
    sendinput.press_key(ENTER_VK, settle=0.45)
    time.sleep(0.3)
    sendinput.type_unicode("W", settle=0.03)
    sendinput.press_key(ENTER_VK, settle=0.45)
    time.sleep(0.3)
    sendinput.type_unicode(f"{lo_x:g},{lo_y:g}", settle=0.03)
    sendinput.press_key(ENTER_VK, settle=0.45)
    time.sleep(0.3)
    sendinput.type_unicode(f"{hi_x:g},{hi_y:g}", settle=0.03)
    sendinput.press_key(ENTER_VK, settle=settle)
    time.sleep(0.4)

    w_px, h_px = float(pane["width"]), float(pane["height"])
    dx, dy = hi_x - lo_x, hi_y - lo_y
    scale = min(w_px / dx, h_px / dy)
    return {"ok": True,
            "state": {"left": pane["left"], "top": pane["top"],
                      "width": pane["width"], "height": pane["height"],
                      "centre": [(lo_x + hi_x) / 2.0, (lo_y + hi_y) / 2.0],
                      "viewsize": h_px / scale, "scale": scale,
                      "source": "zoom-w-computed"},
            "rect": [lo_x, lo_y, hi_x, hi_y], "scale": scale}


def zoom_to_handles(handles: list[str], fill: float = 0.55,
                    pad: float | None = None) -> dict[str, Any]:
    """缩放到这些对象的包围盒，并返回自算的屏幕变换。

    fill 是"对象占窗格的比例"：0.55 表示对象大约占一半，四周留白。
    这一点很关键——如果按 bbox 精确铺满窗格（fill≈1），框选的角点就落到画布
    之外，鼠标根本点不到（实测报"窗口说明无效"）。留白既保证文字够大能点中，
    又留出可以落角点的空间。
    """
    boxes = acad_conn._com(_bbox_union_impl, {"targets": list(handles)}, timeout=90)
    if not boxes.get("ok"):
        return boxes
    pane = _pane_rect(gui_select._hwnd_of(acad_conn._attach_app()))
    if not pane.get("width") or not pane.get("height"):
        return {"ok": False, "error": "读不到绘图窗格的像素尺寸"}
    lo, hi = boxes["lo"], boxes["hi"]
    cx, cy = (float(lo[0]) + float(hi[0])) / 2.0, (float(lo[1]) + float(hi[1])) / 2.0
    dx = max(float(hi[0]) - float(lo[0]), 1.0)
    dy = max(float(hi[1]) - float(lo[1]), 1.0)
    ratio = max(0.05, min(0.95, float(fill if pad is None else 1.0 / (1.0 + float(pad))))
                ) if pad is not None else max(0.05, min(0.95, float(fill)))
    base = min(float(pane["width"]) / dx, float(pane["height"]) / dy)
    scale = base * ratio
    half_w = float(pane["width"]) / (2.0 * scale)
    half_h = float(pane["height"]) / (2.0 * scale)
    out = zoom_window_kb([cx - half_w, cy - half_h], [cx + half_w, cy + half_h])
    out["bbox"] = {"lo": lo, "hi": hi}
    out["fill"] = round(ratio, 3)
    return out


def _bbox_union_impl(spec: dict[str, Any]) -> dict[str, Any]:
    """多个对象（或整张图）的包围盒并集。targets 为空时取模型空间全部实体。"""
    targets = list(spec.get("targets") or [])
    if targets:
        found = gui_select._lookup(targets)
        ents = list(found.values())
    else:
        doc = acad_conn._active_document()
        msp = doc.ModelSpace
        ents = [msp.Item(i) for i in range(int(msp.Count))]
    lo = [float("inf"), float("inf")]
    hi = [float("-inf"), float("-inf")]
    for ent in ents:
        try:
            b = ent.GetBoundingBox()
        except Exception:  # noqa: BLE001
            continue
        lo[0] = min(lo[0], float(b[0][0]))
        lo[1] = min(lo[1], float(b[0][1]))
        hi[0] = max(hi[0], float(b[1][0]))
        hi[1] = max(hi[1], float(b[1][1]))
    if lo[0] == float("inf"):
        return {"ok": False, "error": "找不到要缩放的目标"}
    return {"ok": True, "lo": lo, "hi": hi, "entities": len(ents)}


def zoom_extents_kb(fill: float = 0.85,
                    handles: list[str] | None = None) -> dict[str, Any]:
    """等效 `ZOOM E`：把整张图（或给定对象）按画布 `fill` 的比例铺开。

    为什么不是"缩放到对象": 对象一旦被放大到铺满窗格，框选的两个角点就落到
    画布之外，鼠标点不到（实测报"窗口说明无效"）；而且画面里只剩几笔文字，
    实体边界全被裁掉。取整张图的 extents，建筑轮廓、表格都在视野内，窗交/窗口
    选择才有落脚点。
    """
    boxes = acad_conn._com(_bbox_union_impl,
                           {"targets": list(handles or [])}, timeout=90)
    if not boxes.get("ok"):
        return boxes
    pane = _pane_rect(gui_select._hwnd_of(acad_conn._attach_app()))
    if not pane.get("width") or not pane.get("height"):
        return {"ok": False, "error": "读不到绘图窗格的像素尺寸"}
    lo, hi = boxes["lo"], boxes["hi"]
    cx, cy = (float(lo[0]) + float(hi[0])) / 2.0, (float(lo[1]) + float(hi[1])) / 2.0
    dx = max(float(hi[0]) - float(lo[0]), 1.0)
    dy = max(float(hi[1]) - float(lo[1]), 1.0)
    ratio = max(0.05, min(0.95, float(fill)))
    base = min(float(pane["width"]) / dx, float(pane["height"]) / dy)
    scale = base * ratio
    half_w = float(pane["width"]) / (2.0 * scale)
    half_h = float(pane["height"]) / (2.0 * scale)
    out = zoom_window_kb([cx - half_w, cy - half_h], [cx + half_w, cy + half_h])
    out["bbox"] = {"lo": lo, "hi": hi}
    out["fill"] = round(ratio, 3)
    return out


def _ping_impl(_spec: dict[str, Any]) -> dict[str, Any]:
    """探活：读一个最便宜的量。**故意不吞异常** —— 被拒就让它抛上去。

    （`gui_select._count_impl` 内部 try/except 把 RPC_E_CALL_REJECTED 当 0 处理，
    拿它当探针会永远"成功"，实测踩过这个坑。）
    """
    return {"count": int(acad_conn._active_document().ModelSpace.Count)}


def ensure_ready(hwnd: int, attempts: int = 8, settle: float = 0.6) -> dict[str, Any]:
    """把 CAD 退回"没有命令在等输入"的干净状态。

    天正好几条命令（轴网插入、墙体连续画、标注循环）做完一件事会停在自己的
    循环里继续问"再点一个"，必须真按 ESC 才退出来；不退出来 COM 全被拒，
    后面的调用就一直超时。这里先走 SendInput 的 ESC，退不掉就直接往窗口塞
    ESC，最后用 `_ping_impl` 确认真的通了。
    """
    sendinput.ensure_focus(hwnd)
    sendinput.focus_drawing_view(hwnd)
    view = sendinput.drawing_view_window(hwnd)
    for i in range(max(1, attempts)):
        try:
            acad_conn._com(_ping_impl, {}, timeout=5.0, transient=True)
            return {"ok": True, "tries": i}
        except Exception:  # noqa: BLE001 - 被拒/超时都等于"还没退干净"
            if i < 3:
                sendinput.send_escape(2, settle=0.3)
            else:
                for target in (hwnd, view):
                    if target:
                        winmsg.post_key(int(target), 0x1B)
                time.sleep(settle * 0.6)
        time.sleep(settle)
    return {"ok": False, "tries": attempts}


def _centre_impl(spec: dict[str, Any]) -> dict[str, Any]:
    """对象包围盒中心。只用于"给坐标"的提示，不能用于"点选对象"。"""
    found = gui_select._lookup([spec["handle"]])
    ent = found.get(str(spec["handle"]).upper())
    if ent is None:
        return {"ok": False, "error": "模型空间里没有这个句柄"}
    geom = gui_select._geometry_of(ent)
    centre = gui_select._bbox_centre(geom.get("bbox"))
    if centre is None:
        return {"ok": False, "error": "这个对象没有包围盒"}
    return {"ok": True, "kind": geom.get("kind"), "world": [centre[0], centre[1]]}


def centre_of(handle: str) -> dict[str, Any]:
    """句柄 → 包围盒中心（世界坐标）。坐标输入用它，点选对象用 `pick_point_from_geometry`。"""
    return acad_conn._com(_centre_impl, {"handle": handle}, timeout=90)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run_prompt_driven(command: str,
                      handles: list[str] | None = None,
                      points: list[list[float]] | None = None,
                      answers: list[str] | None = None,
                      pick_points: list[list[float]] | None = None,
                      verify_type: str = "", expect_delta: int = 1,
                      select_mode: str = "point", window_pad: float = 3.0,
                      select_keyword: str = "",
                      confirm_enter: bool = True,
                      zoom_pad: float = 0.0,
                      zoom_fill: float = 0.55,
                      zoom_window: list[list[float]] | None = None,
                      settle: float = 0.45,
                      extra_enters: int = 3,
                      deadline: float = 80.0) -> dict[str, Any]:
    """跑一条需要鼠标的天正命令。

    参数
      handles  依次要"点选"的对象句柄（会换算成屏幕坐标真点）
      points   依次要"指定"的世界坐标点（插入点 / 标注位置 / 墙体基线点…）
      answers  依次要"输入"的文本（表头、编号前缀、数值…）
      pick_points  已验过的"能点中对象"的世界坐标点（房间这类只有文字可点的对象用它）
      expect_delta 命令结束后实体增量至少要这么多（0 表示只要求跑完）
    """
    if not gui_select.gui_enabled():
        raise acad_conn.AcadError(
            f"宿主没有桌面控制能力（{gui_select.GUI_ENV} 未开启）。这条命令需要真实鼠标。")
    if not command.strip():
        raise acad_conn.AcadError("command 不能为空")

    app = acad_conn._attach_app()
    hwnd = gui_select._hwnd_of(app)
    pid = gui_select._pid_of(hwnd)

    if zoom_window:
        # 指定世界窗口：用来把"还没有实体的插入点"也一起框进视野
        zoom = zoom_window_kb(zoom_window[0], zoom_window[1])
        time.sleep(0.4)
        state = zoom.get("state") if zoom.get("ok") else None
    elif (zoom_pad or zoom_fill) and handles:
        zoom = zoom_extents_kb(fill=zoom_fill if not zoom_pad else 1.0 / (1.0 + zoom_pad))
        time.sleep(0.4)
        state = zoom.get("state") if zoom.get("ok") else None
    else:
        state = None

    coord_points = list(points or [])
    pick_list = list(pick_points or [])
    plan = _plan_run(hwnd, handles or [], coord_points + pick_list, window_pad,
                     state=state)
    if not plan.get("ok"):
        return {"ok": False, "stage": "plan", "plan": plan,
                "message": plan.get("error") or "算不出屏幕坐标，先 ZOOM 全图或检查句柄。"}
    targets: list[dict[str, Any]] = list(plan["targets"])
    all_spots: list[dict[str, Any]] = list(plan["points"])
    spots = all_spots[:len(coord_points)]
    picks = all_spots[len(coord_points):]
    broken = [t for t in targets if not t.get("screen")]
    if broken:
        return {"ok": False, "stage": "plan", "targets": targets,
                "message": "有点算不出屏幕坐标，先 ZOOM 全图或检查句柄。"}

    before = acad_conn._com(gui_select._count_impl, {"kind": verify_type})
    start_size = winmsg.command_output_size(pid)

    def pending() -> bool:
        """命令还挂着等输入吗？等输入时 AutoCAD 会拒掉所有 COM 调用（实测稳定成立）。"""
        try:
            acad_conn._com(_ping_impl, {}, timeout=3.0, transient=True)
            return False
        except Exception:  # noqa: BLE001 - 超时/被拒都等于"还在等"
            return True

    def finished(grace: float = 2.4, poll: float = 0.4) -> bool:
        """等命令收尾：一段时间内只要出现过一次"COM 不再被拒"，就算结束。"""
        for _ in range(max(2, int(grace / poll))):
            time.sleep(poll)
            if not pending():
                return True
        return False

    steps: list[dict[str, Any]] = []
    status = "unknown"
    started = time.time()

    def act(kind: str, payload: Any) -> None:
        if kind in ("click", "point", "corner"):
            px, py = payload["screen"]
            sendinput.click(int(px), int(py), settle=settle)
            steps.append({"action": kind, "target": payload})
        elif kind == "enter":
            sendinput.press_key(ENTER_VK, settle=settle)
            steps.append({"action": "enter"})
        elif kind == "text":
            sendinput.type_unicode(str(payload), settle=0.04)
            sendinput.press_key(ENTER_VK, settle=settle)
            steps.append({"action": "text", "value": payload})

    sendinput.ensure_focus(hwnd)
    sendinput.focus_drawing_view(hwnd)
    time.sleep(settle)
    sendinput.type_unicode(command.strip(), settle=0.05)
    sendinput.press_key(ENTER_VK, settle=0.2)
    if select_keyword:
        # 有些命令的选择要先进子模式（例如面积统计要先 C 切到窗交）
        time.sleep(0.35)
        sendinput.type_unicode(select_keyword.strip(), settle=0.05)
        sendinput.press_key(ENTER_VK, settle=0.35)
        steps.append({"action": "text", "value": select_keyword.strip()})

    # 默认顺序：点对象 → 回车确认选择 → 点坐标 → 打字
    if select_mode == "window":
        script: list[tuple[str, Any]] = []
        for target in targets:
            corners = target.get("corners")
            if corners:
                script.append(("corner", {"screen": corners[0]}))
                script.append(("corner", {"screen": corners[1]}))
            else:
                script.append(("click", target))
    else:
        script = [("click", t) for t in targets] + [("click", p) for p in picks]
    if targets or picks:
        if confirm_enter:
            script.append(("enter", None))
    script += [("point", s) for s in spots]
    script += [("text", a) for a in (answers or [])]
    if not targets and not spots and not answers:
        script.append(("enter", None))

    time.sleep(0.4)
    started_cmd = False
    for _ in range(14):
        time.sleep(0.4)
        if pending():
            started_cmd = True
            break
    if not started_cmd:
        status = "no-command"
    else:
        for kind, payload in script:
            if time.time() - started > deadline:
                status = "timeout"
                break
            act(kind, payload)
            if finished():
                status = "done"
                break
        if status == "unknown":
            # 脚本走完还挂着：多半在问一个我们没有素材的问题，回车取默认
            for _ in range(max(1, extra_enters)):
                if finished(grace=1.2):
                    status = "done"
                    break
                if time.time() - started > deadline:
                    status = "timeout"
                    break
                act("enter", None)
            if status == "unknown":
                status = "done" if not pending() else "still-busy"

    if status in ("still-busy", "timeout", "no-command"):
        sendinput.send_escape(2, settle=0.2)

    after = acad_conn._com(gui_select._count_impl, {"kind": verify_type})
    tail_after = winmsg.command_tail(pid, 600)
    segments = [s.strip() for s in tail_after.split("命令:") if s.strip()]
    transcript = segments[-1] if segments else ""
    total_delta = int(after.get("total", 0)) - int(before.get("total", 0))
    typed_delta = None
    if verify_type and after.get("typed") is not None and before.get("typed") is not None:
        typed_delta = int(after["typed"]) - int(before["typed"])
    measured = typed_delta if typed_delta is not None else total_delta

    ok = status == "done" and measured >= expect_delta
    return {
        "ok": bool(ok),
        "status": status,
        "command": command,
        "steps": steps,
        "transcript": transcript,
        "before": before,
        "after": after,
        "total_delta": total_delta,
        "typed_delta": typed_delta,
        "output_growth": winmsg.command_output_size(pid) - start_size,
        "tail_after": tail_after,
        "message": None if ok else _explain(status, measured, expect_delta, transcript),
    }


def _explain(status: str, measured: int, expect: int, transcript: str) -> str:
    if status == "no-command":
        return "命令没起来（可能没有这个命令名，或天正 ARX 没加载）"
    if status == "still-busy":
        return ("命令还在等输入，回车也推不动 —— 多半是要『精确点在某对象上』"
                f"或者弹了对话框。最后一段：{transcript[-120:]}")
    if status == "timeout":
        return f"超时了，最后一段：{transcript[-120:]}"
    if status == "done":
        return f"命令跑完了，但实体只增加了 {measured} 个（预期 {expect}）。看 tail_after。"
    return f"没跑完（{status}）：{transcript[-120:]}"
