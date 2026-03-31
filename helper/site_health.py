"""
helper/site_health.py

Intercepts and classifies "Website Blocked" and other site failure responses
from Torrent-Api-py scrapers. Provides:
  - Response content sniffing to detect block pages before they hit the UI
  - Per-site failure tracking with automatic degraded/disabled marking
  - Structured status codes: ok | blocked | timeout | parse_failed | domain_dead | no_results
  - In-memory TTL cache so consecutive failures disable a site temporarily
  - A clean status API the frontend can query

This module is pure Python with no extra dependencies (uses stdlib only).
It patches into your existing search flow via wrap_search_result().
"""

import re
import time
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("site_health")

# ── Constants ────────────────────────────────────────────────────────────────

# Phrases that appear in block/error pages returned by ISPs or sites themselves.
# These are checked against the *title* or *name* field of each result item,
# and also against raw response text when we can intercept it.
_BLOCK_PHRASES: List[str] = [
    # Torrent-Api-py upstream exact strings
    "website blocked change ip or website domain",
    "website blocked / scraper failed",
    "website blocked",
    "scraper failed",
    "change ip or website domain",
    "change ip",
    # Generic block/access phrases
    "access denied",
    "access forbidden",
    "403 forbidden",
    "this site is blocked",
    "domain has been blocked",
    "blocked by your isp",
    "blocked in your country",
    "your ip has been blocked",
    "ip has been blocked",
    "site is not available",
    "not available in your country",
    # Cloudflare / DDoS protection pages leaking through
    "cloudflare",
    "cf-browser-verification",
    "just a moment",       # CF "Just a moment..." challenge page
    "ddos-guard",
    "ddos protection",
    "please enable javascript",
    "enable javascript and cookies",
    # Generic error pages
    "this page is not available",
    "page not available",
    "page not found",
    "404 not found",
    "502 bad gateway",
    "503 service unavailable",
    "site not available",
    "cannot be reached",
    "connection refused",
]

# Phrases that mean "no results" — NOT a block, NOT a failure
_NO_RESULTS_PHRASES: List[str] = [
    "result not found",
    "no results",
    "selected site not available",  # site key typo/unknown — not a network block
]

# If a result's title/name matches these (case-insensitive) it is a block result
_BLOCK_RE = re.compile(
    "|".join(re.escape(p) for p in _BLOCK_PHRASES),
    re.IGNORECASE,
)

_NO_RESULTS_RE = re.compile(
    "|".join(re.escape(p) for p in _NO_RESULTS_PHRASES),
    re.IGNORECASE,
)

# Failure tracking: { site_key: [timestamp, ...] }
_FAILURE_TIMES: Dict[str, List[float]] = {}
_SITE_STATUS: Dict[str, Dict[str, Any]] = {}   # { site_key: { status, reason, last_fail, fail_count } }

# How many consecutive failures before a site is marked "degraded" or "disabled"
_DEGRADED_THRESHOLD = 2
_DISABLED_THRESHOLD = 5

# How long (seconds) to remember failures for TTL-based auto-recovery
_FAILURE_WINDOW_SEC = 600   # 10 minutes

# How long (seconds) a disabled site stays disabled before being retried
_DISABLED_TTL_SEC = 1800    # 30 minutes


# ── Core logic ───────────────────────────────────────────────────────────────

def _now() -> float:
    return time.time()


def _trim_failures(site: str) -> List[float]:
    cutoff = _now() - _FAILURE_WINDOW_SEC
    times = [t for t in _FAILURE_TIMES.get(site, []) if t >= cutoff]
    _FAILURE_TIMES[site] = times
    return times


def record_failure(site: str, reason: str) -> None:
    """Record a failure event for a site and update its status."""
    times = _trim_failures(site)
    times.append(_now())
    _FAILURE_TIMES[site] = times

    count = len(times)
    if count >= _DISABLED_THRESHOLD:
        new_status = "disabled"
    elif count >= _DEGRADED_THRESHOLD:
        new_status = "degraded"
    else:
        new_status = "degraded"

    _SITE_STATUS[site] = {
        "status": new_status,
        "reason": reason,
        "last_fail": _now(),
        "fail_count": count,
    }
    logger.warning("site_health: %s -> %s (%s) [%d failures]", site, new_status, reason, count)


