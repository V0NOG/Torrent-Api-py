from __future__ import annotations
import re
import unicodedata
from typing import Optional

_NOISE_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Strip bracketed noise: (Official Video), [Lyric Video], (Color Coded Lyrics), etc.
    (re.compile(
        r'[\(\[]\s*(?:'
        r'official\s+(?:music\s+)?(?:audio|video|lyric(?:s)?(?:\s+video)?|visuali[sz]er?|clip|hd|4k)|'
        r'official\s+(?:audio|video)|'
        r'(?:music\s+)?(?:audio|video)\s+(?:official)|'
        r'official|lyrics?\s*(?:video)?|lyric\s*video|visuali[sz]er?|'
        r'hd\s*(?:audio|video)?|4k\s*(?:audio|video)?|\bHQ\b|\baudio\b|\bvideo\b|'
        r'color\s*coded\s*(?:lyrics?|video)?|'
        r'remaster(?:ed)?(?:\s+\d{4})?|remastered\s+version|'
        r'remastered\s*[-–]?\s*featured\s+in\s+[^\)\]]*|'
        r'featured?\s+in\s+[^\)\]]*|'
        r'from\s+(?:the\s+)?(?:movie|film|album|soundtrack|series|show|vault)[^\)\]]*|'
        r'from\s+["\'\u201c\u201d][^\)\]]*["\'\u201d\u2019][^\)\]]*|'
        r'full\s+(?:song|album|version)|extended\s+version|radio\s+edit|'
        r'original\s+(?:mix|version)|(?:explicit|clean)\s*(?:version)?|'
        r'feat\.?\s*[^\)\]]*|ft\.?\s*[^\)\]]*|'
        r'live(?:\s+at\s+[^\)\]]*)?|acoustic(?:\s+version)?|'
        r'(?:bonus\s+)?demo|(?:bonus\s+)?track|topic|'
        r'auto-generated\s+by\s+youtube|provided\s+to\s+youtube|'
        r'with\s+lyrics|\d{4}\s+remaster(?:ed)?|'
        r'mp3|m4a|flac|320\s*kbps|192\s*kbps'
        r')\s*[\)\]]',
        re.IGNORECASE
    ), ''),
    # Strip trailing noise after dash/pipe
    (re.compile(
        r'\s*[-–|]\s*(?:official\s+(?:music\s+)?(?:audio|video|lyric(?:s)?|visuali[sz]er?)|'
        r'official\s+(?:audio|video)|official|hd|4k|lyrics?|'
        r'color\s*coded\s*(?:lyrics?|video)?)\s*$',
        re.IGNORECASE
    ), ''),
    (re.compile(r'\s*-\s*Topic\s*$', re.IGNORECASE), ''),
    (re.compile(r'Provided to YouTube by\s+[^\n]+', re.IGNORECASE), ''),
    # Strip ｜ Official Music Video ｜ and similar pipe-separated noise segments
    (re.compile(r'\s*[｜|]\s*(?:official\s+(?:music\s+)?(?:video|audio|lyric(?:s)?(?:\s+video)?)|official)\s*(?=[｜|]|$)', re.IGNORECASE), ''),
    # Strip [MP3], [FLAC], [HQ] prefixes/suffixes
    (re.compile(r'^\s*\[(?:mp3|flac|hq|hd|4k|320kbps|192kbps)\]\s*', re.IGNORECASE), ''),
    (re.compile(r'\s*\[(?:mp3|flac|hq|hd|4k|320kbps|192kbps)\]\s*$', re.IGNORECASE), ''),
]

_VERSION_PATTERNS = [
    r'remaster(?:ed)?(?:\s+\d{4})?', r'\d{4}\s+remaster(?:ed)?',
    r'remastered\s+version', r'extended\s+(?:version|mix)',
    r'radio\s+edit', r'original\s+(?:mix|version)',
    r'acoustic(?:\s+version)?', r'live(?:\s+at\s+[^\(\)\[\]]*)?',
    r'(?:explicit|clean)\s*(?:version)?',
]
_VERSION_RE = re.compile(
    r'[\(\[]\s*(' + '|'.join(_VERSION_PATTERNS) + r')\s*[\)\]]',
    re.IGNORECASE
)
_FEAT_RE = re.compile(
    r'[\(\[]\s*(?:feat(?:uring)?|ft)\.?\s+([^\)\]]+)\s*[\)\]]',
    re.IGNORECASE
)

