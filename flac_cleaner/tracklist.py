"""Track lists for batch downloads: Spotify playlist CSV exports and plain "Artist - Title" text.

Exportify (exportify.app) and similar tools export a playlist as CSV with the
Spotify title, artists, album, release date, track/disc number, duration, ISRC
and the 640×640 album image URL. Those values become the tags and cover of the
cleaned files.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

SPOTIFY_LINK_RE = re.compile(r"(?:https?://)?open\.spotify\.com/(?:intl-\w+/)?(track|album|playlist|artist)/(\w+)"
                             r"|spotify:(track|album|playlist|artist):(\w+)")


@dataclass
class TrackInfo:
    title: str
    artists: list[str] = field(default_factory=list)
    album: str = ""
    album_artists: list[str] = field(default_factory=list)
    release_date: str = ""
    disc: int = 0
    track: int = 0
    duration_ms: int = 0
    isrc: str = ""
    image_url: str = ""
    label: str = ""
    copyright: str = ""
    from_spotify: bool = False

    @property
    def artist(self) -> str:
        return ", ".join(self.artists)

    @property
    def duration(self) -> int:
        return round(self.duration_ms / 1000) if self.duration_ms else 0

    def label_text(self) -> str:
        return f"{self.artist} - {self.title}" if self.artists else self.title

    def tags(self) -> dict[str, str]:
        """Tags to write (standard field -> value). Only fields we actually know."""
        t = {"TITLE": self.title}
        if self.artists:
            t["ARTIST"] = self.artist
        if self.album:
            t["ALBUM"] = self.album
        if self.album_artists or self.artists:
            t["ALBUMARTIST"] = ", ".join(self.album_artists or self.artists[:1])
        if self.release_date:
            t["DATE"] = self.release_date
        if self.track:
            t["TRACKNUMBER"] = str(self.track)
        if self.disc:
            t["DISCNUMBER"] = str(self.disc)
        if self.copyright:
            t["COPYRIGHT"] = self.copyright
        if self.label:
            t["PUBLISHER"] = self.label
        if self.isrc:
            t["ISRC"] = self.isrc
        return t


# ------------------------------------------------------------------ search queries

_FEAT_RE = re.compile(r"\s*[\(\[](?:feat\.?|ft\.?|featuring|with)\s[^\)\]]*[\)\]]", re.I)
_DASH_SUFFIX_RE = re.compile(r"\s+-\s+.*$")
_BRACKETS_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")


def clean_title(title: str) -> str:
    """'Song (feat. X) - 2011 Remaster' -> 'Song'. Used for the search words only."""
    t = _FEAT_RE.sub("", title)
    t = _DASH_SUFFIX_RE.sub("", t)
    t = _BRACKETS_RE.sub("", t)
    return t.strip() or title.strip()


def _words(s: str) -> str:
    s = re.sub(r"[^\w\s']", " ", s)
    s = s.replace("'", "")
    return re.sub(r"\s+", " ", s).strip()


def search_queries(t: TrackInfo) -> list[str]:
    """Queries to try, most specific first. Soulseek needs every word to match the file path."""
    title = _words(clean_title(t.title))
    artist = _words(t.artists[0]) if t.artists else ""
    out = []
    if artist:
        out.append(f"{artist} {title}")
    if t.album and artist and _words(t.album).lower() != title.lower():
        out.append(f"{title} {_words(clean_title(t.album))}")
    if len(title.split()) >= 2 or not artist:
        out.append(title)
    seen, uniq = set(), []
    for q in out:
        k = q.lower()
        if k and k not in seen:
            seen.add(k)
            uniq.append(q)
    return uniq


def wanted_words(t: TrackInfo) -> str:
    """Words that legitimately describe this track's version (so 'Live' isn't penalised for a live track)."""
    return f"{t.title} {t.album}"


# ------------------------------------------------------------------ parsing

_COLS = {
    "title": ["track name", "title", "name", "song", "track"],
    "artists": ["artist name(s)", "artist names", "artists", "artist", "artist name"],
    "artist_uris": ["artist uri(s)", "artist uris"],
    "album": ["album name", "album"],
    "album_artists": ["album artist name(s)", "album artist names", "album artist", "album artists"],
    "album_artist_uris": ["album artist uri(s)"],
    "release_date": ["album release date", "release date", "date", "year"],
    "disc": ["disc number", "disc"],
    "track": ["track number", "track no", "track #"],
    "duration_ms": ["track duration (ms)", "duration (ms)", "duration_ms"],
    "duration": ["duration", "length", "time"],
    "isrc": ["isrc"],
    "image_url": ["album image url", "image url", "cover url", "artwork url"],
    "label": ["label", "record label"],
    "copyright": ["copyrights", "copyright"],
}


def _find(header: list[str], key: str) -> int | None:
    low = [h.strip().lower() for h in header]
    for name in _COLS[key]:
        if name in low:
            return low.index(name)
    return None


def _split_names(names: str, uris: str | None) -> list[str]:
    names = (names or "").strip()
    if not names:
        return []
    n = len([u for u in (uris or "").split(",") if u.strip()]) if uris else 0
    if n == 1:
        return [names]
    # Exportify joins names with "," (no space), so "Tyler, The Creator,Someone" is two artists.
    for sep in (r",(?!\s)", r";", r",\s+"):
        parts = [p.strip() for p in re.split(sep, names) if p.strip()]
        if n and len(parts) == n:
            return parts
        if not n and sep == ";" and len(parts) > 1:
            return parts
    return [p.strip() for p in re.split(r",(?!\s)", names) if p.strip()] if n else [names]


def _int(s: str) -> int:
    m = re.match(r"\s*(\d+)", s or "")
    return int(m.group(1)) if m else 0


def _duration_ms(s: str) -> int:
    s = (s or "").strip()
    m = re.match(r"^(\d+):(\d{2})(?::(\d{2}))?$", s)
    if m:
        parts = [int(x) for x in m.groups() if x is not None]
        secs = parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0] * 3600 + parts[1] * 60 + parts[2]
        return secs * 1000
    return _int(s) * 1000 if s.isdigit() and int(s) < 10000 else _int(s)


def _first_copyright(s: str) -> str:
    # Exportify joins copyrights; prefer the (P) phonographic line like the Ginkgo file's COPYRIGHT
    parts = [p.strip() for p in re.split(r"\s*[,;|]\s*(?=[©℗(CP]|\d{4})", s or "") if p.strip()]
    strip = lambda x: re.sub(r"^(?:\(P\)|\(C\)|℗|©|P|C)\s+", "", x).strip()  # noqa: E731
    for p in parts:  # like the Ginkgo file: "2025 Panchiko Limited under exclusive license to …"
        if p.startswith(("℗", "(P)", "P ")):
            return strip(p)
    return strip(parts[0]) if parts else ""


def parse_csv(text: str) -> list[TrackInfo]:
    text = text.lstrip("﻿")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    if not rows:
        return []
    header, body = rows[0], rows[1:]
    col = {k: _find(header, k) for k in _COLS}
    if col["title"] is None:
        raise ValueError("No track title column found (expected 'Track Name' or 'Title').")
    spotify = any("uri" in h.lower() for h in header) or col["image_url"] is not None

    def g(row, key):
        i = col[key]
        return row[i].strip() if i is not None and i < len(row) else ""

    out = []
    for row in body:
        title = g(row, "title")
        if not title:
            continue
        ms = _int(g(row, "duration_ms")) or _duration_ms(g(row, "duration"))
        date = g(row, "release_date")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", date):
            date = date[:10]
        out.append(TrackInfo(
            title=title,
            artists=_split_names(g(row, "artists"), g(row, "artist_uris")),
            album=g(row, "album"),
            album_artists=_split_names(g(row, "album_artists"), g(row, "album_artist_uris")),
            release_date=date, disc=_int(g(row, "disc")), track=_int(g(row, "track")),
            duration_ms=ms, isrc=g(row, "isrc").upper(), image_url=g(row, "image_url"),
            label=g(row, "label"), copyright=_first_copyright(g(row, "copyright")), from_spotify=spotify))
    return out


def parse_text(text: str) -> tuple[list[TrackInfo], list[str]]:
    """Plain lines: 'Artist - Title' (or just a title). Returns (tracks, spotify links found)."""
    tracks, links = [], []
    for line in text.splitlines():
        line = line.strip().strip("﻿")
        if not line or line.startswith("#"):
            continue
        m = SPOTIFY_LINK_RE.search(line)
        if m:
            links.append(line)
            continue
        line = re.sub(r"^\s*\d{1,3}[.)]\s+", "", line)  # "1. Artist - Title"
        parts = re.split(r"\s+[-–—]\s+", line, maxsplit=1)
        if len(parts) == 2:
            tracks.append(TrackInfo(title=parts[1].strip(), artists=[parts[0].strip()]))
        else:
            tracks.append(TrackInfo(title=line))
    return tracks, links


def looks_like_csv(text: str) -> bool:
    first = text.lstrip("﻿").splitlines()[0] if text.strip() else ""
    low = first.lower()
    return ("," in first or ";" in first or "\t" in first) and any(
        n in low for n in ("track name", "title", "artist"))
