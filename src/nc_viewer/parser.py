# -*- coding: utf-8 -*-
"""NC(G 代码)刀路解析器

将 NC 文本解析为带模态信息(M/F/平面/绝对增量)的移动序列,
并为每一行记录执行后的刀具位置, 供可视化与"按行定位"使用。

支持:
  - G0/G1/G2/G3 模态运动
  - F 模态进给
  - G17/G18/G19 平面选择
  - G90/G91 绝对/增量
  - I/J/K 增量圆心(绝对圆心 = start + (I,J,K))
  - N 行号、; 与 () 注释
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# 单个 G 代码字: 字母 + 可选符号数值
WORD_RE = re.compile(r"([A-Za-z])(-?\d+(?:\.\d+)?)")
# 括号注释
PAREN_RE = re.compile(r"\([^)]*\)")


def _strip_comment(line: str) -> str:
    """去掉 (...) 注释与 ; 行内注释"""
    line = PAREN_RE.sub("", line)
    idx = line.find(";")
    if idx >= 0:
        line = line[:idx]
    return line


@dataclass
class Move:
    line_number: int                # 文件行号(1 基)
    n_number: Optional[float]       # N 字值, 无则 None
    motion: str                     # 'G0'/'G1'/'G2'/'G3'
    start: tuple                    # (x, y, z)
    end: tuple                      # (x, y, z)
    center: Optional[tuple]         # 圆弧绝对圆心, 直线为 None
    cw: Optional[bool]              # 圆弧顺逆, 直线为 None
    plane: str                      # 'XY'/'XZ'/'YZ'
    feed: Optional[float]           # 进给, G0 为 None
    s: Optional[float] = None       # 主轴转速, G0 为 None (与 feed 同规则)


@dataclass
class ParseResult:
    moves: list
    line_positions: dict            # 行号(1 基) -> (x,y,z) 执行后位置
    feeds: list                     # 切削移动出现过的 F 值(首次出现序, 去重)
    bbox: tuple                     # (minx,miny,minz,maxx,maxy,maxz)
    n_to_line: dict = field(default_factory=dict)   # N 值 -> 文件行号

    def position_at_line(self, line: int) -> tuple:
        """返回指定行号执行后的刀具位置; 超界则取首/末位置"""
        if line < 1 or not self.line_positions:
            return (0.0, 0.0, 0.0)
        max_line = max(self.line_positions)
        if line > max_line:
            return self.line_positions[max_line]
        return self.line_positions[line]

    def line_for_n(self, n) -> Optional[int]:
        """按 N 字值查文件行号, 无则 None"""
        return self.n_to_line.get(int(n))


@dataclass
class ProgramStats:
    """程序统计 (供侧栏面板与详情页展示)"""
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float
    s_min: Optional[float] = None       # 无 S 则为 None
    s_max: Optional[float] = None
    f_min: Optional[float] = None       # 无切削移动则为 None
    f_max: Optional[float] = None
    f_count: int = 0                    # F 档位数
    g_counts: dict = field(default_factory=dict)   # {'G0': n, 'G1': n, ...}
    moves_total: int = 0
    cut_total: int = 0                  # 切削段数 (G1/G2/G3)
    f_seg_counts: dict = field(default_factory=dict)  # {feed: 段数}


def compute_stats(result: ParseResult) -> ProgramStats:
    """从 ParseResult 计算程序统计。

    行程按刀路端点(不包含原点)计算, 避免 bbox 含原点导致行程虚高。
    """
    if not result.moves:
        return ProgramStats(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    x_min = y_min = z_min = float("inf")
    x_max = y_max = z_max = float("-inf")
    s_values: list = []
    f_values: list = []
    f_seg_counts: dict = {}
    g_counts: dict = {}
    cut_total = 0

    for m in result.moves:
        for p in (m.start, m.end):
            x, y, z = p
            if x < x_min: x_min = x
            if x > x_max: x_max = x
            if y < y_min: y_min = y
            if y > y_max: y_max = y
            if z < z_min: z_min = z
            if z > z_max: z_max = z
        if m.s is not None:
            s_values.append(m.s)
        if m.motion != "G0":
            cut_total += 1
            if m.feed is not None:
                f_values.append(m.feed)
                f_seg_counts[m.feed] = f_seg_counts.get(m.feed, 0) + 1
        g_counts[m.motion] = g_counts.get(m.motion, 0) + 1

    f_min = min(f_values) if f_values else None
    f_max = max(f_values) if f_values else None
    s_min = min(s_values) if s_values else None
    s_max = max(s_values) if s_values else None

    return ProgramStats(
        x_min=x_min, x_max=x_max,
        y_min=y_min, y_max=y_max,
        z_min=z_min, z_max=z_max,
        s_min=s_min, s_max=s_max,
        f_min=f_min, f_max=f_max,
        f_count=len(result.feeds),
        g_counts=g_counts,
        moves_total=len(result.moves),
        cut_total=cut_total,
        f_seg_counts=f_seg_counts,
    )


def parse_nc(text: str) -> ParseResult:
    moves: list = []
    line_positions: dict = {}
    feeds: list = []
    feed_seen: set = set()
    n_to_line: dict = {}

    # 模态状态
    motion: Optional[str] = None
    feed: Optional[float] = None
    spindle: Optional[float] = None
    plane = "XY"
    absolute = True
    x = y = z = 0.0

    # 包围盒, 起点纳入原点
    minx = miny = minz = 0.0
    maxx = maxy = maxz = 0.0

    for i, raw in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw)
        words = WORD_RE.findall(line)

        g_codes: list = []
        word_dict: dict = {}
        for letter, val in words:
            L = letter.upper()
            if L == "G":
                g_codes.append(float(val))
            else:
                word_dict[L] = float(val)

        # 按出现顺序处理 G 码对模态的影响
        for g in g_codes:
            gi = int(round(g))
            if gi in (0, 1, 2, 3):
                motion = f"G{gi}"
            elif gi == 17:
                plane = "XY"
            elif gi == 18:
                plane = "XZ"
            elif gi == 19:
                plane = "YZ"
            elif gi == 90:
                absolute = True
            elif gi == 91:
                absolute = False
            # 其余 G 码(G54/G43 等)忽略

        n_num = word_dict.get("N")
        if n_num is not None:
            # 长程序中 N 编号会重启, 同一 N 值取首次出现
            n_to_line.setdefault(int(round(n_num)), i)

        if "F" in word_dict:
            feed = word_dict["F"]

        if "S" in word_dict:
            spindle = word_dict["S"]

        # 计算新坐标
        has_coord = any(k in word_dict for k in ("X", "Y", "Z"))
        new_x, new_y, new_z = x, y, z
        if absolute:
            if "X" in word_dict:
                new_x = word_dict["X"]
            if "Y" in word_dict:
                new_y = word_dict["Y"]
            if "Z" in word_dict:
                new_z = word_dict["Z"]
        else:
            if "X" in word_dict:
                new_x = x + word_dict["X"]
            if "Y" in word_dict:
                new_y = y + word_dict["Y"]
            if "Z" in word_dict:
                new_z = z + word_dict["Z"]

        # 有运动指令且有坐标变化才生成移动
        if motion is not None and has_coord:
            start = (x, y, z)
            end = (new_x, new_y, new_z)
            center = None
            cw = None
            if motion in ("G2", "G3"):
                ci = word_dict.get("I", 0.0)
                cj = word_dict.get("J", 0.0)
                ck = word_dict.get("K", 0.0)
                center = (x + ci, y + cj, z + ck)
                cw = (motion == "G2")
            moves.append(Move(
                line_number=i,
                n_number=n_num,
                motion=motion,
                start=start,
                end=end,
                center=center,
                cw=cw,
                plane=plane,
                feed=(feed if motion != "G0" else None),
                s=(spindle if motion != "G0" else None),
            ))
            # 仅切削移动收集 F (用于配色)
            if motion != "G0" and feed is not None and feed not in feed_seen:
                feed_seen.add(feed)
                feeds.append(feed)

        x, y, z = new_x, new_y, new_z
        line_positions[i] = (x, y, z)

        if x < minx: minx = x
        if y < miny: miny = y
        if z < minz: minz = z
        if x > maxx: maxx = x
        if y > maxy: maxy = y
        if z > maxz: maxz = z

    bbox = (minx, miny, minz, maxx, maxy, maxz)
    return ParseResult(moves=moves, line_positions=line_positions, feeds=feeds,
                       bbox=bbox, n_to_line=n_to_line)