"""
music/queue_router.py

GET /api/v1/music/queue    — full queue state (processing, waiting, history)
GET /api/v1/music/recent   — last 25 status entries regardless of state
GET /api/v1/music/seen     — set of YouTube IDs already successfully imported
GET /api/v1/music/status   — status for a single request_id
GET /api/v1/music/logs     — last 250 lines of the worker service journal
GET /api/v1/music/health   — disk usage + worker state

All endpoints require a valid Jellyfin JWT.
"""

import json
import logging
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from helper.music_utils import (
    BASE_DIR,
    DONE_DIR,
    EXPECTED_BEETS_DIR,
    FAILED_DIR,
    INBOX_DIR,
    RETRY_DIR,
    STATUS_DIR,
    WORKER_SERVICE,
    derive_import_path,
    ensure_dirs,
    extract_youtube_id,
    read_current_processing,
    read_request_url,
    read_status_json,
    read_worker_state,
    to_item,
)
from routers.v1.auth_router import require_auth, require_admin

logger = logging.getLogger("music.queue_router")
router = APIRouter()


@router.get("/queue")
def music_queue(req: Request):
    """
    Return the full music request queue:
    - currently processing item
    - queued items (waiting in inbox)
    - recent history (last 50 done/failed/retry_later entries)
    """
    require_auth(req)
    ensure_dirs()

    worker = read_worker_state()
    cur = read_current_processing()
    cur_rid = (cur.get("request_id") or "").strip()
    cur_started_at = cur.get("started_at")

    # Load optional priority override
    _priority_file = INBOX_DIR / "priority.json"
    _priority: List[str] = []
    try:
        if _priority_file.exists():
            _priority = json.loads(_priority_file.read_text(encoding="utf-8"))
    except Exception:
        _priority = []

    def _inbox_sort_key(fp: Path):
        stem = fp.stem
        try:
            idx = _priority.index(stem)
            return (0, idx, stem)
        except ValueError:
            return (1, 0, stem)

    queued_items: List[dict] = []
    for fp in sorted(INBOX_DIR.glob("*.txt"), key=_inbox_sort_key):
        rid = fp.stem
        if cur_rid and rid == cur_rid:
            continue
        st_json = read_status_json(rid)
        queued_items.append(to_item(rid, "QUEUED", fp.stat().st_mtime, st_json))

    processing_item = None
    processing_stale = False
    if cur_rid:
        st_json = read_status_json(cur_rid)
        if not worker.get("running"):
            processing_stale = True
        inbox_fp = INBOX_DIR / f"{cur_rid}.txt"
        mtime = inbox_fp.stat().st_mtime if inbox_fp.exists() else None
        processing_item = to_item(cur_rid, "PROCESSING", mtime, st_json)

    history: List[dict] = []
    for fp in sorted(STATUS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
        try:
            st = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        rid = (st.get("request_id") or fp.stem).strip()
        st_status = (st.get("status") or "UNKNOWN").strip()
        if st_status not in ("DONE", "FAILED", "RETRY_LATER"):
            continue
        history.append(to_item(rid, st_status, fp.stat().st_mtime, st))

    return JSONResponse({
        "worker":               worker,
        "processing":           processing_item,
        "processing_stale":     processing_stale,
        "processing_started_at": cur_started_at,
        "queued":               queued_items,
        "history":              history,
    })


@router.get("/recent")
def music_recent(req: Request):
    """Last 25 status entries sorted by most recently updated."""
    require_auth(req)
    ensure_dirs()

    items = []
    for fp in sorted(STATUS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:25]:
        try:
            st = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue

        rid = st.get("request_id") or fp.stem
        imported_paths = st.get("imported_paths") or []
        import_path = (
            imported_paths[0]
            if isinstance(imported_paths, list) and imported_paths
            else None
        )

        title = (st.get("title") or "").strip()
        if not title and import_path:
            title = Path(import_path).name
            title = re.sub(r"\.mp3$", "", title, flags=re.IGNORECASE)
        if title:
            title = re.sub(r"^\d+\s+", "", title).strip()

        items.append({
            "request_id":   rid,
            "status":       st.get("status") or "",
            "message":      st.get("message") or "",
            "import_path":  import_path or "",
            "title":        title or "",
            "updated_at":   st.get("updated_at"),
            "nav_user":     st.get("nav_user") or "",
            "type":         "MUSIC",
        })

    return JSONResponse({"items": items})


@router.get("/seen")
def music_seen(req: Request):
    """Return the set of YouTube IDs that have been successfully imported."""
    require_auth(req)
    ensure_dirs()

    seen_ids = set()
    for fp in STATUS_DIR.glob("*.json"):
        try:
            st = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (st.get("status") or "").upper() != "DONE":
            continue
        imported_paths = st.get("imported_paths") or []
        import_path = (
            imported_paths[0]
            if isinstance(imported_paths, list) and imported_paths
            else ""
        )
        if not (import_path and Path(import_path).exists()):
            continue

        rid = (st.get("request_id") or fp.stem).strip()
        yt_id = (st.get("youtube_id") or "").strip()
        if not yt_id:
            url = (st.get("url") or "").strip()
            yt_id = extract_youtube_id(url) if url else ""
        if not yt_id and rid:
            req_url = read_request_url(rid)
            yt_id = extract_youtube_id(req_url) if req_url else ""
        if not yt_id and rid:
            m = re.search(r"_([A-Za-z0-9_-]{6,})$", rid)
            if m:
                yt_id = m.group(1)
        if yt_id:
            seen_ids.add(yt_id)

    return JSONResponse({"seen": sorted(seen_ids)})


@router.get("/status")
def music_status(req: Request, request_id: str = ""):
    """Return status for a single request_id."""
    require_auth(req)

    request_id = request_id.strip()
    if not request_id:
        raise HTTPException(status_code=400, detail="request_id is required")

    inbox  = INBOX_DIR  / f"{request_id}.txt"
    done   = DONE_DIR   / f"{request_id}.txt"
    failed = FAILED_DIR / f"{request_id}.txt"
    retry  = RETRY_DIR  / f"{request_id}.txt"

    worker   = read_worker_state()
    st_json  = read_status_json(request_id)
    import_path = derive_import_path(st_json)
    nav_ready = bool(import_path and Path(import_path).exists())

    def base(status: str, path: Optional[Path] = None) -> dict:
        return {
            "status":         status,
            "path":           str(path) if path else None,
            "mtime":          (path.stat().st_mtime if path and path.exists() else None),
            "worker":         worker,
            "import_path":    import_path,
            "navidrome_ready": nav_ready,
            "worker_status":  st_json.get("status"),
            "worker_message": st_json.get("message"),
            "title":          st_json.get("title") or "",
        }

    if done.exists():
        return JSONResponse(base("DONE", done))
    if failed.exists():
        return JSONResponse(base("FAILED", failed))
    if retry.exists():
        return JSONResponse(base("RETRY_LATER", retry))
    if inbox.exists():
        s = (st_json.get("status") or "") == "PROCESSING"
        return JSONResponse(base("PROCESSING" if s else "QUEUED", inbox))
    if st_json:
        st = st_json.get("status") or "UNKNOWN"
        if st in ("RETRY_LATER", "DONE", "FAILED"):
            return JSONResponse(base(st, None))

    return JSONResponse(base("UNKNOWN", None))


@router.get("/logs", response_class=PlainTextResponse)
def music_logs(req: Request):
    """
    Return the last 250 lines of the music worker systemd journal.
    Admin-only.
    """
    require_admin(req)

    p = subprocess.run(
        ["journalctl", "-u", WORKER_SERVICE, "-n", "250", "--no-pager"],
        capture_output=True, text=True, check=False,
    )
    return ((p.stdout or "").strip() or "(no logs)") + "\n"


@router.post("/queue/reorder")
async def music_reorder(req: Request):
    """
    Reorder the music inbox queue by updating a priority.json file.
    Body: { "request_id": str, "before_id": str | null }
    Moves request_id to just before before_id, or to the end if before_id is null.
    """
    require_auth(req)
    ensure_dirs()

    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    request_id = (body or {}).get("request_id", "").strip()
    before_id = ((body or {}).get("before_id") or "").strip()

    if not request_id:
        raise HTTPException(status_code=400, detail="request_id is required")

    if not (INBOX_DIR / f"{request_id}.txt").exists():
        raise HTTPException(status_code=404, detail="request not found in inbox")

    # Build current order from inbox, respecting any existing priority
    _priority_file = INBOX_DIR / "priority.json"
    try:
        existing = json.loads(_priority_file.read_text(encoding="utf-8")) if _priority_file.exists() else []
    except Exception:
        existing = []

    inbox_stems = [fp.stem for fp in sorted(INBOX_DIR.glob("*.txt"), key=lambda p: p.name)]

    # Merge: prioritised items first, then natural order for the rest
    merged: List[str] = [r for r in existing if r in inbox_stems]
    for stem in inbox_stems:
        if stem not in merged:
            merged.append(stem)

    # Remove the item being moved, then re-insert at the target position
    merged = [r for r in merged if r != request_id]
    if before_id and before_id in merged:
        merged.insert(merged.index(before_id), request_id)
    else:
        merged.append(request_id)

    _priority_file.write_text(json.dumps(merged), encoding="utf-8")
    return JSONResponse({"ok": True})


@router.get("/health")
def music_health(req: Request):
    """Worker state + disk usage for the music library."""
    require_auth(req)

    worker = read_worker_state()
    ensure_dirs()

    inbox_count = len(list(INBOX_DIR.glob("*.txt"))) if INBOX_DIR.exists() else 0
    retry_count = len(list(RETRY_DIR.glob("*.txt"))) if RETRY_DIR.exists() else 0
    busy        = (BASE_DIR / "current.json").exists()

    target = EXPECTED_BEETS_DIR if Path(EXPECTED_BEETS_DIR).exists() else "/"
    du = shutil.disk_usage(target)
    used_pct = round((du.used / du.total) * 100, 2) if du.total else 0.0

    return JSONResponse({
        "ok":           True,
        "busy":         busy,
        "inbox_count":  inbox_count,
        "retry_count":  retry_count,
        "worker":       worker,
        "disk": {
            "path":      target,
            "total_gb":  round(du.total / 1e9, 1),
            "used_gb":   round(du.used  / 1e9, 1),
            "free_gb":   round(du.free  / 1e9, 1),
            "used_pct":  used_pct,
        },
    })
