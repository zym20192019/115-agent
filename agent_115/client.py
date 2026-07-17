"""115 API 客户端 — cookie 管理 + HTTP 请求"""

import json
import fcntl
import logging
import os
import threading
import time
from http.cookies import SimpleCookie
from typing import Any, Optional
from urllib.parse import urlencode

import requests

from .exceptions import APIError, AuthError, NetworkError, ValidationError

log = logging.getLogger("115-agent")


def _log_request(method: str, url: str, started: float, status: object = "error") -> None:
    elapsed_ms = (time.monotonic() - started) * 1000
    log.info("115 API request method=%s url=%s status=%s elapsed_ms=%.0f", method, url, status, elapsed_ms)

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


SETTINGS_PATH = "/opt/115-agent/.webui-settings.json"
RATE_LOCK_PATH = "/opt/115-agent/.115-api-rate.lock"
ENV_PATH = "/opt/115-agent/.env"


def load_qps() -> float:
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as handle:
            qps = float(json.load(handle)["qps"])
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        qps = 0.5
    if qps <= 0:
        raise ValueError("qps must be greater than zero")
    return qps


def load_cookie() -> str:
    try:
        with open(ENV_PATH, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("PAN115_COOKIE="):
                    return line.rstrip("\n").split("=", 1)[1]
    except OSError:
        pass
    return ""


class GlobalRateLimiter:
    """Cross-process serialized limiter shared by every 115 API client."""

    def __init__(self, qps: float = 1.0):
        self._lock = threading.Lock()
        self._config_lock = threading.Lock()
        self._qps = qps
        self.set_qps(qps)

    @property
    def qps(self) -> float:
        return self._qps

    def set_qps(self, qps: float) -> None:
        qps = float(qps)
        if qps <= 0:
            raise ValueError("qps must be greater than zero")
        with self._config_lock:
            self._qps = qps

    def __enter__(self):
        self._lock.acquire()
        self._rate_file = open(RATE_LOCK_PATH, "a+", encoding="ascii")
        fcntl.flock(self._rate_file, fcntl.LOCK_EX)
        qps = load_qps()
        interval = 1.0 / qps
        self.set_qps(qps)
        self._rate_file.seek(0)
        raw = self._rate_file.read().strip()
        next_allowed = float(raw) if raw else 0.0
        delay = next_allowed - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._rate_file.seek(0)
        self._rate_file.truncate()
        self._rate_file.write(f"{time.monotonic() + interval:.9f}")
        self._rate_file.flush()
        return self

    def __exit__(self, exc_type, exc, tb):
        fcntl.flock(self._rate_file, fcntl.LOCK_UN)
        self._rate_file.close()
        self._lock.release()


GLOBAL_RATE_LIMITER = GlobalRateLimiter(load_qps())


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
        started = time.monotonic()
        try:
            with GLOBAL_RATE_LIMITER:
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
            _log_request(method, resp.url, started, resp.status_code)
        except requests.Timeout:
            _log_request(method, url, started, "timeout")
            raise NetworkError(f"请求超时: {url}")
        except requests.RequestException as e:
            status = getattr(getattr(e, "response", None), "status_code", "network_error")
            _log_request(method, url, started, status)
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

    def request_raw(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[dict] = None,
        timeout: int = 30,
    ) -> requests.Response:
        """Return a raw response through the authenticated, rate-limited outlet."""
        self._check_auth()
        started = time.monotonic()
        try:
            with GLOBAL_RATE_LIMITER:
                response = self._session.request(
                    method=method, url=url, headers=headers, timeout=timeout
                )
            response.raise_for_status()
            _log_request(method, response.url, started, response.status_code)
            return response
        except requests.Timeout as exc:
            _log_request(method, url, started, "timeout")
            raise NetworkError(f"请求超时: {url}") from exc
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", "network_error")
            _log_request(method, url, started, status)
            raise NetworkError(f"请求失败: {exc}") from exc

    def get(self, path: str, *, params: Optional[dict] = None, **kw) -> Any:
        return self.request("GET", f"{API_BASE}{path}", params=params, **kw)

    def post(self, path: str, *, data: Optional[dict] = None, **kw) -> Any:
        return self.request("POST", f"{API_BASE}{path}", data=data, **kw)

    def form_post(self, path: str, *, data: Optional[dict | str] = None, **kw) -> Any:
        """POST application/x-www-form-urlencoded

        data 可为 dict，或已 urlencode 的字符串（用于 extract_file[] 等多值字段）。
        """
        headers = kw.pop("headers", {})
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        return self.request(
            "POST", f"{API_BASE}{path}",
            data=data, headers=headers, **kw,
        )

    @staticmethod
    def encode_form(
        fields: Optional[dict] = None,
        *,
        arrays: Optional[dict[str, list]] = None,
        style: str = "bracket",
    ) -> str:
        """编码 form 体，支持重复键数组。

        style:
          - bracket: key[]=a&key[]=b  （jQuery 默认）
          - index:   key[0]=a&key[1]=b（与 fid[i] 一致）
        """
        pairs: list[tuple[str, str]] = []
        for k, v in (fields or {}).items():
            if v is None:
                continue
            pairs.append((str(k), str(v)))
        for key, values in (arrays or {}).items():
            for i, val in enumerate(values or []):
                if val is None:
                    continue
                if style == "index":
                    pairs.append((f"{key}[{i}]", str(val)))
                else:
                    pairs.append((f"{key}[]", str(val)))
        return urlencode(pairs)

    def app_request(self, method: str, path: str, **kw) -> Any:
        """请求 proapi 接口"""
        headers = kw.pop("headers", {})
        headers["User-Agent"] = f"115App/{self._app_version}"
        return self.request(
            method, f"{APP_BASE}{path}",
            headers=headers, **kw,
        )
