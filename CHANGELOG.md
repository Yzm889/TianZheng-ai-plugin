# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **GUI 档跑通 8 类「必须点鼠标」的天正命令，并出了验收样式图**（2026-11-18）
  - `gui_drive.run_prompt_driven` 新增 `zoom_window`（给世界窗口，把"还没有实体的
    插入点"一起框进视野）、`confirm_enter`（尺寸类命令点完不许补回车，补了会被当"退出"）。
  - 缩放口径统一为 **ZOOM Extents + 留白 15%**（对象只占画布约 15–25%），不再把对象
    顶到界面边界——那样没法窗选边框，也和命令行区域重叠。
  - 修掉两个会拖死后续调用的问题：① `gui_select._count_impl` 不再走
    `tianzheng_query.count_entities`（在 `TCH_WALL` 上会卡 25 s 并把 COM 线程堵死），
    改为自己遍历 `ObjectName`；② `counts()` 加类型别名 `TCH_WALL→TDbWall` 等。
  - 存盘：新建图后**先 `SaveAs` 到唯一文件名**再干活（同名 dwg 被别的文档占着时
    `SaveAs` 恒报「保存文档时出错」-2145320861）；末了 `doc.Save()` + 复制成交付名。
  - 新增 `repro/gui_style_drawing.py`：一份脚本把 11 步（轴网→墙体→房间→房间排序→
    面积统计→圆+半径→弧+直径→填充+填充面积→文字加框→单轴标注→墙体分段）全跑一遍，
    自己核对实体增量，**11/11 通过**；成图 `docs/GUI功能验收样式图.png` / `.dwg`。
- **给 DSH 用户的 GUI 提示落地**：`capability_report` 返回固定话术 `user_hint_zh`
  （初次会话说一次、第一次遇到选择类命令点不动再说一次，不要每轮重复），以及
  分步操作说明 `user_steps_zh`（用户追问“那我该怎么操作”时原样照抄）：
  先做不需要鼠标的部分 → 存盘 → 交给带 GUI 的 AI 工具点选 → 回来继续。
  `run_tianzheng_command` 跑到选择类命令且实体没动时，两句都随结果带回。
  注意：接入谁的账户不改变能力，只看宿主有没有截图/点击工具（`host_gui`）。
- **新增工具 `handoff_to_gui_host(pending, out_path)`**：只读的交接清单生成器，纯文本
  宿主停手前调一次就能拿到「路径 + 已有实体 + 还差什么 + 目标句柄 + 点得中的点选点 +
  命令要的尺寸线位置/表格插入点/基线点 + 预期实体增量」的 `handoff_text`（可直接贴给
  带 GUI 的 AI）。`kind` 收 9 类命令，中英文写法都认；`out_path` 可落成 .md。
  算法在 `handoff.py` 的 `build_handoff()`（纯函数），COM 采集在 `collect_document()`。
  测试 `tests/test_handoff.py`（18 例：kind 解析取最长别名、句柄按类型挑、圆周点位、
  横墙基线点、两表错行、已完成判定、不支持项说明、文案带护栏）。

### Fixed

- 直径标注 `TDimDia` **只认弧不认圆**：点整圆会问"是否为该对象[Y/N]"、答 Y 后回
  "请选择弧线!"；改画 `AddArc`（20°–160°，挑 45° 圆周点）即可产出标注。
- `AddArc` 的圆心必须用 `VARIANT(VT_ARRAY|VT_R8)` 包装（直接传 tuple 报 -2147024809）。

### Known limits

- `export_pdf` 走 AutoCAD 核心打印，**天正自定义对象（TDbWall/TDbSpace/TDbSheet/
  TDb*Dim）不会出现在 PDF 里**（轴网、圆、填充、原生文字正常）。`PROXYGRAPHICS=1`
  也无效。正式出图请用天正自带打印，或先分解（`TExplode`）再打。
- 车位编号 `TParkingNumber` 仍未跑通：`TParkingSpace` 单点/两点都只回"*取消*"，
  没有车位对象可编号；样张里因此不含车位。

