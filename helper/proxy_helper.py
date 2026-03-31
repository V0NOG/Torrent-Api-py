"""
proxy_helper.py — centralises proxy config for all scrapers.
Only routes known AU-blocked sites through Tor; everything else goes direct.
"""
import os

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
    """Return a ProxyConnector only for AU-blocked sites."""
    if _should_proxy(url) and HTTP_PROXY.startswith("socks5://"):
        try:
            from aiohttp_socks import ProxyConnector
            return ProxyConnector.from_url(HTTP_PROXY)
        except ImportError:
            pass
    return None


def get_aiohttp_connector_for_base(base_url: str = ""):
    """Alias — pass the scraper's BASE_URL to decide proxy routing."""
    return get_aiohttp_connector(base_url)
