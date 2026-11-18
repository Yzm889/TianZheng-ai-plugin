"""opening_library.py —— 门窗模板宽度登记表。

为什么需要它（实测，2026-11-18，Drawing31.dwg 的 4 个 TDbOpening）：

  · `GetXData("TCH"/"TDb"/...)` 全部返回 `[null, null]` —— 宽度不存在 XData 里；
  · `Width` / `Height` / `Rotation` 这些属性在天正对象上读不到；
  · 包围盒也不可靠：1000 宽的窗 bbox 是 1000×975，另一个 900 宽的是 900×877.5
    （高度跟着宽度一起变），所以拿 bbox 猜宽度会猜错。

结论：**宽度只能由人登记一次**，之后按登记值匹配模板。天正的门窗宽度是内部
参数，`ScaleEntity` 只改高度（README #32），所以"不同宽度"只能用不同的模板。

这条记录持久化在 `T20_STATE_DIR`（默认用户目录）下的 JSON 里，重启 MCP 服务后
仍然有效；按图纸名分区，避免 A 图的句柄被 B 图误用。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

DEFAULT_TOLERANCE = 50.0
STATE_ENV = "T20_OPENING_LIBRARY"
STATE_NAME = ".t20_opening_library.json"


def normalize_kind(kind: str | None) -> str:
    """'窗' / 'window' / 'C' 都归一成 window，其余当门。"""
    raw = str(kind or "door").strip().lower()
    if raw in ("window", "win", "w", "窗", "窗户"):
        return "window"
    if raw in ("door", "d", "门", "m"):
        return "door"
    return "window" if raw.startswith("win") else "door"


def _state_file() -> str:
    override = os.environ.get(STATE_ENV)
    if override:
        return override
    base = os.environ.get("T20_STATE_DIR") or os.path.expanduser("~")
    return os.path.join(base, STATE_NAME)


def _load() -> dict[str, Any]:
    try:
        with open(_state_file(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {"entries": []}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return {"entries": []}
    return data


def _save(data: dict[str, Any]) -> None:
    path = _state_file()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _doc_key(document: str | None) -> str | None:
    if not document:
        return None
    return os.path.basename(str(document)).strip().lower() or None


def register(handle: str, width: float, kind: str = "door",
             height: float | None = None, document: str | None = None) -> dict[str, Any]:
    """登记一个门窗模板的真实宽度（同一个句柄重复登记会覆盖）。"""
    handle = str(handle).strip()
    if not handle:
        return {"ok": False, "error": "handle 不能为空"}
    try:
        width = float(width)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"width 不是数字：{width!r}"}
    if width <= 0:
        return {"ok": False, "error": "width 必须大于 0（单位 mm）"}

    entry = {
        "handle": handle,
        "width": round(width, 1),
        "kind": normalize_kind(kind),
        "document": _doc_key(document),
        "registered_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if height is not None:
        try:
            entry["height"] = round(float(height), 1)
        except (TypeError, ValueError):
            pass

    data = _load()
    entries = [e for e in data["entries"]
               if not (str(e.get("handle", "")).upper() == handle.upper()
                       and _doc_key(e.get("document")) == entry["document"])]
    entries.append(entry)
    data["entries"] = entries
    _save(data)
    return {"ok": True, "entry": entry, "total": len(entries), "state_file": _state_file()}


def entries(document: str | None = None) -> list[dict[str, Any]]:
    """列出登记过的模板；给 document 时只返回该图的。"""
    items = _load()["entries"]
    if document is None:
        return list(items)
    key = _doc_key(document)
    return [e for e in items if _doc_key(e.get("document")) == key]


def forget(handle: str, document: str | None = None) -> dict[str, Any]:
    """删掉一条登记（模板不再存在时用）。"""
    handle_upper = str(handle).strip().upper()
    key = _doc_key(document)
    data = _load()
    before = len(data["entries"])
    data["entries"] = [
        e for e in data["entries"]
        if not (str(e.get("handle", "")).upper() == handle_upper
                and (key is None or _doc_key(e.get("document")) == key))
    ]
    removed = before - len(data["entries"])
    if removed:
        _save(data)
    return {"ok": True, "removed": removed, "total": len(data["entries"])}


def as_candidates(value: Any, document: str | None = None) -> list[dict[str, Any]]:
    """把 opening_templates 里的一项归一成 `[{handle, width}]`。

    支持三种写法（都从简到繁兼容）：
        "4C7"                          —— 只有句柄，宽度去登记表里补
        {"handle": "4C7", "width": 1500}
        [{"handle": "4C7", "width": 1500}, {"handle": "4C4", "width": 1000}]
    """
    if value is None:
        return []
    raw_items: list[Any]
    if isinstance(value, (list, tuple)):
        raw_items = list(value)
    else:
        raw_items = [value]

    out: list[dict[str, Any]] = []
    for item in raw_items:
        if isinstance(item, dict):
            handle = item.get("handle")
            width = item.get("width")
        else:
            handle, width = item, None
        if handle is None:
            continue
        handle = str(handle).strip()
        if not handle:
            continue
        if width is None:
            width = width_of(document, handle)
        try:
            width = float(width) if width is not None else None
        except (TypeError, ValueError):
            width = None
        out.append({"handle": handle, "width": width})
    return out


def select(candidates: list[dict[str, Any]], kind: str, width: float | None,
           tolerance: float = DEFAULT_TOLERANCE) -> tuple[dict[str, Any] | None, str]:
    """从候选里挑模板。返回 (候选, 原因)。

    原因取值：
        "matched"      —— 宽度在容差内命中
        "no_width"     —— 调用方没给宽度，退回第一个（宽度未核实）
        "no_match"     —— 给了宽度，但没有登记过这个宽度的模板（不能瞎用）
        "empty"        —— 一个候选都没有
    """
    if not candidates:
        return None, "empty"
    if width is None:
        return candidates[0], "no_width"

    try:
        target = float(width)
        tol = abs(float(tolerance))
    except (TypeError, ValueError):
        return None, "no_match"

    best: dict[str, Any] | None = None
    best_delta: float | None = None
    for cand in candidates:
        cand_width = cand.get("width")
        if cand_width is None:
            continue
        delta = abs(float(cand_width) - target)
        if delta <= tol and (best_delta is None or delta < best_delta):
            best, best_delta = cand, delta
    if best is None:
        return None, "no_match"
    return best, "matched"


def pick(document: str | None, kind: str, width: float,
         tolerance: float = DEFAULT_TOLERANCE) -> dict[str, Any] | None:
    """只在登记表里按「类型 + 宽度」找模板，找不到返回 None。"""
    want = normalize_kind(kind)
    same_kind = [e for e in entries(document) if normalize_kind(e.get("kind")) == want]
    chosen, reason = select(same_kind, want, width, tolerance)
    return chosen if reason == "matched" else None


def width_of(document: str | None, handle: str) -> float | None:
    """查某个句柄登记过的宽度（用来补全调用方只给句柄的情况）。"""
    handle_upper = str(handle).strip().upper()
    for entry in entries(document):
        if str(entry.get("handle", "")).upper() == handle_upper:
            try:
                return float(entry["width"])
            except (TypeError, ValueError):
                return None
    return None
