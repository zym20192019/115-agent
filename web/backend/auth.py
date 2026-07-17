from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from threading import RLock
from typing import Optional

from fastapi import HTTPException, Request, Response


@dataclass
class WebUser:
    username: str
    password_hash: str


class AuthStore:
    def __init__(self) -> None:
        username = os.getenv("WEB_ADMIN_USER", "admin")
        password = os.getenv("WEB_ADMIN_PASSWORD", "change-me")
        self.users = {username: WebUser(username, self._hash(password))}
        self.sessions: dict[str, str] = {}
        self.cookies: dict[str, str] = {}
        self.lock = RLock()

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
            self.sessions[token] = username
        return token

    def user_for(self, token: str | None) -> str | None:
        with self.lock:
            return self.sessions.get(token or "")

    def logout(self, token: str | None) -> None:
        with self.lock:
            self.sessions.pop(token or "", None)
            self.cookies.pop(token or "", None)

    def set_cookie(self, token: str, cookie: str) -> None:
        with self.lock:
            self.cookies[token] = cookie.strip()

    def get_cookie(self, token: str) -> str:
        with self.lock:
            return self.cookies.get(token, "")


auth_store = AuthStore()


def require_user(request: Request) -> tuple[str, str]:
    token = request.cookies.get("web_session")
    username = auth_store.user_for(token)
    if not username:
        raise HTTPException(status_code=401, detail="请先登录")
    return username, token or ""


def set_session(response: Response, token: str) -> None:
    response.set_cookie("web_session", token, httponly=True, samesite="lax", secure=False, max_age=86400)
