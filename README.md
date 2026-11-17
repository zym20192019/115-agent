# 115-agent 🤖

> 115 网盘 AI 智能助手 — SDK + CLI + MCP Server

基于逆向的 115 API，提供统一的 Python SDK、命令行工具和 AI（MCP）接口，让你像聊天一样操作 115 网盘。

## 快速开始

```bash
# 安装（CLI 依赖 click）
pip install -e ".[cli]"

# 设置 Cookie（推荐写入 .env 文件）
115 login "UID=xxx; CID=yyy; SEID=zzz"

# 查看文件
115 ls /影视/电影

# 接收分享
115 share-receive "https://115.com/s/swswpn3dfl3?password=xxx"

# 云解压（需 VIP）
115 unzip /下载/movie.zip
115 unzip -m direct /下载/pack.rar
115 unzip-pre /下载/   # 仅预解压
```

Cookie 读取顺序：`-c/--cookie` 参数 → 环境变量 `PAN115_COOKIE` → 当前目录 `.env` → `$PAN115_HOME/.env`。

## 环境变量

| 变量 | 说明 |
|---|---|
| `PAN115_COOKIE` | 115 Cookie 字符串 |
| `PAN115_HOME` | 数据目录（`.env`、跨进程限速锁、WebUI 设置）。未设置时：存在 `/opt/115-agent` 则用它，否则用 `~/.115-agent` |
| `WEBUI_SETTINGS_PATH` | WebUI 设置文件路径（默认 `$PAN115_HOME/.webui-settings.json`） |

`115 login <cookie>` 会校验 Cookie 含 `UID`，并写入当前目录的 `.env`（`PAN115_COOKIE=...`，权限 600）。

所有 115 请求经过跨进程全局限速（默认 0.5 次/秒，CLI / MCP / WebUI 共享），GET 请求遇到超时或连接错误会自动重试 2 次；Linux 与 Windows 均可运行。

## CLI 命令

全局选项：

| 选项 | 说明 |
|---|---|
| `-c` / `--cookie` | 指定 Cookie（也读环境变量 `PAN115_COOKIE`） |
| `--json` | JSON 格式输出（部分命令支持） |
| `-v` / `--verbose` | 输出调试日志 |

| 命令 | 说明 |
|---|---|
| `115 login <cookie>` | 设置 Cookie 并写入 `.env` |
| `115 ls [path]` | 列出目录（`-l` 条数、`-r` 刷新、`-d` 仅目录） |
| `115 tree [path]` | 导出目录树（`-l` 层级、`-o` 保存到文件、`--max-wait` 最长等待分钟数，默认 30） |
| `115 rename <path\|fid:ID> <new_name>` | 重命名 |
| `115 mv <path> <target>` | 移动文件/目录 |
| `115 mkdir <path>` | 新建目录 |
| `115 rm <path>` | 删除（移入回收站，`-y` 跳过确认） |
| `115 change-ext <dir> --from .mp4.iso --to .mp4` | 批量改扩展名（见下） |

`rename` / `mv` / `rm` / `unzip` 的路径按**完整名称精确匹配**（旧版本是模糊匹配，可能命中名称相近的其他文件）。

目录树导出：提交成功后每 30 秒查询一次，最长等待 30 分钟，适合十万级文件的大目录。

### 改扩展名 `change-ext`

115 的普通重命名不允许改扩展名。`change-ext` 用秒传实现：以新文件名秒传一份同内容文件（只用 sha1、大小，加一次字节范围校验；不下载、不上传内容），确认新文件的 sha1 和大小一致后，把原文件删进回收站。

```bash
115 change-ext "/云下载/某合集" --from .mp4.iso --to .mp4 --dry-run   # 先看会改哪些
115 change-ext "/云下载/某合集" --from .mp4.iso --to .mp4             # 执行
115 change-ext "/某目录" --from .MP4 --to .mp4 -r --keep-original      # 递归，保留原文件
```

