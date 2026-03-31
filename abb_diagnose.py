#!/usr/bin/env python3
"""
abb_diagnose.py
===============
Run on your Pi to see exactly what HTML the ABB container returns,
so we can fix the scraper's CSS selectors to match.

    cd /home/von/Torrent-Api-py
    python3 abb_diagnose.py
"""
import urllib.request
import re
from html.parser import HTMLParser

ABB_URL = "http://127.0.0.1:5078"
QUERY   = "hobbit"

print(f"Fetching {ABB_URL}/search?q={QUERY} ...")

try:
    req = urllib.request.Request(
        f"{ABB_URL}/search?q={QUERY}",
        headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        html = r.read().decode("utf-8", errors="replace")
except Exception as e:
    print(f"ERROR: Could not reach ABB container: {e}")
    print("Is the container running? Try: docker ps")
    raise SystemExit(1)

print(f"Got {len(html)} bytes\n")

# ── 1. Show all div class names ──────────────────────────────────────────────
print("=" * 60)
print("DIV CLASS NAMES found in the page:")
print("=" * 60)
div_classes = re.findall(r'<div[^>]+class=["\']([^"\']+)["\']', html)
unique = sorted(set(div_classes))
for c in unique:
    print(f"  {c}")

# ── 2. Show all <a href> patterns ────────────────────────────────────────────
print("\n" + "=" * 60)
print("LINK HREF PATTERNS (first 30):")
print("=" * 60)
hrefs = re.findall(r'<a[^>]+href=["\']([^"\']+)["\']', html)
for h in hrefs[:30]:
    print(f"  {h}")

# ── 3. Show all <img src> patterns ──────────────────────────────────────────
print("\n" + "=" * 60)
print("IMAGE SRC PATTERNS (first 20):")
print("=" * 60)
imgs = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html)
for i in imgs[:20]:
    print(f"  {i}")

# ── 4. Show first result card HTML (100 chars around "book" or "result") ────
print("\n" + "=" * 60)
print("RAW HTML SNIPPET (first 3000 chars of body):")
print("=" * 60)
body_start = html.find("<body")
if body_start == -1:
    body_start = 0
print(html[body_start:body_start + 3000])
