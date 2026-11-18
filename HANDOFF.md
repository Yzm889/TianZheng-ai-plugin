# 交接文档（HANDOFF）

> 给接手这个项目的 AI 或开发者。
> 写于 2026-11-18，对应提交 `571e39b`。
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
& $py -m pytest tests -q          # 应输出 44 passed
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
├── server.py               MCP 服务入口，24 个工具
├── sendinput.py            SendInput 键鼠模拟、焦点管理
├── winmsg.py               窗口消息、命令行文本读取
├── dialog.py               天正式 #32770 对话框的枚举与按钮点击
├── screen_map.py           DPI 感知、世界坐标 ↔ 屏幕坐标
├── template_clone.py    ★ 模板克隆（复制已有天正对象，见 §4）
├── scene_build.py         分阶段建图 walls→openings→dimensions
├── opening_assist.py    ★ 门窗位置提示（见 §5）
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
| 辅助 | 扫描模板 / 实体分类 / 开工前检查 / 插入门窗助手 | 本插件自研 |

### 明确放弃的（不要浪费时间重试）

| 功能 | 原因 |
|---|---|
| 改墙厚 / 改外墙厚 | 实测无法可靠实现，用户已确认放弃 |
| PDF 导出 | 机器上没有 `.pc3` 绘图仪配置，`PlotToFile` 写不出文件 |
| 立面 / 剖面（`TBudElev` / `TBudSect`） | 必须先在「工程管理」建工程 + 楼层表，纯 UI 操作 |
| 轴网自定义开间 | 对话框里的 Axf 自绘控件，无法写入 |
| 门窗宽度修改 | `ScaleEntity` 只改高度，宽度是天正内部参数 |

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
# 44 passed, 74 subtests
```

现有测试文件：
- `test_nl.py` — 指令解析、别名、参数
- `test_template_request.py` — 缺模板时的请求文案
- `test_prereq_check.py` — 开工前预检
- `test_opening_assist.py` — 门窗助手

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

### 已完成并推送

```
53 条指令 · 24 个 MCP 工具 · 44 passed / 74 subtests
本地 = 远端 = 571e39b · 工作区 clean
```

### 仓库

```
https://github.com/Yzm889/TianZheng-ai-plugin
```

### Marketplace

已向 `awesome-dsh-plugin/awesome-dsh-plugin` 提交 PR **#16178**，
等仓库满 1 天后自动通过（预计 2026-11-17 后已满足）。

### 可以继续做的

1. **F5：`build_plan` 内部的轴网标注** —— 现在会因为禁止嵌套 `_com` 而跳过
2. **门窗宽度区分模板** —— 现在所有门窗宽度都是模板的宽度
3. **PDF 导出** —— 需要装一个 `.pc3` 绘图仪配置
4. **立面/剖面** —— 需要先解决「工程管理」建工程的问题

### 测试图纸

`C:\Users\19891\Desktop\插件创作\教学楼一层平面图.dwg`
（38 面墙 + 16 个正常门窗，还差 5 个门窗）

> 写这份文档时的实测状态（2026-11-18）：
> ```
> pytest            44 passed, 74 subtests          ✅
> server import     OK                              ✅
> CAD 连接          失败（AutoCAD 未运行，非代码问题）
> ```
> 也就是说：**代码是健康的，只是需要先启动 CAD**。

---

## 11. 如果接手后要做的第一件事

```powershell
# 1) 确认代码无损坏（不需要 CAD）
cd C:\Users\19891\Desktop\插件创作\python
& $py -m pytest tests -q
# 期望：44 passed, 74 subtests passed

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
