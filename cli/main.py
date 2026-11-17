"""115-agent CLI — 命令行操作 115 网盘"""

import json
import logging
import os
from dataclasses import asdict

import click

from agent_115.client import Client, load_cookie
from agent_115.exceptions import Agent115Error
from agent_115.api import files as file_api
from agent_115.api import share as share_api
from agent_115.api import directory as dir_api
from agent_115.api import life as life_api
from agent_115.ops import unzip as unzip_ops
from agent_115.ops import change_ext as change_ext_ops

log = logging.getLogger("115-agent")


# ── 全局上下文 ──────────────────────────────

class Context:
    def __init__(self, cookie: str = "", json_output: bool = False):
        self.client = Client()
        self.cookie_override = cookie
        self.json_output = json_output
        self._cookie_loaded = False

    def ensure_cookie(self):
        if self._cookie_loaded:
            return
        cookie = load_cookie(self.cookie_override)
        if not cookie:
            raise click.UsageError(
                "未找到 115 Cookie：请使用 -c/--cookie、环境变量 PAN115_COOKIE，或先执行 115 login <cookie>"
            )
        self.client.set_cookie(cookie)
        self._cookie_loaded = True


pass_ctx = click.make_pass_decorator(Context)


class FriendlyGroup(click.Group):
    """把 SDK 异常转成简洁的错误提示，而不是整段 traceback"""

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except Agent115Error as exc:
            raise click.ClickException(str(exc)) from exc


# ── CLI 主入口 ──────────────────────────────

@click.group(cls=FriendlyGroup)
@click.option("--cookie", "-c", default="", help="115 Cookie（默认读取 PAN115_COOKIE 或 .env）")
@click.option("--json", "json_output", is_flag=True, help="JSON 格式输出")
@click.option("--verbose", "-v", is_flag=True, help="输出请求日志")
@click.pass_context
def cli(ctx, cookie, json_output, verbose):
    """115-agent: 115 网盘命令行工具"""
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(message)s")
    ctx.obj = Context(cookie, json_output)


# ── login ──────────────────────────────

@cli.command()
@click.argument("cookie")
@pass_ctx
def login(ctx, cookie):
    """设置 115 Cookie 并保存到当前目录 .env"""
    ctx.client.set_cookie(cookie)
    if not ctx.client.is_logged_in:
        raise click.UsageError("Cookie 中缺少 UID 字段")
    fd = os.open(".env", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"PAN115_COOKIE={cookie.strip()}\n")
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
    cid = file_api.resolve_path_to_cid(ctx.client, path)
    entries = file_api.list_files(ctx.client, cid, limit=limit, refresh=refresh, folders_only=dirs_only)

    if ctx.json_output:
        click.echo(json.dumps([asdict(e) for e in entries], ensure_ascii=False))
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
@click.option("--output", "-o", type=click.Path(dir_okay=False, writable=True), help="完整目录树保存到文件")
@click.option("--max-wait", default=30, show_default=True, help="等待 115 生成目录树的最长分钟数")
@pass_ctx
def tree(ctx, path, layer, output, max_wait):
    """导出目录树（大目录生成需要数分钟）"""
    ctx.ensure_cookie()
    cid = file_api.resolve_path_to_cid(ctx.client, path)
    click.echo("已提交导出任务，等待 115 生成目录树…")
    result = dir_api.export_tree(ctx.client, cid, layer_limit=layer, max_wait_s=max_wait * 60)
    click.secho(f"✓ {result['file_name']} ({result['content_size']} 字符)", fg="green")
    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(result["content"])
        click.echo(f"已保存到 {output}")
    else:
        click.echo(result["content"][:2000])
        if result["content_size"] > 2000:
            click.echo("…（仅显示前 2000 字符，使用 -o 保存完整内容）")


# ── rename ──────────────────────────────

@cli.command()
@click.argument("path")
@click.argument("new_name")
@pass_ctx
def rename(ctx, path, new_name):
    """重命名文件或目录（PATH 可为路径或 fid:<ID>）"""
    ctx.ensure_cookie()
    if path.startswith("fid:") and len(path) > 4:
        file_api.rename_entry(ctx.client, path[4:], new_name)
    else:
        _, entry = file_api.resolve_path(ctx.client, path)
        file_api.rename_entry(ctx.client, entry.id, new_name)
    click.secho(f"✓ 已重命名为: {new_name}", fg="green")


# ── change-ext ──────────────────────────────

