from __future__ import annotations

import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from threading import Event, RLock
from typing import Any

from agent_115.client import Client
from agent_115.ops import unzip as unzip_ops
from agent_115.api import files as file_api

# 除排队 / 运行 / 等待密码外都是终态（含 UnzipResult 的 password_required、skipped 等）
ACTIVE_STATUSES = frozenset({"queued", "running", "waiting_password"})
TERMINAL_STATUSES = frozenset({
    "success", "failed", "cancelled", "timeout", "incomplete", "damaged", "password_required", "skipped",
})
MAX_FINISHED_JOBS = 100


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
    title: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    @property
    def is_finished(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "title": self.title, "status": self.status,
            "progress": self.progress, "message": self.message,
            "created_at": self.created_at, "finished_at": self.finished_at,
        }

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
        self._prune()
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
            return sorted((j for j in self.jobs.values() if j.user == user), key=lambda j: j.created_at, reverse=True)

    def start_unzip(self, job: Job, cookie: str, payload: dict[str, Any]) -> None:
        self.pool.submit(self._run_unzip, job, cookie, payload)

    def _run_unzip(self, job: Job, cookie: str, payload: dict[str, Any]) -> None:
        client = Client(cookie)
        job.status = "running"
        job.emit("info", "start", "任务开始")
        try:
            mode = str(payload.get("mode", "each"))
            if mode not in ("each", "direct"):
                raise ValueError("mode 必须是 each 或 direct")
            parent_cid, entry = self._locate(client, payload)
            if entry.is_dir or not unzip_ops.is_archive_name(entry.name):
                raise ValueError(f"不是可云解压的压缩包: {entry.name}")
            if not entry.pick_code:
                raise ValueError(f"缺少 pick_code: {entry.name}")
            job.title = entry.name
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
            job.emit("info" if result.status == "success" else "warning", "complete", result.message or result.status, 100)
            job.status = result.status
        except Exception as exc:
            job.emit("error", "error", str(exc))
            job.status = "cancelled" if job.cancel_event.is_set() else "failed"
        finally:
            job.finished_at = time.time()

    @staticmethod
    def _locate(client: Client, payload: dict[str, Any]):
        """优先按 parent_cid + file_id 精确定位（前端只知道 CID），兼容旧的 path 参数"""
        file_id = str(payload.get("file_id") or "").strip()
        if file_id:
            parent_cid = str(payload.get("parent_cid") or "0")
            entry = next((e for e in file_api.list_all_files(client, parent_cid) if e.id == file_id), None)
            if not entry:
                raise ValueError(f"目录 {parent_cid} 中未找到文件 {file_id}")
            return parent_cid, entry
        path = str(payload.get("path") or "").strip()
        if not path:
            raise ValueError("缺少 file_id 或 path")
        return file_api.resolve_path(client, path)

    def _prune(self) -> None:
        """只保留最近 MAX_FINISHED_JOBS 个已结束任务，避免常驻进程内存无限增长"""
        with self.lock:
            finished = sorted(
                (j for j in self.jobs.values() if j.status in TERMINAL_STATUSES),
                key=lambda j: j.finished_at or 0,
            )
            for job in finished[:-MAX_FINISHED_JOBS]:
                self.jobs.pop(job.id, None)

    def cancel(self, job: Job) -> None:
        if job.is_finished:
            return
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
