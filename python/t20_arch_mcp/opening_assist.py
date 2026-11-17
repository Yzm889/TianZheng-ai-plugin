"""opening_assist.py —— 插入门窗助手。

实测结论：
  天正门窗只有在 MC 里【鼠标点选插入到墙上】时才会与墙建立宿主关系并自动
  开洞；COM 复制的门窗外观对但墙不开洞，TValidOp 也修不了。

所以做成"半自动"：插件算好每个插入点（落在墙轴心线上）并在图上画【可捕捉】
的锚点，用户执行一次 MC 照着点，插件再核对。

锚点为什么可捕捉：
  · 一条 ±grip_len 的短竖线，【中点】就是插入点 -> 用中点捕捉精确命中
  · 一个 POINT 对象 -> 用节点捕捉
  · 小圆 + 序号文字只作视觉提示
"""
from __future__ import annotations

from typing import Any

LAYER = "AUX-OPENING"


# ---------------------------------------------------------------- plan
def make_plan(items: list[dict[str, Any]]) -> dict[str, Any]:
    """把门窗清单整理成带序号的点击序列。"""
    seq = []
    dn = wn = 0
    for i, it in enumerate(items, 1):
        kind = "window" if str(it.get("kind", "door")).lower().startswith("win") else "door"
        if kind == "window":
            wn += 1
            default_label = "C%d" % wn
        else:
            dn += 1
            default_label = "M%d" % dn
        seq.append({
            "step": i,
            "kind": kind,
            "kind_cn": "窗" if kind == "window" else "门",
            "x": round(float(it["x"]), 1),
            "y": round(float(it["y"]), 1),
            "width": it.get("width"),
            "label": it.get("label") or default_label,
        })
    return {
        "ok": True,
        "count": len(seq),
        "doors": dn,
        "windows": wn,
        "steps": seq,
        "how_to": [
            "先在 CAD 里开捕捉：输入 OSNAP 回车，勾选【中点】和【节点】，确定。",
            "输入 MC 回车，弹出「门窗参数」对话框。",
            "门：直接回车确认（默认就是门）。",
            "窗：先点对话框底部工具栏第 2 个【窗】图标，再回车。",
            "鼠标移到锚点短线的中间，出现「中点」提示时点击。",
            "按清单序号依次点完全部门窗，每点一个墙会自动开洞。",
            "全部点完后回车结束 MC。",
            "最后调用 verify 核对数量，用 clear_marks 删除辅助标记。",
        ],
        "why_manual": (
            "MC 的插入点只接受鼠标点选：输入坐标会被当成命令并报「未知命令」。"
            "所以这一步无法自动化，但插件已经把「算点位」「画锚点」「核对结果」都做好了。"
        ),
        "note": "插入点必须落在墙轴心线上（锚点已算好），墙才会自动开洞。",
    }


# ---------------------------------------------------------------- marks
def mark_on_drawing(items: list[dict[str, Any]], text_height: float = 260.0,
                    grip_len: float = 60.0, timeout: float = 180.0) -> dict[str, Any]:
    from .acad_conn import _com

    return _com(_mark_impl, list(items), float(text_height), float(grip_len), timeout=timeout)


def _mark_impl(items: list[dict[str, Any]], text_height: float, grip_len: float) -> dict[str, Any]:
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
    made = 0
    for i, it in enumerate(items, 1):
        try:
            x, y = float(it["x"]), float(it["y"])
            # snappable anchor: short vertical line whose MIDPOINT is the target
            ln = msp.AddLine(P(x, y - grip_len), P(x, y + grip_len))
            ln.Layer = LAYER
            # node point
            try:
                p = msp.AddPoint(P(x, y))
                p.Layer = LAYER
            except Exception:
                pass
            # visual ring + number, offset so it does not sit on the anchor
            try:
                c = msp.AddCircle(P(x, y), grip_len * 3.5)
                c.Layer = LAYER
            except Exception:
                pass
            t = msp.AddText(str(i), P(x + grip_len * 4, y + text_height * 1.6), float(text_height))
            t.Layer = LAYER
            made += 1
        except Exception:
            continue

    return {
        "ok": made > 0,
        "marked": made,
        "layer": LAYER,
        "snap_hint": "用【中点】捕捉短线的中点，或用【节点】捕捉 POINT，即可精确命中墙轴心线。",
        "cleanup": "点选完成后可调用 clear_marks() 删除本图层标记。",
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


# ---------------------------------------------------------------- verify
def verify(items: list[dict[str, Any]], tolerance: float = 400.0,
           timeout: float = 180.0) -> dict[str, Any]:
    from .acad_conn import _com

    return _com(_verify_impl, list(items), float(tolerance), timeout=timeout)


def _verify_impl(items: list[dict[str, Any]], tolerance: float) -> dict[str, Any]:
    from .acad_conn import _active_document

    doc = _active_document()
    msp = doc.ModelSpace
    found = []
    for i in range(msp.Count):
        try:
            e = msp.Item(i)
            if "OPENING" not in str(e.ObjectName).upper():
                continue
            mn, mx = e.GetBoundingBox()
            found.append((str(e.Handle), mn, mx))
        except Exception:
            continue

    results = []
    for i, it in enumerate(items, 1):
        x, y = float(it["x"]), float(it["y"])
        hit = None
        for h, mn, mx in found:
            if (mn[0] - tolerance <= x <= mx[0] + tolerance
                    and mn[1] - tolerance <= y <= mx[1] + tolerance):
                hit = h
                break
        results.append({"step": i, "label": it.get("label") or i,
                        "found": bool(hit), "handle": hit})

    ok_n = sum(1 for r in results if r["found"])
    return {
        "ok": ok_n == len(items),
        "expected": len(items),
        "found": ok_n,
        "missing_steps": [r["step"] for r in results if not r["found"]],
        "results": results,
    }
