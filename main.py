import os
import re
import unicodedata
import time
import json
import hmac
import hashlib
import secrets
import asyncio
import subprocess
from math import ceil
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import urllib.parse
import shutil
import logging
import uvicorn
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from routers.v1.search_router import router as search_router
from routers.v1.trending_router import router as trending_router
from routers.v1.catergory_router import router as category_router
from routers.v1.recent_router import router as recent_router
from routers.v1.combo_routers import router as combo_router
from routers.v1.sites_list_router import router as site_list_router
from routers.home_router import router as home_router
from routers.v1.search_url_router import router as search_url_router

# ── NEW: auth + file manager routers ────────────────────────────────────────
from routers.v1.auth_router import router as auth_router, require_auth
from routers.v1.files_router import router as files_router
from auth.navidrome_store import USER_DATA_DIR
# ────────────────────────────────────────────────────────────────────────────

# ── Music routers ────────────────────────────────────────────────────────────
from routers.v1.music.search_router   import router as music_search_router
from routers.v1.music.requests_router import router as music_requests_router
from routers.v1.music.queue_router    import router as music_queue_router
# ─────────────────────────────────────────────────────────────────────────────

from helper.uptime import getUptime
from helper.dependencies import authenticate_request
from helper.torrent_utils import torrent_infohash_hex, magnet_from_btih

from mangum import Mangum

try:
    import httpx
except Exception:
    httpx = None

# ── NEW: logging setup ───────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("main")
# ────────────────────────────────────────────────────────────────────────────

startTime = time.time()

# ----------------------------
# Config (env-driven)
# ----------------------------
TORRENT_API_KEY = (os.getenv("TORRENT_API_KEY", "") or "").strip()

NAS_INGEST_URL = (os.getenv("NAS_INGEST_URL", "") or "").strip()
NAS_SHARED_SECRET = (os.getenv("NAS_SHARED_SECRET", "") or "").strip()
NAS_CALLBACK_SECRET = (os.getenv("NAS_CALLBACK_SECRET", "") or "").strip()

DOWNLOADS_PER_MINUTE = int(os.getenv("DOWNLOADS_PER_MINUTE", "8"))
MAX_ACTIVE_DOWNLOADS = int(os.getenv("MAX_ACTIVE_DOWNLOADS", "3"))
QUEUE_MAX_ITEMS = int(os.getenv("QUEUE_MAX_ITEMS", "200"))

_ALLOWED_VIDEO_EXT = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv"}
_ALLOWED_SUB_EXT = {".srt", ".ass", ".ssa", ".sub", ".vtt"}
_ALLOWED_AUDIO_EXT = {".mp3", ".flac", ".m4a", ".m4b", ".aac", ".ogg", ".opus", ".wav"}

_JUNK_EXT = {
    ".txt", ".url", ".nfo", ".jpg", ".jpeg", ".png", ".gif", ".exe", ".bat", ".cmd",
    ".sfv", ".md5", ".sha1", ".torrent"
}
_JUNK_NAME_HINTS = {"sample", "readme", "proof", "rarbg", "yts", "extratorrent"}

NAVIDROME_URL = (os.getenv("NAVIDROME_URL", "") or "").strip()
NAVIDROME_USER = (os.getenv("NAVIDROME_USER", "") or "").strip()
NAVIDROME_PASSWORD = (os.getenv("NAVIDROME_PASSWORD", "") or "").strip()
NAVIDROME_CLIENT = (os.getenv("NAVIDROME_CLIENT", "torrent-api-py") or "torrent-api-py").strip()
NAVIDROME_API_VER = (os.getenv("NAVIDROME_API_VER", "1.16.1") or "1.16.1").strip()

STAGING_BASE_DIR = Path(os.getenv("TORRENT_STAGING_DIR", "/mnt/media/Downloads/Torrents/_staging"))

FINAL_MOVIES_DIR = Path(os.getenv("FINAL_MOVIES_DIR", "/mnt/media/Movies"))
FINAL_TV_DIR = Path(os.getenv("FINAL_TV_DIR", "/mnt/media/TV"))
FINAL_MUSIC_DIR = Path(os.getenv("FINAL_MUSIC_DIR", "/mnt/media/Music"))
FINAL_OTHER_BASE = Path(os.getenv("FINAL_OTHER_BASE", "/mnt/media"))

MIN_FREE_GB = int(os.getenv("MIN_FREE_GB", "20"))

JELLYFIN_URL = (os.getenv("JELLYFIN_URL", "") or "").strip()
JELLYFIN_API_KEY = (os.getenv("JELLYFIN_API_KEY", "") or "").strip()
WEBHOOK_URL = (os.getenv("WEBHOOK_URL", "") or "").strip()

# TMDB_API_KEY — get from themoviedb.org, add to systemd service:
#   sudo systemctl edit torrent-api-py.service
#   Add: Environment="TMDB_API_KEY=8bf2895bab3cea75349692b62a59cdf8"
TMDB_API_KEY = (os.getenv("TMDB_API_KEY", "") or "").strip()
TMDB_LANGUAGE = (os.getenv("TMDB_LANGUAGE", "en-AU") or "en-AU").strip()
TMDB_REGION = (os.getenv("TMDB_REGION", "AU") or "AU").strip()
TMDB_BASE = "https://api.themoviedb.org/3"

YTDLP_COOKIES_FILE = (os.getenv("YTDLP_COOKIES_FILE", "") or "").strip()

ABB_COOKIE = (os.getenv("ABB_COOKIE", "") or "").strip()
ABB_USERNAME = (os.getenv("ABB_USERNAME", "") or "").strip()
ABB_PASSWORD = (os.getenv("ABB_PASSWORD", "") or "").strip()
ABB_USER_AGENT = (os.getenv("ABB_USER_AGENT", "Torrent-Api-Py/1.0 (+local)") or "").strip()

ABB_URL = (os.getenv("ABB_URL", "http://127.0.0.1:5078") or "").strip()
TRANSMISSION_WEB = (os.getenv("TRANSMISSION_WEB", "http://127.0.0.1:9091/transmission/web/") or "").strip()
TRANSMISSION_RPC = (os.getenv("TRANSMISSION_RPC", "http://127.0.0.1:9091/transmission/rpc") or "").strip()

BEETS_IMPORT_CMD = (os.getenv("BEETS_IMPORT_CMD", "") or "").strip()

# ── NEW: user media root ─────────────────────────────────────────────────────
USER_MEDIA_ROOT = Path(os.getenv("USER_MEDIA_ROOT", "/mnt/media/Users"))
# ────────────────────────────────────────────────────────────────────────────

# ── Music request worker (read by helper/music_utils.py via env vars) ────────
# These are declared here for documentation; the actual reads happen in
# helper/music_utils.py so the music routers can import them directly.
#   MUSICREQ_BASE_DIR      — base dir for the music worker (default /home/von/music-requests)
#   MUSICREQ_INBOX         — inbox dir (default <BASE_DIR>/inbox)
#   MUSICREQ_STATUS_DIR    — status dir (default <BASE_DIR>/status)
#   MUSICREQ_BEETS_DIR     — beets library dir for disk-usage reporting
#   MUSICREQ_YTDLP         — path to yt-dlp binary
#   MUSICREQ_DENO          — path to deno binary
#   MUSICREQ_SEARCH_CACHE_TTL — seconds to cache yt-dlp search results (default 45)
#   MUSICREQ_HOST_MUSIC_ROOT  — music library root for per-user folders (default /mnt/media/Music)
# ─────────────────────────────────────────────────────────────────────────────

_FORBIDDEN_CHARS_RE = re.compile(r'[\/\\:\*\?"<>\|\x00-\x1F\x7F]')

def _disk_free_gb(path: Path) -> int:
    try:
        usage = shutil.disk_usage(str(path))
        return int(usage.free / (1024**3))
    except Exception:
        return 0

def _guard_free_space(path: Path):
    free = _disk_free_gb(path)
    if free and free < MIN_FREE_GB:
        raise HTTPException(
            status_code=507,
            detail=f"Insufficient disk space: {free}GB free (min {MIN_FREE_GB}GB)."
        )

def _normalize_category(v: Optional[str]) -> str:
    s = (v or "all").strip().lower()
    if s in ("movie", "movies"):
        return "movies"
    if s in ("tv", "tvshows", "tv_shows", "tv shows", "shows"):
        return "tv"
    if s == "music":
        return "music"
    if s in ("other", "custom"):
        return "other"
    return "all"

def _final_base_for_category(category: str) -> Path:
    c = _normalize_category(category)
    if c == "movies":
        return FINAL_MOVIES_DIR
    if c == "tv":
        return FINAL_TV_DIR
    if c == "music":
        return FINAL_MUSIC_DIR
    if c == "other":
        return FINAL_OTHER_BASE
    return FINAL_MOVIES_DIR

def _safe_title(title: str, max_len: int = 120) -> str:
    t = (title or "").strip()
    if not t:
        return "untitled"
    t = unicodedata.normalize("NFKC", t)
    t = _FORBIDDEN_CHARS_RE.sub(" ", t)
    t = " ".join(t.split()).strip()
    if len(t) > max_len:
        t = t[:max_len].rstrip()
    t = t.rstrip(" .")
    return t or "untitled"

def _safe_filename(name: str, fallback_ext: str) -> str:
    n = (name or "").strip()
    if not n:
        return f"file{fallback_ext}"
    n = unicodedata.normalize("NFKC", n)
    n = _FORBIDDEN_CHARS_RE.sub(" ", n)
    n = " ".join(n.split()).strip().rstrip(" .")
    if not n:
        n = "file"
    if Path(n).suffix == "" and fallback_ext:
        n = n + fallback_ext
    return n

def _staging_dir_for(entry: Dict[str, Any]) -> Path:
    return STAGING_BASE_DIR / f"{entry['id']}_{_safe_title(entry.get('title') or 'untitled', 60)}"

def _guess_tv_show_and_season(title: str):
    t = (title or "").strip()
    m = re.search(r"(.*?)[\s\.\-_]+S(\d{1,2})E\d{1,2}\b", t, re.IGNORECASE)
    if m:
        show = m.group(1).replace(".", " ").replace("_", " ").replace("-", " ").strip()
        return show, int(m.group(2))
    m = re.search(r"(.*?)[\s\.\-_]+Season[\s\.\-_]*(\d{1,2})\b", t, re.IGNORECASE)
    if m:
        show = m.group(1).replace(".", " ").replace("_", " ").replace("-", " ").strip()
        return show, int(m.group(2))
    return None, None

def _extract_year(title: str) -> Optional[int]:
    t = (title or "")
    m = re.search(r"\b(19\d{2}|20\d{2})\b", t)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None

def _clean_title_for_search(title: str) -> str:
    t = (title or "").strip()
    t = re.sub(r"\b(19\d{2}|20\d{2})\b", "", t)
    t = re.sub(r"\b(1080p|720p|2160p|4k|hdr|dv|bluray|web[-\s]?dl|webrip|hdtv|x264|x265|hevc|aac|dts|atmos)\b", "", t, flags=re.I)
    t = t.replace(".", " ").replace("_", " ").replace("-", " ")
    t = " ".join(t.split()).strip()
    return t or (title or "").strip()

def _tmdb_movie_label(m: Dict[str, Any]) -> str:
    title = (m.get("title") or "").strip()
    y = (m.get("release_date") or "")[:4]
    return f"{title} ({y})".strip()

def _tmdb_tv_label(t: Dict[str, Any]) -> str:
    name = (t.get("name") or "").strip()
    y = (t.get("first_air_date") or "")[:4]
    return f"{name} ({y})".strip()

