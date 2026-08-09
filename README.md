# NC 刀路查看器 (NCodeViewer)

可视化查看 NC/G 代码刀路的桌面工具。不同 F 进给以不同颜色绘制，支持按行号（或 N 号）定位刀具实际位置、多文件加载切换、代码搜索跳转，以及 CAD 风格的轨道球旋转视图。

## 特性

- 解析 NC/G 代码（`.MPF/.NC/.CNC/.TXT`），按模态还原刀路
- 不同 F 值用不同颜色绘制，G0 快移灰色，颜色图例同步显示
- 圆弧（G2/G3）按角度与弧长双因素离散，小圆弧也能显示曲率
- 按行号 / N 号定位刀具实际位置（十字 + 当前段高亮 + 加工方向箭头）
- 画布内左键单击刀路即跳转到对应 NC 行（按折线段最近距离拾取）
- 左侧文件栏一次加载多个文件，随时切换；顶部栏内嵌加载进度（读取/解析/离散/载入分阶段，超时显示防闪烁）
- 代码搜索定位与跳转
- 轨道球（四元数 ArcBall）旋转：中键旋转（空白处为平面滚转）/ 左键平移 / 滚轮缩放，旋转中心不漂移
- 深色现代化界面（ttk clam 定制主题，纯标准库，兼容 Win7）
- 程序统计：X/Y/Z 行程、S/F 范围、G 次数等关键指标 + 详情二级窗口
- F 进给趋势曲线：按 F 档位着色的折线图二级窗口
- 程序逐行运行：连续播放（合并跳行 1-100）/单步/直达/演示到行（画布逐行画出刀路）
- 刀具：aptsource 自动解析、直径剖面图、六种类型自定义、画布 3D 模型（刀尖对刀）
- 按段浏览：抬刀平面可编辑、段导航、仅显示当前段（段内播放）
- 文件列表双区：MPF 与 APT 按文件名/程序名关联互高亮

## 文档

- **[技术手册](docs/技术手册.md)**：总结性文档，含概述、架构、算法、操作、测试、FAQ 及**一切注意事项**。
- **[开发流程](docs/开发流程.md)**：开发 / 测试 / 打包流程规范，供严格遵循。
- **[更改记录](CHANGELOG.md)**：历次改动留痕。

## 环境要求

- Python ≥ 3.8，且必须自带 Tkinter（验证 3.8 / 3.11 / 3.14）
- **Windows 7 目标必须用 conda 的 Python 3.8 构建**（3.9+ 官方不再支持 Win7；详见下文打包章节）
- 运行时仅依赖标准库；测试需要 `pytest`；打包需要 `PyInstaller`（脚本会自动安装）

## 安装

```bash
# 推荐：可编辑安装，使 `nc-viewer` 命令与 `import nc_viewer` 可用
python -m pip install -e .

# 若仅开发测试，安装 pytest 即可
python -m pip install pytest
```

## 运行

```bash
# 方式一：模块入口
python -m nc_viewer

# 方式二：带文件启动
python -m nc_viewer "样例文件\数控程序\HASS\D0354F31311-201_AG6D311A0101.MPF"

# 方式三：安装后使用命令行脚本
nc-viewer
```

> 命令行仅支持加载**单个**文件；多文件请用界面「打开文件…」多选。

## 运行测试

```bash
python -m pytest
```

共 89 例：刀具（15）+ 解析（33）+ 几何（7）+ 界面结构（34）。遵循 TDD，改动后需全绿（green）再打包。

## 打包 EXE

```bash
build_exe.bat
```

产物为单文件 `dist\NCViewer.exe`（不含样例文件，样例仅本地测试用）。打包前须保证测试全绿，打包后做冒烟测试。

构建解释器自动探测顺序：`NCVIEWER_PY` 环境变量（python.exe 完整路径）→ conda `python38` 环境（**Win7 目标构建优先**）→ `py -3.11` / `py -3.14`（Win10/11 构建）。
用 conda python38 构建时自动固定 PyInstaller 5.13.2（6.10+ 的 bootloader 不支持 Win7），并把 UCRT 运行时打包进 EXE，裸装 Win7 无需补丁即可运行。

## 项目结构

```
NCodeViewer/
├── src/nc_viewer/        # 源码包
│   ├── parser.py         # NC/G 代码解析（纯计算，不依赖 Tkinter）
│   ├── geometry.py       # 配色 / 圆弧离散 / 四元数轨道旋转 / 投影（纯计算）
│   ├── theme.py          # 深色现代化主题（配色/字体常量 + apply_theme）
│   ├── viewer.py         # Tkinter 主窗口
│   └── __main__.py       # python -m nc_viewer 入口
├── tests/                # pytest 单元测试（89 例）
│   ├── test_parser.py
│   ├── test_geometry.py
│   └── test_viewer_ui.py
├── docs/                 # 发布版文档（不含本机隐私信息）；本地版见 docs/本地版/（不入库）
├── CHANGELOG.md          # 更改记录
├── launcher.py           # PyInstaller 打包入口
├── build_exe.bat         # 打包脚本
├── 样例文件/              # 样例 NC 程序（仅本地测试用，不打包、不入库）
└── pyproject.toml        # 构建与测试配置
```
