"""交接清单：把「剩下要手点的活」写成一份带句柄和坐标的清单，直接交给带 GUI 的 AI。

为什么需要它：纯文本宿主（DeepSeek/DSH）做完轴网墙体这些之后，剩下的点选类命令它
做不了；如果只丢一句"剩下的你来做"，接手的宿主得重新摸图纸、猜句柄、猜插入点。
这个模块把"已完成 / 还差什么 / 每题的目标句柄 / 建议点位"一次算好，接手方照单执行。

设计上刻意分成两层：
  * `collect_document()`  只碰 CAD（COM 侧采集句柄、类型、图层、包围盒）；
  * `build_handoff()`     纯函数，喂进来的就是普通 dict，方便单测。
"""
from __future__ import annotations

from typing import Any

# kind -> 命令 / 可接受的源对象 / 点位算法 / 期望增量
KIND_SPEC: dict[str, dict[str, Any]] = {
    "radius_dim": {
        "label": "半径标注", "command": "TDimRad",
        "targets": ("AcDbCircle", "AcDbArc"), "point_mode": "dim_side",
        "expect_delta": 1,
    },
    "diameter_dim": {
        "label": "直径标注", "command": "TDimDia",
        "targets": ("AcDbArc",), "point_mode": "dim_above",
        "expect_delta": 1,
        "note": "天正直径标注只认弧（AcDbArc），整圆会被拒；图里没有弧就先画一段或用半径标注",
    },
    "area_stat": {
        "label": "面积统计", "command": "TCountArea",
        "targets": ("TDbSpace",), "point_mode": "table", "expect_delta": 1,
    },
    "hatch_area": {
        "label": "填充面积", "command": "THatchArea",
        "targets": ("AcDbHatch",), "point_mode": "table", "expect_delta": 1,
    },
    "text_frame": {
        "label": "文字加框", "command": "TTextAddFrame",
        "targets": ("AcDbText",), "point_mode": "", "expect_delta": 1,
    },
    "room_sort": {
        "label": "房间排序", "command": "TSortSpace",
        "targets": ("TDbSpace",), "point_mode": "", "expect_delta": 0,
    },
    "wall_split": {
        "label": "墙体分段", "command": "TPartwall",
        "targets": ("TDbWall",), "point_mode": "baseline", "expect_delta": 0,
    },
    "axis_label": {
        "label": "单轴标注", "command": "TSingleAxisDim",
        "targets": ("AcDbLine",), "point_mode": "", "expect_delta": 1,
        "layer": "DOTE",
    },
    "parking_number": {
        "label": "车位编号", "command": "TParkingNumber",
        "targets": (), "point_mode": "", "expect_delta": 1,
        "unsupported": "前置的 TParkingSpace（车位布置）只回“*取消*”不产出实体，图里没有车位可编号",
    },
}

# 中文口语 -> kind
KIND_ALIASES: dict[str, str] = {
    "半径": "radius_dim", "半径标注": "radius_dim", "rad": "radius_dim",
    "直径": "diameter_dim", "直径标注": "diameter_dim", "dia": "diameter_dim",
    "面积": "area_stat", "面积统计": "area_stat", "统计面积": "area_stat",
    "填充面积": "hatch_area", "填充统计": "hatch_area", "hatch": "hatch_area",
    "文字加框": "text_frame", "加框": "text_frame", "文字框": "text_frame",
    "房间排序": "room_sort", "排序": "room_sort",
    "墙体分段": "wall_split", "分段": "wall_split",
    "单轴": "axis_label", "单轴标注": "axis_label", "轴号": "axis_label",
    "车位": "parking_number", "车位编号": "parking_number",
}

# 已完成判定：类型名里出现这些片段、且数量达到 min_count 就认为这类活已有产出
# 注意：天正的半径标注和直径标注都产出 TDbRadiusDim（2026-11-18 真图实测），
# 类型上区分不了，只能靠数量推断——1 个基本是半径，2 个才大概率两种都做了。
FINISHED_RULES: tuple[tuple[str, str, str, int], ...] = (
    ("radius_dim", "RADIUSDIM", "半径标注", 1),
    ("diameter_dim", "RADIUSDIM", "直径标注（与半径同类型，按数量推断）", 2),
    ("area_stat", "TDBSHEET", "天正表格（面积统计 / 填充面积）", 1),
    ("hatch_area", "TDBSHEET", "天正表格（面积统计 / 填充面积）", 1),
    ("axis_label", "TDBAXISLABELSET", "单轴标注（轴号圈）", 1),
)

