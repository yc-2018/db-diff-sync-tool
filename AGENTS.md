# AGENTS.md

本文件适用于仓库根目录及全部子目录，供在本项目中工作的自动化编码代理使用。

## 项目概览

- 本项目是仅面向 Windows 的数据库同步比对桌面工具，使用 Python + pywebview 承载原生窗口，`web/` 提供内嵌页面。
- 应用支持 Oracle、MySQL 和 SQLite；两侧数据库必须同类型。
- 核心安全边界：应用只生成结构或数据修复 SQL，绝不自动执行生成的 SQL。
- `app.py` 是应用入口和 JavaScript API 桥，负责连接配置、会话状态以及比对流程调度。
- `dbcore.py` 负责数据库方言、元数据读取、结构/数据差异计算和 SQL 生成。
- `web/index.html`、`web/style.css`、`web/app.js` 构成前端界面。
- `tests/selftest.py` 是 SQLite 端到端自测及 Oracle/MySQL SQL 文本校验；`tests/test_where.py` 校验筛选条件处理。

## 版本管理

- 当前应用版本从 `2.0.1` 开始，版本号定义在 `app.py` 的 `APP_VERSION`。
- 每次修改代码后递增补丁版本号，例如 `2.0.1` 改为 `2.0.2`；窗口标题和 `web/index.html` 中的前端资源版本参数必须同步更新。

## 开发环境

- 使用 Windows 10/11 和 Python 3.10-3.12，优先 Python 3.12。不要升级到 Python 3.13+，除非已确认 pywebview、pythonnet 和 cffi 均有兼容 wheel。
- 初始化环境：双击 `初始化环境.bat`，或在命令行运行 `初始化环境.bat`。
- 启动应用：双击 `启动.bat`。
- 主要运行依赖：`pywebview`、`oracledb`、`pymysql`；打包依赖：`pyinstaller`。
- 本地连接信息保存在 `%USERPROFILE%\.dbsync_tool\`，不得提交连接配置、密码、数据库文件或其他真实业务数据。

## 编码与命令

- 新增或修改 Python、JavaScript、HTML、CSS、YAML、JSON、Markdown、SQL 等文本文件时统一使用 UTF-8。
- 在 PowerShell 中读取或输出项目文件前显式设置 UTF-8：

  ```powershell
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  $OutputEncoding = [System.Text.Encoding]::UTF8
  Get-Content -Path README.md -Encoding UTF8
  ```

- 现有 `.bat` 文件是特殊例外：为兼容中文 Windows 的 `cmd.exe`，必须保持纯 ASCII，脚本提示也必须使用英文。
- 仓库存在 `.codegraph/`。理解或定位代码时先运行 `codegraph explore "问题或符号名"`，CodeGraph 无结果时再使用 `rg` 或直接读取文件。
- 不提交 `.venv/`、`dist/`、`build/`、`*.spec`、缓存、日志或 `.oracle_client/` 下的本地二进制依赖。

## 修改约束

- 保持 `app.py`、`dbcore.py` 和前端 API 字段兼容；修改桥接方法时同步检查 `web/app.js` 的调用方。
- 数据库方言行为应封装在 `dbcore.py`，不要把 Oracle/MySQL/SQLite 的 SQL 分支散落到 UI 层。
- 所有表名和筛选条件继续经过现有校验；不得放宽标识符、分号或 SQL 注释的拦截规则。
- 不得加入自动执行修复 SQL 的功能。涉及 `DROP`、重建表等破坏性 SQL 时，继续保留清晰警告。
- 保持单表 20 万行和单方向 5000 条 SQL 的保护上限，除非需求明确要求修改且同步补充测试和说明。
- Oracle Instant Client 是可选依赖。缺失时必须保持 thin mode 可用；不要将 Oracle 客户端二进制直接提交到仓库。
- 修改用户可见行为、环境要求、启动或打包方式时同步更新 `README.md`。

## 验证与打包

- 修改后至少运行：

  ```powershell
  .\.venv\Scripts\python.exe tests\selftest.py
  .\.venv\Scripts\python.exe tests\test_where.py
  ```

- 涉及前端时还应启动应用，检查连接弹窗、左右侧切换、结构比对、数据比对、SQL 复制和错误提示。
- 涉及打包或资源路径时运行 `打包.bat`，确认 `dist\数据库同步比对工具\` 包含可执行文件和 `web` 资源并能启动。
- `tests/debug_comment.py` 是需要外部 Oracle 环境的诊断脚本，不属于默认自动化测试。
- GitHub Actions 在每次推送时执行上述自动化测试、生成 Windows x64 压缩包，并创建以提交 SHA 标识的预发布版本。
