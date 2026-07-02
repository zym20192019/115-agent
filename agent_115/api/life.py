"""115 Life 相关 API — 最近操作等"""

import json
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from ..client import Client
from ..exceptions import APIError, ValidationError

log = logging.getLogger("115-agent.life")

LIFE_API = "https://life.115.com"
APP_VER = "26.0"


def _parse_last_data(raw: str) -> str:
    """确保 last_data 是正确编码的 JSON 字符串"""
    if not raw:
        return urlencode({"last_time": 0, "last_count": 1, "total_count": 0})
    # 如果已经是未编码的 JSON，编码它
    try:
        if raw.startswith("{"):
            return urlencode({"last_data": raw})
        return raw
    except Exception:
        return raw


def recent_operations(
    client: Client,
    *,
    operation_type: int = 0,
    last_data: Optional[dict] = None,
    limit: int = 20,
) -> dict:
    """获取最近操作记录

    请求: GET https://life.115.com/api/1.0/web/{app_ver}/life/recent_operations
    参数: last_data={last_data_json}&operation_type={type}

    Args:
        client: API 客户端
        operation_type: 操作类型 (0=全部, 1=浏览, 2=移动复制, 3=重命名)
        last_data: 分页游标，格式 {"last_time": int, "last_count": int, "total_count": int}
        limit: 返回条数

    Returns:
        {count: int, list: [操作记录分组]}
    """
    if last_data is None:
        last_data = {"last_time": 0, "last_count": 1, "total_count": 0}

    params = {
        "last_data": json.dumps(last_data, ensure_ascii=False),
        "operation_type": str(operation_type),
    }

    headers = client._build_headers({
        "User-Agent": "Mozilla/5.0 115-agent",
    })

    body = client.request(
        "GET",
        f"{LIFE_API}/api/1.0/web/{APP_VER}/life/recent_operations",
        params=params,
        headers=headers,
        timeout=30,
    )
    if not body.get("state"):
        raise APIError(
            body.get("message", "获取最近操作失败"),
            response=body,
        )
    return body.get("data", {})
