# 交接文档（HANDOFF）

> 给接手这个项目的 AI 或开发者。
> 写于 2026-11-18，基线提交 `375c313`，其上叠加了 F5（`build_plan` 轴网标注，
> 见 §10）。
> **这份文档的价值在于：省掉你重新踩一遍我踩过的坑的时间。** 每条结论都有实测依据。

---

## 0. 先读这段

这个项目是 **DSH（DeepSeek Harness）的 MCP 插件**，让 AI 能驱动**真实运行中的
AutoCAD + 天正建筑（T20/T30）**，创建真正的天正对象（墙、门、窗、柱、楼梯、房间…）。

**它不是什么**：
- 不是独立的 CAD 软件
- 不解析 DWG 文件格式
- 不绕过天正自己造对象

**它的核心原理一句话**：天正对象只能由**天正自己的进程内代码**创建，所以本插件
用 `SendInput` 键盘/鼠标模拟 + `PostCommand`，把指令"打"进一个正在运行的
AutoCAD 窗口，让天正自己执行。

**天然限制（不是 bug，是架构决定）**：
- 必须有一个开着的 AutoCAD 窗口
- 依赖窗口焦点
- 界面型命令（对话框类）自动化很脆弱

---

## 1. 五分钟上手

### 环境要求

| 项 | 版本 |
|---|---|
| Windows | 10 / 11 |
| AutoCAD | 2019 ~ 2025（2026/2027 **不支持**，天正没有对应 ARX） |
| 天正建筑 | T20 / T30 |
| Python | 3.10+，需 `pywin32` + `mcp` |

### 验证环境

```powershell
$py = "C:\Users\<你>\AppData\Local\Programs\Python\Python313\python.exe"
cd python
& $py -m pytest tests -q          # 应输出 74 passed
```

### 跑起来

```powershell
# 1) 先打开 AutoCAD（天正会自动加载）
# 2) 启动 MCP 服务
cd python
& $py -m t20_arch_mcp.server
```

或者在你自己的 Python 脚本里直接调：

```python
import sys, pythoncom
sys.path.insert(0, r"...\插件创作\python")
import win32com.client as w
from t20_arch_mcp import acad_conn, tianzheng_runner

pythoncom.CoInitialize()
app = w.GetActiveObject("AutoCAD.Application.24")   # 2022 的 ProgID
doc = app.ActiveDocument
acad_conn.remember_active_document(doc)   # 必须！否则会被安全检查拦截

tianzheng_runner.run_with_dialog(
    "HZQT", ["0,0", "6000,0", ""],
    verify_type="TCH_WALL", dialog_title=None, timeout=25, settle=1.6)
```

---

## 2. 代码地图

```
python/t20_arch_mcp/
├── acad_conn.py         ★ 核心：单线程 COM 工作线程 + 所有 _com 包装
├── tianzheng_runner.py  ★ 最大文件(80KB)：所有天正命令的参数编组与执行
├── nl_catalog.py        ★ 53 条指令的规格表（改这里就加指令）
├── nl_parser.py            自然语言 → 意图解析
├── nl_dispatch.py          意图 → 实际调用 + 结果摘要
├── server.py               MCP 服务入口，29 个工具（开 T20_HOST_GUI=1 后 31 个）
├── sendinput.py            SendInput 键鼠模拟、焦点管理
├── winmsg.py               窗口消息、命令行文本读取
├── dialog.py               天正式 #32770 对话框的枚举与按钮点击
├── screen_map.py           DPI 感知、世界坐标 ↔ 屏幕坐标
├── template_clone.py    ★ 模板克隆（复制已有天正对象，见 §4）
├── scene_build.py         分阶段建图 walls→openings→dimensions
├── opening_assist.py    ★ 门窗位置提示（见 §5）
├── opening_library.py   ★ 门窗模板宽度登记表（宽度读不出来，只能登记，见 §5.1）
├── plot_export.py       ★ PDF 导出（COM 异步打印，见 §5.2）
├── gui_select.py        ★ 选择类命令的鼠标点选（见 §5.3，需 T20_HOST_GUI=1）
├── prereq_check.py         开工前需求预检
├── template_request.py     缺模板时给用户的组件规格说明
├── tianzheng_query.py      实体统计、类型直方图
├── native_tools.py         原生 AutoCAD 图元（线/圆/文字/多段线/DXF导出）
└── env_check.py            环境诊断
```

### 三个必须理解的内部机制

**① 单线程 COM（`acad_conn.py`）**
所有 COM 调用都跑在**一个专属工作线程**上。这是为了避开 AutoCAD 的 STA 问题。

> ⚠️ **致命坑**：不要在 `_com` 包装的函数内部再调用另一个 `_com` 包装的函数。
> 会**永久死锁**（内层等外层占用的线程）。
> 正确做法：内部只调 `_..._impl` 裸实现。
> 这个坑在 `build_plan` 上真实发生过，卡死两次。

**② 所有权安全检查**
插件只允许操作**它自己创建的**图纸。

```
T20_ALLOW_FOREIGN_DOC=true   # 覆盖（不推荐）
acad_conn.remember_active_document(doc)   # 或显式登记
```

