# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.0]: https://github.com/REPLACE_WITH_YOUR_ACCOUNT/dsh-t20-arch/releases/tag/v0.1.0
