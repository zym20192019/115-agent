"""115 云解压 API — extract_info / push_extract / add_extract_file"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

from ..client import Client
from ..exceptions import APIError, ValidationError
from ..models import ExtractListItem

log = logging.getLogger("115-agent.extract")


def get_extract_info(
    client: Client,
    pick_code: str,
    *,
    file_name: str = "",
    page_count: int = 999,
    paths: str = "文件",
) -> dict:
    """获取压缩包内文件列表

    GET /files/extract_info
    """
    if not pick_code:
        raise ValidationError("pick_code 不能为空")

    body = client.get(
        "/files/extract_info",
        params={
            "pick_code": pick_code,
            "file_name": file_name or "",
            "page_count": str(page_count),
            "paths": paths or "文件",
        },
        timeout=60,
    )
    data = body.get("data") or {}
    if not isinstance(data, dict):
        data = {}

    raw_list = data.get("list") or []
    items: list[ExtractListItem] = []
    for item in raw_list if isinstance(raw_list, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("file_name") or item.get("n") or "").strip()
        if not name:
            continue
        cat = item.get("file_category")
        if cat is None:
            # 部分响应用 fc / is_dir
            cat = 0 if item.get("is_dir") or str(item.get("fc", "")) == "0" else 1
        items.append(ExtractListItem(file_name=name, file_category=int(cat)))

    path_parts = data.get("paths") or []
    return {
        "list": items,
        "paths": path_parts if isinstance(path_parts, list) else [],
        "raw": data,
        "state": bool(body.get("state", True)),
    }


def push_extract(
    client: Client,
    pick_code: str,
    *,
    secret: Optional[str] = None,
) -> dict:
    """触发预解压

    POST /files/push_extract
    """
    if not pick_code:
        raise ValidationError("pick_code 不能为空")

    data: dict[str, str] = {"pick_code": pick_code}
    if secret:
        data["secret"] = secret

    body = client.form_post("/files/push_extract", data=data, timeout=60)
    raw = body.get("data") if isinstance(body.get("data"), dict) else {}
    return {
        "state": bool(body.get("state", True)),
        "unzip_status": _as_int((raw or {}).get("unzip_status"), -1),
        "progress": _as_int((raw or {}).get("progress"), 0),
        "raw": raw or body,
    }


def query_push_extract_status(client: Client, pick_code: str) -> dict:
    """查询预解压状态（无浏览器 Worker）

    优先 GET /files/push_extract；失败则用 extract_info 是否非空判断就绪。
    """
    if not pick_code:
        raise ValidationError("pick_code 不能为空")

    # 1) 尝试 GET 镜像
    try:
        body = client.get(
            "/files/push_extract",
            params={"pick_code": pick_code},
            timeout=30,
        )
        raw = body.get("data") if isinstance(body.get("data"), dict) else body
        if isinstance(raw, dict) and (
            "unzip_status" in raw or "progress" in raw or "unzip_status" in body
        ):
            status = _as_int(raw.get("unzip_status", body.get("unzip_status")), -1)
            progress = _as_int(raw.get("progress", body.get("progress")), 0)
            return {
                "unzip_status": status,
                "progress": progress,
                "raw": raw if isinstance(raw, dict) else body,
            }
    except APIError as e:
        log.debug("GET push_extract 不可用: %s", e)
    except Exception as e:
        log.debug("GET push_extract 异常: %s", e)

    # 2) POST 无 secret 探测（部分接口用 POST 返回状态）
    try:
        pushed = push_extract(client, pick_code)
        if pushed.get("unzip_status", -1) >= 0:
            return {
                "unzip_status": pushed["unzip_status"],
                "progress": pushed.get("progress", 0),
                "raw": pushed.get("raw") or {},
            }
    except APIError as e:
        # 密码需求等可能出现在 errno
        errno = getattr(e, "errno", -1)
        resp = getattr(e, "response", {}) or {}
        data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
        status = _as_int(data.get("unzip_status"), -1)
        if status >= 0:
            return {
                "unzip_status": status,
                "progress": _as_int(data.get("progress"), 0),
                "raw": data,
            }
        log.debug("POST push_extract 探测: %s errno=%s", e, errno)

    # 3) extract_info 有列表 => 视为就绪 (status=4 约定)
    try:
        info = get_extract_info(client, pick_code)
        if info.get("list"):
            return {
                "unzip_status": 4,  # ready
                "progress": 100,
                "raw": {"via": "extract_info"},
            }
        return {
            "unzip_status": 0,  # need push
            "progress": 0,
            "raw": {"via": "extract_info_empty"},
        }
    except APIError:
        return {
            "unzip_status": 0,
            "progress": 0,
            "raw": {"via": "extract_info_error"},
        }


def wait_push_extract(
    client: Client,
    pick_code: str,
    *,
    secret: Optional[str] = None,
    timeout_s: float = 600.0,
    interval_s: float = 1.0,
    on_need_password: Optional[Callable[[], Optional[str]]] = None,
) -> dict:
    """轮询预解压直到就绪/失败/需要密码。

    Returns:
        {status: ready|damaged|failed|password_required|timeout, progress, raw}
    """
    deadline = time.time() + timeout_s
    used_secret = secret
    password_tried = False

    while time.time() < deadline:
        st = query_push_extract_status(client, pick_code)
        code = int(st.get("unzip_status", -1))
        progress = int(st.get("progress", 0) or 0)
        log.debug("push_extract status=%s progress=%s", code, progress)

        if code == 0:
            try:
                push_extract(client, pick_code, secret=used_secret)
            except APIError as e:
                log.debug("push_extract 调用: %s", e)
            time.sleep(interval_s)
            continue

        if code == 1:
            time.sleep(interval_s)
            continue

        if code == 2:
            return {"status": "damaged", "progress": progress, "raw": st.get("raw")}

        if code in (3, 7):
            return {"status": "failed", "progress": progress, "raw": st.get("raw")}

        if code == 6:
            if used_secret and not password_tried:
                password_tried = True
                try:
                    push_extract(client, pick_code, secret=used_secret)
                except APIError as e:
                    log.debug("push secret: %s", e)
                time.sleep(interval_s)
                continue
            if on_need_password and not password_tried:
                new_secret = on_need_password()
                password_tried = True
                if new_secret:
                    used_secret = new_secret
                    try:
                        push_extract(client, pick_code, secret=used_secret)
                    except APIError as e:
                        log.debug("push prompt secret: %s", e)
                    time.sleep(interval_s)
                    continue
            return {
                "status": "password_required",
                "progress": progress,
                "raw": st.get("raw"),
            }

        # ready (4) or unknown success codes from site
        if code == 4 or code not in (0, 1, 2, 3, 6, 7):
            # double-check list available
            try:
                info = get_extract_info(client, pick_code)
                if info.get("list"):
                    return {
                        "status": "ready",
                        "progress": max(progress, 100),
                        "raw": st.get("raw"),
                        "secret": used_secret,
                    }
            except APIError:
                pass
            if code == 4:
                return {
                    "status": "ready",
                    "progress": 100,
                    "raw": st.get("raw"),
                    "secret": used_secret,
                }
            # unknown but progress 100
            if progress >= 100:
                return {
                    "status": "ready",
                    "progress": 100,
                    "raw": st.get("raw"),
                    "secret": used_secret,
                }
            time.sleep(interval_s)
            continue

        time.sleep(interval_s)

    return {"status": "timeout", "progress": 0, "raw": {}}


def add_extract_file(
    client: Client,
    pick_code: str,
    *,
    extract_files: list[str],
    extract_dirs: list[str],
    to_pid: str,
    paths: str,
    array_style: str = "bracket",
) -> dict:
    """提交正式解压任务

    POST /files/add_extract_file
    """
    if not pick_code:
        raise ValidationError("pick_code 不能为空")
    if not to_pid and to_pid != "0":
        raise ValidationError("to_pid 不能为空")

    fields = {
        "pick_code": pick_code,
        "to_pid": str(to_pid),
        "paths": paths or "文件",
    }
    body_str = Client.encode_form(
        fields,
        arrays={
            "extract_file": list(extract_files or []),
            "extract_dir": list(extract_dirs or []),
        },
        style=array_style,
    )

    try:
        body = client.form_post(
            "/files/add_extract_file",
            data=body_str,
            timeout=120,
        )
    except APIError as e:
        # 参数错误时尝试 index 风格
        if getattr(e, "errno", None) == 990002 and array_style == "bracket":
            log.warning("add_extract_file bracket 编码失败，回退 index 风格")
            return add_extract_file(
                client,
                pick_code,
                extract_files=extract_files,
                extract_dirs=extract_dirs,
                to_pid=to_pid,
                paths=paths,
                array_style="index",
            )
        raise

    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    extract_id = str((data or {}).get("extract_id") or body.get("extract_id") or "")
    if not extract_id:
        raise APIError("解压任务未返回 extract_id", response=body)
    return {"extract_id": extract_id, "raw": data or body}


def query_extract_progress(client: Client, extract_id: str | int) -> dict:
    """查询正式解压进度

    GET /files/add_extract_file?extract_id=
    """
    if extract_id is None or str(extract_id) == "":
        raise ValidationError("extract_id 不能为空")

    try:
        body = client.get(
            "/files/add_extract_file",
            params={"extract_id": str(extract_id)},
            timeout=30,
        )
    except APIError as e:
        return {
            "percent": 0,
            "status": "failed",
            "errno": getattr(e, "errno", -1),
            "message": str(e),
            "raw": getattr(e, "response", {}) or {},
        }

    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    percent = _as_float(
        (data or {}).get("percent", body.get("percent", body.get("data", {}).get("percent") if isinstance(body.get("data"), dict) else 0)),
        0.0,
    )
    # 有的响应 percent 在顶层 data 数字字段
    if percent == 0 and isinstance(body.get("data"), dict):
        percent = _as_float(body["data"].get("percent"), 0.0)

    errno = _as_int(body.get("errno", (data or {}).get("errno") or (data or {}).get("code")), 0)
    code = _as_int((data or {}).get("code"), errno)
    message = str(
        body.get("message")
        or body.get("error")
        or (data or {}).get("message")
        or ""
    )

    if code in (51017, 51018) or errno in (51017, 51018):
        return {
            "percent": percent,
            "status": "failed",
            "errno": code or errno,
            "message": message or "含违规内容",
            "raw": body,
        }

    if not body.get("state", True) and percent < 100:
        return {
            "percent": percent,
            "status": "failed",
            "errno": errno or code,
            "message": message or "解压失败",
            "raw": body,
        }

    if percent >= 100:
        return {
            "percent": 100.0,
            "status": "completed",
            "errno": None,
            "message": "",
            "raw": body,
        }

    return {
        "percent": percent,
        "status": "processing",
        "errno": None,
        "message": "",
        "raw": body,
    }


def wait_extract(
    client: Client,
    extract_id: str | int,
    *,
    timeout_s: float = 300.0,
    interval_s: float = 1.0,
) -> dict:
    """轮询直到解压完成或失败/超时。"""
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = query_extract_progress(client, extract_id)
        status = last.get("status")
        if status == "completed":
            return last
        if status == "failed":
            return last
        time.sleep(interval_s)
    last = last or {}
    last["status"] = "timeout"
    last.setdefault("message", "解压超时")
    return last


def build_paths_string(path_parts: list) -> str:
    """将 extract_info.paths 拼成 paths 参数。"""
    names: list[str] = []
    for p in path_parts or []:
        if isinstance(p, dict):
            n = str(p.get("file_name") or p.get("n") or "").strip()
        else:
            n = str(p).strip()
        if n:
            names.append(n)
    if not names:
        return "文件"
    return "/".join(names)


def _as_int(v: Any, default: int = 0) -> int:
    try:
        if v is None or v == "":
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default
