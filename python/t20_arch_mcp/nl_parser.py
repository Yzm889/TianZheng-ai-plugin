"""nl_parser.py - deterministic Chinese instruction -> command mapping.

No LLM and no external dependencies are involved here.  The parser normalises
punctuation, detects the operation mode, scores the intent aliases from
:nomod:`nl_catalog`, extracts typed parameters, and returns structured issues
instead of sending a half-understood command to AutoCAD.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from . import nl_catalog

# --------------------------------------------------------------------------- #
# Text normalisation
# --------------------------------------------------------------------------- #
_PUNCT = str.maketrans(
    {
        "，": ",", "；": ";", "：": ":", "（": "(", "）": ")",
        "“": '"', "”": '"', "‘": "'", "’": "'",
        "×": "x", "─": "-", "－": "-", "—": "-", "。": ".",
    }
)


def _same_point(a: str, b: str, tol: float = 0.5) -> bool:
    """True when two 'x,y' strings name (almost) the same point."""
    try:
        ax, ay = [float(v) for v in str(a).replace(";", ",").split(",")[:2]]
        bx, by = [float(v) for v in str(b).replace(";", ",").split(",")[:2]]
    except Exception:
        return False
    return abs(ax - bx) <= tol and abs(ay - by) <= tol


def normalize_text(text: str) -> str:
    """NFKC + Chinese punctuation -> ASCII, so '（0，0）' parses like '(0,0)'."""
    out = unicodedata.normalize("NFKC", text or "")
    out = out.translate(_PUNCT)
    return re.sub(r"[ \t]+", " ", out).strip()


# --------------------------------------------------------------------------- #
# Mode detection: 'count the walls' must not be read as 'draw a wall'
# --------------------------------------------------------------------------- #
MODE_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("diagnostic", ("检查", "诊断", "加载状态", "是否加载", "确认连接", "连接状态", "当前文档")),
    ("save", ("另存", "保存", "存为", "存图", "save")),
    ("query", ("多少", "几个", "数量", "统计", "查询", "查一下", "数一下", "列出", "有哪些", "count", "list")),
    ("ui", ("打开图层管理器", "图层管理器", "缩放全图", "缩放到全图", "全图显示", "显示全部", "充满视口", "zoom extents")),
    ("create", ("画", "绘制", "创建", "新建", "建", "添加", "新增", "生成", "开", "写", "放置", "布置", "draw", "add", "create")),
)


def detect_mode(norm_text: str) -> str | None:
    low = norm_text.lower()
    for mode, signals in MODE_SIGNALS:
        for sig in signals:
            if sig in low:
                return mode
    return None


# --------------------------------------------------------------------------- #
# Issue / result containers
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    hint: str
    field: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "field": self.field,
            "message": self.message,
            "hint": self.hint,
        }


@dataclass
class ParseResult:
    ok: bool
    input: str
    normalized: str
    mode: str | None = None
    intent: str | None = None
    backend: str | None = None
    command: str | None = None
    verify_type: str | None = None
    inputs: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    confidence: float = 0.0
    unstable: bool = False
    example: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "input": self.input,
            "normalized": self.normalized,
            "mode": self.mode,
            "intent": self.intent,
            "backend": self.backend,
            "command": self.command,
            "verify_type": self.verify_type,
            "inputs": self.inputs,
            "params": self.params,
            "warnings": self.warnings,
            "issues": [i.to_dict() for i in self.issues],
            "confidence": self.confidence,
            "unstable": self.unstable,
            "example": self.example,
            "note": self.note,
        }


# --------------------------------------------------------------------------- #
# Token extraction
# --------------------------------------------------------------------------- #
_NUM = r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)"
_POINT_RE = re.compile(
    rf"(?<![\dA-Za-z])\(?\s*({_NUM})\s*[,，、xX]\s*({_NUM})"
    rf"(?:\s*[,，、xX]\s*({_NUM}))?\s*\)?"
)
_QUOTED_RE = re.compile(r"['\"]([^'\"]{1,200})['\"]")
_LAYER_RE = re.compile(r"图层\s*(?:是|为|[:=])?\s*([A-Za-z0-9_\u4e00-\u9fff-]+)")
_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s,;，；'\"]+")
_BAYS_RE = re.compile(r"(\d+)\s*跨\s*(?:[x*]\s*)?(\d+)\s*跨|(\d+)\s*[x*]\s*(\d+)\s*跨")
_UNIT_FACTOR: tuple[tuple[str, float], ...] = (
    ("毫米", 1.0), ("公厘", 1.0), ("cm", 10.0), ("mm", 1.0),
    ("分米", 100.0), ("dm", 100.0), ("厘米", 10.0), ("米", 1000.0), ("m", 1000.0),
)
_UNIT_RE = re.compile(
    "(" + "|".join(re.escape(u) for u, _ in _UNIT_FACTOR) + r")?", re.IGNORECASE
)


def _extract_points(text: str) -> list[tuple[float, float, float]]:
    """Every point mentioned, in order, PRESERVING a repeated final point.

    A repeated point is meaningful: "画墙 0,0 8000,0 8000,6000 0,6000 0,0" closes
    the loop, and the parser turns that repeat into Tianzheng's 闭合(C) option.
    De-duplicating here silently swallowed the closing point (measured: the loop
    stayed open and 搜索房间 then failed because no area was enclosed).
    """
    points: list[tuple[float, float, float]] = []
    if re.search(r"原点|零点|坐标原点", text):
        points.append((0.0, 0.0, 0.0))
    for match in _POINT_RE.finditer(text):
        x = float(match.group(1))
        y = float(match.group(2))
        z = float(match.group(3)) if match.group(3) is not None else 0.0
        points.append((x, y, z))
    return points


def _unit_factor(unit: str | None) -> float:
    if not unit:
        return 1.0
    for name, factor in _UNIT_FACTOR:
        if name.lower() == unit.lower():
            return factor
    return 1.0


def _number_with_unit(raw: str, unit: str | None) -> float:
    return float(raw) * _unit_factor(unit)


_LABEL_WORDS: dict[str, tuple[str, ...]] = {
    "radius": ("半径",),
    # Bare 高 is a fallback only: it must not swallow 门槛高 / 窗台高 / 文字高度,
    # which name different parameters ("门槛高 100" was read as a door height).
    "height": ("字高", "文字高度", "窗高", "门高", "高度", "高"),
    "width": ("门宽", "窗宽", "宽度", "宽"),
    "sill": ("窗台高", "门槛高", "门槛", "窗台", "台高"),
    "thickness": ("板厚度", "板厚", "墙厚", "厚度", "墙宽"),
    "x_spacing": ("横向间距", "x间距", "x方向间距", "开间", "间距"),
    "y_spacing": ("纵向间距", "y间距", "y方向间距", "进深"),
    "x_bays": ("横向跨数", "x跨数", "x方向跨数"),
    "y_bays": ("纵向跨数", "y跨数", "y方向跨数"),
}


_HEIGHT_DISQUALIFIERS = ("门槛", "窗台", "台", "文字", "字", "栏", "踏步")


def _labelled_number(text: str, field_name: str) -> float | None:
    words = sorted(_LABEL_WORDS.get(field_name, ()), key=len, reverse=True)
    for word in words:
        pattern = re.compile(
            re.escape(word) + r"\s*(?:是|为|[:=])?\s*(" + _NUM + r")\s*" + _UNIT_RE.pattern,
            re.IGNORECASE,
        )
        for match in pattern.finditer(text):
            if field_name == "height" and word == "高":
                # "门槛高 100" / "窗台高 900" name the sill, not the height.
                before = text[: match.start()]
                if any(before.endswith(tag) for tag in _HEIGHT_DISQUALIFIERS):
                    continue
            return _number_with_unit(match.group(1), match.group(2))
    return None


def _bays(text: str) -> tuple[int | None, int | None]:
    match = _BAYS_RE.search(text)
    if match:
        a, b = match.group(1) or match.group(3), match.group(2) or match.group(4)
        return int(a), int(b)
    x = _labelled_number(text, "x_bays")
    y = _labelled_number(text, "y_bays")
    return (int(x) if x else None), (int(y) if y else None)


def _quoted_text(text: str) -> str | None:
    match = _QUOTED_RE.search(text)
    if match:
        return match.group(1).strip()
    match = re.search(
        r"(?:文字|文本|内容)\s*(?:内容)?\s*(?:是|为|[:=])?\s*(.+?)"
        r"(?:,\s*(?:字高|图层|高度)|$)",
        text,
    )
    if match:
        value = match.group(1).strip(" ，,。.;；")
        if value and not re.fullmatch(r"[\d.,xX]+", value):
            return value
    return None


def _extract_path(text: str) -> str | None:
    match = _PATH_RE.search(text)
    if match:
        return match.group(0)
    match = _QUOTED_RE.search(text)
    if match and ("\\" in match.group(1) or "/" in match.group(1)):
        return match.group(1)
    return None


def _extract_entity_type(text: str) -> tuple[str | None, list[str]]:
    choices = nl_catalog.entity_choices()
    tokens = [t for t in choices if t.lower() in text.lower()]
    if not tokens:
        return None, []
    longest = max(len(t) for t in tokens)
    best = [t for t in tokens if len(t) == longest]
    value = choices[best[0]]
    warnings = []
    if len(tokens) > 1:
        warnings.append(
            "检测到多种实体词(" + "、".join(tokens) + ")，本次只按 '" + best[0] + "' 执行。"
        )
    return value, warnings


def _fmt_number(value: float) -> str:
    value = float(value)
    return str(int(value)) if abs(value - round(value)) < 1e-9 else f"{value:.4f}".rstrip("0").rstrip(".")


def _format_point(point: tuple[float, float, float]) -> str:
    if abs(point[2]) < 1e-9:
        return f"{_fmt_number(point[0])},{_fmt_number(point[1])}"
    return f"{_fmt_number(point[0])},{_fmt_number(point[1])},{_fmt_number(point[2])}"


# --------------------------------------------------------------------------- #
# Intent matching
# --------------------------------------------------------------------------- #
def _alias_score(norm_text: str, alias: str) -> int:
    token = normalize_text(alias).lower()
    if not token:
        return 0
    return len(token) + 5 if token in norm_text.lower() else 0


def _match_intent(norm_text: str, mode: str | None) -> tuple[Any | None, int, list[Any]]:
    candidates = [
        spec for spec in nl_catalog.SPECS
        if not mode or not spec.modes or mode in spec.modes
    ]
    scores: list[tuple[Any, int]] = []
    for spec in candidates:
        score = max((_alias_score(norm_text, a) for a in spec.aliases), default=0)
        if score:
            scores.append((spec, score))
    if not scores:
        return None, 0, []
    top = max(s for _, s in scores)
    winners = [spec for spec, s in scores if s == top]
    return (winners[0] if len(winners) == 1 else None), top, winners


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def parse(text: str) -> ParseResult:
    """Parse one natural-language instruction into an executable command plan."""
    original = text or ""
    norm = normalize_text(original)
    result = ParseResult(ok=False, input=original, normalized=norm)

    if not norm:
        result.issues.append(Issue("EMPTY_INPUT", "指令为空。", "请描述要执行的操作，例如：从 0,0 到 7000,0 画一堵墙。"))
        return result

    mode = detect_mode(norm)
    result.mode = mode
    spec, score, winners = _match_intent(norm, mode)

    if spec is None and winners:
        names = "、".join(f"{w.aliases[0]}({w.key})" for w in winners)
        result.issues.append(Issue(
            "AMBIGUOUS_INTENT",
            "一句话里检测到多个并列意图：" + names + "。",
            "一次只下达一条指令，例如先说'画一个门'，再说'画一个窗'。",
        ))
        return result

    if spec is None:
        hints = "；".join(s.aliases[0] for s in nl_catalog.SPECS[:8])
        result.issues.append(Issue(
            "UNKNOWN_INTENT",
            "无法识别指令 '" + original + "'。",
            "可识别的意图示例：" + hints + "。也可以调用 list_capabilities 查看完整清单。",
        ))
        return result

    result.intent = spec.key
    result.backend = spec.backend
    result.command = spec.command
    result.verify_type = spec.verify_type
    result.example = spec.example
    result.note = spec.note
    result.unstable = spec.unstable
    result.confidence = min(0.99, 0.55 + score * 0.08)
    if spec.unstable:
        result.warnings.append(spec.note or "该命令的命令行输入序列尚未在本机验证。")

    params: dict[str, Any] = {}
    points = _extract_points(norm)
    warnings = result.warnings

    for pspec in spec.params:
        name = pspec.name
        if pspec.kind == "points":
            if len(points) >= 2:
                params[name] = [_format_point(p) for p in points]
            elif len(points) == 1:
                result.issues.append(Issue(
                    "MISSING_PARAMETER", "缺少终点坐标（只识别到一个点）。",
                    "示例：" + spec.example, name,
                ))
            else:
                result.issues.append(Issue(
                    "MISSING_PARAMETER", "缺少点坐标。", "示例：" + spec.example, name,
                ))
            continue

        if pspec.kind == "point":
            if points:
                params[name] = _format_point(points[0])
                # always consume, otherwise a single point would be reused for the
                # NEXT point parameter (measured: 标高标注 got the same point twice)
                points = points[1:]
            elif "原点" in norm:
                params[name] = "0,0"
            elif not getattr(pspec, "required", True):
                # optional point: leave it to the backend default
                continue
            else:
                result.issues.append(Issue(
                    "MISSING_PARAMETER", "缺少 '" + name + "' 的坐标。",
                    "示例：" + spec.example, name,
                ))
            continue

        if pspec.kind in ("number", "integer"):
            value = _labelled_number(norm, name)
            if name in ("x_spacing", "y_spacing") and value is None:
                # Only fall back to a bare "间距" when neither axis was labelled;
                # otherwise "纵向间距5000" would be stolen as the X spacing.
                if not re.search(r"纵向间距|y间距|y方向间距|进深|横向间距|x间距|x方向间距|开间", norm, re.I):
                    bare = re.search(
                        r"间距\s*(?:是|为|[:=])?\s*(" + _NUM + r")\s*" + _UNIT_RE.pattern,
                        norm, re.IGNORECASE,
                    )
                    if bare:
                        value = _number_with_unit(bare.group(1), bare.group(2))
                        if name == "x_spacing":
                            warnings.append("只提供一个间距值，横纵方向都按 " + str(value) + " 处理。")
            if name in ("x_bays", "y_bays"):
                bx, by = _bays(norm)
                value = bx if name == "x_bays" else by
            if value is None and pspec.default is not None:
                value = pspec.default
            if value is None:
                if pspec.required:
                    result.issues.append(Issue(
                        "MISSING_PARAMETER", "缺少 '" + name + "'。" + (pspec.hint or ""),
                        "示例：" + spec.example, name,
                    ))
                continue
            if pspec.minimum is not None and value < pspec.minimum:
                result.issues.append(Issue(
                    "INVALID_NUMBER",
                    "'" + name + "' 的值 " + str(value) + " 小于允许的最小值 " + str(pspec.minimum) + "。",
                    "请给出合法数值（图纸单位按毫米）。", name,
                ))
                continue
            if pspec.kind == "integer":
                if abs(value - round(value)) > 1e-9:
                    result.issues.append(Issue(
                        "INVALID_NUMBER", "'" + name + "' 必须是整数。", "例如 3 跨。", name,
                    ))
                    continue
                params[name] = int(round(value))
            else:
                params[name] = value
            continue

        if pspec.kind == "text":
            value = _quoted_text(norm) if name == "text" else None
            if name == "text" and value:
                # "多行文字 备注文字 在 13000,3000" must keep only 备注文字
                value = re.split(r"\s*在\s*-?\d", value)[0]
                value = re.sub(r"\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*$", "", value)
                value = value.strip(" ，,。.;；")
            if name == "layer":
                match = _LAYER_RE.search(norm)
                value = match.group(1) if match else pspec.default
            if value is None and pspec.choices:
                # Sheet sizes and similar enumerations: pick the stated one,
                # e.g. "插入 A1 图框" must use A1 rather than the A3 default.
                for choice in pspec.choices:
                    if re.search(re.escape(str(choice)), norm, re.IGNORECASE):
                        value = str(choice)
                        break
            if value is None and pspec.default is not None:
                value = pspec.default
            if value is None:
                if pspec.required:
                    result.issues.append(Issue(
                        "MISSING_PARAMETER", "缺少 '" + name + "'。" + (pspec.hint or ""),
                        "示例：" + spec.example, name,
                    ))
            else:
                params[name] = str(value)
            continue

        if pspec.kind == "path":
            value = _extract_path(norm) or pspec.default
            if value is not None:
                params[name] = str(value)
            continue

        if pspec.kind == "choice":
            value, choice_warnings = _extract_entity_type(norm)
            warnings.extend(choice_warnings)
            if value is None:
                result.issues.append(Issue(
                    "MISSING_PARAMETER", "缺少要操作的实体类型。", "示例：" + spec.example, name,
                ))
            elif pspec.choices and value not in set(nl_catalog.entity_choices().values()):
                result.issues.append(Issue(
                    "INVALID_CHOICE", "'" + value + "' 当前不支持。", "示例：" + spec.example, name,
                ))
            else:
                params[name] = value
            continue

    # Build the exact command-line sequence.  Empty string = press Enter.
    if spec.backend in ("tianzheng", "tianzheng_custom"):
        ordered: list[str] = []
        for pspec in spec.params:
            value = params.get(pspec.name)
            if value is None:
                continue
            if pspec.kind == "points":
                ordered.extend(value)
            elif pspec.kind == "point":
                ordered.append(str(value))
            elif pspec.kind in ("number", "integer"):
                ordered.append(_fmt_number(value))
            elif pspec.kind == "path":
                ordered.append(str(value))

        # Closing a wall loop: Tianzheng has a dedicated 闭合(C) option. If the
        # caller repeats the first point as the last one, emit C instead of that
        # duplicate point -- otherwise the wall command draws a zero-length
        # segment and the loop stays open (measured: 3 walls instead of 4, which
        # then makes 搜索房间 fail because no area is enclosed).
        if spec.key == "draw_wall" and len(ordered) >= 4:
            if _same_point(ordered[0], ordered[-1]):
                ordered = ordered[:-1] + ["C"]

        if spec.backend == "tianzheng_custom":
            # Custom chains come from the catalog template; values were collected
            # above only for validation, so rebuild from the template.
            templated: list[str] = []
            for token in spec.input_template:
                if token.startswith("{") and token.endswith("}"):
                    val = params.get(token[1:-1])
                    templated.append("" if val in (None, "") else str(val))
                else:
                    templated.append(token)
            result.inputs = templated
        else:
            result.inputs = ordered + [""]

    result.params = params
    result.warnings = warnings
    result.ok = not result.issues
    return result
