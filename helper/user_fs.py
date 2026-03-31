"""
user_fs.py — per-user music folder management.

Folder layout:
  /mnt/media/Music/Users/<sanitized_username>/

Rules:
  - Username is sanitized to alphanumeric + hyphen/underscore, max 32 chars
  - Folder is created on first use
  - All path operations are validated against the user's root (no traversal)
  - No access to other users' folders or any path outside music root
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import List, Optional

MUSIC_ROOT = Path(os.getenv("MUSICREQ_HOST_MUSIC_ROOT", "/mnt/media/Music"))
USERS_ROOT = MUSIC_ROOT / "Users"


def sanitize_username(username: str) -> str:
    """
    Convert a Navidrome username to a safe folder name.
    Keeps letters, digits, hyphens, underscores. Max 32 chars.
    """
    s = (username or "").strip().lower()
    s = re.sub(r"[^a-z0-9_\-]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    s = s[:32]
    if not s:
        raise ValueError(f"Username '{username}' produces an empty folder name after sanitization")
    return s


def user_folder(username: str) -> Path:
    """Return the Path for this user's music folder (does not create it)."""
    safe = sanitize_username(username)
    return USERS_ROOT / safe


def ensure_user_folder(username: str) -> Path:
    """Create the user's folder if it doesn't exist. Return the Path."""
    p = user_folder(username)
    p.mkdir(parents=True, exist_ok=True)
    # Correct permissions
    try:
        p.chmod(0o755)
    except Exception:
        pass
    return p


def _assert_within(path: Path, root: Path) -> Path:
    """
    Resolve path and ensure it is within root.
    Raises ValueError on traversal attempt.
    """
    try:
        resolved = path.resolve()
        root_resolved = root.resolve()
        resolved.relative_to(root_resolved)  # raises ValueError if outside
        return resolved
    except ValueError:
        raise ValueError(f"Path '{path}' is outside allowed root '{root}'")


def list_folder(username: str, subpath: str = "") -> List[dict]:
    """
    List files and subdirectories in the user's folder (or a subdirectory).
    Returns list of dicts with keys: name, type, size, mtime, path (relative to user root).
    """
    root = ensure_user_folder(username)
    if subpath:
        target = _assert_within(root / subpath, root)
    else:
        target = root

    if not target.is_dir():
        raise ValueError(f"Not a directory: {subpath}")

    items: List[dict] = []
    for entry in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        try:
            stat = entry.stat()
            rel = str(entry.relative_to(root))
            items.append({
                "name": entry.name,
                "type": "file" if entry.is_file() else "dir",
                "size": stat.st_size if entry.is_file() else None,
                "mtime": stat.st_mtime,
                "path": rel,
            })
        except Exception:
            continue
    return items


def rename_item(username: str, rel_path: str, new_name: str) -> str:
    """
    Rename a file or folder within the user's folder.
    rel_path: path relative to user root.
    new_name: new basename only (no slashes).
    Returns the new relative path.
    """
    root = ensure_user_folder(username)
    source = _assert_within(root / rel_path, root)

    # Validate new_name
    new_name = new_name.strip()
    if not new_name or "/" in new_name or "\\" in new_name or new_name in (".", ".."):
        raise ValueError(f"Invalid new name: '{new_name}'")
    # Strip dangerous chars
    new_name = re.sub(r'[<>:"|?*\x00-\x1f]', '', new_name).strip()
    if not new_name:
        raise ValueError("New name is empty after sanitization")

    dest = source.parent / new_name
    _assert_within(dest, root)  # still within root

    if dest.exists() and dest != source:
        raise FileExistsError(f"A file named '{new_name}' already exists here")

    source.rename(dest)
    return str(dest.relative_to(root))


def delete_item(username: str, rel_path: str) -> None:
    """
    Delete a file or folder within the user's folder.
    Directories are deleted recursively.
    """
    root = ensure_user_folder(username)
    target = _assert_within(root / rel_path, root)

    # Safety: never delete the root itself
    if target == root:
        raise ValueError("Cannot delete user root folder")

    if target.is_dir():
        shutil.rmtree(str(target))
    else:
        target.unlink()


def user_download_path(username: str) -> Path:
    """Return the path where downloaded songs for this user should land."""
    return ensure_user_folder(username)
