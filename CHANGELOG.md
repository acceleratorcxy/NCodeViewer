# 更改记录 (CHANGELOG)

本文件记录 NC 刀路查看器的所有更改（功能、修复、重构、打包等）。
> **维护约定**：每次代码/文档/打包有改动，都应在「未发布」或当次版本条目下追加记录。
> - 记录格式：`- <类型> <一句话说明>`，类型取 `新增 / 修改 / 修复 / 移除 / 文档 / 打包 / 测试`。
> - 改动较大或对外发布时，新建版本号条目 `[x.y.z] - YYYY-MM-DD`。
> - 遵循 TDD：先写/跑测试（green），再打包。
---

## [1.1.0] - 2026-08-08

本次会话为项目增加 Windows 7 支持：改用 conda 的 Python 3.8 构建 EXE（3.9+ 官方不支持 Win7），并内置 UCRT 运行时使裸装 Win7 可运行。
### 新增
- 支持 Windows 7（64 位）目标：`build_exe.bat` 自动探测 conda `python38` 环境（Python 3.8.19，自带 Tk 8.6.9）作为构建解释器，Win10/11 构建不受影响（回退 `py -3.11` / `py -3.14`）。
- conda 构建时自动把 UCRT（`ucrtbase.dll` + `api-ms-win-crt-*.dll` + `vcruntime140*.dll`）打包进 EXE，裸装 Win7 无需 KB2999226 补丁。
- 打包脚本支持 `NCVIEWER_PY` 环境变量显式指定构建解释器（python.exe 完整路径）。
### 修改
- `pyproject.toml`：版本提升至 1.1.0；`requires-python` 放宽为 `>=3.8`；dev 依赖限定 `pytest<9`（pytest 9 不支持 3.8）。
- 维护 `build_exe.bat`：重构为四步流程（解释器探测 → PyInstaller 检查 → 清理 → 打包），conda python38 构建时自动固定 PyInstaller 5.13.2（6.10+ 的 bootloader 不支持 Win7）。
- 修复 `build_exe.bat` 两个 cmd 陷阱：以引号开头的命令行若以重定向结尾会被剥首尾引号（一律改重定向前置、输出走临时文件）；块内 echo 文本不得含半角括号（会被当作分组操作符）。
### 移除
- 删除 `requirements.txt`（与 `pyproject.toml` dev 依赖重复）。
- 删除 `NCViewer.spec`（构建以 `build_exe.bat` 为准，PyInstaller 每次运行自动重新生成，无用途），`.gitignore` 新增 `*.spec`。
- 清理全部缓存与构建残留：各目录 `__pycache__`/pyc（含旧扁平模块 `nc_parser`/`nc_viewer`/`test_nc_*` 及 `f_detect` 实验残留）、`.pytest_cache`、`build/`、`src/nc_viewer.egg-info`。
### 文档
- 全面校对并重写 `README.md`、`docs/技术手册.md`、`docs/开发流程.md`：修复历史乱码字符、损坏的目录树与编号错乱。
- 修正技术手册事实性错误：「旋转中心 2px 内吸附」改为「12px」（与 `PICK_MAX_PX=12` 一致）。
- 更新文档目录结构（移除 `requirements.txt` / `NCViewer.spec`，补充 `样例文件`、`__init__.py` 职责说明）。
### 新增
- 建立 git 仓库；`.gitignore` 排除 `样例文件/`（仅本地测试用）与 `docs/本地版/`（含本机路径等隐私信息，不入库）。
- 文档拆分为发布版与本地版：`docs/`（发布版，本机路径用 `<conda38-python>` 占位符替代，随仓库分发）与 `docs/本地版/`（恢复本机路径，仅供本机使用，被 git 忽略）。
### 修改
- 样例文件不再打包进 EXE：`build_exe.bat` 移除 `--add-data "样例文件;样例文件"`；`viewer._sample_dir` 打包环境回退到用户主目录作为文件对话框初始目录（样例仅本地开发/测试用）。
- `build_exe.bat` 的 conda python38 探测改为 `where conda` 动态定位 conda 根目录（移除本机硬编码路径，不泄漏本机路径信息）。
- 重建 `dist\NCViewer.exe`：**10.5 MB**（原 30.3 MB，样例文件约占 20 MB），归档验证无样例条目、UCRT/tcl/tk 完整。
### 测试
- 新增 `_sample_dir` 目录定位与回退测试；测试总数 **39**（解析 28 + 几何 7 + 界面结构 4），py38 / py311 双环境全绿。
- 更新 `README.md`、`docs/技术手册.md`、`docs/开发流程.md`：Win7 构建方式、环境要求、打包注意与 FAQ。
### 测试
- conda python38（Python 3.8.19 + Tk 8.6.9）下全量 38 例通过（green）；GUI 冒烟通过（真实样例文件 25321 刀路段正常加载渲染）。
### 打包
- 用 conda python38 + PyInstaller 5.13.2 重新打包 `dist\NCViewer.exe`（内置 UCRT 与样例文件）。
- Win11 冒烟测试通过；**Win7 实测待用户在真实机器上验证**。

