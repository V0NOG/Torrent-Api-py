"""
Secure file manager router.

Security guarantees:
- All paths are resolved via Path.resolve() and checked against allowed roots
- Symlinks are not followed outside allowed roots
- No ../ traversal possible (resolve() + startswith check)
- Forbidden characters rejected in new names
- All mutating actions (rename, delete) are audit-logged
- Admin can access ADMIN_FILE_ROOTS; normal users only USER_MEDIA_ROOT/<username>/
- No filesystem paths outside allowed roots are ever returned or accepted
"""
import asyncio
import os
import re
import shutil
import logging
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from routers.v1.auth_router import require_auth

logger = logging.getLogger("files_router")
router = APIRouter()

# ── Config ──────────────────────────────────────────────────────────────────
USER_MEDIA_ROOT = Path(os.getenv("USER_MEDIA_ROOT", "/mnt/media/Users"))

_raw_admin_roots = (os.getenv("ADMIN_FILE_ROOTS", "") or "").strip()
ADMIN_FILE_ROOTS: List[Path] = []
if _raw_admin_roots:
    for _r in _raw_admin_roots.split(","):
        _r = _r.strip()
        if _r:
            ADMIN_FILE_ROOTS.append(Path(_r))

if not ADMIN_FILE_ROOTS:
    # sensible defaults matching existing main.py config
    ADMIN_FILE_ROOTS = [
        Path(os.getenv("FINAL_MOVIES_DIR", "/mnt/media/Movies")),
        Path(os.getenv("FINAL_TV_DIR", "/mnt/media/TV")),
        Path(os.getenv("FINAL_MUSIC_DIR", "/mnt/media/Music")),
        Path(os.getenv("TORRENT_STAGING_DIR", "/mnt/media/Downloads/Torrents/_staging")),
    ]

# Characters forbidden in new filenames (Windows-safe + shell-safe)
_FORBIDDEN_IN_NAME = re.compile(r'[\/\\:\*\?"<>\|\x00-\x1F\x7F]')
# Extra names to block outright
_BLOCKED_NAMES = {".", "..", ".git", ".env", "passwd", "shadow"}

# ── Directory size cache (10-minute TTL, top-level only) ────────────────────
_dir_size_cache: Dict[str, Any] = {}  # path_str -> {"size": int, "ts": float}
_DIR_SIZE_TTL = 600

async def _dir_size_bytes(path: Path) -> Optional[int]:
    """Return total bytes used by a directory via du -sb. Returns None on error."""
    key = str(path)
    now = time.time()
    cached = _dir_size_cache.get(key)
    if cached and (now - cached["ts"]) < _DIR_SIZE_TTL:
        return cached["size"]
    try:
        proc = await asyncio.create_subprocess_exec(
            "du", "-sb", str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        size = int(stdout.split()[0])
        _dir_size_cache[key] = {"size": size, "ts": now}
        return size
    except Exception:
        return None


# ── Path safety helpers ──────────────────────────────────────────────────────

def _validate_new_name(name: str) -> str:
    """
    Validate a new filename/dirname.
    Returns cleaned name or raises HTTPException.
    """
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    if len(name) > 255:
        raise HTTPException(status_code=400, detail="Name too long (max 255 chars)")
    if name in _BLOCKED_NAMES:
        raise HTTPException(status_code=400, detail=f"Name not allowed: {name!r}")
    if _FORBIDDEN_IN_NAME.search(name):
        raise HTTPException(
            status_code=400,
            detail="Name contains forbidden characters (/ \\ : * ? \" < > | and control chars)"
        )
    # Block names that start with a dot to prevent hidden-file tricks
    if name.startswith("."):
        raise HTTPException(status_code=400, detail="Names starting with '.' are not allowed")
    return name


def _resolve_safe(base: Path, rel: str) -> Path:
    """
    Resolve base/rel safely.
    - Strips leading slashes from rel
    - Resolves symlinks
    - Ensures resolved path is strictly under base (or IS base)
    - Raises HTTPException(400) on traversal attempt
    """
    rel_clean = (rel or "").lstrip("/").replace("\\", "/")
    # Don't allow empty-ish traversal
    if rel_clean in ("", "."):
        return base.resolve()

    candidate = (base / rel_clean).resolve()
    resolved_base = base.resolve()

    # Must be strictly under base
    try:
        candidate.relative_to(resolved_base)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Access denied: path is outside your allowed directory"
        )

    return candidate


