from __future__ import annotations

import asyncio
import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from threading import Event, RLock
from typing import Any, Callable

from agent_115.client import Client
from agent_115.ops import unzip as unzip_ops
from agent_115.api import files as file_api


@dataclass
class JobEvent:
    id: int
    at: float
    level: str
    stage: str
    message: str
    progress: float | None = None


@dataclass
class Job:
    id: str
    user: str
    kind: str
    status: str = "queued"
    progress: float = 0
    message: str = "等待执行"
    result: Any = None
    events: list[JobEvent] = field(default_factory=list)
    cancel_event: Event = field(default_factory=Event)
    password: str | None = None
    password_event: Event = field(default_factory=Event)
    next_event_id: int = 1

    def emit(self, level: str, stage: str, message: str, progress: float | None = None) -> None:
        if progress is not None:
            self.progress = max(0, min(100, progress))
        self.message = message
        self.events.append(JobEvent(self.next_event_id, time.time(), level, stage, message, progress))
        self.next_event_id += 1
        self.events = self.events[-500:]


class JobStore:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.lock = RLock()
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="115-web")

    def create(self, user: str, kind: str) -> Job:
        job = Job(secrets.token_urlsafe(12), user, kind)
        with self.lock:
            self.jobs[job.id] = job
        return job

    def get(self, user: str, job_id: str) -> Job:
        with self.lock:
            job = self.jobs.get(job_id)
        if not job or job.user != user:
            raise KeyError(job_id)
        return job

    def list(self, user: str) -> list[Job]:
        with self.lock:
            return [j for j in self.jobs.values() if j.user == user]

    def start_unzip(self, job: Job, cookie: str, payload: dict[str, Any]) -> None:
        self.pool.submit(self._run_unzip, job, cookie, payload)

    def _run_unzip(self, job: Job, cookie: str, payload: dict[str, Any]) -> None:
        client = Client(cookie)
        job.status = "running"
        job.emit("info", "start", "任务开始")
        try:
            path = str(payload.get("path", "")).strip()
            mode = str(payload.get("mode", "each"))
            if mode not in ("each", "direct"):
                raise ValueError("mode 必须是 each 或 direct")
            parent, name = path.rsplit("/", 1) if "/" in path.strip("/") else ("", path.strip("/"))
            parent_cid = file_api.resolve_path_to_cid(client, parent) if parent else "0"
            entries = file_api.search_files_by_name(client, parent_cid, name)
            entry = next((e for e in entries if e.name == name and not e.is_dir), None)
            if not entry:
                raise ValueError(f"未找到压缩包: {path}")
            job.emit("info", "resolve", f"已定位: {entry.name}", 5)

            def password_callback() -> str | None:
                job.status = "waiting_password"
                job.emit("warning", "password", "压缩包需要密码，请在页面输入")
                job.password_event.wait(timeout=600)
                secret = job.password
                job.password = None
                job.password_event.clear()
                if job.cancel_event.is_set():
                    return None
                job.status = "running"
                return secret

            def event(level: str, stage: str, message: str, progress: float | None = None) -> None:
                if job.cancel_event.is_set():
                    raise RuntimeError("任务已取消")
                job.emit(level, stage, message, progress)

            result = unzip_ops.unzip_one(
                client,
                pick_code=entry.pick_code,
                file_id=entry.id,
                file_name=entry.name,
                parent_cid=parent_cid,
                archive_size=int(entry.size or 0),
                mode=mode,
                secret=payload.get("password") or None,
                delete_zip=bool(payload.get("delete_zip", False)),
                skip_pre_extract=bool(payload.get("skip_pre", False)),
                timeout_s=float(payload.get("timeout", 300)),
                pre_timeout_s=float(payload.get("pre_timeout", 600)),
                on_need_password=password_callback,
                on_event=event,
            )
            job.result = asdict(result)
            job.status = "success" if result.status == "success" else result.status
            job.emit("info" if result.status == "success" else "warning", "complete", result.message or result.status, 100)
        except Exception as exc:
            job.status = "cancelled" if job.cancel_event.is_set() else "failed"
            job.emit("error", "error", str(exc))

    def cancel(self, job: Job) -> None:
        job.cancel_event.set()
        if job.status == "waiting_password":
            job.password_event.set()
        job.emit("warning", "cancel", "已请求取消")

    def submit_password(self, job: Job, password: str) -> None:
        job.password = password
        job.password_event.set()


job_store = JobStore()


def event_payload(event: JobEvent) -> str:
    return json.dumps(asdict(event), ensure_ascii=False)