# Placeholder artist prefix used in many library filenames: "NA - Title"
_NA_PREFIX_RE = re.compile(r'^(?:NA|N/A|Unknown)\s*-\s*', re.IGNORECASE)


def _unicode_normalize(s: str) -> str:
    s = unicodedata.normalize('NFD', s)
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return unicodedata.normalize('NFC', s)


def clean_youtube_title(raw: str) -> str:
    s = (raw or '').strip()
    if not s:
        return s
    s = _FEAT_RE.sub('', s)
    for pattern, replacement in _NOISE_PATTERNS:
        s = pattern.sub(replacement, s)
    s = re.sub(r'\s*-\s*$', '', s)
    s = re.sub(r'^\s*-\s*', '', s)
    s = re.sub(r'\s{2,}', ' ', s)
    # Strip standalone noise words (not inside brackets, just bare at end)
    s = re.sub(r'\s*\b(Official|Audio|Video|Music|HQ|HD|4K|Lyric|Lyrics|Visualizer|Visualiser|Topic)(\s+(Official|Audio|Video|Music|HQ|HD|4K|Lyric|Lyrics))*\b\s*$', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s{2,}', ' ', s)
    return s.strip(' -–|')


def _normalize_token(s: str) -> str:
    s = _unicode_normalize(s.lower())
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _strip_na_prefix(s: str) -> str:
    """Remove 'NA - ' placeholder prefix from filenames."""
    return _NA_PREFIX_RE.sub('', s).strip()


def _split_artist_title(raw: str) -> tuple[str, str]:
    for sep in (' - ', ' – ', ' — '):
        if sep in raw:
            parts = raw.split(sep, 1)
            a, t = parts[0].strip(), parts[1].strip()
            # Reject NA placeholder
            if a.lower() not in ('na', 'n/a', 'unknown', ''):
                return a, t
    return '', raw.strip()


def canonicalize(
    artist: str = '',
    title: str = '',
    raw: str = '',
) -> dict:
    working_artist = (artist or '').strip()
    working_title  = (title  or '').strip()
    working_raw    = (raw    or '').strip()

    # Strip NA prefix from title if it snuck in
    working_title  = _strip_na_prefix(working_title)
    working_artist = _strip_na_prefix(working_artist)

    if working_raw and not (working_artist or working_title):
        working_artist, working_title = _split_artist_title(working_raw)

    if not working_title and working_raw:
        working_title = _strip_na_prefix(working_raw)

    clean_title  = clean_youtube_title(working_title)
    clean_artist = clean_youtube_title(working_artist)

    if not clean_title:
        clean_title = working_title

    version_match = _VERSION_RE.search(working_title)
    canonical_version = version_match.group(1).lower().strip() if version_match else ''

    canon_artist = _normalize_token(clean_artist)
    canon_title  = _normalize_token(clean_title)

    # Reject NA as a canonical artist
    if canon_artist in ('na', 'n a', 'n/a', 'unknown'):
        canon_artist = ''

    if canon_artist and canon_title:
        canonical_track_key = f"{canon_artist}|||{canon_title}"
        if canonical_version:
            canonical_track_key += f"|||{canonical_version}"
    elif canon_title:
        canonical_track_key = f"|||{canon_title}"
        if canonical_version:
            canonical_track_key += f"|||{canonical_version}"
    else:
        canonical_track_key = ''

    return {
        'canonical_artist':    canon_artist,
        'canonical_title':     canon_title,
        'canonical_version':   canonical_version,
        'canonical_track_key': canonical_track_key,
        'clean_title':         clean_title,
        'clean_artist':        clean_artist,
    }
