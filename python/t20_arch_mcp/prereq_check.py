"""需求预检 —— 动手前先算清楚"哪些组件我自己能造，哪些必须用户给"。

用户要求的工作流：
   收到指令 -> 解析出需要的组件 -> 自己能造的列出、造不了的列成清单
            -> 一次性问用户"请帮我建这 N 个"
            -> 用户建好后我扫描到句柄 -> 直接克隆，不返工

对应场景：用户说"建一个教室"，插件应该先回答：
   "需要 1 个墙体模板（我能用 HZQT 造）；门窗需要你放 1 樘门 + 1 樘窗。"
而不是直接开画一半，卡在门窗上再来问。
"""
from __future__ import annotations

import re
from typing import Any

from . import template_request

# 造得出来的：走命令就能生成（HZQT/TSlab/TUpdSpace/TLStair 等）
SELF_BUILDABLE = {
    "墙": ("TDBWALL", "HZQT 可直接画，无需模板"),
    "楼板": ("TDBSLAB", "TSlab 可直接画"),
    "房间": ("TDBSPACE", "TUpdSpace 可直接标"),
    "尺寸": ("TDBDIMENSION", "TQuickDim / TDimMP 可直接标"),
    "轴网": ("LINE", "HZZW 可直接画"),
    "文字": ("TDBTEXT", "TText 可直接写"),
    "标高": ("TDBSYMBELEVATION", "TMElev 可直接标"),
    "图名": ("TDBDRAWINGNAME", "TDrawingName 可直接标"),
    "图框": ("BLOCKREFERENCE", "TTitleFrame 可直接插"),
}

# 造不出来的：命令只接受鼠标点选，必须人放一次当模板
NEEDS_USER_TEMPLATE = {
    "门窗": ("TDBOPENING", "MC"),
    "门": ("TDBOPENING", "MC"),
    "窗": ("TDBOPENING", "MC"),
    "柱": ("TDbColumn", "BZZ"),
    "楼梯": ("TDbLineStair", "TLStair"),
}

KEYWORDS = {
    "门窗": ("门窗", "开一个", "开门", "开窗", "门洞", "窗洞", "door", "window"),
    "柱": ("柱", "column"),
    "楼梯": ("楼梯", "stair"),
    "墙": ("墙", "wall"),
    "楼板": ("楼板", "slab"),
    "房间": ("房间", "面积", "space"),
    "尺寸": ("标注", "尺寸", "dim"),
    "轴网": ("轴网", "轴线", "grid"),
    "图框": ("图框", "标题栏", "title"),
}

# 哪些词本身就是一个"建筑构件组合"，需要连带门窗（否则画出来的是封闭空壳，
# 用户说的"教室""户型""平面图"显然都包含门）
IMPLIES_OPENINGS = (
    "教室", "户型", "平面图", "平面", "教学楼", "住宅", "宿舍", "办公",
    "卫生间", "厨房", "会议室", "实验室", "建筑",
)


def analyze(instruction: str, existing: dict[str, int] | None = None) -> dict[str, Any]:
    """分析一条自然语言指令需要哪些组件、哪些缺、谁该来做。

    existing: 当前图里已有对象计数（如 {"TDBWALL": 3, "TDBOPENING": 0}）。
    """
    text = (instruction or "").lower()
    existing = existing or {}

    wanted: list[str] = []
    for kind, words in KEYWORDS.items():
        if any(w.lower() in text for w in words):
            wanted.append(kind)

    # A word like 教室/平面图 implies a real room that needs doors and windows,
    # so ask for the opening template up front instead of drawing a sealed box
    # and discovering the gap halfway through.
    if any(w in text for w in IMPLIES_OPENINGS):
        for implied in ("墙", "房间", "门窗"):
            if implied not in wanted:
                wanted.append(implied)

    can_self: list[dict[str, str]] = []
    need_user: list[dict[str, Any]] = []
    seen_tpl: set[str] = set()

    for kind in wanted:
        if kind in NEEDS_USER_TEMPLATE:
            tkey, cmd = NEEDS_USER_TEMPLATE[kind]
            have = int(existing.get(tkey, 0) or 0)
            if have > 0:
                continue                       # already present, nothing to ask
            if tkey in seen_tpl:
                continue
            seen_tpl.add(tkey)
            req = template_request.missing_template_request(tkey, instruction[:40])
            need_user.append(req)
        elif kind in SELF_BUILDABLE:
            key, why = SELF_BUILDABLE[kind]
            can_self.append({"component": kind, "object": key, "how": why})

    return {
        "ok": not need_user,
        "instruction": instruction,
        "detected": wanted,
        "i_can_build": can_self,
        "need_from_user": need_user,
        "need_user_count": len(need_user),
        "message": _render(wanted, can_self, need_user),
        "next": (
            "用户补齐模板后，我扫描句柄并直接克隆，不再返工。"
            if need_user else "所需组件齐备，可以开始。"
        ),
    }


def _render(wanted: list[str], can_self: list[dict[str, str]],
            need_user: list[dict[str, Any]]) -> str:
    if not need_user:
        if not wanted:
            return "没有识别出需要的组件；请补充说明要画什么。"
        parts = ["、".join(c["component"] for c in can_self)]
        return "这条指令需要的组件我都能自己造：%s。" % parts

    lines = ["开工前先确认组件：", ""]
    if can_self:
        lines.append("我自己能造：")
        for c in can_self:
            lines.append("  · %s（%s）" % (c["component"], c["how"]))
        lines.append("")
    lines.append("需要你帮我建（我用不了命令直接造，只能复制）：")
    for req in need_user:
        lines.append("  · %s：命令 %s" % (req.get("template_required"), req.get("command")))
        for step in req.get("steps", []):
            lines.append("      - %s" % step)
        lines.append("    规格：%s" % req.get("spec", ""))
    lines.append("")
    lines.append("建好之后告诉我，我会直接复制使用，不会再来回问。")
    return "\n".join(lines)