TABLE_STEP = 3400.0      # 天正 1:100 的表格约 2435 高，留点余量
TABLE_OFFSET = 1200.0    # 表格建议放在图形右侧留白处
MAX_TARGETS_PER_ITEM = 3

# 这些点位在命令里分别叫什么，写清单时直接用人话
POINT_LABELS: dict[str, str] = {
    "dim_side": "尺寸线位置",
    "dim_above": "尺寸线位置",
    "table": "表格插入点",
    "baseline": "基线点",
}

# 排版基准只算"图形本体"：已经排好的表格和标注会把包围盒撑大，
# 拿它们当基准会让新表格越排越远（真图实测：max_x 从 13200 变成 17506）。
NON_BODY_TOKENS: tuple[str, ...] = ("TDBSHEET", "TDBAXISLABELSET", "DIM")


def _fmt(value: Any) -> str:
    """1234.0 -> '1234'，1933.333 -> '1933.3'，坐标打印别太长。"""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    text = f"{num:.1f}"
    return text[:-2] if text.endswith(".0") else text


# --------------------------------------------------------------------------- #
# 纯逻辑
# --------------------------------------------------------------------------- #
def _type_counts(entities: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ent in entities:
        name = str(ent.get("type") or "?")
        counts[name] = counts.get(name, 0) + 1
    return counts


def _extents(entities: list[dict[str, Any]]) -> tuple[float, float, float, float] | None:
    """图形本体的包围盒：表格和标注不算，否则新表格会被旧表格越推越远。"""
    boxes = []
    for ent in entities:
        if not ent.get("bbox"):
            continue
        upper = str(ent.get("type") or "").upper()
        if any(token in upper for token in NON_BODY_TOKENS):
            continue
        boxes.append(ent["bbox"])
    if not boxes:
        boxes = [e["bbox"] for e in entities if e.get("bbox")]
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes), min(b[1] for b in boxes),
        max(b[2] for b in boxes), max(b[3] for b in boxes),
    )


def _centre(bbox: list[float] | tuple[float, ...]) -> tuple[float, float]:
    return ((float(bbox[0]) + float(bbox[2])) / 2.0,
            (float(bbox[1]) + float(bbox[3])) / 2.0)


def _radius_of(bbox: list[float] | tuple[float, ...]) -> float:
    """圆/弧的外接框推半径（弧可能只占一段，取长边一半）。"""
    w = abs(float(bbox[2]) - float(bbox[0]))
    h = abs(float(bbox[3]) - float(bbox[1]))
    return max(w, h) / 2.0


def _baseline_points(bbox: list[float] | tuple[float, ...]) -> list[list[float]]:
    """墙体分段要的两个基线点：沿墙中心线取 1/3、2/3 处。"""
    x1, y1, x2, y2 = (float(bbox[0]), float(bbox[1]),
                      float(bbox[2]), float(bbox[3]))
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    if abs(x2 - x1) >= abs(y2 - y1):          # 横墙
        span = abs(x2 - x1)
        return [[x1 + span / 3.0, cy], [x1 + span * 2.0 / 3.0, cy]]
    span = abs(y2 - y1)                       # 竖墙
    return [[cx, y1 + span / 3.0], [cx, y1 + span * 2.0 / 3.0]]


def _pick_point(ent: dict[str, Any]) -> list[float] | None:
    """对象上"一定点得中"的点：圆/弧取 45° 圆周点，线取中点，其余取中心。"""
    bbox = ent.get("bbox")
    if not bbox:
        return None
    kind = str(ent.get("type") or "")
    cx, cy = _centre(bbox)
    if kind in ("AcDbCircle", "AcDbArc"):
        r = _radius_of(bbox)
        return [round(cx + r * 0.7071, 3), round(cy + r * 0.7071, 3)]
    return [round(cx, 3), round(cy, 3)]