- **GUI 层：需要真实鼠标点选的命令（`gui_select`）** — 一类命令以「选择对象」开头，
  COM 无法替它完成点选（`SelectOnScreen` 也是交互式的），过去只能返回"请你手点"。
  现在把它变成：句柄 → 屏幕坐标 → 真点一下 → 回车 →（可选）点定位点 → 核对实体增量。
  - `gui_click_targets`：只点选并回读 `PickfirstSelectionSet`，先验证"点得准不准"；
  - `gui_select_command`：直接跑命令（半径/直径标注、面积统计、填充面积、房间排序、
    墙体分段等），支持第二步的 `label_points`；
  - 能力门控：默认不注册，只有 `T20_HOST_GUI=1` 才暴露（工具数 29 → 31）；
  - 新增 `capability_report`：告诉调用方当前是哪一档能力，并给出给用户看的说明。
  
  实测（`Drawing31.dwg` 副本）：
  ```
  A. 点 2 个房间 -> PickfirstSelectionSet = {4CF, 4D0}（两个都命中）
  B. TDimRad：点圆 5D0 -> 回车接受"是否为该对象[Y]" -> 点 4100,4050
     模型空间 56 -> 57，直方图新增 CIRCLE 1 + TDBRADIUSDIM 1
  ```
- 11 条新单测 `tests/test_gui_select.py`（能力门控、点选点算法、视图判定）。
- 复现脚本 `repro/gui_select_check.py <dwg副本>`。
- **给用 DSH 的用户一句提示**（写进 MCP `instructions` + `capability_report` +
  README 故障表）：选择类命令和天正自绘对话框需要真鼠标，纯终端宿主点不了；
  想要更顺的操作、并用到这些功能，建议用**具备图形界面控制能力的 AI 工具**
  （如豆包 PC 客户端）来驱动本插件。
