"""115-agent CLI — 命令行操作 115 网盘"""

import json
import logging
import os
import sys
from typing import Optional

import click

from agent_115.client import Client
from agent_115.exceptions import Agent115Error
from agent_115.api import files as file_api
from agent_115.api import share as share_api
from agent_115.api import directory as dir_api
from agent_115.api import life as life_api

log = logging.getLogger("115-agent")


# ── 全局上下文 ──────────────────────────────

class Context:
    def __init__(self):
        self.client = Client()
        self._cookie_loaded = False

    def ensure_cookie(self):
        if self._cookie_loaded:
            return
        cookie = os.environ.get("PAN115_COOKIE", "")
        if cookie:
            self.client.set_cookie(cookie)
            self._cookie_loaded = True
        else:
            raise click.UsageError(
                "请设置环境变量 PAN115_COOKIE，或使用 115 login <cookie>"
            )


pass_ctx = click.make_pass_decorator(Context, ensure=True)


# ── CLI 主入口 ──────────────────────────────

@click.group()
@click.option("--cookie", "-c", envvar="PAN115_COOKIE", default="", help="115 Cookie")
@click.option("--json", "json_output", is_flag=True, help="JSON 格式输出")
@click.pass_context
def cli(ctx, cookie, json_output):
    """115-agent: 115 网盘命令行工具"""
    ctx.obj = Context()
    ctx.obj.json_output = json_output
    if cookie:
        ctx.obj.client.set_cookie(cookie)


# ── login ──────────────────────────────

@cli.command()
@click.argument("cookie")
@pass_ctx
def login(ctx, cookie):
    """设置 115 Cookie 并保存到环境"""
    ctx.client.set_cookie(cookie)
    # 写入 .env 供下次使用
    with open(".env", "w") as f:
        f.write(f"PAN115_COOKIE={cookie}\n")
    click.secho("✓ Cookie 已保存到 .env", fg="green")


# ── ls ──────────────────────────────

@cli.command()
@click.argument("path", default="/")
@click.option("--limit", "-l", default=200, help="显示条数")
@click.option("--refresh", "-r", is_flag=True, help="强制刷新缓存")
@click.option("--dirs-only", "-d", is_flag=True, help="仅显示目录")
@pass_ctx
def ls(ctx, path, limit, refresh, dirs_only):
    """列出目录内容"""
    ctx.ensure_cookie()
    cid = file_api.resolve_path_to_cid(ctx.client, path) if path != "/" else "0"
    entries = file_api.list_files(ctx.client, cid, limit=limit, refresh=refresh, folders_only=dirs_only)

    if ctx.json_output:
        click.echo(json.dumps([e.__dict__ for e in entries], ensure_ascii=False))
        return

    if not entries:
        click.echo("(空)")
        return

    for e in entries:
        icon = "📁" if e.is_dir else "📄"
        size_str = f" {_fmt_size(e.size)}" if not e.is_dir else ""
        click.echo(f"  {icon} {e.name}{size_str}")


# ── tree ──────────────────────────────

@cli.command()
@click.argument("path", default="/")
@click.option("--layer", "-l", default=25, help="目录层级")
@pass_ctx
def tree(ctx, path, layer):
    """导出目录树"""
    ctx.ensure_cookie()
    cid = file_api.resolve_path_to_cid(ctx.client, path) if path != "/" else "0"
    result = dir_api.export_tree(ctx.client, cid, layer_limit=layer)
    click.secho(f"✓ {result['file_name']} ({_fmt_size(result['content_size'])})", fg="green")
    click.echo(result["content"][:2000])


# ── rename ──────────────────────────────

@cli.command()
@click.argument("path")
@click.argument("new_name")
@pass_ctx
def rename(ctx, path, new_name):
    """重命名文件或目录"""
    ctx.ensure_cookie()
    # path 格式：/目录文件路径 或 file_id
    # 如果是 file_id 直接使用
    if path.isdigit() or (path.startswith("fid:") and len(path) > 4):
        fid = path.replace("fid:", "")
        file_api.rename_entry(ctx.client, fid, new_name)
    else:
        # 通过路径查找
        parent, name = _split_path(path)
        cid = file_api.resolve_path_to_cid(ctx.client, parent) if parent else "0"
        entries = file_api.search_files_by_name(ctx.client, cid, name)
        if not entries:
            raise click.UsageError(f"未找到: {path}")
        file_api.rename_entry(ctx.client, entries[0].id, new_name)

    click.secho(f"✓ 已重命名为: {new_name}", fg="green")


