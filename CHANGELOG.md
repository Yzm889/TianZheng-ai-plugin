# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
