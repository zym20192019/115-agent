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

也可直接设置环境变量 `PAN115_COOKIE`，或对任意命令传 `-c/--cookie`。

## 环境变量

| 变量 | 说明 |
|---|---|
| `PAN115_COOKIE` | 115 Cookie 字符串 |

`115 login <cookie>` 会把 Cookie 写入当前目录的 `.env`（`PAN115_COOKIE=...`）。

## CLI 命令

全局选项：

| 选项 | 说明 |
|---|---|
| `-c` / `--cookie` | 指定 Cookie（也读环境变量 `PAN115_COOKIE`） |
| `--json` | JSON 格式输出（部分命令支持） |

| 命令 | 说明 |
|---|---|
| `115 login <cookie>` | 设置 Cookie 并写入 `.env` |
| `115 ls [path]` | 列出目录（`-l` 条数、`-r` 刷新、`-d` 仅目录） |
| `115 tree [path]` | 导出目录树（`-l` 层级） |
| `115 rename <path\|fid> <new_name>` | 重命名 |
| `115 mv <path> <target>` | 移动文件/目录 |
| `115 mkdir <path>` | 新建目录 |
| `115 rm <path>` | 删除（移入回收站，`-y` 跳过确认） |
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
3. 若结果 **小于** 原包 → 视为不完整：删除已解压结果、**保留**压缩包，并提示  
4. 若结果 **≥** 原包 → 成功；默认仍保留压缩包，仅显式 `--delete` 时删除  

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

需设置 `PAN115_COOKIE`（或工作目录下 `.env` 中有该变量）。

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
| `export_tree` | 导出目录树 |
| `search_files` | 全局搜索 |
| `recycle_bin_list` | 回收站列表 |
| `recycle_bin_restore` | 回收站还原 |
| `recycle_bin_empty` | 清空回收站 |
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
├── pyproject.toml
└── README.md
```

## 开发

```bash
pip install -e ".[dev,cli,mcp]"
pytest tests/   # 若仓库中有 tests/
```

当前版本：`0.1.0`（见 `pyproject.toml`）。

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
