#!/usr/bin/env python3
"""
apply_abb_patch.py
==================
Run this script from inside your Torrent-Api-py directory:

    cd /home/von/Torrent-Api-py
    python3 apply_abb_patch.py

It will:
  1. Patch main.py  — inject ABB proxy routes before the StaticFiles mount
  2. Patch static/app.js  — add ABB search/render/download logic
  3. Patch static/app.css — add cover image + badge styles

Backups are written to main.py.bak, static/app.js.bak, static/app.css.bak
before any changes are made.
"""

import shutil
import sys
from pathlib import Path

# ── Locate files ──────────────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent
MAIN_PY  = HERE / "main.py"
APP_JS   = HERE / "static" / "app.js"
APP_CSS  = HERE / "static" / "app.css"

for p in (MAIN_PY, APP_JS, APP_CSS):
    if not p.exists():
        print(f"ERROR: {p} not found. Run this script from inside the Torrent-Api-py directory.")
        sys.exit(1)

# ── Backups ───────────────────────────────────────────────────────────────────
for p in (MAIN_PY, APP_JS, APP_CSS):
    bak = p.with_suffix(p.suffix + ".bak")
    shutil.copy2(p, bak)
    print(f"  Backed up {p.name} → {bak.name}")

# ══════════════════════════════════════════════════════════════════════════════
# 1.  main.py patch
# ══════════════════════════════════════════════════════════════════════════════

