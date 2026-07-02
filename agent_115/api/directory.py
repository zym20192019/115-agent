"""115 官方目录树导出 API"""

import logging
import time
from typing import Any, Dict, Optional

from ..client import Client
from ..exceptions import APIError

log = logging.getLogger("115-agent.directory")


def export_tree(client: Client, folder_cid: str, layer_limit: int = 25) -> Dict[str, Any]:
    """生成目录树并等待完成，返回 TXT 文件内容

    Args:
        client: API 客户端
        folder_cid: 目录 CID
        layer_limit: 目录层级限制

    Returns:
        {file_name, content, file_id, pick_code, content_size}
    """
    from .files import export_directory_tree, query_export_status

    # 1. 提交导出任务
    result = export_directory_tree(client, folder_cid, layer_limit=layer_limit)
    export_id = result["export_id"]
    log.info(f"导出任务已提交: export_id={export_id}")

    # 2. 轮询直到完成
    max_retries = 60
    for i in range(max_retries):
        time.sleep(2)
        status = query_export_status(client, export_id)
        if status["status"] == "completed":
            log.info(f"导出完成: {status.get('file_name')}")
            return _download_export(client, status)
        if status["status"] in ("failed", "error"):
            raise APIError(f"导出失败: {status.get('error', '未知错误')}")

    raise APIError("导出任务超时")


def _download_export(client: Client, status: dict) -> Dict[str, Any]:
    """通过 pick_code 下载导出的目录树 TXT"""
    from .files import get_file_download_info
    import requests

    pick_code = status["pick_code"]
    dl_info = get_file_download_info(client, pick_code)

    urls = dl_info.get("urls") or dl_info.get("url", []) or []
    if isinstance(urls, str):
        urls = [urls]
    if not urls:
        raise APIError("无法获取下载链接")

    # 尝试下载
    resp = requests.get(urls[0], timeout=60, headers={"User-Agent": client._session.headers["User-Agent"]})
    resp.raise_for_status()
    raw_bytes = resp.content

    # 解码文本（自动探测编码）
    text_content = _decode_tree_text(raw_bytes)

    return {
        "file_name": status.get("file_name", "目录树.txt"),
        "file_id": status.get("file_id", ""),
        "pick_code": pick_code,
        "content": text_content,
        "content_size": len(text_content),
    }


def _decode_tree_text(raw_bytes: bytes) -> str:
    """自动探测目录树文本编码"""
    payload = raw_bytes or b""
    if not payload:
        return ""
    for encoding in ("utf-8-sig", "utf-16", "utf-16le", "gb18030", "utf-8"):
        try:
            text = payload.decode(encoding)
            if text:
                return text
        except Exception:
            continue
    return payload.decode("utf-8", errors="ignore")
