"""云解压编排 — 预解压 / 分别·直接解压 / 体积安全策略 / 批量串行"""

from __future__ import annotations

import logging
import time
from difflib import SequenceMatcher
from typing import Callable, List, Literal, Optional

from ..client import Client
from ..exceptions import APIError, ValidationError
from ..models import FileEntry, UnzipResult
from ..api import extract as extract_api
from ..api import files as file_api

log = logging.getLogger("115-agent.ops.unzip")

ARCHIVE_EXTS = (
    ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz",
    ".iso", ".001", ".cab", ".arj", ".lzh", ".wim",
)
SKIP_MARK = "无法云解压"
SIM_THRESHOLD = 0.6


def is_archive_name(name: str) -> bool:
    lower = (name or "").lower()
    if SKIP_MARK in (name or ""):
        return False
    return any(lower.endswith(ext) for ext in ARCHIVE_EXTS)


def strip_archive_ext(name: str) -> str:
    """去掉最后一个已知压缩扩展名。"""
    base = name or ""
    lower = base.lower()
    for ext in sorted(ARCHIVE_EXTS, key=len, reverse=True):
        if lower.endswith(ext):
            return base[: -len(ext)]
    # 通用：最后一个 .
    if "." in base:
        return base.rsplit(".", 1)[0]
    return base


def name_similarity(a: str, b: str) -> float:
    """名称相似度 0~1（对齐脚本 sim 语义，实现用 SequenceMatcher）。"""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _insert_skip_mark(name: str) -> str:
    """file.zip -> file（无法云解压）.zip"""
    if SKIP_MARK in name:
        return name
    if "." in name:
        stem, ext = name.rsplit(".", 1)
        return f"{stem}（{SKIP_MARK}）.{ext}"
    return f"{name}（{SKIP_MARK}）"


def list_all_entries(
    client: Client,
    cid: str,
    *,
    page_size: int = 1000,
    refresh: bool = False,
) -> List[FileEntry]:
    """分页列出目录全部条目。"""
    offset = 0
    results: List[FileEntry] = []
    while True:
        batch = file_api.list_files(
            client, cid, limit=page_size, offset=offset, refresh=refresh and offset == 0,
        )
        if not batch:
            break
        results.extend(batch)
        if len(batch) < page_size:
            break
        offset += len(batch)
    return results


def sum_entry_size(client: Client, entry_id: str, is_dir: bool, known_size: int = 0) -> int:
    """统计条目体积。

    文件/目录统一走官方 ``GET /category/get?cid=``（文件传 fid 同样可用），
    不递归列目录。失败返回 -1（体积未知）；成功返回 size_bytes（可为 0）。
    ``known_size`` 仅作接口失败时的弱回退（文件），避免误删。
    """
    try:
        info = file_api.get_category_info(client, entry_id)
        return int(info.get("size_bytes") or 0)
    except (APIError, ValueError, TypeError) as e:
        log.warning("category/get 获取体积失败（不递归）: %s", e)
        # 文件列表里已有 size 时可弱回退；目录绝不回退递归
        if not is_dir and known_size:
            return int(known_size)
        return -1
    except Exception as e:
        log.warning("category/get 获取体积异常（不递归）: %s", e)
        if not is_dir and known_size:
            return int(known_size)
        return -1

def snapshot_dir(client: Client, cid: str) -> dict[str, FileEntry]:
    return {e.name: e for e in list_all_entries(client, cid, refresh=True)}


