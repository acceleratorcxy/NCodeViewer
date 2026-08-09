# -*- coding: utf-8 -*-
"""PyInstaller 打包入口：启动 NC 刀路查看器。

用法: py -3.11 -m PyInstaller --onefile --windowed --name NCViewer --paths src launcher.py
"""
from __future__ import annotations

from nc_viewer.viewer import main

if __name__ == "__main__":
    main()