def record_success(site: str) -> None:
    """Record a successful search for a site, partially clearing failure count."""
    _FAILURE_TIMES[site] = []
    _SITE_STATUS[site] = {
        "status": "ok",
        "reason": None,
        "last_fail": None,
        "fail_count": 0,
    }


def get_site_status(site: str) -> Dict[str, Any]:
    """Return current health status for a site."""
    st = _SITE_STATUS.get(site)
    if not st:
        return {"status": "unknown", "reason": None, "fail_count": 0}

    # Auto-recover disabled sites after TTL
    if st["status"] == "disabled":
        last = st.get("last_fail") or 0
        if (_now() - last) > _DISABLED_TTL_SEC:
            _SITE_STATUS[site] = {"status": "unknown", "reason": "auto-recovered", "fail_count": 0}
            _FAILURE_TIMES[site] = []
            return _SITE_STATUS[site]

    return st


def get_all_statuses() -> Dict[str, Dict[str, Any]]:
    """Return health status for all known sites."""
    all_sites = set(_SITE_STATUS.keys()) | set(_FAILURE_TIMES.keys())
    return {s: get_site_status(s) for s in all_sites}


def is_site_disabled(site: str) -> bool:
    """True if site is currently marked disabled (too many failures)."""
    return get_site_status(site).get("status") == "disabled"


# ── Result sniffing ──────────────────────────────────────────────────────────

def _is_block_text(text: str) -> bool:
    """Return True if text looks like a block/error page rather than a real result."""
    if not text:
        return False
    # If it matches a no-results phrase, it's NOT a block — it's just empty
    if _NO_RESULTS_RE.search(text):
        return False
    return bool(_BLOCK_RE.search(text))


def classify_results(site: str, raw_results: Any) -> Dict[str, Any]:
    """
    Given the raw data returned by a Torrent-Api-py search adapter,
    classify it and return a structured response:

    {
        "ok": bool,
        "status": "ok" | "blocked" | "no_results" | "parse_failed" | "error",
        "reason": str | None,
        "data": [...],   # cleaned results (block items removed)
    }

    Also updates site health tracking.
    """
    # None data: could mean auth failure passed through, or a genuinely broken
    # adapter. We only record this as a failure if we're sure it's a site issue
    # (the auth-failure guard in wrap_search_response handles the auth case first).
    if raw_results is None:
        # Don't record as site failure — could be upstream app error, not site problem.
        return {"ok": True, "status": "no_results", "reason": "Adapter returned no data", "data": []}

    if isinstance(raw_results, dict) and "error" in raw_results:
        err = str(raw_results["error"])
        reason = _classify_error_message(err)
        record_failure(site, reason)
        return {"ok": False, "status": reason, "reason": err, "data": []}

    if not isinstance(raw_results, list):
        record_failure(site, "parse_failed")
        return {"ok": False, "status": "parse_failed", "reason": f"Unexpected type: {type(raw_results).__name__}", "data": []}

    if len(raw_results) == 0:
        # Genuine empty result — valid search, site just has no matches.
        # Never penalise for this.
        return {"ok": True, "status": "no_results", "reason": None, "data": []}

    # Filter out block-page items
    clean = []
    blocked_count = 0
    for item in raw_results:
        title = str(item.get("name") or item.get("title") or "")
        if _is_block_text(title):
            blocked_count += 1
            logger.warning(
                "site_health: filtered block-page result from %s: %r",
                site, title[:120],
            )
        else:
            clean.append(item)

    if blocked_count > 0 and len(clean) == 0:
        record_failure(site, "blocked")
        return {
            "ok": False,
            "status": "blocked",
            "reason": (
                "All results appear to be block-page responses. "
                "This site is likely geo-blocked or down at your IP. "
                "No results were returned."
            ),
            "data": [],
        }

    if blocked_count > 0:
        # Partial: some real results, some block items — log but don't penalise hard
        logger.warning("site_health: %s returned %d block items + %d real items", site, blocked_count, len(clean))

    if clean:
        record_success(site)
        return {"ok": True, "status": "ok", "reason": None, "data": clean}

    return {"ok": True, "status": "no_results", "reason": None, "data": []}


