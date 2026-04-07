#!/usr/bin/env python3
"""
scripts/test_tor_proxy.py — Tor/SOCKS proxy diagnostic for PiMedia.

Tests generic HTTP reachability via both direct and SOCKS5 paths.
Does NOT target any specific piracy site; uses check.torproject.org/api/ip
which is the canonical Tor Project endpoint for verifying Tor exit usage.

Usage (on Pi):
    cd /home/von/homelab-app
    /home/von/homelab-app/venv/bin/python scripts/test_tor_proxy.py

    # Or with an explicit proxy URL:
    HTTP_PROXY=socks5h://127.0.0.1:9050 \
        /home/von/homelab-app/venv/bin/python scripts/test_tor_proxy.py

Exit codes:
    0  both tests passed (or proxy test skipped because HTTP_PROXY not set)
    1  one or more tests failed
"""

import asyncio
import logging
import os
import sys

# ── Bootstrap: add app root to sys.path so helper imports work. ──────────────
# The script lives at  app/scripts/test_tor_proxy.py
# The helpers live at  app/helper/
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_ROOT   = os.path.dirname(_SCRIPT_DIR)
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("test_tor_proxy")

# ── Import aiohttp — must be installed ───────────────────────────────────────
try:
    import aiohttp
except ImportError:
    logger.error("aiohttp is not installed. Run: pip install aiohttp")
    sys.exit(1)

# ── Import our proxy helper — exercises the real code path ───────────────────
try:
    from helper.proxy_helper import HTTP_PROXY, _SOCKS_SCHEMES
except ImportError as exc:
    logger.error("Could not import helper.proxy_helper: %s", exc)
    sys.exit(1)

# ── Target URL ───────────────────────────────────────────────────────────────
# check.torproject.org/api/ip returns JSON: {"IsTor": bool, "IP": "x.x.x.x"}
CHECK_URL = "https://check.torproject.org/api/ip"
TIMEOUT   = aiohttp.ClientTimeout(total=30)

# ── Test helpers ─────────────────────────────────────────────────────────────

async def fetch_json(session: aiohttp.ClientSession, url: str) -> dict:
    """Fetch *url* and return parsed JSON, or raise on any failure."""
    async with session.get(url, timeout=TIMEOUT) as resp:
        resp.raise_for_status()
        return await resp.json()


async def test_direct() -> bool:
    """Direct HTTPS request — no proxy."""
    logger.info("── Direct request ─────────────────────────────────────")
    try:
        async with aiohttp.ClientSession() as session:
            data = await fetch_json(session, CHECK_URL)
        ip      = data.get("IP", "unknown")
        is_tor  = data.get("IsTor", False)
        logger.info("  IP      : %s", ip)
        logger.info("  IsTor   : %s", is_tor)
        if is_tor:
            logger.warning(
                "  Direct request resolved via Tor — your system-wide "
                "routing may already proxy all traffic."
            )
        logger.info("  RESULT  : PASS (direct HTTPS works)")
        return True
    except Exception as exc:
        logger.error("  RESULT  : FAIL — %s: %s", type(exc).__name__, exc)
        return False


async def test_socks_proxy(proxy_url: str) -> bool:
    """SOCKS-proxied HTTPS request via *proxy_url*."""
    logger.info("── SOCKS proxy request (%s) ─────────────────────────", proxy_url)

    # Verify aiohttp-socks is available before we attempt connection.
    try:
        from aiohttp_socks import ProxyConnector  # noqa: PLC0415
    except ImportError:
        logger.error(
            "  aiohttp-socks is not installed. "
            "Run: pip install 'aiohttp-socks>=0.8.4'"
        )
        return False

    try:
        connector = ProxyConnector.from_url(proxy_url)
        logger.debug("  Connector created: %s", connector)
    except Exception as exc:
        logger.error(
            "  RESULT  : FAIL — could not create connector: %s: %s",
            type(exc).__name__,
            exc,
        )
        return False

    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            data = await fetch_json(session, CHECK_URL)
        ip      = data.get("IP", "unknown")
        is_tor  = data.get("IsTor", False)
        logger.info("  IP      : %s", ip)
        logger.info("  IsTor   : %s", is_tor)
        if not is_tor:
            logger.warning(
                "  Exit IP %s is NOT a recognised Tor exit node. "
                "Your SOCKS proxy may not be Tor, or Tor's DB is stale.",
                ip,
            )
            logger.info("  RESULT  : PASS (SOCKS proxy works, but not a Tor exit)")
        else:
            logger.info("  RESULT  : PASS (SOCKS proxy works and confirmed Tor exit)")
        return True
    except Exception as exc:
        logger.error(
            "  RESULT  : FAIL — %s: %s",
            type(exc).__name__,
            exc,
        )
        logger.error(
            "  Common causes: Tor not running, wrong port, SOCKS auth mismatch, "
            "Tor circuit timeout. Check: systemctl status tor"
        )
        return False


async def main() -> int:
    logger.info("PiMedia Tor/SOCKS proxy diagnostic")
    logger.info("Target URL : %s", CHECK_URL)
    logger.info("HTTP_PROXY : %s", HTTP_PROXY or "(not set)")
    logger.info("")

    results = []

    # 1. Direct request — always run.
    results.append(await test_direct())
    logger.info("")

    # 2. SOCKS proxy — only if HTTP_PROXY is set to a recognised scheme.
    if HTTP_PROXY and HTTP_PROXY.lower().startswith(_SOCKS_SCHEMES):
        results.append(await test_socks_proxy(HTTP_PROXY))
    elif HTTP_PROXY:
        logger.warning(
            "HTTP_PROXY=%s does not start with a recognised SOCKS scheme %s — "
            "skipping SOCKS proxy test.",
            HTTP_PROXY,
            _SOCKS_SCHEMES,
        )
    else:
        logger.info(
            "HTTP_PROXY is not set — skipping SOCKS proxy test.\n"
            "To test: HTTP_PROXY=socks5h://127.0.0.1:9050 python scripts/test_tor_proxy.py"
        )

    logger.info("")
    passed = all(results)
    logger.info("── Summary ─────────────────────────────────────────────")
    logger.info("  %s / %s test(s) passed", sum(results), len(results))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
