from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .auth import auth_store, require_user
from .jobs import event_payload, job_store

router = APIRouter(prefix="/api/jobs")


class UnzipBody(BaseModel):
    path: str
    mode: str = "each"
    password: str = ""
    delete_zip: bool = False
    skip_pre: bool = False
    timeout: int = 300
    pre_timeout: int = 600


def owned(request: Request, job_id: str):
    user, _ = require_user(request)
    try:
        return job_store.get(user, job_id)
    except KeyError:
        raise HTTPException(404, "任务不存在")


@router.post("/unzip")
def create_unzip(body: UnzipBody, request: Request):
    user, token = require_user(request)
    cookie = auth_store.get_cookie(token)
    if not cookie:
        raise HTTPException(409, "请先配置 115 Cookie")
    job = job_store.create(user, "unzip")
    job_store.start_unzip(job, cookie, body.model_dump())
    return {"job_id": job.id}


@router.get("")
def list_jobs(request: Request):
    user, _ = require_user(request)
    return {"items": [{"id": j.id, "kind": j.kind, "status": j.status, "progress": j.progress, "message": j.message} for j in job_store.list(user)]}


@router.get("/{job_id}")
def get_job(job_id: str, request: Request):
    job = owned(request, job_id)
    return {"id": job.id, "kind": job.kind, "status": job.status, "progress": job.progress, "message": job.message, "result": job.result, "events": [asdict(e) for e in job.events]}


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, request: Request):
    job_store.cancel(owned(request, job_id))
    return {"ok": True}


class PasswordBody(BaseModel):
    password: str


@router.post("/{job_id}/password")
def submit_password(job_id: str, body: PasswordBody, request: Request):
    job = owned(request, job_id)
    if job.status != "waiting_password":
        raise HTTPException(409, "任务当前不需要密码")
    job_store.submit_password(job, body.password)
    return {"ok": True}


@router.get("/{job_id}/events")
async def events(job_id: str, request: Request):
    job = owned(request, job_id)
    try:
        last_id = int(request.headers.get("Last-Event-ID", "0"))
    except ValueError:
        last_id = 0

    async def stream():
        sent = last_id
        while True:
            current = [e for e in job.events if e.id > sent]
            for event in current:
                sent = event.id
                yield f"id: {event.id}\ndata: {event_payload(event)}\n\n"
            if job.status in ("success", "failed", "cancelled", "timeout", "incomplete", "damaged") and sent >= job.next_event_id - 1:
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
