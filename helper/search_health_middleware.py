"""
helper/search_health_middleware.py

Starlette middleware that intercepts /api/v1/search responses and:
  1. Decodes the JSON body
  2. Runs it through site_health.wrap_search_response()
  3. Returns the filtered response

This is the recommended integration approach — it doesn't require
modifying upstream router code at all.

Register it in main.py AFTER creating the app and BEFORE mounting static files:

    from helper.search_health_middleware import SearchHealthMiddleware
    app.add_middleware(SearchHealthMiddleware)
"""

import json
import logging
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from helper.site_health import wrap_search_response, get_all_statuses, is_site_disabled, get_site_status

logger = logging.getLogger("search_health_middleware")

_SEARCH_PATH = "/api/v1/search"
_SITE_STATUS_PATH = "/api/v1/search/site-status"


class SearchHealthMiddleware(BaseHTTPMiddleware):
    timeout = 120
    """
    Intercepts GET /api/v1/search responses to filter block-page results
    and inject structured site health data.

    Also adds a synthetic GET /api/v1/search/site-status endpoint.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path.rstrip("/")

        # ── Synthetic site-status endpoint ──
        if path == _SITE_STATUS_PATH and request.method == "GET":
            return Response(
                content=json.dumps({"sites": get_all_statuses()}),
                media_type="application/json",
            )

        # ── Only intercept GET /api/v1/search ──
        if path != _SEARCH_PATH or request.method != "GET":
            return await call_next(request)

        site = (request.query_params.get("site") or "piratebay").strip().lower()

        # Fast-path: site is disabled — don't even hit upstream
        if is_site_disabled(site):
            st = get_site_status(site)
            body = json.dumps({
                "data": [],
                "error": (
                    f"Site '{site}' is temporarily disabled after repeated failures. "
                    f"Reason: {st.get('reason', 'unknown')}. "
                    "It will be retried automatically after 30 minutes."
                ),
                "error_code": "disabled",
                "site_status": st,
            })
            return Response(content=body, status_code=503, media_type="application/json")

        # Call the upstream handler
        try:
            upstream_response = await call_next(request)
        except Exception as e:
            logger.error("SearchHealthMiddleware: upstream exception site=%s: %s", site, e)
            from helper.site_health import record_failure, _classify_error_message
            reason = _classify_error_message(str(e))
            record_failure(site, reason)
            body = json.dumps({
                "data": [],
                "error": f"Search request failed: {type(e).__name__}",
                "error_code": reason,
            })
            return Response(content=body, status_code=502, media_type="application/json")

        # Read response body
        raw_body = b""
        async for chunk in upstream_response.body_iterator:
            raw_body += chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")

        # Only process JSON responses
        content_type = upstream_response.headers.get("content-type", "")
        if "application/json" not in content_type:
            # Pass through non-JSON unchanged (shouldn't happen for search)
            return Response(
                content=raw_body,
                status_code=upstream_response.status_code,
                headers=dict(upstream_response.headers),
                media_type=content_type,
            )

        # Decode and filter
        try:
            api_data = json.loads(raw_body)
        except Exception as e:
            logger.error("SearchHealthMiddleware: JSON decode error: %s", e)
            return Response(
                content=raw_body,
                status_code=upstream_response.status_code,
                media_type=content_type,
            )

        filtered = wrap_search_response(site, api_data, http_status=upstream_response.status_code)

        out_status = upstream_response.status_code
        error_code = filtered.get("error_code", "")
        if error_code in ("blocked", "disabled", "domain_dead", "timeout"):
            out_status = 503
        elif error_code in ("parse_failed", "error"):
            out_status = 502

        return Response(
            content=json.dumps(filtered),
            status_code=out_status,
            media_type="application/json",
        )
