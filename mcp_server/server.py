"""115-agent MCP Server — 让 AI 直接操作 115 网盘"""

import json
import logging
import os
from typing import Any, Optional

from agent_115.client import Client
from agent_115.exceptions import Agent115Error
from agent_115.api import files as file_api
from agent_115.api import share as share_api
from agent_115.api import directory as dir_api
from agent_115.api import life as life_api

log = logging.getLogger("115-agent.mcp")


def create_server():
    """创建 MCP 服务器实例（手动构造，不依赖 mcp 库）"""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        log.warning("MCP 库未安装，请安装: pip install mcp")
        raise

    mcp = FastMCP("115-agent", log_level="WARNING")

    # 存放客户端实例的上下文
    _client: Optional[Client] = None

    def _ensure_client() -> Client:
        nonlocal _client
        if _client is not None:
            return _client
        cookie = os.environ.get("PAN115_COOKIE", "")
        if not cookie:
            # 尝试从 .env 读取
            try:
                with open(".env") as f:
                    for line in f:
                        if line.startswith("PAN115_COOKIE="):
                            cookie = line.strip().split("=", 1)[1]
                            break
            except (FileNotFoundError, IndexError):
                pass
        if not cookie:
            raise RuntimeError("未设置 PAN115_COOKIE 环境变量")
        _client = Client(cookie)
        return _client

    # ═══════════════════════════════════════
    # 工具定义
    # ═══════════════════════════════════════

    @mcp.tool()
    def list_files(path: str = "/", limit: int = 200, dirs_only: bool = False) -> str:
        """📂 列出 115 网盘目录内容

        Args:
            path: 目录路径，如 /、/影视、/我的影视/电影
            limit: 返回条数上限
            dirs_only: 仅显示目录
        """
        try:
            client = _ensure_client()
            cid = file_api.resolve_path_to_cid(client, path) if path != "/" else "0"
            entries = file_api.list_files(client, cid, limit=limit, folders_only=dirs_only)
            if not entries:
                return "(空目录)"
            lines = [f"{'📁' if e.is_dir else '📄'} {e.name}" + (f"  {_fmt_size(e.size)}" if not e.is_dir else "") for e in entries]
            return f"📋 {path} 共 {len(entries)} 项:\n" + "\n".join(lines)
        except Exception as e:
            return f"❌ 错误: {e}"

    @mcp.tool()
    def rename_file(file_id: str, new_name: str) -> str:
        """✏️ 重命名 115 网盘中的文件或目录

        Args:
            file_id: 文件/目录 ID
            new_name: 新名称
        """
        try:
            client = _ensure_client()
            file_api.rename_entry(client, file_id, new_name)
            return f"✅ 已重命名为: {new_name}"
        except Exception as e:
            return f"❌ 重命名失败: {e}"

    @mcp.tool()
    def delete_files(file_ids: list[str], parent_path: str = "/") -> str:
        """🗑️ 删除 115 网盘中的文件或目录（移入回收站）

        Args:
            file_ids: 要删除的文件/目录 ID 列表
            parent_path: 父目录路径，用于定位
        """
        try:
            client = _ensure_client()
            cid = file_api.resolve_path_to_cid(client, parent_path) if parent_path != "/" else "0"
            result = file_api.delete_entries(client, file_ids, parent_cid=cid)
            return f"✅ 已删除 {len(result['deleted'])} 项"
        except Exception as e:
            return f"❌ 删除失败: {e}"

    @mcp.tool()
    def move_files(file_ids: list[str], target_path: str) -> str:
        """📦 移动文件/目录到目标目录

        Args:
            file_ids: 要移动的文件/目录 ID 列表
            target_path: 目标目录路径，如 /影视/电影
        """
        try:
            client = _ensure_client()
            cid = file_api.resolve_path_to_cid(client, target_path)
            result = file_api.move_entries(client, file_ids, cid)
            return f"✅ 已移动 {len(result['moved'])} 项到 {target_path}"
        except Exception as e:
            return f"❌ 移动失败: {e}"

    @mcp.tool()
    def create_folder(path: str) -> str:
        """📁 创建新目录

        Args:
            path: 目录路径，如 /影视/新电影 或 /下载/分类/2024
        """
        try:
            client = _ensure_client()
            parent, name = path.strip("/").rsplit("/", 1) if "/" in path.strip("/") else ("", path.strip("/"))
            if not name:
                return "❌ 路径不能为空"
            parent_cid = file_api.resolve_path_to_cid(client, parent) if parent else "0"
            result = file_api.create_folder(client, name, parent_cid)
            return f"✅ 已创建目录: {result['name']}"
        except Exception as e:
            return f"❌ 创建目录失败: {e}"

    @mcp.tool()
    def batch_rename(renames: dict) -> str:
        """✏️ 批量重命名文件 {file_id: new_name, ...}

        Args:
            renames: 重命名映射，如 {"123456": "新名称1.mp4", "123457": "新名称2.mp4"}
        """
        try:
            client = _ensure_client()
            result = file_api.batch_rename(client, renames=renames)
            return f"✅ 批量重命名完成: {len(result['renamed'])} 个文件"
        except Exception as e:
            return f"❌ 批量重命名失败: {e}"

    @mcp.tool()
    def receive_share(url: str, save_path: str = "/") -> str:
        """📎 接收 115 分享链接

        Args:
            url: 115 分享链接，如 https://115.com/s/swswpn3dfl3?password=xxx
            save_path: 保存到的目录路径，默认为根目录
        """
        try:
            client = _ensure_client()
            info = share_api.parse_share_url(url)
            cid = file_api.resolve_path_to_cid(client, save_path) if save_path != "/" else "0"
            result = share_api.receive_share(client, info["share_code"],
                receive_code=info["receive_code"], cid=cid)
            return f"✅ 接收成功: task_id={result['task_id']}"
        except Exception as e:
            return f"❌ 接收分享失败: {e}"

    @mcp.tool()
    def export_tree(path: str = "/", layer_limit: int = 25) -> str:
        """🌳 导出 115 网盘目录树为文本（下载 TXT 内容）

        Args:
            path: 要导出的目录路径
            layer_limit: 目录层级限制
        """
        try:
            client = _ensure_client()
            cid = file_api.resolve_path_to_cid(client, path) if path != "/" else "0"
            result = dir_api.export_tree(client, cid, layer_limit=layer_limit)
            content = result["content"]
            preview = content[:1000]
            if len(content) > 1000:
                preview += f"\n\n...（共 {len(content)} 字符，完整内容可下载）"
            return f"🌳 目录树: {result['file_name']} ({_fmt_size(result['content_size'])})\n\n{preview}"
        except Exception as e:
            return f"❌ 导出目录树失败: {e}"

    @mcp.tool()
    def search_files(keyword: str, limit: int = 30) -> str:
        """🔍 全局搜索 115 网盘中的文件

        Args:
            keyword: 搜索关键词
            limit: 返回条数上限
        """
        try:
            client = _ensure_client()
            result = file_api.search_files_global(client, keyword, limit=limit)
            if not result["entries"]:
                return f"🔍 搜索「{keyword}」: 无结果"
            lines = [f"🔍 搜索「{keyword}」共 {result['count']} 条:"]
            for e in result["entries"]:
                icon = "📁" if e.is_dir else "📄"
                size_str = f" {_fmt_size(e.size)}" if not e.is_dir else ""
                lines.append(f"  {icon} {e.name}{size_str}")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ 搜索失败: {e}"

    @mcp.tool()
    def recycle_bin_list(limit: int = 40) -> str:
        """🗑️ 列出回收站文件

        Args:
            limit: 返回条数上限
        """
        try:
            client = _ensure_client()
            result = file_api.list_recycle_bin(client, limit=limit)
            if not result["items"]:
                return "🗑️ 回收站为空"
            lines = [f"🗑️ 回收站共 {result['count']} 项:"]
            for i in result["items"]:
                icon = "📁" if i.is_dir else "📄"
                lines.append(f"  {icon} {i.file_name}  ({_fmt_size(i.file_size)})  <- {i.parent_name}  [rid:{i.rid}]")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ 查询回收站失败: {e}"

    @mcp.tool()
    def recycle_bin_restore(rids: list[str]) -> str:
        """♻️ 从回收站还原文件

        Args:
            rids: 回收站记录 ID 列表（可多个）
        """
        try:
            client = _ensure_client()
            result = file_api.restore_recycle_bin_items(client, rids)
            return f"✅ 已还原 {len(result['restored'])} 项"
        except Exception as e:
            return f"❌ 还原失败: {e}"

    @mcp.tool()
    def recycle_bin_empty() -> str:
        """🗑️ 清空回收站（不可恢复）"""
        try:
            client = _ensure_client()
            file_api.empty_recycle_bin(client)
            return "✅ 回收站已清空"
        except Exception as e:
            return f"❌ 清空回收站失败: {e}"

    @mcp.tool()
    def recent_operations(op_type: int = 0, limit: int = 20) -> str:
        """📋 查看 115 网盘最近操作记录

        Args:
            op_type: 操作类型 (0=全部, 1=浏览, 2=移动复制, 3=重命名)
            limit: 返回条数
        """
        try:
            client = _ensure_client()
            result = life_api.recent_operations(client, operation_type=op_type, limit=limit)
            groups = result.get("list", [])
            if not groups:
                return "📋 无最近操作"
            lines = [f"📋 最近操作 ({result.get('count', 0)} 条):"]
            for group in groups:
                lines.append(f"\n  [{group.get('date', '')}] {group.get('tab_title', '')}")
                for item in group.get("items", [])[:5]:
                    lines.append(f"    📄 {item.get('file_name', '')}")
                if len(group.get("items", [])) > 5:
                    lines.append(f"    ... 还有 {len(group['items']) - 5} 项")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ 查询失败: {e}"

    return mcp


def _fmt_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}PB"


def main():
    """启动 MCP 服务器"""
    logging.basicConfig(level=logging.WARNING)
    mcp = create_server()
    mcp.run()


if __name__ == "__main__":
    main()