def ensure_pre_extract(
    client: Client,
    pick_code: str,
    *,
    secret: Optional[str] = None,
    file_id: str = "",
    file_name: str = "",
    mark_damaged: bool = True,
    timeout_s: float = 600.0,
    on_need_password: Optional[Callable[[], Optional[str]]] = None,
) -> dict:
    """确保预解压就绪。"""
    # 已可列目录则跳过
    try:
        info = extract_api.get_extract_info(client, pick_code)
        if info.get("list"):
            return {"status": "ready", "progress": 100, "secret": secret}
    except APIError:
        pass

    result = extract_api.wait_push_extract(
        client,
        pick_code,
        secret=secret,
        timeout_s=timeout_s,
        on_need_password=on_need_password,
    )
    status = result.get("status")

    if status == "damaged" and mark_damaged and file_id and file_name:
        try:
            new_name = _insert_skip_mark(file_name)
            file_api.rename_entry(client, file_id, new_name)
            result["renamed_to"] = new_name
        except APIError as e:
            log.warning("损坏包重命名失败: %s", e)

    if status == "failed" and mark_damaged and file_id and file_name and "无法云解压" not in file_name:
        # status 7 脚本也会标
        try:
            new_name = _insert_skip_mark(file_name)
            file_api.rename_entry(client, file_id, new_name)
            result["renamed_to"] = new_name
        except APIError:
            pass

    return result


def _split_extract_lists(items) -> tuple[list[str], list[str]]:
    files: list[str] = []
    dirs: list[str] = []
    for it in items or []:
        name = it.file_name if hasattr(it, "file_name") else str(it.get("file_name", ""))
        cat = it.file_category if hasattr(it, "file_category") else int(it.get("file_category", 1))
        if not name:
            continue
        if int(cat) == 0:
            dirs.append(name)
        else:
            files.append(name)
    return files, dirs


