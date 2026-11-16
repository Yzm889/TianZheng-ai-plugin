"""nl_catalog.py - the single source of truth for command intent mapping.

This module is deliberately dependency-free (standard library only).  Both the
natural-language parser and the low-level Tianzheng runner import it, so an
alias, its real CAD command, and the entity type used to verify it can never
drift apart again.

Before this module existed, the mapping lived partly in README.md, partly in
the MCP tool descriptions, and mostly in the upstream model's imagination.
That is why the same Chinese sentence could produce different commands, and why
`verify_type` was frequently omitted (which made the runner compare against the
command name and report a guaranteed failure).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ParamSpec:
    """One parameter the parser must extract from a Chinese instruction."""

    name: str
    kind: str  # point | points | number | integer | text | path | choice
    required: bool = True
    default: Any = None
    choices: tuple[str, ...] = ()      # for kind == "choice": Chinese token -> value
    hint: str = ""
    minimum: float | None = None
    unit_hint: str = ""                # e.g. "mm"; numbers are normalised to mm


@dataclass(frozen=True)
class CommandSpec:
    """A user intent and everything needed to execute and verify it."""

    key: str
    aliases: tuple[str, ...]
    backend: str  # tianzheng | tianzheng_dialog | native | query | document | diagnostic | unsupported
    modes: tuple[str, ...] = ()        # create | query | save | diagnostic | ui; empty = any
    command: str | None = None         # real AutoCAD / Tianzheng command
    verify_type: str | None = None     # entity type whose count must increase
    params: tuple[ParamSpec, ...] = ()
    example: str = ""
    note: str = ""
    unstable: bool = False             # command-line sequence not proven on the target build
    dialog_title: str | None = None    # native parameter dialog this command opens
    dialog_width_edit: int | None = None  # which visible Edit holds the size, e.g. 2 for 门窗参数
    verify_layer: str | None = None    # count only entities on this layer (e.g. DOTE for axis lines)
    input_template: tuple[str, ...] = ()  # literal tokens and "{param}" placeholders
    pre_step: str | None = None        # e.g. "closed_polyline": create geometry before the command


_ENTITY_CHOICES: tuple[tuple[str, str], ...] = (
    ("墙", "TCH_WALL"),
    ("墙体", "TCH_WALL"),
    ("wall", "TCH_WALL"),
    ("门", "TCH_OPENING"),
    ("窗", "TCH_OPENING"),
    ("门窗", "TCH_OPENING"),
    ("opening", "TCH_OPENING"),
    ("柱", "TCH_COLUMN"),
    ("柱子", "TCH_COLUMN"),
    ("column", "TCH_COLUMN"),
    ("轴号", "TCH_AXIS_LABEL"),
    ("轴标", "TCH_AXIS_LABEL"),
    ("轴线标注", "TCH_AXIS_LABEL"),
    ("楼梯", "TCH_STAIR"),
    ("房间", "TCH_SPACE"),
    ("空间", "TCH_SPACE"),
    ("阳台", "TCH_BALCONY"),
    ("楼板", "TCH_SLAB"),
    ("板", "TCH_SLAB"),
    ("屋顶", "TCH_ROOF"),
    ("屋面", "TCH_ROOF"),
    ("直线", "LINE"),
    ("线", "LINE"),
    ("圆", "CIRCLE"),
    ("圆形", "CIRCLE"),
    ("文字", "TEXT"),
    ("文本", "TEXT"),
)

SPECS: tuple[CommandSpec, ...] = (
    CommandSpec(
        key="draw_wall",
        aliases=(
            "画墙", "绘制墙", "建墙", "创建墙", "画一段墙", "画一堵墙", "画墙体",
            "画墙线", "一面墙", "一段墙", "墙体", "墙线", "墙", "HZQT", "TGWall",
            "draw wall", "wall",
        ),
        backend="tianzheng",
        modes=("create",),
        command="HZQT",
        verify_type="TCH_WALL",
        params=(
            ParamSpec("points", "points", hint="起点和至少一个后续点，例如 0,0 到 7000,0"),
        ),
        example="从 0,0 到 7000,0 画一堵墙",
    ),
    CommandSpec(
        key="draw_axis_grid",
        aliases=("画轴网", "绘制轴网", "建轴网", "轴线网", "矩形轴网", "插入轴网", "HZZW", "TRectAxis"),
        backend="tianzheng",
        modes=("create",),
        command="HZZW",
        verify_type="LINE",
        verify_layer="DOTE",
        params=(
            ParamSpec("origin", "point", hint="轴网插入点，例如 0,0"),
        ),
        example="在 0,0 画轴网",
        note=(
            "实测可用：HZZW → 请选择插入点 → 输入坐标即生成轴网（轴线是 DOTE 图层的 LINE）。"
            "注意同一命令在本机也出现过「PC2T20 选择需要转化的图元」分支（要求选取图元、"
            "坐标无效）；若遇到该分支，请手动打开天正「绘制轴网」确认轴网数据后再试。"
        ),
    ),
    CommandSpec(
        key="draw_column",
        aliases=("画柱", "绘制柱", "建柱", "柱子", "柱", "TGColumn"),
        backend="tianzheng",
        modes=("create",),
        command="TGColumn",
        verify_type="TCH_COLUMN",
        params=(
            ParamSpec("origin", "point", hint="柱子插入点，例如 0,0"),
        ),
        example="在 0,0 画一根柱子",
        note="实测：TGColumn → 点取位置 → 输入坐标即放置；按天正当前柱尺寸生成。",
    ),
    CommandSpec(
        key="draw_door",
        aliases=("开门", "画门", "绘制门", "添加门", "门洞", "门", "MC", "TOpening"),
        backend="tianzheng_dialog",
        modes=("create",),
        command="MC",
        verify_type="TCH_OPENING",
        params=(
            ParamSpec("insertion", "point", hint="插入点，例如 1000,0（会吸附到最近的墙）"),
            ParamSpec("width", "number", hint="门宽，例如 900", minimum=1, unit_hint="mm"),
        ),
        example="在 1000,0 开一个宽 900 的门",
        dialog_title="门窗参数",
        dialog_width_edit=2,
        note="实测流程：MC → 门窗参数对话框（设为门宽）→ 点取门窗插入位置 → 输入坐标（吸附到墙）。",
    ),
    CommandSpec(
        key="draw_window",
        aliases=("开窗", "画窗", "绘制窗", "添加窗", "窗洞", "窗", "TC", "TWindow"),
        backend="tianzheng_dialog",
        modes=("create",),
        command="MC",
        verify_type="TCH_OPENING",
        params=(
            ParamSpec("insertion", "point", hint="插入点，例如 3000,0（会吸附到最近的墙）"),
            ParamSpec("width", "number", hint="窗宽，例如 1500", minimum=1, unit_hint="mm"),
        ),
        example="在 3000,0 开一个宽 1500 的窗",
        dialog_title="门窗参数",
        dialog_width_edit=2,
        note="门窗共用 MC 命令与同一个参数对话框；窗宽同样在第 3 个输入框。",
    ),
    # Additional object families confirmed in the real install's
    # SYS\createobjcmd.ini.  Their creation is interactive (points/picks), so an
    # unverified command-line sequence must never be sent as if it were reliable.
    CommandSpec(
        key="draw_slab",
        aliases=("画楼板", "画板", "绘制楼板", "楼板", "楼面板", "TSlab"),
        backend="tianzheng_custom",
        modes=("create",),
        command="TSlab",
        verify_type="TCH_SLAB",
        pre_step="closed_polyline",
        input_template=("L", "", "", "{thickness}"),
        params=(
            ParamSpec("points", "points", hint="楼板边界顶点，至少 3 个，例如 0,0 8000,0 8000,6000 0,6000"),
            ParamSpec("thickness", "number", required=False, default=None,
                      hint="板厚，负值表示向下生成；省略则用天正默认 200", unit_hint="mm"),
        ),
        example="画楼板 0,0 8000,0 8000,6000 0,6000，板厚 120",
        note=(
            "实测：先用边界点生成闭合多段线，再执行 TSlab，"
            "用选择关键字 L 选中该多段线（无需鼠标拾取），"
            "两个回车跳过「不可见边」和「洞口」，最后回车或输入板厚。"
        ),
    ),
    CommandSpec(
        key="annotate_axis",
        aliases=("轴网标注", "标注轴网", "标注轴线", "轴线标注", "轴号标注", "ZWBZ", "TMultAxisDim"),
        backend="tianzheng_annotate",
        modes=("create",),
        command="ZWBZ",
        verify_type="TCH_AXIS_LABEL",
        params=(
            ParamSpec("start_axis", "point", hint="第一条轴线上的点，例如 0,1000"),
            ParamSpec("end_axis", "point", hint="最后一条轴线上的点，例如 4800,1000"),
        ),
        example="轴网标注 0,1000 4800,1000",
        note=(
            "需先有轴网。实测稳定（3/3）：按 起始轴线 →(必要时)是否该对象[Y]→ 终止轴线 →"
            "(必要时)是否该对象[Y]→ 跳过排除轴线 → 回车结束 的顺序输入，"
            "会先生成 TDbAxisLabelSet(轴号) 再生成 TDbDimension2(轴间尺寸)。"
            "天正的提示要等有输入才渲染，所以这里用固定节奏输入而不是等提示；两个点必须落在真实轴线上。"
        ),
    ),
    CommandSpec(
        key="draw_room",
        aliases=("搜索房间", "生成房间", "标记房间", "房间", "面积", "房间面积", "TUpdSpace", "TSPace"),
        backend="tianzheng_custom",
        modes=("create",),
        command="TUpdSpace",
        verify_type="TCH_SPACE",
        input_template=("ALL", "", "{label_point}"),
        params=(
            ParamSpec("label_point", "point", hint="房间面积标注位置，例如 4000,3000（需落在墙体围合的区域内）"),
        ),
        example="搜索房间，标注位置 4000,3000",
        note=(
            "实测：TSPace 在天正里不存在（未知命令），真名是 TUpdSpace。"
            "先用 ALL 选中全部墙体，回车结束选择，再给一个标注位置点即生成房间。"
            "要求墙体围合成闭合区域，否则该位置不会生成房间。"
        ),
    ),
    CommandSpec(
        key="draw_stair",
        aliases=("画楼梯", "绘制楼梯", "建楼梯", "楼梯", "TLStair", "TRStair"),
        backend="tianzheng",
        modes=("create",),
        command="TLStair",
        verify_type="TCH_LINESTAIR",
        params=(
            ParamSpec("origin", "point", hint="楼梯左下角，例如 0,0"),
            ParamSpec("corner", "point", hint="楼梯右上角，例如 3000,3000"),
        ),
        example="在 0,0 到 3000,3000 画一部楼梯",
        note="实测：TLStair 输入对角两点即可生成（TDBLINESTAIR）。踏步参数取天正当前默认值。",
    ),
    CommandSpec(
        key="draw_balcony",
        aliases=("画阳台", "绘制阳台", "阳台", "TBalcony"),
        backend="tianzheng",
        modes=("create",),
        command="TBalcony",
        verify_type="TCH_BALCONY",
        params=(
            ParamSpec("origin", "point", hint="阳台起点，例如 0,0"),
            ParamSpec("p2", "point", hint="第二点，例如 2000,0"),
            ParamSpec("p3", "point", hint="第三点，例如 2000,1000"),
            ParamSpec("p4", "point", hint="第四点，例如 0,1000"),
        ),
        example="画阳台 0,0 2000,0 2000,1000 0,1000",
        note="实测：TBalcony 依次输入边界点即可生成（TDBBALCONY）。",
    ),
    CommandSpec(
        key="add_tianzheng_text",
        aliases=("天正文字", "写天正文字", "天正文本", "TText", "TMText"),
        backend="tianzheng",
        modes=("create",),
        command="TText",
        verify_type="TCH_TEXT",
        params=(
            ParamSpec("insertion", "point", hint="文字插入点，例如 0,0"),
            ParamSpec("text", "text", hint="文字内容"),
        ),
        example="在 0,0 写天正文字 轴线说明",
        note="实测：TText 输入插入点与文字内容即可生成（TDBTEXT）。",
    ),
    CommandSpec(
        key="layer_manager",
        aliases=("图层管理", "打开图层管理器", "图层管理器", "TCGL", "TLayerManager"),
        backend="unsupported",
        modes=("ui",),
        command="TCGL",
        example="打开图层管理器",
        note="纯界面命令，不产生实体，无法用实体数量验证成功。",
    ),
    CommandSpec(
        key="count_entities",
        aliases=(
            "统计数量", "统计", "查询数量", "查数量", "数一下", "有多少", "几", "数量",
            "实体数量", "数实体", "列出数量", "entity count", "count entities",
        ),
        backend="query",
        modes=("query",),
        params=(
            ParamSpec(
                "entity_type", "choice",
                choices=tuple(dict(_ENTITY_CHOICES).keys()),
                hint="要统计的实体，例如 墙 / 门 / 窗 / 柱 / 圆",
            ),
        ),
        example="统计墙的数量",
    ),
    CommandSpec(
        key="list_entities",
        aliases=("列出实体", "列出墙", "实体列表", "查看实体", "显示实体", "list entities"),
        backend="query",
        modes=("query",),
        params=(
            ParamSpec(
                "entity_type", "choice",
                choices=tuple(dict(_ENTITY_CHOICES).keys()),
                hint="要列出的实体，例如 墙",
            ),
        ),
        example="列出墙",
    ),
    CommandSpec(
        key="draw_line",
        aliases=("画线", "画直线", "绘制直线", "画一条线", "直线", "LINE", "draw line"),
        backend="native",
        modes=("create",),
        command="LINE",
        verify_type="LINE",
        params=(
            ParamSpec("start", "point", hint="起点，例如 0,0"),
            ParamSpec("end", "point", hint="终点，例如 1000,1000"),
            ParamSpec("layer", "text", required=False, default=None, hint="可选图层名"),
        ),
        example="从 0,0 到 1000,1000 画一条线，图层 AXIS",
    ),
    CommandSpec(
        key="draw_circle",
        aliases=("画圆", "画圆形", "绘制圆", "圆", "圆形", "CIRCLE", "draw circle"),
        backend="native",
        modes=("create",),
        command="CIRCLE",
        verify_type="CIRCLE",
        params=(
            ParamSpec("center", "point", hint="圆心，例如 100,100"),
            ParamSpec("radius", "number", hint="半径，例如 500", minimum=0.0001, unit_hint="mm"),
            ParamSpec("layer", "text", required=False, default=None, hint="可选图层名"),
        ),
        example="在 100,100 画半径 500 的圆",
    ),
    CommandSpec(
        key="add_text",
        aliases=("写文字", "写文本", "添加文字", "加文字", "标注文字", "输入文字", "文字", "TEXT"),
        backend="native",
        modes=("create",),
        command="TEXT",
        verify_type="TEXT",
        params=(
            ParamSpec("insertion", "point", hint="插入点，例如 500,500"),
            ParamSpec("text", "text", hint="文字内容，用引号包起来最稳妥"),
            ParamSpec("height", "number", required=False, default=250.0,
                      hint="字高，默认 250", minimum=1, unit_hint="mm"),
            ParamSpec("layer", "text", required=False, default=None, hint="可选图层名"),
        ),
        example="在 500,500 写文字 \"轴线 A\"，字高 300",
    ),
    CommandSpec(
        key="save_drawing",
        aliases=("另存为", "保存为", "保存图纸", "存图", "保存", "save drawing", "save"),
        backend="document",
        modes=("save",),
        params=(
            ParamSpec("path", "path", required=False, default=None, hint="可选保存路径"),
        ),
        example="保存图纸 或 另存为 C:\\temp\\t20.dwg",
    ),
    CommandSpec(
        key="new_test_drawing",
        aliases=("新建测试图纸", "新建图纸", "新建测试图", "新建", "测试图纸", "创建测试图纸", "new drawing"),
        backend="document",
        modes=("create",),
        params=(),
        example="新建一个测试图纸",
    ),
    CommandSpec(
        key="zoom_extents",
        aliases=("缩放全图", "显示全部", "全图显示", "缩放到全图", "充满视口", "zoom extents"),
        backend="document",
        modes=("ui",),
        params=(),
        example="缩放到全图",
    ),
    CommandSpec(
        key="check_tianzheng_loaded",
        aliases=("检查天正", "检查天正是否加载", "天正加载状态", "确认天正", "check tianzheng"),
        backend="diagnostic",
        modes=("diagnostic",),
        params=(),
        example="检查天正是否加载",
    ),
    CommandSpec(
        key="get_active_document",
        aliases=("当前图纸", "活动图纸", "连接状态", "确认连接", "当前文档", "active document"),
        backend="diagnostic",
        modes=("diagnostic",),
        params=(),
        example="确认连接",
    ),
    CommandSpec(
        key="diagnose_environment",
        aliases=("诊断环境", "环境诊断", "检查环境", "诊断安装", "diagnose environment"),
        backend="diagnostic",
        modes=("diagnostic",),
        params=(),
        example="诊断环境",
    ),
)

_BY_KEY = {spec.key: spec for spec in SPECS}


def get_spec(key: str) -> CommandSpec | None:
    return _BY_KEY.get(key)


def suggest_verify_type(command: str | None) -> str | None:
    """Map a raw CAD command (alias) to the entity type that proves it worked.

    The runner calls this when a caller omits `verify_type`.  Without it the
    runner used the command name itself ("HZQT") as the expected entity type,
    which can never match a real object and produced false failures.
    """
    if not command:
        return None
    token = command.strip().strip("._- ").upper()
    for spec in SPECS:
        if spec.command and spec.command.strip().upper() == token:
            return spec.verify_type
        # Accept English aliases too: callers often send TGWall / TOpenimg
        # instead of the PGP alias HZQT / MC.
        for alias in spec.aliases:
            if alias.isascii() and alias.strip().upper() == token:
                return spec.verify_type
    # Also accept a spec key such as "draw_wall" as a convenience.
    spec = _BY_KEY.get(token.lower())
    return spec.verify_type if spec else None


def resolve_entity_token(token: str) -> str | None:
    """Chinese/English entity word -> canonical entity type name."""
    key = token.strip().lower()
    for chinese, value in _ENTITY_CHOICES:
        if key == chinese.lower():
            return value
    return None


def entity_choices() -> dict[str, str]:
    return dict(_ENTITY_CHOICES)


def supported_intents() -> list[dict[str, Any]]:
    """Compact catalogue for the MCP tool description / error hints."""
    return [
        {
            "intent": spec.key,
            "say": spec.aliases[0],
            "command": spec.command,
            "verify_type": spec.verify_type,
            "example": spec.example,
            "unstable": spec.unstable,
            "note": spec.note,
        }
        for spec in SPECS
    ]
