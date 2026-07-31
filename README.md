# 数据库同步比对工具

一个 Windows 桌面应用（原生窗口 + 网页 UI），用于比对**两个同类型数据库**之间的表结构与表数据差异，并分别生成「让某一侧变成对方」的修复 SQL。**应用只生成 SQL 供复制，绝不替你执行。**

## 支持的数据库

| 类型 | 说明 |
|---|---|
| **Oracle（优先适配）** | 使用 python-oracledb 纯 Python 模式，**无需安装 Oracle 客户端**；支持服务名 / SID 两种方式 |
| MySQL | 通过 PyMySQL |
| SQLite | 本机 .db 文件，方便无数据库环境时直接试用 |

> 两侧必须是**相同类型**的数据库（如：Oracle ↔ Oracle）。

## 启动方式

```bat
启动.bat
```

首次使用若提示缺少虚拟环境，先双击 `初始化环境.bat`。

开发目录中已建好 `.venv`，可直接双击 `启动.bat`。

### 环境要求

- **Windows 10/11**（64 位）
- **Python 3.12**（推荐）或 3.11 / 3.10
  - ⚠️ **不要用 Python 3.13+**：`cffi` / `pythonnet` 的预编译 wheel 在 3.13/3.14 上尚未就绪，会导致 pywebview 的 WinForms 后端 `import _cffi_backend` 失败，应用窗口起不来。
  - 若本机已装 3.12，`初始化环境.bat` 会自动优先使用 `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`。
  - 没有 3.12 的话，脚本会 fallback 到 PATH 里的 `python`，但版本不满足 3.10–3.12 会报错退出。
- 首次初始化需要联网（pip 拉取 pywebview / oracledb / pymysql）。
- WebView2 Runtime：Win10 2004+ / Win11 默认自带，无需额外安装。

### 常见启动问题排查

**症状：双击 启动.bat 后窗口没弹出来 / 一闪而过**

多半是 venv 里的 Python 版本不对。验证方式：

```bat
.venv\Scripts\python.exe -c "import _cffi_backend; print('ok')"
```

- 如果报 `ModuleNotFoundError: No module named '_cffi_backend'`：venv 里的 Python 版本太新（3.13+），需要重建 venv：
  1. 删除 `.venv` 目录
  2. 重新双击 `初始化环境.bat`（会优先找 Python 3.12）
- 如果报 `No module named 'webview'`：你直接双击了 `app.py`，全局 Python 没装 pywebview。正确启动方式是双击 `启动.bat`，它会用 `.venv` 里的解释器。

**症状：启动.bat 报 `'t' 不是内部或外部命令` 或 `' ' 不是内部或外部命令`**

这是 .bat 文件编码问题。本仓库的 .bat 文件**必须保持纯 ASCII**（提示信息用英文，不写中文），因为 Windows 中文系统的 cmd 按 GBK 解析批处理文件，UTF-8 中文会被拆成乱码字节变成不存在的命令。如果被编辑器改成 UTF-8，需要改回 ASCII 或用 ANSI 保存。

## 使用流程

1. **连接两侧数据库**：左右两栏各填一份连接信息，勾选「记住此连接」后会保存在本机
   `%USERPROFILE%\.dbsync_tool\connections.json`（密码仅做 Base64 混淆，请自行保管好机器）。
2. **切换连接**：已连接后，每栏顶部的下拉框可快速切换到其他已保存的连接；
   选择「＋ 新建数据库链接」回到连接表单。
3. **同步数据表（结构比对）**：顶部点「同步数据表」，在任一侧输入一个或多个表名
   （逗号/换行分隔），点确定：
   - 中部显示每张表的差异明细（缺列/多列/类型/可空/默认值/主键差异）；
   - 左下 SQL = 在**左侧库**执行后结构与右侧一致；右下 SQL 反之。点「复制SQL」即可。
4. **同步数据（数据比对）**：顶部点「同步数据」，选择/输入一张表，点确定：
   - 按主键比对行（无主键时只能识别多/少行）；
   - 中部显示差异明细（仅左侧/仅右侧/内容不同，变更列高亮）；
   - 两侧各自输出行级修复 SQL（INSERT/UPDATE/DELETE），仅供复制。


![](https://img11.360buyimg.com/cxxjwimg/jfs/t1/493921/26/3209/293328/6a6c0f7cF02f63923/06d765e52da39b25.webp)
![](https://img11.360buyimg.com/cxxjwimg/jfs/t1/497116/7/729/167664/6a6c0fe0Ff60a1d75/06d77a6434731e54.webp)
![](https://img11.360buyimg.com/cxxjwimg/jfs/t1/497479/7/344/186230/6a6c1032F9fa23106/06d77a642b5d4d0b.webp)
![](https://img11.360buyimg.com/cxxjwimg/jfs/t1/483155/19/11296/140596/6a6c1074F9d64af8e/06d77a642bb2b10d.webp)

## 目录结构

```
app.py            应用入口与 JS API 桥（连接管理、比对调度）
dbcore.py         比对核心：三种方言的元数据读取、结构/数据差异 SQL 生成
web/              网页 UI（index.html / style.css / app.js）
tests/selftest.py 自测：SQLite 双库端到端验证 + Oracle/MySQL SQL 文本校验
启动.bat          启动应用
初始化环境.bat     首次创建虚拟环境
```

## 自测

```bat
.venv\Scripts\python.exe tests\selftest.py
```

## 限制说明

- 数据比对为全量内存比对，单表上限 20 万行；单方向输出 SQL 上限 5000 条（超出截断并注明）。
- 结构比对覆盖：列（类型/可空/默认值）、主键；暂不含索引、外键、触发器、视图。
- 修改列定义/主键时，SQLite 会生成重建表方案；Oracle/MySQL 用 ALTER。
- DROP 类语句前都有警告注释，请在数据库工具里确认后再执行。

## 打包成 exe（可选）

```bat
.venv\Scripts\python.exe -m pip install pyinstaller
.venv\Scripts\pyinstaller --noconfirm --windowed --name 数据库同步比对工具 ^
  --add-data "web;web" app.py
```

产物在 `dist\数据库同步比对工具\`。

## 依赖版本参考（venv Python 3.12 实测通过）

| 依赖 | 版本 | 说明 |
|---|---|---|
| pywebview | 6.2.1 | 窗口容器（WinForms 后端） |
| pythonnet | 3.1.0 | pywebview 在 Windows 上的 .NET 绑定 |
| cffi | 2.1.0 | pythonnet 的底层 C FFI，**必须有预编译 wheel**（Python 3.12 有，3.13+ 目前没有） |
| oracledb | 4.0.2 | Oracle 驱动，纯 Python 模式，免 Oracle 客户端 |
| PyMySQL | 1.2.0 | MySQL 驱动 |

## 变更日志

- 2026-07-31：修复 Python 3.14 下 venv 无法启动的问题（cffi 缺失预编译 wheel → pythonnet → pywebview WinForms 后端整条链断掉）。`初始化环境.bat` 改为优先用本机 Python 3.12 绝对路径重建 venv；README 新增环境要求与常见启动问题排查章节。