- **`export_pdf` / `list_plot_devices`（PDF 导出解禁）** — 之前记为「机器上没有
  `.pc3`，PDF 不可能」，实测证明是两条误判：
  - `.pc3` 目录确实存在（`%APPDATA%\Autodesk\AutoCAD 2022\R24.1\chs\Plotters\`
    自带 `DWG To PDF.pc3`）；
  - `PlotToFile` **是异步的**：调用返回时文件还没落盘（约 20 s 后才写完），
    立刻 `GetSize` 读到 0 字节，于是被误判为失败。
  
  实现要点：`IAcadPlot` 上**没有** `PlotConfigurations` 集合（会 AttributeError），
  绘图仪只能设在 layout 上（`ActiveLayout.ConfigName`，新图默认是「无」——这才是静默
  失败的真正原因）；白线在白底 PDF 上不可见，默认套 `monochrome.ctb`；设置后立即读
  属性会撞 `RPC_E_CALL_REJECTED`，所以每步都走重试；最后轮询到文件大小稳定再报成功，
  0 字节一律算错误。
  
  实测（`Drawing31.dwg` 副本，`Model` 布局）：58 662 字节、`%PDF-`、等待 22 s，
  样例见 `docs/示例-Drawing31平面图.pdf`。工具数 26 → 28。
- 5 条新单测 `tests/test_plot_export.py`（选绘图仪优先级、异步等待、0 字节不算成功）。
- 复现脚本 `repro/pdf_export_check.py <dwg副本> <out.pdf>`。
- **门窗宽度区分模板（不需要猜宽度）** — 天正不暴露门窗宽度：`GetXData` 返回
  `[null,null]`、没有 `Width`/`Rotation` 属性、`GetBoundingBox` 把宽度和高度混在
  一起（实测 1000 宽的窗量出来是 `1000×975`，1800 宽的是 `1800×1755`）。
  所以改成"登记 + 匹配"：
  - 新模块 `opening_library.py`：`register_opening_template(handle, width, kind)`
    登记真实宽度，按图纸名分区持久化（重启 MCP 服务仍在）；
  - `build_plan` 的 `openings` 支持 `width`：命中登记宽度（±50 mm）才克隆，
    不然返回 `stage: "missing_opening_width"`，说清楚要建哪一樘、多宽、怎么登记，
    **一个都不会克隆**；
  - 没给 `width` 时退回第一个模板，但结果里标 `width_unverified` + 警告；
  - 新增工具 `register_opening_template` / `list_opening_templates`（工具数 26）。
- 实测脚本 `repro/opening_width_check.py`。实测（`Drawing31.dwg` 副本）：
  4 → 5（成品包围盒 `1000×975`）；要 1500 宽时拒绝动手，计数停在 5。
- 8 条新单测 `tests/test_opening_library.py`、6 条
  `tests/test_scene_build_openings.py`。
- **端到端串联回归** `repro/e2e_chain_check.py`：一次 `build_plan` 跑完
  walls → openings（带宽度）→ dimensions（线性 + 轴网）。实测（`Drawing31.dwg`
  副本，10 墙 / 4 门窗 / 8 条 DOTE 轴线）：
  ```
  walls 10 -> 12 · openings 4 -> 6 · TDbDimension2 7 -> 12（+1 线性 +4 轴间）
  completed_stages ["walls","openings","dimensions"] · ok true
  ```
  `--fail-openings`（要 1500 宽但只有 1000 模板）：
  ```
  walls 10 -> 12（前序保留）· openings 停在 4 · dims 不变
  completed_stages ["walls"] · stage missing_opening_width
  ```

### Added (F5)

- **`build_plan` 现在能在内部做轴网标注（F5）** — `dimensions` 里 `kind: "axis"`
  不再被跳过，改为调用裸实现 `tianzheng_runner._annotate_axis_impl`
  （不经过 `_com`，所以不会在同一个 COM 工作线程上自锁）。
- **轴网预检** — 动手前先数 `DOTE` 图层的 `LINE`：为 0 就直接报
  「请先用绘制轴网 HZZW 建轴网」，并把 `axis_dote_lines` 写进返回值，不盲发注定
  失败的 ZWBZ。轴网标注成功的条目记在 `axis_labels`（含 before/after/delta）。
- 6 条新单测 `tests/test_scene_build_axis.py`（不需要 CAD）：轴网分支、偏移、
  缺轴线预检、失败诊断、线性标注回归、混合计划。

### Verification status

单元测试 `83 passed / 74 subtests`。

**轴网标注（F5）** 真实图纸实测通过（2026-11-18，`Drawing1.dwg`，8 条 DOTE 轴线）：

```
标注前  {"LINE": 8}
标注后  {"LINE": 8, "TDBDIMENSION2": 4, "TDBAXISLABELSET": 1}
build_plan -> dimension_count 1 · axis_dote_lines 8 · axis_labels[0].counts 0 -> 1
```

复现脚本 `repro/f5_axis_check.py`（`--inspect` 只看不发命令）。

**门窗宽度区分模板** 真实图纸实测通过（2026-11-18，`Drawing31.dwg` 临时副本）：

```
A. 声明宽度 1000 + 模板 4C4(1000)  -> TDbOpening 4 -> 5，成品包围盒 1000x975
B. 要 1500 宽，模板只有 1000       -> 拒绝动手，TDbOpening 停在 5，返回"请建 1500 的窗"
```

复现脚本 `repro/opening_width_check.py <dwg副本>`。

**端到端串联回归** 实测通过（2026-11-18，`Drawing31.dwg` 副本）：

```
walls 10 -> 12 · openings 4 -> 6 · TDbDimension2 7 -> 12 · TDbAxisLabelSet 1 -> 2
中途失败时：walls 保留 +2，openings/dims 一个不动，completed_stages ["walls"]
```

复现脚本 `repro/e2e_chain_check.py <dwg副本>`（`--fail-openings` 验部分保留）。

**PDF 导出** 实测通过（2026-11-18，`Drawing31.dwg` 副本，`Model` 布局）：

```
config DWG To PDF.pc3 · plot_type extents · plot_style monochrome.ctb
58 662 字节 · 文件头 %PDF- · PlotToFile 返回后等待 22 018 ms 才落盘
```

复现脚本 `repro/pdf_export_check.py <dwg副本> <out.pdf>`。

**GUI 点选** 实测通过（2026-11-18，`Drawing31.dwg` 副本）：

```
A. click_targets -> PickfirstSelectionSet {4CF, 4D0}（2/2 命中）
B. TDimRad -> 模型空间 56 -> 57，新增 CIRCLE 1 + TDBRADIUSDIM 1
```

复现脚本 `repro/gui_select_check.py <dwg副本>`。

## [0.7.0] — 2026-11-17

**A complete, common architectural plan can now be produced from natural language.**

### Added

- **轴网标注 (axis annotation)** — `ZWBZ` is now reliable (3/3 measured):
  `起始轴线 → (必要时)是否该对象[Y] → 终止轴线 → (必要时)[Y] → 跳过排除轴线 → 回车`,
  producing `TDbAxisLabelSet`(轴号) + `TDbDimension2`(轴间尺寸).
- **尺寸标注 (dimensions)** — two working commands:
  - `TQuickDim`（快速标注）: `ALL → Enter → 尺寸线位置 → Enter` → `TDbDimension2 +2`
  - `TDimMP`（逐点标注）: `p1 → p2 → 尺寸线位置 → Enter` → `TDbDimension2 +1`
- Type aliases `TCH_AXIS_LABEL ↔ TDbAxisLabelSet`, `TCH_DIMENSION2 ↔ TDbDimension2`
  (without them axis numbers and dimensions were always counted as 0).
- Prompt-driven flow helpers and command-output probing (`winmsg.command_output_size`,
  `command_tail`) so execution can react to what AutoCAD actually prints.

### Verified end-to-end (9/9, natural language only)

```
在 0,0 画轴网                       -> LINE +8 (DOTE)
轴网标注 0,y 4800,y                 -> TDbAxisLabelSet +1
画墙 0,0 8400,0 8400,6300 0,6300    -> TDbWall +4
在 1200,0 开一个宽 1000 的门         -> TDbOpening +1
在 5000,0 开一个宽 1800 的窗         -> TDbOpening +1
在 5400,1800 到 8100,4500 画一部楼梯  -> TDbLineStair +2
搜索房间，标注位置 3000,3000          -> TDbSpace +1（自动带名称与面积）
快速标注 0,-1500                     -> TDbDimension2 +2
逐点标注 0,6300 8400,6300 4200,7800  -> TDbDimension2 +1
```

Result: axis numbers ①②③④, dimension chains, walls, door/window openings, stair and a
room label (房间 49.45m²) — visually confirmed on screen.

### Fixed (root causes of "sometimes it works")

- **Never click the drawing area to focus.** That click starts a window selection
  (`指定对角点`), so the next typed coordinate became a selection corner, and one run
  even executed `_HELP` — a modal dialog that blocks every COM call. Focus is now moved
  with `SetFocus` on the DirectX view window (no mouse event).
- **First keystroke was swallowed** after activation (`0,0` → `,0`). Typing now pauses
  and sends a harmless warm-up key first.
- **The plugin brings AutoCAD to the foreground itself** before a drawing command
  (plain foreground change, no minimise/restore flash); `T20_ALLOW_FOCUS_STEAL=0`
  disables it. Measured working even when another window was active at call time.
- **Real ESC keystrokes can collide with desktop-automation hotkeys** — cancellation now
  posts ESC as window messages instead (a computer-use plugin binding Esc to "stop"
  aborted the session twice).
- **Tianzheng prompts may only render once input arrives**, so waiting for them
  deadlocks; fixed-pace typing plus an entity-count check replaced prompt waiting.
- ZWBZ no longer loops on failure (the visible "select axis → cancel → select again"
  thrash was auto-retry; it now fails once and reports).

### Documented limitation

- **改墙厚 `TWallThick` is NOT automated.** A guessed input sequence erased all four
  walls (4 → 0), and `TDbWall` exposes no width/thickness property over COM. Walls use
  Tianzheng's configured default thickness.

## [0.4.1] — 2026-11-17

### Fixed

- **A real ESC keystroke could abort the agent's desktop session.** Cancelling a
  pending CAD command used to type a real ESC via `SendInput`. If a computer-use
  plugin binds **Esc to its own "stop" shortcut**, that keystroke is read as "user
  stopped the agent" — measured twice, and it looked exactly like the user hitting
  stop. Cancellation now posts ESC as window messages (`post_escape`), which needs
  no foreground, produces no real keystroke, and cannot trip a global hotkey; a real
  ESC is only the last resort.

### Changed

- **轴网 `HZZW` corrected back to ✅.** The earlier "conditional" verdict was wrong:
  the failing runs were being rejected by the focus guard, not by Tianzheng's
  `PC2T20` branch. With the keyboard focused, `HZZW → 请选择插入点 → (x,y)` creates
  the grid as `DOTE` lines. The `PC2T20` branch was observed once and is now
  documented as a caveat instead of being treated as the normal path.

### Verified

13/13 end-to-end — 轴网 · 闭合墙体 · 楼板 · 房间 · 柱 · 门 · 窗 · 楼梯 · 阳台 ·
天正文字 · 线 · 圆 · 统计.

## [0.4.0] — 2026-11-17

Two more Tianzheng objects become automatable, and the parser stops losing input.

### Added

- **楼板 (slab)** — `TSlab`. The command wants "一封闭的多段线或圆", so the plugin
  creates a closed polyline through COM and then answers with the typed selection
  keyword **`L`** (last).板厚 is supported; measured chain:
  `L → Enter → Enter → 厚度`.
- **房间 (room)** — `TUpdSpace`. The real command name is `TUpdSpace`: **`TSPace` does
  not exist** and AutoCAD answers "未知命令". Chain: `ALL → Enter → 标注位置点`.
- **Generic prompt-template backend** (`tianzheng_custom` + `input_template` +
  `pre_step`), so a command whose prompts interleave literal keywords with user
  values is described in the catalog instead of hardcoded in the dispatcher.
- `native_tools.draw_closed_polyline` — closed boundary creation for pick-based
  commands.

### Fixed

- **A repeated final point now closes a wall loop.** The point extractor used to
  de-duplicate, silently swallowing the closing point of
  `画墙 0,0 8000,0 8000,6000 0,6000 0,0`. The loop stayed open (3 walls instead of 4)
  and `搜索房间` then found no enclosed area. A repeated point is now translated into
  Tianzheng's `闭合(C)` option.
- `板厚` was not a recognised parameter label, so "画楼板 …，板厚 120" silently used
  the default thickness.

### Verified

12/12 end-to-end on T30天正建筑 V2.0 + AutoCAD 2022 — 墙体(闭合) · 楼板 · 房间 · 柱 ·
门 · 窗 · 楼梯 · 阳台 · 天正文字 · 线 · 圆 · 统计, producing
`TDBWALL/TDBSLAB/TDBSPACE/TDBCOLUMN/TDBOPENING/TDBLINESTAIR/TDBBALCONY/TDBTEXT`.

## [0.3.1] — 2026-11-17

### Changed

- **The plugin no longer steals the foreground.** It used to call
  `SetForegroundWindow`, and when that was refused it fell back to a
  minimise/restore cycle — which made AutoCAD jump in front of whatever the user was
  doing (in practice: it kept interrupting a running game). Focus is now only
  attempted when `T20_ALLOW_FOCUS_STEAL=1`; otherwise a run that needs typed
  coordinates is refused with an explicit "click the drawing area" message instead
  of typing into an unknown window or hanging.
- **轴网 `HZZW` reclassified from ✅ to ⚠️ conditional.** The same command takes two
  branches depending on Tianzheng's axis-data state: `请选择插入点` (works, draws
  `DOTE` axes) or `PC2T20 选择需要转化的图元` (wants an entity selection; coordinates
  are ignored). The branch cannot be chosen from the command line, so the earlier
  "verified" claim was too optimistic and has been corrected in the catalog, README
  and tests.

### Added

- `sendinput.post_escape()` — cancels a pending command by posting ESC to the window
  and all descendants. This works **without** the foreground, so recovering a stuck
  AutoCAD never disturbs the user.
- `clear_pending()` runs before every command: a half-finished interactive loop from
  an earlier run makes every later COM call fail with `RPC_E_CALL_REJECTED`, which
  used to look like "the next command silently did nothing".
- Specific failure hints for the missing-focus and `PC2T20` cases.

## [0.3.0] — 2026-11-16

Full capability pass on **T30天正建筑 V2.0 (Build250811)** + **AutoCAD 2022**, all
verified by live entity-count deltas.

### Added

- **轴网 (axis grid)** — `HZZW` needs only an insertion point and draws the axes as
  `LINE` entities on the `DOTE` layer; verification is layer-scoped so unrelated
  lines don't count.
- **柱子 (column)** — `TGColumn` from a typed insertion point → `TDbColumn`.
- **楼梯 (stair)** — `TLStair` from two typed corners → `TDbLineStair`.
- **阳台 (balcony)** — `TBalcony` from four typed boundary points → `TDbBalcony`.
- **天正文字 (Tianzheng text)** — `TText` from insertion point + content → `TDbText`.
- **`verify_layer`** — success checks can be restricted to one layer (needed for axes).
- **Stepwise execution** — post the command, wait, then type each input and ESC.
  Posting `command + inputs` in one shot silently did nothing for pick-style commands.
- **One automatic retry** with a longer settle when verification fails; success is
  still only ever reported from a real entity delta.

### Fixed

- **Canonical type matching.** COM reports `TDbColumn`/`TDbWall` while the documented
  names are `TCH_COLUMN`/`TCH_WALL`. Previously only walls had a hand-added alias, so
  real columns were reported as "0 created" while they were visibly present. Matching
  now strips the `TCH`/`TDb` variants, so every type compares correctly.
- **Per-version Tianzheng folder is read from the AutoCAD profile** instead of a
  hardcoded year map. Measured: on T30-Arch V2.0, AutoCAD 2022 uses `SYS24x64`, not
  `sys22x64`; every other `sysXXx64` folder fails with `ARXLOAD 失败`.
- **`tch.ini` decoding** — the file is GBK, so the product name used to come back as
  mojibake from a UTF-8 read.
- **Environment discovery is no longer a fixed list** — registry keys are enumerated
  and all fixed drives are scanned, so a future Tianzheng version is found too.

### Documented

- Antivirus (360 Total Security) quarantining Tianzheng's DLL-hijack loader
  (`uxtheme.dll` in the AutoCAD folder) makes **every Tianzheng ARX fail to load** and
  the UI vanish. This was the root cause of a full-day "Tianzheng is broken" episode.
- Elevation mismatch (`TGStart.exe` runs CAD elevated) blocks COM with `0x80070522`.
- `IsQuiescent`, `PostMessage(WM_KEYDOWN)` and one-shot posting are all unreliable —
  see README for the measured behaviour.

### Not automated (honest scope)

- 楼板 `TSlab` (needs boundary/room pick loop), 轴网标注 `ZWBZ` (interactive axis
  selection), 房间 `TSPace`, and 图层管理器 `TCGL` (UI-only, creates no entity).

## [0.2.0] — 2026-11-16

### Added

- **Deterministic natural-language layer**: `nl_catalog.py` is now the single
  source of truth mapping Chinese/English intents to CAD commands, verification
  entity types and typed parameters; `nl_parser.py` normalises punctuation and
  units, distinguishes query from create intent, and validates input;
  `nl_dispatch.py` executes the plan and returns structured Chinese feedback.
- **Dialog driving** (`dialog.py`): `MC` opens the native `门窗参数` dialog, which
  blocks COM entirely, so its edit fields are filled with `WM_SETTEXT` and accepted
  with `BM_CLICK` before the insertion point is typed.
- **`diagnose_environment`**: reports install roots, product/build, per-CAD version
  folders and whether `DataStore.dbf` / `tchdata.dbf` are writable (report-only; it
  never modifies the installation).
- **Per-file `DataStore.dbf` check** — the per-version `sysXXx64` copy is the one that
  actually aborts a command, so it is checked separately from the root copy.

### Fixed

- Backends that return plain data (no `ok`/`success` flag) were treated as failures,
  so successful `create_test_drawing` and entity queries reported `ok: false`.
- `verify_type` no longer falls back to the command name, which could never match an
  entity and produced guaranteed false failures.

## [0.1.0] — 2026-11-16

### Added

- First working release: the wall creation loop, proven end to end on
  **T20天正建筑 V10.0** + **AutoCAD 2022** — `run_tianzheng_command("HZQT", ...)`
  creates real `TDbWall` objects (single segment `0 → 1`, polyline `1 → 3`).
- Objective success criteria: success is reported only when the verified entity type
  actually increased; failures return the exact payload, `LASTPROMPT` and a diagnosis.
- Asynchronous dispatch via `PostCommand` + polling (because `SendCommand` deadlocks
  when Tianzheng opens a panel) and ESC termination via driver-level `SendInput`.
- Drawing ownership isolation, persisted across restarts.
- dsh bundle packaging: `package.json`, `cordis.patch.yml`, `lib/index.js`, and a
  launcher that finds or bootstraps a Python with `pywin32` + `mcp`.

[0.3.0]: https://github.com/Yzm889/TianZheng-ai-plugin/releases/tag/v0.3.0
[0.2.0]: https://github.com/Yzm889/TianZheng-ai-plugin/releases/tag/v0.2.0
[0.1.0]: https://github.com/Yzm889/TianZheng-ai-plugin/releases/tag/v0.1.0
