#!/usr/bin/env python3
"""
fix_abb_scraper.py
==================
Rewrites the ABB integration to scrape audiobookbay.is directly
(the JamesRy96 container has no /search API — it's UI-only).

    cd /home/von/Torrent-Api-py
    python3 fix_abb_scraper.py
"""
import shutil, sys, re
from pathlib import Path

MAIN = Path("main.py")
if not MAIN.exists():
    print("ERROR: main.py not found. Run from inside Torrent-Api-py/")
    sys.exit(1)

src = MAIN.read_text(encoding="utf-8")

if "audiobookbay.is" in src:
    print("Already patched to scrape audiobookbay.is — nothing to do.")
    sys.exit(0)

if "_abb_scrape" not in src:
    print("ERROR: _abb_scrape() not found. Was apply_abb_patch.py run first?")
    sys.exit(1)

bak = Path("main.py.bak.scraper_fix")
shutil.copy2(MAIN, bak)
print(f"Backed up → {bak.name}")

# ─────────────────────────────────────────────────────────────────────────────
# 1. Replace _ABBHTMLParser with one that matches audiobookbay.is structure
# ─────────────────────────────────────────────────────────────────────────────
NEW_PARSER = '''class _ABBHTMLParser(_HTMLParser):
    """
    Parses audiobookbay.is search results.
    Structure:
      <div class="post ...">
        <div class="postCover"><a href="/slug/"><img src="cover.jpg"/></a></div>
        <div class="postContent">
          <div class="postTitle"><h2><a href="/slug/">Title</a></h2></div>
          <div class="postInfo">Author: X | Narrator: Y | ...</div>
        </div>
      </div>
    Infohash is NOT on search page; needs detail page fetch.
    """
    def __init__(self):
        super().__init__()
        self.results = []
        self._in_post = False
        self._in_cover = False
        self._in_title_div = False
        self._in_title_link = False
        self._in_info = False
        self._current = {}
        self._depth = 0
        self._post_depth = -1
        self._cover_depth = -1
        self._title_depth = -1

    def handle_starttag(self, tag, attrs):
        self._depth += 1
        d = dict(attrs)
        cls = d.get("class") or ""
        parts = set(cls.split())

        # Post card: <div class="post ..."> (exclude sub-divs)
        non_post = {"postContent","postCover","postTitle","postInfo","postPage","postMeta"}
        if tag == "div" and "post" in parts and not (parts & non_post):
            self._in_post = True
            self._post_depth = self._depth
            self._current = {}
            return

        if not self._in_post:
            return

        # Cover image
        if tag == "div" and "postCover" in parts:
            self._in_cover = True
            self._cover_depth = self._depth
        if self._in_cover and tag == "img":
            src = d.get("src") or d.get("data-src") or ""
            if src and not self._current.get("cover"):
                self._current["cover"] = src

        # Title div
        if tag == "div" and "postTitle" in parts:
            self._in_title_div = True
            self._title_depth = self._depth
        if self._in_title_div and tag == "a":
            href = d.get("href") or ""
            if href and not self._current.get("url"):
                self._current["url"] = href
                self._in_title_link = True

        # Info (author, narrator)
        if tag == "div" and "postInfo" in parts:
            self._in_info = True

        # Magnet (rare on search page)
        href = d.get("href") or ""
        if tag == "a" and href.startswith("magnet:"):
            self._current["magnet"] = href
            ih = _abb_btih(href)
            if ih:
                self._current["infohash"] = ih

    def handle_endtag(self, tag):
        if self._in_post and tag == "div" and self._depth == self._post_depth:
            if self._current.get("url") or self._current.get("title"):
                self.results.append(dict(self._current))
            self._in_post = False
            self._in_cover = False
            self._in_title_div = False
            self._in_title_link = False
            self._in_info = False
            self._current = {}
            self._post_depth = -1
        if self._in_cover and tag == "div" and self._depth == self._cover_depth:
            self._in_cover = False
        if self._in_title_div and tag == "div" and self._depth == self._title_depth:
            self._in_title_div = False
            self._in_title_link = False
        if self._in_title_link and tag == "a":
            self._in_title_link = False
        if self._in_info and tag == "div":
            self._in_info = False
        self._depth -= 1

    def handle_data(self, data):
        s = data.strip()
        if not s:
            return
        if self._in_title_link:
            self._current["title"] = (self._current.get("title") or "") + s
        elif self._in_info:
            self._current["_info"] = ((self._current.get("_info") or "") + " " + s).strip()
'''

