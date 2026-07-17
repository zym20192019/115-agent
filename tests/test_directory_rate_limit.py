import multiprocessing as mp
import threading
import time
from types import SimpleNamespace

import requests

from agent_115.api import directory
from agent_115.client import Client, GlobalRateLimiter


def _cross_process_worker(_):
    limiter = GlobalRateLimiter(qps=0.5)
    with limiter:
        return time.monotonic()


def fake_response(payload=None, content=b"tree"):
    response = requests.Response()
    response.status_code = 200
    response._content = content if payload is None else __import__('json').dumps(payload).encode()
    response.encoding = "utf-8"
    response.headers["Content-Type"] = "application/json"
    return response


def test_client_request_uses_global_limiter(monkeypatch):
    limiter = GlobalRateLimiter(qps=0.5)
    starts = []
    client = Client("UID=test")
    monkeypatch.setattr("agent_115.client.GLOBAL_RATE_LIMITER", limiter)
    monkeypatch.setattr(client._session, "request", lambda **kwargs: (starts.append(time.monotonic()) or fake_response({"state": True})))
    client.request("GET", "https://mock.test/a")
    client.request("GET", "https://mock.test/b")
    assert starts[1] - starts[0] >= 1.95


def test_directory_download_payload_uses_client_raw(monkeypatch):
    client = Client("UID=test")
    calls = []
    monkeypatch.setattr(client, "request_raw", lambda *args, **kwargs: (calls.append((args, kwargs)) or fake_response({"state": True, "data": {"url": "https://mock.test/tree"}})))
    urls, cookie = directory._resolve_download_payload(client, "pick")
    assert urls == ["https://mock.test/tree"]
    assert len(calls) == 1


def test_directory_download_bytes_uses_client_raw(monkeypatch):
    client = Client("UID=test")
    calls = []
    monkeypatch.setattr(client, "request_raw", lambda *args, **kwargs: (calls.append((args, kwargs)) or fake_response(content=b"tree-bytes")))
    assert directory._download_tree_bytes(client, ["https://mock.test/tree"]) == b"tree-bytes"
    assert len(calls) == 1


def test_export_tree_uses_limited_client_for_all_requests(monkeypatch):
    client = Client("UID=test")
    calls = []
    monkeypatch.setattr(directory.time, "sleep", lambda _: None)
    monkeypatch.setattr("agent_115.api.files.export_directory_tree", lambda c, cid, layer_limit: calls.append("submit") or {"export_id": "x"})
    monkeypatch.setattr("agent_115.api.files.query_export_status", lambda c, eid: calls.append("status") or {"status": "completed", "pick_code": "p", "file_name": "tree.txt"})
    monkeypatch.setattr(directory, "_resolve_download_payload", lambda c, p: calls.append("resolve") or (["https://mock.test/tree"], ""))
    monkeypatch.setattr(directory, "_download_tree_bytes", lambda c, urls, cookie: calls.append("download") or b"root\\nfile.mp4")
    result = directory.export_tree(client, "0")
    assert result["content"] == "root\\nfile.mp4"
    assert calls == ["submit", "status", "resolve", "download"]


def test_cross_process_lock_enforces_two_seconds():
    with mp.Pool(3) as pool:
        starts = sorted(pool.map(_cross_process_worker, range(3)))
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert min(gaps) >= 1.95


def test_thread_serialization_at_half_qps():
    limiter = GlobalRateLimiter(qps=0.5)
    starts = []

    def worker():
        with limiter:
            starts.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    starts.sort()
    assert all(b - a >= 1.95 for a, b in zip(starts, starts[1:]))
