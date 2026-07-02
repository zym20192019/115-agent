"""文件操作 API — list / rename / delete / move / batch_rename"""

import logging
import re
from typing import Any, List, Optional

from ..client import Client
from ..exceptions import APIError, ValidationError
from ..models import FileEntry

log = logging.getLogger("115-agent.files")


def list_files(
    client: Client,
    cid: str = "0",
    *,
    limit: int = 1000,
    offset: int = 0,
    folders_only: bool = False,
    refresh: bool = False,
) -> List[FileEntry]:
    """列出目录下的文件和目录

    Args:
        client: API 客户端
        cid: 目录 ID，根目录为 "0"
        limit: 返回条数上限
        offset: 分页偏移
        folders_only: 仅返回目录
        refresh: 强制刷新缓存
    """
    params = {
        "cid": cid,
        "aid": "1",
        "offset": offset,
        "limit": limit,
        "type": "0",
        "show_dir": "1",
        "fc_mix": "0",
        "natsort": "1",
        "count_folders": "1",
        "format": "json",
        "custom_order": "0",
    }
    if refresh:
        params["_"] = "1"

    body = client.get("/files", params=params)
    data = body.get("data", []) if isinstance(body.get("data"), list) else body.get("data", {})

    # data 可能是 dict（单条）或 list
    entries = data if isinstance(data, list) else data.get("data", data.get("entries", data))

    results: List[FileEntry] = []
    for item in entries if isinstance(entries, list) else []:
        if not isinstance(item, dict):
            continue
        fid = str(item.get("fid") or item.get("id") or "")
        is_dir = bool(item.get("cid")) or item.get("is_dir", False)
        name = str(item.get("n") or item.get("name") or "").strip()
        if not name:
            continue

        results.append(FileEntry(
            id=fid or str(item.get("cid", "")),
            name=name,
            is_dir=is_dir,
            cid=str(item.get("cid", "")),
            size=int(item.get("s", 0) or 0),
            pick_code=str(item.get("pc", "") or "").strip(),
            sha1=str(item.get("sha", "") or "").upper(),
            updated_at=str(item.get("te", "") or ""),
            created_at=str(item.get("tp", "") or ""),
        ))

    return results


def resolve_path_to_cid(client: Client, path: str) -> str:
    """将相对路径解析为目录 CID

    Args:
        client: API 客户端
        path: 相对路径，如 "我的影视/电影"
    """
    normalized = path.strip("/").strip()
    if not normalized:
        return "0"

    current_cid = "0"
    for part in normalized.split("/"):
        if not part:
            continue
        entries = list_files(client, current_cid, folders_only=True)
        matched = next(
            (e for e in entries if e.name == part and e.is_dir),
            None,
        )
        if not matched:
            raise ValidationError(f"目录不存在: {normalized} (找不到「{part}」)")
        current_cid = matched.id
    return current_cid


def rename_entry(client: Client, entry_id: str, new_name: str) -> dict:
    """重命名单个文件/目录

    Args:
        client: API 客户端
        entry_id: 文件/目录 ID (fid/cid)
        new_name: 新名称
    """
    if not entry_id or not new_name:
        raise ValidationError("entry_id 和 new_name 不能为空")

    body = client.form_post(
        "/files/batch_rename",
        data={
            f"files_new_name[{entry_id}]": new_name,
            "format": "json",
        },
        timeout=45,
    )
    # 验证返回
    if not body.get("state"):
        raise APIError(
            body.get("error", "重命名失败"),
            response=body,
        )
    return {"id": entry_id, "name": new_name}


def batch_rename(client: Client, *,
    renames: dict,  # {file_id: new_name, ...}
) -> dict:
    """批量重命名文件

    Args:
        client: API 客户端
        renames: {file_id: new_name, ...}
    """
    if not renames:
        raise ValidationError("renames 不能为空")

    data = {"format": "json"}
    for file_id, new_name in renames.items():
        data[f"files_new_name[{file_id}]"] = new_name

    body = client.form_post("/files/batch_rename", data=data, timeout=60)
    if not body.get("state"):
        raise APIError(
            body.get("error", "批量重命名失败"),
            response=body,
        )
    return {"renamed": list(renames.keys()), "response": body}


def delete_entries(client: Client, entry_ids: List[str], parent_cid: str = "") -> dict:
    """删除文件或目录（放入回收站）

    Args:
        client: API 客户端
        entry_ids: 要删除的 ID 列表（支持批量）
        parent_cid: 父目录 ID（可选，用于清理缓存）
    """
    if not entry_ids:
        raise ValidationError("entry_ids 不能为空")

    data = {
        "pid": parent_cid,
        "ignore_warn": "1",
    }
    for i, eid in enumerate(entry_ids):
        data[f"fid[{i}]"] = eid

    body = client.form_post("/rb/delete", data=data, timeout=60)
    if not body.get("state"):
        raise APIError(
            body.get("error", "删除失败"),
            response=body,
        )
    return {"deleted": entry_ids}