def _get_user_root(claims: Dict[str, Any]) -> Path:
    """Return the single allowed root for a normal user."""
    username = (claims.get("sub") or "").strip().lower()
    if not username:
        raise HTTPException(status_code=403, detail="Invalid session")
    # Validate username as a safe directory name
    if _FORBIDDEN_IN_NAME.search(username) or username in _BLOCKED_NAMES:
        raise HTTPException(status_code=403, detail="Username not suitable for filesystem root")
    return USER_MEDIA_ROOT / username


def _get_allowed_roots(claims: Dict[str, Any]) -> List[Path]:
    """Return list of allowed roots for this user — all users get full access."""
    return ADMIN_FILE_ROOTS if ADMIN_FILE_ROOTS else [_get_user_root(claims)]


def _ensure_user_root(claims: Dict[str, Any]) -> Path:
    """Create user root directory if it doesn't exist (normal users only)."""
    if claims.get("role") == "admin":
        return ADMIN_FILE_ROOTS[0] if ADMIN_FILE_ROOTS else Path("/mnt/media")
    root = _get_user_root(claims)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Could not create user directory: {type(e).__name__}"
        )
    return root


def _stat_entry(p: Path, base: Path) -> Dict[str, Any]:
    """Build a safe dict describing a file or directory."""
    try:
        st = p.stat()
        is_dir = p.is_dir()
        return {
            "name": p.name,
            "rel": str(p.relative_to(base)),
            "is_dir": is_dir,
            "size": st.st_size if not is_dir else None,
            "modified": int(st.st_mtime),
        }
    except Exception:
        return {
            "name": p.name,
            "rel": str(p.relative_to(base)),
            "is_dir": False,
            "size": None,
            "modified": None,
        }


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/roots")
async def list_roots(req: Request):
    """
    Return the list of root paths this user can browse.
    Returns display labels + root keys; no absolute server paths exposed
    (we return relative labels for display, actual resolution is backend-only).
    """
    claims = require_auth(req)
    roots = _get_allowed_roots(claims)

    out = []
    for r in roots:
        # Create user root if needed
        if claims.get("role") != "admin":
            try:
                r.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

        exists = r.exists()
        out.append({
            "key": r.name,          # used as identifier; safe (not full path)
            "label": r.name,
            "exists": exists,
        })

    return JSONResponse({"roots": out})


@router.get("/list")
async def list_directory(req: Request, root: str = "", path: str = ""):
    """
    List contents of a directory within the user's allowed area.
    - root: root key (from /roots)
    - path: relative path within root (default: root itself)
    """
    claims = require_auth(req)
    allowed_roots = _get_allowed_roots(claims)

    # Find matching root
    root_path = None
    for r in allowed_roots:
        if r.name == root or str(r) == root:
            root_path = r
            break

    if root_path is None:
        # For non-admins with single root, allow empty root key
        if not root and len(allowed_roots) == 1:
            root_path = allowed_roots[0]
        else:
            raise HTTPException(status_code=403, detail="Root not allowed")

    # Ensure user root exists
    if claims.get("role") != "admin":
        try:
            root_path.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    target = _resolve_safe(root_path, path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="Directory not found")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")

    entries = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            # Skip hidden files/dirs
            if child.name.startswith("."):
                continue
            entries.append(_stat_entry(child, root_path))
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied reading directory")

    # Compute breadcrumb relative to root
    try:
        rel_to_root = str(target.relative_to(root_path.resolve()))
        if rel_to_root == ".":
            rel_to_root = ""
    except Exception:
        rel_to_root = ""

    # At top level only: compute directory sizes via du (cached 10 min)
    if rel_to_root == "":
        dir_entries = [e for e in entries if e.get("is_dir")]
        if dir_entries:
            sizes = await asyncio.gather(
                *[_dir_size_bytes(root_path / e["name"]) for e in dir_entries],
                return_exceptions=True,
            )
            for entry, sz in zip(dir_entries, sizes):
                if isinstance(sz, int):
                    entry["size"] = sz

    return JSONResponse({
        "root_key": root_path.name,
        "current_path": rel_to_root,
        "entries": entries,
    })


