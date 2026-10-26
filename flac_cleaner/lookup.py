"""Online HD cover lookup: Cover Art Archive, iTunes Search API, Deezer API.

Only album/artist names and MusicBrainz IDs are sent. No files or other tags
ever leave the computer. Nothing here runs unless the user enables lookup.
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Callable

from . import __version__
from .cover import image_info

USER_AGENT = f"FLACCleaner/{__version__} (desktop FLAC tag cleaner; cover art lookup)"
MAX_IMAGE_BYTES = 60 * 1024 * 1024
CONFIDENT = 0.80
MINIMUM = 0.60

# Minimum seconds between requests per host (iTunes allows ~20 requests/minute).
_HOST_INTERVAL = {"itunes.apple.com": 3.1, "api.deezer.com": 0.25, "coverartarchive.org": 1.0}
_last_call: dict[str, float] = {}
_rate_lock = threading.Lock()


@dataclass
class AlbumQuery:
    artist: str
    album: str
    albumartist: str = ""
    mbid: str = ""          # MUSICBRAINZ_ALBUMID (release)
    rgid: str = ""          # MUSICBRAINZ_RELEASEGROUPID
    total_tracks: int | None = None

    @property
    def search_artist(self) -> str:
        aa = self.albumartist.strip()
        return "" if _is_various(aa) else (aa or self.artist)


@dataclass
class Candidate:
    source: str             # "Cover Art Archive", "iTunes", "Deezer"
    url: str
    label: str              # what the service called the release
    match: float            # 0..1 confidence that this is the same release
    width: int = 0
    height: int = 0
    data: bytes = field(default=b"", repr=False)
    error: str = ""

    @property
    def side(self) -> int:
        return min(self.width, self.height)

    def describe(self) -> str:
        if self.error:
            return f"{self.source}: {self.error}"
        return (f"{self.source}: {self.width}×{self.height}, {len(self.data) / 1024:.0f} KB, "
                f"match {self.match * 100:.0f}% ({self.label})")


class Cancelled(Exception):
    pass


def _is_various(s: str) -> bool:
    return _norm(s) in {"various artists", "various", "va", "verschiedene interpreten"}


_EDITION_RE = re.compile(
    r"[\(\[][^\)\]]*\b(deluxe|edition|remaster(ed)?|expanded|anniversary|version|bonus|explicit|clean|"
    r"super|special|collector'?s?)\b[^\)\]]*[\)\]]", re.I)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").casefold()
    s = _EDITION_RE.sub(" ", s)
    s = re.sub(r"\s+-\s+(single|ep)\s*$", "", s)
    s = s.replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def similarity(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    r = SequenceMatcher(None, na, nb).ratio()
    if (na in nb or nb in na) and min(len(na), len(nb)) >= 4:
        r = max(r, 0.9)
    return r


def score(q: AlbumQuery, artist: str, album: str, track_count: int | None = None) -> float:
    album_s = similarity(q.album, album)
    if q.search_artist:
        artist_s = max(similarity(q.search_artist, artist), similarity(q.artist, artist))
        s = 0.65 * album_s + 0.35 * artist_s
    else:  # compilation: the album title carries the match
        s = 0.9 * album_s + (0.1 if _is_various(artist) else 0.05)
    has_ed = lambda x: bool(_EDITION_RE.search(x or ""))  # noqa: E731
    if has_ed(album) != has_ed(q.album):
        s -= 0.03
    if re.search(r"\s-\s(single|ep)\s*$", album or "", re.I) and (q.total_tracks or 0) > 3:
        s -= 0.15
    if track_count and q.total_tracks:
        diff = abs(track_count - q.total_tracks)
        s -= 0.0 if diff == 0 else (0.04 if diff <= 2 else 0.08)
    return max(0.0, min(1.0, s))


def _wait(host: str, cancel: Callable[[], bool]) -> None:
    gap = _HOST_INTERVAL.get(host, 0.5)
    while True:
        with _rate_lock:
            now = time.monotonic()
            due = _last_call.get(host, 0) + gap
            if now >= due:
                _last_call[host] = now
                return
        if cancel():
            raise Cancelled()
        time.sleep(min(0.2, due - now))


def http_get(url: str, cancel: Callable[[], bool] = lambda: False, limit: int = 5 * 1024 * 1024,
             timeout: float = 25) -> tuple[int, bytes]:
    host = urllib.parse.urlsplit(url).hostname or ""
    _wait(host, cancel)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            chunks, total = [], 0
            while True:
                if cancel():
                    raise Cancelled()
                c = resp.read(256 * 1024)
                if not c:
                    break
                total += len(c)
                if total > limit:
                    raise ValueError("response too large")
                chunks.append(c)
            return resp.status, b"".join(chunks)
    except urllib.error.HTTPError as e:
        return e.code, b""


def _fetch_image(c: Candidate, cancel) -> Candidate:
    try:
        status, data = http_get(c.url, cancel, limit=MAX_IMAGE_BYTES, timeout=60)
        if status != 200 or not data:
            c.error = f"HTTP {status}"
            return c
        info = image_info(data)
        c.width, c.height, c.data = info.width, info.height, data
    except Cancelled:
        raise
    except Exception as e:  # network errors, bad images
        c.error = str(e) or e.__class__.__name__
    return c


# ---------------------------------------------------------------- sources

def from_cover_art_archive(q: AlbumQuery, cancel) -> list[Candidate]:
    out = []
    if q.mbid:
        c = _fetch_image(Candidate("Cover Art Archive", f"https://coverartarchive.org/release/{q.mbid}/front",
                                   "exact MusicBrainz release", 1.0), cancel)
        if not c.error:
            return [c]
        out.append(c)
    if q.rgid:
        c = _fetch_image(Candidate("Cover Art Archive",
                                   f"https://coverartarchive.org/release-group/{q.rgid}/front",
                                   "MusicBrainz release group", 0.97), cancel)
        out.append(c)
    return out


def _itunes_search(q: AlbumQuery, country: str, cancel) -> list[dict]:
    term = f"{q.search_artist} {q.album}".strip()
    url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(
        {"term": term, "entity": "album", "limit": 15, "country": country})
    status, body = http_get(url, cancel)
    if status != 200:
        return []
    return json.loads(body.decode("utf-8", "replace")).get("results", [])


def from_itunes(q: AlbumQuery, cancel, top: int = 2) -> list[Candidate]:
    results = _itunes_search(q, "US", cancel)
    scored = sorted(((score(q, r.get("artistName", ""), r.get("collectionName", ""), r.get("trackCount")), r)
                     for r in results if r.get("artworkUrl100")), key=lambda t: -t[0])
    if (not scored or scored[0][0] < CONFIDENT) and re.search(r"[぀-ヿ一-鿿가-힯]",
                                                               q.album + q.artist):
        jp = _itunes_search(q, "JP", cancel)
        scored = sorted(scored + [(score(q, r.get("artistName", ""), r.get("collectionName", ""),
                                         r.get("trackCount")), r) for r in jp if r.get("artworkUrl100")],
                        key=lambda t: -t[0])
    out = []
    for s, r in scored[:top]:
        if s < MINIMUM:
            break
        base = r["artworkUrl100"]
        label = f"{r.get('artistName', '')} – {r.get('collectionName', '')}"
        c = _fetch_image(Candidate("iTunes", base.replace("100x100bb", "3000x3000bb"), label, s), cancel)
        if c.error:
            c = _fetch_image(Candidate("iTunes", base.replace("100x100bb", "1400x1400bb"), label, s), cancel)
        out.append(c)
    return out


def from_deezer(q: AlbumQuery, cancel, top: int = 2) -> list[Candidate]:
    queries = []
    if q.search_artist:
        queries.append(f'artist:"{q.search_artist}" album:"{q.album}"')
    queries.append(f"{q.search_artist} {q.album}".strip())
    results: list[dict] = []
    for qs in queries:
        status, body = http_get("https://api.deezer.com/search/album?" + urllib.parse.urlencode({"q": qs}), cancel)
        if status == 200:
            results = json.loads(body.decode("utf-8", "replace")).get("data", []) or []
        if results:
            break
    scored = sorted(((score(q, (r.get("artist") or {}).get("name", ""), r.get("title", ""), r.get("nb_tracks")), r)
                     for r in results if r.get("cover_xl")), key=lambda t: -t[0])
    out = []
    for s, r in scored[:top]:
        if s < MINIMUM:
            break
        label = f"{(r.get('artist') or {}).get('name', '')} – {r.get('title', '')}"
        out.append(_fetch_image(Candidate("Deezer", r["cover_xl"], label, s), cancel))
    return out


SOURCES = [from_cover_art_archive, from_itunes, from_deezer]


def find_candidates(q: AlbumQuery, cancel: Callable[[], bool] = lambda: False,
                    log: Callable[[str], None] = lambda s: None) -> list[Candidate]:
    cands: list[Candidate] = []
    for src in SOURCES:
        if cancel():
            raise Cancelled()
        try:
            found = src(q, cancel)
        except Cancelled:
            raise
        except Exception as e:
            log(f"{src.__name__}: {e}")
            continue
        cands.extend(found)
        # An exact MusicBrainz release cover that is already large is as good as it gets.
        if any(c.match >= 1.0 and not c.error and c.side >= 1400 for c in found):
            break
    return cands


def choose_best(cands: list[Candidate], target: int | None) -> Candidate | None:
    """Pick the best cover: confident match first, then resolution (up to the target size), then match."""
    ok = [c for c in cands if not c.error and c.data and c.match >= MINIMUM]
    if not ok:
        return None
    cap = target or 10 ** 9
    tier = lambda c: 2 if c.match >= 0.95 else (1 if c.match >= CONFIDENT else 0)  # noqa: E731
    squareness = lambda c: c.side / max(c.width, c.height, 1)  # noqa: E731
    return max(ok, key=lambda c: (tier(c), min(c.side, cap), round(c.match, 2), squareness(c) > 0.95, c.side))