# ── rm ──────────────────────────────

@cli.command()
@click.argument("path")
@click.option("--yes", "-y", is_flag=True, help="直接确认")
@pass_ctx
def rm(ctx, path, yes):
    """删除文件或目录（移入回收站）"""
    ctx.ensure_cookie()
    parent, name = _split_path(path)
    cid = file_api.resolve_path_to_cid(ctx.client, parent) if parent else "0"
    entries = file_api.search_files_by_name(ctx.client, cid, name)
    if not entries:
        raise click.UsageError(f"未找到: {path}")

    if not yes:
        click.confirm(f"确认删除 {name} ({len(entries)} 项)?", abort=True)

    entry_ids = [e.id for e in entries if not e.is_dir]
    dir_ids = [e.id for e in entries if e.is_dir]
    if dir_ids:
        file_api.delete_entries(ctx.client, dir_ids, parent_cid=cid)
    if entry_ids:
        file_api.delete_entries(ctx.client, entry_ids, parent_cid=cid)
    click.secho(f"✓ 已删除 {len(entries)} 项", fg="green")


# ── search ──────────────────────────────

@cli.command()
@click.argument("keyword")
@click.option("--limit", "-l", default=30, help="显示条数")
@click.option("--page", "-p", default=1, help="页码")
@pass_ctx
def search(ctx, keyword, limit, page):
    """全局搜索文件"""
    ctx.ensure_cookie()
    result = file_api.search_files_global(ctx.client, keyword, limit=limit, offset=(page - 1) * limit)

    if ctx.json_output:
        click.echo(json.dumps({
            "count": result["count"],
            "entries": [e.__dict__ for e in result["entries"]],
        }, ensure_ascii=False))
        return

    click.echo(f"🔍 搜索「{keyword}」共 {result['count']} 条结果:")
    for e in result["entries"]:
        icon = "📁" if e.is_dir else "📄"
        size_str = f" {_fmt_size(e.size)}" if not e.is_dir else ""
        click.echo(f"  {icon} {e.name}{size_str}")

    if result["page_count"] > page:
        click.echo(f"  共 {result['page_count']} 页，使用 -p {page + 1} 查看下一页")


# ── mv ──────────────────────────────

@cli.command()
@click.argument("path")
@click.argument("target")
@pass_ctx
def mv(ctx, path, target):
    """移动文件/目录到目标目录"""
    ctx.ensure_cookie()
    parent, name = _split_path(path)
    cid = file_api.resolve_path_to_cid(ctx.client, parent) if parent else "0"
    entries = file_api.search_files_by_name(ctx.client, cid, name)
    if not entries:
        raise click.UsageError(f"未找到: {path}")

    target_cid = file_api.resolve_path_to_cid(ctx.client, target)
    ids = [e.id for e in entries]
    file_api.move_entries(ctx.client, ids, target_cid)
    click.secho(f"✓ 已移动 {len(ids)} 项到 {target}", fg="green")


# ── mkdir ──────────────────────────────

@cli.command()
@click.argument("path")
@pass_ctx
def mkdir(ctx, path):
    """创建新目录"""
    ctx.ensure_cookie()
    parent, name = _split_path(path)
    if not name:
        raise click.UsageError("路径不能为空")
    parent_cid = file_api.resolve_path_to_cid(ctx.client, parent) if parent else "0"
    result = file_api.create_folder(ctx.client, name, parent_cid)
    click.secho(f"✓ 已创建目录: {result['name']} (cid={result['cid']})", fg="green")


# ── share-receive ──────────────────────────────