def _point_for_mode(mode: str, ent: dict[str, Any],
                    extents: tuple[float, float, float, float] | None,
                    table_index: int) -> list[list[float]]:
    bbox = ent.get("bbox") or [0, 0, 0, 0]
    cx, cy = _centre(bbox)
    r = _radius_of(bbox)
    if mode == "dim_side":
        return [[round(cx + r * 2.0, 3), round(cy, 3)]]
    if mode == "dim_above":
        return [[round(cx, 3), round(cy + r * 2.0, 3)]]
    if mode == "baseline":
        return [[round(p[0], 3), round(p[1], 3)] for p in _baseline_points(bbox)]
    if mode == "table":
        if extents is None:
            return [[round(cx + 2000.0, 3), round(cy, 3)]]
        # 天正表格是"插入点 + 向右下铺开"（1:100 约 7300×2435）。放在图形正下方、
        # 与左边缘对齐：横向不会甩出图太多，多张表往下依次排开不重叠。
        min_x, min_y = extents[0], extents[1]
        return [[round(min_x + 300.0, 3),
                 round(min_y - TABLE_OFFSET - TABLE_STEP * table_index, 3)]]
    return []


def _resolve_kind(raw: str) -> str:
    key = str(raw or "").strip()
    if not key:
        return ""
    if key in KIND_SPEC:
        return key
    lowered = key.lower()
    for alias, kind in KIND_ALIASES.items():
        if lowered == alias.lower():
            return kind
    # 模糊匹配取"最长的那个别名"：'填充面积' 不能被更短的 '面积' 抢走
    hits = [(alias, kind) for alias, kind in KIND_ALIASES.items()
            if alias.lower() in lowered]
    if hits:
        return max(hits, key=lambda pair: len(pair[0]))[1]
    return ""


def _finished(kind: str, types: dict[str, int]) -> str:
    for rule_kind, token, label, min_count in FINISHED_RULES:
        if rule_kind != kind:
            continue
        hits = [t for t in types if token in t.upper()]
        if sum(types[t] for t in hits) >= min_count:
            return label
    return ""


