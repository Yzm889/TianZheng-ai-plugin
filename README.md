# dsh-t20-arch

> **T20/T30 天正建筑** (Tianzheng Architecture) connector for [DeepSeek Harness](https://github.com/deepseek-ai) — creates **real Tianzheng objects** (walls, doors, windows, columns, stairs, balconies, axis grids) by driving a running AutoCAD + Tianzheng instance.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows-blue)]()

---

## Why this exists

Tianzheng objects are **ARX custom objects**. This is the whole design premise:

| Approach | Can create `TCH_WALL`? | Why |
|---|---|---|
| COM `AddLine` / `AddCircle` | ❌ | Only native AcDb entities |
| AutoLISP `entmake` | ❌ | Doesn't know ARX custom classes |
| COM custom-object creation | ❌ | No such API exists |
| **Tianzheng ARX running a command inside AutoCAD** | ✅ | The only viable path |

So this plugin is not "a wall drawer". It is a **reliable bridge that delivers a command to an AutoCAD process that already has Tianzheng loaded — and then proves the command actually worked**.

`SendCommand` returns nothing. The only objective proof of success is that the entity count really increased. Every execution path enforces that check and **never reports success without evidence**.

---


> **不提供：改墙厚。** 该功能太难实现，暂不提供；需要改墙厚时请在天正界面里手动操作。

## Verified capabilities

Measured end-to-end on **T30天正建筑 V2.0 (Build250811)** + **AutoCAD 2022 (`24.1s`)**.
Every row below was confirmed by an actual entity-count increase in a live drawing.

| Capability | Command | Verified object | Status |
|---|---|---|---|
| 墙体 wall | `HZQT` | `TDbWall` | ✅ |
| 门窗 door / window | `MC` | `TDbOpening` | ✅ |
| 轴网 axis grid | `HZZW` | 8–12 axes, `LINE` on `DOTE` | ✅ |
| 轴网标注 axis annotation | `ZWBZ` | `TDbAxisLabelSet` + `TDbDimension2` | ✅ |
| 尺寸标注 quick dim | `TQuickDim` | `TDbDimension2` | ✅ `ALL` + 尺寸线位置 |
| 尺寸标注 point dim | `TDimMP` | `TDbDimension2` | ✅ 两点 + 尺寸线位置 |
| 房间名称/面积 room label | `TUpdSpace` | `TDbSpace` | ✅ 自动带名称与面积 |
| 柱子 column | `TGColumn` | `TDbColumn` | ✅ |
| 楼梯 stair | `TLStair` | `TDbLineStair` | ✅ |
| 阳台 balcony | `TBalcony` | `TDbBalcony` | ✅ |
| 楼板 slab | `TSlab` | `TDbSlab` | ✅ 可指定板厚 |
| 房间 room | `TUpdSpace` | `TDbSpace` | ✅ 需闭合墙体 |
| 天正文字 Tianzheng text | `TText` | `TDbText` | ✅ |
| native line / circle / text | `LINE` `CIRCLE` `TEXT` | AcDb entities | ✅ |
| query / count / save / zoom / diagnose | — | — | ✅ |

**Not automated** (honest scope):

| Capability | Why not |
|---|---|
| 轴网 `HZZW`（仅“PC2T20”分支时） | 同一命令会走两条分支：`请选择插入点`（可自动）或 `PC2T20 选择需要转化的图元`（要求选取图元，坐标无效）。分支由天正轴网数据状态决定，命令行无法选择 |
| 改墙厚 `TWallThick` | **Destructive under automation**: driving it with a guessed input sequence erased all four walls (4 → 0). `TDbWall` exposes no width/thickness property over COM, so per-wall thickness cannot be set safely. Walls use Tianzheng's configured default thickness; change it in 天正设置 → 墙体 if needed |
| 图层管理器 `TCGL` | Pure UI command; creates no entity, so it cannot be verified by count |

---

## Instruction reference (原子指令)

Every instruction below is **independent and composable** — nothing is a canned
"generate a whole house" preset. You draw a plan by issuing them in order, exactly
like a drafter would.

### 轴网 / 标注

| 指令 | 作用 | 生成对象 |
|---|---|---|
| `在 0,0 画轴网` | 按天正当前轴网数据生成轴网 | `LINE`（`DOTE` 层） |
| `轴网标注 0,900 7800,900` | 给首末轴线加轴号与轴间尺寸 | `TDbAxisLabelSet` + `TDbDimension2` |
| `快速标注 0,-1500` | 选全部对象做外部尺寸 | `TDbDimension2` |
| `逐点标注 0,0 8000,0 0,-1500` | 两点之间加尺寸 | `TDbDimension2` |

### 墙体（逐段绘制）

| 指令 | 作用 |
|---|---|
| `从 0,0 到 8400,0 画一堵墙` | 画一段墙（外墙的一个边） |
| `从 0,3000 到 8400,3000 画一堵墙` | 画内隔墙；天正会自动把被穿过的外墙切断 |
| `画墙 0,0 8400,0 8400,6300 0,6300 0,0` | 一次画闭合墙环（末点重复起点 = 自动闭合） |

### 门窗 / 构件

| 指令 | 作用 |
|---|---|
| `在 1200,0 开一个宽 1000 高 2100 的门` | 在墙上开门（宽度/高度经对话框设置） |
| `在 3000,0 开一个门槛高 100 的门` | 门/窗可分别指定 宽 / 高 / 门槛(台)高 |
| `在 4000,0 开一个宽 1800 高 1500 的窗` | 在墙上开窗 |
| `门窗表 在 0,-3000` | 生成门窗明细表（需已有门窗） |
| `插入 A3 图框 在 -2000,-12000` | 插入图框（A0~A4 可选） |
| `填写图框 名称=示例住宅楼 图号=建施-01 比例=1:100` | 填写标题栏属性（24 个字段） |
| `排版` | 把图框外的全部内容整体缩放平移进图幅（右侧留给标题栏） |
| `扫描模板` | 列出可当天正模板用的对象（墙/门窗/柱/楼梯）及句柄、尺寸 |
| `实体分类` | 按天正原生类名统计对象，给出每类样本句柄 |
| `插入门窗助手` | 生成门窗点击清单 + 在图上画**可捕捉**锚点，用户点一遍即可 |
| `开工前检查` | 动手前先解析需要哪些组件，把「我能造」和「需要你建」分开告知 |
| `套版` | 分区排版：平面缩放进左侧，门窗表/说明按原尺寸排在右侧，右下留标题栏 |
| `在 0,0 画一根柱子` | 插入天正柱 |
| `在 5400,1800 到 8100,4500 画一部楼梯` | 画双跑/直线楼梯 |
| `画阳台 0,0 2000,0 2000,1000 0,1000` | 画阳台 |
| `画楼板 0,0 8400,0 8400,6300 0,6300，板厚 120` | 画楼板（可指定板厚） |
| `画台阶 90000,0 93000,0` | 台阶（两点定宽度与方向） |
| `坡道 102000,0` | 坡道（一次插入） |
| `任意坡顶 0,0 8000,0 8000,6000 0,6000，坡度 30` | 任意坡顶（消耗一条封闭多段线） |
| `画剖面墙 12000,0 12000,3000` | 剖面墙（双线） |
| `双线楼板 12000,3000 14000,3200` | 剖面双线楼板（可指定板厚，默认 200） |

### 房间

| 指令 | 作用 |
|---|---|
| `搜索房间，标注位置 4200,1500` | 搜索全部封闭区域并标注**名称 + 面积**（一次标注所有房间，不必逐间调用） |

### 文字 / 几何 / 文档

| 指令 | 作用 |
|---|---|
| `在 0,-3000 写天正文字 平面图` | 天正文字 |
| `从 0,-5000 到 5000,-5000 画一条线` / `在 3000,-4000 画半径 800 的圆` | 原生图元 |
| `标高标注 0,-2000` | 插入标高符号（可省略方向点，自动取上方 600） |
| `图名标注 4200,-5200` | 插入图名 |
| `统计墙的数量` / `列出所有天正实体` | 查询 |
| `保存图纸` / `缩放全图` | 文档操作 |
| `导出DXF` | 另存为 DXF（任何 CAD 可打开；不需要打印设备） |
| `图框清单` | 只读列出每个图框的标题栏字段、矩形与框内实体数 |

### 组合示例（逐条指令，非预设）

```
在 0,0 画轴网
轴网标注 0,900 7800,900
从 0,0 到 8400,0 画一堵墙
从 8400,0 到 8400,6300 画一堵墙
从 8400,6300 到 0,6300 画一堵墙
从 0,6300 到 0,0 画一堵墙
从 0,3000 到 8400,3000 画一堵墙        # 内隔墙，天正自动切断外墙
搜索房间，标注位置 4200,1500           # 两个房间一次标注完
在 1200,0 开一个宽 1000 高 2100 的门
在 4000,0 开一个宽 1800 的窗
快速标注 0,-1500
```

Measured 8/8: 7 walls (4 exterior segments + the interior wall splitting them),
2 rooms, 2 openings.

## 插入门窗为什么要点鼠标

天正门窗只有在 `MC` 命令里**用鼠标点选插入到墙上**时，才会与墙建立宿主关系并自动
开洞。这四条路都实测过、都不成立：

| 尝试 | 结果 |
|---|---|
| COM 复制已有门窗（模板克隆） | 外观正确，**墙不开洞**（墙段数始终为 1） |
| 把复制出的门窗 `Move` 到墙轴心线 | 图形对齐，墙仍是实心 |
| `TValidOp`（门窗检查） | 无效，输入 `ALL` 后触发 `PLAY` |
| 调 `tch_kernal.arx` 的 `insertIntoWall` | 进程外加载失败；符号非标准导出 |

根因是 `MC` 的插入点**只接受鼠标点选**，给坐标会被当成命令报"未知命令"。

所以做成了半自动：`插入门窗助手` 算好每个插入点（落在**墙轴心线**上），在
`AUX-OPENING` 图层画**可捕捉锚点**（短线的中点 + POINT 节点），用户开 MID/节点
捕捉后按序号点一遍，再用 `verify` 核对。

完整步骤与踩坑记录见 [docs/插入门窗指南.md](docs/插入门窗指南.md)。

## Using it from an AI tool that can drive a mouse

The server is a plain MCP stdio process, so any MCP host can talk to it — DSH, or a
desktop assistant that can also move a pointer (Doubao's PC client, for instance).
The only difference is the capability tier:

| Host | Tools | What it can do |
|---|---|---|
| stdio only (a chat/API client) | 29 | build walls, doors, axes, rooms, dimensions, plot to PDF |
| with desktop control, `T20_HOST_GUI=1` | 31 | everything above, plus select-type commands that need real clicks |

A GUI host is simply more comfortable to work in: it can answer Tianzheng's own
dialogs and pick entities on screen instead of asking the user to do it by hand.
That is also why `capability_report` tells a GUI-less host, in so many words, to
consider driving this plugin from an AI tool with GUI control.

### 给 DSH 用户的提示语（初次使用 / 想更顺手时）

插件里这几条命令**必须在图形里用鼠标点中对象**，纯文本宿主点不动：
半径标注 `TDimRad`、直径标注 `TDimDia`、面积统计 `TCountArea`、填充面积
`THatchArea`、文字加框 `TTextAddFrame`、车位编号 `TParkingNumber`、
房间排序 `TSortSpace`、墙体分段 `TPartwall`、单轴标注 `TSingleAxisDim`。

所以本插件对宿主 AI 有一条固定话术（`capability_report` 返回的 `user_hint_zh`，
纯文本宿主跑选择类命令时也会随结果一起带回）。宿主应在**第一次会话**、以及
**第一次遇到选择类命令点不动**时，把这句话转述给用户，之后不要反复重复：

> 温馨提示：这个插件有几类命令（半径/直径标注、面积统计、填充面积、文字加框、
> 车位编号、房间排序、墙体分段、单轴标注）必须先在图里用鼠标点中对象，纯文本/
> 终端型 AI 点不了，会停在“选择对象”。如果你想要更顺手的操作、并用上这些功能，
> 建议改用**具备桌面 GUI 控制能力的 AI 工具**（例如豆包 PC 客户端）来驱动本插件：
> 它能截图定位、自己算屏幕坐标、真的把鼠标点下去，功能更全、体验更顺。

用户追问“那我具体该怎么操作”时，把下面这段（`user_steps_zh`）原样给他：

> 具体操作分三步，不用重画：
> 1. 先在当前环境里把能做的做完：轴网、墙体、房间、门窗、图框、表格、导出、查询
>    这些都不需要鼠标，直接用就行。
> 2. 做到要点选的那几类命令（半径/直径标注、面积统计、填充面积、文字加框、
>    房间排序、墙体分段、单轴标注、车位编号）之前，先把这张图存盘（save_drawing），
>    然后停手。
> 3. 打开豆包 PC 客户端（或任何能自己截屏、点鼠标的 AI 工具），把这张 dwg 的完整
>    路径发给它，说“接着把剩下的标注/统计补上”。它在同一个 CAD 里点完，你把结果
>    拿回来继续做后面的命令行部分。
> 注意：同一张图不要让两个 AI 同时操作，会互相抢 CAD 的焦点和命令行。

判断一个宿主到底能不能干这些活，只看两件事（账户是谁无关）：它有没有截图 / 点击 /
按键这类工具；`capability_report` 里的 `host_gui` 是不是 `true`。

### 交接清单：`handoff_to_gui_host`

纯文本宿主（DeepSeek/DSH）做完轴网、墙体、房间这些之后，**停手前调一次**
`handoff_to_gui_host`，插件会算好一份可以直接照单执行的清单：

```json
{"pending": [
  {"kind": "radius_dim"},
  {"kind": "面积统计", "handles": ["2D2"]},
  {"kind": "hatch_area", "points": [[6300, -2200]]}
]}
```

`kind` 支持 `radius_dim / diameter_dim / area_stat / hatch_area / text_frame /
room_sort / wall_split / axis_label / parking_number`，中文写法（半径、面积、填充、
加框、分段、单轴、车位）也认。除了 `kind` 别的都可以不给。

返回里最有用的是 `handoff_text`，长这样：

```
# 交接清单（给带 GUI 的 AI 执行）

图：C:\tmp\样图.dwg
现有实体：TDbWall 2 / TDbSpace 1 / AcDbCircle 1 / AcDbArc 1 / ...

已完成（插件按类型判定，仅供参考）：
- 天正表格（面积统计 / 填充面积）：1 个（TDbSheet）

待办（照单执行，别动其他实体）：
1. 半径标注　命令 TDimRad
   目标句柄：1D、1E
   点选对象用这些点：10136.4,6236.4｜12995,6445
   尺寸线位置：11300,5600
   预期实体增量：1
...
```

几处细节：

- **点选点一定落在对象上**：圆/弧取 45° 圆周点，线取中点，其余取中心——不是包围盒
  正中那种"看着像、点不到"的坐标。
- **命令要的点位也给了**：尺寸线位置、表格插入点（放图形右侧留白，多张表自动往下
  错开 3400）、墙体分段的两个基线点。
- **目标句柄按类型自动挑**，也能自己指定；图里根本没有这类对象时，`note` 会直接说
  "先在纯文本侧把这些画出来"。
- **已完成是类型级判断**（有 `TDbSheet` 就认为已有统计表、有 `TDbRadiusDim` 就认为
  已有半径标注），`already_looks_done` 只做提醒，不替你下结论。
- `out_path` 给了就顺手把清单写成 `.md`（例如 `docs\交接清单.md`），方便直接贴给
  下一个 AI。
- 这个工具**只读图、不画东西**，所以纯文本宿主也能用，不需要 `T20_HOST_GUI=1`。

### 这 8 类功能的验收样图

`docs\GUI功能验收样式图.dwg`（源图）+ `docs\GUI功能验收样式图.png`（成图截图）
是 `repro\gui_style_drawing.py` 跑出来的样板：轴网、墙体、房间、房间排序、
面积统计表、圆+半径标注、弧+直径标注、填充+填充面积表、文字加框、单轴标注、
墙体分段，一条命令一次点击，跑完 11/11 通过（脚本自己核对实体增量）。

```powershell
python repro\gui_style_drawing.py            # 建图 + 截图 + 导 PDF
python repro\gui_style_drawing.py --no-pdf   # 只建图 + 截图
```

注意：PDF 导出走的是 AutoCAD 核心打印，**天正自定义对象（TDbWall/TDbSpace/
TDbSheet/TDb*Dim）不会出现在 PDF 里**（轴网、圆、填充、原生文字会）。要出正式
图纸，请在 CAD 里用天正自带的打印/输出，或者先 `TExplode` 分解再打。

## Install

Installed from the Git commit (this package is distributed via Git, not npm):

```bash
npx dshpub add Yzm889/TianZheng-ai-plugin --ref <40-char-commit>
```

Or from a local clone:

```bash
dsh plugin add ./
```

Then **fully quit and reopen DSH**. Tools appear as `mcp__t20_arch__*`.

The launcher finds a Python that already has `pywin32` and `mcp`, or builds a private
venv under `%LOCALAPPDATA%\dsh-t20-arch` on first use.


## End-to-end example (27 instructions, all measured)

A complete A3 sheet built entirely from instructions on one drawing:

```
在 0,0 画轴网
轴网标注 0,900 4800,900
画墙 0,0 9000,0 9000,6600 0,6600 0,0
从 0,3000 到 9000,3000 画一堵墙
从 5000,3000 到 5000,6600 画一堵墙
在 1500,0 开一个宽 1000 高 2100 的门
在 2000,3000 开一个宽 900 高 2100 的门
在 6500,0 开一个宽 1800 高 1500 的窗
在 9000,4500 开一个宽 1200 高 1500 的窗
在 5800,3600 到 8600,6200 画一部楼梯
画阳台 0,0 2000,0 2000,1200 0,1200
画楼板 0,0 9000,0 9000,6600 0,6600，板厚 120
搜索房间，标注位置 3000,1500
快速标注 0,-1500
墙厚标注 0,-600 0,1200
标高标注 4500,-2500
图名标注 4500,-4200
剖切符号 4500,7200 4500,8400
索引图名 10000,-2500
剖切索引 4500,7200 1200,7200 4500,8600
箭头引注 3000,-900 3000,-1500
引出标注 6500,3000 7600,3900 7600,4100
多行文字 施工说明 在 10000,-4000
门窗表 在 10500,3000
新建表格 10500,0
插入 A3 图框 在 -3000,-11000
填写图框 名称=示例住宅楼 图号=建施-01 图别=建筑施工图 比例=1:100 设计=张工 日期=2026
```

Result (verified by entity histogram, 55 objects):

```
10 TDBWALL · 4 TDBOPENING · 2 TDBLINESTAIR · 2 TDBBALCONY · 1 TDBSLAB
5 TDBSPACE · 7 TDBDIMENSION2 · 1 TDBAXISLABELSET · 8 axis LINE
1 TDBSYMBELEVATION · 1 TDBDRAWINGNAME · 1 TDBSYMBSECTION
1 TDBSYMBDRAWINGINDEX · 1 TDBSYMBINDEXPOINTER · 1 TDBSYMBARROW
1 TDBSYMBMULTILEADER · 2 TDBSHEET · 1 TDBMTEXT · 2 BLOCKREFERENCE(图框)
```

26 of 27 succeeded on the first pass; the one failure was 轴网标注 pointed at x=8400 while the
default grid only reaches x=4800 — after using the real axis coordinates it succeeded too.

**Layout:** run `排版` afterwards and the whole content is scaled and moved into the frame's usable
area (the right-hand strip is reserved for the title block). Measured: 31 entities scaled 0.44x,
final content box exactly inside the usable area (`inside: True`). Note that scaling changes the
plotted scale — a 9 m plan at 1:100 really is small on an A3 sheet.

## Prerequisites

- **Windows** (COM + Win32 input simulation are Windows-only)
- **AutoCAD + T20/T30 天正建筑**, started so that Tianzheng's ARX modules are loaded
- AutoCAD must run as the **same Windows user** as DSH, at the **same elevation**
- To reuse your own Python: `setx T20_ARCH_PYTHON "E:\your-venv\Scripts\python.exe"`

#### Foreground handling

Windows delivers `SendInput` keystrokes only to the **foreground** window, so drawing
commands need AutoCAD in front. The plugin now brings AutoCAD forward itself (a plain
foreground change, no minimise/restore flash) before a drawing command — verified
working even when another window was active at call time. Read-only work never does
this.

Set `T20_ALLOW_FOCUS_STEAL=0` if you do not want the plugin activating AutoCAD at all;
in that case drawing commands will return a clear "click the drawing area" error
instead of typing into nothing. A computer-use plugin (e.g. `wincu`) is still handy
for the agent, but it is no longer required.

---

## Quick start

```
1. get_active_document       → confirm the COM bridge is attached
2. check_tianzheng_loaded    → confirm tch_kernal.arx is loaded     (do not skip)
3. create_test_drawing       → a NEW drawing this server owns, so your real drawings are safe
4. natural_language("在 0,0 画轴网")
5. natural_language("从 0,0 到 7000,0 画一堵墙")
6. natural_language("在 1000,0 开一个宽 900 的门")
7. natural_language("统计墙的数量")
```

Each call returns the entity delta as proof, e.g. `已执行 MC，TCH_OPENING +1。`

---

## Tools (21)

### Connection & drawings
| Tool | What it does |
|---|---|
| `get_active_document` | AutoCAD version, active drawing, quiescent state |
| `create_test_drawing` | New server-owned drawing; auto-discovers the Tianzheng template |
| `close_test_drawing` | Close a server-owned drawing; refuses to close yours |
| `diagnose_environment` | Install roots, product/build, per-CAD version folder, data-file writability |

### Tianzheng state
| Tool | What it does |
|---|---|
| `check_tianzheng_loaded` | Reads `Application.ListArx()` to confirm `tch_kernal.arx` |

### Execution
| Tool | What it does |
|---|---|
| `natural_language` | Chinese/English sentence → command → verification → Chinese summary |
| `preview_natural_language` | Parse only; shows the exact command and inputs without sending |
| `list_capabilities` | Every supported intent with an example |
| `run_tianzheng_command` | Low-level: send any command + inputs, verify by count |
| `probe_command` | Send and report which entity types appeared |

### Query & native
| Tool | What it does |
|---|---|
| `get_entity_count` / `list_tianzheng_entities` / `entity_type_histogram` | Inspect ModelSpace |
| `draw_line` / `draw_circle` / `add_text` | Native COM drawing |
| `save_drawing` / `zoom_extents` | Document control |

---

## The hard-won details

Every item below was measured on a real install, not inferred. They are why this works at all.

### 1. `ObjectName` differs from the documented class name
`TCH_WALL` is reported by COM as **`TDbWall`**; `TCH_COLUMN` as **`TDbColumn`**.
Matching only the documented name returns **0 forever**. The plugin compares
canonical forms (`TCH_*` and `TDb*` stripped), so no per-type alias is needed.

### 2. `SendCommand` deadlocks; posting everything at once races with prompts
`HZQT` raises a Tianzheng panel, and a synchronous `SendCommand` never returns.
Posting `command + all inputs` in one go also fails for pick-style commands
(`HZZW`, `TGColumn` created nothing while the prompt was still coming up).
The working pattern is **stepwise**: post the command, wait, then type each input,
then ESC to end the loop — exactly what a user does.

### 3. Only `SendInput` works for interactive input
- ❌ `PostMessage(WM_KEYDOWN)` — swallowed by AutoCAD's accelerator tables
- ❌ `PostCommand` while a command is prompting — rejected with `RPC_E_CALL_REJECTED`
- ✅ `SendInput` (driver-level) for both coordinates and ESC

### 4. Dialogs block COM, so they must be driven with Win32
`MC` opens the native **`门窗参数`** dialog. While it is up, every COM call is
rejected. The plugin fills its edit fields with `WM_SETTEXT`, presses 确定, then
types the insertion point. Measured edit order for `门窗参数`:
`0=编号 1=门高 2=门宽 3=门槛高 4=距离 5=个数`.

### 5. `GetActiveObject` returns a weak, untyped object
```python
app = win32com.client.GetActiveObject("AutoCAD.Application")
app.Name          # AttributeError: <unknown>.Name
```
Rebind first: `app = gencache.EnsureDispatch(raw._oleobj_)`.

### 6. `IsQuiescent` is not trustworthy
With Tianzheng loaded it can stay `False` indefinitely while COM works fine.
Never gate logic on it — probe by calling and retrying.

### 7. COM point arguments must be VARIANT arrays
```python
msp.AddLine((x1,y1,z1), (x2,y2,z2))          # -2147024809 invalid argument
msp.AddLine(VARIANT(VT_ARRAY|VT_R8, ...))     # ✅
```

### 8. The per-version folder is NOT predictable — read it from the profile
The `sysXXx64` naming changes between products. On T20-Arch V10.0, AutoCAD 2022
used `sys22x64`. On **T30-Arch V2.0, AutoCAD 2022 uses `SYS24x64`** — probing every
folder showed only `sys24x64` loads; `sys19…sys23` all fail with `ARXLOAD 失败`.
A hardcoded year→folder map silently breaks after an upgrade, so the plugin reads
the folder **from the running AutoCAD's own Tianzheng profile**.

### 9. Antivirus can silently kill Tianzheng
Tianzheng's crack ships a DLL-hijack loader (`uxtheme.dll`, ~7 MB, in the AutoCAD
folder; the system one is ~0.6 MB). When 360 Total Security quarantined it, **every
Tianzheng ARX failed to load** and the Tianzheng UI vanished — with no obvious
connection between cause and symptom. If Tianzheng suddenly disappears, check the
AV quarantine and whitelist the AutoCAD folder plus the Tianzheng install folder.

### 10. A read-only `DataStore.dbf` aborts commands
Tianzheng's installer marks `SYS` read-only. When it cannot open `DataStore.dbf` for
writing it shows `对不起，打不开DataStore.dbf文件！` and aborts the command.
`diagnose_environment` detects this and prints the exact `attrib -R` fix; it never
modifies your installation itself.

### 11. Elevation mismatch blocks COM
`TGStart.exe` may run AutoCAD elevated. A non-elevated DSH then cannot reach it
(`0x80070522 请求的操作需要提升`). Launch AutoCAD at the same elevation as DSH —
a direct `acad.exe` launch from an unelevated shell stays unelevated and works.

### 12. Typed coordinates need the keyboard focus — and the plugin must not steal it

`SendInput` (the only way to answer Tianzheng's interactive prompts) reaches **only
the foreground window**. Posting `WM_CHAR` to the drawing view is ignored by AutoCAD,
so there is no focus-free way to type coordinates. (Posting **ESC** as window
messages *does* work — that is why cancelling a stuck command never disturbs you.)

An earlier version tried to take the foreground itself, including a minimise/restore
cycle. That made AutoCAD jump in front of whatever the user was doing — it kept
interrupting a running game. The behaviour is now:

- **Default: never steal focus.** If AutoCAD is not the foreground window, the run
  is refused with an explicit message telling you to click the drawing area.
- Opt in with `T20_ALLOW_FOCUS_STEAL=1` when you do want the plugin to take over.

Reading the drawing, counting entities, and cancelling commands never need focus.

### 13. `HZZW` normally works, but has a second branch to watch for
Measured working chain: `HZZW → 请选择插入点 → (x,y)` creates the grid as `LINE`s on
the `DOTE` layer. One run instead produced `PC2T20 选择需要转化的图元` ("select the
entities to convert"), where coordinates are ignored — that branch depends on
Tianzheng's axis-data state and cannot be selected from the command line. If it
appears, open 天正「绘制轴网」once, confirm the axis data, then retry.

### 14. A wall loop must be closed with `C`, not by repeating the first point
`画墙 0,0 8000,0 8000,6000 0,6000 0,0` looks closed but is not: the repeated final
point draws a zero-length segment, the loop stays open (3 walls instead of 4), and a
later `搜索房间` silently finds no enclosed area. Use Tianzheng's `闭合(C)` option.
The parser now recognises a repeated final point and emits `C` automatically.

### 15. A real ESC keystroke can collide with desktop-automation hotkeys
Cancelling a pending command used to send a real ESC via `SendInput`. If a
computer-use plugin binds **Esc to its own "stop" shortcut**, that keystroke aborts
the agent's desktop session instead of cancelling the CAD command — measured twice.
Cancellation now posts ESC as window messages (`post_escape`), which needs no
foreground, produces no real keystroke, and therefore cannot trip a global hotkey.
A real ESC is only used as a last resort.

### 16. `TSPace` does not exist — the room command is `TUpdSpace`
`TSPace` answers "未知命令". Use `TUpdSpace`（搜索房间）: `ALL → Enter → 标注位置点`.

### 17. Do not click the drawing area to "focus" it
Clicking inside the drawing view while no command is running starts a **window
selection** (`指定对角点或 [栏选(F)/圈围(WP)/圈交(CP)]`). The next typed coordinate then
became a selection corner instead of command input, and one run even ended up
executing `_HELP` — the modal Help dialog, which then blocked every COM call and made
the whole bridge look dead. Focus is moved with `SetFocus` on the DirectX view window
instead (no mouse event), and `T20_CLICK_TO_FOCUS` now defaults to off.

### 18. The first keystroke after activation is often swallowed
Typing immediately after activating a window lost the first character, so `0,0`
arrived as `,0` and `4800,3750` as `800,3750`; Tianzheng answered 需要点或选项关键字 /
选择无效. The fix is a short pause plus a harmless warm-up key (Shift) before the real
text — this was the single biggest source of non-deterministic failures.

### 19. Pick-only commands CAN be automated with selection keywords
Commands that say "选择…" accept typed selection keywords, so a mouse pick is often
avoidable:

| Command | Prompt | Typed answer that worked |
|---|---|---|
| 楼板 `TSlab` | `选择一封闭的多段线或圆` | **`L`** (last) — create the boundary with COM first, then `L` selects it |
| 房间 `TUpdSpace` | `请选择构成一完整建筑物的所有墙体` | **`ALL`** |

Measured full chains:
```
TSlab      : L → Enter(不可见边) → Enter(洞口) → Enter/厚度
TUpdSpace  : ALL → Enter → <标注位置点>
```

### 20. Tianzheng dialogs ignore BM_CLICK — clicks must be REAL mouse input
`插入图框` is a plain Win32 `#32770` dialog, yet `WM_COMMAND` and `BM_CLICK` on its
**插入** button did nothing — the dialog just sat there. It only advanced when a human
clicked it. So dialog buttons are now clicked with a genuine `SendInput` mouse click at
the button's screen position (which needs the dialog in the foreground). Reading and
writing edit fields with `WM_SETTEXT`/`WM_GETTEXT` still works fine; it is only button
activation that needs real input.

### 21. `TTitleFrame` writes its frame after the dialog closes
The frame arrives as a block reference plus polylines, and AutoCAD keeps building it for
a moment after 插入. Measuring immediately reported "0 created" for a frame that did
appear, so the entity count is now polled until it grows.

### 22. A programmatically closed modal dialog can leave COM rejected
After a modal Tianzheng dialog is dismissed by messages instead of user input, AutoCAD
may keep rejecting COM (`RPC_E_CALL_REJECTED`) even though the command line looks idle.
Only a **real** keypress released it — posting ESC was not enough; with AutoCAD in the
foreground a real ESC worked instantly. Keep that in mind when a run seems hung.

### 23. Tianzheng prompts may only render once input arrives
Waiting for a prompt like `请选择起始轴线` can deadlock: measured, ZWBZ printed its
prompt **only after** a key was sent (the prompt appeared immediately before the ESC
cancelled it). AutoCAD buffers command-line input, so the reliable pattern is
fixed-pace typing with a pause between values (1.8 s here) followed by an entity-count
check — not waiting for the prompt. This took 轴网标注 from 0/3 to 3/3.

Two traps found this way: `TSPace` **does not exist** (Tianzheng answers "未知命令";
the real command is `TUpdSpace`), and a wall loop must be closed with the `C` option —
repeating the first point draws a zero-length segment and leaves the loop open, after
which `搜索房间` silently finds no area. The parser now turns a repeated final point
into `C` automatically.

---

## Safety

1. **Attaches to a running instance only** (override: `AUTOCAD_ALLOW_NEW_INSTANCE=true`).
2. **Drawing ownership isolation** — persisted across restarts; write operations refuse
   drawings this server did not create (override: `T20_ALLOW_FOREIGN_DOC=true`).
3. **Never fakes success** — no entity delta means `success: false`, plus the exact
   payload, `LASTPROMPT` and a diagnosis.
4. **Never edits your installation** — read-only detection is report-only.
5. **Busy retry** for `RPC_E_CALL_REJECTED`; single STA worker thread for all COM.
6. **Bounded timeouts** — a command stuck on a dialog returns an error, not a hang.

### Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `TIANZHENG_ROOT` | auto-detected | Tianzheng install root (the folder containing `SYS`) |
| `T20_ARCH_PYTHON` | auto-detected | Python with `pywin32` + `mcp` |
| `AUTOCAD_ALLOW_NEW_INSTANCE` | `false` | Allow launching AutoCAD when none is running |
| `T20_ALLOW_FOREIGN_DOC` | `false` | Allow commands on drawings this server did not create |
| `T20_ALLOW_FOCUS_STEAL` | `false` | Allow the plugin to pull AutoCAD to the foreground. Off by default so a background run never interrupts you |
| `T20_STATE_DIR` | `%USERPROFILE%` | Where the ownership state file lives |

---

## Layout

```
dsh-t20-arch/
├── package.json           # dsh bundle manifest
├── cordis.patch.yml       # staging row + MCP client row
├── lib/index.js           # stages bin/ and python/ into %LOCALAPPDATA%
├── bin/run.ps1            # finds/builds a Python with pywin32+mcp, runs the server
├── python/
│   └── t20_arch_mcp/
│       ├── server.py           # MCP entry, 28 tools
│       ├── acad_conn.py        # COM attach, STA thread, busy retry, ownership, ARX check
│       ├── tianzheng_runner.py # stepwise execution, dialog flow, count verification
│       ├── tianzheng_query.py  # entity walking + canonical type matching
│       ├── scene_build.py      # staged builder: walls → openings → dimensions
│       ├── template_clone.py   # clone existing Tianzheng objects as templates
│       ├── opening_library.py  # per-drawing door/window width registry
│       ├── plot_export.py      # COM PDF plotting (async, polled)
│       ├── nl_catalog.py       # intent → command/params/verification table
│       ├── nl_parser.py        # Chinese instruction parsing
│       ├── nl_dispatch.py      # parse → execute → verify → Chinese summary
│       ├── dialog.py           # Win32 dialog driving (WM_SETTEXT / BM_CLICK)
│       ├── sendinput.py        # driver-level keyboard injection
│       ├── env_check.py        # install discovery, version folder, writability
│       ├── native_tools.py     # native AutoCAD helpers (VARIANT points)
│       └── winmsg.py           # window enumeration / dialog detection
├── LICENSE
└── README.md
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Tianzheng UI vanished overnight | AV quarantined the crack DLL | Restore it in the AV, whitelist AutoCAD + Tianzheng folders, restart |
| `MK_E_UNAVAILABLE` / `0x800401E3` | AutoCAD not running, or different user | Start AutoCAD; match user and elevation |
| `0x80070522 需要提升` | CAD runs elevated, DSH does not | Launch both at the same elevation |
| `success: false`, nothing added, dialog mentions DataStore | `SYS` read-only | Run `diagnose_environment`, apply `attrib -R`, restart CAD |
| `unknown command` for `HZQT` | Tianzheng ARX not loaded | Start CAD so `tch_kernal.arx` loads; verify with `check_tianzheng_loaded` |
| Wall created but reported 0 | Wrong verify type | Use `TCH_WALL`; canonical matching handles `TDbWall` |
| Every COM call rejected | Command pending or dialog open | Press ESC in AutoCAD; the plugin does this automatically |
| `执行失败` + hint mentions 焦点 / focus | AutoCAD is not the foreground window | Click the AutoCAD drawing area, then retry (or set `T20_ALLOW_FOCUS_STEAL=1`) |
| `PC2T20 选择需要转化的图元` | Tianzheng routed the axis command to its convert branch | Open 天正「绘制轴网」dialog once, confirm the axis data, then retry |
| Select-type command cannot finish (radius dim, area stats, parking numbers…) | Those need a real pointer; a stdio-only host has none | Call `capability_report`. Selecting objects / filling Tianzheng dialogs needs GUI control, so an AI tool **with desktop control** (e.g. the Doubao desktop app) drives this plugin more comfortably and unlocks that tier |
| `gui_select_command` says the target is outside the view | Clicks are screen coordinates and must land inside the drawing pane | `ZOOM` extents first (or zoom out), then retry |

---

## License

MIT — see [LICENSE](LICENSE).

AutoCAD is a registered trademark of Autodesk, Inc. 天正建筑 is a product of
北京天正工程软件有限公司 (Tangent). This is an independent interoperability tool,
not affiliated with or endorsed by either company.

### 24. `Move` takes a destination point, not a displacement
`entity.Move(BasePoint, DestinationPoint)` expects an ABSOLUTE destination. Passing a
displacement moved everything by `(dx - base.x, dy - base.y)` and pushed the laid-out
content outside the frame. The layouter now always passes `base + delta`.

### 25. The title frame is a block reference PLUS polylines
Reading only the block's bounding box gave 7000x40000 for a sheet that is really about
42000x40500, so the first layout attempt scaled the drawing to 0.11 and threw it out of
the frame. The plugin now records the union bbox of the entities created during
`插入图框` (handle diff) into `~/.t20_arch_mcp/frame_rect.json` and the layout uses it.

### 26. 排版/套版 assume exactly ONE frame per drawing
With two frames present (an A3 plus a newly inserted A2) the older frame's polylines are
counted as plan content, so 套版 reports `plan_inside_zone=false`. Keep one frame per
drawing: erase the old one before inserting another.

### 27. PDF export cannot work without a plotter configuration
`Plot.PlotToFile` returned False, and after forcing `ActiveLayout.ConfigName =
"DWG To PDF.pc3"` it returned True while writing no file. `-EXPORTPDF` is unknown in
AutoCAD 2022 and `EXPORTPDF` opens a 另存为 PDF dialog that completes without producing a
file. Root cause: the installation has no `.pc3` devices (the Plotters folder is missing).
Fixing the plotter installation is required before PDF output can be added.

### 28. 天正快捷键可直接用作指令别名
The Tianzheng shortcut list doubles as an alias source, so the short forms work too —
e.g. `SSFJ`(搜索房间) `MCB`(门窗表) `CRTK`(插入图框) `BGBZ`(标高) `PMPQ`(剖切符号)
`ZDBZ`(逐点标注) `SYFH`(索引符号) `HZZW` `ZWBZ` `HZQT` `MC` `TJ` `PD` `RYPD`.

### 30. Copied doors/windows never open the wall

`Copy()` on a `TDbOpening` reproduces the graphics but not the host relationship
to its wall, so the wall stays solid. Moving the copy onto the wall axis does not
help, and `TValidOp` cannot rebuild the link either — only a real `MC` pick does.

### 31. A marker circle is not snappable

The first version of the opening markers used a circle plus a cross; the user
could not pick them. Snapping needs geometry with a real vertex: use a short line
whose **midpoint** is the target (MID snap) and a `POINT` entity (NOD snap).

### 32. `ScaleEntity` does not change an opening's width

Scaling a `TDbOpening` only affects its height; the width is an internal
Tianzheng parameter. Different widths therefore need different MC templates.

The width cannot be read back either: `GetXData("TCH"/"TDb"/...)` returns
`[null, null]`, there is no `Width`/`Rotation` attribute, and `GetBoundingBox`
mixes width with height -- measured on real objects, a 1000-wide window reports
`1000 x 975`, a 1800-wide one `1800 x 1755`. So the plugin does **not** guess:

* `register_opening_template(handle, width, kind)` records the real width once
  (persisted per drawing, survives MCP restarts);
* `build_plan` with `openings: [{..., width: 1500}]` only clones a template whose
  recorded width matches within 50 mm, and otherwise returns
  `stage: "missing_opening_width"` telling the user exactly which opening to
  create -- it never silently clones a wrong-width opening;
* with no `width` it falls back to the first template and flags
  `width_unverified` plus a warning, so the result never pretends to be precise.

### 33. Nested `_com` calls deadlock the worker thread

`build_plan` originally called `scan_templates()` / `clone_walls_batch()`, which
are themselves `_com`-wrapped. Because there is a single COM worker thread, the
inner call waits forever on the thread that is executing the outer one. Inside a
`_com` function, always call the `_..._impl` helpers directly.

Axis dimensions used to be the one thing `build_plan` had to skip for exactly
this reason. They now run inside the builder by calling the bare
`_annotate_axis_impl` (no `_com` hop), after a pre-check that the drawing
actually has axis lines on layer `DOTE` — ZWBZ refuses any point that is not on
a real axis.

### 34. One `build_plan` call is verified end to end, not stage by stage

Until 2026-11-18 every feature had only been measured in isolation: axis
annotation on a grid drawing, opening widths on an opening drawing. The stages
had never been run as one chain, so "the walls are kept when openings fail" was
a unit-test assumption, not an observation.

`repro/e2e_chain_check.py` closes that gap. On a copy of a real small plan
(`Drawing31.dwg`: 10 walls, 4 openings, 8 `DOTE` axis lines) one call builds
walls + doors/windows with an explicit width + a linear dimension + an axis
annotation:

```
walls 10 -> 12 · openings 4 -> 6 · TDbDimension2 7 -> 12 (+1 linear, +4 axis)
completed_stages ["walls", "openings", "dimensions"] · ok true
```

With `--fail-openings` (ask for 1500 while only a 1000 template exists) the
callback keeps exactly what it should:

```
walls 10 -> 12 (kept) · openings 4 -> 4 · dims unchanged
completed_stages ["walls"] · failed_stage "openings"
stage "missing_opening_width" + the "please create a 1500 window" instructions
```

Two measurement traps worth remembering:

* read entity counts with `count_entities()` — the histogram reports one `LINE`
  as `<unknown>` on some runs (same total, different key), so never compare a
  single histogram key across runs;
* the axis annotation is the only dimension that adds `TDbAxisLabelSet`; linear
  dimensions alone leave that at its old value, which is correct.

### 35. PDF export works — the "no plotter" conclusion was wrong

The project had listed PDF export as abandoned because `PlotToFile` produced
nothing. Two wrong assumptions were behind that: the `.pc3` folder *does* exist
(`%APPDATA%\Autodesk\AutoCAD 2022\R24.1\chs\Plotters\` ships `DWG To PDF.pc3`),
and `PlotToFile` is **asynchronous** — the file appears ~20 s after the call
returns, so an immediate `GetSize` check reads 0 bytes and looks like a failure.

What actually matters when driving it from COM:

* `IAcadPlot` has **no** `PlotConfigurations` collection (that call raises
  `AttributeError`). The plotter is set on the layout instead:
  `ActiveLayout.ConfigName = "DWG To PDF.pc3"`;
* `ActiveLayout.ConfigName` reads `"无"` on a fresh drawing — that is the real
  cause of a silent no-op, not a missing driver;
* white drawing lines disappear on a white PDF page: set
  `ActiveLayout.StyleSheet = "monochrome.ctb"`;
* property reads right after the sets can hit `RPC_E_CALL_REJECTED` (AutoCAD is
  busy plotting), so every step needs the retry helper.

`export_pdf(path)` therefore sets config + plot style, calls
`Plot.PlotToFile(path, config)`, then polls until the file stops growing and
reports size plus the `%PDF-` magic; a 0-byte file is an error, never success.

Measured on a copy of `Drawing31.dwg`: `Model` layout, `DWG To PDF.pc3`,
extents, `monochrome.ctb` -> 58 662 bytes, first bytes `%PDF-`, 22 s wait.
Sample output: `docs/示例-Drawing31平面图.pdf`.

### 36. GUI tier: the commands that need a real pointer

A whole class of Tianzheng commands starts with "select objects" and cannot be
completed over COM (`SelectOnScreen` is interactive too). Seven of them were
listed as impossible: radius/diameter dimensions, area statistics, hatch area,
text frames, parking numbers, room sorting, wall splitting.

They stop being impossible when the host has desktop control. `gui_select.py`
turns a handle into a screen point, really clicks, presses Enter, optionally
clicks a second "label position", and then checks the entity delta:

```
TDimRad on a fresh circle:
  click circle 5D0 -> Enter (accepts 是否为该对象[Y]) -> click 4100,4050
  model space 56 -> 57 · histogram gains CIRCLE 1 + TDBRADIUSDIM 1
```

Click accuracy is verified separately: `click_targets()` reads back
`PickfirstSelectionSet`, so a wrong click shows up as the wrong handle instead of
as a mystery failure deep inside a command.

Three traps, all measured:

* **`Document.SendCommand` must not be used here.** It is synchronous: the call
  blocks until the command finishes, and a command waiting for a mouse pick never
  finishes -- the send timed out after 120 s with AutoCAD stuck at
  `请选择对象`. Type the command with the keyboard instead, then do the clicking;
* **the pick point cannot be the bounding-box centre.** Walls are double lines
  drawn between the two lines (AutoCAD calls it empty space), and a door's bbox
  includes its swing arc. Lines take their midpoint, circles/arcs a point on the
  circumference;
* **not every "selection" command is selection-only.** `TTextAddFrame` opens a
  modal dialog, so clicking around it leaves AutoCAD blocked on `#32770`
  (needs a real ESC to recover). Only use commands you have actually measured.

Capability gating (`T20_HOST_GUI=1`) keeps this honest for hosts without a
pointer: `capability_report` then explains what is unavailable and suggests
switching to an AI tool with GUI control.

### 29. DXF export as the plotter-free fallback

The earlier conclusion "this machine has no `.pc3`, so PDF is impossible" was
wrong (see §35). DXF is still worth having: `SaveAs(path, 16)` bypasses the whole
plotting stack, needs no plotter and no retry dance, and opens in any CAD — so
`导出DXF` stays the cheap, always-works export. Use `export_pdf` when you need a
print-ready sheet, DXF when you need geometry.