**③ 焦点与输入**
`SendInput` 只能发到**前台窗口**。所以有 `T20_ALLOW_FOCUS_STEAL`（默认开）。
`PostMessage` 发字符 AutoCAD 不理，但发 ESC 可以取消命令。

---

## 3. 已验证能力（每项都有实体增量证据）

| 类别 | 指令 | 天正命令 |
|---|---|---|
| 轴网 | 轴网标注 | `ZWBZ` |
| 墙体 | 画墙 / 闭合墙 | `HZQT` |
| 门窗 | **见 §5（必须人工点选）** | `MC` |
| 柱 | 插柱 | `TGColumn` |
| 楼梯 | 楼梯 | `TLStair` |
| 阳台 | 阳台 | `TBalcony` |
| 楼板 | 楼板 | `TSlab` |
| 房间 | 搜索房间 | `TUpdSpace` |
| 文字 | 天正文字 / 多行文字 | `TText` / `TMText` |
| 表格 | 新建表格 | `TNewSheet` |
| 标注 | 快速标注 / 逐点标注 / 墙厚标注 | `TQuickDim` / `TDimMP` / `TDimWall` |
| 图框 | 插入图框 / 填写图框 | `TTitleFrame` |
| 符号 | 标高 / 图名 / 剖切符号 / 索引图名 / 剖切索引 / 箭头引注 / 引出标注 | `TMElev` 等 |
| 其他 | 台阶 / 坡道 / 任意坡顶 / 剖面墙 / 双线楼板 | `TJ` / `PD` / `RYPD` / `sdwall` / `sdfloor` |
| 排版 | 排版 / 套版 / 图框清单 | 组合指令 |
| 导出 | 导出DXF | `SaveAs(type=16)` |
| 辅助 | 扫描模板 / 实体分类 / 开工前检查 / 插入门窗助手 / 登记门窗模板宽度 | 本插件自研 |

### 明确放弃的（不要浪费时间重试）

| 功能 | 原因 |
|---|---|
| 改墙厚 / 改外墙厚 | 实测无法可靠实现，用户已确认放弃 |
| 立面 / 剖面（`TBudElev` / `TBudSect`） | 必须先在「工程管理」建工程 + 楼层表，纯 UI 操作 |
| 轴网自定义开间 | 对话框里的 Axf 自绘控件，无法写入 |
| 门窗宽度修改 | `ScaleEntity` 只改高度，宽度是天正内部参数（要不同宽度只能建不同宽度的模板并登记，见 §5.1） |

---

## 4. 模板克隆（本项目最有价值的技术点）

**原理**：不调天正命令，而是把图里**已有**的天正对象复制一份，再 `Move`/`Rotate`/`Scale` 到目标位置。

**为什么重要**：绕开了所有界面命令的崩溃风险。

**实测数据**：
```
模板墙 6000×200 → 一次调用克隆 4 面
2C7 (-0,-100)-(12000,100)   12000×200   精确 ✅
2C8 (11900,0)-(12100,6000)  200×6000    精确 ✅
```

**用法**：
```python
from t20_arch_mcp import template_clone
scan = template_clone.scan_templates()          # 先看看有什么可用模板
template_clone.clone_walls_batch("2C6", [
    {"x1": 0, "y1": 0, "x2": 12000, "y2": 0},
])
```

**⚠️ 关键限制**：克隆出来的是**图形副本**，不建立命主关系。
- 墙体 ✅ 完全可用
- **门窗 ❌ 不行** —— 墙不会开洞（见 §5）

---

## 5. 门窗为什么必须人工点选（最重要的一节）

这是我花了最长时间才搞清楚的事。**别再重试这些路**：

| 尝试 | 结果 | 证据 |
|---|---|---|
| COM `Copy()` 复制已有门窗 | 外观对，**墙不开洞** | 墙段数始终为 1 |
| 把副本 `Move` 到墙轴心线 | 图形对齐，墙仍实心 | 移动前后墙段数都不变 |
| `TValidOp`（门窗检查） | 无效 | 输入 `ALL` 触发 `PLAY` 命令 |
| 调 `tch_kernal.arx` 的 `insertIntoWall` | 进程外不可行 | `LoadLibrary` 失败（缺 ObjectARX）；符号非标准导出 |
| 第三方 DLL（CodexAutoCadMcpBridge） | 不能给所有人用 | 句柄硬编码在原作者图纸里；也不能再分发 |

**根因**：`MC` 的插入点**只接受鼠标点选**。给坐标会报"未知命令"。

### 正确做法：`opening_assist.py`

```
1. make_plan(items)        算出每个门窗插入点（落在墙轴心线上）
2. mark_on_drawing(items)  画位置提示：
      窗 → 在墙体内画方框（左右竖线=窗宽，上下横线=墙厚）
      门 → 建筑外侧画箭头
3. 用户执行 MC，照标记点一遍
4. verify(items)           核对
5. clear_marks()           删标记
```

**为什么窗和门标记画法不同**（用户实测得出的结论）：