- 每个文件约 3 次请求（受全局限流约束），大目录请耐心等待。
- 目标目录已有同名同内容文件时，只删原文件；同名但内容不同时跳过。
- 需要 `cryptography` 库。库函数：`agent_115.ops.change_ext.rapid_rename()` / `change_ext_in_dir()`，底层协议见 `agent_115/api/upload.py`。
| `115 search <keyword>` | 全局搜索（`-l` 条数、`-p` 页码） |
| `115 share-receive <url>` | 接收分享（`-p` 保存目录） |
| `115 recycle ls` | 列出回收站（`-l` / `-p`） |
| `115 recycle restore <rid>...` | 从回收站还原（可多个 rid） |
| `115 recycle clear` | 清空回收站（`-y` 跳过确认） |
| `115 recent` | 最近操作记录（`-t` 类型、`-l` 条数） |
| `115 unzip <path>...` | 云解压（见下） |
| `115 unzip-pre <path>...` | 预解压（索引） |

### 云解压 `unzip`

| 选项 | 说明 |
|---|---|
| `-m` / `--mode` | `each`（默认，分别解压到同名文件夹）或 `direct`（解到压缩包所在目录） |
| `-p` / `--password` | 解压密码 |
| `--delete` / `--no-delete` | 默认保留压缩包；仅当体积检查通过时 `--delete` 才会删包 |
| `--skip-pre` | 跳过预解压 |
| `--timeout` | 正式解压超时秒数（默认 300） |
| `--pre-timeout` | 预解压超时秒数（默认 600） |

**体积安全策略：**

1. 解压完成后比较「解压结果体积」与「原压缩包体积」  
2. 文件/目录体积统一用官方 `GET /category/get?cid=`（文件传 fid 同样可用）返回的 `size`（如 `2.56MB` / `139.53GB`）；失败则视为体积未知，**不**递归列表求和  
3. 若结果 **小于** 原包的 95% → 视为不完整：删除本次新增的解压结果、**保留**压缩包，并提示  
4. 若结果 **≥** 原包 → 成功；默认仍保留压缩包，仅显式 `--delete` 时删除  
5. 体积未知（统计失败、目录快照失败、解压内容合并进已有同名目录等）→ 视为成功但不做校验、**不删除任何东西**  

路径可以是单个压缩包，或目录（串行批量解压该目录下压缩包）。名称含「无法云解压」的会跳过。

示例：

```bash
115 unzip /下载/movie.zip
115 unzip -m each -p mypass /下载/secret.7z
115 unzip --delete /下载/ok.zip          # 仅体积合格时删包
115 unzip /下载/压缩包目录               # 批量
115 unzip-pre /下载/big.rar
115 --json unzip /下载/a.zip
```

### `recent` 操作类型（`-t`）

| 值 | 含义 |
|---|---|
| `0` | 全部（默认） |
| `1` | 浏览 |
| `2` | 移动/复制 |
| `3` | 重命名 |

## MCP Server（AI 调用）

```bash
pip install -e ".[mcp]"
# 或
pip install mcp
python -m mcp_server.server
```

Cookie 来源同 CLI（`PAN115_COOKIE` 环境变量、工作目录 `.env` 或 `$PAN115_HOME/.env`）。列表与搜索结果带 `[id:...]`，路径参数均按完整名称精确匹配。

### 已暴露工具

| 工具 | 说明 |
|---|---|
| `list_files` | 列出目录 |
| `rename_file` | 重命名 |
| `delete_files` | 删除（回收站） |
| `move_files` | 移动 |
| `create_folder` | 新建目录 |
| `batch_rename` | 批量重命名 |
| `receive_share` | 接收分享 |
| `export_tree` | 导出目录树（`save_to` 可保存到本地文件，避免超长文本塞进对话） |
| `search_files` | 全局搜索 |
| `recycle_bin_list` | 回收站列表 |
| `recycle_bin_restore` | 回收站还原 |
| `recycle_bin_empty` | 清空回收站（必须显式传 `confirm=true`） |
| `recent_operations` | 最近操作记录 |
| `unzip_file` | 云解压单个压缩包 |
| `unzip_batch` | 目录内批量云解压 |
| `unzip_pre` | 预解压 |

