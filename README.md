# dsh-t20-arch

> **T20天正建筑** (Tianzheng Architecture) connector for [DeepSeek Harness](https://github.com/deepseek-ai) — creates **real Tianzheng objects** (`TCH_WALL`, …) and native AutoCAD entities by driving a running AutoCAD + T20 instance over COM.

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

`SendCommand` returns nothing. The only objective proof of success is that the wall count in ModelSpace really increased. Every execution tool enforces that before/after comparison and **never reports success without evidence**.

---

## Install

```bash
dsh plugin add dsh-t20-arch
```

Then **fully quit and reopen DSH**. Tools appear as `mcp__t20_arch__*`.

The plugin bootstraps its own Python environment on first launch: it looks for an interpreter that already has `pywin32` and `mcp`, and if none exists it creates a private venv under `%LOCALAPPDATA%\dsh-t20-arch\venv` and installs them (needs internet, takes a few minutes).

### Prerequisites

- **Windows** (COM + Win32 input simulation are Windows-only)
- **AutoCAD 2019–2024** with **T20天正建筑** loaded, started via **`TGStart.exe`**
- Syntax note: a bare `acad.exe` has **no Tianzheng ARX**, so every `TCH_*` command answers *"unknown command"*. Always launch through the Tianzheng launcher.
- AutoCAD must run as the **same Windows user** as DSH, and both must be **non-elevated** (an elevated DSH cannot drive a non-elevated AutoCAD, and vice versa).

To reuse your own Python:

```powershell
setx T20_ARCH_PYTHON "E:\your-venv\Scripts\python.exe"
```

### From source

```powershell
git clone https://github.com/REPLACE_WITH_YOUR_ACCOUNT/dsh-t20-arch.git
cd dsh-t20-arch
python -m pip install -r python/requirements.txt   # or: pip install pywin32 mcp
```

---

## Quick start

```
1. get_active_document       → confirm the COM bridge is attached
2. check_tianzheng_loaded    → confirm tch_kernal.arx is loaded  (do not skip)
3. diagnose_environment      → confirm Tianzheng data files are writable
4. create_test_drawing       → a NEW drawing this server owns, so your real drawings are safe
5. get_entity_count("TCH_WALL")    → 0
6. run_tianzheng_command("HZQT", ["0,0","7000,0",""], verify_type="TCH_WALL")
                                   → success: true, delta: 1
7. get_entity_count("TCH_WALL")    → 1
```

Verified wall geometry: `object_name=TDbWall`, layer `WALL`, bbox `X 0→7000, Y −120→120, Z 0→3000` (240 thick, 3000 high).

---

## Tools (18)

### Natural language (preferred entry point)

| Tool | What it does |
|---|---|
| `natural_language(text, dry_run=False, auto_create_drawing=True)` | Parse → validate → execute → report, with structured Chinese errors |
| `preview_natural_language(text)` | Dry-run: show intent, CAD command, exact inputs and `verify_type` without touching AutoCAD |
| `list_capabilities()` | List all supported intents, aliases and example sentences |

The parser is deterministic (no extra LLM call, no new dependency) and never sends a
half-understood instruction to AutoCAD. It normalises punctuation and units
(`（0，0）`→`(0,0)`, `6 米`→`6000`), separates query from create intents
(`统计墙的数量` cannot be misread as `画墙`), binds `verify_type` from the command
catalogue, and refuses to continue when a required parameter is missing.

Representative instructions (all covered by `python/tests/test_nl.py`):

```
从 0,0 到 7000,0 画一堵墙        → HZQT  ["0,0","7000,0",""]  verify TCH_WALL
统计墙的数量                     → count_entities("TCH_WALL")
在 100,100 画半径 500 的圆       → native AddCircle(center, 500)
在 500,500 写文字 "轴线 A"，字高 300 → native AddText
另存为 C:\temp\t20.dwg           → SaveAs
检查天正是否加载                 → check_tianzheng_loaded
```

Failures return `stage: "parse"` with an error code (`UNKNOWN_INTENT`,
`AMBIGUOUS_INTENT`, `MISSING_PARAMETER`, `INVALID_NUMBER`, ...) and a Chinese hint,
or `stage: "execute"` with the raw backend result plus a specific diagnosis
(TGStart not used, read-only `DataStore.dbf`, blocking dialog, ...).

### Connection & drawings
| Tool | What it does |
|---|---|
| `get_active_document` | AutoCAD version, active drawing, quiescent state |
| `create_test_drawing` | Create a **new** server-owned drawing; auto-discovers the Tianzheng template matching your AutoCAD version |
| `close_test_drawing` | Close a server-owned drawing; refuses to close yours |
| `diagnose_environment` | Install roots, per-version `sysXXx64` folders, data-file writability, auto template |

### Tianzheng state
| Tool | What it does |
|---|---|
| `check_tianzheng_loaded` | Reads `Application.ListArx()` to confirm `tch_kernal.arx` |

### Execution (the core)
| Tool | What it does |
|---|---|
| `run_tianzheng_command` | Send a command + input sequence, **judge success by entity-count delta** |
| `probe_command` | Same, but returns the full before/after entity-type histogram |

`run_tianzheng_command` parameters:

| Parameter | Meaning |
|---|---|
| `command` | Tianzheng command, e.g. `HZQT` |
| `inputs` | Ordered command-line inputs; `""` means Enter; points as `"x,y"` |
| `verify_type` | Expected entity type, e.g. `TCH_WALL` |
| `timeout_seconds` | Default 20 |
| `mode` | `async` (default, `PostCommand` + polling) / `sync` (blocking `SendCommand`, only for non-UI commands) |
| `auto_dismiss_ui` | Auto-send ESC and dismiss blocking dialogs |

**Verified input sequences:**

```python
# single wall segment
run_tianzheng_command("HZQT", ["0,0", "7000,0", ""], verify_type="TCH_WALL")

# multi-segment polyline wall (creates 2 walls)
run_tianzheng_command("HZQT", ["7000,0", "7000,5000", "0,5000", ""], verify_type="TCH_WALL")
```

### Query
| Tool | What it does |
|---|---|
| `get_entity_count` | Count entities of a type |
| `list_tianzheng_entities` | List with handle, layer, bounding box |
| `entity_type_histogram` | Count every type present |

### Native AutoCAD
| Tool | What it does |
|---|---|
| `draw_line` / `draw_circle` / `add_text` | Standard COM drawing |
| `save_drawing` | Save the server-owned drawing |
| `zoom_extents` | Zoom to extents |

---

## Command aliases

Tianzheng aliases live in `SYS\acad.pgp`. Confirmed on a real T20-Arch V10.0 install:

```
HZQT  →  *TGWall         # draw wall      ← verified working
HZZW  →  *TRectAxis      # draw axis grid
ZWBZ  →  *TMultAxisDim   # axis annotation
MC    →  *TOpening       # door / window
TCGL  →  *TLayerManager  # layer manager
```

The English command names (`TGWall`, `TRectAxis`, …) work too. `SYS\tchcmd.txt` is the full command table, and `SYS\createobjcmd.ini` maps each `TCH_*` type to its creation command.

---

## The hard-won details

Every item below was measured on a real install, not inferred. They are the reason this plugin works at all.

### 1. `ObjectName` is `TDbWall`, not `TCH_WALL`

The DXF **class** name is `TCH_WALL`, but COM's `Entity.ObjectName` reports **`TDbWall`** (histogram key `TDBWALL`). Matching only on `TCH_WALL` returns **0 forever**. The plugin has a bidirectional alias table.

### 2. `SendCommand` blocks forever — use `PostCommand`

`HZQT` raises Tianzheng's wall panel; synchronous `SendCommand` then **never returns** and deadlocks the COM thread. Use `Document.PostCommand()`, which returns immediately, and poll the entity count. AutoCAD 2022's type library has **no** `SendStringToExecute`; `PostCommand` is the only async entry point.

### 3. Only `SendInput` cancels the command — `PostMessage` is silently dropped

After drawing, Tianzheng keeps asking for more points, so AutoCAD stays non-quiescent and **every** COM call returns `RPC_E_CALL_REJECTED (0x8001010A)` — it looks frozen.

- ❌ `PostMessage(WM_KEYDOWN, VK_ESCAPE)` — silently swallowed by AutoCAD's accelerator tables and Tianzheng's message filters
- ✅ `SendInput` (driver-level injection) — works

### 4. `GetActiveObject` returns a weak, untyped object

```python
app = win32com.client.GetActiveObject("AutoCAD.Application")
app.Name          # AttributeError: <unknown>.Name
```

Rebind to the type-library wrapper first:

```python
app = gencache.EnsureDispatch(raw._oleobj_)
```

### 5. `IsQuiescent` is not trustworthy

With Tianzheng loaded, `IsQuiescent` can stay `False` indefinitely even though COM works fine. Never gate logic on it — probe by calling and retrying.

### 6. COM point arguments must be VARIANT arrays

```python
msp.AddLine((x1,y1,z1), (x2,y2,z2))          # -2147024809 invalid argument
msp.AddLine(VARIANT(VT_ARRAY|VT_R8, ...))     # ✅
```

### 7. A read-only `DataStore.dbf` silently kills `HZQT`

Tianzheng's installer marks everything under `SYS` **read-only**. When Tianzheng cannot open `DataStore.dbf` for writing it shows a modal dialog:

```
对不起，打不开DataStore.dbf文件！
```

and **aborts the command**. Because a modal dialog blocks COM, this looks like "the wall command did nothing".

`diagnose_environment` detects this and tells you the exact fix. **The plugin never modifies your Tianzheng installation** — that is your call:

```powershell
attrib -R "C:\Tangent\TArchT20V10\SYS\DataStore.dbf"
# then fully restart AutoCAD via TGStart.exe
```

### 8. Startup timing

A Tianzheng-enabled AutoCAD takes **60–90 s** to accept COM calls. Calls made earlier are rejected.

---

## Safety

1. **Attaches to a running instance only.** Never silently launches a bare `acad.exe` (set `AUTOCAD_ALLOW_NEW_INSTANCE=true` to override).
2. **Drawing ownership isolation.** Drawings created by `create_test_drawing` are persisted to a state file and recognised across restarts. All write operations refuse to touch drawings this server did not create (override: `T20_ALLOW_FOREIGN_DOC=true`).
3. **Never fakes success.** No count increase → `success: false` plus the exact command string, `LASTPROMPT`, and a diagnosis.
4. **Never edits your install.** Read-only detection is report-only.
5. **Busy retry.** `RPC_E_CALL_REJECTED` is retried before erroring.
6. **Single STA thread.** All COM calls are marshalled onto one dedicated thread, because AutoCAD is a single-threaded-apartment server and MCP callbacks arrive on other threads.
7. **Timeout protection.** A command that hangs on a dialog returns an error instead of blocking forever.

### Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `TIANZHENG_ROOT` | auto-detected | Tianzheng install root (the folder containing `SYS`) |
| `T20_ARCH_PYTHON` | auto-detected | Python with `pywin32` + `mcp` |
| `AUTOCAD_ALLOW_NEW_INSTANCE` | `false` | Allow launching AutoCAD when none is running |
| `T20_ALLOW_FOREIGN_DOC` | `false` | Allow commands on drawings this server didn't create |
| `T20_STATE_DIR` | `%USERPROFILE%` | Where the ownership state file lives |

---

## Scope

**In scope:** the natural-language command layer (deterministic parsing, mapping,
validation, execution and error feedback); the wall creation loop, proven
end-to-end; a universal `run_tianzheng_command` that can dispatch any Tianzheng
command; native entities; entity queries.

**Deliberately out of scope:**

- ❌ **Property editing** of Tianzheng objects (wall thickness, opening numbers). Needs `AcadObject` dynamic properties and possibly ARX exports — much harder than command dispatch. Planned for v0.2.
- ❌ **Tuned input sequences for every command.** `HZZW` (axis grid) currently opens a dialog-driven flow whose command-line sequence is **not** tuned yet. This is sequence tuning, not an architecture problem — `run_tianzheng_command` can already send it.
- ❌ Creating `TCH_WALL` directly via COM. Architecturally wrong.

---

## Layout

```
dsh-t20-arch/
├── package.json           # dsh bundle manifest
├── cordis.patch.yml       # DSH profile patch: staging row + MCP client row
├── lib/index.js           # stages bin/ + python/ into %LOCALAPPDATA%
├── bin/
│   ├── run.ps1            # finds/creates a Python with pywin32+mcp, runs the server
│   └── launch.cmd         # convenience wrapper
├── python/
│   ├── requirements.txt
│   └── t20_arch_mcp/
│       ├── server.py           # MCP entry, 18 tools
│       ├── nl_catalog.py        # single source of truth: intent/command/verify_type/params
│       ├── nl_parser.py         # Chinese instruction -> validated command plan
│       ├── nl_dispatch.py       # plan -> backend call + structured Chinese feedback
│       ├── acad_conn.py        # COM attach, STA thread, busy retry, ownership, ARX check
│       ├── tianzheng_runner.py # PostCommand + count polling + command termination
│       ├── tianzheng_query.py  # entity walking + TCH alias matching
│       ├── native_tools.py      # native AutoCAD helpers (VARIANT points)
│       ├── env_check.py         # install discovery + read-only detection
│       ├── sendinput.py         # driver-level key injection
│       └── winmsg.py            # window enumeration / dialog handling
│   └── tests/
│       └── test_nl.py           # 21 regression cases, no AutoCAD/pywin32 needed
├── LICENSE
└── README.md
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `MK_E_UNAVAILABLE` / `0x800401E3` | AutoCAD not running, or different user/elevation | Start AutoCAD via `TGStart.exe`; match DSH's user and elevation |
| `success: false`, ModelSpace unchanged, dialog "DataStore.dbf" | `SYS` is read-only | `attrib -R` the file, restart AutoCAD |
| `unknown command` for `HZQT` | Tianzheng ARX not loaded (bare `acad.exe`) | Launch via `TGStart.exe` |
| Wall created but reported 0 | Verifying with the wrong type name | Use `TCH_WALL` (aliases `TDbWall`/`TDBWALL` are handled) |
| Every COM call rejected | A dialog is open or a command is pending | Press ESC in AutoCAD; use `auto_dismiss_ui` |
| Tools missing in DSH | Plugin not fully reloaded | Fully quit and reopen DSH |
| `stage: "parse"`, `UNKNOWN_INTENT` | Phrasing outside the catalogue | Call `list_capabilities()`; rephrase using one of its example sentences |
| `stage: "parse"`, `MISSING_PARAMETER` | Required coordinate/size not recognised | The error `hint` contains a working example; e.g. give both points for a wall |
| `stage: "execute"`, `verify_type: null` in a command result | Output type not catalogued (e.g. HZZW) | Verification fell back to total ModelSpace count; run `probe_command` to discover the real type |

Logs:

- `%LOCALAPPDATA%\dsh-t20-arch\plugin.log` — staging
- `%LOCALAPPDATA%\dsh-t20-arch\launcher.log` — interpreter discovery and venv

---

## License

MIT — see [LICENSE](LICENSE).

AutoCAD is a registered trademark of Autodesk, Inc. T20天正建筑 is a product of 北京天正工程软件有限公司 (Tangent). This is an independent interoperability tool, not affiliated with or endorsed by either company.