ABB_ROUTES = r'''
# ============================================================
# AudiobookBay Proxy Routes  (auto-patched by apply_abb_patch.py)
# ============================================================
import re as _re_abb
from html.parser import HTMLParser as _HTMLParser
from fastapi.responses import Response as _FResponse


class _ABBHTMLParser(_HTMLParser):
    """Scrapes book cards from ABB search results HTML (stock JamesRy96 image)."""
    def __init__(self):
        super().__init__()
        self.results = []
        self._in_post = False
        self._in_title = False
        self._current = {}
        self._depth = 0
        self._post_depth = -1

    def handle_starttag(self, tag, attrs):
        self._depth += 1
        d = dict(attrs)
        cls = (d.get("class") or "")
        parts = cls.split()

        if tag == "div" and "post" in parts and "postContent" not in parts and "postCover" not in parts:
            self._in_post = True
            self._post_depth = self._depth
            self._current = {}

        if self._in_post:
            href = (d.get("href") or "")
            if tag == "a" and href.startswith("/"):
                if ("/book/" in href or "/audiobook/" in href) and not self._current.get("url"):
                    self._current["url"] = href
                    self._in_title = True
            if tag == "img" and (d.get("src") or "").startswith("http"):
                src = d["src"]
                if any(ext in src for ext in (".jpg", ".jpeg", ".png", ".webp")) and not self._current.get("cover"):
                    self._current["cover"] = src
            if tag == "a" and href.startswith("magnet:"):
                self._current["magnet"] = href
                ih = _abb_btih(href)
                if ih:
                    self._current["infohash"] = ih

    def handle_endtag(self, tag):
        if self._in_post and tag == "div" and self._depth == self._post_depth:
            if self._current.get("url"):
                self.results.append(dict(self._current))
            self._in_post = False
            self._current = {}
            self._post_depth = -1
            self._in_title = False
        self._depth -= 1

    def handle_data(self, data):
        if self._in_title and data.strip():
            self._current["title"] = (self._current.get("title") or "") + data.strip()
            self._in_title = False


def _abb_btih(magnet: str) -> Optional[str]:
    try:
        q = parse_qs(urlparse(magnet).query)
        for xt in (q.get("xt") or []):
            if xt.lower().startswith("urn:btih:"):
                return xt.split(":", 2)[-1].strip().lower()
    except Exception:
        pass
    return None


def _abb_ih_from_html(html: str) -> Optional[str]:
    m = _re_abb.search(r'magnet:\?[^"\'<\s]+', html)
    if m:
        ih = _abb_btih(m.group(0))
        if ih:
            return ih
    m2 = _re_abb.search(r'\b([0-9a-fA-F]{40})\b', html)
    if m2:
        return m2.group(1).lower()
    return None


async def _abb_detail_ih(rel_url: str) -> Optional[str]:
    if not httpx or not ABB_URL:
        return None
    try:
        base = ABB_URL.rstrip("/")
        full = base + rel_url if rel_url.startswith("/") else base + "/" + rel_url
        hdrs = {"User-Agent": ABB_USER_AGENT}
        if ABB_COOKIE:
            hdrs["Cookie"] = ABB_COOKIE
        async with httpx.AsyncClient(timeout=10.0, headers=hdrs, follow_redirects=True) as c:
            r = await c.get(full)
            if r.status_code < 400:
                return _abb_ih_from_html(r.text or "")
    except Exception:
        pass
    return None


async def _abb_scrape(query: str, limit: int) -> List[Dict[str, Any]]:
    if not httpx or not ABB_URL:
        return []
    try:
        url = ABB_URL.rstrip("/") + "/search"
        hdrs = {"User-Agent": ABB_USER_AGENT}
        if ABB_COOKIE:
            hdrs["Cookie"] = ABB_COOKIE
        async with httpx.AsyncClient(timeout=15.0, headers=hdrs, follow_redirects=True) as c:
            r = await c.get(url, params={"q": query, "page": 1})
            if r.status_code >= 400:
                return []
            p = _ABBHTMLParser()
            p.feed(r.text or "")
            return p.results[:limit]
    except Exception:
        return []


def _abb_norm(raw: Dict[str, Any], base: str) -> Dict[str, Any]:
    title    = (raw.get("title") or "").strip()
    cover    = (raw.get("cover") or "").strip()
    url      = (raw.get("url") or "").strip()
    ih       = (raw.get("infohash") or "").strip().lower()
    magnet   = (raw.get("magnet") or "").strip()
    author   = (raw.get("author") or "").strip()
    narrator = (raw.get("narrator") or "").strip()

    if cover and cover.startswith("/"):
        cover = base + cover
    if url and url.startswith("/"):
        url = base + url
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


@app.get("/api/v1/abb/search")
async def abb_search_proxy(req: Request, query: str = "", limit: int = 10):
    _require_ui_api_key(req)
    q = query.strip()
    if not q:
        raise HTTPException(status_code=400, detail="query is required")
    lim = max(1, min(50, limit))

    if not ABB_URL:
        return JSONResponse({"success": False, "error": "ABB_URL not configured", "results": []})

    base = ABB_URL.rstrip("/")
    raw = await _abb_scrape(q, lim)
    results = [_abb_norm(r, base) for r in raw]

    # Concurrently fetch infohashes for results that don't have one (first 5)
    missing = [r for r in results if r.get("needs_detail") and r.get("url")][:5]
    if missing and httpx:
        async def _enrich(item: Dict[str, Any]):
            rel = item["url"]
            if rel.startswith(base):
                rel = rel[len(base):]
            ih = await _abb_detail_ih(rel)
            if ih:
                item["infohash"] = ih
                item["needs_detail"] = False
                try:
                    item["magnet"] = magnet_from_btih(ih)
                except Exception:
                    item["magnet"] = f"magnet:?xt=urn:btih:{ih}"
        await asyncio.gather(*[_enrich(r) for r in missing], return_exceptions=True)

    return JSONResponse({"success": True, "results": results, "source": "abb"})


@app.post("/api/v1/abb/download")
async def abb_download_proxy(req: Request):
    """
    Accepts { title, infohash } from the UI.
    Builds a magnet and injects it into YOUR Transmission queue —
    completely bypassing ABB's own torrent client.
    """
    _require_ui_api_key(req)
    data = await req.json()
    title    = (data.get("title") or "").strip()
    infohash = (data.get("infohash") or "").strip().lower()

    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    if not infohash or not re.match(r'^[0-9a-f]{32,40}$', infohash):
        raise HTTPException(status_code=400, detail="valid infohash required (32–40 hex chars)")

    try:
        magnet = magnet_from_btih(infohash)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not build magnet: {e}")

    async with QUEUE_LOCK:
        existing = BTIH_INDEX.get(infohash)
        if existing and existing in QUEUE:
            st = QUEUE[existing].get("status")
            if st in ("queued", "downloading"):
                return JSONResponse({"success": True, "id": existing, "deduped": True, "message": "Already in queue"})

    ip = req.client.host if req.client else "unknown"
    blocked = await _rate_limit_check(ip)
    if blocked:
        return JSONResponse({"success": False, "error": "rate_limited", "message": blocked}, status_code=429)

    entry = await _queue_add(title=title, site="audiobookbay", magnet=magnet, btih=infohash)
    asyncio.create_task(_add_to_transmission(entry))
    return JSONResponse({"success": True, "id": entry["id"]})


@app.get("/api/v1/abb/image-proxy")
async def abb_image_proxy(req: Request, url: str = ""):
    """Server-side cover image proxy — prevents CORS and hotlink issues."""
    _require_ui_api_key(req)
    img_url = url.strip()
    if not img_url.startswith("http"):
        raise HTTPException(status_code=400, detail="Invalid URL")

    if ABB_URL:
        allowed = urlparse(ABB_URL).netloc
        requested = urlparse(img_url).netloc
        if allowed and requested and allowed != requested and "audiobookbay" not in requested:
            raise HTTPException(status_code=403, detail="Image URL not from configured ABB host")

    if not httpx:
        raise HTTPException(status_code=500, detail="httpx not installed")

    try:
        hdrs = {"User-Agent": ABB_USER_AGENT}
        if ABB_COOKIE:
            hdrs["Cookie"] = ABB_COOKIE
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as c:
            r = await c.get(img_url, headers=hdrs)
            if r.status_code >= 400:
                raise HTTPException(status_code=502, detail=f"Upstream {r.status_code}")
            ct = r.headers.get("content-type", "image/jpeg")
            return _FResponse(bytes(r.content), media_type=ct,
                              headers={"Cache-Control": "public, max-age=86400"})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Image fetch failed: {type(e).__name__}")

# ============================================================
# END AudiobookBay Proxy Routes
# ============================================================
'''

