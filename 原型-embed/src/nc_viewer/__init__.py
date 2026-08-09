# -*- coding: utf-8 -*-
"""nc_viewer - NC 代码刀路可视化查看器

包结构:
  nc_viewer.parser     NC/G 代码解析 (Move, ParseResult, parse_nc)
  nc_viewer.geometry   视图几何: 颜色映射 / 圆弧离散 / 四元数轨道旋转 / 投影
  nc_viewer.viewer     Tkinter 主窗口 (NCViewer)
"""
from __future__ import annotations

from .parser import Move, ParseResult, parse_nc

__all__ = ["Move", "ParseResult", "parse_nc", "NCViewer"]


def __getattr__(name):
    # 延迟导入, 避免 import 包时就拉起 Tkinter
    if name == "NCViewer":
        from .viewer import NCViewer
        return NCViewer
    if name == "main":
        from .viewer import main
        return main
    raise AttributeError(f"module 'nc_viewer' has no attribute {name!r}")