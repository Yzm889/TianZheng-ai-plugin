"""opening_assist.py —— 插入门窗助手。

背景（实测结论）：
  天正门窗只有在 MC 命令里【被鼠标点选插入到墙上】时，才会与墙建立宿主关系
  并自动开洞。通过 COM 复制出来的门窗外观正确，但墙不会开洞；TValidOp（门窗
  检查）也无法重建这个关系。
  而 MC 的插入点只接受鼠标点选，无法用坐标自动化。

所以把"能不能自动"降到"让用户点一下就行"：
  1. 插件按规格算好每个门窗的准确插入点（落在墙轴心线上）
  2. 在图上画带编号的标记 + 一张点击清单（序号、坐标、类型、宽度）
  3. 用户执行一次 MC，按编号顺序点完，回车结束
  4. 插件核对数量并报告

这样所有装了天正的机器都能用，不依赖任何第三方 DLL，也不需要逆向。
"""
from __future__ import annotations

import json
from typing import Any

from .acad_conn import _active_document, _com


def make_plan(items: list[dict[str, Any]]) -> dict[str, Any]:
    """把门窗清单整理成可执行的点击序列。

    items: [{"kind": "door"|"window", "x":, "y":, "width":, "label":}]
    返回带序号、坐标、说明的点击清单；坐标是落在墙轴心线上的插入点。
    """
    seq = []
    for i, it in enumerate(items, 1):
        kind = "window" if str(it.get("kind", "door")).lower().startswith("win") else "door"
        seq.append({
            "step": i,
            "kind": kind,
            "kind_cn": "窗" if kind == "window" else "门",
            "x": round(float(it["x"]), 1),
            "y": round(float(it["y"]), 1),
            "width": it.get("width"),
            "label": it.get("label") or ("C%d" % i if kind == "window" else "M%d" % i),
        })
    return {
        "ok": True,
        "count": len(seq),
        "doors": sum(1 for s in seq if s["kind"] == "door"),
        "windows": sum(1 for s in seq if s["kind"] == "window"),
        "steps": seq,
        "how_to": (
            "1) 输入 MC 回车；2) 门：直接回车确认；窗：先点【窗】图标再回车；"
            "3) 按下面清单的顺序，用鼠标依次点每个坐标位置；4) 全部点完后回车结束。"
        ),
        "note": (
            "MC 的插入点只能鼠标点选，这是天正本身的限制；"
            "点的位置必须落在墙轴心线上，墙才会自动开洞。"
        ),
    }


def mark_on_drawing(items: list[dict[str, Any]], text_height: float = 260.0,
                    timeout: float = 180.0) -> dict[str, Any]:
    """在图上画序号标记，方便用户照着点（画在 AUX-OPENING 图层，可一键删除）。"""
    return _com(_mark_impl, list(items), float(text_height), timeout=timeout)


def _mark_impl(items: list[dict[str, Any]], text_height: float) -> dict[str, Any]:
    import pythoncom
    from win32com.client import VARIANT

    doc = _active_document()
    try:
        doc.Layers.Item("AUX-OPENING")
    except Exception:
        doc.Layers.Add("AUX-OPENING")
    try:
        lay = doc.Layers.Item("AUX-OPENING")
        lay.color = 6
    except Exception:
        pass

    def P(x, y):
        return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, (float(x), float(y), 0.0))

    made = 0
    for i, it in enumerate(items, 1):
        try:
            x, y = float(it["x"]), float(it["y"])
            label = str(it.get("label") or i)
            # small cross + number
            doc.ModelSpace.AddLine(P(x - 200, y), P(x + 200, y)).Layer = "AUX-OPENING"
            doc.ModelSpace.AddLine(P(x, y - 200), P(x, y + 200)).Layer = "AUX-OPENING"
            doc.ModelSpace.AddCircle(P(x, y), 420.0).Layer = "AUX-OPENING"
            t = doc.ModelSpace.AddText(str(i), P(x - 120, y + 500), float(text_height))
            t.Layer = "AUX-OPENING"
            made += 1
        except Exception:
            continue
    return {"ok": made > 0, "marked": made, "layer": "AUX-OPENING",
            "hint": "点选完成后可用「清标记」删除本图层的所有辅助标记。"}


def clear_marks(timeout: float = 120.0) -> dict[str, Any]:
    """删除 AUX-OPENING 图层上的所有辅助标记。"""
    return _com(_clear_impl, timeout=timeout)


def _clear_impl() -> dict[str, Any]:
    doc = _active_document()
    removed = 0
    msp = doc.ModelSpace
    for i in range(msp.Count - 1, -1, -1):
        try:
            e = msp.Item(i)
            if str(getattr(e, "Layer", "")) == "AUX-OPENING":
                e.Delete()
                removed += 1
        except Exception:
            continue
    return {"ok": True, "removed": removed}


def verify(items: list[dict[str, Any]], tolerance: float = 300.0,
           timeout: float = 180.0) -> dict[str, Any]:
    """核对每个预期位置是否真的有了门窗对象。"""
    return _com(_verify_impl, list(items), float(tolerance), timeout=timeout)


def _verify_impl(items: list[dict[str, Any]], tolerance: float) -> dict[str, Any]:
    doc = _active_document()
    msp = doc.ModelSpace
    found = []
    for i in range(msp.Count):
        try:
            e = msp.Item(i)
            if "OPENING" not in str(e.ObjectName).upper():
                continue
            mn, mx = e.GetBoundingBox()
            found.append((str(e.Handle), (mn[0] + mx[0]) / 2.0, (mn[1] + mx[1]) / 2.0,
                          mn, mx))
        except Exception:
            continue

    results = []
    hit_handles = set()
    for i, it in enumerate(items, 1):
        x, y = float(it["x"]), float(it["y"])
        best = None
        bestd = 1e18
        for h, cx, cy, mn, mx in found:
            # an opening on a wall axis has its bbox spanning that axis
            if mn[1] - tolerance <= y <= mx[1] + tolerance and mn[0] - tolerance <= x <= mx[0] + tolerance:
                d = abs(cx - x)
                if d < bestd:
                    bestd = d
                    best = h
        if best:
            hit_handles.add(best)
            results.append({"step": i, "label": it.get("label") or i, "found": True,
                            "handle": best, "offset": round(bestd, 1)})
        else:
            results.append({"step": i, "label": it.get("label") or i, "found": False})

    ok_n = sum(1 for r in results if r["found"])
    return {
        "ok": ok_n == len(items),
        "expected": len(items),
        "found": ok_n,
        "missing_steps": [r["step"] for r in results if not r["found"]],
        "results": results,
    }
