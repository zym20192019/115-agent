from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from agent_115.api import files as file_api
from .auth import auth_store, require_user

router = APIRouter(prefix="/api/files")


def client_for(request: Request):
    _, token = require_user(request)
    cookie = auth_store.get_cookie(token)
    if not cookie:
        raise HTTPException(409, "请先在设置中配置 115 Cookie")
    from agent_115.client import Client
    return Client(cookie)


@router.get("")
def list_files(request: Request, cid: str = Query("0"), offset: int = 0, limit: int = 200):
    client = client_for(request)
    entries = file_api.list_files(client, cid, offset=offset, limit=limit)
    return {"cid": cid, "items": [e.__dict__ for e in entries]}


@router.get("/info")
def file_info(request: Request, id: str):
    client = client_for(request)
    return file_api.get_file_info(client, id)


@router.get("/search")
def search_files(request: Request, q: str, limit: int = 50):
    client = client_for(request)
    entries = file_api.search_files_global(client, q, limit=limit)
    return {"items": [e.__dict__ for e in entries]}


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
    return file_api.rename_entry(client_for(request), body.id, body.name)