INJECTION_MARKER = "\nfrom pathlib import Path as _Path\n"

original = MAIN_PY.read_text(encoding="utf-8")

if "/api/v1/abb/search" in original:
    print("\nmain.py: ABB routes already present — skipping (no change made)")
elif INJECTION_MARKER not in original:
    print("\nERROR: Could not find injection point in main.py.")
    print("  Expected to find the line:  from pathlib import Path as _Path")
    print("  Patch NOT applied to main.py.")
else:
    idx = original.index(INJECTION_MARKER)
    patched = original[:idx] + ABB_ROUTES + original[idx:]
    MAIN_PY.write_text(patched, encoding="utf-8")
    print(f"\nmain.py: Injected {len(ABB_ROUTES.splitlines())} lines of ABB routes ✓")


# ══════════════════════════════════════════════════════════════════════════════
# 2.  static/app.js patch
# ══════════════════════════════════════════════════════════════════════════════

ABB_JS_HELPERS = r'''
// ── ABB: result card renderer ────────────────────────────────────────────────
function abbRow(item){
  const title    = item.title || "(untitled)";
  const cover    = item.cover || "";
  const infohash = item.infohash || "";
  const url      = item.url || "";
  const author   = item.author || "";
  const narrator = item.narrator || "";
  const needsDet = item.needs_detail && !infohash;

  const proxied = cover
    ? `${API}/api/v1/abb/image-proxy?url=${encodeURIComponent(cover)}`
    : "";

  const canDl = !!infohash;
  const meta  = [
    author   ? `<b>Author:</b> ${esc(author)}`   : "",
    narrator ? `<b>Narrator:</b> ${esc(narrator)}` : "",
  ].filter(Boolean).join(" • ");

  const extLink = url ? `<a href="${esc(url)}" target="_blank" rel="noreferrer">ABB page ↗</a>` : "";

  return `
    <div class="track abbTrack"
      data-site="audiobookbay"
      data-title="${esc(title)}"
      data-infohash="${esc(infohash)}">

      ${proxied ? `<div class="abbCover">
        <img src="${esc(proxied)}" alt="${esc(title)}" loading="lazy"
             onerror="this.parentElement.style.display='none'" />
      </div>` : ""}

      <div class="meta" style="flex:1;">
        <p class="t">${esc(title)} <span class="pill info abbBadge">ABB</span></p>
        ${meta ? `<p class="a">${meta}</p>` : ""}
        <div class="mini">${extLink}</div>
        ${needsDet ? `<div class="mini" style="color:var(--orange);margin-top:4px;">
          ⚠ Infohash not resolved — open the ABB page to download manually
        </div>` : ""}
      </div>

      <div class="rightActions">
        <button class="btn sm primary abbDlBtn" ${canDl ? "" : "disabled"}
          title="${canDl ? "Queue via your Transmission" : "No infohash available"}">
          Download
        </button>
      </div>
    </div>`;
}

// ── ABB: download handler ─────────────────────────────────────────────────────
async function abbTriggerDownload(trackEl, btn){
  const title    = trackEl.getAttribute("data-title") || "(untitled)";
  const infohash = trackEl.getAttribute("data-infohash") || "";

  if(!infohash){ toast("No infohash — cannot queue", "warn"); return; }

  btn.disabled = true;
  btn.textContent = "Queuing…";
  toast("Queuing audiobook…", "info");

  try{
    const res = await apiFetch(`${API}/api/v1/abb/download`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ title, infohash }),
    });
    const data = await res.json().catch(() => ({}));

    if(!res.ok || !data?.success){
      const msg = data?.message || data?.detail || data?.error || `HTTP ${res.status}`;
      btn.disabled = false;
      btn.textContent = "Download";
      toast(msg, "bad");
      return;
    }
    btn.textContent = "Queued ✓";
    toast(data?.deduped ? "Already in queue (deduped)" : "Queued — check Download Queue", "ok");
  }catch(e){
    btn.disabled = false;
    btn.textContent = "Download";
    toast("ABB download request failed", "bad");
  }
}
// ── END ABB helpers ──────────────────────────────────────────────────────────
'''

