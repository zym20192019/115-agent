import json

from agent_115.api.directory import _collect_download_urls, _decode_tree_text


def test_collect_download_urls_walks_nested_mediahub_response():
    payload = {
        "state": True,
        "data": {
            "url": "https://download.example/a.txt",
            "result": [{"download_url": "https://download.example/b.txt"}],
        },
    }
    assert _collect_download_urls(payload) == [
        "https://download.example/a.txt",
        "https://download.example/b.txt",
    ]


def test_decode_tree_text_supports_utf16le():
    raw = "根目录\\n视频.mp4".encode("utf-16le")
    assert _decode_tree_text(raw) == "根目录\\n视频.mp4"