@cli.command("change-ext")
@click.argument("path")
@click.option("--from", "old_suffix", required=True, help="原后缀（不区分大小写），如 .mp4.iso")
@click.option("--to", "new_suffix", required=True, help="新后缀，如 .mp4；可为空字符串表示直接去掉")
@click.option("-r", "--recursive", is_flag=True, help="递归子目录")
@click.option("--dry-run", is_flag=True, help="只列出将要修改的文件")
@click.option("--keep-original", is_flag=True, help="秒传成功后保留原文件（默认删进回收站）")
@pass_ctx
def change_ext(ctx, path, old_suffix, new_suffix, recursive, dry_run, keep_original):
    """批量修改扩展名（秒传新名 + 原文件进回收站，不下载不上传内容）。PATH 为目录。"""
    ctx.ensure_cookie()
    cid = file_api.resolve_path_to_cid(ctx.client, path)

    def show(r):
        if ctx.json_output:
            return
        color = {"done": "green", "dry_run": "cyan", "skipped": "yellow"}.get(r.status, "red")
        click.secho(f"[{r.status}] {r.old_name} → {r.new_name} {r.message}", fg=color)

    summary = change_ext_ops.change_ext_in_dir(
        ctx.client, cid, old_suffix=old_suffix, new_suffix=new_suffix,
        recursive=recursive, dry_run=dry_run, delete_original=not keep_original, on_result=show,
    )
    if ctx.json_output:
        click.echo(json.dumps([asdict(r) for r in summary.results], ensure_ascii=False, indent=2))
        return
    click.echo(f"共 {len(summary.results)} 个：完成 {summary.count('done')}，跳过 {summary.count('skipped')}，"
               f"失败 {summary.count('failed')}" + ("（dry-run）" if dry_run else ""))


# ── rm ──────────────────────────────

@cli.command()
@click.argument("path")
@click.option("--yes", "-y", is_flag=True, help="直接确认")
@pass_ctx
def rm(ctx, path, yes):
    """删除文件或目录（移入回收站）"""
    ctx.ensure_cookie()
    parent_cid, entry = file_api.resolve_path(ctx.client, path)
    kind = "目录" if entry.is_dir else "文件"
    if not yes:
        click.confirm(f"确认删除{kind}「{entry.name}」?", abort=True)
    file_api.delete_entries(ctx.client, [entry.id], parent_cid=parent_cid)
    click.secho(f"✓ 已删除{kind}: {entry.name}", fg="green")


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
            "entries": [asdict(e) for e in result["entries"]],
        }, ensure_ascii=False))
        return

    click.echo(f"🔍 搜索「{keyword}」共 {result['count']} 条结果:")
    for e in result["entries"]:
        icon = "📁" if e.is_dir else "📄"
        size_str = f" {_fmt_size(e.size)}" if not e.is_dir else ""
        click.echo(f"  {icon} {e.name}{size_str}")

    if result["count"] > page * limit:
        click.echo(f"  使用 -p {page + 1} 查看下一页")


# ── mv ──────────────────────────────

@cli.command()
@click.argument("path")
@click.argument("target")
@pass_ctx
def mv(ctx, path, target):
    """移动文件/目录到目标目录"""
    ctx.ensure_cookie()
    _, entry = file_api.resolve_path(ctx.client, path)
    target_cid = file_api.resolve_path_to_cid(ctx.client, target)
    file_api.move_entries(ctx.client, [entry.id], target_cid)
    click.secho(f"✓ 已移动「{entry.name}」到 {target}", fg="green")


# ── mkdir ──────────────────────────────

@cli.command()
@click.argument("path")
@pass_ctx
def mkdir(ctx, path):
    """创建新目录"""
    ctx.ensure_cookie()
    parent, name = file_api.split_path(path)
    if not name:
        raise click.UsageError("路径不能为空")
    parent_cid = file_api.resolve_path_to_cid(ctx.client, parent)
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
    cid = file_api.resolve_path_to_cid(ctx.client, path)
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
            "items": [asdict(i) for i in result["items"]],
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
    ctx.ensure_cookie()
    if not yes:
        click.confirm("确认清空回收站? 此操作不可恢复!", abort=True)
    result = file_api.empty_recycle_bin(ctx.client)
    if not result["ok"]:
        raise click.ClickException("清空回收站失败")
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
        items = group.get("items", [])
        click.echo(f"\n  [{group.get('date', '')}] {group.get('tab_title', '')} (共 {group.get('total', len(items))} 项)")
        for item in items[:5]:  # 最多显示 5 条详情
            click.echo(f"    📄 {item.get('file_name', '')}")
        if len(items) > 5:
            click.echo(f"    ... 还有 {len(items) - 5} 项")


# ── unzip ──────────────────────────────

def _prompt_password():
    try:
        return click.prompt("请输入解压密码", default="", show_default=False) or None
    except click.Abort:
        return None


@cli.command()
@click.argument("paths", nargs=-1, required=True)
@click.option("--mode", "-m", type=click.Choice(["each", "direct"]), default="each",
              help="each=分别解压到同名文件夹, direct=直接解压到当前目录")
@click.option("--password", "-p", default="", help="解压密码")
@click.option("--delete/--no-delete", default=False,
              help="体积检查通过后删除原压缩包（默认保留）")
