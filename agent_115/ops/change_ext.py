"""通过秒传实现「改扩展名」：同内容以新文件名秒传一份 → 校验 → 原文件进回收站。

115 的普通重命名接口不允许修改扩展名；这里不下载、不上传文件内容，
只用 sha1 + 文件大小 + 一次字节范围校验完成秒传。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional

from ..api import files as file_api
from ..api import upload as upload_api
from ..client import Client
from ..exceptions import Agent115Error
from ..models import FileEntry

log = logging.getLogger("115-agent.change_ext")


@dataclass
class ChangeExtResult:
    file_id: str
    old_name: str
    new_name: str
    status: str = "pending"  # done / skipped / failed / dry_run
    message: str = ""
    new_file_id: str = ""
    original_deleted: bool = False


@dataclass
class ChangeExtSummary:
    results: List[ChangeExtResult] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for r in self.results if r.status == status)


def replace_suffix(name: str, old_suffix: str, new_suffix: str) -> Optional[str]:
    """old_suffix 不区分大小写；不匹配返回 None。"""
    if old_suffix and name.lower().endswith(old_suffix.lower()):
        return name[: len(name) - len(old_suffix)] + new_suffix
    return None


def rapid_rename(
    client: Client,
    entry: FileEntry,
    new_name: str,
    *,
    parent_cid: str,
    target_cid: Optional[str] = None,
    delete_original: bool = True,
    user: Optional[tuple[int, str]] = None,
) -> ChangeExtResult:
    """把单个文件以 new_name 秒传到 target_cid（默认原目录），校验通过后删除原文件。"""
    res = ChangeExtResult(entry.id, entry.name, new_name)
    tgt = str(target_cid or parent_cid)
    if not entry.sha1 or not entry.size:
        res.status, res.message = "skipped", "目录或缺少 sha1/大小"
        return res
    existing = {e.name.lower(): e for e in file_api.list_all_files(client, tgt, refresh=True)}
    hit = existing.get(new_name.lower())
    if hit is not None and hit.id != entry.id:
        if hit.sha1 == entry.sha1 and hit.size == entry.size:
            res.message = "目标已存在同内容文件"
            res.new_file_id = hit.id
        else:
            res.status, res.message = "skipped", "目标目录已有同名但内容不同的文件"
            return res
    else:
        upload_api.rapid_upload(
            client, filename=new_name, sha1=entry.sha1, size=entry.size,
            target_cid=tgt, pick_code=entry.pick_code, user=user,
        )
        after = {e.name.lower(): e for e in file_api.list_all_files(client, tgt, refresh=True)}
        hit = after.get(new_name.lower())
        if hit is None or hit.sha1 != entry.sha1 or hit.size != entry.size:
            res.status, res.message = "failed", "秒传返回成功但目标目录未找到同内容新文件，原文件保留"
            return res
        res.new_file_id = hit.id
    if delete_original:
        file_api.delete_entries(client, [entry.id], parent_cid=parent_cid)
        res.original_deleted = True
    res.status = "done"
    return res


def change_ext_in_dir(
    client: Client,
    cid: str,
    *,
    old_suffix: str,
    new_suffix: str,
    recursive: bool = False,
    dry_run: bool = False,
    delete_original: bool = True,
    on_result: Optional[Callable[[ChangeExtResult], None]] = None,
) -> ChangeExtSummary:
    """把目录内后缀为 old_suffix 的文件批量改为 new_suffix。"""
    summary = ChangeExtSummary()
    user = None if dry_run else upload_api.get_upload_info(client)

    def walk(dir_cid: str) -> Iterable[tuple[FileEntry, str]]:
        for e in file_api.list_all_files(client, dir_cid, refresh=True):
            if not e.sha1:  # 目录没有 sha1（is_dir 字段不可靠）
                if recursive:
                    yield from walk(e.id)
                continue
            yield e, dir_cid

    for entry, parent in list(walk(cid)):
        new_name = replace_suffix(entry.name, old_suffix, new_suffix)
        if not new_name:
            continue
        if dry_run:
            res = ChangeExtResult(entry.id, entry.name, new_name, status="dry_run")
        else:
            try:
                res = rapid_rename(client, entry, new_name, parent_cid=parent,
                                   delete_original=delete_original, user=user)
            except Agent115Error as exc:
                res = ChangeExtResult(entry.id, entry.name, new_name, status="failed", message=str(exc))
                log.warning("改扩展名失败 %s: %s", entry.name, exc)
        summary.results.append(res)
        if on_result:
            on_result(res)
    return summary
