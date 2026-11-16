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

## Verified capabilities

Measured end-to-end on **T30天正建筑 V2.0 (Build250811)** + **AutoCAD 2022 (`24.1s`)**.
Every row below was confirmed by an actual entity-count increase in a live drawing.

| Capability | Command | Verified object | Status |
|---|---|---|---|
| 墙体 wall | `HZQT` | `TDbWall` | ✅ |
| 门窗 door / window | `MC` | `TDbOpening` | ✅ |
| 轴网 axis grid | `HZZW` | 12 axes, `LINE` on `DOTE` | ✅ |
| 柱子 column | `TGColumn` | `TDbColumn` | ✅ |
| 楼梯 stair | `TLStair` | `TDbLineStair` | ✅ |
| 阳台 balcony | `TBalcony` | `TDbBalcony` | ✅ |
| 天正文字 Tianzheng text | `TText` | `TDbText` | ✅ |
| native line / circle / text | `LINE` `CIRCLE` `TEXT` | AcDb entities | ✅ |
| query / count / save / zoom / diagnose | — | — | ✅ |

**Not automated** (honest scope):

| Capability | Why not |
|---|---|
| 楼板 `TSlab` | Needs a boundary/room pick loop; typed points alone created nothing |
| 轴网标注 `ZWBZ` | Selects axes interactively; no reliable command-line sequence |
| 房间 `TSPace` | Depends on closed room boundaries |
| 图层管理器 `TCGL` | Pure UI command; creates no entity, so it cannot be verified by count |

---

## Install

```bash
dsh plugin add dsh-t20-arch
```

Then **fully quit and reopen DSH**. Tools appear as `mcp__t20_arch__*`.

The launcher finds a Python that already has `pywin32` and `mcp`, or builds a private
venv under `%LOCALAPPDATA%\dsh-t20-arch` on first use.

### Prerequisites

- **Windows** (COM + Win32 input simulation are Windows-only)
- **AutoCAD + T20/T30 天正建筑**, started so that Tianzheng's ARX modules are loaded
- AutoCAD must run as the **same Windows user** as DSH, at the **same elevation**
- To reuse your own Python: `setx T20_ARCH_PYTHON "E:\your-venv\Scripts\python.exe"`

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

## Tools (15)

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

### 11. Elevation must match
`TGStart.exe` may run AutoCAD elevated. A non-elevated DSH then cannot reach it
(`0x80070522 请求的操作需要提升`). Launch AutoCAD at the same elevation as DSH —
a direct `acad.exe` launch from an unelevated shell stays unelevated and works.

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
│       ├── server.py           # MCP entry, 15 tools
│       ├── acad_conn.py        # COM attach, STA thread, busy retry, ownership, ARX check
│       ├── tianzheng_runner.py # stepwise execution, dialog flow, count verification
│       ├── tianzheng_query.py  # entity walking + canonical type matching
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

---

## License

MIT — see [LICENSE](LICENSE).

AutoCAD is a registered trademark of Autodesk, Inc. 天正建筑 is a product of
北京天正工程软件有限公司 (Tangent). This is an independent interoperability tool,
not affiliated with or endorsed by either company.
