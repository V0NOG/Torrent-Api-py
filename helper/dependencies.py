import os

from fastapi import Security, HTTPException, status, Request
from fastapi.security import APIKeyHeader

api_key = os.environ.get("PYTORRENT_API_KEY")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def authenticate_request(
    request: Request,
    x_api_key: str = Security(api_key_header),
):
    """
    Accepts either:
    - X-API-Key header matching PYTORRENT_API_KEY, or
    - Authorization: Bearer <jwt> header with a valid JWT
    """
    # 1. API key check
    if x_api_key and api_key and x_api_key == api_key:
        return

    # 2. JWT Bearer check
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            from auth.jwt_handler import verify_token
            if verify_token(token):
                return
        except Exception:
            pass

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Access forbidden: Incorrect credentials."
    )