| 类型 | 包围盒 | 标记策略 |
|---|---|---|
| 窗 | 高约 100，扁平 | **画在墙里**，用户点框中间 |
| 门 | 高约 2279（含开启弧） | **画在墙外**，箭头指引 |

**⚠️ 验证标准（我一开始搞错的）**：
```
❌ 错：看墙有没有被切成多段
✅ 对：看门窗对象包围盒高度 > 100（退化对象高度为 0）
```
天正的墙**永远是一个完整对象**，洞口信息存内部数据，不会把墙拆开。

### 5.1 门窗宽度：读不出来，只能登记

**为什么读不出来（实测 2026-11-18，`Drawing31.dwg` 的 4 个 `TDbOpening`）**：

| 尝试 | 结果 |
|---|---|
| `GetXData("TCH"/"TDb"/"TDbOpening"/...)` | 全部 `[null, null]` |
| `Width` / `Height` / `Rotation` 属性 | 读不到（AttributeError） |
| `GetBoundingBox` | 混了高度：1000 宽 → `1000×975`；900 宽 → `900×877.5`；1800 宽 → `1800×1755` |

所以 **不要试图从几何或 XData 猜宽度**，会猜错。正确做法是登记：

```python
opening_library.register("4C4", 1000, "window", document=当前图纸名)  # 只登记一次，持久化
```

`build_plan` 的 `openings` 里给了 `width` 时，只在登记过的同宽度模板（±50 mm）里选；
选不到就返回 `stage: "missing_opening_width"`，把"要建哪一樘、多宽、怎么登记"讲清楚，
**一个都不克隆**。没给 `width` 时退回第一个模板，但结果里带 `width_unverified` 警告。

实测证据（临时副本，`repro/opening_width_check.py`）：

```
A. 声明 1000 宽 + 模板 4C4  -> TDbOpening 4 -> 5，成品包围盒 1000×975
B. 要 1500 宽，模板只有 1000 -> 拒绝动手，TDbOpening 停在 5
```

### 5.2 PDF 导出（曾经的"不可能"其实是两条误判）

早期结论「机器上没有 `.pc3`，PDF 出不来」是错的：

| 误判 | 实际 |
|---|---|
| 没有 `.pc3` | `%APPDATA%\Autodesk\AutoCAD 2022\R24.1\chs\Plotters\` 自带 `DWG To PDF.pc3` |
| `PlotToFile` 写不出文件 | 它是**异步**的，返回时文件还不存在，约 20 s 后才落盘 |

真正要绕的坑：

- `IAcadPlot` 上**没有** `PlotConfigurations` 集合，调用直接 `AttributeError`；
  绘图仪必须设在 layout：`ActiveLayout.ConfigName = "DWG To PDF.pc3"`；
- 新图的 `ConfigName` 读出来是 `"无"` —— 这才是"调用成功但没输出"的原因；
- 图中的墙线是白色，打在白底 PDF 上等于隐形，要套 `monochrome.ctb`；
- 设置后立刻读属性会撞 `RPC_E_CALL_REJECTED`，每步都要重试；
- 判定成功必须轮询到文件大小稳定，0 字节算失败。

实测（`Drawing31.dwg` 副本）：58 662 字节、`%PDF-`、等待 22 s，样例
`docs/示例-Drawing31平面图.pdf`。复现 `repro/pdf_export_check.py <dwg> <out.pdf>`。

### 5.3 选择类命令：GUI 层（需 T20_HOST_GUI=1）

「半径标注 / 面积统计 / 填充面积 / 房间排序 / 墙体分段」这类命令第一步都是
**选择对象**，COM 替不了（`SelectOnScreen` 也是交互的）。有桌面控制的宿主可以：

```
句柄 → 屏幕坐标（screen_map.world_to_screen）→ 真点 → 回车 →（可选）点定位点 → 核对增量
```

实测证据（`Drawing31.dwg` 副本）：

```
A. click_targets 点 2 个房间 -> PickfirstSelectionSet {4CF, 4D0}（2/2 命中）
B. TDimRad: 点圆 5D0 -> 回车接受"是否为该对象[Y]" -> 点 4100,4050
   模型空间 56 -> 57，直方图新增 CIRCLE 1 + TDBRADIUSDIM 1
```

三个必须记住的点：

- **`Document.SendCommand` 不能用在这里**：它是同步的，命令挂着等鼠标时调用会一直阻塞
  （实测 TDimRad 发出后 120 s 超时）。要改用键盘输入（`_type_command`）；
- **点选点不能取包围盒中心**：墙是双线，中心在空隙里；门的包围盒含开启弧。线段取中点、
  圆/弧取圆周上的点；
- **不是所有"选择类"都是纯选择**：`TTextAddFrame` 会弹模态对话框，点完会把 CAD 卡在
  `#32770` 对话框上（要真 ESC 才能解），所以只收录实测过的命令。

能力门控：默认不注册 `gui_click_targets` / `gui_select_command`；`T20_HOST_GUI=1` 才暴露。
给没有 GUI 的宿主的统一提示写在 `capability_report` 里（也写进了 MCP `instructions`）：
**想要更顺的操作、并用到这些功能，建议换用具备图形界面控制能力的 AI 工具（如豆包 PC 客户端）
来驱动本插件。**

