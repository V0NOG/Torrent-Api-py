"""
JWT creation and verification for local sessions.
Uses HS256 with a configurable secret.
"""
import os
import time
import json
import hmac
import hashlib
import base64
import secrets as _secrets
from typing import Optional, Dict, Any

JWT_SECRET = (os.getenv("JWT_SECRET", "") or "").strip()
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    # Re-pad
    s = s + "=" * (4 - len(s) % 4)
    return base64.urlsafe_b64decode(s.encode("ascii"))


def _check_secret():
    if not JWT_SECRET:
        raise RuntimeError(
            "JWT_SECRET env var is not set. "
            "Generate one with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
        )


def create_token(payload: Dict[str, Any], expire_minutes: Optional[int] = None) -> str:
    """
    Create a signed HS256 JWT with expiry.
    payload should include at minimum: sub (username), role, jellyfin_id.
    """
    _check_secret()
    exp_min = expire_minutes if expire_minutes is not None else JWT_EXPIRE_MINUTES
    now = int(time.time())
    claims = {
        **payload,
        "iat": now,
        "exp": now + exp_min * 60,
        "jti": _secrets.token_hex(8),
    }
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64url_encode(json.dumps(claims).encode())
    signing_input = f"{header}.{body}".encode("ascii")
    sig = hmac.new(JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header}.{body}.{_b64url_encode(sig)}"


def verify_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Verify and decode a JWT. Returns claims dict or None if invalid/expired.
    """
    if not JWT_SECRET:
        return None
    try:
        parts = (token or "").strip().split(".")
        if len(parts) != 3:
            return None
        header_b64, body_b64, sig_b64 = parts
        signing_input = f"{header_b64}.{body_b64}".encode("ascii")
        expected_sig = hmac.new(
            JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256
        ).digest()
        provided_sig = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected_sig, provided_sig):
            return None
        claims = json.loads(_b64url_decode(body_b64).decode("utf-8"))
        if int(time.time()) > claims.get("exp", 0):
            return None
        return claims
    except Exception:
        return None