def build_handoff(document: dict[str, Any], entities: list[dict[str, Any]],
                  pending: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """把"已完成 + 还差什么 + 目标句柄 + 建议点位"算成一份清单。

    pending 每项：{"kind": "radius_dim" | "半径标注" | ..., "note"?: str,
                   "handles"?: [str], "points"?: [[x,y]]} —— handles/points 给了就用给的。
    """
    types = _type_counts(entities)
    ext = _extents(entities)

    planned: list[dict[str, Any]] = []
    table_index = 0
    for item in pending or []:
        raw_kind = item.get("kind") or item.get("command") or ""
        kind = _resolve_kind(raw_kind)
        spec = KIND_SPEC.get(kind)
        if spec is None:
            planned.append({
                "input": raw_kind, "kind": "", "label": str(raw_kind),
                "supported": False, "expect_delta": 0,
                "handles": [], "pick_points": [], "points": [],
                "note": "插件不认识这类活，请用 kind ∈ " + ", ".join(sorted(KIND_SPEC)),
            })
            continue

        already = _finished(kind, types)
        entry: dict[str, Any] = {
            "kind": kind, "label": spec["label"], "command": spec["command"],
            "point_mode": spec.get("point_mode", ""),
            "supported": not spec.get("unsupported"),
            "expect_delta": spec.get("expect_delta", 1),
            "already_looks_done": bool(already),
            "finished_hint": already,
        }
        if spec.get("unsupported"):
            entry["note"] = spec["unsupported"]
            entry["handles"] = []
            entry["pick_points"] = []
            entry["points"] = []
            planned.append(entry)
            continue

        handles: list[str] = list(item.get("handles") or [])
        if not handles:
            accept = spec.get("targets", ())
            layer = spec.get("layer")
            for ent in entities:
                if str(ent.get("type") or "") not in accept:
                    continue
                if layer and str(ent.get("layer") or "").upper() != layer:
                    continue
                handles.append(str(ent.get("handle")))
                if len(handles) >= MAX_TARGETS_PER_ITEM:
                    break

        points: list[list[float]] = [list(p) for p in (item.get("points") or [])]
        if not points:
            mode = spec.get("point_mode") or ""
            if mode:
                for handle in handles:
                    match = next((e for e in entities
                                  if str(e.get("handle")) == handle), None)
                    if match is None:
                        continue
                    points = _point_for_mode(mode, match, ext,
                                             table_index if mode == "table" else 0)
                    if mode == "table":
                        table_index += 1
                    break

        entry["handles"] = handles
        entry["pick_points"] = [_pick_point(e) for e in entities
                               if str(e.get("handle")) in handles]
        entry["points"] = points
        if not handles:
            entry["note"] = "图里找不到这类对象（" + "/".join(spec.get("targets", ())) + "），先在纯文本侧把这些画出来"
        if item.get("note"):
            entry["note"] = item["note"]
        planned.append(entry)

    counts_line = " / ".join(f"{k} {v}" for k, v in sorted(types.items(), key=lambda kv: -kv[1]))
    doc_name = document.get("name") or ""
    doc_path = document.get("path") or ""
    lines = [
        "# 交接清单（给带 GUI 的 AI 执行）",
        "",
        f"图：{doc_path or doc_name}",
        f"现有实体：{counts_line or '（空图）'}",
        "",
        "已完成（插件按类型判定，仅供参考）：",
    ]
    finished_any = False
    seen_tokens: set[str] = set()
    for rule_kind, token, label, min_count in FINISHED_RULES:
        if token in seen_tokens:
            continue
        hits = [t for t in types if token in t.upper()]
        total = sum(types[t] for t in hits)
        if total:
            seen_tokens.add(token)
            detail = "、".join(f"{t} {types[t]}" for t in hits)
            lines.append(f"- {label}：{total} 个（{detail}）")
            finished_any = True
    if not finished_any:
        lines.append("- 还没检测到标注/表格类产出")
    lines += ["", "待办（照单执行，别动其他实体）："]
    todo_index = 0
    for entry in planned:
        if not entry.get("supported"):
            lines.append(f"- {entry['label']}：暂不支持 —— {entry.get('note', '')}")
            continue
        todo_index += 1
        lines.append(f"{todo_index}. {entry['label']}　命令 {entry['command']}")
        if entry.get("already_looks_done"):
            lines.append(f"   （图上似乎已有：{entry['finished_hint']}，确认后可以跳过）")
        lines.append(f"   目标句柄：{'、'.join(entry['handles']) or '（无）'}")
        if entry.get("pick_points"):
            pts = "｜".join(
                f"{_fmt(p[0])},{_fmt(p[1])}" if p else "—"
                for p in entry["pick_points"])
            lines.append(f"   点选对象用这些点：{pts}")
        if entry.get("points"):
            pts = "｜".join(f"{_fmt(p[0])},{_fmt(p[1])}" for p in entry["points"])
            label = POINT_LABELS.get(entry.get("point_mode", ""), "命令要的点位")
            lines.append(f"   {label}：{pts}")
        lines.append(f"   预期实体增量：{entry['expect_delta']}")
        if entry.get("note"):
            lines.append(f"   注意：{entry['note']}")
    lines += [
        "",
        "要求：只补上面几项，不要动其它实体；做完 save_drawing 存盘，把路径和"
        "每项的实体增量回来告诉我。",
        "提醒：同一张图别让两个 AI 同时操作，会互相抢 CAD 的焦点和命令行。",
    ]

    return {
        "ok": True,
        "document": document,
        "extents": list(ext) if ext else None,
        "type_counts": types,
        "pending": planned,
        "handoff_text": "\n".join(lines),
    }


# --------------------------------------------------------------------------- #
# COM 侧采集
# --------------------------------------------------------------------------- #
def collect_document() -> dict[str, Any]:
    from . import acad_conn

    def impl() -> dict[str, Any]:
        doc = acad_conn._active_document()
        try:
            name = str(doc.Name)
        except Exception:  # noqa: BLE001
            name = ""
        try:
            path = str(doc.FullName)
        except Exception:  # noqa: BLE001
            path = ""
        msp = doc.ModelSpace
        total = int(msp.Count)
        entities: list[dict[str, Any]] = []
        for i in range(total):
            try:
                ent = msp.Item(i)
            except Exception:  # noqa: BLE001
                continue
            try:
                handle = str(ent.Handle)
            except Exception:  # noqa: BLE001
                continue
            try:
                type_name = str(ent.ObjectName)
            except Exception:  # noqa: BLE001
                type_name = "?"
            try:
                layer = str(ent.Layer)
            except Exception:  # noqa: BLE001
                layer = "?"
            bbox = None
            try:
                lo, hi = ent.GetBoundingBox()
                bbox = [float(lo[0]), float(lo[1]), float(hi[0]), float(hi[1])]
            except Exception:  # noqa: BLE001
                bbox = None
            entities.append({"handle": handle, "type": type_name,
                             "layer": layer, "bbox": bbox})
        return {"name": name, "path": path, "entities": entities}

    return acad_conn._com(impl, timeout=90)


def handoff(pending: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """采集当前图 + 生成交接清单。"""
    doc = collect_document()
    return build_handoff(
        {"name": doc.get("name", ""), "path": doc.get("path", "")},
        list(doc.get("entities") or []),
        pending,
    )
