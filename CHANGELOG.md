# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-11-16

### Added

- **Deterministic natural-language layer**: `nl_catalog.py` is now the single
  source of truth mapping Chinese/English intents to CAD commands, verification
  entity types and typed parameters; `nl_parser.py` normalises punctuation and
  units, distinguishes query from create intent, and validates input;
  `nl_dispatch.py` executes the plan and returns structured Chinese feedback.
- **3 new MCP tools**: `natural_language`, `preview_natural_language`,
  `list_capabilities`. 25+ command aliases across walls, axis grids, doors,
  windows, native line/circle/text, entity queries, save/new drawing and
  diagnostics.
- **21 regression tests** (`python/tests/test_nl.py`) covering typical commands,
  full-width punctuation, unit conversion, missing/ambiguous/illegal input,
  dry-run, execution failure hints and catalogue consistency. No AutoCAD or
  pywin32 needed.
- Command aliases cross-checked against the real install's `SYS\acad.pgp`, and
  five more object intents (column, slab, stair, balcony, Tianzheng text) added
  from `SYS\createobjcmd.ini`. They are recognised and reported as unsupported
  rather than answered with UNKNOWN_INTENT, because their input sequences are
  interactive and unverified.

### Fixed

- **`verify_type` false failures**: omitting it used the command name (e.g.
  `HZQT`) as the expected entity type, which can never match a real object.
  It is now inferred from the catalogue; commands with an unknown output type
  fall back to a clearly-labelled ModelSpace total comparison.
- **Blocking-dialog diagnosis never fired**: the failure path checked a key
  (`has_blocking_dialog`) that the simplified UI report never contained.
- **Optional parameters reported as missing** (e.g. `layer`) when their default
  was `None`.
- **Unrelated optional dependencies could break dispatch**: backend imports are
  now lazy and per-branch.
- `TGWall` / `TOpening`-style English aliases now infer `verify_type` as well.
- **Environment diagnosis missed the real blocker**: `DataStore.dbf` and
  `tchdata.dbf` exist at BOTH the Tianzheng install root and under `SYS`, but
  only `SYS` was checked. On the actual T20 V10 install the root copy of
  `DataStore.dbf` is read-only, which triggers the modal abort dialog. Both
  locations are now checked and each remedy command names the exact file.

### Known limitations

- The parser is rule-based: it handles the catalogued phrasings, not arbitrary
  free-form prose. `list_capabilities()` gives the exact supported vocabulary.
- `HZZW`, `MC` command-line sequences are still not validated on the target
  build (returned with `unstable: true`); `ZWBZ` and `TCGL` are reported as
  unsupported instead of faking success.
- No live AutoCAD was available in the test environment: CAD-side execution was
  verified with an injected executor, not against a real drawing.

## [0.1.0] — 2026-11-16

First working release. Verified end-to-end against **T20天正建筑 V10.0** on
**AutoCAD 2022** (`24.1s`), Tianzheng SYS folder `sys22x64`.

### Added

- **Wall creation loop, proven.** `run_tianzheng_command("HZQT", [...])` creates
  real Tianzheng walls (`TDbWall`), verified by ModelSpace count increase:
  single segment `0 → 1`, 3-point polyline `1 → 3`.
- **15 MCP tools**: connection/drawing management, Tianzheng load check,
  command execution and probing, entity queries, native AutoCAD drawing.
- **Objective success criteria.** Success is reported only when the verified
  entity type actually increased. Failures return the exact sent payload,
  `LASTPROMPT`, and a diagnosis — never a fabricated success.
- **Asynchronous command dispatch** via `Document.PostCommand` + entity-count
  polling, because `SendCommand` deadlocks whenever Tianzheng opens a panel.
- **Command termination** via driver-level `SendInput` ESC, the only method
  that actually cancels Tianzheng's interactive loop (`PostMessage` is dropped
  by AutoCAD's accelerator tables).
- **Bidirectional type aliases** so `TCH_WALL` matches the `TDbWall` /
  `TDBWALL` spelling that COM actually reports.
- **Drawing ownership isolation**, persisted across restarts, so the server
  refuses to modify drawings it did not create.
- **`diagnose_environment`**: discovers Tianzheng install roots and per-AutoCAD
  `sysXXx64` folders, and detects read-only `DataStore.dbf` / `tchdata.dbf` —
  the cause of the silent `HZQT` abort. Report-only; never modifies the install.
- **Automatic template discovery** matching the running AutoCAD version.
- **Busy-state retry** for `RPC_E_CALL_REJECTED`.
- **dsh bundle packaging**: `package.json`, `cordis.patch.yml`, `lib/index.js`,
  and a launcher that finds or bootstraps a Python with `pywin32` + `mcp`.

### Known limitations

- Tianzheng **property editing** is not implemented (planned for v0.2).
- `HZZW` (axis grid) opens a dialog-driven flow; its command-line input
  sequence is not tuned yet. The command itself can be dispatched.
- Windows only.

[0.2.0]: https://github.com/REPLACE_WITH_YOUR_ACCOUNT/dsh-t20-arch/releases/tag/v0.2.0
[0.1.0]: https://github.com/REPLACE_WITH_YOUR_ACCOUNT/dsh-t20-arch/releases/tag/v0.1.0
