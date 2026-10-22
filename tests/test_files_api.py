import pytest

from agent_115.api import files as file_api
from agent_115.api.directory import _decode_tree_text
from agent_115.exceptions import ValidationError
from agent_115.models import FileEntry


def test_parse_entry_file_uses_fid_and_parent_cid():
    entry = file_api._parse_entry({"fid": "11", "cid": "5", "n": "a.zip", "s": 100, "pc": "pick"})
    assert entry.id == "11"
    assert entry.cid == "5"
    assert entry.is_dir is False
    assert entry.pick_code == "pick"


def test_parse_entry_dir_uses_cid_and_pid():
    # 目录条目没有 fid，cid 是目录自身 ID，pid 才是父目录
    entry = file_api._parse_entry({"cid": "7", "pid": "5", "n": "folder"})
    assert entry.id == "7"
    assert entry.cid == "5"
    assert entry.is_dir is True


def _fake_listing(monkeypatch, entries):
    monkeypatch.setattr(file_api, "list_all_files", lambda client, cid, **kw: [e for e in entries if e.cid == cid])


def test_find_entry_matches_exact_name_only(monkeypatch):
    _fake_listing(monkeypatch, [
        FileEntry(id="1", name="电影 2.zip", is_dir=False, cid="0"),
        FileEntry(id="2", name="电影.zip", is_dir=False, cid="0"),
    ])
    assert file_api.find_entry(None, "0", "电影.zip").id == "2"
    assert file_api.find_entry(None, "0", "电影") is None


def test_resolve_path_walks_directories(monkeypatch):
    _fake_listing(monkeypatch, [
        FileEntry(id="10", name="视频", is_dir=True, cid="0"),
        FileEntry(id="11", name="视频备份", is_dir=True, cid="0"),
        FileEntry(id="20", name="a.zip", is_dir=False, cid="10"),
    ])
    parent, entry = file_api.resolve_path(None, "/视频/a.zip")
    assert (parent, entry.id) == ("10", "20")
    with pytest.raises(ValidationError):
        file_api.resolve_path(None, "/视频备份/a.zip")


def test_split_path():
    assert file_api.split_path("/a/b/c.zip") == ("a/b", "c.zip")
    assert file_api.split_path("c.zip") == ("", "c.zip")


def test_decode_tree_text_utf16le_without_bom_ascii_only():
    raw = "root\n|-- movie.mp4\n".encode("utf-16le")
    assert _decode_tree_text(raw) == "root\n|-- movie.mp4\n"


def test_decode_tree_text_utf8():
    assert _decode_tree_text("根目录\n视频.mp4".encode("utf-8")) == "根目录\n视频.mp4"