# Find and replace the old parser class
parser_match = re.search(
    r'class _ABBHTMLParser\(_HTMLParser\):.*?(?=\ndef _abb_btih)',
    src, re.DOTALL
)
if parser_match:
    src = src[:parser_match.start()] + NEW_PARSER + "\n" + src[parser_match.end():]
    print("Replaced _ABBHTMLParser ✓")
else:
    print("WARNING: Could not find _ABBHTMLParser to replace")

# ─────────────────────────────────────────────────────────────────────────────
# 2. Replace _abb_scrape to hit audiobookbay.is directly
# ─────────────────────────────────────────────────────────────────────────────
NEW_SCRAPE = '''async def _abb_scrape(query: str, limit: int) -> List[Dict[str, Any]]:
    """Scrape audiobookbay.is search directly. The JamesRy96 container has no /search API."""
    if not httpx:
        return []
    abb_hostname = (os.getenv("ABB_HOSTNAME", "audiobookbay.is") or "audiobookbay.is").strip().strip("'\\\"")
    base_url = f"https://{abb_hostname}"
    search_url = f"{base_url}/page/1/"
    hdrs = {
        "User-Agent": ABB_USER_AGENT or "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": base_url + "/",
    }
    if ABB_COOKIE:
        hdrs["Cookie"] = ABB_COOKIE
    try:
        async with httpx.AsyncClient(timeout=20.0, headers=hdrs, follow_redirects=True) as c:
            r = await c.get(search_url, params={"s": query, "cat": "undefined"})
            if r.status_code >= 400:
                return []
            p = _ABBHTMLParser()
            p.feed(r.text or "")
            for item in p.results:
                item["_base"] = base_url
            return p.results[:limit]
    except Exception:
        return []
'''

scrape_match = re.search(
    r'async def _abb_scrape\(query: str, limit: int\).*?(?=\ndef _abb_norm)',
    src, re.DOTALL
)
if scrape_match:
    src = src[:scrape_match.start()] + NEW_SCRAPE + "\n" + src[scrape_match.end():]
    print("Replaced _abb_scrape ✓")
else:
    print("WARNING: Could not find _abb_scrape to replace")

# ─────────────────────────────────────────────────────────────────────────────
# 3. Update _abb_norm to use item-level _base and extract author/narrator
# ─────────────────────────────────────────────────────────────────────────────
NEW_NORM = '''def _abb_norm(raw: Dict[str, Any], base: str) -> Dict[str, Any]:
    title    = (raw.get("title") or "").strip()
    cover    = (raw.get("cover") or "").strip()
    url      = (raw.get("url") or "").strip()
    ih       = (raw.get("infohash") or "").strip().lower()
    magnet   = (raw.get("magnet") or "").strip()
    author   = (raw.get("author") or "").strip()
    narrator = (raw.get("narrator") or "").strip()

    # Extract author/narrator from _info if not set directly
    info = (raw.get("_info") or "")
    if not author and info:
        am = re.search(r"(?:Author|By)[:\\s]+([^|/,]+)", info, re.I)
        if am:
            author = am.group(1).strip()
    if not narrator and info:
        nm = re.search(r"(?:Narrator|Read by)[:\\s]+([^|/,]+)", info, re.I)
        if nm:
            narrator = nm.group(1).strip()

    # Use item-stored base (from scraper) if available
    item_base = raw.get("_base") or base
    if cover and cover.startswith("/"):
        cover = item_base + cover
    if url and url.startswith("/"):
        url = item_base + url
    if not url.startswith("http") and url:
        url = item_base + "/" + url.lstrip("/")

    if not ih and magnet:
        ih = _abb_btih(magnet) or ""
    if ih and not magnet:
        try:
            magnet = magnet_from_btih(ih)
        except Exception:
            magnet = f"magnet:?xt=urn:btih:{ih}"

    return {
        "title": title, "cover": cover, "url": url,
        "infohash": ih, "magnet": magnet,
        "author": author, "narrator": narrator,
        "needs_detail": (not ih and bool(url)),
    }
'''

