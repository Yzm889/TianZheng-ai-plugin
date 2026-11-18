"""模板需求说明书 —— 缺模板时直接告诉用户"请在 CAD 里放一个 X"。

设计原则（来自用户反馈）：不要再自己去安装目录/图库里翻找，也不要用界面
命令强行造模板。缺什么就明确说出需要的组件规格，由用户 10 秒操作补上，
之后插件用模板克隆批量生成。
"""
from __future__ import annotations

from typing import Any

# 每种模板：需要用户在天正里用什么命令、点什么、给出什么
TEMPLATE_GUIDE: dict[str, dict[str, Any]] = {
    "TDBWALL": {
        "name": "墙体",
        "command": "HZQT",
        "steps": [
            "输入 HZQT 回车",
            "点起点、点终点，画一面外墙（建议 6000 长、240 厚）",
            "回车结束",
        ],
        "spec": "任意一段直墙；建议先用天正默认墙厚 240",
        "why": "克隆时按目标长度等比缩放、按方向旋转，所以模板长度不敏感",
    },
    "TDBOPENING": {
        "name": "门窗",
        "command": "MC",
        "steps": [
            "输入 MC 回车，弹出「门窗参数」对话框",
            "门：直接回车确认；窗：先点对话框底部工具栏第 2 个【窗】图标再回车",
            "用鼠标在墙上点一下（这一步天正只接受鼠标，不接受坐标）",
            "回车结束",
        ],
        "spec": "一樘门 + 一樘窗（各一个即可）；每一种宽度各建一樘",
        "why": (
            "MC 的插入点必须鼠标点选，无法用坐标自动化，所以模板必须由人放一次。"
            "另外门窗的宽度是天正内部参数，克隆只能原样复制，"
            "需要不同宽度就必须有不同的模板，并用 register_opening_template 登记宽度"
        ),
    },
    "TDbColumn": {
        "name": "柱",
        "command": "BZZ",
        "steps": ["输入 BZZ 回车", "在空白处点一下插入标准柱", "回车结束"],
        "spec": "一根标准柱（默认 600×600 即可）",
        "why": "克隆时按尺寸缩放",
    },
    "TDbLineStair": {
        "name": "楼梯",
        "command": "TLStair",
        "steps": ["输入 TLStair 回车", "点两个角点画一部直跑楼梯", "回车结束"],
        "spec": "一部双跑楼梯（尺寸不限）",
        "why": "克隆时按包围盒缩放",
    },
}


def missing_template_request(kind: str, needed_for: str = "") -> dict[str, Any]:
    """返回一份给用户看的"请提供组件"说明。"""
    key = kind if kind in TEMPLATE_GUIDE else None
    if key is None:
        # try to match by loose name
        upper = kind.upper()
        for k in TEMPLATE_GUIDE:
            if k.upper().replace("TDB", "") in upper or upper in k.upper():
                key = k
                break
    if key is None:
        return {
            "ok": False,
            "need_user_action": True,
            "message": f"缺少「{kind}」模板，且没有内置说明。请在天正里手动插入一个 {kind} 对象。",
        }

    g = TEMPLATE_GUIDE[key]
    lines = [
        f"请在天正里插入一个{g['name']}，供后续批量克隆使用。",
        "",
        f"命令：{g['command']}",
        "步骤：",
    ]
    for i, step in enumerate(g["steps"], 1):
        lines.append(f"  {i}. {step}")
    lines += [
        "",
        f"规格：{g['spec']}",
        f"说明：{g['why']}",
    ]
    if needed_for:
        lines.insert(0, f"（{needed_for}）")

    return {
        "ok": False,
        "need_user_action": True,
        "stage": "missing_template",
        "template_required": key,
        "command": g["command"],
        "steps": g["steps"],
        "spec": g["spec"],
        "message": "\n".join(lines),
        "next": "放好之后告诉我，我扫描模板句柄并自动克隆出全部实例。",
    }


def missing_opening_width(kind: str, width: float, document: str | None = None) -> dict[str, Any]:
    """缺某个宽度的门/窗模板时，给用户一份可直接照做的说明。

    比 missing_template_request 更具体：说清楚要哪一樘、多宽、怎么登记。
    """
    kind_cn = "窗" if str(kind).lower().startswith("win") else "门"
    try:
        width_cn = "%g" % float(width)
    except (TypeError, ValueError):
        width_cn = str(width)

    steps = [
        "输入 MC 回车，弹出「门窗参数」对话框",
        f"在「宽」里填 {width_cn}，回车确认（%s直接回车即可）" % ("先点【窗】图标再" if kind_cn == "窗" else ""),
        "用鼠标在任意一段墙上点一下（天正只接受鼠标点选，不接受坐标）",
        "把这一樘放在空白处就行，不必放到目标位置",
        "告诉我它的句柄，我登记成 %s 宽的模板（register_opening_template）" % width_cn,
    ]
    lines = [
        f"图上还没有 {width_cn} 宽的{kind_cn}模板，我不替你猜宽度。",
        "",
        "请在天正里建一樘，照着做：",
    ]
    for i, step in enumerate(steps, 1):
        lines.append(f"  {i}. {step}")
    lines += [
        "",
        "为什么必须这样做：门窗宽度是天正内部参数，克隆只能原样复制"
        "（ScaleEntity 只改高度），所以每一种宽度都要有自己的模板。",
    ]
    return {
        "ok": False,
        "need_user_action": True,
        "stage": "missing_opening_width",
        "kind": "window" if kind_cn == "窗" else "door",
        "kind_cn": kind_cn,
        "width": width,
        "command": "MC",
        "steps": steps,
        "message": "\n".join(lines),
        "next": "建好之后调用 register_opening_template(handle, width) 登记，再重跑这条计划。",
    }


def all_requirements() -> dict[str, Any]:
    """列出所有可克隆类型及其模板要求（供 list_capabilities 使用）。"""
    return {
        k: {
            "name": v["name"],
            "command": v["command"],
            "spec": v["spec"],
        }
        for k, v in TEMPLATE_GUIDE.items()
    }