async def _tmdb_get(path: str, params: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    if not httpx:
        return {}, "httpx not installed"
    if not TMDB_API_KEY:
        return {}, "TMDB_API_KEY not set"
    try:
        p = dict(params or {})
        p["api_key"] = TMDB_API_KEY
        p["language"] = TMDB_LANGUAGE

        headers = {"User-Agent": "Torrent-Api-Py/1.0 (+local)"}
        async with httpx.AsyncClient(timeout=8.0, headers=headers) as client:
            r = await client.get(f"{TMDB_BASE}{path}", params=p)
            if r.status_code >= 400:
                txt = (r.text or "")[:200].replace("\n", " ")
                return {}, f"TMDb HTTP {r.status_code}: {txt}"
            js = r.json() if r.content else {}
            return (js or {}), f"TMDb OK ({path})"
    except Exception as e:
        return {}, f"TMDb error: {type(e).__name__}"

async def _tmdb_search_movie(query: str, year: Optional[int] = None) -> Tuple[List[Dict[str, Any]], str]:
    q = (query or "").strip()
    if not q:
        return [], "empty query"
    params: Dict[str, Any] = {"query": q, "region": TMDB_REGION, "include_adult": False}
    if year:
        params["year"] = year
    data, dbg = await _tmdb_get("/search/movie", params)
    results = (data.get("results") or [])[:8]
    if results:
        return results, dbg
    if year:
        data2, dbg2 = await _tmdb_get("/search/movie", {"query": q, "region": TMDB_REGION, "include_adult": False})
        return (data2.get("results") or [])[:8], f"{dbg} • fallback(no-year): {dbg2}"
    return [], dbg

async def _tmdb_search_tv(query: str) -> Tuple[List[Dict[str, Any]], str]:
    q = (query or "").strip()
    if not q:
        return [], "empty query"
    params: Dict[str, Any] = {"query": q, "include_adult": False}
    data, dbg = await _tmdb_get("/search/tv", params)
    return (data.get("results") or [])[:8], dbg

def _parse_season_episode_from_name(name: str):
    m = re.search(r"[Ss](\d{1,2})[Ee]\d{1,2}", name)
    if m:
        try:
            return int(m.group(1))
        except:
            return None
    m2 = re.search(r"[Ss]eason[\s\.\-_]*(\d{1,2})\b", name, re.IGNORECASE)
    if m2:
        try:
            return int(m2.group(1))
        except:
            return None
    return None

def _parse_season_from_relpath(rel: str) -> Optional[int]:
    r = (rel or "").replace("\\", "/")
    m = re.search(r"[Ss](\d{1,2})[Ee]\d{1,2}", r)
    if m:
        try: return int(m.group(1))
        except: return None
    m2 = re.search(r"[Ss]eason[\s\.\-_]*(\d{1,2})\b", r, re.IGNORECASE)
    if m2:
        try: return int(m2.group(1))
        except: return None
    m3 = re.search(r"(?:^|/)[Ss](\d{1,2})(?:/|$)", r)
    if m3:
        try: return int(m3.group(1))
        except: return None
    return None

def _parse_music_from_filename(fname: str):
    base = Path(fname).stem
    parts = [p.strip() for p in re.split(r"[-–—]", base) if p.strip()]
    if len(parts) >= 3:
        artist = parts[0]
        album = parts[1]
        title = " - ".join(parts[2:])
        return artist, album, title
    if len(parts) == 2:
        artist, title = parts
        return artist, None, title
    return None, None, None

def _is_media_file(p: Path) -> bool:
    ext = p.suffix.lower()
    return ext in _ALLOWED_VIDEO_EXT or ext in _ALLOWED_SUB_EXT or ext in _ALLOWED_AUDIO_EXT

def _is_junk_file(p: Path) -> bool:
    ext = p.suffix.lower()
    name = p.name.lower()
    if ext in _JUNK_EXT:
        return True
    if any(h in name for h in _JUNK_NAME_HINTS):
        return True
    return False

def _collect_files(staging_dir: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for p in staging_dir.rglob("*"):
        if not p.is_file():
            continue
        try:
            rel = str(p.relative_to(staging_dir))
        except Exception:
            continue

        ext = p.suffix.lower()
        size = 0
        try:
            size = p.stat().st_size
        except Exception:
            size = 0

        if _is_media_file(p):
            default_action = "keep"
        elif _is_junk_file(p):
            default_action = "delete"
        else:
            default_action = "delete"

        out.append({"rel": rel, "abs": str(p), "size": size, "ext": ext, "default_action": default_action})

    out.sort(key=lambda x: (x["default_action"] != "keep", -(x.get("size") or 0), x["rel"]))
    return out

def _largest_video_rel(files: List[Dict[str, Any]]) -> Optional[str]:
    vids = [f for f in files if (f.get("ext") or "").lower() in _ALLOWED_VIDEO_EXT]
    if not vids:
        return None
    vids.sort(key=lambda x: (x.get("size") or 0), reverse=True)
    return vids[0]["rel"]

def _looks_like_episode(rel_or_name: str) -> bool:
    s = (rel_or_name or "")
    return bool(re.search(r"[Ss]\d{1,2}[Ee]\d{1,2}", s)) or bool(re.search(r"\b(Episode|Ep)\s*\d+\b", s, re.I))

def _guess_category_from_files(raw_title: str, files: List[Dict[str, Any]]) -> str:
    video = [f for f in files if (f.get("ext") or "").lower() in _ALLOWED_VIDEO_EXT]
    audio = [f for f in files if (f.get("ext") or "").lower() in _ALLOWED_AUDIO_EXT]

    tv_hits = 0
    for f in video:
        rel = f.get("rel") or ""
        if _looks_like_episode(rel) or _parse_season_from_relpath(rel) is not None:
            tv_hits += 1
    if tv_hits >= 2:
        return "tv"

    if any((f.get("ext") or "").lower() == ".m4b" for f in audio):
        return "music"
    if len(audio) >= 2 and len(video) == 0:
        return "music"

    if len(video) >= 1:
        try:
            biggest = max([int(v.get("size") or 0) for v in video] or [0])
        except Exception:
            biggest = 0
        if biggest >= 700 * 1024 * 1024 and tv_hits == 0:
            return "movies"

    show_guess, _season_guess = _guess_tv_show_and_season(raw_title or "")
    if show_guess:
        return "tv"
    return "movies"

def _guess_music_kind(files: List[Dict[str, Any]]) -> str:
    audio = [f for f in files if (f.get("ext") or "").lower() in _ALLOWED_AUDIO_EXT]
    if any((f.get("ext") or "").lower() == ".m4b" for f in audio):
        return "audiobook"

    huge = 0
    for f in audio:
        try:
            if int(f.get("size") or 0) >= 250 * 1024 * 1024:
                huge += 1
        except Exception:
            pass
    if huge >= 1 and len(audio) <= 3:
        return "audiobook"

    hints = ("audiobook", "audible", "aax", "m4b")
    for f in audio:
        rel = (f.get("rel") or "").lower()
        if any(h in rel for h in hints):
            return "audiobook"

    return "music"

async def _post_webhook(event: str, payload: Dict[str, Any]):
    if not WEBHOOK_URL or not httpx:
        return
    try:
        body = {"event": event, **payload}
        async with httpx.AsyncClient(timeout=4.0) as client:
            await client.post(WEBHOOK_URL, json=body)
    except Exception:
        pass

async def _jellyfin_refresh():
    if not JELLYFIN_URL or not JELLYFIN_API_KEY or not httpx:
        return

    base = JELLYFIN_URL.rstrip("/")
    headers = {
        "X-Emby-Token": JELLYFIN_API_KEY,
        "X-MediaBrowser-Token": JELLYFIN_API_KEY,
    }

    await asyncio.sleep(4.0)

    endpoints = [f"{base}/Library/Refresh", f"{base}/library/refresh"]

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                ok = False
                for url in endpoints:
                    r = await client.post(url, headers=headers)
                    if r.status_code < 400:
                        ok = True
                        break
                if ok:
                    return
        except Exception:
            pass
        await asyncio.sleep(1.5 * (attempt + 1))

def _normalise_title(title: str) -> str:
    """Normalise a title for fuzzy library matching."""
    t = (title or "").lower().strip()
    t = re.sub(r"[^a-z0-9 ]", " ", t)      # convert dots/special chars to spaces first
    t = re.sub(r"^(the|a|an)\s+", "", t)   # now article strip works on space-separated words
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t


async def _refresh_jellyfin_library():
    """Fetch all Jellyfin movies/shows and rebuild the in-memory title cache."""
    global _JELLYFIN_LIBRARY, _JELLYFIN_LIBRARY_LAST_FETCH
    if not JELLYFIN_URL or not JELLYFIN_API_KEY or not httpx:
        return
    try:
        base = JELLYFIN_URL.rstrip("/")
        params = {
            "api_key": JELLYFIN_API_KEY,
            "Recursive": "true",
            "IncludeItemTypes": "Movie,Series,Episode,Season",
            "Fields": "Name,OriginalTitle,ProductionYear",
            "Limit": "5000",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{base}/Items", params=params)
            if r.status_code >= 400:
                logger.warning("Jellyfin library fetch failed: HTTP %s", r.status_code)
                return
            data = r.json()
        items = data.get("Items") or []
        new_set: set = set()
        for item in items:
            name = item.get("Name") or ""
            year = item.get("ProductionYear")
            orig = item.get("OriginalTitle") or ""
            if name:
                new_set.add(_normalise_title(name))
                if year:
                    new_set.add(_normalise_title(f"{name} {year}"))
            if orig and orig != name:
                new_set.add(_normalise_title(orig))
                if year:
                    new_set.add(_normalise_title(f"{orig} {year}"))
        async with _JELLYFIN_LIBRARY_LOCK:
            _JELLYFIN_LIBRARY = new_set
            _JELLYFIN_LIBRARY_LAST_FETCH = time.time()
        logger.info("Jellyfin library cache updated: %d titles", len(new_set))
    except Exception as e:
        logger.warning("_refresh_jellyfin_library failed: %s", e)


def _navidrome_auth_params() -> Optional[Dict[str, str]]:
    if not NAVIDROME_URL or not NAVIDROME_USER or not NAVIDROME_PASSWORD:
        return None
    return {
        "u": NAVIDROME_USER,
        "p": NAVIDROME_PASSWORD,
        "v": NAVIDROME_API_VER,
        "c": NAVIDROME_CLIENT,
        "f": "json",
    }

# ----------------------------
# In-memory queue + rate limit
# ----------------------------
QUEUE: Dict[str, Dict[str, Any]] = {}
QUEUE_ORDER: List[str] = []
RATE_BUCKET: Dict[str, List[float]] = {}
QUEUE_LOCK = asyncio.Lock()
RATE_LOCK = asyncio.Lock()

# ── Jellyfin library cache ────────────────────────────────────────────────────
_JELLYFIN_LIBRARY: set = set()          # normalised titles
_JELLYFIN_LIBRARY_LOCK = asyncio.Lock()
_JELLYFIN_LIBRARY_LAST_FETCH: float = 0.0
# ─────────────────────────────────────────────────────────────────────────────

QUEUE_PERSIST_FILE = Path(os.getenv("QUEUE_PERSIST_FILE", "/home/von/Torrent-Api-py/queue_state.json"))

def _queue_save_sync():
    try:
        tmp = QUEUE_PERSIST_FILE.with_suffix(".tmp")
        data = {"queue": QUEUE, "order": QUEUE_ORDER}
        tmp.write_text(json.dumps(data, default=str))
        tmp.replace(QUEUE_PERSIST_FILE)
    except Exception as e:
        logger.warning("Failed to persist queue: %s", e)

def _queue_load_sync():
    global QUEUE, QUEUE_ORDER
    try:
        if not QUEUE_PERSIST_FILE.exists():
            return
        data = json.loads(QUEUE_PERSIST_FILE.read_text())
        QUEUE = data.get("queue", {})
        QUEUE_ORDER = data.get("order", [])
        # Mark in-progress items as failed so user knows to re-queue
        for entry in QUEUE.values():
            if entry.get("status") in ("downloading", "queued", "processing"):
                entry["status"] = "failed"
                entry["error"] = "Service restarted — please re-queue"
        logger.info("Loaded %d queue entries from disk", len(QUEUE))
    except Exception as e:
        logger.warning("Failed to load queue from disk: %s", e)

_queue_load_sync()

BTIH_INDEX: Dict[str, str] = {}

def _extract_btih(magnet: str) -> Optional[str]:
    try:
        if not magnet.startswith("magnet:?"):
            return None
        q = parse_qs(urlparse(magnet).query)
        xts = q.get("xt") or []
        for xt in xts:
            if isinstance(xt, str) and xt.lower().startswith("urn:btih:"):
                return xt.split(":", 2)[-1].strip().lower()
    except Exception:
        return None
    return None

def _transmission_run(args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )

def _transmission_find_id_by_btih(btih: str) -> Optional[int]:
    if not btih:
        return None
    cp = _transmission_run(["transmission-remote", "localhost", "-t", "all", "-i"])
    out = cp.stdout or ""
    cur_id = None
    for line in out.splitlines():
        s = line.strip()
        if s.lower().startswith("id:"):
            try:
                cur_id = int(s.split(":", 1)[1].strip())
            except Exception:
                cur_id = None
        if s.lower().startswith("hash:"):
            cur_hash = s.split(":", 1)[1].strip().lower()
            if cur_id is not None and cur_hash == btih:
                return cur_id
    return None

def _parse_rate_kib(s: str) -> Optional[float]:
    try:
        s = (s or "").strip()
        m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([KMG]i?B)\/s", s, re.I)
        if not m:
            return None
        val = float(m.group(1))
        unit = m.group(2).lower()
        if unit.startswith("k"):
            return val
        if unit.startswith("m"):
            return val * 1024.0
        if unit.startswith("g"):
            return val * 1024.0 * 1024.0
        return val
    except Exception:
        return None

def _transmission_get_info(tid: int) -> Dict[str, Any]:
    cp = _transmission_run(["transmission-remote", "localhost", "-t", str(tid), "-i"])
    out = cp.stdout or ""

    pct = None
    state = None
    rate_dl = None
    rate_ul = None
    eta = None
    have_pct = None
    downloaded_bytes = None

    def _parse_bytes(txt: str) -> Optional[int]:
        t = (txt or "").strip().lower().replace(",", "")
        if t.endswith("bytes"):
            try: return int(float(t.split()[0]))
            except: return None
        m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([kmgt])i?b", t)
        if not m:
            return None
        val = float(m.group(1))
        unit = m.group(2)
        mult = {"k":1024, "m":1024**2, "g":1024**3, "t":1024**4}.get(unit, 1)
        return int(val * mult)

    for line in out.splitlines():
        s = line.strip()
        lo = s.lower()

        if lo.startswith("percent done:"):
            v = s.split(":", 1)[1].strip().replace("%", "")
            try:
                f = float(v)
                if f <= 1.0:
                    f = f * 100.0
                pct = max(0.0, min(100.0, f))
            except Exception:
                pct = None
        elif lo.startswith("have:"):
            m = re.search(r"have:\s*([0-9]+(?:\.[0-9]+)?)\s*%", lo)
            if m:
                try:
                    have_pct = float(m.group(1))
                except Exception:
                    have_pct = None
        elif lo.startswith("downloaded:"):
            rhs = s.split(":", 1)[1].strip()
            downloaded_bytes = _parse_bytes(rhs)
        elif lo.startswith("state:"):
            state = s.split(":", 1)[1].strip().lower()
        elif lo.startswith("rate download:"):
            rate_dl = _parse_rate_kib(s.split(":", 1)[1].strip())
        elif lo.startswith("rate upload:"):
            rate_ul = _parse_rate_kib(s.split(":", 1)[1].strip())
        elif lo.startswith("eta:"):
            eta = s.split(":", 1)[1].strip()

    return {
        "percent": pct,
        "have_pct": have_pct,
        "downloaded_bytes": downloaded_bytes,
        "state": state,
        "rate_download_kib": rate_dl,
        "rate_upload_kib": rate_ul,
        "eta": eta,
        "raw": out[:2000],
    }

def _now() -> float:
    return time.time()

def _hmac_sha256(secret: str, body_bytes: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    return f"sha256={mac}"

def _trim_rate_list(ts_list: List[float], window_sec: int = 60) -> List[float]:
    cutoff = _now() - window_sec
    return [t for t in ts_list if t >= cutoff]

async def _rate_limit_check(ip: str) -> Optional[str]:
    async with RATE_LOCK:
        ts_list = RATE_BUCKET.get(ip, [])
        ts_list = _trim_rate_list(ts_list, 60)
        if len(ts_list) >= DOWNLOADS_PER_MINUTE:
            retry_in = int(max(1, 60 - (_now() - min(ts_list))))
            RATE_BUCKET[ip] = ts_list
            return f"Rate limited: too many downloads. Try again in ~{retry_in}s."
        ts_list.append(_now())
        RATE_BUCKET[ip] = ts_list

    async with QUEUE_LOCK:
        active = 0
        for _id in QUEUE_ORDER:
            st = (QUEUE.get(_id) or {}).get("status")
            if st in ("queued", "downloading"):
                active += 1
        if active >= MAX_ACTIVE_DOWNLOADS:
            return f"Too many active downloads (max {MAX_ACTIVE_DOWNLOADS}). Try again shortly."

    return None

async def _navidrome_start_scan() -> bool:
    if not httpx:
        return False
    params = _navidrome_auth_params()
    if not params:
        return False

    try:
        base = NAVIDROME_URL.rstrip("/")
        url = f"{base}/rest/startScan.view"
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, params=params)
            return r.status_code < 400
    except Exception:
        return False


async def _navidrome_wait_for_scan(timeout: int = 60) -> bool:
    """Poll getScanStatus until scan is complete."""
    if not httpx:
        return False
    params = _navidrome_auth_params()
    if not params:
        return False
    base = NAVIDROME_URL.rstrip("/")
    for _ in range(timeout // 3):
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(f"{base}/rest/getScanStatus.view", params=params)
                if r.status_code < 400:
                    data = r.json()
                    scanning = data.get("subsonic-response", {}).get("scanStatus", {}).get("scanning", True)
                    if not scanning:
                        return True
        except Exception:
            pass
        await asyncio.sleep(3)
    return False

async def _navidrome_search_tracks(params: dict, folder_path: str) -> list:
    """Search Navidrome for tracks matching a folder path, return list of track IDs."""
    if not httpx:
        return []
    base = NAVIDROME_URL.rstrip("/")
    # Use the folder name as search query
    folder_name = folder_path.rstrip("/").split("/")[-1]
    try:
        search_params = {**params, "query": folder_name, "songCount": 100, "albumCount": 10, "artistCount": 0}
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{base}/rest/search3.view", params=search_params)
            if r.status_code >= 400:
                return []
            data = r.json()
            songs = data.get("subsonic-response", {}).get("searchResult3", {}).get("song", [])
            if isinstance(songs, dict):
                songs = [songs]
            # Filter to songs whose path contains our folder
            folder_lower = folder_name.lower()
            return [s["id"] for s in songs if folder_lower in (s.get("title","") + s.get("album","") + s.get("path","")).lower()]
    except Exception:
        return []

async def _navidrome_create_playlist(name: str, comment: str = "", dest_dir: str = "") -> bool:
    """Create a playlist in Navidrome and populate it with tracks from dest_dir."""
    params = _navidrome_auth_params()
    if not params:
        return False
    return await _navidrome_build_playlist(params, name, comment, dest_dir)

async def _navidrome_build_playlist(params: dict, name: str, comment: str = "", dest_dir: str = "") -> bool:
    """Core: create playlist, find tracks, add them."""
    if not httpx:
        return False
    base = NAVIDROME_URL.rstrip("/")
    try:
        # 1. Create empty playlist
        create_params = {**params, "name": name}
        if comment:
            create_params["comment"] = comment
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{base}/rest/createPlaylist.view", params=create_params)
            if r.status_code >= 400:
                return False
            data = r.json()
            pl_id = data.get("subsonic-response", {}).get("playlist", {}).get("id")

        if not pl_id or not dest_dir:
            return bool(pl_id)  # empty playlist is still a success

        # 2. Find tracks in that directory
        track_ids = await _navidrome_search_tracks(params, dest_dir)
        if not track_ids:
            return True  # playlist created, just empty — tracks may appear after next scan

        # 3. Add tracks to playlist
        song_params = {**params, "playlistId": pl_id}
        for tid in track_ids:
            song_params[f"songIdToAdd"] = tid
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Subsonic API: updatePlaylist with songIdToAdd (multiple values)
            import urllib.parse
            qs = urllib.parse.urlencode(list(song_params.items()) +
                 [("songIdToAdd", tid) for tid in track_ids[1:]])
            r = await client.get(f"{base}/rest/updatePlaylist.view?{qs}")
        return True
    except Exception:
        return False

async def _navidrome_user_auth_params(username: str, password: str) -> Optional[Dict[str, str]]:
    """Build Navidrome auth params for a specific user."""
    if not NAVIDROME_URL or not username or not password:
        return None
    return {
        "u": username,
        "p": password,
        "v": NAVIDROME_API_VER,
        "c": NAVIDROME_CLIENT,
        "f": "json",
    }

async def _navidrome_create_user_playlist(nd_username: str, nd_password: str, name: str, comment: str = "", dest_dir: str = "") -> bool:
    """Create a Navidrome playlist for a specific user using their own credentials."""
    params = await _navidrome_user_auth_params(nd_username, nd_password)
    if not params:
        return False
    return await _navidrome_build_playlist(params, name, comment, dest_dir)

async def _audiobook_exists_on_disk(author: str, book: str) -> Optional[str]:
    """Check if an audiobook already exists. Returns path string or None."""
    try:
        ab_root = Path(os.getenv("FINAL_MUSIC_DIR", "/mnt/media/Music")) / "Audiobooks"
        a_dir = _safe_title(author, 120) or "Unknown Author"
        # Search for any directory matching the book name under author
        author_path = ab_root / a_dir
        if not author_path.exists():
            return None
        b_safe = _safe_title(book, 120) or ""
        if not b_safe:
            return None
        for d in author_path.iterdir():
            if d.is_dir() and d.name.startswith(b_safe):
                return str(d)
    except Exception:
        pass
    return None


async def _navidrome_populate_playlist(playlist_name: str, author: str, book: str,
                                        nd_user: str = None, nd_pass: str = None) -> int:
    """
    After a scan, find tracks for this audiobook by album lookup (more reliable than
    search) and add them to the named playlist in filename order.
    Returns number of tracks added, or 0 on failure.
    """
    params = {"u": nd_user or NAVIDROME_USER, "p": nd_pass or NAVIDROME_PASSWORD,
              "v": NAVIDROME_API_VER, "c": NAVIDROME_CLIENT, "f": "json"}
    if not params["u"] or not params["p"]:
        return 0
    try:
        base = NAVIDROME_URL.rstrip("/")
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Find the playlist by name
            r = await client.get(f"{base}/rest/getPlaylists.view", params=params)
            playlists = (r.json().get("subsonic-response") or {}).get("playlists", {}).get("playlist", [])
            if isinstance(playlists, dict):
                playlists = [playlists]
            pl = next((p for p in playlists if p.get("name") == playlist_name), None)
            if not pl:
                return 0
            pl_id = pl["id"]

            # Strategy 1: find album matching book title, get ALL its tracks
            songs = []
            r2 = await client.get(f"{base}/rest/search3.view",
                                  params={**params, "query": book, "albumCount": 10,
                                          "songCount": 0, "artistCount": 0})
            albums = (r2.json().get("subsonic-response") or {}).get("searchResult3", {}).get("album", [])
            if isinstance(albums, dict):
                albums = [albums]

            for album in albums:
                # Match album to our book (loose match)
                if book.lower() in (album.get("name") or "").lower() or                    (album.get("name") or "").lower() in book.lower():
                    r3 = await client.get(f"{base}/rest/getAlbum.view",
                                         params={**params, "id": album["id"]})
                    album_songs = (r3.json().get("subsonic-response") or {}).get("album", {}).get("song", [])
                    if isinstance(album_songs, dict):
                        album_songs = [album_songs]
                    songs.extend(album_songs)

            # Strategy 2: fallback to song search if album lookup found nothing
            if not songs:
                r4 = await client.get(f"{base}/rest/search3.view",
                                      params={**params, "query": book, "songCount": 200,
                                              "songOffset": 0, "albumCount": 0, "artistCount": 0})
                songs = (r4.json().get("subsonic-response") or {}).get("searchResult3", {}).get("song", [])
                if isinstance(songs, dict):
                    songs = [songs]

            if not songs:
                return 0

            # Sort by filename (title field contains filename prefix like "05 - ...")
            # This is more reliable than track number which may not be set
            songs.sort(key=lambda s: s.get("title") or "")

            # Clear existing playlist entries first then re-add in order
            existing_r = await client.get(f"{base}/rest/getPlaylist.view",
                                          params={**params, "id": pl_id})
            existing = (existing_r.json().get("subsonic-response") or {}).get("playlist", {}).get("entry", [])
            if isinstance(existing, dict):
                existing = [existing]
            for _ in existing:
                await client.get(f"{base}/rest/updatePlaylist.view",
                                 params={**params, "playlistId": pl_id, "songIndexToRemove": 0})

            # Add all tracks in sorted order
            for s in songs:
                await client.get(f"{base}/rest/updatePlaylist.view",
                                 params={**params, "playlistId": pl_id, "songIdToAdd": s["id"]})

            return len(songs)
    except Exception:
        return 0

async def _queue_add(title: str, site: str, magnet: str, btih: Optional[str], requested_by: Optional[str] = None) -> Dict[str, Any]:
    async with QUEUE_LOCK:
        qid = secrets.token_hex(8)
        entry = {
            "id": qid,
            "site": site,
            "title": title,
            "magnet": magnet,
            "prepared_meta": None,
            "approved_meta": None,
            "btih": btih,
            "transmission_id": None,
            "download_dir": None,
            "status": "queued",
            "progress": 0,
            "requested_by": requested_by,
            "created_at": int(_now()),
            "updated_at": int(_now()),
            "error": None,
            "transmission_state": None,
            "rate_download_kib": None,
            "rate_upload_kib": None,
            "eta": None,
        }
        QUEUE[qid] = entry
        QUEUE_ORDER.insert(0, qid)

        _queue_save_sync()
        if len(QUEUE_ORDER) > QUEUE_MAX_ITEMS:
            for old_id in QUEUE_ORDER[QUEUE_MAX_ITEMS:]:
                old = QUEUE.pop(old_id, None)
                if old and old.get("btih"):
                    BTIH_INDEX.pop(old["btih"], None)
            del QUEUE_ORDER[QUEUE_MAX_ITEMS:]

        if btih:
            BTIH_INDEX[btih] = qid

        return entry

async def _queue_update(qid: str, status: str, progress: Optional[int] = None, error: Optional[str] = None, extra: Optional[Dict[str, Any]] = None):
    async with QUEUE_LOCK:
        entry = QUEUE.get(qid)
        if not entry: return
        if not entry:
            return
        entry["status"] = status
        if progress is not None:
            entry["progress"] = max(0, min(100, int(progress)))
        if error is not None:
            entry["error"] = error
        if extra:
            entry.update(extra)
        entry["updated_at"] = int(_now())
        _queue_save_sync()

        if status in ("failed", "completed", "cancelled", "imported"):
            btih = entry.get("btih")
            if btih:
                BTIH_INDEX.pop(btih, None)

async def _add_to_transmission(entry: Dict[str, Any]):
    try:
        _guard_free_space(STAGING_BASE_DIR)
        staging_dir = _staging_dir_for(entry)
        staging_dir.mkdir(parents=True, exist_ok=True)
        entry["download_dir"] = str(staging_dir)

        cp = _transmission_run(
            ["transmission-remote", "localhost", "-a", entry["magnet"], "-w", str(staging_dir)]
        )

        out = (cp.stdout or "").lower()
        if "duplicate torrent" in out or "duplicate" in out:
            await _queue_update(entry["id"], "failed", error="Duplicate torrent (already added)")
            return

        btih = entry.get("btih")
        if btih:
            tid = _transmission_find_id_by_btih(btih)
            if tid is not None:
                entry["transmission_id"] = tid

        await _queue_update(entry["id"], "downloading", progress=0)
        await _post_webhook("downloading", {"id": entry["id"], "title": entry.get("title")})
    except Exception as e:
        await _queue_update(entry["id"], "failed", error=str(e))
        await _post_webhook("failed", {"id": entry["id"], "error": str(e)})

async def _queue_poller():
    while True:
        try:
            async with QUEUE_LOCK:
                ids = list(QUEUE_ORDER)

            for qid in ids:
                async with QUEUE_LOCK:
                    entry = QUEUE.get(qid)
                    if not entry:
                        continue
                    st = entry.get("status")
                    tid = entry.get("transmission_id")

                if st not in ("queued", "downloading", "completed", "ready"):
                    continue

                if tid is None:
                    btih = entry.get("btih")
                    if btih:
                        tid = _transmission_find_id_by_btih(btih)
                        if tid is not None:
                            async with QUEUE_LOCK:
                                if qid in QUEUE:
                                    QUEUE[qid]["transmission_id"] = tid
                    if tid is None:
                        continue

                info = _transmission_get_info(int(tid))
                percent = info.get("percent")
                state = (info.get("state") or "").lower()

                prog = 0
                if percent is not None:
                    prog = int(max(0, min(100, round(float(percent)))))

                extra = {
                    "transmission_state": state,
                    "rate_download_kib": info.get("rate_download_kib"),
                    "rate_upload_kib": info.get("rate_upload_kib"),
                    "eta": info.get("eta"),
                }

                have_pct = info.get("have_pct")
                downloaded_bytes = info.get("downloaded_bytes")

                is_finishedish = state in ("idle", "finished", "seeding", "stopped")
                # Must have actual bytes — guards against unresolved magnets (nan%/idle, no data)
                has_real_data = (downloaded_bytes or 0) > 0
                is_really_complete = has_real_data and (
                    (have_pct is not None and have_pct >= 99.9) or (prog >= 100)
                )

                if is_finishedish and is_really_complete:
                    await _queue_update(qid, "ready", progress=100, extra=extra)
                elif is_finishedish and not has_real_data:
                    # Torrent is idle/stopped but has no data — unresolved magnet or no seeders.
                    # Wait 3 minutes before marking failed to allow slow peer discovery.
                    age_secs = _now() - (entry.get("created_at") or _now())
                    if age_secs > 180:
                        no_seed_extra = {**extra, "error": "No seeders found — torrent metadata could not be resolved. Try a different source."}
                        await _queue_update(qid, "failed", progress=0, extra=no_seed_extra)
                elif state in ("downloading", "download", "up & down", "up and down", "verifying"):
                    await _queue_update(qid, "downloading", progress=prog, extra=extra)
                else:
                    await _queue_update(qid, st, progress=prog if st == "downloading" else entry.get("progress", 0), extra=extra)
        except Exception:
            pass

        await asyncio.sleep(2)

def _require_ui_api_key(req: Request):
    """
    Legacy admin auth: X-API-Key header.
    Still required for all torrent/queue operations.
    JWT auth is used separately for file manager and user identity.
    """
    if not TORRENT_API_KEY:
        raise HTTPException(status_code=500, detail="Server misconfigured: TORRENT_API_KEY not set")
    key = (req.headers.get("x-api-key") or "").strip()
    if not key or not secrets.compare_digest(key, TORRENT_API_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized: missing/invalid API key")

def _require_nas_signature(req: Request, raw_body: bytes):
    if not NAS_CALLBACK_SECRET:
        raise HTTPException(status_code=500, detail="Server misconfigured: NAS_CALLBACK_SECRET not set")
    sig = (req.headers.get("x-signature") or "").strip()
    expected = _hmac_sha256(NAS_CALLBACK_SECRET, raw_body)
    if not sig or not secrets.compare_digest(sig, expected):
        raise HTTPException(status_code=401, detail="Unauthorized: bad signature")

app = FastAPI(
    title="Torrent-Api-Py",
    version="1.1.0",
    description="Local torrent ingestion + review + user file manager",
    docs_url="/docs",
    redirect_slashes=True,
)

async def _probe_url(url: str, timeout: float = 2.5) -> Dict[str, Any]:
    if not url:
        return {"ok": False, "status": None, "error": "not configured"}

    if httpx:
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, trust_env=False) as client:
                r = await client.get(url)
                return {"ok": r.status_code < 500, "status": r.status_code, "error": None}
        except Exception as e:
            return {"ok": False, "status": None, "error": type(e).__name__}

    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            code = getattr(resp, "status", 200)
            return {"ok": code < 500, "status": code, "error": None}
    except Exception as e:
        return {"ok": False, "status": None, "error": type(e).__name__}

async def _probe_transmission_rpc(url: str, timeout: float = 2.5) -> Dict[str, Any]:
    if not url:
        return {"ok": False, "status": None, "error": "not configured"}

    if httpx:
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                r = await client.post(url, content=b"")
                ok = (r.status_code < 500) or (r.status_code == 409)
                return {"ok": ok, "status": r.status_code, "error": None}
        except Exception as e:
            return {"ok": False, "status": None, "error": type(e).__name__}

    try:
        import urllib.request
        req = urllib.request.Request(url, data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = getattr(resp, "status", 200)
            ok = (code < 500) or (code == 409)
            return {"ok": ok, "status": code, "error": None}
    except Exception as e:
        if getattr(e, "code", None) == 409:
            return {"ok": True, "status": 409, "error": None}
        return {"ok": False, "status": None, "error": type(e).__name__}

@app.get("/api/services/health")
async def services_health():
    transmission_probe = (TRANSMISSION_RPC or "").strip()
    if not transmission_probe:
        base = (TRANSMISSION_WEB or "").rstrip("/")
        if base.endswith("/transmission/web"):
            transmission_probe = base[:-len("/web")] + "/rpc"
        else:
            transmission_probe = "http://127.0.0.1:9091/transmission/rpc"

    transmission = await _probe_transmission_rpc(transmission_probe)
    abb = await _probe_url(ABB_URL)

    jellyfin = await _probe_url(JELLYFIN_URL.rstrip("/") + "/System/Info/Public") if JELLYFIN_URL else {"ok": False, "status": None, "error": "not configured"}
    navidrome = await _probe_url(NAVIDROME_URL.rstrip("/") + "/") if NAVIDROME_URL else {"ok": False, "status": None, "error": "not configured"}

    return {
        "ok": True,
        "services": {
            "transmission": transmission,
            "audiobookbay": abb,
            "jellyfin": jellyfin,
            "navidrome": navidrome,
        },
        "uptime_seconds": int(time.time() - startTime),
    }

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── YouTube file watcher ─────────────────────────────────────────────────────
_YT_KNOWN_FILES: set = set()
_YT_LAST_CHANGE: float = 0.0
_YT_RESCAN_DEBOUNCE = 30.0  # seconds to wait after last new file before scanning

async def _yt_file_watcher():
    """
    Poll YOUTUBE_BASE_DIR every 30s for new video files.
    When new files appear, start a 30-second debounce timer.
    After timer expires (no further new files), trigger Jellyfin library scan.
    """
    global _YT_KNOWN_FILES, _YT_LAST_CHANGE
    _WATCH_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".wmv"}
    _first_run = True

    while True:
        try:
            if YOUTUBE_BASE_DIR.exists():
                current: set = set()
                for p in YOUTUBE_BASE_DIR.rglob("*"):
                    if p.is_file() and p.suffix.lower() in _WATCH_EXTS:
                        current.add(str(p))

                new_files = current - _YT_KNOWN_FILES
                if new_files and not _first_run:
                    _YT_LAST_CHANGE = time.time()
                    logger.info(
                        "YouTube watcher: %d new file(s) detected: %s",
                        len(new_files),
                        ", ".join(Path(f).name for f in list(new_files)[:3]),
                    )

                _YT_KNOWN_FILES = current
                _first_run = False

                # Fire Jellyfin scan once the debounce window has passed
                if _YT_LAST_CHANGE > 0 and (time.time() - _YT_LAST_CHANGE) >= _YT_RESCAN_DEBOUNCE:
                    logger.info("YouTube watcher: debounce elapsed — triggering Jellyfin library scan")
                    _YT_LAST_CHANGE = 0.0
                    asyncio.create_task(_jellyfin_refresh())
        except Exception as exc:
            logger.warning("_yt_file_watcher error: %s", exc)

        await asyncio.sleep(30)
# ─────────────────────────────────────────────────────────────────────────────


@app.on_event("startup")
async def _startup():
    STAGING_BASE_DIR.mkdir(parents=True, exist_ok=True)
    # Create user media root directory
    try:
        USER_MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning("Could not create USER_MEDIA_ROOT %s: %s", USER_MEDIA_ROOT, e)
    asyncio.create_task(_queue_poller())
    asyncio.create_task(_refresh_jellyfin_library())
    asyncio.create_task(_yt_file_watcher())

    async def _library_refresh_loop():
        while True:
            await asyncio.sleep(3600)
            await _refresh_jellyfin_library()
    asyncio.create_task(_library_refresh_loop())

@app.get("/health")
async def health_route(req: Request):
    return JSONResponse(
        {"app":"Torrent-Api-Py","version":"v1.1.0","ip":req.client.host,"uptime":ceil(getUptime(startTime))}
    )

@app.get("/api/v1/prepare/{qid}")
async def prepare_move(qid: str, req: Request):
    _require_ui_api_key(req)

    async with QUEUE_LOCK:
        entry = QUEUE.get(qid)
        if not entry:
            raise HTTPException(status_code=404, detail="Not found")

    if entry.get("status") not in ("ready", "completed"):
        raise HTTPException(status_code=409, detail="Not ready to process yet")

    staging_dir = Path(entry.get("download_dir") or "")
    if not staging_dir.exists():
        raise HTTPException(status_code=404, detail="Staging dir missing")

    files = _collect_files(staging_dir)

    ui_settings = {
        "tv_auto_split_default": True,
        "music_recommend_beets": bool(BEETS_IMPORT_CMD),
        "music_example_structure": "Artist/Album/Track - Title.ext",
    }

    raw_title = entry.get("title") or ""
    norm_cat_guess = _guess_category_from_files(raw_title, files)

    src_site = (entry.get("site") or "").strip().lower()
    if src_site == "audiobookbay":
        norm_cat_guess = "music"

    music_kind_guess = _guess_music_kind(files) if norm_cat_guess == "music" else None
    if src_site == "audiobookbay":
        music_kind_guess = "audiobook"

    ui_settings["music_kind_guess"] = music_kind_guess

    show_guess, season_guess = _guess_tv_show_and_season(raw_title)
    if norm_cat_guess == "tv" and not show_guess:
        show_guess = _clean_title_for_search(raw_title)

    suggested = {"kind": norm_cat_guess}
    candidates: List[Dict[str, Any]] = []
    tmdb_debug = ""

    if norm_cat_guess == "movies":
        year = _extract_year(raw_title)
        query = _clean_title_for_search(raw_title)
        results, dbg = await _tmdb_search_movie(query, year=year)
        tmdb_debug = dbg
        for m in results:
            candidates.append({
                "source": "tmdb",
                "kind": "movie",
                "tmdb_id": m.get("id"),
                "title": m.get("title"),
                "year": (m.get("release_date") or "")[:4] or None,
                "label": _tmdb_movie_label(m),
            })
        if candidates:
            suggested.update(candidates[0])

        largest = _largest_video_rel(files)
        for f in files:
            ext = (f.get("ext") or "").lower()
            if ext in _ALLOWED_VIDEO_EXT:
                f["default_action"] = "keep" if (largest and f["rel"] == largest) else "delete"
            elif ext in _ALLOWED_SUB_EXT:
                f["default_action"] = "keep"

    elif norm_cat_guess == "tv":
        query = _clean_title_for_search(show_guess or raw_title)
        results, dbg = await _tmdb_search_tv(query)
        tmdb_debug = dbg
        for t in results:
            candidates.append({
                "source": "tmdb",
                "kind": "tv",
                "tmdb_id": t.get("id"),
                "show": t.get("name"),
                "year": (t.get("first_air_date") or "")[:4] or None,
                "label": _tmdb_tv_label(t),
            })
        if candidates:
            suggested.update(candidates[0])
        suggested["season"] = int(season_guess or 1)

    async with QUEUE_LOCK:
        if entry["id"] in QUEUE:
            QUEUE[entry["id"]]["prepared_meta"] = {"suggested": suggested, "candidates": candidates}

    return JSONResponse({
        "success": True,
        "suggested": suggested,
        "candidates": candidates,
        "tmdb_debug": tmdb_debug,
        "id": entry["id"],
        "title": entry.get("title"),
        "category": norm_cat_guess,
        "staging_dir": str(staging_dir),
        "files": [{"rel": f["rel"], "size": f.get("size", 0), "default_action": f.get("default_action")} for f in files],
        "transmission_state": entry.get("transmission_state"),
        "ui_settings": ui_settings,
    })

def _resolve_under(base: Path, rel: str) -> Path:
    rel = (rel or "").lstrip("/").replace("\\", "/")
    p = (base / rel).resolve()
    b = base.resolve()
    if not str(p).startswith(str(b) + os.sep) and p != b:
        raise HTTPException(status_code=400, detail=f"Invalid path: {rel}")
    return p

@app.post("/api/v1/process")
async def process_move(req: Request):
    _require_ui_api_key(req)
    data = await req.json()

    qid = (data.get("id") or "").strip()
    if not qid:
        raise HTTPException(status_code=400, detail="Missing id")

    async with QUEUE_LOCK:
        entry = QUEUE.get(qid)
        if not entry:
            raise HTTPException(status_code=404, detail="Not found")

    if entry.get("status") not in ("ready", "completed"):
        raise HTTPException(status_code=409, detail="Not ready to process yet")

    staging_dir = Path(entry.get("download_dir") or "")
    if not staging_dir.exists():
        raise HTTPException(status_code=404, detail="Staging dir missing")

    dest_type = _normalize_category((data.get("dest_type") or "movies"))
    custom_subfolder = (data.get("custom_subfolder") or "").strip()
    meta = data.get("meta") or {}
    name_override = data.get("name_override") or {}

    keep_rels = data.get("keep_files") or []
    del_rels  = data.get("delete_files") or []

    file_name_overrides = data.get("file_name_overrides") or {}
    if not isinstance(file_name_overrides, dict):
        file_name_overrides = {}

    selected_keep: List[Path] = []
    selected_del: List[Path] = []

    if not isinstance(keep_rels, list) or not isinstance(del_rels, list):
        keep_rels, del_rels = [], []

    if keep_rels or del_rels:
        for rel in keep_rels:
            p = _resolve_under(staging_dir, str(rel))
            if p.is_file():
                selected_keep.append(p)
        for rel in del_rels:
            p = _resolve_under(staging_dir, str(rel))
            if p.is_file():
                selected_del.append(p)
    else:
        files = _collect_files(staging_dir)
        for f in files:
            p = _resolve_under(staging_dir, f["rel"])
            if f.get("default_action") == "keep":
                selected_keep.append(p)
            else:
                selected_del.append(p)

    dest_base = _final_base_for_category(dest_type)
    _guard_free_space(dest_base)

    if dest_type == "other":
        safe_sub = _safe_title(custom_subfolder.replace("/", " "), 120) if custom_subfolder else "Other"
        parts = [p for p in custom_subfolder.replace("\\", "/").split("/") if p.strip()]
        if parts:
            clean_parts = [_safe_title(seg, 80) for seg in parts]
            dest_dir = FINAL_OTHER_BASE.joinpath(*clean_parts)
        else:
            dest_dir = FINAL_OTHER_BASE / safe_sub
    elif dest_type == "movies":
        title = (name_override.get("title") or meta.get("title") or meta.get("name") or entry.get("title") or "Untitled").strip()
        year = (name_override.get("year") or meta.get("year") or "")
        year = str(year)[:4].strip() if year else ""
        folder = _safe_title(title, 120)
        if year:
            folder = f"{folder} ({year})"
        dest_dir = FINAL_MOVIES_DIR / folder
    elif dest_type == "tv":
        show = (name_override.get("title") or meta.get("show") or meta.get("title") or entry.get("title") or "Untitled Show").strip()
        show = _safe_title(show, 120)
        tv_year = (name_override.get("year") or meta.get("year") or "")
        tv_year = str(tv_year)[:4].strip() if tv_year else ""
        if tv_year:
            show_folder = f"{show} ({tv_year})"
        else:
            show_folder = show
        try:
            season = int(name_override.get("season") or meta.get("season") or 1)
        except Exception:
            season = 1
        season = max(1, season)
        dest_dir = FINAL_TV_DIR / show_folder / f"Season {season:02d}"
    elif dest_type == "music":
        dest_dir = FINAL_MUSIC_DIR / "_incoming"
    else:
        dest_dir = dest_base / _safe_title(entry.get("title") or "untitled", 120)

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        await _queue_update(qid, "failed", error=f"Dest create failed: {type(e).__name__}: {e}")
        return JSONResponse({"success": False, "message": "Destination creation failed"})

    for p in selected_del:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass

    moved_any = False
    move_errors: List[str] = []
    moved_targets: List[str] = []
    moved_count = 0
    tv_season_counts: Dict[int, int] = {}

    norm_cat = _normalize_category(dest_type)

    approved = {}
    if norm_cat == "tv":
        approved["show"] = (name_override.get("title") or meta.get("show") or meta.get("title") or entry.get("title") or "Untitled Show").strip()
        approved["year"] = (name_override.get("year") or meta.get("year") or "")
        try:
            approved["season"] = int(name_override.get("season") or meta.get("season") or 1)
        except Exception:
            approved["season"] = 1

    auto_split_tv = bool((meta.get("options") or {}).get("tv_auto_split", True))
    run_beets = bool((meta.get("options") or {}).get("music_run_beets", bool(BEETS_IMPORT_CMD)))

    def _safe_move(src: Path, target: Path, rel_hint: Optional[str] = None):
        try:
            final_target = target
            if rel_hint and rel_hint in file_name_overrides:
                override = str(file_name_overrides.get(rel_hint) or "").strip()
                if override:
                    safe = _safe_filename(override, src.suffix)
                    final_target = final_target.with_name(safe)
            final_target.parent.mkdir(parents=True, exist_ok=True)
            if final_target.exists():
                stem = final_target.stem
                ext = final_target.suffix
                for i in range(1, 1000):
                    candidate = final_target.with_name(f"{stem} ({i}){ext}")
                    if not candidate.exists():
                        final_target = candidate
                        break
            shutil.move(str(src), str(final_target))
            return True, str(final_target), None
        except Exception as e:
            return False, None, f"{type(e).__name__}: {e}"

    if norm_cat == "tv":
        base_show = _safe_title(approved.get("show") or entry.get("title") or "Untitled Show", 120)
        tv_year = str((approved.get("year") or "")).strip()[:4]
        show_name = f"{base_show} ({tv_year})" if tv_year else base_show

        for p in selected_keep:
            try:
                try:
                    rel = str(p.relative_to(staging_dir)).replace("\\", "/")
                except Exception:
                    rel = p.name

                season = None
                if auto_split_tv:
                    season = _parse_season_from_relpath(rel) or _parse_season_episode_from_name(p.name)

                if season is None:
                    season = int(approved.get("season") or 1)

                season = max(1, int(season))

                target_dir = FINAL_TV_DIR / show_name / f"Season {season:02d}"
                target = target_dir / p.name

                ok, tgt, err = _safe_move(p, target, rel_hint=rel)
                if ok:
                    moved_any = True
                    moved_count += 1
                    moved_targets.append(tgt)
                    tv_season_counts[season] = tv_season_counts.get(season, 0) + 1
                else:
                    move_errors.append(f"{p}: {err}")
            except Exception as e:
                move_errors.append(f"{p}: {type(e).__name__}: {e}")

    elif norm_cat == "music":
        opts = (meta.get("options") or {})
        music_kind = ((meta.get("music") or {}).get("kind") or opts.get("music_kind") or "music").strip().lower()
        if music_kind not in ("music", "audiobook"):
            music_kind = "music"

        music_meta = (meta.get("music") or {})
        artist = _safe_title((music_meta.get("artist") or "").strip(), 120)
        album  = _safe_title((music_meta.get("album") or "").strip(), 120)
        myear  = str((music_meta.get("year") or "")).strip()[:4]

        author = _safe_title((music_meta.get("author") or "").strip(), 120)
        book   = _safe_title((music_meta.get("book") or "").strip(), 120)
        byear  = str((music_meta.get("year") or "")).strip()[:4]

        if music_kind == "audiobook":
            ab_root = dest_base / "Audiobooks"
            a_dir = author or "Unknown Author"
            b_dir = book or "Unknown Book"
            if byear:
                b_dir = f"{b_dir} ({byear})"
            dest_dir_effective = ab_root / a_dir / b_dir

            # Dedup: if this audiobook already exists, skip move and just create playlist
            if dest_dir_effective.exists() and any(dest_dir_effective.iterdir()):
                _req_user = entry.get("requested_by")
                _pl_name = f"{book} — {author}" if (book and author) else (book or entry.get("title") or "Audiobook")
                _dedup_nd_user = entry.get("nd_user") or NAVIDROME_USER
                _dedup_nd_pass = entry.get("nd_pass") or NAVIDROME_PASSWORD
                _pl_ok = await _navidrome_create_user_playlist(
                    _dedup_nd_user, _dedup_nd_pass, _pl_name,
                    "Audiobook already in library — playlist created for this user"
                )
                await _navidrome_start_scan()
                await _queue_update(qid, "imported", progress=100)
                return JSONResponse({
                    "success": True,
                    "destination_dir": str(dest_dir_effective),
                    "moved_files_count": 0,
                    "moved_paths_sample": [],
                    "deduped": True,
                    "navidrome_scan": True,
                    "navidrome_playlist": _pl_name if _pl_ok else None,
                    "message": "Audiobook already exists in library. Playlist created for your account.",
                })
        else:
            if artist:
                a_dir = artist
            else:
                a_dir = "_incoming"
            if album:
                alb = album
                if myear:
                    alb = f"{alb} ({myear})"
                dest_dir_effective = dest_base / a_dir / alb
            else:
                dest_dir_effective = dest_base / a_dir / "_single"

        try:
            dest_dir_effective.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            await _queue_update(qid, "failed", error=f"Dest create failed: {type(e).__name__}: {e}")
            return JSONResponse({"success": False, "message": "Destination creation failed"})

        for p in selected_keep:
            try:
                try:
                    rel = str(p.relative_to(staging_dir)).replace("\\", "/")
                except Exception:
                    rel = p.name
                target = dest_dir_effective / p.name
                ok, tgt, err = _safe_move(p, target, rel_hint=rel)
                if ok:
                    moved_any = True
                    moved_count += 1
                    moved_targets.append(tgt)
                else:
                    move_errors.append(f"{p}: {err}")
            except Exception as e:
                move_errors.append(f"{p}: {type(e).__name__}: {e}")

        if music_kind == "music" and run_beets and BEETS_IMPORT_CMD:
            try:
                subprocess.Popen(BEETS_IMPORT_CMD.split() + [str(dest_dir_effective)])
            except Exception:
                pass

        dest_dir = dest_dir_effective

    else:
        for p in selected_keep:
            try:
                try:
                    rel = str(p.relative_to(staging_dir)).replace("\\", "/")
                except Exception:
                    rel = p.name
                ok, tgt, err = _safe_move(p, dest_dir / p.name, rel_hint=rel)
                if ok:
                    moved_any = True
                    moved_count += 1
                    moved_targets.append(tgt)
                else:
                    move_errors.append(f"{p}: {err}")
            except Exception as e:
                move_errors.append(f"{p}: {type(e).__name__}: {e}")

    if not moved_any:
        await _queue_update(
            qid,
            "failed",
            error="No usable media files found to move -- " + "; ".join(move_errors[:3]),
        )
        return JSONResponse({"success": False, "message": "No usable media files found", "errors": move_errors})

    try:
        for _ in range(5):
            for d in sorted(staging_dir.rglob("*"), key=lambda x: len(str(x)), reverse=True):
                if d.is_dir():
                    try:
                        d.rmdir()
                    except Exception:
                        pass
            try:
                staging_dir.rmdir()
                break
            except Exception:
                break
    except Exception:
        pass

    tid = entry.get("transmission_id")
    if tid is not None:
        _transmission_run(["transmission-remote", "localhost", "-t", str(tid), "-S"])
        _transmission_run(["transmission-remote", "localhost", "-t", str(tid), "-r"])

    await _queue_update(qid, "imported", progress=100)

    await _jellyfin_refresh()

    navidrome_triggered = False
    navidrome_playlist = None
    if dest_type == "music":
        navidrome_triggered = await _navidrome_start_scan()
        if navidrome_triggered:
            await _navidrome_wait_for_scan(timeout=90)  # wait for scan before creating playlist
        if music_kind == "audiobook":
            _ab_author = _safe_title((meta.get("music") or {}).get("author", "").strip(), 80)
            _ab_book   = _safe_title((meta.get("music") or {}).get("book", "").strip(), 80)
            _pl_name = f"{_ab_book} — {_ab_author}" if (_ab_book and _ab_author) else (_ab_book or entry.get("title") or "Audiobook")
            _pl_comment = "Audiobook imported via Torrent Requests"

            # Use requesting user's Navidrome credentials if available, else fall back to admin
            _req_user = entry.get("requested_by")
            _nd_user = _req_user or NAVIDROME_USER
            _nd_pass = NAVIDROME_PASSWORD  # we only store admin pass; per-user pass not stored

            # Use per-user Navidrome creds if provided, else fall back to admin
            _entry_nd_user = entry.get("nd_user") or _nd_user
            _entry_nd_pass = entry.get("nd_pass") or _nd_pass
            _pl_dest = str(dest_dir_effective) if dest_dir_effective else ""
            if _entry_nd_user and _entry_nd_pass:
                ok = await _navidrome_create_user_playlist(_entry_nd_user, _entry_nd_pass, _pl_name, _pl_comment, _pl_dest)
                if not ok:
                    ok = await _navidrome_create_playlist(_pl_name, _pl_comment, _pl_dest)
            else:
                ok = await _navidrome_create_playlist(_pl_name, _pl_comment, _pl_dest)
            if ok:
                navidrome_playlist = _pl_name

    # After scan, populate playlist with actual tracks (run in background)
    if navidrome_playlist and music_kind == "audiobook":
        async def _populate_later():
            await asyncio.sleep(20)  # wait for Navidrome to finish scanning
            await _navidrome_populate_playlist(
                navidrome_playlist, _ab_author, _ab_book,
                entry.get("nd_user") or NAVIDROME_USER,
                entry.get("nd_pass") or NAVIDROME_PASSWORD
            )
        asyncio.create_task(_populate_later())

    await _post_webhook("imported", {
        "id": qid,
        "title": entry.get("title"),
        "dest": str(dest_dir),
        "navidrome_scan": navidrome_triggered,
    })

    tv_summary = None
    if norm_cat == "tv":
        base = str(FINAL_TV_DIR / show_name)
        seasons = [{"season": s, "path": f"{base}/Season {s:02d}", "files": tv_season_counts.get(s, 0)} for s in sorted(tv_season_counts.keys())]
        tv_summary = {
            "show_base": base,
            "seasons": seasons,
        }

    return JSONResponse({
        "success": True,
        "destination_dir": str(dest_dir),
        "moved_files_count": moved_count,
        "moved_paths_sample": moved_targets[:25],
        "tv_summary": tv_summary,
        "navidrome_scan": navidrome_triggered,
        "navidrome_playlist": navidrome_playlist,
    })

def _is_probable_url(s: str) -> bool:
    s = (s or "").strip().lower()
    return s.startswith("http://") or s.startswith("https://")

async def _fetch_torrent_bytes(torrent_url: str) -> bytes:
    if not httpx:
        raise HTTPException(status_code=500, detail="httpx not installed (required for torrent_url)")

    url = (torrent_url or "").strip()
    if not _is_probable_url(url):
        raise HTTPException(status_code=400, detail="Invalid torrent_url")

    headers = {"User-Agent": ABB_USER_AGENT}
    if ABB_COOKIE:
        headers["Cookie"] = ABB_COOKIE

    async with httpx.AsyncClient(timeout=20.0, headers=headers, follow_redirects=True) as client:
        r = await client.get(url)
        if r.status_code >= 400 or not r.content:
            txt = (r.text or "")[:200].replace("\n", " ")
            raise HTTPException(status_code=502, detail=f"Failed to fetch .torrent (HTTP {r.status_code}): {txt}")
        if r.headers.get("content-type", "").lower().startswith("text/html"):
            head = (r.text or "")[:200].lower()
            if "<html" in head or "login" in head:
                raise HTTPException(status_code=502, detail="Fetched HTML instead of .torrent (blocked or login required)")
        return bytes(r.content)

# Existing API routers (unchanged)
app.include_router(search_router, prefix="/api/v1/search", dependencies=[Depends(authenticate_request)])
app.include_router(trending_router, prefix="/api/v1/trending", dependencies=[Depends(authenticate_request)])
app.include_router(category_router, prefix="/api/v1/category", dependencies=[Depends(authenticate_request)])
app.include_router(recent_router, prefix="/api/v1/recent", dependencies=[Depends(authenticate_request)])
app.include_router(combo_router, prefix="/api/v1/all", dependencies=[Depends(authenticate_request)])
app.include_router(site_list_router, prefix="/api/v1/sites", dependencies=[Depends(authenticate_request)])
app.include_router(search_url_router, prefix="/api/v1/search_url", dependencies=[Depends(authenticate_request)])
app.include_router(home_router, prefix="/readme")

# ── NEW: auth + file manager routers ────────────────────────────────────────
app.include_router(auth_router, prefix="/api/v1/auth")
app.include_router(files_router, prefix="/api/v1/files")
# ────────────────────────────────────────────────────────────────────────────

# ── Music routers (Jellyfin JWT auth, no separate dependency wrapper needed) ─
app.include_router(music_search_router,   prefix="/api/v1/music")
app.include_router(music_requests_router, prefix="/api/v1/music")
app.include_router(music_queue_router,    prefix="/api/v1/music")
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/v1/download")
async def trigger_download(req: Request):
    _require_ui_api_key(req)

    # Optionally extract requesting user from JWT for per-user features
    _requesting_user = None
    try:
        _auth_hdr = req.headers.get("Authorization", "")
        if _auth_hdr.startswith("Bearer "):
            from auth.jwt_handler import verify_token
            _claims = verify_token(_auth_hdr[7:])
            if _claims:
                _requesting_user = _claims.get("sub") or None
    except Exception:
        pass

    data = await req.json()
    site = (data.get("site") or "piratebay").strip()
    title = (data.get("title") or "").strip()
    magnet = (data.get("magnet") or "").strip()
    torrent_url = (data.get("torrent") or data.get("torrent_url") or "").strip()
    _nd_user_payload = (data.get("nd_user") or "").strip() or None
    _nd_pass_payload = (data.get("nd_pass") or "").strip() or None

    if not title:
        raise HTTPException(status_code=400, detail="Invalid payload: title is required")

    if magnet:
        if not magnet.startswith("magnet:?"):
            raise HTTPException(status_code=400, detail="Invalid magnet")
        btih = _extract_btih(magnet)
        if not btih:
            raise HTTPException(status_code=400, detail="Invalid magnet: missing BTIH")
    else:
        if not torrent_url:
            raise HTTPException(status_code=400, detail="Invalid payload: magnet or torrent is required")
        torrent_bytes = await _fetch_torrent_bytes(torrent_url)
        try:
            btih = torrent_infohash_hex(torrent_bytes)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid .torrent: {type(e).__name__}")
        magnet = magnet_from_btih(btih)

    async with QUEUE_LOCK:
        existing_qid = BTIH_INDEX.get(btih)
        if existing_qid and existing_qid in QUEUE:
            existing = QUEUE[existing_qid]
            st = existing.get("status")
            if st in ("queued", "downloading"):
                return JSONResponse({"success": True, "id": existing_qid, "deduped": True, "message": "Already added"})

    ip = req.client.host if req.client else "unknown"
    blocked = await _rate_limit_check(ip)
    if blocked:
        return JSONResponse({"success": False, "error": "rate_limited", "message": blocked}, status_code=429)

    entry = await _queue_add(title=title, site=site, magnet=magnet, btih=btih, requested_by=_requesting_user)
    # Store per-user Navidrome creds in queue entry for playlist creation
    if _nd_user_payload and _nd_pass_payload:
        entry["nd_user"] = _nd_user_payload
        entry["nd_pass"] = _nd_pass_payload
    asyncio.create_task(_add_to_transmission(entry))

    return JSONResponse({"success": True, "id": entry["id"]})


@app.post("/api/v1/auth/navidrome-test")
async def test_navidrome_credentials(req: Request):
    """Test Navidrome credentials for a user — called from UI on save."""
    _require_ui_api_key(req)
    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    nd_user = (body.get("nd_user") or "").strip()
    nd_pass = (body.get("nd_pass") or "").strip()
    if not nd_user or not nd_pass:
        raise HTTPException(status_code=400, detail="Username and password required")
    if not NAVIDROME_URL or not httpx:
        raise HTTPException(status_code=503, detail="Navidrome not configured")
    try:
        params = {"u": nd_user, "p": nd_pass, "v": NAVIDROME_API_VER, "c": NAVIDROME_CLIENT, "f": "json"}
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get(f"{NAVIDROME_URL.rstrip('/')}/rest/ping.view", params=params)
        data = r.json()
        status = (data.get("subsonic-response") or {}).get("status")
        if status == "ok":
            return JSONResponse({"ok": True, "username": nd_user})
        else:
            err = (data.get("subsonic-response") or {}).get("error", {})
            return JSONResponse({"ok": False, "error": err.get("message", "Invalid credentials")})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})

@app.post("/api/v1/library/rescan")
async def library_rescan(req: Request):
    _require_ui_api_key(req)

    jellyfin_ok = False
    navidrome_ok = False

    try:
        await _jellyfin_refresh()
        jellyfin_ok = True if (JELLYFIN_URL and JELLYFIN_API_KEY and httpx) else False
    except Exception:
        jellyfin_ok = False

    try:
        navidrome_ok = await _navidrome_start_scan()
    except Exception:
        navidrome_ok = False

    return JSONResponse({
        "success": True,
        "jellyfin_triggered": jellyfin_ok,
        "navidrome_triggered": navidrome_ok,
    })

@app.get("/api/v1/queue")
async def get_queue(req: Request):
    _require_ui_api_key(req)

    async with QUEUE_LOCK:
        items = [QUEUE[qid] for qid in QUEUE_ORDER if qid in QUEUE]
        out = []
        for it in items:
            out.append({
                "id": it["id"],
                "title": it["title"],
                "download_dir": it.get("download_dir"),
                "status": it["status"],
                "progress": it.get("progress", 0),
                "error": it.get("error"),
                "transmission_state": it.get("transmission_state"),
                "rate_download_kib": it.get("rate_download_kib"),
                "rate_upload_kib": it.get("rate_upload_kib"),
                "eta": it.get("eta"),
                "created_at": it.get("created_at"),
                "updated_at": it.get("updated_at"),
            })
    return JSONResponse(out)


@app.post("/api/v1/queue/remove")
async def remove_from_queue(req: Request):
    """Remove a terminal-state item from the queue (completed/imported/failed/cancelled/ready)."""
    _require_ui_api_key(req)
    data = await req.json()
    qid = (data.get("id") or "").strip()
    if not qid:
        raise HTTPException(status_code=400, detail="Missing id")

    async with QUEUE_LOCK:
        entry = QUEUE.get(qid)
        if not entry:
            raise HTTPException(status_code=404, detail="Not found")
        status = entry.get("status", "")
        if status not in ("completed", "imported", "failed", "cancelled", "ready"):
            raise HTTPException(status_code=409, detail=f"Cannot remove item with status '{status}'")
        btih = entry.get("btih")
        if btih:
            BTIH_INDEX.pop(btih, None)
        QUEUE.pop(qid, None)
        if qid in QUEUE_ORDER:
            QUEUE_ORDER.remove(qid)

    _queue_save_sync()
    return JSONResponse({"success": True})


@app.post("/api/v1/queue/retry")
async def retry_queue_item(req: Request):
    """Re-add a failed or cancelled torrent back to Transmission."""
    _require_ui_api_key(req)
    data = await req.json()
    qid = (data.get("id") or "").strip()
    if not qid:
        raise HTTPException(status_code=400, detail="Missing id")

    async with QUEUE_LOCK:
        entry = QUEUE.get(qid)
        if not entry:
            raise HTTPException(status_code=404, detail="Not found")
        status = entry.get("status", "")
        if status not in ("failed", "cancelled"):
            raise HTTPException(status_code=409, detail=f"Can only retry failed or cancelled items (current: {status})")
        if not entry.get("magnet"):
            raise HTTPException(status_code=409, detail="No magnet link stored — cannot retry")
        entry["status"] = "queued"
        entry["error"] = None
        entry["progress"] = 0
        entry["transmission_id"] = None
        entry["transmission_state"] = None
        entry["rate_download_kib"] = None
        entry["rate_upload_kib"] = None
        entry["eta"] = None
        entry["updated_at"] = int(_now())
        # Re-add btih to index so dedup works again
        btih = entry.get("btih")
        if btih:
            BTIH_INDEX[btih] = qid

    _queue_save_sync()
    asyncio.create_task(_add_to_transmission(entry))
    return JSONResponse({"success": True})

@app.post("/api/v1/cancel")
async def cancel_download(req: Request):
    _require_ui_api_key(req)
    data = await req.json()
    qid = (data.get("id") or "").strip()
    if not qid:
        raise HTTPException(status_code=400, detail="Missing id")

    async with QUEUE_LOCK:
        entry = QUEUE.get(qid)
        if not entry:
            raise HTTPException(status_code=404, detail="Not found")

    btih = entry.get("btih")
    tid = entry.get("transmission_id")

    if tid is None and btih:
        tid = _transmission_find_id_by_btih(btih)
        if tid is not None:
            entry["transmission_id"] = tid

    if tid is None:
        await _queue_update(qid, "failed", error="Cancel failed: cannot find torrent in Transmission")
        return JSONResponse({"success": False, "message": "Cannot find torrent in Transmission"})

    _transmission_run(["transmission-remote", "localhost", "-t", str(tid), "-S"])
    cp = _transmission_run(["transmission-remote", "localhost", "-t", str(tid), "-r"])
    if cp.returncode != 0:
        await _queue_update(qid, "failed", error=f"Cancel failed: {cp.stdout.strip()[:200]}")
        return JSONResponse({"success": False, "message": "Cancel failed"})

    await _queue_update(qid, "cancelled")
    return JSONResponse({"success": True})

@app.post("/api/v1/download/status")
async def download_status(req: Request):
    raw = await req.body()
    _require_nas_signature(req, raw)

    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    qid = (data.get("id") or "").strip()
    status = (data.get("status") or "").strip()
    progress = data.get("progress", None)
    error = data.get("error", None)

    if not qid or status not in ("queued", "downloading", "completed", "failed"):
        raise HTTPException(status_code=400, detail="Invalid payload")

    await _queue_update(qid, status=status, progress=progress, error=error)
    return JSONResponse({"success": True})

# ── YouTube → Jellyfin download ──────────────────────────────────────────────
_YT_QUEUE: List[Dict[str, Any]] = []
_YT_QUEUE_LOCK = asyncio.Lock()

YOUTUBE_BASE_DIR = Path(os.getenv("YOUTUBE_BASE_DIR", "/mnt/media/YouTube"))

@app.post("/api/v1/youtube/download")
async def youtube_download(req: Request):
    _require_ui_api_key(req)
    body = await req.json()
    url = (body.get("url") or "").strip()
    title = (body.get("title") or "").strip()
    subfolder = (body.get("subfolder") or "").strip().strip("/")
    jellyfin_rescan = bool(body.get("jellyfin_rescan", True))

    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    dest = YOUTUBE_BASE_DIR / subfolder if subfolder else YOUTUBE_BASE_DIR
    dest.mkdir(parents=True, exist_ok=True)

    output_tpl = str(dest / (f"{title}.%(ext)s" if title else "%(title)s.%(ext)s"))
    ytdlp_bin = shutil.which("yt-dlp") or "yt-dlp"
    ytdlp_cmd = [
        ytdlp_bin,
        "--format", "bestvideo+bestaudio/best/mp4/m4a/91/bestaudio",
        "--merge-output-format", "mp4",
        "--output", output_tpl,
        url,
    ]
    entry_id = secrets.token_hex(6)
    entry: Dict[str, Any] = {
        "id": entry_id,
        "url": url,
        "title": title or "",
        "subfolder": subfolder,
        "dest": str(dest),
        "status": "queued",
        "error": None,
        "created_at": time.time(),
    }

    async with _YT_QUEUE_LOCK:
        _YT_QUEUE.append(entry)
        if len(_YT_QUEUE) > 20:
            _YT_QUEUE.pop(0)

    async def _run():
        async with _YT_QUEUE_LOCK:
            entry["status"] = "downloading"
        try:
            proc = await asyncio.create_subprocess_exec(
                *ytdlp_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode == 0:
                async with _YT_QUEUE_LOCK:
                    entry["status"] = "done"
                if jellyfin_rescan:
                    asyncio.create_task(_jellyfin_refresh())
            else:
                err = (stderr.decode(errors="replace") or "")[-400:].strip()
                async with _YT_QUEUE_LOCK:
                    entry["status"] = "failed"
                    entry["error"] = err
        except Exception as exc:
            async with _YT_QUEUE_LOCK:
                entry["status"] = "failed"
                entry["error"] = str(exc)

    asyncio.create_task(_run())
    return JSONResponse({"ok": True, "id": entry_id})

@app.get("/api/v1/youtube/queue")
async def youtube_queue(req: Request):
    _require_ui_api_key(req)
    async with _YT_QUEUE_LOCK:
        return JSONResponse(list(reversed(_YT_QUEUE)))

@app.get("/api/v1/youtube/check")
async def youtube_check(req: Request, url: str = ""):
    _require_ui_api_key(req)
    import re
    m = re.search(r'[?&]v=([a-zA-Z0-9_-]{11})', url)
    if not m:
        return JSONResponse({"exists": False, "path": None})
    video_id = m.group(1)
    needle = f"[{video_id}]"
    if YOUTUBE_BASE_DIR.exists():
        for root, _dirs, files in os.walk(YOUTUBE_BASE_DIR):
            for fname in files:
                if needle in fname:
                    return JSONResponse({"exists": True, "path": str(Path(root) / fname)})
    return JSONResponse({"exists": False, "path": None})

@app.post("/api/v1/youtube/playlist-info")
async def youtube_playlist_info(req: Request):
    _require_ui_api_key(req)
    body = await req.json()
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    ytdlp_bin = shutil.which("yt-dlp") or "yt-dlp"
    cmd = [ytdlp_bin, "--flat-playlist", "-j", "--no-warnings", "--no-progress", url]
    if YTDLP_COOKIES_FILE and Path(YTDLP_COOKIES_FILE).exists():
        cmd += ["--cookies", YTDLP_COOKIES_FILE]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=408, detail="Playlist info timed out (30s)")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    entries = []
    playlist_title = ""
    for line in stdout.decode(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if not playlist_title:
                playlist_title = (data.get("playlist_title") or data.get("playlist") or "").strip()
            eid = (data.get("id") or "").strip()
            etitle = (data.get("title") or eid).strip()
            if eid:
                entries.append({"id": eid, "title": etitle})
        except Exception:
            continue

    return JSONResponse({"title": playlist_title, "count": len(entries), "entries": entries})
# ─────────────────────────────────────────────────────────────────────────────


# ── Jellyfin library ownership check ─────────────────────────────────────────

@app.post("/api/v1/jellyfin/check-titles")
async def jellyfin_check_titles(req: Request):
    """
    Check which titles from a list are already in the Jellyfin library cache.
    Body: { "titles": ["Title 1", "Title 2", ...] }  (up to 50)
    Returns: { results: { "Title 1": true/false, ... }, library_size, last_fetch }
    """
    _require_ui_api_key(req)
    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    titles = (body or {}).get("titles") or []
    if not isinstance(titles, list):
        raise HTTPException(status_code=400, detail="titles must be a list")
    titles = titles[:50]

    async with _JELLYFIN_LIBRARY_LOCK:
        lib = set(_JELLYFIN_LIBRARY)
        last = _JELLYFIN_LIBRARY_LAST_FETCH

    results: Dict[str, bool] = {}
    for title in titles:
        norm = _normalise_title(str(title))
        if not norm or len(norm) < 2:
            results[title] = False
            continue
        if norm in lib:
            results[title] = True
            continue
        # Fuzzy: handle year-suffix differences (library has "title 2008", query has "title")
        found = any(
            (e.startswith(norm + " ") or norm.startswith(e + " ") or e == norm)
            for e in lib if e
        )
        results[title] = found

    return JSONResponse({"results": results, "library_size": len(lib), "last_fetch": last})


@app.get("/api/v1/jellyfin/library-status")
async def jellyfin_library_status():
    """Return current library cache size and age. No auth required."""
    async with _JELLYFIN_LIBRARY_LOCK:
        size = len(_JELLYFIN_LIBRARY)
        last = _JELLYFIN_LIBRARY_LAST_FETCH
    ago = round((time.time() - last) / 60.0, 1) if last else None
    return JSONResponse({"library_size": size, "last_fetch": last, "last_fetch_ago_minutes": ago})


@app.post("/api/v1/jellyfin/refresh-library")
async def jellyfin_refresh_library(req: Request):
    """Trigger an immediate library cache refresh. Returns updated status."""
    _require_ui_api_key(req)
    await _refresh_jellyfin_library()
    async with _JELLYFIN_LIBRARY_LOCK:
        size = len(_JELLYFIN_LIBRARY)
        last = _JELLYFIN_LIBRARY_LAST_FETCH
    return JSONResponse({"ok": True, "library_size": size, "last_fetch": last})

# ─────────────────────────────────────────────────────────────────────────────


# ── Metadata detection ────────────────────────────────────────────────────────
# Requires TMDB_API_KEY env var for Stage 2 TMDb verification (optional).

_QUALITY_TAGS = re.compile(
    r"\b(2160p|4k|uhd|1080p|720p|480p|bluray|blu-ray|bdrip|brrip|web[-\s]?dl|webrip|"
    r"hdtv|hdrip|dvdrip|dvdscr|hdcam|cam|ts|hdr|dv|sdr|remux|"
    r"x264|x265|h264|h265|hevc|avc|xvid|divx|"
    r"aac|dts|ac3|mp3|atmos|truehd|dd5\.?1|ddp5\.?1|"
    r"extended|theatrical|directors\.cut|remastered|proper|"
    r"multi|dubbed|subbed|\d+bit|\d+ch)\b",
    re.IGNORECASE,
)
_GROUP_TAG   = re.compile(r"-[A-Za-z0-9]{2,10}$")
_SEASON_EP   = re.compile(r"\bS(\d{1,2})E(\d{1,2})\b", re.IGNORECASE)
_SEASON_ONLY = re.compile(r"\bS(\d{1,2})\b(?!E\d)", re.IGNORECASE)
# Year must NOT be immediately preceded by digits (catches "10bit", "8bit", port numbers etc.)
_YEAR_RE     = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")

_AUDIO_EXTS     = re.compile(r"\b(flac|mp3|aac|wav|ogg|opus|alac|m4a|m4b|wma|aiff?)\b", re.IGNORECASE)
_AUDIOBOOK_NOISE = re.compile(
    r"\b(unabridged|abridged|audiobook|audio\s*book|part\s*\d+|book\s*\d+|vol(?:ume)?\s*\d+|narrator[:\s]\S+)\b",
    re.IGNORECASE,
)
_AUDIO_KEYWORDS = re.compile(r"\b(discography|album|soundtrack|ost|singles|collection)\b", re.IGNORECASE)

def _parse_filename(filename: str) -> Dict[str, Any]:
    """
    Stage 1: pure-regex metadata extraction.
    Returns dict with: title, year, season, episode, type, confidence (low/medium/high)
    """
    # Strip extension
    name = re.sub(r"\.[a-zA-Z0-9]{2,5}$", "", (filename or "").strip())

    # Bug C fix: strip -GROUP suffix from raw name before normalising separators
    name = _GROUP_TAG.sub("", name)

    # Normalise separators
    norm = name.replace(".", " ").replace("_", " ").replace("-", " ")

    # Extract season/episode
    season: Optional[int] = None
    episode: Optional[int] = None
    m_so = None
    m_se = _SEASON_EP.search(norm)
    if m_se:
        season  = int(m_se.group(1))
        episode = int(m_se.group(2))
    else:
        # Season-only (e.g. S01.COMPLETE) — no episode number
        m_so = _SEASON_ONLY.search(norm)
        if m_so:
            season = int(m_so.group(1))

    # Extract year
    year: Optional[int] = None
    m_yr = _YEAR_RE.search(norm)
    if m_yr:
        year = int(m_yr.group(1))

    # Strip everything from the year / S##E## / S## onwards, and remove quality tags
    cutoff = len(norm)
    if m_yr:
        cutoff = min(cutoff, m_yr.start())
    if m_se:
        cutoff = min(cutoff, m_se.start())
    if m_so:                                          # Bug 1: use season-only match for cutoff
        cutoff = min(cutoff, m_so.start())
    title_raw = norm[:cutoff]
    title_raw = _QUALITY_TAGS.sub(" ", title_raw)
    title_raw = re.sub(r"\bSEASON\s*\d+\b", " ", title_raw, flags=re.IGNORECASE)
    title_raw = re.sub(r"\bCOMPLETE\b", " ", title_raw, flags=re.IGNORECASE)
    title_raw = re.sub(r"\bS\d{1,2}\b", " ", title_raw, flags=re.IGNORECASE)  # Bug 2: bare S01 tokens
    title_raw = re.sub(r"\s{2,}", " ", title_raw).strip(" -([{")

    # Bug A fix: detect music BEFORE tv/movie
    is_audio_ext = bool(_AUDIO_EXTS.search(filename))
    if is_audio_ext or _AUDIO_KEYWORDS.search(norm):
        media_type = "music"
        # Strip audiobook noise words from title
        title_raw = _AUDIOBOOK_NOISE.sub(" ", title_raw)
        title_raw = re.sub(r"\s{2,}", " ", title_raw).strip(" -([{")
        # For m4b audiobooks: detect "Author - Title" from original name (before normalisation)
        if is_audio_ext and re.search(r"\.m4b$", filename, re.IGNORECASE):
            # `name` still has the original separator characters (dashes preserved)
            name_stripped = re.sub(r"\.[a-zA-Z0-9]{2,5}$", "", (filename or "").strip())
            name_stripped = _GROUP_TAG.sub("", name_stripped)
            m_sep = re.search(r"\s+-\s+", name_stripped)
            if m_sep:
                raw_author = name_stripped[:m_sep.start()].replace(".", " ").replace("_", " ").strip()
                raw_book   = name_stripped[m_sep.end():].replace(".", " ").replace("_", " ").strip()
                # Strip year and noise from book part
                raw_book = _QUALITY_TAGS.sub(" ", raw_book)
                raw_book = _AUDIOBOOK_NOISE.sub(" ", raw_book)
                m_yr2 = _YEAR_RE.search(raw_book)
                if m_yr2:
                    raw_book = raw_book[:m_yr2.start()]
                raw_book = re.sub(r"\s{2,}", " ", raw_book).strip(" -([{")
                if raw_author and raw_book:
                    title_raw = f"{raw_author} - {raw_book}"
    elif season is not None:
        media_type = "tv"
    else:
        media_type = "movie"

    # Confidence
    has_year    = year is not None
    has_season  = season is not None
    title_words = len(title_raw.split()) if title_raw else 0

    # Bug B fix: noise check — all words 3 chars or fewer → low confidence
    if title_words >= 1 and all(len(w) <= 3 for w in title_raw.split()):
        confidence = "low"
    elif title_words >= 1 and (has_year or has_season):
        confidence = "high"
    elif title_words >= 1:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "title":      title_raw or "",
        "year":       year,
        "season":     season,
        "episode":    episode,
        "type":       media_type,
        "confidence": confidence,
    }


@app.post("/api/v1/detect-metadata")
async def detect_metadata(req: Request):
    """
    Detect title/year/season/type from a torrent filename.

    Stage 1 — local regex (always runs).
    Stage 2 — TMDb verification (only when TMDB_API_KEY is set and confidence
              is medium or high).

    Body: { "filename": "<torrent name or path>" }
    Returns: { title, year, season, episode, type, confidence,
               tmdb_id, tmdb_poster, tmdb_overview }
    """
    _require_ui_api_key(req)
    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    filename = (((body or {}).get("filename") or "")).strip()
    if not filename:
        raise HTTPException(status_code=400, detail="filename is required")

    # Use just the last path component
    filename = Path(filename).name or filename

    parsed = _parse_filename(filename)
    result: Dict[str, Any] = {
        "title":        parsed["title"],
        "year":         parsed["year"],
        "season":       parsed["season"],
        "episode":      parsed["episode"],
        "type":         parsed["type"],
        "confidence":   parsed["confidence"],
        "tmdb_id":      None,
        "tmdb_poster":  None,
        "tmdb_overview": None,
    }

    # Stage 2 — TMDb verification
    if TMDB_API_KEY and parsed["confidence"] in ("medium", "high") and parsed["title"]:
        try:
            import asyncio as _asyncio
            q = parsed["title"]
            yr = parsed["year"]

            if parsed["type"] == "tv":
                candidates, _ = await _asyncio.wait_for(
                    _tmdb_search_tv(q), timeout=3.0
                )
                hit = next(
                    (c for c in candidates if (c.get("vote_count") or 0) > 10),
                    candidates[0] if candidates else None,
                )
                if hit:
                    result["tmdb_id"]       = hit.get("id")
                    result["title"]         = (hit.get("name") or parsed["title"]).strip()
                    raw_yr = (hit.get("first_air_date") or "")[:4]
                    if raw_yr.isdigit():
                        result["year"] = int(raw_yr)
                    result["confidence"]    = "high"
                    result["tmdb_overview"] = (hit.get("overview") or "")[:200]
                    poster = hit.get("poster_path")
                    if poster:
                        result["tmdb_poster"] = f"https://image.tmdb.org/t/p/w92{poster}"
            else:
                candidates, _ = await _asyncio.wait_for(
                    _tmdb_search_movie(q, yr), timeout=3.0
                )
                hit = next(
                    (c for c in candidates if (c.get("vote_count") or 0) > 10),
                    candidates[0] if candidates else None,
                )
                if hit:
                    result["tmdb_id"]       = hit.get("id")
                    result["title"]         = (hit.get("title") or parsed["title"]).strip()
                    raw_yr = (hit.get("release_date") or "")[:4]
                    if raw_yr.isdigit():
                        result["year"] = int(raw_yr)
                    result["confidence"]    = "high"
                    result["tmdb_overview"] = (hit.get("overview") or "")[:200]
                    poster = hit.get("poster_path")
                    if poster:
                        result["tmdb_poster"] = f"https://image.tmdb.org/t/p/w92{poster}"
        except Exception:
            pass  # TMDb failure is non-fatal; return Stage 1 result

    return JSONResponse(result)
# ─────────────────────────────────────────────────────────────────────────────

# ─── Storage stats ───────────────────────────────────────────────────────────
_storage_stats_cache: Dict[str, Any] = {"data": None, "ts": 0.0}
_STORAGE_CACHE_TTL = 300  # 5 minutes

@app.get("/api/v1/storage/stats")
async def storage_stats(req: Request):
    authenticate_request(req)
    now = time.time()
    if _storage_stats_cache["data"] and (now - _storage_stats_cache["ts"]) < _STORAGE_CACHE_TTL:
        return JSONResponse(_storage_stats_cache["data"])

    result: Dict[str, Any] = {}
    media_root = Path("/mnt/media")
    try:
        usage = shutil.disk_usage(str(media_root))
        result["total_gb"]      = round(usage.total / (1024 ** 3), 1)
        result["total_used_gb"] = round(usage.used  / (1024 ** 3), 1)
        result["total_free_gb"] = round(usage.free  / (1024 ** 3), 1)
    except Exception:
        result["total_gb"] = result["total_used_gb"] = result["total_free_gb"] = 0

    for subdir, key in [("Music", "music_gb"), ("TV", "tv_gb"), ("Movies", "movies_gb")]:
        try:
            p = media_root / subdir
            if p.exists():
                proc = await asyncio.create_subprocess_exec(
                    "du", "-sb", str(p),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
                result[key] = round(int(stdout.split()[0]) / (1024 ** 3), 1)
            else:
                result[key] = 0
        except Exception:
            result[key] = 0

    _storage_stats_cache["data"] = result
    _storage_stats_cache["ts"]   = now
    return JSONResponse(result)
# ─────────────────────────────────────────────────────────────────────────────

# ─── Jellyfin recently added ─────────────────────────────────────────────────
_recently_added_cache: Dict[str, Any] = {"data": None, "ts": 0.0}
_RECENTLY_ADDED_TTL = 300  # 5 minutes

@app.get("/api/v1/jellyfin/recently-added")
async def jellyfin_recently_added(req: Request):
    authenticate_request(req)
    now = time.time()
    if _recently_added_cache["data"] is not None and (now - _recently_added_cache["ts"]) < _RECENTLY_ADDED_TTL:
        return JSONResponse(_recently_added_cache["data"])

    if not JELLYFIN_URL or not JELLYFIN_API_KEY or not httpx:
        return JSONResponse({"items": []})

    try:
        base = JELLYFIN_URL.rstrip("/")
        params = {
            "api_key": JELLYFIN_API_KEY,
            "SortBy": "DateCreated,SortName",
            "SortOrder": "Descending",
            "Recursive": "true",
            "Limit": "8",
            "IncludeItemTypes": "Movie,Series",
            "Fields": "Name,ProductionYear,Type,ImageTags",
        }
        async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
            resp = await client.get(f"{base}/Items", params=params)
        raw = resp.json() if resp.status_code == 200 else {}
        items_raw = raw.get("Items") or []
        items = []
        for it in items_raw:
            item_id = it.get("Id") or ""
            image_tags = it.get("ImageTags") or {}
            thumb_url = None
            if item_id and image_tags.get("Primary"):
                thumb_url = f"{base}/Items/{item_id}/Images/Primary?api_key={JELLYFIN_API_KEY}&maxHeight=80&quality=70"
            items.append({
                "name": it.get("Name") or "",
                "year": it.get("ProductionYear") or None,
                "type": (it.get("Type") or "").lower(),
                "thumb_url": thumb_url,
            })
        result = {"items": items}
        _recently_added_cache["data"] = result
        _recently_added_cache["ts"] = now
        return JSONResponse(result)
    except Exception:
        return JSONResponse({"items": []})
# ─────────────────────────────────────────────────────────────────────────────

# ─── Navidrome recently added ─────────────────────────────────────────────────
_nd_recently_added_cache: Dict[str, Any] = {"data": None, "ts": 0.0}
_ND_RECENTLY_ADDED_TTL = 300  # 5 minutes

@app.get("/api/v1/navidrome/recently-added")
async def navidrome_recently_added(req: Request):
    authenticate_request(req)
    now = time.time()
    if _nd_recently_added_cache["data"] is not None and (now - _nd_recently_added_cache["ts"]) < _ND_RECENTLY_ADDED_TTL:
        return JSONResponse(_nd_recently_added_cache["data"])

    params = _navidrome_auth_params()
    if not params or not httpx:
        return JSONResponse({"albums": []})

    try:
        base = NAVIDROME_URL.rstrip("/")
        req_params = {**params, "type": "newest", "size": "8"}
        async with httpx.AsyncClient(timeout=8.0, trust_env=False) as client:
            resp = await client.get(f"{base}/rest/getAlbumList2", params=req_params)
        if resp.status_code != 200:
            return JSONResponse({"albums": []})
        raw = resp.json()
        album_list = ((raw.get("subsonic-response") or {}).get("albumList2") or {}).get("album") or []
        albums = []
        for al in album_list:
            al_id = al.get("id") or al.get("coverArt") or ""
            cover_url = None
            if al_id:
                cover_params = urllib.parse.urlencode({**params, "id": al_id, "size": "64"})
                cover_url = f"{base}/rest/getCoverArt?{cover_params}"
            albums.append({
                "name":        al.get("name") or al.get("album") or "",
                "artist":      al.get("artist") or al.get("artistName") or "",
                "year":        al.get("year") or None,
                "cover_url":   cover_url,
                "play_count":  al.get("playCount") or 0,
                "last_played": al.get("played") or None,  # ISO date string or None
            })
        result = {"albums": albums}
        _nd_recently_added_cache["data"] = result
        _nd_recently_added_cache["ts"] = now
        return JSONResponse(result)
    except Exception:
        return JSONResponse({"albums": []})
# ─────────────────────────────────────────────────────────────────────────────

# ─── Jellyfin Now Playing ────────────────────────────────────────────────────
@app.get("/api/v1/jellyfin/now-playing")
async def jellyfin_now_playing(req: Request):
    _require_ui_api_key(req)
    if not JELLYFIN_URL or not JELLYFIN_API_KEY or not httpx:
        return JSONResponse({"sessions": []})
    try:
        base = JELLYFIN_URL.rstrip("/")
        headers = {"X-Emby-Token": JELLYFIN_API_KEY, "X-MediaBrowser-Token": JELLYFIN_API_KEY}
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            r = await client.get(f"{base}/Sessions", headers=headers)
        if r.status_code >= 400:
            return JSONResponse({"sessions": []})
        sessions_raw = r.json() or []
        playing = []
        for s in sessions_raw:
            item = s.get("NowPlayingItem")
            if not item:
                continue
            play_state = s.get("PlayState") or {}
            pos_ticks = play_state.get("PositionTicks") or 0
            dur_ticks = item.get("RunTimeTicks") or 0
            pos_s = int(pos_ticks / 10_000_000) if pos_ticks else 0
            dur_s = int(dur_ticks / 10_000_000) if dur_ticks else 0
            pct = min(100, int(pos_s / dur_s * 100)) if dur_s else 0

            transcode_info = s.get("TranscodingInfo")
            play_method_raw = play_state.get("PlayMethod") or ""
            if transcode_info:
                play_method = "Transcode"
            elif play_method_raw == "DirectStream":
                play_method = "Remux"
            else:
                play_method = "Direct"

            item_id = item.get("Id") or ""
            thumb_url = None
            if item_id:
                thumb_url = f"{base}/Items/{item_id}/Images/Primary?api_key={JELLYFIN_API_KEY}&maxHeight=80&quality=70"

            playing.append({
                "title": item.get("Name") or "",
                "subtitle": item.get("SeriesName") or item.get("AlbumArtist") or "",
                "type": (item.get("Type") or "").lower(),
                "position_s": pos_s,
                "duration_s": dur_s,
                "progress_pct": pct,
                "play_method": play_method,
                "user": s.get("UserName") or "",
                "thumb_url": thumb_url,
            })
        return JSONResponse({"sessions": playing})
    except Exception as e:
        logger.warning("jellyfin_now_playing error: %s", e)
        return JSONResponse({"sessions": []})
# ─────────────────────────────────────────────────────────────────────────────

# ─── YouTube folder list ──────────────────────────────────────────────────────
@app.get("/api/v1/youtube/folders")
async def youtube_folders(req: Request):
    _require_ui_api_key(req)
    folders = []
    try:
        if YOUTUBE_BASE_DIR.exists():
            for child in sorted(YOUTUBE_BASE_DIR.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    folders.append(child.name)
    except Exception:
        pass
    return JSONResponse({"folders": folders})
# ─────────────────────────────────────────────────────────────────────────────

# ─── Music track-info (explicit/clean detection) ──────────────────────────────
_track_info_cache: Dict[str, Any] = {}  # video_id -> {"data": {...}, "ts": float}
_TRACK_INFO_TTL = 3600  # 1 hour

_EXPLICIT_TITLE_RE = re.compile(r'\(explicit\)|[\[\(]explicit[\]\)]|\bexplicit\s+version\b|\s[-–]\s*explicit\b', re.IGNORECASE)
_CLEAN_TITLE_RE    = re.compile(r'\(clean\)|[\[\(]clean[\]\)]|\bclean\s+version\b|\s[-–]\s*clean\b|\bradio\s+edit\b', re.IGNORECASE)

@app.get("/api/v1/music/track-info")
async def music_track_info(req: Request, video_id: str = ""):
    _require_ui_api_key(req)
    video_id = (video_id or "").strip()
    if not video_id or not re.match(r'^[A-Za-z0-9_\-]{6,20}$', video_id):
        raise HTTPException(status_code=400, detail="valid video_id required")

    now = time.time()
    cached = _track_info_cache.get(video_id)
    if cached and (now - cached["ts"]) < _TRACK_INFO_TTL:
        return JSONResponse(cached["data"])

    url = f"https://www.youtube.com/watch?v={video_id}"
    ytdlp_bin = shutil.which("yt-dlp") or "yt-dlp"
    cmd = [ytdlp_bin, "--dump-json", "--no-download", "--quiet", url]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        info = json.loads(stdout) if stdout else {}
    except Exception:
        return JSONResponse({"explicit": None})

    title      = (info.get("title") or "")
    age_limit  = info.get("age_limit") or 0
    tags       = [t.lower() for t in (info.get("tags") or [])]

    explicit: Optional[bool] = None
    if age_limit > 0 or _EXPLICIT_TITLE_RE.search(title) or "explicit" in tags:
        explicit = True
    elif _CLEAN_TITLE_RE.search(title) or "clean" in tags:
        explicit = False

    result = {"explicit": explicit}
    _track_info_cache[video_id] = {"data": result, "ts": now}
    return JSONResponse(result)
# ─────────────────────────────────────────────────────────────────────────────

# ─── User settings ───────────────────────────────────────────────────────────
import re as _re
_SAFE_ID_RE = _re.compile(r"^[A-Za-z0-9\-]{8,64}$")

def _settings_path(jellyfin_id: str) -> Path:
    jid = (jellyfin_id or "").strip()
    if not _SAFE_ID_RE.match(jid):
        raise ValueError(f"Invalid jellyfin_id: '{jid}'")
    return USER_DATA_DIR / jid / "settings.json"

@app.get("/api/v1/user/settings")
async def get_user_settings(req: Request):
    claims = require_auth(req)
    jellyfin_id = claims.get("jellyfin_id") or ""
    if not jellyfin_id:
        raise HTTPException(status_code=400, detail="JWT missing jellyfin_id claim")
    try:
        path = _settings_path(jellyfin_id)
        if not path.exists():
            return JSONResponse({})
        data = json.loads(path.read_text(encoding="utf-8"))
        return JSONResponse({
            "api_key": (data.get("api_key") or "").strip(),
            "nd_user":  (data.get("nd_user")  or "").strip(),
            "nd_pass":  (data.get("nd_pass")  or "").strip(),
        })
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        return JSONResponse({})

@app.post("/api/v1/user/settings")
async def save_user_settings(req: Request):
    claims = require_auth(req)
    jellyfin_id = claims.get("jellyfin_id") or ""
    if not jellyfin_id:
        raise HTTPException(status_code=400, detail="JWT missing jellyfin_id claim")
    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Validate input lengths
    api_key = (body.get("api_key") or "")
    nd_user  = (body.get("nd_user")  or "")
    nd_pass  = (body.get("nd_pass")  or "")
    if len(api_key) > 256 or len(nd_user) > 128 or len(nd_pass) > 512:
        raise HTTPException(status_code=400, detail="Input too long")

    try:
        path = _settings_path(jellyfin_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Merge with existing data so partial updates don't clobber other fields
        existing: Dict[str, Any] = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
        if "api_key" in body:
            existing["api_key"] = api_key.strip()
        if "nd_user" in body:
            existing["nd_user"] = nd_user.strip()
        if "nd_pass" in body:
            existing["nd_pass"] = nd_pass.strip()
        path.write_text(json.dumps(existing), encoding="utf-8")
        return JSONResponse({"ok": True})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
# ─────────────────────────────────────────────────────────────────────────────

from pathlib import Path as _Path
_UI_DIR = _Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")

handler = Mangum(app)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8009)
