"""
Auth router: login, logout, /me
POST /api/v1/auth/login   -> issues JWT
GET  /api/v1/auth/me      -> returns current user info (from JWT)
POST /api/v1/auth/logout  -> client-side only (JWT is stateless; just acknowledge)
"""
import logging
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from auth.jellyfin_auth import authenticate_jellyfin
from auth.jwt_handler import create_token, verify_token

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