@click.option("--skip-pre", is_flag=True, help="跳过预解压")
@click.option("--timeout", default=300, show_default=True, help="正式解压超时秒数")
@click.option("--pre-timeout", default=600, show_default=True, help="预解压超时秒数")
@pass_ctx
def unzip(ctx, paths, mode, password, delete, skip_pre, timeout, pre_timeout):
    """云解压压缩包（支持文件路径或目录批量）

    体积策略：若解压结果小于原压缩包，删除不完整结果并保留压缩包。
    """
    ctx.ensure_cookie()
    secret = password or None
    on_need_password = _prompt_password if not secret else None
    results = []

    for path in paths:
        kind, parent_cid, entries = _resolve_unzip_targets(ctx.client, path)
        if kind == "batch":
            results.extend(unzip_ops.unzip_batch(
                ctx.client,
                entries,
                parent_cid=parent_cid,
                mode=mode,
                secret=secret,
                delete_zip=delete,
                skip_pre_extract=skip_pre,
                timeout_s=timeout,
                pre_timeout_s=pre_timeout,
                on_need_password=on_need_password,
            ))
        else:
            e = entries[0]
            results.append(unzip_ops.unzip_one(
                ctx.client,
                pick_code=e.pick_code,
                file_id=e.id,
                file_name=e.name,
                parent_cid=parent_cid,
                archive_size=int(e.size or 0),
                mode=mode,
                secret=secret,
                delete_zip=delete,
                skip_pre_extract=skip_pre,
                timeout_s=timeout,
                pre_timeout_s=pre_timeout,
                on_need_password=on_need_password,
            ))

    if ctx.json_output:
        click.echo(json.dumps([asdict(r) for r in results], ensure_ascii=False))
        return

    if not results:
        click.echo("(无压缩包)")
        return

    for i, r in enumerate(results, 1):
        color = "green" if r.status == "success" else (
            "yellow" if r.status in ("incomplete", "skipped", "password_required") else "red"
        )
        size_info = ""
        if r.archive_size or r.extracted_size:
            size_info = f"  size={_fmt_size(r.extracted_size)}/{_fmt_size(r.archive_size)}"
        del_info = "  [zip deleted]" if r.zip_deleted else ""
        click.secho(
            f"[{i}/{len(results)}] {r.archive_name}  mode={r.mode}  {r.status}{size_info}{del_info}",
            fg=color,
        )
        if r.message:
            click.echo(f"    {r.message}")


@cli.command("unzip-pre")
@click.argument("paths", nargs=-1, required=True)
@click.option("--password", "-p", default="", help="解压密码")
@click.option("--pre-timeout", default=600, show_default=True, help="预解压超时秒数")
@pass_ctx
def unzip_pre(ctx, paths, password, pre_timeout):
    """预解压（索引）压缩包，完成后才可正式解压"""
    ctx.ensure_cookie()
    secret = password or None
    rows = []

    for path in paths:
        _, _, entries = _resolve_unzip_targets(ctx.client, path)
        for e in entries:
            pre = unzip_ops.ensure_pre_extract(
                ctx.client,
                e.pick_code,
                secret=secret,
                file_id=e.id,
                file_name=e.name,
                timeout_s=pre_timeout,
                on_need_password=_prompt_password if not secret else None,
            )
            rows.append({
                "name": e.name,
                "pick_code": e.pick_code,
                "status": pre.get("status"),
                "progress": pre.get("progress"),
                "renamed_to": pre.get("renamed_to", ""),
            })

    if ctx.json_output:
        click.echo(json.dumps(rows, ensure_ascii=False))
        return

    if not rows:
        click.echo("(无压缩包)")
        return
    for i, row in enumerate(rows, 1):
        click.echo(f"[{i}/{len(rows)}] {row['name']}  {row['status']}  progress={row.get('progress', 0)}")
        if row.get("renamed_to"):
            click.echo(f"    已重命名: {row['renamed_to']}")


def _resolve_unzip_targets(client, path: str):
    """解析 unzip 路径：目录 → 目录内全部压缩包；文件 → 该压缩包（精确匹配）。

    返回 (kind, parent_cid, entries)，kind 为 "batch" 或 "file"。
    """
    path = (path or "").strip()
    if not path:
        raise click.UsageError("路径不能为空")
    if path.startswith("fid:"):
        raise click.UsageError("请使用文件路径（需定位父目录与 pick_code），暂不支持 fid")

    parent_cid, entry = file_api.resolve_path(client, path)
    if entry.is_dir:
        return "batch", entry.id, unzip_ops.collect_archives_in_dir(client, entry.id)
    if not unzip_ops.is_archive_name(entry.name):
        raise click.UsageError(f"不是可云解压的压缩包: {entry.name}")
    if not entry.pick_code:
        raise click.UsageError(f"缺少 pick_code: {entry.name}")
    return "file", parent_cid, [entry]


# ── 辅助函数 ──────────────────────────────

def _fmt_size(size: int) -> str:
    size = float(size or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}PB"


if __name__ == "__main__":
    cli()
