"""
navidrome_store.py — per-user Navidrome credential storage.

Credentials are persisted to disk at:
  <USER_DATA_DIR>/<jellyfin_id>/navidrome.json

The file contains: { "nav_user": str, "nav_pass": str, "linked_at": float }

Passwords are stored in plaintext — same security posture as the existing
music app (which kept them in memory). This is acceptable for a private
homelab not exposed to the internet.

Validation uses the Navidrome Subsonic ping endpoint before writing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import Optional, Tuple

import httpx

NAVIDROME_URL: str = (os.getenv("NAVIDROME_URL", "") or "").strip()

# Default: app/user_data/ (sibling of the auth/ package)
_DEFAULT_USER_DATA = Path(__file__).parent.parent / "user_data"
USER_DATA_DIR = Path(os.getenv("APP_USER_DATA_DIR", str(_DEFAULT_USER_DATA)))

# Only allow jellyfin IDs that look like UUIDs or hex strings — no path traversal
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9\-]{8,64}$")


def _safe_id(jellyfin_id: str) -> str:
    """Validate jellyfin_id is safe to use as a directory name."""
    jid = (jellyfin_id or "").strip()
    if not _SAFE_ID_RE.match(jid):
        raise ValueError(f"Invalid jellyfin_id: '{jid}'")
    return jid


def _cred_path(jellyfin_id: str) -> Path:
    return USER_DATA_DIR / _safe_id(jellyfin_id) / "navidrome.json"


def _subsonic_token(password: str, salt: str) -> str:
    return hashlib.md5((password + salt).encode()).hexdigest()


async def _ping(nav_user: str, nav_pass: str) -> bool:
    """
    Validate credentials against Navidrome using the Subsonic ping endpoint.
    Returns True if credentials are valid. If NAVIDROME_URL is not configured,
    skips validation and returns True (trust caller).
    """
    url = NAVIDROME_URL.rstrip("/")
    if not url:
        return True

    salt = secrets.token_hex(8)
    token = _subsonic_token(nav_pass, salt)
    uenc = urllib.parse.quote(nav_user)
    endpoint = (
        f"{url}/rest/ping.view"
        f"?u={uenc}&t={token}&s={salt}&v=1.16.1&c=homelab&f=json"
    )

    try:
        async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
            resp = await client.get(endpoint)
        data = resp.json()
        sr = data.get("subsonic-response", {})
        return sr.get("status") == "ok"
    except Exception:
        return False


async def link(jellyfin_id: str, nav_user: str, nav_pass: str) -> bool:
    """
    Validate nav_user/nav_pass against Navidrome, then persist to disk.
    Returns True on success, False if credentials are invalid.
    Raises ValueError if jellyfin_id is malformed.
    """
    nav_user = (nav_user or "").strip()
    nav_pass = (nav_pass or "").strip()
    if not nav_user or not nav_pass:
        return False

    if not await _ping(nav_user, nav_pass):
        return False

    path = _cred_path(jellyfin_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "nav_user": nav_user,
            "nav_pass": nav_pass,
            "linked_at": time.time(),
        }),
        encoding="utf-8",
    )
    return True


def get_credentials(jellyfin_id: str) -> Optional[Tuple[str, str]]:
    """
    Return (nav_user, nav_pass) for this user, or None if not linked.
    """
    try:
        path = _cred_path(jellyfin_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        nav_user = (data.get("nav_user") or "").strip()
        nav_pass = (data.get("nav_pass") or "").strip()
        if not nav_user or not nav_pass:
            return None
        return nav_user, nav_pass
    except Exception:
        return None


def is_linked(jellyfin_id: str) -> bool:
    """Return True if this user has stored Navidrome credentials."""
    try:
        return _cred_path(jellyfin_id).exists()
    except Exception:
        return False


def unlink(jellyfin_id: str) -> None:
    """Remove stored Navidrome credentials for this user."""
    try:
        path = _cred_path(jellyfin_id)
        if path.exists():
            path.unlink()
    except Exception:
        pass
