import re
import time
import asyncio
import aiohttp
import socket
from bs4 import BeautifulSoup
from helper.proxy_helper import get_aiohttp_connector

BASE_URL = "https://audiobookbay.lu"

_TRACKERS = "&".join([
    "tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce",
    "tr=udp%3A%2F%2Fopen.stealth.si%3A80%2Fannounce",
    "tr=udp%3A%2F%2Ftracker.openbittorrent.com%3A6969%2Fannounce",
    "tr=udp%3A%2F%2Ftracker.torrent.eu.org%3A451%2Fannounce",
])

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Max concurrent detail page fetches — keeps Tor happy and total time reasonable
_MAX_CONCURRENT = 3


def _build_magnet(infohash: str, title: str) -> str:
    from urllib.parse import quote_plus
    return f"magnet:?xt=urn:btih:{infohash}&dn={quote_plus(title)}&{_TRACKERS}"


def _parse_size(text: str) -> str:
    m = re.search(r"([\d.]+)\s*(GB|MB|KB)s?", text, re.IGNORECASE)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return ""


def _parse_format(text: str) -> str:
    m = re.search(r"Format:\s*([A-Z0-9]+)", text)
    return m.group(1) if m else ""


def _parse_date(text: str) -> str:
    m = re.search(r"Posted:\s*([\d]+ \w+ \d{4})", text)
    return m.group(1) if m else ""


async def _fetch(session: aiohttp.ClientSession, url: str) -> str | None:
    try:
        async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status == 200:
                return await r.text()
    except Exception:
        pass
    return None


async def _get_infohash(session: aiohttp.ClientSession, sem: asyncio.Semaphore, detail_url: str) -> str | None:
    async with sem:
        html = await _fetch(session, detail_url)
        if not html:
            return None
        hashes = re.findall(r"[a-fA-F0-9]{40}", html)
        return hashes[0] if hashes else None


async def _parse_results(session: aiohttp.ClientSession, html: str, limit: int) -> list:
    soup = BeautifulSoup(html, "html.parser")
    posts = soup.find_all("div", class_="post")
    metas = []
    detail_urls = []

    for post in posts[:limit]:
        try:
            title_tag = post.find("h2").find("a")
            title = title_tag.text.strip()
            detail_url = title_tag["href"]
            if not detail_url.startswith("http"):
                detail_url = BASE_URL + detail_url

            info_div = post.find("div", class_="postInfo")
            category = ""
            if info_div:
                category = info_div.get_text(" ", strip=True).replace("Category:", "").split("Language:")[0].strip()

            content_div = post.find("div", class_="postContent")
            content_text = content_div.get_text(" ", strip=True) if content_div else ""
            size = _parse_size(content_text)
            fmt = _parse_format(content_text)
            date = _parse_date(content_text)

            img_tag = post.find("img")
            cover = img_tag["src"] if img_tag and img_tag.get("src") else ""

            detail_urls.append(detail_url)
            metas.append({
                "name": title,
                "size": size,
                "format": fmt,
                "date": date,
                "category": category,
                "cover": cover,
                "url": detail_url,
                "seeders": "?",
                "leechers": "?",
            })
        except Exception:
            continue

    # Fetch infohashes with concurrency limit to avoid Tor overload
    sem = asyncio.Semaphore(_MAX_CONCURRENT)
    hashes = await asyncio.gather(*[_get_infohash(session, sem, u) for u in detail_urls])

    results = []
    for meta, infohash in zip(metas, hashes):
        if infohash:
            meta["hash"] = infohash.upper()
            meta["magnet"] = _build_magnet(infohash, meta["name"])
        results.append(meta)

    return results



_BLOCK_PHRASES = ("website blocked", "change ip", "access denied", "403 forbidden", "cloudflare")

def _tor_newnym():
    """Send NEWNYM to Tor control port to get a fresh exit node."""
    try:
        with socket.create_connection(("127.0.0.1", 9051), timeout=3) as s:
            s.sendall(b'AUTHENTICATE ""\r\nSIGNAL NEWNYM\r\nQUIT\r\n')
            s.recv(256)
    except Exception:
        pass

def _is_blocked(html: str) -> bool:
    if not html:
        return True
    low = html[:2000].lower()
    return any(p in low for p in _BLOCK_PHRASES)

class AudiobookBay:
    _name = "AudiobookBay"

    def __init__(self):
        self.BASE_URL = BASE_URL
        self.LIMIT = None

    async def search(self, query: str, page: int, limit: int) -> dict | None:
        from urllib.parse import quote_plus
        import asyncio
        start = time.time()
        url = f"{BASE_URL}/page/{page}/?s={quote_plus(query)}" if page > 1 else f"{BASE_URL}/?s={quote_plus(query)}"

        html = None
        data = []
        for attempt in range(5):  # max 5 attempts
            if attempt > 0:
                _tor_newnym()
                wait = 4 + (attempt - 1) * 2  # 4s, 6s, 8s, 10s
                await asyncio.sleep(wait)

            connector = get_aiohttp_connector(BASE_URL)
            async with aiohttp.ClientSession(connector=connector) as session:
                html = await _fetch(session, url)
                if not _is_blocked(html):
                    data = await _parse_results(session, html, limit)
                    break
                html = None

        if not html:
            return None

        return {
            "data": data,
            "total": len(data),
            "time": time.time() - start,
        }

    async def trending(self, category, page, limit):
        return await self.search("", page, limit)

    async def recent(self, category, page, limit):
        return await self.search("", page, limit)