@cli.command("share-receive")
@click.argument("url")
@click.option("--path", "-p", default="/", help="保存到的目录")
@pass_ctx
def share_receive(ctx, url, path):
    """接收分享链接"""
    ctx.ensure_cookie()
    info = share_api.parse_share_url(url)
    cid = file_api.resolve_path_to_cid(ctx.client, path) if path != "/" else "0"
    result = share_api.receive_share(ctx.client, info["share_code"],
        receive_code=info["receive_code"], cid=cid)
    click.secho(f"✓ 接收成功: task_id={result.get('task_id', '-')}", fg="green")


# ── recycle ──────────────────────────────

@cli.group()
def recycle():
    """回收站操作"""

@recycle.command("ls")
@click.option("--limit", "-l", default=40, help="显示条数")
@click.option("--page", "-p", default=1, help="页码")
@pass_ctx
def recycle_ls(ctx, limit, page):
    """列出回收站文件"""
    ctx.ensure_cookie()
    result = file_api.list_recycle_bin(ctx.client, limit=limit, offset=(page - 1) * limit)

    if ctx.json_output:
        click.echo(json.dumps({
            "count": result["count"],
            "items": [{
                "rid": i.rid, "file_name": i.file_name,
                "file_size": i.file_size, "is_dir": i.is_dir,
                "cid": i.cid, "parent_name": i.parent_name,
            } for i in result["items"]],
        }, ensure_ascii=False))
        return

    click.echo(f"🗑️ 回收站共 {result['count']} 项:")
    for i in result["items"]:
        icon = "📁" if i.is_dir else "📄"
        click.echo(f"  {icon} {i.file_name}  ({_fmt_size(i.file_size)})  <- {i.parent_name}  [rid:{i.rid}]")

@recycle.command("restore")
@click.argument("rids", nargs=-1, required=True)
@pass_ctx
def recycle_restore(ctx, rids):
    """从回收站还原文件（支持多个 rid）"""
    ctx.ensure_cookie()
    result = file_api.restore_recycle_bin_items(ctx.client, list(rids))
    click.secho(f"✓ 已还原 {len(result['restored'])} 项", fg="green")

@recycle.command("clear")
@click.option("--yes", "-y", is_flag=True, help="直接确认")
@pass_ctx
def recycle_clear(ctx, yes):
    """清空回收站"""
    if not yes:
        click.confirm("确认清空回收站? 此操作不可恢复!", abort=True)
    ctx.ensure_cookie()
    file_api.empty_recycle_bin(ctx.client)
    click.secho("✓ 回收站已清空", fg="green")


# ── recent ──────────────────────────────

@cli.command()
@click.option("--type", "-t", "op_type", default=0, help="操作类型 (0=全部 1=浏览 2=移动复制 3=重命名)")
@click.option("--limit", "-l", default=20, help="显示条数")
@pass_ctx
def recent(ctx, op_type, limit):
    """查看最近操作记录"""
    ctx.ensure_cookie()
    result = life_api.recent_operations(ctx.client, operation_type=op_type, limit=limit)
    entries = result.get("list", [])

    if ctx.json_output:
        click.echo(json.dumps(result, ensure_ascii=False))
        return

    if not entries:
        click.echo("(无最近操作)")
        return

    click.echo(f"📋 最近操作 ({result.get('count', 0)} 条):")
    for group in entries:
        click.echo(f"\n  [{group.get('date', '')}] {group.get('tab_title', '')} (共 {group['total']} 项)")
        for item in group.get("items", [])[:5]:  # 最多显示 5 条详情
            click.echo(f"    📄 {item.get('file_name', '')}")
        if len(group.get("items", [])) > 5:
            click.echo(f"    ... 还有 {len(group['items']) - 5} 项")


# ── 辅助函数 ──────────────────────────────

def _fmt_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}PB"


def _split_path(path: str) -> tuple:
    """将路径拆分为 (父目录, 名称)，如 /影视/电影 -> (/影视, 电影)"""
    path = path.strip("/")
    if "/" in path:
        idx = path.rfind("/")
        return path[:idx] or "", path[idx + 1:]
    return "", path


if __name__ == "__main__":
    cli()