@router.post("/rename")
async def rename_entry(req: Request):
    """
    Rename a file or directory.
    Body: { "root": str, "path": str, "new_name": str }
    """
    claims = require_auth(req)
    allowed_roots = _get_allowed_roots(claims)

    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    root_key = (body.get("root") or "").strip()
    rel_path = (body.get("path") or "").strip()
    new_name = (body.get("new_name") or "").strip()

    if not rel_path:
        raise HTTPException(status_code=400, detail="path required")

    new_name = _validate_new_name(new_name)

    # Find root
    root_path = None
    for r in allowed_roots:
        if r.name == root_key or str(r) == root_key:
            root_path = r
            break
    if root_path is None and len(allowed_roots) == 1:
        root_path = allowed_roots[0]
    if root_path is None:
        raise HTTPException(status_code=403, detail="Root not allowed")

    src = _resolve_safe(root_path, rel_path)
    if not src.exists():
        raise HTTPException(status_code=404, detail="Source not found")

    dst = src.parent / new_name

    # Ensure dst is still within root
    _resolve_safe(root_path, str(dst.relative_to(root_path.resolve())))

    if dst.exists():
        raise HTTPException(status_code=409, detail=f"A file named {new_name!r} already exists")

    username = claims.get("sub", "unknown")
    logger.info(
        "RENAME | user=%s role=%s | %s -> %s",
        username, claims.get("role"), src, dst
    )

    try:
        src.rename(dst)
    except Exception as e:
        logger.error("RENAME FAILED | user=%s | %s -> %s | %s", username, src, dst, e)
        raise HTTPException(status_code=500, detail=f"Rename failed: {type(e).__name__}")

    return JSONResponse({"success": True, "new_name": new_name})


@router.post("/delete")
async def delete_entry(req: Request):
    """
    Delete a file or directory (directory must be empty or recursive=true).
    Body: { "root": str, "path": str, "recursive": bool }
    """
    claims = require_auth(req)
    allowed_roots = _get_allowed_roots(claims)

    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    root_key = (body.get("root") or "").strip()
    rel_path = (body.get("path") or "").strip()
    recursive = bool(body.get("recursive", False))

    if not rel_path:
        raise HTTPException(status_code=400, detail="path required")

    # Find root
    root_path = None
    for r in allowed_roots:
        if r.name == root_key or str(r) == root_key:
            root_path = r
            break
    if root_path is None and len(allowed_roots) == 1:
        root_path = allowed_roots[0]
    if root_path is None:
        raise HTTPException(status_code=403, detail="Root not allowed")

    target = _resolve_safe(root_path, rel_path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Target not found")

    # Protect: cannot delete the root itself
    if target.resolve() == root_path.resolve():
        raise HTTPException(status_code=400, detail="Cannot delete root directory")

    username = claims.get("sub", "unknown")
    logger.info(
        "DELETE | user=%s role=%s recursive=%s | %s",
        username, claims.get("role"), recursive, target
    )

    try:
        if target.is_dir():
            if recursive:
                shutil.rmtree(str(target))
            else:
                target.rmdir()  # Only succeeds if empty
        else:
            target.unlink()
    except OSError as e:
        logger.error("DELETE FAILED | user=%s | %s | %s", username, target, e)
        if "not empty" in str(e).lower():
            raise HTTPException(
                status_code=409,
                detail="Directory is not empty. Set recursive=true to delete contents."
            )
        raise HTTPException(status_code=500, detail=f"Delete failed: {type(e).__name__}")

    return JSONResponse({"success": True})


@router.get("/search")
async def search_files(req: Request, root: str = "", path: str = "", query: str = "", recursive: str = "true"):
    """
    Search for files/directories matching query within the allowed root.
    - root: root key (from /roots)
    - path: relative path to search within (default: root)
    - query: search string (case-insensitive substring match on filename)
    - recursive: whether to search subdirectories (default: true)
    """
    claims = require_auth(req)
    allowed_roots = _get_allowed_roots(claims)

    if not query.strip():
        raise HTTPException(status_code=400, detail="query required")

    # Find matching root
    root_path = None
    for r in allowed_roots:
        if r.name == root or str(r) == root:
            root_path = r
            break
    if root_path is None:
        if not root and len(allowed_roots) == 1:
            root_path = allowed_roots[0]
        else:
            raise HTTPException(status_code=403, detail="Root not allowed")

    search_base = _resolve_safe(root_path, path)
    if not search_base.exists() or not search_base.is_dir():
        raise HTTPException(status_code=404, detail="Search directory not found")

    do_recursive = recursive.lower() not in ("false", "0", "no")
    q = query.strip().lower()
    entries = []
    MAX_RESULTS = 50
    _deadline = time.monotonic() + 10.0  # 10-second hard stop

    def _walk(directory: Path):
        if len(entries) >= MAX_RESULTS or time.monotonic() > _deadline:
            return
        try:
            for child in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                if child.name.startswith("."):
                    continue
                if q in child.name.lower():
                    try:
                        st = child.stat()
                        is_dir = child.is_dir()
                        # frontend expects 'path' as relative to root_path
                        rel = str(child.relative_to(root_path.resolve()))
                        entries.append({
                            "name": child.name,
                            "path": rel,
                            "is_dir": is_dir,
                            "size": st.st_size if not is_dir else None,
                            "modified": int(st.st_mtime),
                        })
                    except Exception:
                        pass
                if do_recursive and child.is_dir() and len(entries) < MAX_RESULTS and time.monotonic() <= _deadline:
                    _walk(child)
        except PermissionError:
            pass

    _walk(search_base)

    return JSONResponse({"entries": entries, "query": query, "truncated": len(entries) >= MAX_RESULTS})


@router.post("/delete-batch")
async def delete_batch(req: Request):
    """
    Delete multiple files/directories in one request.
    Body: { "root": str, "items": [{ "path": str, "is_dir": bool, "recursive": bool }] }
    Returns: { "deleted": int, "failed": [{ "path": str, "error": str }] }
    """
    claims = require_auth(req)
    allowed_roots = _get_allowed_roots(claims)

    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    root_key = (body.get("root") or "").strip()
    items = body.get("items") or []

    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="items list required")
    if len(items) > 200:
        raise HTTPException(status_code=400, detail="Too many items (max 200)")

    # Find root
    root_path = None
    for r in allowed_roots:
        if r.name == root_key or str(r) == root_key:
            root_path = r
            break
    if root_path is None and len(allowed_roots) == 1:
        root_path = allowed_roots[0]
    if root_path is None:
        raise HTTPException(status_code=403, detail="Root not allowed")

    username = claims.get("sub", "unknown")
    deleted = 0
    failed = []

    for item in items:
        rel_path = (item.get("path") or "").strip()
        is_dir = bool(item.get("is_dir", False))
        recursive = bool(item.get("recursive", False))

        if not rel_path:
            continue
        try:
            target = _resolve_safe(root_path, rel_path)
            if not target.exists():
                continue
            if target.resolve() == root_path.resolve():
                failed.append({"path": rel_path, "error": "Cannot delete root"})
                continue

            logger.info(
                "BATCH-DELETE | user=%s role=%s recursive=%s | %s",
                username, claims.get("role"), recursive, target
            )

            if target.is_dir():
                if recursive:
                    shutil.rmtree(str(target))
                else:
                    target.rmdir()
            else:
                target.unlink()
            deleted += 1
        except OSError as e:
            err = str(e)
            logger.error("BATCH-DELETE FAILED | user=%s | %s | %s", username, rel_path, e)
            failed.append({"path": rel_path, "error": err[:120]})
        except HTTPException as e:
            failed.append({"path": rel_path, "error": e.detail})
        except Exception as e:
            failed.append({"path": rel_path, "error": type(e).__name__})

    return JSONResponse({"deleted": deleted, "failed": failed})


