# 115-agent 🤖

> 115 网盘 AI 智能助手 — SDK + CLI + MCP Server

基于逆向的 115 API，提供统一的 Python SDK、命令行工具和 AI（MCP）接口，让你像聊天一样操作 115 网盘。

## 快速开始

```bash
# 安装
pip install -e .

# 设置 Cookie（推荐写入 .env 文件）
115 login "UID=xxx; CID=yyy; SEID=zzz"

# 查看文件
115 ls /影视/电影

# 接收分享
115 share-receive "https://115.com/s/swswpn3dfl3?password=xxx"
```

## 环境变量

| 变量 | 说明 |
|---|---|
| `PAN115_COOKIE` | 115 Cookie 字符串 |

## CLI 命令

| 命令 | 说明 |
|---|---|
| `115 login <cookie>` | 设置 Cookie |
| `115 ls [path]` | 列出目录 |
| `115 tree [path]` | 导出目录树 |
| `115 rename <path> <new_name>` | 重命名 |
| `115 rm <path>` | 删除（移入回收站） |
| `115 share-receive <url>` | 接收分享 |

## MCP Server（AI 调用）

```bash
pip install mcp
python -m mcp_server.server
```

配置后，AI 可直接通过 MCP 协议操作 115 网盘——列出文件、重命名、删除、接收分享、导出目录树等。

## SDK 示例

```python
from agent_115.client import Client
from agent_115.api import files as fapi

client = Client("UID=xxx; CID=yyy; ...")
entries = fapi.list_files(client, cid="0")
for e in entries:
    if e.is_dir:
        print(f"📁 {e.name}")
    else:
        print(f"📄 {e.name} {e.size}bytes")
```

## 开发

```bash
pip install -e ".[dev]"
pytest tests/
```

## 已逆向的 API

- [x] 文件列表（分页/按目录）
- [x] 目录路径解析
- [x] 重命名（单个/批量）
- [x] 删除（回收站）
- [x] 目录树导出
- [x] 分享接收/查看
- [ ] 文件移动/复制
- [ ] 文件上传
- [ ] 创建目录
- [ ] 搜索文件
