"""
music/requests_router.py

POST /api/v1/music/request            — submit a single track request
POST /api/v1/music/requests/csv       — upload a CSV playlist for bulk requests
POST /api/v1/music/queue/cancel       — cancel a queued (not yet processing) request
POST /api/v1/music/retry              — move a RETRY_LATER request back to inbox

All endpoints require a valid Jellyfin JWT.
User identity (for per-user folders and nav_user labelling) comes from the JWT
claims["sub"] (Jellyfin username).

Navidrome credentials are pulled from navidrome_store if available — used only
for writing the .nav.json sidecar that lets the worker create playlists.
Missing Navidrome link does NOT block any request.
"""

import asyncio
import csv
import io
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from auth.navidrome_store import get_credentials
from helper import user_fs
from helper.music_utils import (
    FAILED_DIR,
    INBOX_DIR,
    RETRY_DIR,
    STATUS_DIR,
    UPLOADS_DIR,
    duplicate_lookup,
    ensure_dirs,
    kick_worker_now,
    read_current_processing,
    read_status_json,
    run_ytdlp_search,
    safe_playlist_name,
    write_initial_status,
    write_request_file,
)
from helper.track_identity import clean_youtube_title
from routers.v1.auth_router import require_auth

logger = logging.getLogger("music.requests_router")
router = APIRouter()

_STOP_WORDS = {"the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "&"}

def _music_match_confidence(artist: str, title: str, yt_title: str) -> bool:
    """Return True if yt_title is a confident match for requested artist + title."""
    yt_low = yt_title.lower()
    art_low = artist.strip().lower()
    # Artist name (or last word of artist) must appear in result title
    if art_low:
        if art_low not in yt_low and art_low.split()[-1] not in yt_low:
            return False
    # >50% word overlap on title (ignoring stop words)
    title_words = {w for w in re.sub(r"[^\w\s]", "", title.lower()).split()
                   if w not in _STOP_WORDS and len(w) > 1}
    yt_words    = set(re.sub(r"[^\w\s]", "", yt_low).split())
    if not title_words:
        return True
    overlap = len(title_words & yt_words) / len(title_words)
    return overlap > 0.5


def _parse_csv_tracks(content: bytes) -> List[tuple]:
    """Parse CSV bytes → list of (artist, title) tuples."""
    text = content.decode("utf-8", errors="replace")
    tracks: List[tuple] = []
    try:
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            artist = (row.get("artist") or row.get("Artist") or row.get("ARTIST") or "").strip()
            title  = (row.get("title")  or row.get("Title")  or row.get("TITLE")  or
                      row.get("track")  or row.get("Track")  or row.get("song")   or "").strip()
            if title:
                tracks.append((artist, title))
    except Exception:
        pass
    return tracks


@router.post("/request")
async def request_track(req: Request):
    """
    Submit a single track for download.
    Body: { "url": str, "custom_title": str, "filename": str, "force": bool }

    Deduplication is performed before queueing. Pass force=true to override.
    """
    claims = require_auth(req)
    jellyfin_id = claims.get("jellyfin_id") or ""
    username = claims.get("sub") or ""

    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    body = body or {}
    url = (body.get("url") or "").strip()
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="url is required")

    custom_title = (body.get("custom_title") or "").strip()
    filename     = (body.get("filename") or "").strip()[:120]
    force        = bool(body.get("force", False))

    clean_ct = clean_youtube_title(custom_title) if custom_title else ""
    clean_fn = clean_youtube_title(filename) if filename else ""
    raw_identity = clean_ct or clean_fn

    dup = duplicate_lookup(raw=raw_identity)

    if dup["status"] == "exact_duplicate" and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "message":          "Track already exists",
                "duplicate_status": dup["status"],
                "reason":           dup.get("reason", ""),
                "matched_path":     dup.get("matched_path", ""),
                "canonical":        dup.get("identity", {}),
            },
        )

    if dup["status"] == "possible_duplicate" and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "message":          "Possible duplicate already exists",
                "duplicate_status": dup["status"],
                "reason":           dup.get("reason", ""),
                "matched_path":     dup.get("matched_path", ""),
                "canonical":        dup.get("identity", {}),
            },
        )

    # Ensure per-user folder exists
    user_folder_path = ""
    if username:
        try:
            user_folder_path = str(user_fs.ensure_user_folder(username))
        except Exception:
            pass

    request_id, _ = write_request_file(
        url,
        filename=filename,
        custom_title=clean_ct or custom_title,
        nav_user=username,
        user_folder=user_folder_path,
    )
    kick_worker_now()
    logger.info("Track requested: %s by %s (id=%s)", url[:80], username, request_id)
    return JSONResponse({"request_id": request_id})