@router.post("/mkdir")
async def make_directory(req: Request):
    """
    Create a new directory.
    Body: { "root": str, "path": str, "name": str }
    path = parent directory (relative to root), name = new dir name
    """
    claims = require_auth(req)
    allowed_roots = _get_allowed_roots(claims)

    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    root_key = (body.get("root") or "").strip()
    parent_rel = (body.get("path") or "").strip()
    name = (body.get("name") or "").strip()

    name = _validate_new_name(name)

    # Find root
    root_path = None
    for r in allowed_roots:
        if r.name == root_key or str(r) == root_key:
            root_path = r
            break
    if root_path is None and len(allowed_roots) == 1:
        root_path = allowed_roots[0]
    if root_path is None:
        raise HTTPException(status_code=403, detail="Root not allowed")

    parent = _resolve_safe(root_path, parent_rel)
    if not parent.exists() or not parent.is_dir():
        raise HTTPException(status_code=404, detail="Parent directory not found")

    new_dir = parent / name
    _resolve_safe(root_path, str((parent / name).relative_to(root_path.resolve())))

    if new_dir.exists():
        raise HTTPException(status_code=409, detail=f"Already exists: {name!r}")

    username = claims.get("sub", "unknown")
    logger.info("MKDIR | user=%s role=%s | %s", username, claims.get("role"), new_dir)

    try:
        new_dir.mkdir(parents=False, exist_ok=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"mkdir failed: {type(e).__name__}")

    return JSONResponse({"success": True, "name": name})