def unzip_one(
    client: Client,
    *,
    pick_code: str,
    file_id: str,
    file_name: str,
    parent_cid: str,
    archive_size: int = 0,
    mode: Literal["each", "direct"] = "each",
    secret: Optional[str] = None,
    delete_zip: bool = False,
    skip_pre_extract: bool = False,
    timeout_s: float = 300.0,
    pre_timeout_s: float = 600.0,
    on_need_password: Optional[Callable[[], Optional[str]]] = None,
    on_event: Optional[Callable[[str, str, str, Optional[float]], None]] = None,
) -> UnzipResult:
    """解压单个压缩包。"""
    def emit(level: str, stage: str, message: str, progress: Optional[float] = None) -> None:
        if on_event:
            on_event(level, stage, message, progress)

    emit("info", "start", f"开始解压 {file_name}", 0)
    mode = "direct" if mode == "direct" else "each"
    base = UnzipResult(
        archive_name=file_name,
        pick_code=pick_code,
        mode=mode,
        status="failed",
        archive_size=int(archive_size or 0),
    )

    if not pick_code:
        base.message = "缺少 pick_code"
        return base
    if SKIP_MARK in (file_name or ""):
        base.status = "skipped"
        base.message = f"已标记{SKIP_MARK}，跳过"
        return base

    used_secret = secret

    # 1) 预解压
    if not skip_pre_extract:
        pre = ensure_pre_extract(
            client,
            pick_code,
            secret=used_secret,
            file_id=file_id,
            file_name=file_name,
            timeout_s=pre_timeout_s,
            on_need_password=on_need_password,
        )
        st = pre.get("status")
        emit("info", "pre_extract", f"预解压状态: {st}", 20)
        if st == "password_required":
            base.status = "password_required"
            base.message = "需要解压密码，请使用 -p/--password"
            return base
        if st == "damaged":
            base.status = "damaged"
            base.message = pre.get("renamed_to") and f"压缩包损坏，已重命名为 {pre['renamed_to']}" or "压缩包损坏"
            return base
        if st == "failed":
            base.status = "failed"
            base.message = "预解压失败"
            return base
        if st == "timeout":
            base.status = "timeout"
            base.message = "预解压超时"
            return base
        if pre.get("secret"):
            used_secret = pre["secret"]

    # 2) extract_info
    try:
        info = extract_api.get_extract_info(client, pick_code)
    except APIError as e:
        base.message = f"获取解压列表失败: {e}"
        return base

    items = info.get("list") or []
    if not items:
        base.message = "压缩包无法获取解压列表，请先预解压或手动处理"
        return base

    dirname = strip_archive_ext(file_name)
    to_pid = str(parent_cid or "0")
    created_folder_id: Optional[str] = None
    path_parts = info.get("paths") or []

    # 单层相似目录判断
    single_dir = None
    if len(items) == 1 and int(getattr(items[0], "file_category", 1)) == 0:
        single_dir = items[0].file_name

    if mode == "each":
        add_new_dir = True
        if single_dir and name_similarity(single_dir, dirname) > SIM_THRESHOLD:
            if len(single_dir) >= len(dirname):
                add_new_dir = False
        if add_new_dir:
            try:
                created = file_api.create_folder(client, dirname, parent_cid)
                to_pid = str(created.get("cid") or created.get("file_id") or "")
                created_folder_id = to_pid
                if not to_pid:
                    base.message = "创建解压目录失败"
                    return base
            except APIError as e:
                base.message = f"创建目录失败: {e}"
                return base
    else:
        # direct：可进入单层相似文件夹内部再解
        if single_dir and name_similarity(single_dir, dirname) > SIM_THRESHOLD:
            try:
                info = extract_api.get_extract_info(
                    client, pick_code, file_name=single_dir,
                )
                items = info.get("list") or items
                path_parts = info.get("paths") or path_parts
            except APIError as e:
                log.warning("进入内层目录失败，回退根列表: %s", e)

    extract_files, extract_dirs = _split_extract_lists(items)
    paths = extract_api.build_paths_string(path_parts)

    # 快照（direct 或 each 未建新目录）
    need_snapshot = not created_folder_id
    before: dict[str, FileEntry] = {}
    if need_snapshot:
        try:
            before = snapshot_dir(client, parent_cid)
        except APIError as e:
            log.warning("快照失败: %s", e)

    # 3) 提交解压
    try:
        task = extract_api.add_extract_file(
            client,
            pick_code,
            extract_files=extract_files,
            extract_dirs=extract_dirs,
            to_pid=to_pid,
            paths=paths,
        )
    except APIError as e:
        errno = getattr(e, "errno", -1)
        base.message = str(e)
        if errno == 990028:
            base.message = "存储空间不足"
        elif errno == 51005:
            base.message = "压缩包已损坏或无法解压"
            base.status = "damaged"
        # 清理空目录
        if created_folder_id:
            _safe_delete(client, [created_folder_id], parent_cid)
        return base

    extract_id = str(task.get("extract_id", ""))
    base.extract_id = extract_id
    base.target_cid = to_pid
    emit("info", "submitted", f"已提交解压任务 {extract_id}", 35)

    progress = extract_api.wait_extract(
        client, extract_id, timeout_s=timeout_s, interval_s=1.0,
    )
    emit("info", "extract", f"正式解压状态: {progress.get('status')}", 75 if progress.get("status") == "completed" else None)
    pstatus = progress.get("status")
    if pstatus == "timeout":
        base.status = "timeout"
        base.message = "解压超时"
        return base
    if pstatus == "failed":
        errno = progress.get("errno")
        base.message = progress.get("message") or "解压失败"
        if errno in (51017, 51018):
            base.message = progress.get("message") or "含违规内容，解压失败"
        if created_folder_id:
            # 失败时若目录为空可删
            try:
                ents = list_all_entries(client, created_folder_id)
                if not ents:
                    _safe_delete(client, [created_folder_id], parent_cid)
            except APIError:
                pass
        return base

    # 4) 体积检查
    created_ids: list[str] = []
    extracted_size = 0

    if created_folder_id:
        created_ids = [created_folder_id]
        try:
            # 解压刚完成时 category 缓存可能未刷新，短暂重试
            extracted_size = 0
            for attempt in range(3):
                extracted_size = sum_entry_size(client, created_folder_id, True)
                if extracted_size > 0 or attempt == 2:
                    break
                time.sleep(1.5)
        except APIError as e:
            log.warning("统计解压目录体积失败: %s", e)
            extracted_size = 0
    else:
        try:
            after = snapshot_dir(client, parent_cid)
            new_names = set(after) - set(before)
            # 若无 delta，按期望顶层名
            if not new_names:
                expected = set(extract_files) | set(extract_dirs)
                new_names = {n for n in expected if n in after}
            created_entries = [after[n] for n in new_names if n in after]
            created_ids = [e.id for e in created_entries]
            for e in created_entries:
                size = sum_entry_size(client, e.id, e.is_dir, e.size)
                extracted_size = size if size < 0 else extracted_size + size
        except APIError as e:
            log.warning("快照对比失败: %s", e)

    base.extracted_size = max(0, int(extracted_size))
    base.created_ids = created_ids
    size_unknown = extracted_size < 0

    arch = int(archive_size or 0)
    if not size_unknown and arch > 0 and extracted_size < arch:
        # 不完整：删结果，保 zip
        if created_ids:
            _safe_delete(client, created_ids, parent_cid if not created_folder_id else parent_cid)
        base.status = "incomplete"
        base.message = (
            f"解压结果({_fmt(extracted_size)})小于原压缩包({_fmt(arch)})，"
            f"已删除不完整结果并保留压缩包，请预解压后重试或手动检查"
        )
        base.zip_deleted = False
        return base

    # 成功
    base.status = "success"
    base.message = "解压完成"

    can_delete = (
        delete_zip
        and not size_unknown
        and arch > 0
        and extracted_size >= arch
        and file_id
    )
    if delete_zip and not can_delete:
        base.message += "（未删压缩包：体积检查未通过或大小未知）"
    if can_delete:
        try:
            file_api.delete_entries(client, [file_id], parent_cid=parent_cid)
            base.zip_deleted = True
            base.message += "，已删除原压缩包"
        except APIError as e:
            base.message += f"（删除压缩包失败: {e}）"

    return base