---

## 6. 坑清单（按重要性排序）

| # | 坑 | 解法 |
|---|---|---|
| 1 | 嵌套 `_com` 调用死锁 | 内部只调 `_..._impl` |
| 2 | `SendCommand` 在面板期间死锁 | 用 `PostCommand` |
| 3 | 天正对话框按钮 `BM_CLICK` 无效 | 必须真实鼠标 `SendInput` |
| 4 | 首次按键被吞 | 发无副作用的 Shift 热身 |
| 5 | 点绘图区"聚焦"会触发窗选甚至 `_HELP` | 用 `SetFocus` 给 DirectX 视图 |
| 6 | DPI 未感知导致窗口坐标偏 1.25× | `SetProcessDpiAwareness(2)` |
| 7 | `GetVariable("VIEWCTR")` 不可信 | 用 `ActiveViewport` 或 `calibrate_view` |
| 8 | `Move` 第二参数是**绝对目标点**，不是位移 | 传 `base + delta` |
| 9 | 图框包围盒不准 | 记 `frame_rect.json` 并集 |
| 10 | `TMText` 对话框循环弹出 | 真实 ESC + `finally` 清扫残留对话框 |
| 11 | `Copy()` 已自动加入模型空间 | 不要调 `ModelSpace.AddItem`（不存在） |
| 12 | 版本文件夹不可预测 | 从 AutoCAD profile 读，如 `sys24x64` |
| 13 | 杀毒软件会静默杀掉天正 | 检查 `uxtheme.dll` 是否被隔离 |
| 14 | `DataStore.dbf` 只读会中断命令 | `attrib -R` 后重启 CAD |
| 15 | 提权不一致导致 COM 失败 `0x80070522` | DSH 和 CAD 必须同权限级别 |
| 16 | `github.com:443` 被重置导致 `dshpub` 装不上 | `git config --global url."git@github.com:".insteadOf "https://github.com/"` |
| 17 | `dshpub` 报 `spawn dsh ENOENT` | 桌面版 `dsh` 不在 PATH；改用桌面版插件管理器安装 |
| 16 | 直方图的 key 是**归一后**的类名（`TDBAXISLABELSET`），拿 `TCH_AXIS_LABEL` 去查永远得 0 | 计数一律走 `count_entities()`（带别名匹配），直方图只用来读原始类名 |
| 17 | 门窗宽度**读不出来**（XData 空、无 Width 属性、包围盒混高度），`ScaleEntity` 只改高度 | 用 `register_opening_template` 登记宽度，`build_plan` 按登记值匹配；读不到就报错，不许猜 |
| 18 | `PlotToFile` 返回 ≠ 文件写好（异步，约 20 s），`IAcadPlot` 也没有 `PlotConfigurations` | 绘图仪设在 `ActiveLayout.ConfigName`（默认「无」= 静默失败）；套 `monochrome.ctb`；轮询到大小稳定才算成功 |
| 19 | 给"选择类"命令用 `Document.SendCommand` 会死等鼠标（实测 120 s 超时） | 改用键盘输入命令（`gui_select._type_command`），再自己点、自己回车 |
| 20 | `TTextAddFrame` 等命令会弹模态对话框，点选后 CAD 卡在 `#32770` | 只收录实测过的纯选择类命令；卡住先真 ESC / 关窗再继续 |

---

## 7. 加一条新指令怎么做

```python
# 1) python/t20_arch_mcp/nl_catalog.py  —— 加规格
CommandSpec(
    key="my_command",
    aliases=("我的指令", "MyCommand"),      # 中文别名 + 英文名
    backend="tianzheng_custom",              # 或新建 backend
    modes=(),
    verify_type="TCH_XXX",                   # 用来验证实体增量
    params=(ParamSpec("width", "number", required=False),),
    example="我的指令 宽=1500",
    note="一句话说明",
),

# 2) python/t20_arch_mcp/nl_dispatch.py —— 若需新 backend，加分支
# 3) 跑测试
#    cd python; python -m pytest tests -q
```

**必须验证**：用 `tianzheng_query.entity_type_histogram()` 对比前后实体数。
**不允许**在没有实体增量证据的情况下声称成功。

---

## 8. 测试约定

```powershell
cd python
python -m pytest tests -q
# 74 passed, 74 subtests
```

现有测试文件：
- `test_nl.py` — 指令解析、别名、参数
- `test_template_request.py` — 缺模板时的请求文案
- `test_prereq_check.py` — 开工前预检
- `test_opening_assist.py` — 门窗助手
- `test_scene_build_axis.py` — `build_plan` 内部轴网标注（F5），守住「不嵌套
  `_com`」和「没 DOTE 轴线先提示」两条原则
- `test_opening_library.py` — 门窗模板宽度登记表（按图纸分区、容差、种类不串）
- `test_scene_build_openings.py` — 宽度路由：命中才克隆、缺宽度先要模板、没给
  宽度必须标 `width_unverified`
