import asyncio
import logging
import os

import aiohttp

from .asyncioPoliciesFix import decorator_asyncio_fix
from constants.headers import HEADER_AIO
from .proxy_helper import get_aiohttp_connector

logger = logging.getLogger(__name__)

HTTP_PROXY = os.environ.get("HTTP_PROXY", None)


class Scraper:
    @decorator_asyncio_fix
    async def _get_html(self, session, url):
        try:
            async with session.get(url, headers=HEADER_AIO) as r:
                return await r.text()
        except Exception as exc:
            # Log with enough detail to distinguish network errors, SOCKS
            # failures, timeouts, TLS errors, etc.
            logger.warning(
                "html_scraper._get_html: request failed for %s — %s: %s",
                url,
                type(exc).__name__,
                exc,
            )
            return None

    async def get_all_results(self, session, url):
        """
        Fetch *url* via a SOCKS/Tor connector when one is warranted, otherwise
        use the supplied *session* directly.

        Behaviour:
        - If a SOCKS connector can be created → open a dedicated aiohttp
          ClientSession with that connector and fetch through the proxy.
        - If the connector is None (URL not proxy-routed) → use *session*.
        - If connector creation raises (e.g. aiohttp-socks not installed) →
          log the error and fall back to *session* so the scraper still
          returns a result rather than crashing the whole request.
        """
        connector = None
        try:
            connector = get_aiohttp_connector(url)
        except RuntimeError as exc:
            # aiohttp-socks missing or misconfigured — surface in logs and
            # continue with a direct request rather than crashing.
            logger.error(
                "html_scraper.get_all_results: cannot create SOCKS connector "
                "for %s — %s. Falling back to direct request.",
                url,
                exc,
            )

        if connector is not None:
            logger.debug(
                "html_scraper.get_all_results: using SOCKS proxy for %s", url
            )
            async with aiohttp.ClientSession(connector=connector) as proxy_session:
                return await asyncio.gather(
                    asyncio.create_task(self._get_html(proxy_session, url))
                )

        # Either proxy not needed or connector creation failed — go direct.
        if HTTP_PROXY and _url_needs_proxy_hint(url):
            # This branch is hit when the connector failed but we still know
            # the site normally needs a proxy — worth a log entry.
            logger.warning(
                "html_scraper.get_all_results: fetching %s directly even though "
                "HTTP_PROXY=%s is set (connector unavailable)",
                url,
                HTTP_PROXY,
            )

        return await asyncio.gather(
            asyncio.create_task(self._get_html(session, url))
        )


def _url_needs_proxy_hint(url: str) -> bool:
    """
    Lightweight check used only for the warning log path.  Mirrors the same
    logic as proxy_helper._should_proxy but avoids importing the full set to
    keep this module self-contained.
    """
    _PROXY_SITES = {
        "thepiratebay", "thepiratebay10", "kickass", "kat.", "glodls",
    }
    url_lower = (url or "").lower()
    return any(s in url_lower for s in _PROXY_SITES)