def unzip_batch(
    client: Client,
    items: List[FileEntry],
    *,
    parent_cid: str,
    mode: Literal["each", "direct"] = "each",
    secret: Optional[str] = None,
    delete_zip: bool = False,
    skip_pre_extract: bool = False,
    timeout_s: float = 300.0,
    pre_timeout_s: float = 600.0,
    on_need_password: Optional[Callable[[], Optional[str]]] = None,
    skip_names_containing: str = SKIP_MARK,
) -> List[UnzipResult]:
    """串行批量解压。"""
    results: List[UnzipResult] = []
    for entry in items:
        if entry.is_dir:
            continue
        name = entry.name or ""
        if skip_names_containing and skip_names_containing in name:
            results.append(UnzipResult(
                archive_name=name,
                pick_code=entry.pick_code or "",
                mode=mode,
                status="skipped",
                message=f"名称含「{skip_names_containing}」，跳过",
                archive_size=int(entry.size or 0),
            ))
            continue
        if not is_archive_name(name) and not entry.pick_code:
            continue
        if not entry.pick_code:
            results.append(UnzipResult(
                archive_name=name,
                pick_code="",
                mode=mode,
                status="failed",
                message="缺少 pick_code",
                archive_size=int(entry.size or 0),
            ))
            continue

        r = unzip_one(
            client,
            pick_code=entry.pick_code,
            file_id=entry.id,
            file_name=name,
            parent_cid=parent_cid,
            archive_size=int(entry.size or 0),
            mode=mode,
            secret=secret,
            delete_zip=delete_zip,
            skip_pre_extract=skip_pre_extract,
            timeout_s=timeout_s,
            pre_timeout_s=pre_timeout_s,
            on_need_password=on_need_password,
        )
        results.append(r)
        # 空间不足则中止
        if r.status == "failed" and "空间不足" in (r.message or ""):
            break
    return results


def collect_archives_in_dir(client: Client, cid: str) -> List[FileEntry]:
    """收集目录内可云解压的压缩包。"""
    out: List[FileEntry] = []
    for e in list_all_entries(client, cid):
        if e.is_dir:
            continue
        if is_archive_name(e.name) and e.pick_code:
            out.append(e)
    return out


def _safe_delete(client: Client, ids: list[str], parent_cid: str) -> None:
    if not ids:
        return
    try:
        file_api.delete_entries(client, ids, parent_cid=parent_cid)
    except APIError as e:
        log.warning("删除失败 ids=%s: %s", ids, e)


def _fmt(size: int) -> str:
    n = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"