- `test_plot_export.py` — 选绘图仪的优先级、`PlotToFile` 异步等待、0 字节不算成功
- `test_gui_select.py` — 能力门控、点选点算法（线段中点/圆周/别用包围盒中心）、视图判定

**测试里有一些"原则守护"用例**，例如：
```python
# 不允许在提示里出现"安装目录/图库/搜索/自己找"
for bad in ("安装目录", "图库", "搜索"):
    self.assertNotIn(bad, message)
```
这些是用户明确要求的行为约束，**不要为了过测试而删掉它们**。

---

## 9. 用户的工作方式（重要）

从长期协作中总结，**请遵守**：

1. **不要"一句话成图"** —— 要原子化、可组合的指令，不要预设的整套生成
2. **不要盲点坐标** —— 先截图确认，再点击
3. **缺组件时主动要，不要自己翻找** —— 直接说"请建一个 1500 宽的门"，不要搜安装目录
4. **动手前先做需求预检** —— 分清"我能造"和"需要你建"
5. **不要反复实验用户的 CAD** —— 崩溃三次就该停下来换方案
6. **只报告实测结果** —— 没有实体增量证据就不算成功
7. **同一个图纸复用** —— 不要为每个功能新开一张图（曾经开过 115 张）
8. **遇到循环问题立刻停** —— "做到出现循环问题的时候一定要停下来，开发下一个功能"

---

## 10. 当前状态与未完成

### 已完成

```
53 条指令 · 29 个 MCP 工具（GUI 档 31 个）· 83 passed / 74 subtests
本地领先远端 4 个提交 · 工作区 clean
```

### 已完成并实测：F5（`build_plan` 内部的轴网标注）

`build_plan` 的 `dimensions` 里 `kind: "axis"` 不再跳过，改为在 builder 内部调
`tianzheng_runner._annotate_axis_impl`（裸实现，不走 `_com`，因此不会自锁）。
动手前先数 `DOTE` 图层的 `LINE`：为 0 就直接返回「请先用绘制轴网 HZZW 建轴网」，
并写 `axis_dote_lines` 进结果；成功的条目记在 `axis_labels`。

**实测证据**（2026-11-18，`Drawing1.dwg`，8 条 DOTE 轴线 = 4 竖 + 4 横）：

```
标注前  类型直方图 {"LINE": 8}
取点    22092.60,26686.98 → 26892.60,26686.98   （两根最外侧竖轴，同一 y）
返回    dimension_count 1 · axis_dote_lines 8 · axis_labels[0].counts 0 → 1
标注后  类型直方图 {"LINE": 8, "TDBDIMENSION2": 4, "TDBAXISLABELSET": 1}
```

轴号集 `0 → 1`、轴间尺寸 `0 → 4`，与本项目对 ZWBZ 的历史实测（1 个轴号集 +
4 个 `TDbDimension2`）一致。复现：

```powershell
& $py repro\f5_axis_check.py            # 真的标一次并对比前后
& $py repro\f5_axis_check.py --inspect  # 只看几何与现状，不发命令
```

> 脚本里有个坑：直方图把类名归一成 `TDBAXISLABELSET`，直接找
> `TCH_AXIS_LABEL` 会永远得到 0，必须走 `count_entities` 的别名匹配。

### 已完成并实测：门窗宽度区分模板

宽度读不出来（见 §5.1），所以走"登记 + 匹配"：`opening_library.register()` 记一次
真实宽度（按图纸名分区持久化），`build_plan` 只在登记过的同宽度模板里选，选不到就
返回 `stage: "missing_opening_width"` 并说清要建哪一樘——**不会静默用错宽度的模板**。
新增 MCP 工具 `register_opening_template` / `list_opening_templates`。

**实测证据**（2026-11-18，`Drawing31.dwg` 临时副本，4 个 `TDbOpening`）：

```
A. 声明 1000 宽 + 模板 4C4   -> TDbOpening 4 → 5，新句柄 5CA，成品包围盒 1000×975
B. 要 1500 宽，模板只有 1000 -> 返回 missing_opening_width，TDbOpening 停在 5
```

复现：`& $py repro\opening_width_check.py <dwg副本路径>`（跑完关闭不保存）。

### 已完成并实测：端到端串联回归

之前 F5 与门窗宽度都只是**单项**实测，整链（walls → openings → dimensions）没跑过，
"中途失败保留前序"只是单测假设。`repro/e2e_chain_check.py` 把它变成观测：

```
# 整链（Drawing31.dwg 副本：10 墙 / 4 门窗 / 8 条 DOTE 轴线）
walls 10 -> 12 · openings 4 -> 6 · TDbDimension2 7 -> 12（+1 线性 +4 轴间）
TDbAxisLabelSet 1 -> 2 · completed_stages ["walls","openings","dimensions"] · ok true

# 中途失败（要 1500 宽，但只有 1000 模板）
walls 10 -> 12（保留）· openings 停在 4 · dims 不变
completed_stages ["walls"] · failed_stage "openings" · stage missing_opening_width
```

两个量测陷阱：直方图偶尔把某个 `LINE` 读成 `<unknown>`（总数不变），**不要**拿单个
直方图 key 跨 run 比较，计数走 `count_entities()`；线性标注不增加 `TDbAxisLabelSet`，
只有轴网标注会。

