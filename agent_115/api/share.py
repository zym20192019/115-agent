"""分享 API — 接收分享 / 列出 / 清理"""

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

from ..client import Client
from ..exceptions import APIError, ValidationError

log = logging.getLogger("115-agent.share")

SHARE_URL_PATTERN = re.compile(
    r"https?://115\.com/.*?[/?]s[=/]([a-zA-Z0-9]+)"
)


def parse_share_url(url: str) -> dict:
    """解析 115 分享链接，提取 share_code 和 password

    Args:
        url: 分享链接，如 https://115.com/s/swswpn3dfl3?password=xxx
    """
    url = url.strip()
    share_code = ""
    password = ""

    m = SHARE_URL_PATTERN.search(url)
    if m:
        share_code = m.group(1)
    else:
        # 尝试从 query 参数提取
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        share_code = qs.get("s", [""])[0]
        if not share_code:
            # 最后尝试路径提取
            match = re.search(r"/([a-zA-Z0-9]{8,})$", parsed.path)
            if match:
                share_code = match.group(1)

    if not share_code:
        raise ValidationError(f"无法解析分享链接: {url}")

    # 提取密码
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    password = qs.get("password", [""])[0] or qs.get("pwd", [""])[0] or qs.get("code", [""])[0]

    return {"share_code": share_code, "receive_code": password}


def receive_share(
    client: Client,
    share_code: str,
    *,
    receive_code: str = "",
    cid: str = "0",
) -> dict:
    """接收分享到指定目录

    Args:
        client: API 客户端
        share_code: 分享码
        receive_code: 提取码（可选）
        cid: 目标目录 CID，默认根目录

    Returns:
        {task_id: str, file_ids: [str]}
    """
    data = {
        "share_code": share_code,
        "cid": cid,
        "format": "json",
    }
    if receive_code:
        data["receive_code"] = receive_code

    body = client.form_post("/files/receive_share", data=data, timeout=60)
    if not body.get("state"):
        raise APIError(
            body.get("error", "接收分享失败"),
            response=body,
        )
    result = body.get("data", {})
    return {
        "task_id": result.get("task_id", ""),
        "file_ids": result.get("file_ids", []),
    }


def get_share_snapshot(
    client: Client,
    share_code: str,
    *,
    receive_code: str = "",
    limit: int = 1000,
) -> List[dict]:
    """查看分享内容（不接收）

    Args:
        client: API 客户端
        share_code: 分享码
        receive_code: 提取码（可选）
        limit: 返回条数上限

    Returns:
        分享中的文件/目录列表
    """
    data = {
        "share_code": share_code,
        "offset": "0",
        "limit": str(limit),
        "format": "json",
    }
    if receive_code:
        data["receive_code"] = receive_code

    body = client.form_post("/files/share_snapshot", data=data, timeout=60)
    entries = body.get("data", []) if isinstance(body.get("data"), list) else body.get("data", {}).get("data", [])
    return entries


def list_received_shares(client: Client, *, limit: int = 200) -> List[dict]:
    """列出已接收的分享记录"""
    body = client.get("/files/received", params={"limit": limit, "offset": "0", "format": "json"})
    data = body.get("data", []) if isinstance(body.get("data"), list) else body.get("data", {}).get("data", [])
    return data
