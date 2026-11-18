# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
  - 新增工具 `register_opening_template` / `list_opening_templates`（共 26 个工具）。
- 实测脚本 `repro/opening_width_check.py`。实测（`Drawing31.dwg` 副本）：
  4 → 5（成品包围盒 `1000×975`）；要 1500 宽时拒绝动手，计数停在 5。
- 8 条新单测 `tests/test_opening_library.py`、6 条
  `tests/test_scene_build_openings.py`。

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

单元测试 `66 passed / 74 subtests`。

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