NEW_DO_SEARCH = r'''async function doSearch(){
  const site = $("site")?.value || "piratebay";
  const q = ($("q")?.value || "").trim();
  const limit = $("limit")?.value || "10";

  if(!q){ setStatus(pill("warn", "Enter a search query")); return; }

  setStatus(pill("info", "Searching…"));
  const results = $("results");
  if (results) results.innerHTML = "";

  // ── AudiobookBay path ──────────────────────────────────────────────────────
  if(site === "audiobookbay"){
    try{
      const res = await apiFetch(
        `${API}/api/v1/abb/search?query=${encodeURIComponent(q)}&limit=${encodeURIComponent(limit)}`
      );
      const data = await res.json().catch(() => ({}));
      if(!res.ok || !data?.success){
        setStatus(pill("bad", data?.error || data?.detail || `HTTP ${res.status}`));
        return;
      }
      const items = data.results || [];
      if(items.length === 0){ setStatus(pill("warn", "No results from AudiobookBay")); return; }
      if(results) results.innerHTML = items.map(it => abbRow(it)).join("");
      setStatus(pill("ok", `Found ${items.length} result(s) via AudiobookBay`));
    }catch(e){
      setStatus(pill("bad", "ABB search failed"));
    }
    return;
  }
  // ── Standard path (unchanged) ──────────────────────────────────────────────

  const url = `${API}/api/v1/search?site=${encodeURIComponent(site)}&query=${encodeURIComponent(q)}&limit=${encodeURIComponent(limit)}`;
  try{
    const res = await apiFetch(url);
    const data = await res.json().catch(() => ({}));
    if(!res.ok){
      setStatus(pill("bad", data?.error || data?.detail || `HTTP ${res.status}`));
      return;
    }
    const items = data?.data || [];
    if(items.length === 0){ setStatus(pill("warn", "No results")); return; }
    if (results) results.innerHTML = items.map(it => row(it, site)).join("");
    setStatus(pill("ok", `Found ${items.length} result(s)`));
  }catch(e){
    setStatus(pill("bad", "Request failed"));
  }
}'''

