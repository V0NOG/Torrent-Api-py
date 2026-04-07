"""
music_utils.py — shared config and utility functions for the music request routers.

All directory paths are driven by env vars so they can be pointed at the existing
music/ worker directory without code changes.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from helper.track_identity import canonicalize, clean_youtube_title

# ─────────────────────────────────────────────────────────────────────────────
# Config — env vars with the same names the music worker already uses
# ─────────────────────────────────────────────────────────────────────────────

_BASE_DIR_DEFAULT = "/home/von/music-requests"
BASE_DIR = Path(os.getenv("MUSICREQ_BASE_DIR", _BASE_DIR_DEFAULT))

INBOX_DIR   = Path(os.getenv("MUSICREQ_INBOX",      str(BASE_DIR / "inbox")))
STATUS_DIR  = Path(os.getenv("MUSICREQ_STATUS_DIR", str(BASE_DIR / "status")))
DONE_DIR    = BASE_DIR / "done"
FAILED_DIR  = BASE_DIR / "failed"
RETRY_DIR   = BASE_DIR / "retry"
UPLOADS_DIR = BASE_DIR / "uploads"

MASTER_DB        = BASE_DIR / "master_index.sqlite"
EXPECTED_BEETS_DIR = os.getenv("MUSICREQ_BEETS_DIR", "/mnt/media/Music/Library")
WORKER_SERVICE   = "music-requests-worker.service"

YTDLP_BIN = os.getenv("MUSICREQ_YTDLP", "/usr/local/bin/yt-dlp")
DENO_BIN  = os.getenv("MUSICREQ_DENO",  "/home/von/.deno/bin/deno")

SEARCH_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
SEARCH_CACHE_TTL_SEC = float(os.getenv("MUSICREQ_SEARCH_CACHE_TTL", "45"))

# ─────────────────────────────────────────────────────────────────────────────
# Small utilities
# ─────────────────────────────────────────────────────────────────────────────

def safe_playlist_name(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"[^a-zA-Z0-9 _\-]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:80] or "Uploaded CSV Playlist"


def safe_filename(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace(" ", "_")
    return s[:80] if s else "request"


def extract_youtube_id(url: str) -> str:
    m = re.search(r"[?&]v=([A-Za-z0-9_-]{6,})", url)
    if m:
        return m.group(1)
    m = re.search(r"youtu\.be/([A-Za-z0-9_-]{6,})", url)
    if m:
        return m.group(1)
    return ""


def ensure_dirs() -> None:
    for d in (INBOX_DIR, DONE_DIR, FAILED_DIR, RETRY_DIR, STATUS_DIR, UPLOADS_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Duplicate detection
# ─────────────────────────────────────────────────────────────────────────────

def duplicate_lookup(artist: str = "", title: str = "", raw: str = "") -> dict:
    import sqlite3

    ident = canonicalize(artist=artist, title=title, raw=raw)

    if not MASTER_DB.exists():
        return {"status": "no_match", "identity": ident}

    try:
        conn = sqlite3.connect(str(MASTER_DB), timeout=5)
        try:
            row = conn.execute(
                "SELECT path, canonical_artist, canonical_title, canonical_version "
                "FROM tracks WHERE canonical_track_key=?",
                (ident["canonical_track_key"],)
            ).fetchone()
            if row:
                return {
                    "status": "exact_duplicate",
                    "identity": ident,
                    "matched_path": row[0],
                    "matched_artist": row[1],
                    "matched_title": row[2],
                    "matched_version": row[3],
                    "reason": "Same canonical artist/title/version",
                }

            row = conn.execute(
                "SELECT path, canonical_artist, canonical_title, canonical_version "
                "FROM tracks WHERE canonical_artist=? AND canonical_title=? LIMIT 1",
                (ident["canonical_artist"], ident["canonical_title"])
            ).fetchone()
            if row:
                return {
                    "status": "possible_duplicate",
                    "identity": ident,
                    "matched_path": row[0],
                    "matched_artist": row[1],
                    "matched_title": row[2],
                    "matched_version": row[3],
                    "reason": "Same base song, different version",
                }

            if ident["canonical_title"]:
                row = conn.execute(
                    "SELECT path, canonical_artist, canonical_title, canonical_version "
                    "FROM tracks WHERE canonical_title=? LIMIT 1",
                    (ident["canonical_title"],)
                ).fetchone()
                if row:
                    stored_artist = (row[1] or "").lower()
                    search_artist = ident["canonical_artist"].lower()

                    def norm(s: str) -> str:
                        return re.sub(r"[^a-z0-9]", "", s)

                    stored_n = norm(stored_artist)
                    search_n = norm(search_artist)
                    stored_first = norm(stored_artist.split()[0]) if stored_artist.split() else ""
                    search_first = norm(search_artist.split()[0]) if search_artist.split() else ""
                    artist_overlap = (
                        not search_n
                        or not stored_n
                        or search_n in stored_n
                        or stored_n in search_n
                        or (stored_first and search_first and (
                            stored_first in search_first or search_first in stored_first
                        ))
                    )
                    if artist_overlap:
                        return {
                            "status": "exact_duplicate",
                            "identity": ident,
                            "matched_path": row[0],
                            "matched_artist": row[1],
                            "matched_title": row[2],
                            "matched_version": row[3],
                            "reason": "Title match with same artist",
                        }
        finally:
            conn.close()
    except Exception:
        pass

    return {"status": "no_match", "identity": ident}


# ─────────────────────────────────────────────────────────────────────────────
# yt-dlp search / resolve
# ─────────────────────────────────────────────────────────────────────────────

def run_ytdlp_search(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return []

    now = time.time()
    cached = SEARCH_CACHE.get(q)
    if cached:
        ts, results = cached
        if (now - ts) <= SEARCH_CACHE_TTL_SEC:
            return results[:limit]

    cmd = [
        YTDLP_BIN,
        "--js-runtimes", f"deno:{DENO_BIN}",
        "--dump-single-json",
        "--flat-playlist",
        "--no-playlist",
        f"ytsearch{limit}:{q} audio",
    ]

    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Search timed out")

    if p.returncode != 0 or not p.stdout.strip():
        err = (p.stderr or "").strip()
        raise HTTPException(status_code=502, detail=f"yt-dlp search failed: {err[:240]}")

    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="yt-dlp returned invalid JSON")

    entries = data.get("entries") or []
    results: List[Dict[str, Any]] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        vid = e.get("id") or ""
        if not vid:
            continue
        raw_title = (e.get("title") or "").strip()
        results.append({
            "title": raw_title,
            "clean_title": clean_youtube_title(raw_title),
            "uploader": e.get("uploader") or e.get("channel") or e.get("uploader_id") or "",
            "duration": e.get("duration"),
            "url": f"https://www.youtube.com/watch?v={vid}",
        })
        if len(results) >= limit:
            break

    SEARCH_CACHE[q] = (now, results)
    return results


def resolve_youtube_url(url: str) -> Dict[str, Any]:
    u = (url or "").strip()
    if not u.startswith("http"):
        raise HTTPException(status_code=400, detail="url is required")

    cmd = [
        YTDLP_BIN,
        "--js-runtimes", f"deno:{DENO_BIN}",
        "--dump-single-json",
        "--no-playlist",
        u,
    ]

    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=25, check=False)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Metadata lookup timed out")

    if p.returncode != 0 or not (p.stdout or "").strip():
        err = (p.stderr or "").strip()
        raise HTTPException(status_code=502, detail=f"yt-dlp metadata lookup failed: {err[:240]}")

    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="yt-dlp returned invalid JSON")

    raw_title = (data.get("title") or "").strip()
    uploader = (
        data.get("uploader") or data.get("channel") or data.get("uploader_id") or ""
    ).strip()

    return {
        "title": raw_title,
        "clean_title": clean_youtube_title(raw_title),
        "uploader": uploader,
        "url": u,
        "youtube_id": extract_youtube_id(u),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Worker control
# ─────────────────────────────────────────────────────────────────────────────

def kick_worker_now() -> None:
    try:
        subprocess.run(
            ["sudo", "-n", "/bin/systemctl", "start", WORKER_SERVICE],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        pass


def read_worker_state() -> dict:
    lock_path = "/tmp/music-requests-worker.lock"
    p = subprocess.run(
        ["/usr/bin/flock", "-n", lock_path, "true"],
        capture_output=True, text=True, check=False,
    )
    return {"running": p.returncode != 0}


# ─────────────────────────────────────────────────────────────────────────────
# Status file helpers
# ─────────────────────────────────────────────────────────────────────────────

def read_status_json(request_id: str) -> dict:
    fp = STATUS_DIR / f"{request_id}.json"
    if not fp.exists():
        return {}
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_initial_status(
    request_id: str,
    url: str,
    title: str = "",
    nav_user: str = "",
) -> None:
    ensure_dirs()
    fp = STATUS_DIR / f"{request_id}.json"
    yt_id = extract_youtube_id(url)

    base = {
        "request_id": request_id,
        "status": "QUEUED",
        "message": "",
        "title": title or "",
        "requested_name": title or "",
        "url": url,
        "youtube_id": yt_id,
        "imported_paths": [],
        "nav_user": nav_user or "",
        "updated_at": datetime.now().timestamp(),
    }

    if fp.exists():
        try:
            existing = json.loads(fp.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                existing.setdefault("url", url)
                existing.setdefault("youtube_id", yt_id)
                existing.setdefault("request_id", request_id)
                existing.setdefault("updated_at", base["updated_at"])
                if title:
                    existing.setdefault("requested_name", title)
                    if not (existing.get("title") or "").strip():
                        existing["title"] = title
                if nav_user:
                    existing["nav_user"] = nav_user
                fp.write_text(json.dumps(existing, indent=2), encoding="utf-8")
                return
        except Exception:
            pass

    fp.write_text(json.dumps(base, indent=2), encoding="utf-8")


def write_request_file(
    url: str,
    filename: str = "",
    custom_title: str = "",
    nav_user: str = "",
    user_folder: str = "",
) -> Tuple[str, Path]:
    ensure_dirs()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    yid = extract_youtube_id(url)
    base = f"{ts}_{safe_filename(yid or url)}"
    request_id = base

    path = INBOX_DIR / f"{base}.txt"
    lines = [url.strip()]
    if filename.strip():
        lines.append(f"FILENAME:{filename.strip()}")
    if custom_title.strip():
        lines.append(f"TITLE:{custom_title.strip()}")
    if nav_user.strip():
        lines.append(f"NAV_USER:{nav_user.strip()}")
    if user_folder.strip():
        lines.append(f"USER_FOLDER:{user_folder.strip()}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_initial_status(request_id, url, title=custom_title or filename, nav_user=nav_user)

    try:
        fp = STATUS_DIR / f"{request_id}.json"
        if fp.exists():
            st = json.loads(fp.read_text(encoding="utf-8"))
            if isinstance(st, dict):
                st["custom_title"] = custom_title.strip()
                st["nav_user"] = nav_user.strip()
                st["user_folder"] = user_folder.strip()
                st["updated_at"] = datetime.now().timestamp()
                fp.write_text(json.dumps(st, indent=2), encoding="utf-8")
    except Exception:
        pass

    return request_id, path


# ─────────────────────────────────────────────────────────────────────────────
# Queue / history helpers
# ─────────────────────────────────────────────────────────────────────────────

def read_request_url(request_id: str) -> str:
    candidates = [
        INBOX_DIR  / f"{request_id}.txt",
        DONE_DIR   / f"{request_id}.txt",
        FAILED_DIR / f"{request_id}.txt",
        RETRY_DIR  / f"{request_id}.txt",
    ]
    for fp in candidates:
        try:
            if fp.exists():
                return (fp.read_text(encoding="utf-8").splitlines()[:1] or [""])[0].strip()
        except Exception:
            continue
    return ""


def read_current_processing() -> dict:
    fp = BASE_DIR / "current.json"
    if not fp.exists():
        return {}
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return {}


def derive_import_path(status_json: dict) -> Optional[str]:
    paths = status_json.get("imported_paths")
    if isinstance(paths, list) and paths:
        p0 = paths[0]
        if isinstance(p0, str) and p0:
            return p0
    return None


def to_item(request_id: str, status: str, mtime: Optional[float], st_json: dict) -> dict:
    import_path = derive_import_path(st_json) if st_json else None
    title = (st_json.get("title") or "").strip() if st_json else ""
    if not title and import_path:
        title = Path(import_path).name
        title = re.sub(r"\.mp3$", "", title, flags=re.IGNORECASE)
        title = re.sub(r"^\d+\s+", "", title).strip()

    url = read_request_url(request_id)
    updated_at = None
    if st_json and st_json.get("updated_at") is not None:
        try:
            updated_at = float(st_json["updated_at"])
        except Exception:
            pass
    if updated_at is None:
        updated_at = mtime

    return {
        "request_id": request_id,
        "title": title or "",
        "url": url or "",
        "status": status,
        "updated_at": updated_at,
        "import_path": import_path or "",
        "message": (st_json.get("message") or "") if st_json else "",
        "nav_user": (st_json.get("nav_user") or "") if st_json else "",
        "type": "MUSIC",  # distinguishes from TORRENT items in the unified queue
    }
