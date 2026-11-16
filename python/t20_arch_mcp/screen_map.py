"""screen_map.py - convert drawing (world) coordinates to screen pixels.

Why this exists: Tianzheng's selection prompts ("选择对象" / "请选择起点") cannot be
satisfied by typed coordinates or keywords -- the user confirmed objects must be
picked with the mouse. To click an entity we must know where it is on screen, which
needs AutoCAD's current view: view centre, view height in drawing units, and the
pixel size of the drawing window.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any

from .winmsg import DPI_MODE, user32  # noqa: F401  (import sets DPI awareness)




def _as_pair(value: Any) -> tuple[float, float]:
    try:
        return float(value[0]), float(value[1])
    except Exception:
        pass
    try:
        return float(value[0][0]), float(value[0][1])
    except Exception:
        pass
    raise ValueError(f"cannot read a 2D point from {value!r}")


_VIEW_CHILD_HINTS = ("ACADDM_CHILD_DXGI_FLIP_MODE_VIEW", ":28:")


def doc_view_window(doc: Any, fallback_hwnd: int | None = None) -> int | None:
    """The drawing pane INSIDE the MDI document window.

    MEASURED: using the MDI child's own rect (432,229,1257,644) instead of this
    inner pane (441,267,1239,597) shifted every click by ~9 px and ~14 px, which was
    visible as the status-bar coordinate readout disagreeing with the intended
    world point. The inner pane is the window that actually shows the drawing.
    """
    doc_hwnd = None
    try:
        doc_hwnd = int(doc.HWND)
    except Exception:
        doc_hwnd = None

    best, best_area = None, 0
    roots = [doc_hwnd] if doc_hwnd else []
    if not roots and fallback_hwnd:
        roots = [fallback_hwnd]
    for candidate_root in roots:
        stack = [candidate_root]
        depth = 0
        while stack and depth < 3:
            nxt = []
            for root in stack:
                for child in _descendants(root):
                    try:
                        if not user32.IsWindowVisible(wintypes.HWND(child)):
                            continue
                        cls = ctypes.create_unicode_buffer(256)
                        user32.GetClassNameW(wintypes.HWND(child), cls, 256)
                        name = cls.value
                    except Exception:
                        continue
                    rect = wintypes.RECT()
                    user32.GetWindowRect(wintypes.HWND(child), ctypes.byref(rect))
                    area = (rect.right - rect.left) * (rect.bottom - rect.top)
                    if any(hint in name for hint in _VIEW_CHILD_HINTS) and area > best_area:
                        best, best_area = child, area
                    nxt.append(child)
            stack = nxt
            depth += 1
    return best


def _descendants(hwnd: int) -> list[int]:
    out: list[int] = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(h, _l):
        out.append(int(h))
        return True

    try:
        user32.EnumChildWindows(wintypes.HWND(hwnd), proc(cb), 0)
    except Exception:
        pass
    return out


def ctypes_variant_triple(x: float, y: float, z: float):
    from win32com.client import VARIANT
    import pythoncom

    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, (float(x), float(y), float(z)))


def ctypes_variant_pair(x: float, y: float):
    """VARIANT(VT_ARRAY|VT_R8) with exactly two elements (2D point)."""
    from win32com.client import VARIANT  # noqa: F401  (pywin32)
    import pythoncom

    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, (float(x), float(y)))


def view_state_for_doc(doc: Any, fallback_hwnd: int | None = None) -> dict[str, Any]:
    """View transform using the correct inner drawing pane."""
    pane = doc_view_window(doc, fallback_hwnd)
    if not pane:
        return {}
    return view_state(doc, pane)


def drawing_view_rect(view_hwnd: int) -> tuple[int, int, int, int] | None:
    """Screen rect of the drawing view window as (left, top, width, height)."""
    try:
        rect = wintypes.RECT()
        user32.GetWindowRect(wintypes.HWND(view_hwnd), ctypes.byref(rect))
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return None
        return (rect.left, rect.top, width, height)
    except Exception:
        return None


def view_state(doc: Any, view_hwnd: int) -> dict[str, Any]:
    """Current view transform parameters, or {} when they cannot be read.

    Uses ActiveViewport.Center/Height, NOT GetVariable("VIEWCTR"/"VIEWSIZE") --
    measured, the variables disagreed with the real view: VIEWCTR said
    (4198, 3151) while ActiveViewport.Center said (25708, 22590), and clicks
    computed from VIEWCTR landed thousands of units away. The viewport object is
    the authoritative source.
    """
    rect = drawing_view_rect(view_hwnd)
    if not rect:
        return {}
    left, top, width, height = rect

    centre: tuple[float, float] | None = None
    view_height_units: float | None = None
    source = ""
    try:
        vp = doc.ActiveViewport
        centre = _as_pair(vp.Center)
        view_height_units = float(vp.Height)
        source = "ActiveViewport"
    except Exception:
        centre, view_height_units = None, None

    if not centre or not view_height_units or view_height_units <= 0:
        return {}

    scale = height / view_height_units
    return {
        "left": left,
        "top": top,
        "width": width,
        "height": height,
        "centre": centre,
        "viewsize": view_height_units,
        "scale": scale,
        "source": source,
    }


def world_to_screen(state: dict[str, Any], x: float, y: float) -> tuple[int, int] | None:
    """Map a drawing point to a screen pixel, or None if the state is unusable."""
    if not state:
        return None
    cx, cy = state["centre"]
    scale = state["scale"]
    px = state["left"] + state["width"] / 2.0 + (float(x) - cx) * scale
    py = state["top"] + state["height"] / 2.0 - (float(y) - cy) * scale
    return int(round(px)), int(round(py))


def inside_view(state: dict[str, Any], point: tuple[int, int]) -> bool:
    if not state:
        return False
    px, py = point
    return (
        state["left"] <= px <= state["left"] + state["width"]
        and state["top"] <= py <= state["top"] + state["height"]
    )