NEW_WIRE_BUTTONS = r'''function wireDownloadButtons(){
  const results = $("results");
  if(!results) return;

  results.addEventListener("click", async (e) => {
    // ABB download button
    const abbBtn = e.target?.closest?.(".abbDlBtn");
    if(abbBtn){
      const track = abbBtn.closest(".abbTrack");
      if(track) await abbTriggerDownload(track, abbBtn);
      return;
    }
    // Standard download button (unchanged)
    const btn = e.target?.closest?.(".dlBtn");
    if(!btn) return;
    const trackEl = btn.closest(".track");
    if(!trackEl) return;
    triggerDownload(trackEl, btn);
  });
}'''

js_src = APP_JS.read_text(encoding="utf-8")

if "abbRow" in js_src:
    print("app.js:  ABB code already present — skipping")
else:
    # 1. Replace doSearch
    import re as _re
    old_ds = _re.search(r'async function doSearch\(\)\{.*?\n\}', js_src, _re.DOTALL)
    if old_ds:
        js_src = js_src[:old_ds.start()] + NEW_DO_SEARCH + js_src[old_ds.end():]
        print("app.js:  Replaced doSearch() ✓")
    else:
        print("app.js:  WARNING — could not locate doSearch() to replace")

    # 2. Replace wireDownloadButtons
    old_wire = _re.search(r'function wireDownloadButtons\(\)\{.*?\n\}', js_src, _re.DOTALL)
    if old_wire:
        js_src = js_src[:old_wire.start()] + NEW_WIRE_BUTTONS + js_src[old_wire.end():]
        print("app.js:  Replaced wireDownloadButtons() ✓")
    else:
        print("app.js:  WARNING — could not locate wireDownloadButtons() to replace")

    # 3. Inject ABB helpers after the existing row() function
    row_end = _re.search(r'\nfunction wireDownloadButtons\(\)', js_src)
    if row_end:
        js_src = js_src[:row_end.start()] + "\n" + ABB_JS_HELPERS + js_src[row_end.start():]
        print("app.js:  Injected abbRow() + abbTriggerDownload() ✓")
    else:
        # Fallback: append before the DOMContentLoaded listener
        dom_pos = js_src.find("window.addEventListener(\"DOMContentLoaded\"")
        if dom_pos != -1:
            js_src = js_src[:dom_pos] + "\n" + ABB_JS_HELPERS + "\n" + js_src[dom_pos:]
            print("app.js:  Injected ABB helpers before DOMContentLoaded ✓")
        else:
            js_src += "\n" + ABB_JS_HELPERS
            print("app.js:  Appended ABB helpers to end of file ✓")

    APP_JS.write_text(js_src, encoding="utf-8")
    print("app.js:  Written ✓")


# ══════════════════════════════════════════════════════════════════════════════
# 3.  static/app.css patch
# ══════════════════════════════════════════════════════════════════════════════

ABB_CSS = r'''
/* ── AudiobookBay result cards ───────────────────────────────────────────── */
.abbTrack {
  align-items: flex-start;
  gap: 14px;
}
.abbCover {
  flex: 0 0 auto;
  width: 72px;
  height: 100px;
  border-radius: 10px;
  overflow: hidden;
  background: rgba(0,0,0,.25);
  border: 1px solid var(--line);
  display: flex;
  align-items: center;
  justify-content: center;
}
.abbCover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.abbBadge {
  vertical-align: middle;
  font-size: 10px;
  padding: 3px 7px;
  margin-left: 6px;
}
@media (max-width: 860px) {
  .abbTrack { flex-wrap: wrap; }
  .abbCover { width: 56px; height: 78px; }
}
/* ── END ABB styles ──────────────────────────────────────────────────────── */
'''

css_src = APP_CSS.read_text(encoding="utf-8")
if "abbCover" in css_src:
    print("app.css: ABB styles already present — skipping")
else:
    APP_CSS.write_text(css_src + ABB_CSS, encoding="utf-8")
    print("app.css: Appended ABB styles ✓")


print("\n✅ Patch complete. Restart your FastAPI app to apply changes.")
print("   e.g.:  sudo systemctl restart torrent-api   (or however you run it)\n")
