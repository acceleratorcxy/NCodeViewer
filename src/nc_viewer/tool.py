# -*- coding: utf-8 -*-
"""刀具几何模型 (纯计算, 不依赖 Tkinter)

支持六种刀具类型: 圆鼻立铣刀 / 平底立铣刀 / 反锥立铣刀 /
铅笔刀(正锥立铣刀) / 普通钻头 / 中心钻。

提供:
  - TOOL_SPECS: 各类型字段定义 (供自定义面板生成输入框)
  - parse_aptsource_tool: 解析 CATIA APT 的 CUTTER/ 头部语句
  - tool_profile_points: 右半剖面轮廓 (半径, 长度)
  - tool_summary: 一行摘要文本
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

# 类型 key -> (显示名, [(字段key, 标签, 默认值)])
TOOL_SPECS = {
    "ball":    ("圆鼻立铣刀", [("d", "直径 D", 10.0), ("r", "球头半径 R", 5.0), ("l", "刃长 L", 30.0)]),
    "flat":    ("平底立铣刀", [("d", "直径 D", 10.0), ("r", "圆角半径 R", 0.0), ("l", "刃长 L", 30.0)]),
    "invtaper": ("反锥立铣刀", [("d", "刃口直径 D", 12.0), ("taper", "锥角 θ°", 2.0), ("l", "刃长 L", 30.0)]),
    "taper":   ("铅笔刀(正锥立铣刀)", [("d", "刃口直径 D", 6.0), ("taper", "锥角 θ°", 3.0), ("l", "刃长 L", 30.0)]),
    "drill":   ("普通钻头", [("d", "直径 D", 10.0), ("point", "顶角 θ°", 118.0), ("l", "刃长 L", 50.0)]),
    "center":  ("中心钻", [("d", "直径 D", 3.0), ("point", "顶角 θ°", 60.0), ("l", "刃长 L", 15.0)]),
}


@dataclass
class Tool:
    """刀具: kind 为 TOOL_SPECS 的 key, params 存规格字段"""
    kind: str
    params: dict = field(default_factory=dict)

    def p(self, key, default=None):
        return self.params.get(key, default)


CUTTER_RE = re.compile(r"CUTTER\s*/(.*)", re.IGNORECASE)
NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _fmt(v):
    """去尾零的数值格式化"""
    if isinstance(v, float):
        return format(v, ".6f").rstrip("0").rstrip(".")
    return str(v)


def parse_aptsource_tool(text: str) -> Optional[Tool]:
    """从 aptsource 头部解析首个 CUTTER/ 语句为 Tool; 失败返回 None。

    APT CUTTER 七参数: d=直径, r=圆角半径, e=刃长相关, f=刃口段,
    a=锥角(负=反锥, 正=钻头顶角半角), b, h=总长。
    类型推断为启发式 (样例实测), 自定义面板可完全覆盖:
      r == d/2        -> 圆鼻立铣刀
      a < 0           -> 反锥立铣刀 (锥角 |a|)
      r == 0 且 a > 0 -> e == d/2 为普通钻头(顶角 2a), 否则铅笔刀(正锥)
      其余            -> 平底立铣刀 (带圆角半径 r, 可 0)
    """
    m = CUTTER_RE.search(text)
    if not m:
        return None
    seg = text[m.start():m.start() + 300]      # 覆盖 $ 续行
    nums = [float(t) for t in NUM_RE.findall(seg.replace("$", " "))]
    if len(nums) < 2:
        return None
    d, r = nums[0], nums[1]
    e = nums[2] if len(nums) > 2 else 0.0
    a = nums[4] if len(nums) > 4 else 0.0
    b = nums[5] if len(nums) > 5 else 0.0
    # 实测锥角可能出现在 a 位(钻头)或 b 位(反锥), 取非零者
    angle = a if a != 0.0 else b
    if d <= 0:
        return None
    if r > 0 and abs(r - d / 2) < 1e-6:
        return Tool("ball", {"d": d, "r": r, "l": 30.0})
    if angle < 0:
        return Tool("invtaper", {"d": d, "taper": abs(angle), "l": 30.0})
    if r == 0 and angle > 0:
        if abs(e - d / 2) < 1e-6:
            return Tool("drill", {"d": d, "point": 2 * angle, "l": 30.0})
        return Tool("taper", {"d": d, "taper": angle, "l": 30.0})
    return Tool("flat", {"d": d, "r": r, "l": 30.0})


def tool_profile_points(tool: Tool):
    """右半剖面轮廓点 [(半径, 长度)], 自刃尖向上。

    圆鼻: 柱体 + 半球; 平底: 柱体 + 圆角(可 0);
    反锥/铅笔刀: 梯形; 钻头/中心钻: 柱体 + 锥尖。
    """
    kind = tool.kind
    d = float(tool.p("d", 10.0))
    l = float(tool.p("l", 30.0))
    r = d / 2
    if kind == "ball":
        br = float(tool.p("r", r))
        pts = []
        n = 24
        for i in range(n + 1):
            ang = math.pi / 2 * i / n
            pts.append((br * math.sin(ang), br - br * math.cos(ang)))
        pts.append((br, l))
        return pts
    if kind == "invtaper":
        taper = float(tool.p("taper", 2.0))
        top_r = max(0.0, r - l * math.tan(math.radians(taper)))
        return [(0.0, 0.0), (r, 0.0), (top_r, l)]
    if kind == "taper":
        taper = float(tool.p("taper", 3.0))
        top_r = r + l * math.tan(math.radians(taper))
        return [(0.0, 0.0), (r, 0.0), (top_r, l)]
    if kind in ("drill", "center"):
        point = float(tool.p("point", 118.0 if kind == "drill" else 60.0))
        tip_len = r / math.tan(math.radians(point / 2))
        return [(0.0, 0.0), (r, tip_len), (r, l)]
    # flat: 平底 (带圆角)
    cr = min(float(tool.p("r", 0.0)), r)
    if cr <= 1e-9:
        return [(0.0, 0.0), (r, 0.0), (r, l)]
    pts = [(0.0, 0.0), (r - cr, 0.0)]
    n = 16
    for i in range(n + 1):
        ang = math.pi / 2 * i / n
        pts.append((r - cr + cr * math.sin(ang), cr - cr * math.cos(ang)))
    pts.append((r, l))
    return pts


def tool_summary(tool: Tool) -> str:
    """一行摘要, 如 '平底立铣刀 D20 R3' / '普通钻头 D2.5 顶角62°'"""
    name = TOOL_SPECS[tool.kind][0]
    d = tool.p("d")
    parts = [name, f"D{_fmt(d)}"]
    if tool.kind == "ball":
        parts.append(f"R{_fmt(tool.p('r'))}")
    elif tool.kind == "flat" and tool.p("r", 0):
        parts.append(f"R{_fmt(tool.p('r'))}")
    elif tool.kind in ("invtaper", "taper"):
        parts.append(f"θ{_fmt(tool.p('taper'))}°")
    elif tool.kind in ("drill", "center"):
        parts.append(f"顶角{_fmt(tool.p('point'))}°")
    return " ".join(parts)
