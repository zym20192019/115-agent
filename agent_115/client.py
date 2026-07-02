"""115 API 客户端 — cookie 管理 + HTTP 请求"""

import json
import logging
import time
from http.cookies import SimpleCookie
from typing import Any, Optional
from urllib.parse import urlencode

import requests

from .exceptions import APIError, AuthError, NetworkError, ValidationError

log = logging.getLogger("115-agent")

# 默认请求头
BASE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://115.com/",
    "Origin": "https://115.com",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36 115Browser/36.0.0"
    ),
}

API_BASE = "https://webapi.115.com"
APP_BASE = "https://proapi.115.com"
APP_VER = "3.0.9.5"


class Client:
    """115 API 客户端"""

    def __init__(self, cookie_str: str = "", app_version: str = APP_VER):
        self._session = requests.Session()
        self._session.headers.update(BASE_HEADERS)
        self._app_version = app_version

        if cookie_str:
            self.set_cookie(cookie_str)

    # ── Cookie 管理 ──────────────────────────────

    def set_cookie(self, cookie_str: str) -> None:
        """设置 115 cookie 字符串"""
        raw = str(cookie_str or "").strip()
        if not raw:
            raise AuthError("Cookie 不能为空")
        cookie = SimpleCookie()
        cookie.load(raw)
        for key, morsel in cookie.items():
            self._session.cookies.set(key, morsel.value)
        log.info("Cookie 已设置")

    def get_cookie_str(self) -> str:
        """获取当前 cookie 字符串"""
        parts = []
        for cookie in self._session.cookies:
            parts.append(f"{cookie.name}={cookie.value}")
        return "; ".join(parts)

    @property
    def is_logged_in(self) -> bool:
        """检查是否已登录"""
        return bool(self._session.cookies.get("UID"))

    # ── 通用请求 ──────────────────────────────

    def _build_headers(self, extra: Optional[dict] = None) -> dict:
        headers = {
            "Cookie": self.get_cookie_str(),
        }
        if extra:
            headers.update(extra)
        return headers

    def _check_auth(self) -> None:
        if not self.is_logged_in:
            raise AuthError("未登录，请先设置 Cookie")

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict] = None,
        data: Optional[dict] = None,
        json_data: Optional[dict] = None,
        files: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: int = 30,
    ) -> Any:
        """发送 HTTP 请求并解析 JSON 响应"""
        self._check_auth()
        try:
            resp = self._session.request(
                method=method,
                url=url,
                params=params,
                data=data,
                json=json_data,
                files=files,
                headers=headers,
                timeout=timeout,
            )
            resp.raise_for_status()
        except requests.Timeout:
            raise NetworkError(f"请求超时: {url}")
        except requests.RequestException as e:
            raise NetworkError(f"请求失败: {e}")

        try:
            body = resp.json()
        except ValueError:
            raise APIError(f"响应不是 JSON: {resp.text[:200]}")

        if not body.get("state", True):
            errno = body.get("errno", -1)
            if errno in (403, 402, 40001):
                raise AuthError(f"登录失效: {body.get('error', '')}")
            raise APIError(
                body.get("error", "") or body.get("message", "") or "未知错误",
                errno=errno,
                response=body,
            )
        return body

    def get(self, path: str, *, params: Optional[dict] = None, **kw) -> Any:
        return self.request("GET", f"{API_BASE}{path}", params=params, **kw)

    def post(self, path: str, *, data: Optional[dict] = None, **kw) -> Any:
        return self.request("POST", f"{API_BASE}{path}", data=data, **kw)

    def form_post(self, path: str, *, data: Optional[dict] = None, **kw) -> Any:
        """POST application/x-www-form-urlencoded"""
        headers = kw.pop("headers", {})
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        return self.request(
            "POST", f"{API_BASE}{path}",
            data=data, headers=headers, **kw,
        )

    def app_request(self, method: str, path: str, **kw) -> Any:
        """请求 proapi 接口"""
        headers = kw.pop("headers", {})
        headers["User-Agent"] = f"115App/{self._app_version}"
        return self.request(
            method, f"{APP_BASE}{path}",
            headers=headers, **kw,
        )
