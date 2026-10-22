from agent_115.api import files as file_api
from agent_115.exceptions import NetworkError
from agent_115.models import FileEntry
from agent_115.ops import unzip as unzip_ops


class Item:
    def __init__(self, name, category=1):
        self.file_name = name
        self.file_category = category


def _patch_extract(monkeypatch, items):
    api = unzip_ops.extract_api
    monkeypatch.setattr(api, "get_extract_info", lambda client, pick, **kw: {"list": items, "paths": []})
    monkeypatch.setattr(api, "add_extract_file", lambda client, pick, **kw: {"extract_id": "e1"})
    monkeypatch.setattr(api, "wait_extract", lambda client, eid, **kw: {"status": "completed"})
    monkeypatch.setattr(api, "build_paths_string", lambda parts: "")


def test_direct_mode_snapshot_failure_never_deletes(monkeypatch):
    """快照失败时不能拿空快照做差集，否则父目录全部条目会被当成解压结果删除"""
    _patch_extract(monkeypatch, [Item("a.mkv")])
    deleted = []
    monkeypatch.setattr(file_api, "delete_entries", lambda client, ids, parent_cid="": deleted.append(ids))

    def snapshot(client, cid):
        raise NetworkError("boom")

    monkeypatch.setattr(unzip_ops, "snapshot_dir", snapshot)
    result = unzip_ops.unzip_one(
        None, pick_code="p", file_id="z", file_name="a.zip", parent_cid="0",
        archive_size=10 ** 9, mode="direct", skip_pre_extract=True, delete_zip=True,
    )
    assert result.status == "success"
    assert result.zip_deleted is False
    assert deleted == []


def test_direct_mode_without_new_entries_keeps_existing(monkeypatch):
    _patch_extract(monkeypatch, [Item("a.mkv")])
    deleted = []
    monkeypatch.setattr(file_api, "delete_entries", lambda client, ids, parent_cid="": deleted.append(ids))
    existing = {"a.mkv": FileEntry(id="old", name="a.mkv", is_dir=False, cid="0", size=1)}
    monkeypatch.setattr(unzip_ops, "snapshot_dir", lambda client, cid: dict(existing))
    result = unzip_ops.unzip_one(
        None, pick_code="p", file_id="z", file_name="a.zip", parent_cid="0",
        archive_size=10 ** 9, mode="direct", skip_pre_extract=True,
    )
    assert result.status == "success"
    assert deleted == []


def test_direct_mode_incomplete_deletes_only_new_entries(monkeypatch):
    _patch_extract(monkeypatch, [Item("a.mkv")])
    deleted = []
    monkeypatch.setattr(file_api, "delete_entries", lambda client, ids, parent_cid="": deleted.append(list(ids)))
    snapshots = iter([
        {"keep.txt": FileEntry(id="k", name="keep.txt", is_dir=False, cid="0", size=5)},
        {
            "keep.txt": FileEntry(id="k", name="keep.txt", is_dir=False, cid="0", size=5),
            "a.mkv": FileEntry(id="new", name="a.mkv", is_dir=False, cid="0", size=10),
        },
    ])
    monkeypatch.setattr(unzip_ops, "snapshot_dir", lambda client, cid: next(snapshots))
    result = unzip_ops.unzip_one(
        None, pick_code="p", file_id="z", file_name="a.zip", parent_cid="0",
        archive_size=1000, mode="direct", skip_pre_extract=True,
    )
    assert result.status == "incomplete"
    assert deleted == [["new"]]


def test_is_archive_name():
    assert unzip_ops.is_archive_name("a.ZIP")
    assert unzip_ops.is_archive_name("b.part1.rar")
    assert not unzip_ops.is_archive_name("movie.mkv")