def export_directory_tree(
    client: Client,
    folder_cid: str,
    *,
    layer_limit: int = 25,
) -> dict:
    """提交目录树导出任务

    Args:
        client: API 客户端
        folder_cid: 要导出的目录 CID
        layer_limit: 目录层级限制

    Returns:
        {export_id: int}
    """
    target = f"U_1_{folder_cid}"
    body = client.form_post(
        "/files/export_dir",
        data={
            "file_ids": folder_cid,
            "target": target,
            "layer_limit": str(layer_limit),
        },
        timeout=60,
    )
    export_id = int(body.get("data", {}).get("export_id", 0))
    if not export_id:
        raise APIError("导出请求未返回 export_id", response=body)
    return {"export_id": export_id}


def query_export_status(client: Client, export_id: int) -> dict:
    """查询目录树导出状态

    Returns:
        {status: "processing"|"completed"|"failed", ...}
    """
    body = client.get("/files/export_dir", params={"export_id": export_id})
    raw_data = body.get("data")
    if isinstance(raw_data, list):
        return {"status": "processing"}
    if isinstance(raw_data, dict) and raw_data.get("pick_code"):
        return {
            "status": "completed",
            "file_id": str(raw_data["file_id"]),
            "file_name": str(raw_data["file_name"]),
            "pick_code": str(raw_data["pick_code"]),
        }
    if isinstance(raw_data, dict):
        return {"status": "failed", "error": raw_data.get("error", "未知错误")}
    return {"status": "processing"}


def search_files_by_name(client: Client, cid: str, keyword: str, *,
    folders_only: bool = False,
    limit: int = 100,
) -> List[FileEntry]:
    """在目录中按名称搜索文件（通过列出目录并过滤）

    Args:
        client: API 客户端
        cid: 目录 CID
        keyword: 搜索关键词
        folders_only: 仅搜索目录
        limit: 返回上限
    """
    all_entries = list_files(client, cid, limit=limit, folders_only=folders_only)
    kw_lower = keyword.lower()
    return [e for e in all_entries if kw_lower in e.name.lower()]


def get_file_download_info(client: Client, pick_code: str) -> dict:
    """通过 pick_code 获取文件下载信息

    Returns:
        {urls: [下载链接列表], file_name: str, file_size: int}
    """
    body = client.app_request(
        "GET",
        f"/files/get_download_url?pickcode={pick_code}",
    )
    data = body.get("data") or {}
    if not data.get("file_name"):
        raise APIError("获取下载信息失败", response=body)
    return data


def list_recycle_bin(client: Client, *, limit: int = 200) -> List[FileEntry]:
    """列出回收站文件

    Args:
        client: API 客户端
        limit: 返回上限
    """
    body = client.get("/rb/list", params={"limit": limit, "offset": "0", "format": "json"})
    entries = body.get("data", []) if isinstance(body.get("data"), list) else []
    results = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        results.append(FileEntry(
            id=str(item.get("fid", "")),
            name=str(item.get("n", "")),
            is_dir=bool(item.get("cid")),
            size=int(item.get("s", 0) or 0),
        ))
    return results


def empty_recycle_bin(client: Client) -> dict:
    """清空回收站"""
    body = client.form_post("/rb/delete/all", data={"format": "json"})
    return {"ok": bool(body.get("state"))}


def create_folder(client: Client, name: str, parent_cid: str = "0") -> dict:
    """创建新目录

    请求: POST /files/add
    参数: pid={parent_cid}&cname={name}

    Args:
        client: API 客户端
        name: 目录名称
        parent_cid: 父目录 CID，默认根目录

    Returns:
        {cid: str, name: str, file_id: str}
    """
    if not name or not name.strip():
        raise ValidationError("目录名称不能为空")

    body = client.form_post("/files/add", data={"pid": parent_cid, "cname": name.strip()}, timeout=30)
    if not body.get("state"):
        raise APIError(
            body.get("error", "创建目录失败"),
            response=body,
        )
    return {
        "cid": str(body.get("cid", "")),
        "name": str(body.get("cname", name.strip())),
        "file_id": str(body.get("file_id", "")),
    }


def move_entries(
    client: Client,
    entry_ids: list[str],
    target_cid: str,
) -> dict:
    """移动文件/目录到目标目录

    请求: POST /files/move
    参数: pid={target_cid}&fid[0]={id1}&fid[1]={id2}&move_proid={timestamp}

    Args:
        client: API 客户端
        entry_ids: 要移动的文件/目录 ID 列表
        target_cid: 目标目录 CID

    Returns:
        {moved: [id列表]}
    """
    if not entry_ids:
        raise ValidationError("entry_ids 不能为空")
    if not target_cid:
        raise ValidationError("target_cid 不能为空")

    import time
    data: dict[str, str] = {
        "pid": target_cid,
        "move_proid": f"{int(time.time() * 1000)}_{entry_ids[0]}",
    }
    for i, eid in enumerate(entry_ids):
        data[f"fid[{i}]"] = eid

    body = client.form_post("/files/move", data=data, timeout=60)
    if not body.get("state"):
        raise APIError(
            body.get("error", "移动失败"),
            response=body,
        )
    return {"moved": entry_ids}