@router.post("/requests/csv")
async def upload_csv_playlist(
    req: Request,
    file: UploadFile = File(...),
    playlist_name: str = Form("Uploaded CSV Playlist"),
    make_navidrome_playlist: Optional[str] = Form(None),
):
    """
    Upload a CSV file (artist + title columns) for bulk track requests.
    If the user has linked Navidrome credentials, a playlist will be created
    in Navidrome on completion. Missing Navidrome link is silently skipped.
    """
    claims = require_auth(req)
    jellyfin_id = claims.get("jellyfin_id") or ""
    username    = claims.get("sub") or ""

    ensure_dirs()

    if not file.filename:
        raise HTTPException(status_code=400, detail="file is required")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")

    # Navidrome creds for playlist creation — optional, no block if missing
    nav_user = ""
    nav_pass = ""
    if jellyfin_id:
        creds = get_credentials(jellyfin_id)
        if creds:
            nav_user, nav_pass = creds

    rid = f"csv_{int(time.time())}"
    csv_path = UPLOADS_DIR / f"{rid}.csv"

    pl_name = safe_playlist_name(playlist_name)

    # ── Confidence pre-filter ─────────────────────────────────────────────────
    skipped: List[Dict[str, Any]] = []
    tracks = _parse_csv_tracks(content)
    if len(tracks) > 0:
        async def _search_top(artist: str, title: str) -> Optional[str]:
            q = f"{artist} {title} explicit" if artist else f"{title} explicit"
            try:
                results = await asyncio.wait_for(
                    asyncio.to_thread(run_ytdlp_search, q, 1),
                    timeout=8.0,
                )
                return results[0].get("title") or "" if results else ""
            except Exception:
                return None  # fail-open on timeout/error

        top_titles = await asyncio.gather(*[_search_top(a, t) for a, t in tracks])

        confident: List[tuple] = []
        for (artist, title), yt_title in zip(tracks, top_titles):
            if yt_title is None or _music_match_confidence(artist, title, yt_title):
                confident.append((artist, title))
            else:
                skipped.append({"artist": artist, "title": title,
                                 "reason": f"Low confidence match: {yt_title[:60]}"})
                logger.info("CSV pre-filter skip: '%s - %s' (top result: '%s')",
                            artist, title, yt_title[:60])

        if skipped:
            # Rewrite the CSV with only confident rows
            out = io.StringIO()
            w = csv.writer(out)
            w.writerow(["artist", "title"])
            for a, t in confident:
                w.writerow([a, t])
            content = out.getvalue().encode("utf-8")
    # ─────────────────────────────────────────────────────────────────────────

    csv_path.write_bytes(content)

    user_folder_path = ""
    if username:
        try:
            user_folder_path = str(user_fs.ensure_user_folder(username))
        except Exception:
            pass

    req_line = f"CSV:{csv_path}:{pl_name}\n"
    req_file = INBOX_DIR / f"{rid}.txt"
    req_file.write_text(req_line, encoding="utf-8")

    # Write sidecar only when we have Navidrome creds (playlist creation)
    if nav_user and nav_pass:
        want_nd = (make_navidrome_playlist or "").strip() in ("1", "true", "on", "yes", "")
        if want_nd:
            cred_fp = UPLOADS_DIR / f"{rid}.nav.json"
            try:
                cred_fp.write_text(
                    json.dumps({
                        "nav_user":    nav_user,
                        "nav_pass":    nav_pass,
                        "user_folder": user_folder_path,
                    }, indent=2),
                    encoding="utf-8",
                )
                os.chmod(str(cred_fp), 0o600)
            except Exception:
                pass

    write_initial_status(rid, req_line.strip(), nav_user=username)
    kick_worker_now()

    logger.info("CSV uploaded: %s (%d bytes) by %s (id=%s), skipped=%d",
                pl_name, len(content), username, rid, len(skipped))
    return JSONResponse({
        "ok":            True,
        "request_id":    rid,
        "playlist_name": pl_name,
        "nav_user":      username,
        "queued_count":  len(tracks) - len(skipped),
        "skipped":       skipped,
    })


@router.post("/queue/cancel")
async def cancel_from_queue(req: Request):
    """
    Cancel a queued (not currently processing) request.
    Body: { "request_id": str }
    """
    require_auth(req)

    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    rid = (body or {}).get("request_id", "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="request_id is required")

    cur = read_current_processing()
    if (cur.get("request_id") or "").strip() == rid:
        raise HTTPException(status_code=409, detail="cannot cancel: currently processing")

    src = INBOX_DIR / f"{rid}.txt"
    if not src.exists():
        raise HTTPException(status_code=404, detail="request not found in inbox")

    FAILED_DIR.mkdir(parents=True, exist_ok=True)
    dst = FAILED_DIR / f"{rid}.txt"
    src.replace(dst)

    st_json = read_status_json(rid) or {}
    st_json.update({
        "request_id": rid,
        "status":     "FAILED",
        "message":    "Cancelled by user.",
        "updated_at": datetime.now().timestamp(),
    })
    try:
        (STATUS_DIR / f"{rid}.json").write_text(json.dumps(st_json, indent=2), encoding="utf-8")
    except Exception:
        pass

    return JSONResponse({"ok": True, "request_id": rid})


@router.post("/retry")
async def retry_request(req: Request):
    """
    Move a RETRY_LATER request back to the inbox for re-processing.
    Body: { "request_id": str }
    """
    require_auth(req)

    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    rid = (body or {}).get("request_id", "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="request_id is required")

    src = RETRY_DIR / f"{rid}.txt"
    if not src.exists():
        raise HTTPException(status_code=404, detail="request not in retry queue")

    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    dst = INBOX_DIR / f"{rid}.txt"
    src.replace(dst)

    kick_worker_now()
    return JSONResponse({"ok": True, "request_id": rid})
