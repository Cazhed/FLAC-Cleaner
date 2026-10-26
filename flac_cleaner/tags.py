"""Tag cleaning: whitelist, merge duplicate variants, privacy stripping, date normalization."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

STANDARD_FIELDS = [
    "TITLE", "ARTIST", "ALBUM", "ALBUMARTIST", "DATE", "TRACKNUMBER",
    "TOTALTRACKS", "DISCNUMBER", "TOTALDISCS", "COPYRIGHT", "PUBLISHER",
]


def norm_key(key: str) -> str:
    """Normalize a tag name so spelling variants compare equal.

    "ALBUM ARTIST", "album_artist" and "AlbumArtist" all become "ALBUMARTIST".
    """
    return re.sub(r"[^A-Z0-9]", "", key.upper())


# Standard field -> source tag names (normalized) in priority order.
ALIASES: dict[str, list[str]] = {
    "TITLE": ["TITLE"],
    "ARTIST": ["ARTIST", "ARTISTS"],
    "ALBUM": ["ALBUM"],
    "ALBUMARTIST": ["ALBUMARTIST", "ALBUMARTISTS", "ALBUMARTISTCREDIT", "ALBUMARTISTSCREDIT"],
    "DATE": ["DATE", "RELEASEDATE", "YEAR", "ORIGINALDATE", "ORIGINALYEAR"],
    "TRACKNUMBER": ["TRACKNUMBER", "TRACK", "TRACKNUM"],
    "TOTALTRACKS": ["TOTALTRACKS", "TRACKTOTAL", "TRACKC"],
    "DISCNUMBER": ["DISCNUMBER", "DISC", "DISKNUMBER", "DISCNUM", "DISK"],
    "TOTALDISCS": ["TOTALDISCS", "DISCTOTAL", "DISCC", "DISKTOTAL", "TOTALDISKS"],
    "COPYRIGHT": ["COPYRIGHT", "CPRT"],
    "PUBLISHER": ["PUBLISHER", "ORGANIZATION", "LABEL"],
}

# Optional groups the user can keep. Each maps normalized source names to the
# canonical uppercase Vorbis name, in output order.
OPTIONAL_SIMPLE: dict[str, dict[str, list[str]]] = {
    "genre": {"GENRE": ["GENRE"]},
    "isrc": {"ISRC": ["ISRC", "TSRC"]},
    "label": {"LABEL": ["LABEL"]},
    "lyrics": {"LYRICS": ["LYRICS", "UNSYNCEDLYRICS"]},
}

REPLAYGAIN_CANONICAL = [
    "REPLAYGAIN_TRACK_GAIN", "REPLAYGAIN_TRACK_PEAK", "REPLAYGAIN_ALBUM_GAIN",
    "REPLAYGAIN_ALBUM_PEAK", "REPLAYGAIN_REFERENCE_LOUDNESS",
    "R128_TRACK_GAIN", "R128_ALBUM_GAIN",
]

# Keys that are always removed, whatever the settings (privacy rule).
PRIVACY_KEYS = {norm_key(k) for k in (
    "account_id", "purchase_date", "iTunSMPB", "apple_id", "owner", "user_email",
)}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")

FULL_DATE_RE = re.compile(r"^\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")
COMPACT_DATE_RE = re.compile(r"^\s*(\d{4})(\d{2})(\d{2})\s*$")
YEAR_RE = re.compile(r"^\s*(\d{4})")


@dataclass
class TagOptions:
    keep_genre: bool = False
    keep_isrc: bool = False
    keep_label: bool = False
    keep_musicbrainz: bool = False
    keep_replaygain: bool = False
    keep_lyrics: bool = False
    normalize_date: bool = True
    fill_disc: bool = True  # write DISCNUMBER=1 / TOTALDISCS=1 when missing


@dataclass
class TagResult:
    tags: list[tuple[str, str]]                       # final tags, in output order
    removed: list[str] = field(default_factory=list)  # original keys dropped or merged away
    changed: list[tuple[str, str, str]] = field(default_factory=list)  # (field, old, new)
    privacy_removed: list[str] = field(default_factory=list)  # keys stripped for privacy

    def as_dict(self) -> dict[str, list[str]]:
        d: dict[str, list[str]] = {}
        for k, v in self.tags:
            d.setdefault(k, []).append(v)
        return d


def is_private(key: str, value: str) -> bool:
    return norm_key(key) in PRIVACY_KEYS or bool(EMAIL_RE.search(value or ""))


def _index(tags: list[tuple[str, str]]) -> dict[str, list[str]]:
    """Normalized key -> non-empty, de-duplicated values (privacy-stripped)."""
    idx: dict[str, list[str]] = {}
    for k, v in tags:
        v = (v or "").strip()
        if not v or is_private(k, v):
            continue
        lst = idx.setdefault(norm_key(k), [])
        if v not in lst:
            lst.append(v)
    return idx


def _first(idx: dict[str, list[str]], aliases: list[str]) -> list[str]:
    for a in aliases:
        if idx.get(a):
            return list(idx[a])
    return []


def _clean_int(v: str) -> str:
    v = v.strip()
    return str(int(v)) if v.isdigit() else v


def normalize_date(values: list[str], idx: dict[str, list[str]]) -> list[str]:
    """Return DATE as YYYY-MM-DD when any date tag holds a full date for the same year."""
    if not values:
        return values
    primary = values[0]
    m = FULL_DATE_RE.match(primary) or COMPACT_DATE_RE.match(primary)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return [f"{y:04d}-{mo:02d}-{d:02d}"]
        return [primary]
    ym = YEAR_RE.match(primary)
    if ym:
        for alias in ALIASES["DATE"]:
            for cand in idx.get(alias, []):
                m = FULL_DATE_RE.match(cand) or COMPACT_DATE_RE.match(cand)
                if m and m.group(1) == ym.group(1):
                    mo, d = int(m.group(2)), int(m.group(3))
                    if 1 <= mo <= 12 and 1 <= d <= 31:
                        return [f"{m.group(1)}-{mo:02d}-{d:02d}"]
    return [primary]


def _musicbrainz_name(nk: str) -> str | None:
    if nk.startswith("MUSICBRAINZ") and nk.endswith("ID") and len(nk) > len("MUSICBRAINZID"):
        return "MUSICBRAINZ_" + nk[len("MUSICBRAINZ"):]
    return None


def clean_tags(tags: list[tuple[str, str]], opts: TagOptions | None = None) -> TagResult:
    opts = opts or TagOptions()
    idx = _index(tags)
    out: list[tuple[str, str]] = []
    fields: dict[str, list[str]] = {}

    for f in STANDARD_FIELDS:
        fields[f] = _first(idx, ALIASES[f])

    # "6/8" style track and disc numbers
    for num, total in (("TRACKNUMBER", "TOTALTRACKS"), ("DISCNUMBER", "TOTALDISCS")):
        if fields[num] and "/" in fields[num][0]:
            a, _, b = fields[num][0].partition("/")
            fields[num] = [a.strip()] if a.strip() else []
            if not fields[total] and b.strip():
                fields[total] = [b.strip()]
        fields[num] = [_clean_int(v) for v in fields[num][:1]]
        fields[total] = [_clean_int(v) for v in fields[total][:1]]

    if opts.fill_disc and not fields["DISCNUMBER"] and not fields["TOTALDISCS"]:
        fields["DISCNUMBER"], fields["TOTALDISCS"] = ["1"], ["1"]

    if fields["DATE"]:
        fields["DATE"] = normalize_date(fields["DATE"][:1], idx) if opts.normalize_date else fields["DATE"][:1]

    for f in ("TITLE", "ALBUM", "COPYRIGHT", "PUBLISHER"):
        fields[f] = fields[f][:1]

    for f in STANDARD_FIELDS:
        out.extend((f, v) for v in fields[f])

    # Optional groups
    for group, mapping in OPTIONAL_SIMPLE.items():
        if getattr(opts, f"keep_{group}"):
            for canon, aliases in mapping.items():
                out.extend((canon, v) for v in _first(idx, aliases))
    if opts.keep_musicbrainz:
        seen: dict[str, list[str]] = {}
        for nk, vals in idx.items():
            name = _musicbrainz_name(nk)
            if name:
                lst = seen.setdefault(name, [])
                lst.extend(v for v in vals if v not in lst)
        for name in sorted(seen):
            out.extend((name, v) for v in seen[name])
    if opts.keep_replaygain:
        for canon in REPLAYGAIN_CANONICAL:
            out.extend((canon, v) for v in idx.get(norm_key(canon), [])[:1])

    # Bookkeeping for the preview
    result = TagResult(tags=out)
    final = result.as_dict()
    seen_removed = set()
    for k, v in tags:
        v = (v or "").strip()
        if is_private(k, v):
            if k not in result.privacy_removed:
                result.privacy_removed.append(k)
            continue
        kept_verbatim = k in final and v in final[k]
        if not kept_verbatim and k not in seen_removed and k.upper() not in final:
            seen_removed.add(k)
            result.removed.append(k)
    orig_exact: dict[str, list[str]] = {}
    for k, v in tags:
        if (v or "").strip() and not is_private(k, v):
            orig_exact.setdefault(k.upper(), []).append(v.strip())
    for k, vals in final.items():
        old = orig_exact.get(k, [])
        exact_case = [v for kk, v in tags if kk == k]
        if old != vals:
            result.changed.append((k, "; ".join(old) if old else "(missing)", "; ".join(vals)))
        elif old and not exact_case:
            orig_names = sorted({kk for kk, _ in tags if kk.upper() == k})
            result.changed.append((k, f"field name {', '.join(orig_names)}", f"field name {k} (value unchanged)"))
    return result
