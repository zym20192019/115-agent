"""115 官方目录树导出 API

💡 目录树使用提示：当文件/文件夹特别多或层级很深时，
   优先用 export_tree 获取目录树再本地解析，可避免大量串行列目录请求。
   但目录树仅用于确认候选和父目录，实际文件 ID 仍需通过 SDK 获取。
"""

import json
import logging
import urllib.parse
import time
from http.cookies import SimpleCookie
from typing import Any, Dict, List, Optional, Set, Tuple

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
    pick_code = status["pick_code"]
    urls, download_cookie = _resolve_download_payload(client, pick_code)
    if not urls:
        raise APIError("无法获取下载链接")

    raw_bytes = _download_tree_bytes(client, urls, download_cookie)

    # 解码文本（自动探测编码）
    text_content = _decode_tree_text(raw_bytes)

    return {
        "file_name": status.get("file_name", "目录树.txt"),
        "file_id": status.get("file_id", ""),
        "pick_code": pick_code,
        "content": text_content,
        "content_size": len(text_content),
    }


def _collect_download_urls(payload: Any) -> List[str]:
    """Extract download URLs from the nested response returned by 115."""
    urls: List[str] = []
    seen: Set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, str):
            value = node.strip()
            if value.startswith(("http://", "https://")) and value not in seen:
                seen.add(value)
                urls.append(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for key in ("url", "download_url", "file_url", "download_url_web", "download_url_web2"):
                walk(node.get(key))
            for key in ("data", "urls", "result", "info"):
                walk(node.get(key))

    walk(payload)
    return urls


def _resolve_download_payload(client: Client, pick_code: str) -> Tuple[List[str], str]:
    """Resolve a pickcode through the current webapi download endpoint."""
    url = "https://webapi.115.com/files/download?pickcode=" + urllib.parse.quote(pick_code)
    response = client.request_raw(
        "GET",
        url,
        headers={
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://115.com/",
            "Origin": "https://115.com",
            "User-Agent": "Mozilla/5.0 115-agent",
        },
        timeout=45,
    )
    body = response.content.decode(response.encoding or "utf-8", errors="ignore")
    payload = json.loads(body)
    response_cookies = [response.headers.get("Set-Cookie", "")] if response.headers.get("Set-Cookie") else []

    if not payload.get("state", False):
        raise APIError(payload.get("error") or payload.get("message") or "115 下载地址解析失败", response=payload)

    extra_cookie_pairs: List[str] = []
    for raw_cookie in response_cookies:
        jar = SimpleCookie()
        jar.load(raw_cookie)
        for key, morsel in jar.items():
            pair = f"{key}={morsel.value}"
            if pair not in extra_cookie_pairs:
                extra_cookie_pairs.append(pair)
    return _collect_download_urls(payload), "; ".join(extra_cookie_pairs)


def _download_tree_bytes(client: Client, urls: List[str], download_cookie: str = "") -> bytes:
    """Download the exported TXT with media-hub-compatible headers/cookies."""
    merged_cookie = "; ".join(filter(None, [client.get_cookie_str(), download_cookie]))
    headers = {
        "Cookie": merged_cookie,
        "Referer": "https://115.com/",
        "Origin": "https://115.com",
        "User-Agent": "Mozilla/5.0 115-agent",
        "Accept": "*/*",
    }
    last_error: Optional[Exception] = None
    for raw_url in urls:
        parts = urllib.parse.urlsplit(raw_url)
        encoded_path = urllib.parse.quote(urllib.parse.unquote(parts.path), safe="/%:@+")
        target = urllib.parse.urlunsplit((parts.scheme, parts.netloc, encoded_path, parts.query, parts.fragment))
        try:
            response = client.request_raw("GET", target, headers=headers, timeout=60)
            return response.content
        except Exception as exc:
            last_error = exc
    raise APIError(f"目录树文件下载失败: {last_error}") from last_error


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