norm_match = re.search(
    r'def _abb_norm\(raw: Dict\[str, Any\], base: str\).*?(?=\n@app\.get\("/api/v1/abb/search"\))',
    src, re.DOTALL
)
if norm_match:
    src = src[:norm_match.start()] + NEW_NORM + "\n" + src[norm_match.end():]
    print("Replaced _abb_norm ✓")
else:
    print("WARNING: Could not find _abb_norm to replace")

# ─────────────────────────────────────────────────────────────────────────────
# 4. Fix _abb_detail_ih to fetch from audiobookbay.is
# ─────────────────────────────────────────────────────────────────────────────
NEW_DETAIL = '''async def _abb_detail_ih(rel_url: str) -> Optional[str]:
    """Fetch detail page from audiobookbay.is to get infohash."""
    if not httpx:
        return None
    try:
        abb_hostname = (os.getenv("ABB_HOSTNAME", "audiobookbay.is") or "audiobookbay.is").strip().strip("'\\\"")
        base = f"https://{abb_hostname}"
        if rel_url.startswith("http"):
            full = rel_url
        elif rel_url.startswith("/"):
            full = base + rel_url
        else:
            full = base + "/" + rel_url
        hdrs = {"User-Agent": ABB_USER_AGENT or "Mozilla/5.0"}
        if ABB_COOKIE:
            hdrs["Cookie"] = ABB_COOKIE
        async with httpx.AsyncClient(timeout=10.0, headers=hdrs, follow_redirects=True) as c:
            r = await c.get(full)
            if r.status_code < 400:
                return _abb_ih_from_html(r.text or "")
    except Exception:
        pass
    return None
'''

detail_match = re.search(
    r'async def _abb_detail_ih\(rel_url: str\).*?(?=\nasync def _abb_scrape)',
    src, re.DOTALL
)
if detail_match:
    src = src[:detail_match.start()] + NEW_DETAIL + "\n" + src[detail_match.end():]
    print("Replaced _abb_detail_ih ✓")
else:
    print("WARNING: Could not find _abb_detail_ih to replace")

# ─────────────────────────────────────────────────────────────────────────────
# 5. Fix image-proxy to allow audiobookbay.is images
# ─────────────────────────────────────────────────────────────────────────────
OLD_GUARD = '''    if ABB_URL:
        allowed = urlparse(ABB_URL).netloc
        requested = urlparse(img_url).netloc
        if allowed and requested and allowed != requested and "audiobookbay" not in requested:
            raise HTTPException(status_code=403, detail="Image URL not from configured ABB host")'''

NEW_GUARD = '''    # Allow audiobookbay.is images (direct scrape) and configured ABB_URL host
    requested_host = urlparse(img_url).netloc
    if "audiobookbay" not in requested_host:
        if ABB_URL:
            allowed = urlparse(ABB_URL).netloc
            if allowed and requested_host and allowed != requested_host:
                raise HTTPException(status_code=403, detail="Image URL not from allowed host")'''

if OLD_GUARD in src:
    src = src.replace(OLD_GUARD, NEW_GUARD)
    print("Updated image-proxy host guard ✓")
else:
    print("Image-proxy guard: not found (may already be OK)")

# ─────────────────────────────────────────────────────────────────────────────
# Write
# ─────────────────────────────────────────────────────────────────────────────
MAIN.write_text(src, encoding="utf-8")
print("\n✅ Done. Restart your app:")
print("   sudo systemctl restart torrent-api-py.service")
