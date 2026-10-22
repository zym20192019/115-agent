from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from threading import RLock

from fastapi import HTTPException, Request, Response

from agent_115.client import GLOBAL_RATE_LIMITER, SETTINGS_PATH, load_cookie

SESSION_TTL_S = 86400


@dataclass
class WebUser:
    username: str
    password_hash: str


class AuthStore:
    def __init__(self) -> None:
        username = os.getenv("WEB_ADMIN_USER", "admin")
        password = os.getenv("WEB_ADMIN_PASSWORD", "")
        if not password or password == "change-me":
            # 不再使用公开的默认密码：未配置时每次启动随机生成并打印到服务日志
            password = secrets.token_urlsafe(12)
            print(f"[115-agent web] 未设置 WEB_ADMIN_PASSWORD，本次启动的临时密码：{username} / {password}", flush=True)
        self.users = {username: WebUser(username, self._hash(password))}
        self.sessions: dict[str, tuple[str, float]] = {}
        self.cookies: dict[str, str] = {}
        self.settings_path = SETTINGS_PATH
        self._load_rate_limit()
        self.lock = RLock()

    def _load_rate_limit(self) -> None:
        try:
            with open(self.settings_path, encoding="utf-8") as handle:
                qps = json.load(handle).get("qps")
            if qps is not None:
                GLOBAL_RATE_LIMITER.set_qps(qps)
        except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    def get_qps(self) -> float:
        return GLOBAL_RATE_LIMITER.qps

    def set_qps(self, qps: float) -> float:
        if float(qps) != 0.5:
            raise ValueError("qps is fixed at 0.5")
        GLOBAL_RATE_LIMITER.set_qps(0.5)
        with open(self.settings_path, "w", encoding="utf-8") as handle:
            json.dump({"qps": GLOBAL_RATE_LIMITER.qps}, handle)
        return GLOBAL_RATE_LIMITER.qps

    @staticmethod
    def _hash(password: str, salt: bytes | None = None) -> str:
        salt = salt or secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
        return salt.hex() + ":" + digest.hex()

    @classmethod
    def _verify(cls, password: str, encoded: str) -> bool:
        salt_hex, digest_hex = encoded.split(":", 1)
        expected = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=16384, r=8, p=1)
        return hmac.compare_digest(expected.hex(), digest_hex)

    def login(self, username: str, password: str) -> str | None:
        user = self.users.get(username)
        if not user or not self._verify(password, user.password_hash):
            return None
        token = secrets.token_urlsafe(32)
        with self.lock:
            now = time.time()
            self.sessions = {t: s for t, s in self.sessions.items() if s[1] > now}
            self.sessions[token] = (username, now + SESSION_TTL_S)
        return token

    def user_for(self, token: str | None) -> str | None:
        with self.lock:
            session = self.sessions.get(token or "")
            if not session or session[1] <= time.time():
                return None
            return session[0]

    def logout(self, token: str | None) -> None:
        with self.lock:
            self.sessions.pop(token or "", None)
            self.cookies.pop(token or "", None)

    def set_cookie(self, token: str, cookie: str) -> None:
        with self.lock:
            self.cookies[token] = cookie.strip()

    def get_cookie(self, token: str) -> str:
        with self.lock:
            # 每次读取 .env / 环境变量，服务器上更新 Cookie 后无需重启 WebUI
            return self.cookies.get(token, "") or load_cookie()


auth_store = AuthStore()


def require_user(request: Request) -> tuple[str, str]:
    token = request.cookies.get("web_session")
    username = auth_store.user_for(token)
    if not username:
        raise HTTPException(status_code=401, detail="请先登录")
    return username, token or ""


def set_session(response: Response, token: str) -> None:
    response.set_cookie("web_session", token, httponly=True, samesite="lax", secure=False, max_age=SESSION_TTL_S)
