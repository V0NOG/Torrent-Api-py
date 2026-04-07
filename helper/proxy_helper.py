"""
proxy_helper.py — centralises proxy config for all scrapers.

Only routes known AU-blocked sites through Tor; everything else goes direct.

Environment variable:
    HTTP_PROXY  e.g. socks5h://127.0.0.1:9050
                Use socks5h:// (not socks5://) so DNS is resolved inside Tor
                and not leaked to the local resolver.
"""
import logging
import os

logger = logging.getLogger(__name__)

HTTP_PROXY = os.environ.get("HTTP_PROXY", None)

# Sites confirmed blocked by AU ISPs that benefit from Tor routing.
# Sites that block Tor exit nodes should NOT be listed here.
_PROXY_SITES = {
    "thepiratebay",
    "thepiratebay10",
    "kickass",
    "kat.",
    "glodls",
    # "audiobookbay",  # Direct access works — Tor exits are unreliable with ABB
}

# SOCKS scheme prefixes we recognise for aiohttp connector creation.
_SOCKS_SCHEMES = ("socks5://", "socks5h://", "socks4://", "socks4a://")


def _should_proxy(url: str) -> bool:
    if not HTTP_PROXY or not url:
        return False
    url_lower = url.lower()
    return any(s in url_lower for s in _PROXY_SITES)


def get_requests_proxies(url: str = "") -> dict:
    """Return a proxies dict for requests/cloudscraper, only for blocked sites."""
    if _should_proxy(url):
        return {"http": HTTP_PROXY, "https": HTTP_PROXY}
    return {}


def get_aiohttp_connector(url: str = ""):
    """
    Return a ProxyConnector for aiohttp, but only for AU-blocked sites.

    Returns None if:
    - the URL is not in the proxy-required list
    - HTTP_PROXY is not set
    - HTTP_PROXY does not start with a recognised SOCKS scheme

    Raises RuntimeError (with a clear message) if:
    - a SOCKS connector is needed but aiohttp-socks is not installed

    Never silently swallows the ImportError — callers must handle RuntimeError
    or ensure aiohttp-socks is installed (it is listed in requirements.txt).
    """
    if not _should_proxy(url):
        return None

    if not HTTP_PROXY.lower().startswith(_SOCKS_SCHEMES):
        logger.warning(
            "proxy_helper: HTTP_PROXY is set (%s) but is not a recognised SOCKS "
            "scheme %s — skipping SOCKS connector for %s",
            HTTP_PROXY,
            _SOCKS_SCHEMES,
            url,
        )
        return None

    try:
        from aiohttp_socks import ProxyConnector  # noqa: PLC0415
    except ImportError as exc:
        # Surface clearly instead of silently returning None.
        raise RuntimeError(
            "aiohttp-socks is required for SOCKS/Tor proxy support but is not "
            "installed. Run: pip install 'aiohttp-socks>=0.8.4'"
        ) from exc

    connector = ProxyConnector.from_url(HTTP_PROXY)
    logger.debug(
        "proxy_helper: created SOCKS connector via %s for %s", HTTP_PROXY, url
    )
    return connector


def get_aiohttp_connector_for_base(base_url: str = ""):
    """Alias — pass the scraper's BASE_URL to decide proxy routing."""
    return get_aiohttp_connector(base_url)
