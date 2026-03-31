#!/usr/bin/env python3
"""
fix_dosearch.py
===============
Fixes the doSearch() replacement that the previous patch missed.
Run from inside your Torrent-Api-py directory:

    cd /home/von/Torrent-Api-py
    python3 fix_dosearch.py
"""
import re
from pathlib import Path

APP_JS = Path("static/app.js")
if not APP_JS.exists():
    print("ERROR: static/app.js not found. Run from inside Torrent-Api-py/")
    raise SystemExit(1)

src = APP_JS.read_text(encoding="utf-8")

# Check if already patched
if "api/v1/abb/search" in src:
    print("doSearch() already contains ABB path — nothing to do.")
    raise SystemExit(0)

# ------------------------------------------------------------------
# Find doSearch using a line-by-line brace counter (handles any
# whitespace/formatting style)
# ------------------------------------------------------------------
lines = src.splitlines(keepends=True)

start_idx = None
for i, line in enumerate(lines):
    if re.search(r'async\s+function\s+doSearch\s*\(', line):
        start_idx = i
        break

if start_idx is None:
    print("ERROR: Could not find 'async function doSearch(' in app.js")
    raise SystemExit(1)

# Walk forward counting braces to find the closing }
depth = 0
end_idx = None
for i in range(start_idx, len(lines)):
    depth += lines[i].count('{') - lines[i].count('}')
    if depth == 0 and i > start_idx:
        end_idx = i
        break

if end_idx is None:
    print("ERROR: Could not find closing brace of doSearch()")
    raise SystemExit(1)

old_fn = "".join(lines[start_idx:end_idx+1])
print(f"Found doSearch() at lines {start_idx+1}–{end_idx+1} ({len(old_fn.splitlines())} lines)")

NEW_DO_SEARCH = r"""async function doSearch(){
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
}"""

# Replace in source
new_src = src[:src.index(old_fn)] + NEW_DO_SEARCH + src[src.index(old_fn) + len(old_fn):]

APP_JS.write_text(new_src, encoding="utf-8")
print("app.js: doSearch() replaced with ABB-aware version ✓")
print("\nDone. Restart your FastAPI app:")
print("  sudo systemctl restart torrent-api-py.service")
