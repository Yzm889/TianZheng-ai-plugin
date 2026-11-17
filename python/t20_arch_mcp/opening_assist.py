"""窗标记直接画在墙里，门的标记放墙外。

用户的判断（正确）：
  · 窗对象很扁，位置就在墙面上 -> 标记直接画在墙里，对着画就行
  · 门有开启弧，包围盒大，墙里画什么东西都会干扰 -> 标记放墙外

所以 mark_on_drawing 对两种类型用完全不同的画法：
  窗：在墙体内画一个"口"字框（左右两条竖线标出宽度边界 + 上下两条横线
      标出墙厚范围），用户照着框的中间点
  门：墙外引线箭头 + 序号
"""
from __future__ import annotations

from typing import Any

LAYER = "AUX-OPENING"
DEGENERATE_HEIGHT = 100.0
WALL_T = 200.0          # drawing wall thickness


def make_plan(items: list[dict[str, Any]]) -> dict[str, Any]:
    seq = []
    dn = wn = 0
    for i, it in enumerate(items, 1):
        kind = "window" if str(it.get("kind", "door")).lower().startswith("win") else "door"
        if kind == "window":
            wn += 1
            label = it.get("label") or "C%d" % wn
            tip = "窗在墙上画了方框，点框的中间"
        else:
            dn += 1
            label = it.get("label") or "M%d" % dn
            tip = "门用墙外箭头指引，点箭头指向的墙面"
        seq.append({
            "step": i, "kind": kind,
            "kind_cn": "窗" if kind == "window" else "门",
            "x": round(float(it["x"]), 1), "y": round(float(it["y"]), 1),
            "width": it.get("width"), "label": label, "tip": tip,
        })
    return {
        "ok": True, "count": len(seq), "doors": dn, "windows": wn, "steps": seq,
        "how_to": [
            "窗：墙上已经画好方框，点框中间即可。",
            "门：建筑外侧有箭头指向该点的墙面。",
            "标记只是位置参考，具体点在哪由你自己判断，只要落在墙面上就行。",
            "输入 MC 回车；门直接回车，窗先点【窗】图标再回车。",
            "按序号点完所有位置，回车结束。",
        ],
        "note": "标记只是位置参考，不参与捕捉。",
    }


def mark_on_drawing(items: list[dict[str, Any]], offset: float = 900.0,
                    text_height: float = 300.0, timeout: float = 180.0) -> dict[str, Any]:
    from .acad_conn import _com

    return _com(_mark_impl, list(items), float(offset), float(text_height), timeout=timeout)


def _mark_impl(items: list[dict[str, Any]], offset: float, text_height: float) -> dict[str, Any]:
    import pythoncom
    from win32com.client import VARIANT
    from .acad_conn import _active_document

    doc = _active_document()
    try:
        doc.Layers.Item(LAYER)
    except Exception:
        doc.Layers.Add(LAYER)
    try:
        doc.Layers.Item(LAYER).color = 6
    except Exception:
        pass

    def P(x, y):
        return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, (float(x), float(y), 0.0))

    msp = doc.ModelSpace
    n_win = n_door = 0

    for i, it in enumerate(items, 1):
        try:
            x, y = float(it["x"]), float(it["y"])
            is_window = str(it.get("kind", "door")).lower().startswith("win")
            width = float(it.get("width") or (1800 if is_window else 1500))
            half = width / 2.0
            direction = -1 if y > 9000 else 1

            if is_window:
                # ---- draw a box INSIDE the wall showing the exact opening
                t = WALL_T / 2.0
                # left / right boundaries (vertical)
                msp.AddLine(P(x - half, y - t), P(x - half, y + t)).Layer = LAYER
                msp.AddLine(P(x + half, y - t), P(x + half, y + t)).Layer = LAYER
                # top / bottom (horizontal)
                msp.AddLine(P(x - half, y - t), P(x + half, y - t)).Layer = LAYER
                msp.AddLine(P(x - half, y + t), P(x + half, y + t)).Layer = LAYER
                # number just outside the wall
                tx = x - text_height * 0.32
                ty = y + direction * (t + text_height * 0.4)
                txt = msp.AddText(str(i), P(tx, ty), float(text_height))
                txt.Layer = LAYER
                n_win += 1
            else:
                # ---- door: leader arrow from outside, never touching the wall
                my = y + direction * offset
                ln = msp.AddLine(P(x, my), P(x, y + direction * 420))
                ln.Layer = LAYER
                tip_y = y + direction * 420
                base_y = tip_y - direction * 200
                for dx in (-80, 80):
                    a = msp.AddLine(P(x, tip_y), P(x + dx, base_y))
                    a.Layer = LAYER
                t2 = msp.AddText(str(i), P(x - text_height * 0.32, my + direction * 90),
                                 float(text_height))
                t2.Layer = LAYER
                n_door += 1
        except Exception:
            continue

    return {
        "ok": (n_win + n_door) > 0,
        "marked": n_win + n_door,
        "windows_in_wall": n_win,
        "doors_outside": n_door,
        "layer": LAYER,
        "note": "窗在墙里画了方框（点框中间）；门用墙外箭头指引。",
    }


def clear_marks(timeout: float = 120.0) -> dict[str, Any]:
    from .acad_conn import _com

    return _com(_clear_impl, timeout=timeout)


def _clear_impl() -> dict[str, Any]:
    from .acad_conn import _active_document

    doc = _active_document()
    removed = 0
    msp = doc.ModelSpace
    for i in range(msp.Count - 1, -1, -1):
        try:
            e = msp.Item(i)
            if str(getattr(e, "Layer", "")) == LAYER:
                e.Delete()
                removed += 1
        except Exception:
            continue
    return {"ok": True, "removed": removed}


def verify(items: list[dict[str, Any]], tolerance: float = 600.0,
           timeout: float = 180.0) -> dict[str, Any]:
    from .acad_conn import _com

    return _com(_verify_impl, list(items), float(tolerance), timeout=timeout)


def _verify_impl(items: list[dict[str, Any]], tolerance: float) -> dict[str, Any]:
    from .acad_conn import _active_document

    doc = _active_document()
    msp = doc.ModelSpace

    openings = []
    for i in range(msp.Count):
        try:
            e = msp.Item(i)
            if "OPENING" not in str(e.ObjectName).upper():
                continue
            mn, mx = e.GetBoundingBox()
            h = mx[1] - mn[1]
            openings.append({"handle": str(e.Handle),
                             "bbox": (mn[0], mn[1], mx[0], mx[1]),
                             "height": h, "healthy": h > DEGENERATE_HEIGHT})
        except Exception:
            continue

    results = []
    for i, it in enumerate(items, 1):
        x, y = float(it["x"]), float(it["y"])
        hit = None
        for o in openings:
            mn0, mn1, mx0, mx1 = o["bbox"]
            if (mn0 - tolerance <= x <= mx0 + tolerance
                    and mn1 - tolerance <= y <= mx1 + tolerance):
                hit = o
                break
        results.append({"step": i, "label": it.get("label") or i,
                        "found": hit is not None,
                        "healthy": bool(hit and hit["healthy"]),
                        "handle": hit["handle"] if hit else None})

    found_n = sum(1 for r in results if r["found"])
    degenerate = sum(1 for o in openings if not o["healthy"])
    return {
        "ok": found_n == len(items) and degenerate == 0,
        "expected": len(items), "found": found_n, "degenerate_objects": degenerate,
        "missing_steps": [r["step"] for r in results if not r["found"]],
        "unhealthy_steps": [r["step"] for r in results if r["found"] and not r["healthy"]],
        "results": results,
    }
