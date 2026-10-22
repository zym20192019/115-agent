from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from agent_115.api import files as file_api
from agent_115.client import Client
from agent_115.ops import unzip as unzip_ops
from .auth import auth_store, require_user

router = APIRouter(prefix="/api/files")


def client_for(request: Request) -> Client:
    _, token = require_user(request)
    cookie = auth_store.get_cookie(token)
    if not cookie:
        raise HTTPException(409, "服务器未配置 115 Cookie（.env 中的 PAN115_COOKIE）")
    return Client(cookie)


def entry_payload(entry) -> dict:
    data = asdict(entry)
    data["is_archive"] = not entry.is_dir and unzip_ops.is_archive_name(entry.name)
    return data


@router.get("")
def list_files(request: Request, cid: str = Query("0"), offset: int = 0, limit: int = 200, refresh: bool = False):
    client = client_for(request)
    limit = max(1, min(limit, 1000))
    entries = file_api.list_files(client, cid, offset=offset, limit=limit, refresh=refresh)
    return {
        "cid": cid,
        "offset": offset,
        "limit": limit,
        "has_more": len(entries) >= limit,
        "items": [entry_payload(e) for e in entries],
    }


@router.get("/info")
def file_info(request: Request, id: str):
    info = file_api.get_file_info(client_for(request), id)
    info.pop("raw", None)
    return info


@router.get("/search")
def search_files(request: Request, q: str, cid: str = "0", limit: int = 50):
    result = file_api.search_files_global(client_for(request), q, cid=cid, limit=max(1, min(limit, 200)))
    return {"count": result["count"], "items": [entry_payload(e) for e in result["entries"]]}


class FolderBody(BaseModel):
    name: str
    parent_cid: str = "0"


@router.post("/folder")
def create_folder(body: FolderBody, request: Request):
    return file_api.create_folder(client_for(request), body.name, body.parent_cid)


class RenameBody(BaseModel):
    id: str
    name: str


@router.post("/rename")
def rename_file(body: RenameBody, request: Request):
    return file_api.rename_entry(client_for(request), body.id, body.name.strip())
