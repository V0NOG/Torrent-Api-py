"""
Auth router: login, logout, /me, and Navidrome credential linking.
POST /api/v1/auth/login             -> issues JWT
GET  /api/v1/auth/me                -> returns current user info (from JWT)
POST /api/v1/auth/logout            -> client-side only (JWT is stateless; just acknowledge)
POST /api/v1/auth/navidrome/link    -> validate + store Navidrome credentials for this user
GET  /api/v1/auth/navidrome/status  -> whether this user has linked Navidrome credentials
DELETE /api/v1/auth/navidrome/link  -> remove stored Navidrome credentials
"""
import logging
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from auth.jellyfin_auth import authenticate_jellyfin
from auth.jwt_handler import create_token, verify_token
from auth.navidrome_store import link as nav_link, is_linked, unlink as nav_unlink

logger = logging.getLogger("auth_router")
router = APIRouter()


def _get_token_from_request(req: Request) -> str | None:
    """Extract JWT from Authorization: Bearer <token> header."""
    auth = (req.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def require_auth(req: Request) -> dict:
    """
    Dependency: validates JWT, returns claims.
    Raises 401 if missing/invalid.
    """
    token = _get_token_from_request(req)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    claims = verify_token(token)
    if not claims:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return claims


def require_admin(req: Request) -> dict:
    """
    Dependency: validates JWT + requires admin role.
    """
    claims = require_auth(req)
    if claims.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return claims


@router.post("/login")
async def login(req: Request):
    """
    Authenticate against Jellyfin, return JWT.
    Body: { "username": str, "password": str }
    Never returns Jellyfin token/key to client.
    """
    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    username = (body.get("username") or "").strip()
    password = (body.get("password") or "")

    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password required")

    # Sanitise username (prevent log injection etc.)
    if len(username) > 128 or len(password) > 512:
        raise HTTPException(status_code=400, detail="Input too long")

    try:
        user_info = await authenticate_jellyfin(username, password)
    except ValueError as e:
        logger.warning("Login failed for %s: %s", username, str(e))
        raise HTTPException(status_code=401, detail=str(e))

    role = "admin" if user_info["is_admin"] else "user"

    token = create_token({
        "sub": user_info["username"],
        "display_name": user_info["display_name"],
        "jellyfin_id": user_info["jellyfin_id"],
        "role": role,
    })

    logger.info("Login OK: %s (role=%s)", user_info["username"], role)

    # Return ONLY safe fields to client
    return JSONResponse({
        "success": True,
        "token": token,
        "username": user_info["username"],
        "display_name": user_info["display_name"],
        "role": role,
        # Never return: jellyfin_id in a way that can be misused,
        # Jellyfin API key, JWT_SECRET, passwords
    })


@router.get("/me")
async def me(req: Request):
    """Return current user info from JWT."""
    claims = require_auth(req)
    return JSONResponse({
        "username": claims.get("sub"),
        "display_name": claims.get("display_name"),
        "role": claims.get("role"),
    })


@router.post("/logout")
async def logout(req: Request):
    """
    JWT is stateless — logout is handled client-side by discarding the token.
    This endpoint just acknowledges the request cleanly.
    """
    return JSONResponse({"success": True, "message": "Logged out"})


@router.post("/navidrome/link")
async def navidrome_link(req: Request):
    """
    Validate and store Navidrome credentials for the authenticated user.
    Body: { "nav_user": str, "nav_pass": str }
    Credentials are validated against the Navidrome Subsonic ping endpoint
    before being written to disk.
    """
    claims = require_auth(req)
    jellyfin_id = claims.get("jellyfin_id") or ""
    if not jellyfin_id:
        raise HTTPException(status_code=400, detail="JWT missing jellyfin_id claim")

    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    nav_user = (body.get("nav_user") or "").strip()
    nav_pass = (body.get("nav_pass") or "").strip()

    if not nav_user or not nav_pass:
        raise HTTPException(status_code=400, detail="nav_user and nav_pass required")
    if len(nav_user) > 128 or len(nav_pass) > 512:
        raise HTTPException(status_code=400, detail="Input too long")

    try:
        ok = await nav_link(jellyfin_id, nav_user, nav_pass)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not ok:
        raise HTTPException(status_code=401, detail="Invalid Navidrome credentials")

    logger.info("Navidrome linked for jellyfin_id=%s nav_user=%s", jellyfin_id, nav_user)
    return JSONResponse({"success": True, "nav_user": nav_user})


@router.get("/navidrome/status")
async def navidrome_status(req: Request):
    """Return whether the authenticated user has linked Navidrome credentials."""
    claims = require_auth(req)
    jellyfin_id = claims.get("jellyfin_id") or ""
    if not jellyfin_id:
        raise HTTPException(status_code=400, detail="JWT missing jellyfin_id claim")

    linked = is_linked(jellyfin_id)
    return JSONResponse({"linked": linked})


@router.delete("/navidrome/link")
async def navidrome_unlink(req: Request):
    """Remove stored Navidrome credentials for the authenticated user."""
    claims = require_auth(req)
    jellyfin_id = claims.get("jellyfin_id") or ""
    if not jellyfin_id:
        raise HTTPException(status_code=400, detail="JWT missing jellyfin_id claim")

    nav_unlink(jellyfin_id)
    logger.info("Navidrome unlinked for jellyfin_id=%s", jellyfin_id)
    return JSONResponse({"success": True})
