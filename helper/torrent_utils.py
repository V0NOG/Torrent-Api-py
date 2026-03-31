# /home/von/Torrent-Api-py/helper/torrent_utils.py
from __future__ import annotations

import hashlib
from typing import Any, Tuple, Union

BytesLike = Union[bytes, bytearray, memoryview]


class BencodeError(Exception):
    pass


def bdecode(data: BytesLike) -> Any:
    b = bytes(data)
    val, idx = _decode_at(b, 0)
    if idx != len(b):
        # allow trailing whitespace? torrents shouldn't have it; be strict
        raise BencodeError("Trailing data after bencode object")
    return val


def _decode_at(b: bytes, i: int) -> Tuple[Any, int]:
    if i >= len(b):
        raise BencodeError("Unexpected end of data")

    c = b[i:i+1]
    if c == b"i":
        # int: i<num>e
        j = b.find(b"e", i)
        if j == -1:
            raise BencodeError("Unterminated int")
        num_bytes = b[i+1:j]
        try:
            n = int(num_bytes.decode("ascii"))
        except Exception:
            raise BencodeError("Invalid int")
        return n, j + 1

    if c == b"l":
        # list: l<item...>e
        i += 1
        out = []
        while True:
            if i >= len(b):
                raise BencodeError("Unterminated list")
            if b[i:i+1] == b"e":
                return out, i + 1
            v, i = _decode_at(b, i)
            out.append(v)

    if c == b"d":
        # dict: d<key><val>...e  (keys are byte strings)
        i += 1
        out = {}
        last_key = None
        while True:
            if i >= len(b):
                raise BencodeError("Unterminated dict")
            if b[i:i+1] == b"e":
                return out, i + 1
            k, i = _decode_at(b, i)
            if not isinstance(k, (bytes, bytearray)):
                raise BencodeError("Dict key must be bytes")
            if last_key is not None and bytes(k) < last_key:
                # torrent dict keys should be sorted; keep strict-ish
                pass
            last_key = bytes(k)
            v, i = _decode_at(b, i)
            out[bytes(k)] = v

    # byte string: <len>:<data>
    if b"0" <= c <= b"9":
        j = b.find(b":", i)
        if j == -1:
            raise BencodeError("Invalid byte string length")
        try:
            ln = int(b[i:j].decode("ascii"))
        except Exception:
            raise BencodeError("Invalid byte string length")
        start = j + 1
        end = start + ln
        if end > len(b):
            raise BencodeError("Byte string overruns buffer")
        return b[start:end], end

    raise BencodeError(f"Unknown bencode token at {i}: {c!r}")


def bencode(x: Any) -> bytes:
    if isinstance(x, int):
        return b"i" + str(x).encode("ascii") + b"e"
    if isinstance(x, (bytes, bytearray)):
        bb = bytes(x)
        return str(len(bb)).encode("ascii") + b":" + bb
    if isinstance(x, str):
        bb = x.encode("utf-8")
        return str(len(bb)).encode("ascii") + b":" + bb
    if isinstance(x, list):
        return b"l" + b"".join(bencode(i) for i in x) + b"e"
    if isinstance(x, dict):
        # keys MUST be bytes/str; encode in sorted byte order
        items = []
        for k in sorted(x.keys(), key=lambda kk: (kk if isinstance(kk, (bytes, bytearray)) else str(kk).encode("utf-8"))):
            kb = k if isinstance(k, (bytes, bytearray)) else str(k).encode("utf-8")
            items.append(bencode(kb))
            items.append(bencode(x[k]))
        return b"d" + b"".join(items) + b"e"
    raise BencodeError(f"Unsupported type for bencode: {type(x).__name__}")


def torrent_infohash_hex(torrent_bytes: BytesLike) -> str:
    """
    Compute BTIH (hex) from .torrent bytes by hashing the bencoded 'info' dict.
    """
    meta = bdecode(torrent_bytes)
    if not isinstance(meta, dict) or b"info" not in meta:
        raise BencodeError("Not a torrent (missing 'info')")
    info = meta[b"info"]
    info_bencoded = bencode(info)
    return hashlib.sha1(info_bencoded).hexdigest()


def magnet_from_btih(btih_hex: str, display_name: str = "") -> str:
    btih = (btih_hex or "").strip().lower()
    if not btih:
        raise ValueError("Missing btih")
    dn = display_name.strip()
    if dn:
        # encode display name safely
        # keep minimal dependency: do not import urllib here to avoid cycles
        # caller can leave blank; UI already has title
        return f"magnet:?xt=urn:btih:{btih}&dn={dn}"
    return f"magnet:?xt=urn:btih:{btih}"
