from __future__ import annotations
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .routes_auth import router as auth_router
from .routes_files import router as files_router
from .routes_jobs import router as jobs_router

app = FastAPI(title="115 Agent WebUI")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)
app.include_router(files_router)
app.include_router(jobs_router)


@app.get("/api/health")
def health():
    return {"ok": True}


# Mount built frontend as static files (SPA fallback)
dist_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(dist_dir):
    app.mount("/", StaticFiles(directory=dist_dir, html=True), name="webui")
