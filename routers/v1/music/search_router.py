"""
music/search_router.py

GET  /api/v1/music/search?q=...&limit=10  — YouTube search via yt-dlp
POST /api/v1/music/resolve-url            — resolve a YouTube URL to metadata
POST /api/v1/music/duplicate-check        — check master index for duplicates

All endpoints require a valid Jellyfin JWT (Authorization: Bearer <token>).
"""

import logging
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from routers.v1.auth_router import require_auth
from helper.music_utils import (
    run_ytdlp_search,
    resolve_youtube_url,
    duplicate_lookup,
)
from helper.track_identity import clean_youtube_title

logger = logging.getLogger("music.search_router")
router = APIRouter()


@router.get("/search")
def music_search(req: Request, q: str = "", limit: int = 10):
    """
    Search YouTube for music via yt-dlp and annotate each result with a
    duplicate-status check against the master index.
    """
    require_auth(req)

    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="q is required")
    if limit < 1 or limit > 25:
        limit = 10

    results = run_ytdlp_search(q, limit=limit)

    enriched = []
    for r in results:
        uploader  = (r.get("uploader") or "").strip()
        raw_title = (r.get("title") or "").strip()
        clean_t   = (r.get("clean_title") or clean_youtube_title(raw_title)).strip()
        dup = duplicate_lookup(
            artist=uploader,
            title=clean_t,
            raw=f"{uploader} - {clean_t}",
        )
        enriched.append({
            **r,
            "duplicate_status": dup.get("status", "no_match"),
            "duplicate_reason": dup.get("reason", ""),
            "matched_path":     dup.get("matched_path", ""),
            "canonical":        dup.get("identity", {}),
        })

    return JSONResponse({"results": enriched})


@router.post("/resolve-url")
async def music_resolve_url(req: Request):
    """Resolve a YouTube URL to title, uploader, and youtube_id metadata."""
    require_auth(req)

    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    url = (body or {}).get("url", "").strip()
    return JSONResponse(resolve_youtube_url(url))


@router.post("/duplicate-check")
async def music_duplicate_check(req: Request):
    """
    Check whether an artist/title combination already exists in the master index.
    Body: { "artist": str, "title": str, "raw": str }  (all optional)
    """
    require_auth(req)

    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    body = body or {}
    artist = (body.get("artist") or "").strip()
    title  = clean_youtube_title((body.get("title") or "").strip())
    raw    = (body.get("raw") or "").strip()

    return JSONResponse(duplicate_lookup(artist=artist, title=title, raw=raw))