### 已完成并实测：PDF 导出（`export_pdf`）

`IAcadPlot` 没有 `PlotConfigurations`，绘图仪只能设在 `ActiveLayout.ConfigName`；
`PlotToFile` 是异步的（约 20 s 才落盘），所以必须轮询；白线配 `monochrome.ctb`。

```
config DWG To PDF.pc3 · extents · monochrome.ctb
58 662 字节 · %PDF- · 等待 22 018 ms
```

样例 `docs/示例-Drawing31平面图.pdf`；复现
`& $py repro\pdf_export_check.py <dwg副本> <out.pdf>`。

### 已完成并实测：GUI 层（选择类命令）

`gui_click_targets` / `gui_select_command`（需 `T20_HOST_GUI=1`，默认不注册）。

```
A. click_targets -> PickfirstSelectionSet {4CF, 4D0}（2/2 命中）
B. TDimRad -> 模型空间 56 -> 57，新增 CIRCLE 1 + TDBRADIUSDIM 1
```

复现 `& $py repro\gui_select_check.py <dwg副本>`。坑见 §5.3 / §6 第 19、20 条。

### 可以继续做的

1. **立面/剖面** —— 需要先解决「工程管理」建工程的问题

### 2026-11-18 晚：GUI 档 8 类命令全部跑通 + 验收样图

`repro\gui_style_drawing.py` 一条脚本建出样板图，**11/11 通过**（自己核对实体增量）：

```
[1] 轴网 HZZW            PASS  8 条 DOTE 线
[2] 墙体 HZQT            PASS  0 -> 4 面 TDbWall
[3] 房间 TUpdSpace       PASS  0 -> 1 个 TDbSpace（22.40 m²）
[4] 房间排序 TSortSpace  PASS  命令跑完（不新增实体）
[5] 面积统计 TCountArea  PASS  +1 TDbSheet（1001/房间/22.40）
[6] 半径标注 TDimRad     PASS  +1（点圆周 45° 点，不补回车）
[7] 直径标注 TDimDia     PASS  +1（必须给 ARC，整圆会被拒）
[8] 填充面积 THatchArea  PASS  +1 TDbSheet（ANSI31/5.40）
[9] 文字加框 TTextAddFrame PASS +1
[10] 单轴标注 TSingleAxisDim PASS +1 TDbAxisLabelSet
[11] 墙体分段 TPartwall  PASS  命令跑完（分段不新增实体）
```

成图：`docs\GUI功能验收样式图.png` / `.dwg`。

**本轮新增的可用能力（`gui_drive`）**
- `zoom_window=[[x1,y1],[x2,y2]]`：直接给世界窗口。用来把"还没有实体的插入点"
  （表格插入点、标注位置）也框进视野——否则那个点击会落到命令行上，表现为
  "命令跑完了但没产出"。
- `confirm_enter=False`：尺寸类命令点选后**不要**补回车，补了会被当"退出"。

**缩放口径（用户反复强调过，别再改）**：ZOOM Extents + 留白 15%（`zoom_fill=0.85`），
对象只占画布 15–25%。不要把对象放到贴满界面——边框和界面重合就没法窗选了。

**本轮修掉的三个真凶**
1. `gui_select._count_impl` 里那句 `tianzheng_query.count_entities(kind)`：`TCH_WALL`
   一次卡 25 s 并把 COM 线程堵死，后面 `SaveAs`/计数全超时。已改成本地遍历
   `ObjectName`（并做 `TCH_WALL→TDbWall` 别名）。
2. `SaveAs` 报「保存文档时出错」(-2145320861) = **目标 dwg 被另一个已打开的文档占着**。
   解法：每次跑用唯一工作文件名（`gui_style_work_<HHMMSS>.dwg`），建图后**立刻先存一次**
   （且这一步不要先按 ESC），末了 `doc.Save()` 再复制成交付名。
3. `export_pdf` 出来的 PDF **缺所有天正对象**（TDbWall/TDbSpace/TDbSheet/TDb*Dim），
   轴网/圆/填充/原生文字正常；`PROXYGRAPHICS=1` 也无效。正式出图要用天正自带打印
   或先分解。这条写进 CHANGELOG 的 Known limits。

**给 DSH 用户的提示已落地**：`server.py` 里的 `GUI_HINT_ZH`，由
`capability_report.user_hint_zh` 返回；`run_tianzheng_command` 跑到选择类命令且
实体没动时也随结果带回。话术要求：初次会话说一次、第一次点不动再说一次，别每轮重复。

### 仓库

```
https://github.com/Yzm889/TianZheng-ai-plugin
```

### Marketplace

已向 `awesome-dsh-plugin/awesome-dsh-plugin` 提交 PR **#16178**，
等仓库满 1 天后自动通过（预计 2026-11-17 后已满足）。

### 可以继续做的

1. **PDF 导出** —— 需要装一个 `.pc3` 绘图仪配置
2. **立面/剖面** —— 需要先解决「工程管理」建工程的问题

> F5（内部轴网标注）与门窗宽度区分模板都已完成并实测，见 §10。