def _classify_error_message(msg: str) -> str:
    """Map a raw error message string to a structured status code."""
    m = (msg or "").lower()
    if any(p in m for p in ["timeout", "timed out", "connection timed"]):
        return "timeout"
    if any(p in m for p in ["connection refused", "nodename nor servname", "name or service not known",
                              "no address associated", "failed to establish", "max retries exceeded"]):
        return "domain_dead"
    if any(p in m for p in ["blocked", "change ip", "access denied", "forbidden"]):
        return "blocked"
    if any(p in m for p in ["parse", "beautifulsoup", "attributeerror", "nonetype", "has no attribute"]):
        return "parse_failed"
    return "error"


def wrap_search_response(site: str, api_response: Dict[str, Any], http_status: int = 200) -> Dict[str, Any]:
    """
    Wrap a Torrent-Api-py API response dict through the health filter.

    Expected input shape: { "data": [...], ... } or { "detail": "..." } (FastAPI error)

    Returns the same shape but with:
      - block-page items stripped from data
      - site_status field added
      - error field set if blocked/failed

    Auth errors (401/403) and other HTTP errors from the app itself are passed
    through untouched — we never penalise a site for the caller's bad API key.

    IMPORTANT: The upstream search_router also returns HTTP 403 when a site is
    blocked (resp is None → error_handler with HTTP_403_FORBIDDEN). We must
    distinguish these two cases by inspecting the response body:
      - Real auth error from FastAPI:  {"detail": "Access forbidden: ..."}
      - Site blocked from scraper:     {"error": "Website Blocked ...", "data": ...}
    """
    # ── Real FastAPI auth error: has "detail" key, no "error" key ─────────────
    if "detail" in api_response and "error" not in api_response:
        result = dict(api_response)
        result.setdefault("data", [])
        result["site_status"] = get_site_status(site)
        return result

    # ── FastAPI HTTPException shape without data — but only if no block text ──
    if "detail" in api_response and "data" not in api_response:
        detail_text = str(api_response.get("detail") or "")
        if not _is_block_text(detail_text):
            result = dict(api_response)
            result["data"] = []
            result["site_status"] = get_site_status(site)
            return result
        # If detail itself contains block text, fall through to block detection

    site_st = get_site_status(site)

    # Already disabled — don't pass through at all
    if site_st.get("status") == "disabled":
        return {
            "data": [],
            "error": (
                f"Site '{site}' is temporarily disabled after repeated failures. "
                f"Last reason: {site_st.get('reason', 'unknown')}. "
                f"It will be retried automatically after {_DISABLED_TTL_SEC // 60} minutes."
            ),
            "site_status": site_st,
        }

    # ── Handle upstream "no results" error strings cleanly ───────────────────
    # The search_router returns these as {"error": "...", "data": []} with 404.
    # They are NOT site failures — just empty searches or unknown site keys.
    upstream_error = str(api_response.get("error") or "")
    if upstream_error and _NO_RESULTS_RE.search(upstream_error):
        return {
            "data": [],
            "site_status": get_site_status(site),
        }

    # ── Check the upstream "error" field for block-page text ─────────────────
    # Some Torrent-Api-py adapters return the block page text in the "error" key
    # rather than inside the data array. E.g.:
    #   {"error": "Website Blocked Change IP or Website Domain.", "data": null}
    if upstream_error and _is_block_text(upstream_error):
        record_failure(site, "blocked")
        return {
            "data": [],
            "error": (
                "This site is geo-blocked or unreachable from your IP. "
                f"Original message: {upstream_error[:120]}"
            ),
            "error_code": "blocked",
            "site_status": get_site_status(site),
        }

    # ── Also check for timeout / connection errors in the error field ──────────
    if upstream_error:
        classified_err = _classify_error_message(upstream_error)
        if classified_err in ("timeout", "domain_dead"):
            record_failure(site, classified_err)
            return {
                "data": [],
                "error": upstream_error[:200],
                "error_code": classified_err,
                "site_status": get_site_status(site),
            }

    raw_data = api_response.get("data")
    classified = classify_results(site, raw_data)

    result = dict(api_response)  # copy
    result["data"] = classified["data"]
    result["site_status"] = get_site_status(site)

    if not classified["ok"]:
        result["error"] = classified["reason"] or classified["status"]
        result["error_code"] = classified["status"]
    elif "error" in result and not result.get("error_code"):
        # Clear any upstream error string if we actually got clean results
        if classified["status"] == "ok":
            result.pop("error", None)

    return result
