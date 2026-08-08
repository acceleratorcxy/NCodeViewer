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
# 命名参考样例《刀具说明.txt》: 圆角立铣刀(含球头)统称圆鼻立铣刀
TOOL_SPECS = {
    "ball":    ("圆鼻立铣刀", [("d", "直径 D", 10.0), ("r", "圆角半径 R", 3.0), ("l", "刃长 L", 30.0)]),
    "flat":    ("平底立铣刀", [("d", "直径 D", 10.0), ("r", "圆角半径 R", 0.0), ("l", "刃长 L", 30.0)]),
    "invtaper": ("反锥立铣刀", [("d", "刃口直径 D", 12.0), ("r", "圆角半径 R", 3.0), ("taper", "单边锥角 θ°", 2.0), ("l", "刃长 L", 30.0)]),
    "taper":   ("铅笔刀(正锥立铣刀)", [("d", "刃口直径 D", 6.0), ("r", "圆角半径 R", 3.0), ("taper", "单边锥角 θ°", 3.0), ("l", "刃长 L", 30.0)]),
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
TOOLNO_RE = re.compile(r"TOOLNO\s*/(.{0,120})", re.IGNORECASE | re.DOTALL)
NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _fmt(v):
    """最多 3 位小数、去尾零的数值格式化"""
    if isinstance(v, float):
        return format(v, ".3f").rstrip("0").rstrip(".")
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
    h = nums[6] if len(nums) > 6 else 0.0
    # 实测锥角可能出现在 a 位(钻头)或 b 位(反锥), 取非零者
    angle = a if a != 0.0 else b
    if d <= 0:
        return None
    # 刃长优先 TOOLNO 第 5 位; 总长取 CUTTER 第 7 参数
    # (TOOLNO 第 4 位的 120 是刀柄标准长, 非刀具实际总长)
    cut_len, d_top = _toolno_lengths(text[m.start():m.start() + 300])
    cut_len = cut_len or (e if e > 0 else 30.0)
    total_len = h if h > 0 else cut_len
    common = {"d": d, "l": cut_len, "h": total_len}
    if angle < 0:
        # 反锥立铣刀优先 (圆角 r 与锥角并存, 如 D12R3A2)
        taper = abs(angle)
        # TOOLNO 直径即锥顶直径, 由 (D-锥顶D)/2/tanθ 反推锥段长
        l = common["l"]
        if d_top and d_top > 0 and 0 < d_top < d:
            l = (d - d_top) / (2 * math.tan(math.radians(taper)))
        return Tool("invtaper", {**common, "l": l, "r": r, "taper": taper})
    if r > 0:
        # 圆鼻立铣刀: 圆角半径 0<r<=D/2 (R=D/2 即球头, 命名参考刀具说明.txt)
        return Tool("ball", {**common, "r": r})
    if angle > 0:
        if abs(e - d / 2) < 1e-6:
            return Tool("drill", {**common, "point": 2 * angle})
        return Tool("taper", {**common, "taper": angle})
    return Tool("flat", {**common, "r": 0.0})


def _toolno_lengths(seg):
    """TOOLNO/ 语句中提取 (刃长, TOOLNO 直径)。

    实测格式: TOOLNO/no, d, r, [锥角/顶角或空], 刀柄长, 刃长, ...,
    按逗号位置取字段 (反锥/钻头第 3 位可能为角度值, 不能数字塌缩);
    TOOLNO 直径对反锥刀为锥顶直径 (与 CUTTER 刃口直径不同)。
    """
    m = TOOLNO_RE.search(seg)
    if not m:
        return None, None
    fields = [f.strip() for f in m.group(1).replace("$", " ").split(",")]

    def _num(i):
        if len(fields) > i:
            try:
                v = float(fields[i])
                return v if v > 0 else None
            except ValueError:
                pass
        return None

    return _num(5), _num(1)


def _corner_base(r, cr):
    """底部圆角轮廓: 从 (r-cr,0) 经四分之一圆到 (r, cr)"""
    pts = [(0.0, 0.0), (r - cr, 0.0)]
    n = 16
    for i in range(n + 1):
        ang = math.pi / 2 * i / n
        pts.append((r - cr + cr * math.sin(ang), cr - cr * math.cos(ang)))
    return pts


def tool_profile_points(tool: Tool):
    """右半剖面轮廓点 [(半径, 长度)], 自刃尖向上 (切削部分)。

    圆鼻: 柱体 + 圆角(球头即 R=D/2); 平底: 柱体 + 圆角(可 0);
    反锥: 底部圆角 + 收窄锥段; 铅笔刀: 底部圆角 + 扩宽锥段;
    钻头/中心钻: 柱体 + 锥尖。
    """
    kind = tool.kind
    d = float(tool.p("d", 10.0))
    l = float(tool.p("l", 30.0))
    r = d / 2
    if kind == "ball":
        br = min(float(tool.p("r", r)), r)
        if br >= r - 1e-9:
            # 球头 (R=D/2): 半球 + 柱体
            pts = []
            n = 24
            for i in range(n + 1):
                ang = math.pi / 2 * i / n
                pts.append((br * math.sin(ang), br - br * math.cos(ang)))
            pts.append((br, l))
            return pts
        # 圆角 (R<D/2): 底部圆角 + 柱体
        pts = _corner_base(r, br)
        pts.append((r, l))
        return pts
    if kind in ("invtaper", "taper"):
        taper = float(tool.p("taper", 2.0))
        cr = min(float(tool.p("r", 0.0)), r)
        pts = _corner_base(r, cr) if cr > 1e-9 else [(0.0, 0.0), (r, 0.0)]
        y0 = cr
        if kind == "invtaper":      # 越到根部越大, 向上收窄
            r_top = max(0.0, r - (l - y0) * math.tan(math.radians(taper)))
        else:                       # 铅笔刀: 越到前端越小, 向上扩宽
            r_top = r + (l - y0) * math.tan(math.radians(taper))
        pts.append((r_top, l))
        return pts
    if kind in ("drill", "center"):
        point = float(tool.p("point", 118.0 if kind == "drill" else 60.0))
        tip_len = r / math.tan(math.radians(point / 2))
        return [(0.0, 0.0), (r, tip_len), (r, l)]
    # flat: 平底 (带圆角)
    cr = min(float(tool.p("r", 0.0)), r)
    if cr <= 1e-9:
        return [(0.0, 0.0), (r, 0.0), (r, l)]
    pts = _corner_base(r, cr)
    pts.append((r, l))
    return pts


def tool_full_profile(tool: Tool):
    """完整右半剖面 (切削部分 + 刀柄, 自刃尖向上)。

    反锥刀锥段结束处**缩颈** (刀杆明显细于切削刃);
    其余类型刀杆沿刃长顶径同径延伸至总长。
    """
    profile = tool_profile_points(tool)
    l = float(tool.p("l", 30.0))
    h = tool_overall_height(tool)
    if h <= l + 1e-9:
        return profile
    r_top = profile[-1][0]
    if tool.kind == "invtaper":
        r_neck = max(r_top * 0.6, 0.5)      # 缩颈: 刀杆对比切削刃明显变细
        return list(profile) + [(r_neck, l), (r_neck, h)]
    return list(profile) + [(r_top, l), (r_top, h)]


def tool_summary(tool: Tool) -> str:
    """一行摘要, 如 '平底立铣刀 D20 R3 L70' / '普通钻头 D2.5 顶角62° L50'"""
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
    parts.append(f"L{_fmt(tool.p('l', 0.0))}")
    return " ".join(parts)


def tool_overall_height(tool: Tool) -> float:
    """刀具总长 (含刀柄); 无总长数据时回退刃长"""
    l = float(tool.p("l", 30.0))
    h = float(tool.p("h", 0.0) or 0.0)
    return h if h > l else l
