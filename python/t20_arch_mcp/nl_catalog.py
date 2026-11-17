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
            ParamSpec("width", "number", required=False, hint="门宽，例如 900（省略则用对话框当前值）", minimum=1, unit_hint="mm"),
            ParamSpec("height", "number", required=False, hint="门高，例如 2100", minimum=1, unit_hint="mm"),
            ParamSpec("sill", "number", required=False, hint="门槛高，例如 0", minimum=0, unit_hint="mm"),
        ),
        example="在 1000,0 开一个宽 900 高 2100 的门",
        dialog_title="门窗参数",
        dialog_width_edit=2,
        note=(
            "实测流程：MC → 门窗参数对话框 → 点取门窗插入位置 → 输入坐标（吸附到墙）。"
            "对话框可见输入框顺序为 0=编号 1=门高 2=门宽 3=门槛高 4=距离 5=个数，"
            "因此宽/高/门槛分别写入 2/1/3。"
        ),
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
            ParamSpec("width", "number", required=False, hint="窗宽，例如 1500（省略则用对话框当前值）", minimum=1, unit_hint="mm"),
            ParamSpec("height", "number", required=False, hint="窗高，例如 1500", minimum=1, unit_hint="mm"),
            ParamSpec("sill", "number", required=False, hint="窗台高，例如 900", minimum=0, unit_hint="mm"),
        ),
        example="在 3000,0 开一个宽 1500 高 1500 的窗",
        dialog_title="门窗参数",
        dialog_width_edit=2,
        note=(
            "门窗共用 MC 命令与同一个参数对话框；窗宽/窗高/窗台高同样写入第 3/2/4 个输入框"
            "（0=编号 1=高 2=宽 3=门槛/台高 4=距离 5=个数）。"
        ),
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
        key="insert_title",
        aliases=("插入图框", "加图框", "图框", "标题栏", "TTitleFrame", "CRTK"),
        backend="tianzheng_title",
        modes=("create",),
        command="TTitleFrame",
        verify_type=None,
        params=(
            ParamSpec("size", "text", required=False, default="A3",
                      choices=("A0", "A1", "A2", "A3", "A4"),
                      hint="图幅，例如 A3"),
            ParamSpec("insertion", "point", required=False,
                      hint="图框左下角插入点，例如 0,-9000"),
        ),
        example="插入 A3 图框 在 0,-9000",
        note=(
            "实测：「插入图框」是标准 Win32 对话框，含宽/高/比例三个输入框、"
            "A0/A1/A2/A3/A4 图幅按钮与 插入/取消 按钮，可用窗口消息驱动。"
            "指令会点选图幅按钮、点插入，再给出插入点。"
        ),
    ),
    CommandSpec(
        key="stat_opening",
        aliases=("门窗表", "门窗明细", "TStatOp", "MCB"),
        backend="tianzheng_custom",
        modes=("create",),
        command="TStatOp",
        verify_type="TCH_SHEET",
        input_template=("ALL", "", "{insertion}"),
        params=(
            ParamSpec("insertion", "point", required=False,
                      hint="表格插入点，例如 0,-3000"),
        ),
        example="门窗表 在 0,-3000",
        note=(
            "实测：TStatOp → ALL(选全部门窗) → 回车 → 表格插入点，生成 TDbSheet(门窗表)。"
            "需要图纸里已有门窗对象。"
        ),
    ),
    CommandSpec(
        key="wall_thickness_unsupported",
        aliases=("改墙厚", "修改墙厚", "设置墙厚", "墙厚改为", "改外墙厚", "设置外墙厚", "TWallThick", "TExtThick"),
        backend="unsupported",
        modes=("modify",),
        command="TWallThick",
        verify_type=None,
        example="（暂无）",
        note="该功能太难实现，暂不提供。需要改墙厚请在天正界面里手动操作。",
    ),
    CommandSpec(
        key="annotate_elevation",
        aliases=("标高标注", "标注标高", "标高", "插标高", "TMElev", "BGBZ"),
        backend="tianzheng_custom",
        modes=("create",),
        command="TMElev",
        verify_type="TCH_SYMBOL_ELEVATION",
        input_template=("{point}", "{direction}"),
        params=(
            ParamSpec("point", "point", hint="标高点，例如 0,-2000"),
            ParamSpec("direction", "point", required=False,
                      hint="标高符号方向点，省略时自动取该点上方 600"),
        ),
        example="标高标注 0,-2000",
        note=(
            "实测：TMElev → 请点取标高点 → 请点取标高方向，生成 TDbSymbElevation(标高符号)。"
            "省略方向点时自动在其上方 600 处取点。"
        ),
    ),
    CommandSpec(
        key="drawing_name",
        aliases=("图名标注", "图名", "加图名", "TDrawingName"),
        backend="tianzheng_custom",
        modes=("create",),
        command="TDrawingName",
        verify_type="TCH_DRAWINGNAME",
        input_template=("{insertion}",),
        params=(
            ParamSpec("insertion", "point", hint="图名插入点，例如 2000,-4000"),
        ),
        example="图名标注 2000,-4000",
        note="实测：TDrawingName → 请点取插入位置，生成 TDbDrawingName(图名)。",
    ),
    CommandSpec(
        key="section_mark",
        aliases=("剖切符号", "剖切标记", "加剖切符号", "TSection", "PMPQ"),
        backend="tianzheng_custom",
        modes=("create",),
        command="TSection",
        verify_type="TCH_SYMB_SECTION",
        input_template=("{p1}", "{p2}", ""),
        params=(
            ParamSpec("p1", "point", hint="第一个剖切点，例如 4200,7000"),
            ParamSpec("p2", "point", hint="第二个剖切点，例如 4200,8000"),
        ),
        example="剖切符号 4200,7000 4200,8000",
        note=(
            "实测：TSection → 点取第一个剖切点 → 点取第二个剖切点 → 点取剖视方向"
            "（回车沿用当前方向），生成 TDbSymbSection(剖切符号)。"
        ),
    ),
    CommandSpec(
        key="drawing_index",
        aliases=("索引图名", "索引符号", "索引标注", "TIndexDim", "SYFH"),
        backend="tianzheng_custom",
        modes=("create",),
        command="TIndexDim",
        verify_type="TCH_DRAWING_INDEX",
        input_template=("{insertion}",),
        params=(
            ParamSpec("insertion", "point", hint="标注位置，例如 6000,-3000"),
        ),
        example="索引图名 6000,-3000",
        note="实测：TIndexDim → 请点取标注位置，生成 TDbSymbDrawingIndex(索引图名)。",
    ),
    CommandSpec(
        key="section_index",
        aliases=("剖切索引", "剖切索引符号", "TSECTINDEX"),
        backend="tianzheng_custom",
        modes=("create",),
        command="TSECTINDEX",
        verify_type="TCH_SYMB_INDEX_POINTER",
        input_template=("{node}", "{turn}", "{label_point}", ""),
        params=(
            ParamSpec("node", "point", hint="索引节点位置，例如 4200,7000"),
            ParamSpec("turn", "point", hint="转折点位置，例如 1000,7000"),
            ParamSpec("label_point", "point", hint="文字索引号位置，例如 4200,8200"),
        ),
        example="剖切索引 4200,7000 1000,7000 4200,8200",
        note=(
            "实测：TSECTINDEX → 索引节点 → 转折点 → 文字索引号位置 → 剖视方向（回车沿用当前），"
            "生成 TDbSymbIndexPointer。"
        ),
    ),
    CommandSpec(
        key="new_sheet",
        aliases=("新建表格", "插入表格", "表格", "TNewSheet"),
        backend="dialog_command",
        modes=("create",),
        command="TNewSheet",
        verify_type="TCH_SHEET",
        params=(
            ParamSpec("insertion", "point", hint="表格左上角点，例如 0,-11000"),
        ),
        example="新建表格 0,-11000",
        note=(
            "实测：TNewSheet 先弹出「新建表格」对话框（默认 4 行 4 列、行高 7、列宽 40、"
            "名称“天正表格”），点确定后再给左上角点，生成 TDbSheet。"
        ),
        dialog_title="新建表格",
    ),
    CommandSpec(
        key="multiline_text",
        aliases=("多行文字", "多行文本", "段落文字", "TMText"),
        backend="dialog_command",
        modes=("create",),
        command="TMText",
        verify_type="TCH_MTEXT",
        params=(
            ParamSpec("text", "text", hint="文字内容，例如 备注文字"),
            ParamSpec("insertion", "point", hint="文字左上角点，例如 13000,3000"),
        ),
        example="多行文字 备注文字 在 13000,3000",
        note=(
            "实测：TMText 弹出「多行文字」对话框（第 5 个输入框是正文），点确定后给左上角点，"
            "生成 TDbMText。"
        ),
        dialog_title="多行文字",
    ),
    CommandSpec(
        key="title_info",
        aliases=("填写图框", "图框信息", "改图框", "填写标题栏", "改标题栏", "TitleInfo"),
        backend="title_info",
        modes=(),
        verify_type=None,
        params=(),
        example="填写图框 名称=阳光小区1号楼 图号=建施-05 比例=1:100",
        note=(
            "实测可用：直接写图框块参照的属性。可用标签：建设单位、合作单位、名称、"
            "子项名称、工程编号、图号、图别、比例、日期、设计、制图、校对、审核、审定、"
            "专业负责人、方案设计人、建筑、结构、给排水、暖通、电气。"
            "写法为 标签=值，用空格分隔多个。"
        ),
    ),
    CommandSpec(
        key="arrow_note",
        aliases=("箭头引注", "箭头标注", "加箭头", "TArrow"),
        backend="tianzheng_custom",
        modes=("create",),
        command="TArrow",
        verify_type="TCH_SYMB_ARROW",
        input_template=("{start}", "{end}", ""),
        params=(
            ParamSpec("start", "point", hint="箭头起点，例如 4200,-1000"),
            ParamSpec("end", "point", hint="箭杆下一点，例如 4200,-1600"),
        ),
        example="箭头引注 4200,-1000 4200,-1600",
        note=(
            "实测：TArrow → 箭头起点 → 直段下一点 → 回车结束，生成 TDbSymbArrow。"
        ),
    ),
    CommandSpec(
        key="leader_note",
        aliases=("引出标注", "引线标注", "标注引线", "TLeader"),
        backend="tianzheng_custom",
        modes=("create",),
        command="TLeader",
        verify_type="TCH_SYMB_MULTILEADER",
        input_template=("{p1}", "{p2}", "{text_point}", ""),
        params=(
            ParamSpec("p1", "point", hint="标注第一点，例如 14000,3000"),
            ParamSpec("p2", "point", hint="引线位置，例如 15000,4000"),
            ParamSpec("text_point", "point", hint="文字基线位置，例如 15000,4200"),
        ),
        example="引出标注 14000,3000 15000,4000 15000,4200",
        note=(
            "实测：TLeader → 标注第一点 → 输入引线位置 → 点取文字基线位置 → 回车结束，"
            "生成 TDbSymbMultileader。"
        ),
    ),
    CommandSpec(
        key="layout_frame",
        aliases=("排版", "自动排版", "排入图框", "布图", "布局图框", "LayoutFrame"),
        backend="layout",
        modes=(),
        verify_type=None,
        params=(),
        example="排版",
        note=(
            "把图框之外的所有内容整体缩放并平移到 A3 图框的可用区域内，右侧自动"
            "留给标题栏。图框范围来自插入图框时记录的真实外框（图框由块参照+多段线"
            "组成，单看块包围盒会偏小）。缩放会改变出图比例，结果里会报告缩放系数。"
        ),
    ),
    CommandSpec(
        key="section_wall",
        aliases=("画剖面墙", "剖面墙", "sdwall"),
        backend="tianzheng_custom",
        modes=("create",),
        command="sdwall",
        verify_type="LINE",
        input_template=("{p1}", "{p2}", ""),
        params=(
            ParamSpec("p1", "point", hint="墙起点，例如 50000,0"),
            ParamSpec("p2", "point", hint="墙下一点，例如 50000,3000"),
        ),
        example="画剖面墙 50000,0 50000,3000",
        note=(
            "实测：sdwall → 墙起点 → 直墙下一点 → 回车结束，生成双线（LINE）。"
            "剖面类对象在天正里就是普通线段。墙厚提示默认左 120 / 右 240。"
        ),
    ),
    CommandSpec(
        key="section_floor",
        aliases=("双线楼板", "剖面楼板", "sdfloor"),
        backend="tianzheng_custom",
        modes=("create",),
        command="sdfloor",
        verify_type="LINE",
        input_template=("{p1}", "{p2}", "{thickness}"),
        params=(
            ParamSpec("p1", "point", hint="楼板起始点，例如 50000,0"),
            ParamSpec("p2", "point", hint="楼板结束点，例如 58000,300"),
            ParamSpec("thickness", "number", required=False, hint="板厚，默认 200", minimum=1),
        ),
        example="双线楼板 50000,0 58000,300，板厚 200",
        note=(
            "实测：sdfloor → 起始点 → 结束点 → 板厚（回车用默认 200），生成双线（LINE）。"
        ),
    ),
    CommandSpec(
        key="layout_template",
        aliases=("套版", "模板排版", "分区排版", "模板布图", "LayoutTemplate"),
        backend="layout_template",
        modes=(),
        verify_type=None,
        params=(),
        example="套版",
        note=(
            "分区排版：左侧约 2/3 为平面区，图框外的墙/门窗/标注整体缩放后排入；"
            "右侧为表格与说明区，门窗表、多行文字按**原尺寸**依次堆叠（不缩放），"
            "右下留给标题栏。相比“排版”的整体等比缩放，套版更适合正式出图。"
        ),
    ),
    CommandSpec(
        key="draw_step",
        aliases=("画台阶", "台阶", "TJ", "TStep", "TSTEP"),
        backend="tianzheng_custom",
        modes=("create",),
        command="TJ",
        verify_type="TCH_STEP",
        input_template=("{p1}", "{p2}", ""),
        params=(
            ParamSpec("p1", "point", hint="台阶第一点，例如 90000,0"),
            ParamSpec("p2", "point", hint="台阶第二点，例如 93000,0"),
        ),
        example="画台阶 90000,0 93000,0",
        note=(
            "实测：TJ（内部命令 TSTEP）→ 指定第一点 → 第二点/翻转到另一侧(F) → 回车结束，"
            "生成 TDbStep。两点定义台阶的宽度与方向，踏步数与总宽取天正默认。"
        ),
    ),
    CommandSpec(
        key="draw_ramp",
        aliases=("画坡道", "坡道", "PD", "TAscent", "TASCENT"),
        backend="tianzheng_custom",
        modes=("create",),
        command="PD",
        verify_type="TCH_ASCENT",
        input_template=("{p1}",),
        params=(
            ParamSpec("p1", "point", hint="坡道插入位置，例如 102000,0"),
        ),
        example="画坡道 102000,0",
        note=(
            "实测：PD（TASCENT）→ 点取位置，一次生成 TDbAscent。"
            "可用选项：转90度(A)/左右翻(S)/上下翻(D)/对齐(F)/改转角(R)/改基点(T)。"
        ),
    ),
    CommandSpec(
        key="draw_slope_roof",
        aliases=("任意坡顶", "坡顶", "画坡顶", "RYPD", "TSlopeRoof", "TSLOPEROOF"),
        backend="tianzheng_custom",
        modes=("create",),
        command="RYPD",
        verify_type="TCH_SLOPEROOF",
        pre_step="closed_polyline",
        input_template=("L", "{slope}", ""),
        params=(
            ParamSpec("points", "points", hint="屋顶轮廓点，例如 120000,0 128000,0 128000,6000 120000,6000"),
            ParamSpec("slope", "number", required=False, default=30, hint="坡度角，默认 30"),
        ),
        example="任意坡顶 120000,0 128000,0 128000,6000 120000,6000，坡度 30",
        note=(
            "实测：先用 COM 画一条封闭多段线，再用选择关键字 L（上一个）选中它；"
            "→ 输入坡度角（默认 30）→ 出檐长（回车用默认 600）→ 生成 TDbSlopeRoof。"
            "注意该命令会消耗掉那条多段线（POLYLINE -1）。"
        ),
    ),
    CommandSpec(
        key="inspect_frames",
        aliases=("图框清单", "识图", "读取图框", "看图框", "InspectFrames"),
        backend="inspect_frames",
        modes=(),
        verify_type=None,
        params=(),
        example="图框清单",
        note=(
            "只读：列出当前图纸里的每个图框，含图名/比例/图号等标题栏字段、"
            "图框矩形、尺寸，以及落在框内的实体数。一张总图通常有多个图框"
            "（每层平面一个），这一步用于理解图纸结构，不做任何修改。"
        ),
    ),
    CommandSpec(
        key="export_dxf",
        aliases=("导出DXF", "存为DXF", "输出DXF", "ExportDxf"),
        backend="native",
        modes=(),
        verify_type=None,
        params=(
            ParamSpec("path", "path", required=False, hint="输出路径，例如 D:/图纸/一层.dxf"),
        ),
        example="导出DXF",
        note=(
            "把当前图纸另存为 DXF，任何 CAD 都能打开。DXF 走文件另存，不需要打印设备，"
            "所以本机缺少 .pc3 时（PDF 无法出图）这条仍然可用。"
        ),
    ),
    CommandSpec(
        key="scan_templates",
        aliases=("扫描模板", "模板清单", "可用模板", "ScanTemplates"),
        backend="template_scan",
        modes=(),
        verify_type=None,
        params=(),
        example="扫描模板",
        note=(
            "只读：列出图纸里可当天正模板用的对象（墙/门窗/柱/楼梯/房间等），"
            "给出句柄、图层、包围盒和尺寸。模板克隆需要先有模板。"
        ),
    ),
    CommandSpec(
        key="entity_classes",
        aliases=("实体分类", "类名统计", "统计天正类", "EntityClasses"),
        backend="entity_classes",
        modes=(),
        verify_type=None,
        params=(),
        example="实体分类",
        note="只读：按天正原生类名统计对象，并给出每类样本句柄。",
    ),
    CommandSpec(
        key="prereq_check",
        aliases=("开工前检查", "需求预检", "检查组件", "需要什么组件", "PrereqCheck"),
        backend="prereq_check",
        modes=(),
        verify_type=None,
        params=(
            ParamSpec("instruction", "text", required=False,
                      hint="要执行的原始指令，例如 建一个教室"),
        ),
        example="开工前检查 建一个教室",
        note=(
            "动手前先预检：解析出需要的组件，分成「我自己能造」和「需要你帮我建」两类，"
            "一次性告诉你需要建什么、用什么命令、什么规格。"
            "建好后我扫描句柄直接克隆，不再返工。"
        ),
    ),
    CommandSpec(
        key="opening_assist",
        aliases=("插入门窗助手", "门窗点击清单", "帮我插门窗", "OpeningAssist"),
        backend="opening_assist",
        modes=(),
        verify_type=None,
        params=(
            ParamSpec("items", "text", required=False,
                      hint="门窗清单 JSON：[{kind:'door'|'window',x,y,width,label}]"),
        ),
        example="插入门窗助手",
        note=(
            "生成门窗点击清单：天正 MC 的插入点只能鼠标点选（否则墙不会自动开洞），"
            "本指令算好每个插入点、在图上画序号标记，用户照清单点一遍即可。"
            "所有装了天正的机器都能用，不依赖第三方 DLL。"
        ),
    ),
    CommandSpec(
        key="probe_position",
        aliases=("定位自检", "定位校验", "坐标自检", "校验定位", "点哪了", "ProbePosition"),
        backend="probe_position",
        modes=("query",),
        verify_type=None,
        params=(
            ParamSpec("point", "point", hint="要校验的图纸坐标，例如 4200,3150"),
        ),
        example="定位自检 4200,3150",
        note=(
            "给一个图纸坐标：把鼠标移到换算出的屏幕位置并点击，再用 AutoCAD 的 ID 命令"
            "读回实际落点，报告误差（世界单位与像素）。用于区分“坐标换算错”和"
            "“换算对但点的位置不在图元上”两种点歪情况。"
        ),
    ),
    CommandSpec(
        key="dim_wall",
        aliases=("墙厚标注", "标注墙厚", "标墙厚", "TDimWall"),
        backend="tianzheng_custom",
        modes=("create",),
        command="TDimWall",
        verify_type="TCH_DIMENSION2",
        input_template=("{p1}", "{p2}"),
        params=(
            ParamSpec("p1", "point", hint="墙线上的第一点，例如 0,-500"),
            ParamSpec("p2", "point", hint="同一面墙上的第二点，例如 0,6800"),
        ),
        example="墙厚标注 0,-500 0,6800",
        note=(
            "实测：TDimWall → 直线第一点 → 直线第二点，两点之间那段墙会被标注墙厚，"
            "生成 TDbDimension2（实测 +2）。注意它要的是“两点定墙”，不是选择对象。"
        ),
    ),
    CommandSpec(
        key="dim_quick",
        aliases=("快速标注", "快速尺寸", "尺寸标注", "标注尺寸", "TQuickDim"),
        backend="tianzheng_custom",
        modes=("create",),
        command="TQuickDim",
        verify_type="TCH_DIMENSION2",
        input_template=("ALL", "", "{dim_line}", ""),
        params=(ParamSpec("dim_line", "point", hint="尺寸线位置，例如 0,-1200"),),
        example="快速标注 0,-1200",
        note=(
            "实测可用：TQuickDim → ALL(选全部) → 回车确认 → 尺寸线位置 → 回车。"
            "生成 TDbDimension2。适用于给已有墙体/轴网加外部尺寸。"
        ),
    ),
    CommandSpec(
        key="dim_points",
        aliases=("逐点标注", "两点尺寸", "线性标注", "TDimMP", "ZDBZ"),
        backend="tianzheng_custom",
        modes=("create",),
        command="TDimMP",
        verify_type="TCH_DIMENSION2",
        input_template=("{p1}", "{p2}", "{dim_line}", ""),
        params=(
            ParamSpec("p1", "point", hint="第一个尺寸点，例如 0,0"),
            ParamSpec("p2", "point", hint="第二个尺寸点，例如 8000,0"),
            ParamSpec("dim_line", "point", hint="尺寸线位置，例如 0,-1500"),
        ),
        example="逐点标注 0,0 8000,0 0,-1500",
        note="实测可用：TDimMP → 第一点 → 第二点 → 尺寸线位置 → 回车。生成 TDbDimension2。",
    ),
    CommandSpec(
        key="draw_room",
        aliases=("搜索房间", "生成房间", "标记房间", "房间", "面积", "房间面积", "TUpdSpace", "TSPace", "SSFJ"),
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
