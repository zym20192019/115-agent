from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from .auth import auth_store, require_user, set_session

router = APIRouter(prefix="/api")


class LoginBody(BaseModel):
    username: str
    password: str


class CookieBody(BaseModel):
    cookie: str


class QPSBody(BaseModel):
    qps: float


@router.post("/auth/login")
def login(body: LoginBody, response: Response):
    token = auth_store.login(body.username, body.password)
    if not token:
        return Response(content='{"detail":"用户名或密码错误"}', status_code=401, media_type="application/json")
    set_session(response, token)
    return {"username": body.username}


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get("web_session")
    auth_store.logout(token)
    response.delete_cookie("web_session")
    return {"ok": True}


@router.get("/auth/me")
def me(request: Request):
    username, token = require_user(request)
    return {"username": username, "115_cookie_configured": bool(auth_store.get_cookie(token))}


@router.put("/settings/115-cookie")
def set_115_cookie(body: CookieBody, request: Request):
    _, token = require_user(request)
    if not body.cookie.strip():
        return Response(content='{"detail":"Cookie 不能为空"}', status_code=422, media_type="application/json")
    auth_store.set_cookie(token, body.cookie)
    return {"configured": True}


@router.get("/settings/115-cookie/status")
def cookie_status(request: Request):
    _, token = require_user(request)
    return {"configured": bool(auth_store.get_cookie(token))}


@router.get("/settings/rate-limit")
def rate_limit_status(request: Request):
    require_user(request)
    return {"qps": auth_store.get_qps(), "serial": True}


@router.put("/settings/rate-limit")
def set_rate_limit(body: QPSBody, request: Request):
    require_user(request)
    try:
        qps = auth_store.set_qps(body.qps)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"qps": qps, "serial": True}