### 测试图纸

`C:\Users\19891\Desktop\插件创作\教学楼一层平面图.dwg`
（38 面墙 + 16 个正常门窗，还差 5 个门窗）

> 写这份文档时的实测状态（2026-11-18）：
> ```
> pytest            74 passed, 74 subtests          ✅
> server import     OK                              ✅
> CAD 连接          OK（AutoCAD 2022 + 天正 T20 正在运行）
> ```

---

## 11. 如果接手后要做的第一件事

```powershell
# 1) 确认代码无损坏（不需要 CAD）
cd C:\Users\19891\Desktop\插件创作\python
& $py -m pytest tests -q
# 期望：103 passed, 74 subtests passed

# 2) 确认 MCP 服务可导入（不需要 CAD）
& $py -c "import sys;sys.path.insert(0,'.');import t20_arch_mcp.server;print('OK')"
# 期望：OK

# 3) 确认能连上 CAD —— 这一步必须先手动打开 AutoCAD + 天正！
& $py -c "import sys;sys.path.insert(0,'.');import pythoncom,win32com.client as w;pythoncom.CoInitialize();a=w.GetActiveObject('AutoCAD.Application.24');print(a.Version, a.ActiveDocument.Name, len([x for x in a.ListArx() if 'tch' in x.lower()]))"
# 期望：24.1s DrawingN.dwg 32
# 若报 (-2147221021, '操作无法使用') 说明 AutoCAD 没开，或 ProgID 不对：
#   AutoCAD 2022 -> AutoCAD.Application.24
#   AutoCAD 2025 -> AutoCAD.Application.25
# 可用 progid 列表也写在 README 里

# 4) 读这两份文档，比读代码快
#    README.md                —— 完整能力清单 + 29 条硬核细节
#    docs/插入门窗指南.md      —— 门窗必须人工点选的原因与操作
```

---

## 12. 交接清单：纯文本宿主怎么把活交出去（2026-11-18 新增）

这是本轮最后加的一块，解决"DeepSeek 做不了点选，用户又不知道该怎么接"的断点。

**工具**：`handoff_to_gui_host(pending, out_path=None)`，只读图、不画东西，
**不要求 `T20_HOST_GUI=1`**，所以纯文本宿主也能调。

```python
handoff_to_gui_host(pending=[
    {"kind": "radius_dim"},
    {"kind": "面积统计"},          # 中文也认
    {"kind": "wall_split", "handles": ["1A"]},   # 想指定就指定
], out_path=r"docs\交接清单.md")
```

`pending` 里最省事的写法只给 `kind`，其余全自动：挑目标句柄、算点选点、算命令要的
点位。9 类 kind 与天正命令的对应写在 `handoff.py` 的 `KIND_SPEC`。

**返回的关键字段**

| 字段 | 含义 |
| --- | --- |
| `handoff_text` | 可直接贴给下一个 AI 的中文清单（路径/已有实体/待办编号/护栏） |
| `pending[].handles` | 自动挑好的目标句柄（圆/弧/房间/填充/文字/墙/DOTE 线） |
| `pending[].pick_points` | 一定点得中的点：圆/弧 45° 圆周、线中点、其余中心 |
| `pending[].points` | 命令自己要的点位：尺寸线位置 / 表格插入点 / 墙体基线点 |
| `pending[].already_looks_done` | 图上似乎已有产出（类型级判断，只提醒不下结论） |
| `pending[].note` | 图里找不到源对象、或该命令根本没跑通时的说明 |
| `written_to` | 传了 `out_path` 才有的落盘路径 |

**接手方（带 GUI 的 AI）拿到清单后干什么**：逐条跑 `gui_select_command`，
`handles` 和 `labels/points` 直接用清单里的值，跑完用 `capability_report` 之外的
实体增量核对（清单里写了每项 `expect_delta`）。

**踩过的两个坑**

1. 中文别名模糊匹配如果按字典顺序命中，`填充面积` 会被更短的 `面积` 抢走 →
   改成"取最长别名"（`_resolve_kind`）。
2. 清单里点位直接打 float 会出现 `1933.3333333` 这种读不下去的数字 →
   统一走 `_fmt()`（1 位小数，整数值不带 `.0`）。

**测试**：`python\tests\test_handoff.py` 20 例，全量 `103 passed, 74 subtests`。

**2026-11-18 真图实测（`docs\GUI功能验收样式图.dwg`，不靠假数据）**：跑通后抓到三个真问题，
都已修：

1. **直径标注的类名猜错了**：天正的半径标注和直径标注**都产出 `TDbRadiusDim`**
   （真图里 `TDbRadiusDim: 2` 就是一次半径 + 一次直径）。原来 `FINISHED_RULES` 用
   `"DIADIM"` 关键字永远匹配不到 → 直径项永远显示"没做"。现在改成同类型 + 数量门槛
   （≥2 个才认为两种都有），并在清单里注明"按数量推断"。
2. **不支持项缺字段**：`parking_number` / 无法识别的 kind 走的是 `unsupported` 分支，
   entry 里没有 `handles`，调用方读 `entry["handles"]` 直接 `KeyError`。现在三条分支
   都保证有 `handles / pick_points / points`。