---

## [1.0.0] - 2026-08-07

本次会话对项目进行标准化重构、补充工程配置、打包为 EXE，并整理界面。
### 新增
- 新增开发流程规范 `docs/开发流程.md`（TDD、测试、打包、发布检查清单；流程变更亦须维护至此）。
- 建立标准包结构 `src/nc_viewer`，支持 `python -m nc_viewer` 运行。
- 新增工程配置：`pyproject.toml`（构建 + pytest 配置）、`requirements.txt`、`README.md`、`.gitignore`。
- 新增技术手册 `docs/技术手册.md`（概述、安装、使用、架构、算法、测试、FAQ、扩展）。
- 新增 PyInstaller 打包入口 `launcher.py` 与打包脚本 `build_exe.bat`，产物为单文件 `dist/NCViewer.exe`（含样例文件）。
- 新增界面结构测试 `tests/test_viewer_ui.py`（1 例，验证按钮去重）。
### 修改
- 维护 `README.md`：新增「文档」索引（技术手册/开发流程/更改记录）、「打包 EXE」章节，更新测试数量（58 例）与项目结构（补全 `launcher.py`/`build_exe.bat`/`CHANGELOG`/`docs`/新测试）。
- 将 `docs/技术手册.md` 增强为总结性文档：补全目录结构（含 `launcher.py`/`build_exe.bat`/`CHANGELOG`/新测试）、测试覆盖（38 例）、打包说明，并新增「注意事项」章节（开发/运行/使用/打包/文档维护），开头加入相关文档索引。
- 将原扁平文件 `nc_parser.py` / `nc_viewer.py` 重构为包模块）   - `src/nc_viewer/parser.py`：NC 解析（原 `nc_parser.py`）。   - `src/nc_viewer/geometry.py`：配色 / 圆弧离散 / 四元数轨道旋转 / 投影（纯计算，不依赖 Tkinter）。   - `src/nc_viewer/viewer.py`：Tkinter 主窗口（原 `nc_viewer.py`）。   - `src/nc_viewer/__main__.py`：`python -m nc_viewer` 入口。
- 整理界面：移除左侧文件栏中重复的「打开文件…」按钮，文件栏改为纯列表，统一由顶部工具条作为文件入口。
- 样例文件目录定位改为基于包路径向上推断项目根。
### 移除
- 删除旧扁平文件：`nc_parser.py`、`nc_viewer.py`、`test_nc_parser.py`、`test_nc_viewer_math.py`（内容已迁移到包中 `tests/`）。
### 测试
- 新增 `tests/test_viewer_ui.py` 3 例。
- 测试总数：**38**（解析 28 + 几何 7 + 界面结构 3），`py -3.11 -m pytest -q` 全部通过（green）。
### 打包
- 使用 `build_exe.bat` 生成 `dist/NCViewer.exe`（单文件、无控制台、含样例文件，约 29MB）。
- 冒烟测试通过：EXE 启动后进程存活、无崩溃。
---

<!-- 后续更改在此处上方（最近在上）追加记录 -->