## SDK 示例

```python
from agent_115.client import Client
from agent_115.api import files as fapi
from agent_115.ops import unzip as unzip_ops

client = Client("UID=xxx; CID=yyy; ...")

# 列目录
entries = fapi.list_files(client, cid="0")
for e in entries:
    if e.is_dir:
        print(f"📁 {e.name}")
    else:
        print(f"📄 {e.name} {e.size}bytes")

# 云解压
for e in entries:
    if e.pick_code and unzip_ops.is_archive_name(e.name):
        r = unzip_ops.unzip_one(
            client,
            pick_code=e.pick_code,
            file_id=e.id,
            file_name=e.name,
            parent_cid=e.cid or "0",
            archive_size=e.size,
            mode="each",
        )
        print(r.status, r.message)
```

主要模块：

| 模块 | 职责 |
|---|---|
| `agent_115.client` | HTTP 客户端 / Cookie |
| `agent_115.api.files` | 列表、路径、重命名、移动、删除、搜索、回收站、文件夹信息（`category/get`） |
| `agent_115.api.share` | 分享解析与接收 |
| `agent_115.api.directory` | 目录树导出 |
| `agent_115.api.life` | 最近操作（life.115.com） |
| `agent_115.api.extract` | 云解压 HTTP（extract_info / push / add_extract） |
| `agent_115.ops.unzip` | 解压编排（模式、体积策略、批量） |
| `agent_115.models` | `FileEntry` / `ShareInfo` / `RecycleBinItem` / `UnzipResult` |

## 项目结构

```
115-agent/
├── agent_115/          # SDK
│   ├── api/            # files / share / directory / life / extract
│   ├── ops/            # unzip 等编排
│   ├── client.py
│   ├── models.py
│   └── exceptions.py
├── cli/                # 命令行入口 `115`
├── mcp_server/         # MCP Server
├── web/                # WebUI（FastAPI + React），见 web/README.md
├── tests/
├── pyproject.toml
└── README.md
```

## 开发

```bash
pip install -e ".[dev,cli,mcp]"
pytest tests/   # 测试使用临时 PAN115_HOME，不会碰真实 .env / 限速锁
```

## 升级说明（2026-10）

- **WebUI 不再有默认密码**：`WEB_ADMIN_PASSWORD` 未设置或仍为 `change-me` 时，每次启动会随机生成密码并打印在服务日志中。请在服务环境里设置正式密码。
- **WebUI 去掉了 CORS `*`**：前端与 API 需同源部署（生产用后端直接托管 `web/frontend/dist`，开发用 Vite 代理），不再支持跨域调用。
- **前端需要重新构建**：`cd web/frontend && npm ci && npm run build`（依赖版本已锁定）。
- **CLI 路径改为精确匹配**：以前 `115 rm /下载/a` 可能命中 `a 2.zip` 等相近名称，现在必须写完整名称。
- 数据目录不再写死 `/opt/115-agent`：已存在该目录的服务器行为不变；其他环境默认 `~/.115-agent`，可用 `PAN115_HOME` 指定。

当前版本：`0.2.0`（见 `pyproject.toml`）。

云解压参考用户脚本流程（`extract_info` → `push_extract` → `add_extract_file` + 进度轮询），无浏览器 Worker 依赖。需 115 VIP。

## 已逆向的 API

- [x] 文件列表（分页/按目录）
- [x] 目录路径解析
- [x] 新建文件夹
- [x] 重命名（单个/批量）
- [x] 移动文件/目录
- [x] 删除（回收站）+ 还原 + 清空
- [x] 全局搜索文件
- [x] 目录树导出
- [x] 分享接收/查看
- [x] 最近操作记录（life.115.com）
- [x] 云解压 / 预解压（extract_info / push_extract / add_extract_file）
- [x] 文件/文件夹信息（category/get，含体积）
- [ ] 文件上传
- [ ] 复制文件

## 远程仓库

- GitHub: https://github.com/zym20192019/115-agent