3. **表格插入点越排越远**：原来按"图形 bbox 右下方"算，而 bbox 里包含了上一轮排好的
   表格和轴号圈，实测 `max_x` 从 13200 被撑到 17506，新表插到 18706 去了。现在
   排版基准只算图形本体（排除 `TDBSHEET / TDBAXISLABELSET / *DIM*`），并对齐左边缘、
   往正下方排：实测变成 `-700,-2200` / `-700,-5600`。

真图实测的原始类型统计（可作后续核对基准）：`AcDbLine 8 / TDbWall 4 / TDbSpace 1 /
TDbSheet 2 / AcDbCircle 1 / AcDbArc 1 / TDbRadiusDim 2 / AcDbPolyline 2 / AcDbHatch 1 /
AcDbText 1 / TDbAxisLabelSet 1`。
**仍不确定**：`TParkingNumber` 依旧跑不通（前置 `TParkingSpace` 只回「*取消*」）。

---

## 13. 这台机器的 Git 推送（2026-11-18 定，别再踩）

`github.com:443` 直连会被重置：

```
fatal: unable to access 'https://github.com/...': Recv failure: Connection was reset
Test-NetConnection github.com 443      → False
Test-NetConnection ssh.github.com 443  → True    ← 唯一通的路径
```

**解法是 SSH-over-443**，不是代理（本机没有代理进程，`127.0.0.1:7877` 只是 IE 设置里的历史残留）：

- `~/.ssh/config` 的 `Host github.com` 已加 `Hostname ssh.github.com`、`Port 443`、`User git`
  （原文件备份为 `~/.ssh/config.bak-20261118`）
- remote 已从 https 换成 `git@github.com:Yzm889/TianZheng-ai-plugin.git`

验证：`ssh -T git@github.com` → `Hi Yzm889! You've successfully authenticated`；
`git push` → `Everything up-to-date`。

所以我这边和 DSH 那边都是**直接 `git push`** 就行，不需要额外通道。注意 key 路径在
`core.sshCommand` 里必须写成正斜杠（`C:/Users/19891/.ssh/...`），反斜杠会被 git 吃掉。

---

## 14. 宿主侧闭环验证（2026-11-18，不用装 DSH 也能验）

结论先写：**从 MCP 协议到天正 COM 整条链已验证通过**，用的就是 DSH 的启动方式。

**验法**：用 `mcp` SDK 起 stdio 客户端当宿主（跟 DSH 的 MCP client 同一条路），
两条都跑了：

1. 直启：`python -m t20_arch_mcp.server`（cwd=`python\`，`PYTHONPATH` 指过去，
   env 里**删掉** `T20_HOST_GUI`，模拟纯文本宿主）。
2. 走安装链：`powershell.exe -NoProfile -ExecutionPolicy Bypass -Command
   '& (Join-Path $env:LOCALAPPDATA "dsh-t20-arch\bin\run.ps1")'`，也就是
   `cordis.patch.yml` 里那条 MCP 行的原样。

**两条都返回**：`TOOLS 30`、`handoff_to_gui_host` 在列、`gui_*` 为空
（`host_gui=false`，符合纯文本宿主预期）、`user_steps_zh` 301 字、
`handoff_to_gui_host({"pending":[{"kind":"hatch_area"}]})` 返回真图数据
（`DOC GUI功能验收样式图.dwg`、句柄 `2E0`、插入点 `-700,-2200`）。

**顺手抓到的安装阻断**（已修，见 `lib/index.js::copyTree`）：`fs.cpSync` 递归复制
在本机是 `0xC0000409` 直接杀 node，单个文件复制却正常。改成手写递归后 `apply()` 一次过。
如果哪天又看到 staging 里只有空 `bin`/`python`，先看 `plugin.log` 有没有 `apply done`。

**注意**：`%LOCALAPPDATA%\dsh-t20-arch` 里现在只有 `venv` 是 11-16 留下的，`bin`/`python`
是这次重新 stage 的。DSH 若真要加载，重新触发一次插件加载即可（不必重装 npm 包）。

---

## 附：关键常量

```python
# 天正 ARX 文件夹（每个 CAD 版本一个）
sys19x64 … sys25x64     # 2026/2027 没有
# 如何确定：读 AutoCAD profile，不要猜

# 天正标题栏 24 个属性
建设单位 名称 子项名称 工程编号 图号 图别 比例 日期
设计 制图 校对 审核 审定 专业负责人 方案设计人
建筑 结构 给排水 暖通 电气 合作单位

# 环境变量
TIANZHENG_ROOT           天正安装根目录
T20_ARCH_PYTHON          指定 Python
AUTOCAD_ALLOW_NEW_INSTANCE   默认 false
T20_ALLOW_FOREIGN_DOC    默认 false
T20_ALLOW_FOCUS_STEAL    默认 true
T20_RETRY_ON_FAILURE     默认 1
T20_ALLOW_REAL_ESCAPE    默认 0
T20_STATE_DIR            状态目录
T20_CLICK_TO_FOCUS       默认 0
```
