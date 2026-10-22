from __future__ import annotations
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from agent_115.exceptions import Agent115Error, AuthError, NetworkError

from .routes_auth import router as auth_router
from .routes_files import router as files_router
from .routes_jobs import router as jobs_router

# 前端与 API 同源部署（开发时由 Vite 代理 /api），不需要 CORS；
# 原先的 allow_origins=["*"] + allow_credentials 会让任意站点携带登录态调用接口
app = FastAPI(title="115 Agent WebUI")
app.include_router(auth_router)
app.include_router(files_router)
app.include_router(jobs_router)


@app.exception_handler(Agent115Error)
def agent_error_handler(_: Request, exc: Agent115Error):
    # 115 侧 Cookie 失效不能返回 401，否则前端会误以为是 WebUI 登录过期
    if isinstance(exc, AuthError):
        status = 409
    elif isinstance(exc, NetworkError):
        status = 502
    else:
        status = 400
    return JSONResponse(status_code=status, content={"detail": str(exc)})


@app.get("/api/health")
def health():
    return {"ok": True}


# Mount built frontend as static files (SPA fallback)
dist_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(dist_dir):
    app.mount("/", StaticFiles(directory=dist_dir, html=True), name="webui")
