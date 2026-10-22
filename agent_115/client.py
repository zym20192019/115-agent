"""115 API 客户端 — cookie 管理 + HTTP 请求"""

import json
import logging
import os
import threading
import time
from http.cookies import SimpleCookie
from typing import Any, Optional
from urllib.parse import urlencode

import requests

from .exceptions import APIError, AuthError, NetworkError

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

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


def _default_home() -> str:
    """运行期文件目录：PAN115_HOME > 服务器部署目录 /opt/115-agent > ~/.115-agent"""
    env_home = os.getenv("PAN115_HOME", "").strip()
    if env_home:
        return env_home
    if os.path.isdir("/opt/115-agent"):
        return "/opt/115-agent"
    return os.path.join(os.path.expanduser("~"), ".115-agent")


AGENT_HOME = _default_home()
SETTINGS_PATH = os.getenv("WEBUI_SETTINGS_PATH") or os.path.join(AGENT_HOME, ".webui-settings.json")
RATE_LOCK_PATH = os.path.join(AGENT_HOME, ".115-api-rate.lock")
ENV_PATH = os.path.join(AGENT_HOME, ".env")
DEFAULT_QPS = 0.5
GET_RETRIES = 2
RETRY_BACKOFF_S = 3.0


def load_qps() -> float:
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as handle:
            qps = float(json.load(handle)["qps"])
    except (OSError, KeyError, TypeError, ValueError):
        qps = DEFAULT_QPS
    # 配置文件写了 0 或负数时回退默认值，而不是让每个请求都抛异常
    return qps if qps > 0 else DEFAULT_QPS


def _read_env_cookie(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line.startswith("PAN115_COOKIE="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def load_cookie(explicit: str = "") -> str:
    """Cookie 来源优先级：显式传入 > 环境变量 PAN115_COOKIE > ./.env > <AGENT_HOME>/.env"""
    for candidate in (explicit, os.getenv("PAN115_COOKIE", "")):
        if candidate and candidate.strip():
            return candidate.strip()
    for path in (os.path.join(os.getcwd(), ".env"), ENV_PATH):
        cookie = _read_env_cookie(path)
        if cookie:
            return cookie
    return ""


def _lock_file(handle) -> None:
    if fcntl:
        fcntl.flock(handle, fcntl.LOCK_EX)
    else:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)


def _unlock_file(handle) -> None:
    if fcntl:
        fcntl.flock(handle, fcntl.LOCK_UN)
    else:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


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
        try:
            os.makedirs(os.path.dirname(RATE_LOCK_PATH), exist_ok=True)
            self._rate_file = open(RATE_LOCK_PATH, "a+", encoding="ascii")
            _lock_file(self._rate_file)
            qps = load_qps()
            interval = 1.0 / qps
            self.set_qps(qps)
            self._rate_file.seek(0)
            raw = self._rate_file.read().strip()
            try:
                next_allowed = float(raw) if raw else 0.0
            except ValueError:
                next_allowed = 0.0
            # monotonic 时钟在重启后归零，文件里残留的旧值可能远大于当前时间，不能照着睡
            delay = min(next_allowed - time.monotonic(), interval)
            if delay > 0:
                time.sleep(delay)
            self._rate_file.seek(0)
            self._rate_file.truncate()
            self._rate_file.write(f"{time.monotonic() + interval:.9f}")
            self._rate_file.flush()
        except BaseException:
            self._release()
            raise
        return self

    def _release(self):
        rate_file = getattr(self, "_rate_file", None)
        if rate_file is not None:
            try:
                _unlock_file(rate_file)
            except OSError:
                pass
            rate_file.close()
            self._rate_file = None
        self._lock.release()

    def __exit__(self, exc_type, exc, tb):
        self._release()


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
        resp = self._send(
            method, url,
            params=params, data=data, json=json_data, files=files,
            headers=headers, timeout=timeout,
        )
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
        return self._send(method, url, headers=headers, timeout=timeout)

    def _send(self, method: str, url: str, *, timeout, **kwargs) -> requests.Response:
        """经全局限流发出请求。GET 遇到超时/连接错误自动重试；POST 不重试，避免重复提交。"""
        self._check_auth()
        attempts = 1 + (GET_RETRIES if method.upper() == "GET" else 0)
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            try:
                with GLOBAL_RATE_LIMITER:
                    resp = self._session.request(method=method, url=url, timeout=timeout, **kwargs)
                resp.raise_for_status()
                _log_request(method, resp.url, started, resp.status_code)
                return resp
            except (requests.Timeout, requests.ConnectionError) as exc:
                status = "timeout" if isinstance(exc, requests.Timeout) else "network_error"
                _log_request(method, url, started, status)
                if attempt < attempts:
                    log.warning("115 请求失败，%s 秒后重试 (%s/%s): %s", RETRY_BACKOFF_S, attempt, attempts - 1, exc)
                    time.sleep(RETRY_BACKOFF_S)
                    continue
                if status == "timeout":
                    raise NetworkError(f"请求超时: {url}") from exc
                raise NetworkError(f"请求失败: {exc}") from exc
            except requests.RequestException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", "network_error")
                _log_request(method, url, started, status)
                raise NetworkError(f"请求失败: {exc}") from exc
        raise AssertionError("unreachable")

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
