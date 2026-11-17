"""115 秒传（initupload）与 App 下载直链。

用途：在不下载/不上传文件内容的前提下，以「新文件名」把同一份内容秒传一份，
从而实现 115 网页端不允许的「修改扩展名」（例如 xxx.mp4.iso → xxx.mp4）。

协议要点（公开逆向资料，115-Desktop / p115client 均采用同一协议；此处为独立实现）：
- POST https://uplb.115.com/4.0/initupload.php?k_ec=<token>
  body = AES-128-CBC(固定 key/iv)(urlencode(sorted(payload)))，响应同样 AES 加密且分块 LZ4 压缩。
- 文件 ≥ 1MB 时首次返回 status=7，并给出 sign_key 和 sign_check="start-end"（闭区间字节范围），
  需回传该范围内容的 SHA1（大写）作为 sign_val，再次请求；status=2 表示秒传成功。
- 读取字节范围使用 App 下载直链：POST https://proapi.115.com/app/chrome/downurl，
  请求/响应数据使用 115 的 m115 RSA 变体编码。
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import zlib
from typing import Callable, Optional
from urllib.parse import urlencode

import requests

from ..client import Client
from ..exceptions import APIError, ValidationError

UPLOAD_APP_VERSION = "36.2.28"
UPLOAD_API = "https://uplb.115.com/4.0/initupload.php"
UPLOAD_INFO_API = "https://webapi.115.com/files/uploadinfo"
APP_DOWNURL_API = "https://proapi.115.com/app/chrome/downurl"
DOWNLOAD_UA = "Mozilla/5.0 115disk/36.2.28"

_CRC_SALT = b"^j>WD3Kr?J2gLFjD4W2y@"
_MD5_SALT = b"Qclm8MGWUv59TnrR0XPg"
_EC_PUBKEY = bytes.fromhex("1d030e80a178dceececda377de128d8ed9ddcf55ae61ed46ea121a1cfc81")
_AES_KEY = bytes.fromhex("fb1a19d652f5aaf7bc651d0f69bf422f")
_AES_IV = bytes.fromhex("69bf422f49960550a0ad44ec3446cb4c")

_RSA_N = int(
    "8686980c0f5a24c4b9d43020cd2c22703ff3f450756529058b1cf88f09b8602136477198"
    "a6e2683149659bd122c33592fdb5ad47944ad1ea4d36c6b172aad6338c3bb6ac6227502d"
    "010993ac967d1aef00f0c8e038de2e4d3bc2ec368af2e9f10a6f1eda4f7262f136420c07"
    "c331b871bf139f74f3010e3c4fe57df3afb71683", 16)
_RSA_E = 65537
_RSA_KEY = b"\x8d\xa5\xa5\x8d"
_G_KEY_L = b"x\x06\xadL3\x86]\x18L\x01?F"
_G_KTS = bytes.fromhex(
    "f0e569aebfdcbf8a1a45e8be7da673b8de8fe7c445da86c49b648b146ab4f1aa3801359e"
    "26692c86006b4fa5363462a62a966818f24afdbd6b978f4d8f8913b76c8e93ed0e0d483e"
    "d72f88d8fefe7e8650954fd1eb832634db667b9c7e9d7a8132eab633de3aa95934663baa"
    "ba816048b9d5819cf86c8477ff5478265fbee81e369f34805c452c9b76d51b8fccc3b8f5")


# ── 底层编码 ────────────────────────────────────────────

def _aes_cipher():
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:  # pragma: no cover - 部署缺依赖时给出明确提示
        raise ValidationError("秒传需要 cryptography 库：pip install cryptography") from exc
    return Cipher(algorithms.AES(_AES_KEY), modes.CBC(_AES_IV))


def _aes_encrypt(data: bytes) -> bytes:
    pad = -len(data) & 15
    if pad:
        data += bytes((pad,)) * pad
    enc = _aes_cipher().encryptor()
    return enc.update(data) + enc.finalize()


def _aes_decrypt(data: bytes) -> bytes:
    data = data[: len(data) & -16]
    dec = _aes_cipher().decryptor()
    out = dec.update(data) + dec.finalize()
    pad = out[-1] if out else 0
    if 0 < pad < 16 and out[-pad:] == bytes((pad,)) * pad:
        out = out[:-pad]
    return out


def _lz4_block_decompress(src: bytes) -> bytes:
    dst = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        dst += src[i:i + lit]
        i += lit
        if i >= n:
            break
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        mlen = token & 15
        if mlen == 15:
            while True:
                b = src[i]
                i += 1
                mlen += b
                if b != 255:
                    break
        mlen += 4
        start = len(dst) - offset
        if offset <= 0 or start < 0:
            raise APIError("LZ4 数据损坏")
        for k in range(mlen):
            dst.append(dst[start + k])
    return bytes(dst)


def _lz4_frames_decompress(src: bytes) -> bytes:
    out = bytearray()
    view = memoryview(src)
    while len(view) > 2:
        size = view[0] | (view[1] << 8)
        if not size:
            break
        out += _lz4_block_decompress(bytes(view[2:2 + size]))
        view = view[2 + size:]
    return bytes(out)


def _decrypt_response(content: bytes) -> dict:
    if content[:1] + content[-1:] in (b"{}", b"[]"):
        return json.loads(content)
    return json.loads(_lz4_frames_decompress(_aes_decrypt(content)))


def _ec_token(ts: int) -> str:
    token = bytearray()
    token += _EC_PUBKEY[:15]
    token += b"\x00s\x00\x00\x00"
    token += ts.to_bytes(4, "little")
    token += _EC_PUBKEY[15:]
    token += b"\x00\x01\x00\x00\x00"
    token += zlib.crc32(_CRC_SALT + bytes(token)).to_bytes(4, "little")
    return base64.b64encode(bytes(token)).decode("ascii")


def _xor(src: bytes, key: bytes) -> bytearray:
    out = bytearray(len(src))
    head = len(src) & 3
    for k in range(head):
        out[k] = src[k] ^ key[k]
    klen = len(key)
    for k in range(head, len(src)):
        out[k] = src[k] ^ key[(k - head) % klen]
    return out


def _rsa_gen_key(rand_key: bytes, sk_len: int) -> bytearray:
    xor_key = bytearray(sk_len)
    length = sk_len * (sk_len - 1)
    index = 0
    for i in range(sk_len):
        x = (rand_key[i] + _G_KTS[index]) & 0xFF
        xor_key[i] = _G_KTS[length] ^ x
        length -= sk_len
        index += sk_len
    return xor_key


def _m115_encode(data: bytes) -> str:
    tmp = bytes(_xor(data, _RSA_KEY))[::-1]
    plain = b"\x00" * 16 + bytes(_xor(tmp, _G_KEY_L))
    out = bytearray()
    for i in range(0, len(plain), 117):
        chunk = plain[i:i + 117]
        padded = b"\x00" + b"\x02" * (128 - 2 - len(chunk)) + b"\x00" + chunk
        out += pow(int.from_bytes(padded, "big"), _RSA_E, _RSA_N).to_bytes(128, "big")
    return base64.b64encode(bytes(out)).decode("ascii")


def _m115_decode(text: str) -> bytes:
    raw = base64.b64decode(text)
    data = bytearray()
    for i in range(0, len(raw), 128):
        p = pow(int.from_bytes(raw[i:i + 128], "big"), _RSA_E, _RSA_N)
        b = p.to_bytes((p.bit_length() + 7) >> 3, "big")
        data += b[b.index(0) + 1:]
    key_l = _rsa_gen_key(bytes(data[:16]), 12)
    tmp = bytes(_xor(bytes(data[16:]), key_l))[::-1]
    return bytes(_xor(tmp, _RSA_KEY))


# ── 接口 ───────────────────────────────────────────────

def get_upload_info(client: Client) -> tuple[int, str]:
    """返回 (user_id, userkey)。"""
    body = client.request("GET", UPLOAD_INFO_API)
    uid = body.get("user_id")
    key = body.get("userkey")
    if not uid or not key:
        raise APIError("获取上传信息失败（user_id/userkey 为空）", response=body)
    return int(uid), str(key)


def upload_init(client: Client, *, user_id: int, user_key: str, filename: str, sha1: str,
                size: int, target_cid: str, sign_key: str = "", sign_val: str = "") -> dict:
    """调用一次 initupload，返回解密后的响应。"""
    ts = int(time.time())
    payload = {
        "appid": 0,
        "appversion": UPLOAD_APP_VERSION,
        "fileid": sha1.upper(),
        "filename": filename,
        "filesize": int(size),
        "sign_key": sign_key,
        "sign_val": sign_val,
        "target": f"U_1_{target_cid}",
        "topupload": "true",
        "userid": user_id,
        "userkey": user_key,
        "t": ts,
    }
    inner = hashlib.sha1(f"{user_id}{payload['fileid']}{payload['target']}0".encode("ascii")).hexdigest()
    payload["sig"] = hashlib.sha1((user_key + inner + "000000").encode("ascii")).hexdigest().upper()
    tok = hashlib.md5(_MD5_SALT)
    tok.update(f"{payload['fileid']}{size}{sign_key}{sign_val}{user_id}{ts}".encode("ascii"))
    tok.update(hashlib.md5(str(user_id).encode("ascii")).hexdigest().encode("ascii"))
    tok.update(UPLOAD_APP_VERSION.encode("ascii"))
    payload["token"] = tok.hexdigest()
    body = urlencode(sorted((k, v) for k, v in payload.items() if v)).encode("utf-8")
    ver = UPLOAD_APP_VERSION
    resp = client._send(
        "POST", UPLOAD_API,
        params={"k_ec": _ec_token(ts)},
        data=_aes_encrypt(body),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": f"Mozilla/5.0 115disk/{ver} 115Browser/{ver} 115wangpan_android/{ver}",
        },
        timeout=60,
    )
    return _decrypt_response(resp.content)


def get_app_download_url(client: Client, pick_code: str, user_id: int) -> str:
    """App(chrome) 下载直链，不受网页端大文件限制。返回的直链需用 DOWNLOAD_UA 访问。"""
    plain = json.dumps({"pickcode": pick_code, "user_id": user_id}, separators=(",", ":"))
    resp = client._send(
        "POST", APP_DOWNURL_API,
        params={"t": int(time.time())},
        data={"data": _m115_encode(plain.encode("utf-8"))},
        headers={"User-Agent": DOWNLOAD_UA},
        timeout=30,
    )
    body = _decrypt_response(resp.content)
    if not body.get("state"):
        raise APIError(body.get("msg") or body.get("error") or "获取下载直链失败", response=body)
    info = json.loads(_m115_decode(body["data"]))
    for item in (info.values() if isinstance(info, dict) else []):
        url = (item.get("url") or {}).get("url") if isinstance(item, dict) else None
        if url:
            return url
    raise APIError("下载直链为空", response=info)


def read_range_sha1(client: Client, pick_code: str, byte_range: str, *, user_id: int,
                    timeout: int = 60) -> str:
    """读取 'start-end'（闭区间）字节并返回大写 SHA1。"""
    start, end = (int(x) for x in byte_range.split("-"))
    url = get_app_download_url(client, pick_code, user_id)
    r = requests.get(url, headers={"User-Agent": DOWNLOAD_UA, "Range": f"bytes={start}-{end}"},
                     timeout=timeout)
    if r.status_code not in (200, 206):
        raise APIError(f"读取字节范围失败 HTTP {r.status_code}")
    content = r.content
    if r.status_code == 200:  # 服务端忽略 Range 时自行截取
        content = content[start:end + 1]
    if len(content) != end - start + 1:
        raise APIError(f"读取字节范围长度不符 {len(content)} != {end - start + 1}")
    return hashlib.sha1(content).hexdigest().upper()


def rapid_upload(client: Client, *, filename: str, sha1: str, size: int, target_cid: str,
                 pick_code: str, read_range: Optional[Callable[[str], str]] = None,
                 user: Optional[tuple[int, str]] = None) -> dict:
    """秒传一份同内容文件（新文件名）。成功返回响应（含 status=2），否则抛 APIError。"""
    uid, ukey = user or get_upload_info(client)
    resp = upload_init(client, user_id=uid, user_key=ukey, filename=filename, sha1=sha1,
                       size=size, target_cid=target_cid)
    if resp.get("status") == 7:
        rng = str(resp.get("sign_check") or "")
        sign_key = str(resp.get("sign_key") or "")
        if not rng or not sign_key:
            raise APIError("秒传二次校验参数缺失", response=resp)
        val = (read_range or (lambda r: read_range_sha1(client, pick_code, r, user_id=uid)))(rng)
        resp = upload_init(client, user_id=uid, user_key=ukey, filename=filename, sha1=sha1,
                           size=size, target_cid=target_cid, sign_key=sign_key, sign_val=val)
    if resp.get("status") != 2:
        raise APIError(f"秒传失败 status={resp.get('status')} {resp.get('statusmsg') or resp.get('msg') or ''}",
                       response=resp)
    return resp
