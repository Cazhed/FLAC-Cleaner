"""Turn Soulseek search results into ranked download candidates (songs and album folders).

Pure functions with no network access, so they can be unit-tested.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

AUDIO_EXT = "flac"
IMAGE_NAMES = ("cover", "folder", "front", "albumart", "artwork")
IMAGE_EXTS = (".jpg", ".jpeg", ".png")
# Words that usually mean "not the original recording" unless the user searched for them.
UNWANTED = ["instrumental", "karaoke", "remix", "live", "cover", "acoustic", "sped up", "slowed", "nightcore",
            "8d", "demo", "edit", "radio edit", "remaster", "clean", "a cappella", "acapella", "reverb"]


@dataclass
class FileHit:
    username: str
    path: str                 # full remote path, as needed for the download request
    size: int
    duration: int = 0         # seconds, 0 = unknown
    bitrate: int = 0
    sample_rate: int = 0
    bit_depth: int = 0
    free_slot: bool = False
    speed: int = 0            # bytes/s, as reported by the user
    queue: int = 0
    score: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return split_path(self.path)[-1]

    @property
    def folder(self) -> str:
        return "\\".join(split_path(self.path)[:-1])

    @property
    def ext(self) -> str:
        n = self.name.lower()
        return n.rsplit(".", 1)[-1] if "." in n else ""

    def format_text(self) -> str:
        if self.bit_depth and self.sample_rate:
            return f"FLAC {self.bit_depth}/{self.sample_rate / 1000:g}"
        return "FLAC"


@dataclass
class FolderHit:
    username: str
    folder: str
    tracks: list[FileHit]
    images: list[FileHit] = field(default_factory=list)
    score: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return split_path(self.folder)[-1] if self.folder else "(root)"

    @property
    def size(self) -> int:
        return sum(t.size for t in self.tracks)

    @property
    def free_slot(self) -> bool:
        return self.tracks[0].free_slot if self.tracks else False

    @property
    def speed(self) -> int:
        return self.tracks[0].speed if self.tracks else 0

    @property
    def queue(self) -> int:
        return self.tracks[0].queue if self.tracks else 0

    def format_text(self) -> str:
        depths = sorted({t.bit_depth for t in self.tracks if t.bit_depth})
        rates = sorted({t.sample_rate for t in self.tracks if t.sample_rate})
        if depths and rates:
            return f"FLAC {'/'.join(map(str, depths))}-bit {'/'.join(f'{r / 1000:g}' for r in rates)} kHz"
        return "FLAC"


def split_path(p: str) -> list[str]:
    return [x for x in re.split(r"[\\/]", p) if x]


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").casefold()
    s = s.replace("&", " and ")
    s = re.sub(r"[^\w]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def query_terms(query: str) -> list[str]:
    return [t for t in norm(query).split() if t]


def hits_from_results(results) -> list[FileHit]:
    """Flatten aioslsk SearchResult objects (only visible, unlocked files)."""
    hits = []
    for r in results:
        for f in r.shared_items:
            attrs = {getattr(k, "value", k): v for k, v in f.get_attribute_map().items()}
            hits.append(FileHit(
                username=r.username, path=f.filename, size=f.filesize,
                bitrate=attrs.get(0, 0), duration=attrs.get(1, 0), sample_rate=attrs.get(4, 0),
                bit_depth=attrs.get(5, 0), free_slot=bool(r.has_free_slots), speed=int(r.avg_speed or 0),
                queue=int(r.queue_size or 0)))
    return hits


def _availability(free_slot: bool, speed: int, queue: int) -> tuple[float, list[str]]:
    s, notes = 0.0, []
    if free_slot:
        s += 15
    else:
        s -= 10 + min(queue, 50) / 5
        notes.append(f"no free slot, queue {queue}")
    s += min(speed / 1_000_000, 1.0) * 10  # up to +10 at >= 1 MB/s
    return s, notes


def _quality(bit_depth: int, sample_rate: int, prefer_bits: int | None) -> tuple[float, list[str]]:
    s, notes = 0.0, []
    if prefer_bits and bit_depth:
        if bit_depth == prefer_bits:
            s += 10
        elif bit_depth > prefer_bits:
            s += 5   # usable: can be converted down with dither
            notes.append(f"{bit_depth}-bit (will need conversion)" if prefer_bits == 16 else f"{bit_depth}-bit")
        else:
            s += 2
            notes.append(f"only {bit_depth}-bit")
    elif not bit_depth:
        s += 5
    else:
        s += 8
    if sample_rate and sample_rate > 48000 and prefer_bits == 16:
        s -= 2
    return s, notes


def _unwanted_penalty(text: str, terms: list[str]) -> tuple[float, list[str]]:
    t = " " + norm(text) + " "
    q = " " + " ".join(terms) + " "
    s, notes = 0.0, []
    for w in UNWANTED:
        if f" {w} " in t and f" {w} " not in q:
            s -= 15
            notes.append(f"'{w}' version")
    return s, notes


def score_track(hit: FileHit, terms: list[str], prefer_bits: int | None = 16,
                expected_duration: int = 0, allowed_words: str = "") -> float:
    """0 or less = unusable. Higher is better.

    ``expected_duration`` (seconds, e.g. from Spotify) rewards files of the same length and
    rejects clearly different versions. ``allowed_words`` are words that describe the wanted
    version (e.g. the Spotify title "Song - Live"), so they aren't penalised.
    """
    hit.notes = []
    if hit.ext != AUDIO_EXT:
        return -100
    name, full = norm(hit.name), norm(hit.path)
    if terms:
        in_full = sum(1 for t in terms if t in full.split() or t in full)
        if in_full < len(terms):
            return -50
        in_name = sum(1 for t in terms if t in name)
        match = 30 + 20 * in_name / len(terms)
    else:
        match = 30
    s = match
    if hit.duration:
        if hit.duration < 30:
            hit.notes.append("shorter than 30 s")
            return -40
        kbps = hit.size * 8 / hit.duration / 1000
        if kbps < 320:
            hit.notes.append(f"{kbps:.0f} kbps: not real lossless")
            return -40
        if kbps < 500:
            s -= 10
            hit.notes.append(f"low bitrate for FLAC ({kbps:.0f} kbps)")
    if expected_duration:
        if not hit.duration:
            s -= 3
            hit.notes.append("length unknown")
        else:
            diff = abs(hit.duration - expected_duration)
            if diff <= 3:
                s += 10
            elif diff <= 8:
                s += 2
                hit.notes.append(f"length differs by {diff} s")
            elif diff <= 20:
                s -= 15
                hit.notes.append(f"length differs by {diff} s (other version?)")
            else:
                hit.notes.append(f"length differs by {diff} s: different version")
                return -30
    q, n = _quality(hit.bit_depth, hit.sample_rate, prefer_bits)
    a, n2 = _availability(hit.free_slot, hit.speed, hit.queue)
    u, n3 = _unwanted_penalty(hit.name, terms + query_terms(allowed_words))
    hit.notes += n + n2 + n3
    return s + q + a + u


def rank_tracks(hits: list[FileHit], query: str, prefer_bits: int | None = 16,
                expected_duration: int = 0, allowed_words: str = "") -> list[FileHit]:
    terms = query_terms(query)
    out = []
    seen = set()
    for h in hits:
        key = (h.username, h.path)
        if key in seen:
            continue
        seen.add(key)
        h.score = score_track(h, terms, prefer_bits, expected_duration, allowed_words)
        if h.score > 0:
            out.append(h)
    return sorted(out, key=lambda h: (-h.score, -h.size))


def group_folders(hits: list[FileHit]) -> list[FolderHit]:
    groups: dict[tuple[str, str], FolderHit] = {}
    for h in hits:
        key = (h.username, h.folder)
        g = groups.setdefault(key, FolderHit(h.username, h.folder, []))
        n = h.name.lower()
        if h.ext == AUDIO_EXT:
            g.tracks.append(h)
        elif n.endswith(IMAGE_EXTS):
            g.images.append(h)
    return [g for g in groups.values() if g.tracks]


def score_folder(g: FolderHit, terms: list[str], prefer_bits: int | None = 16) -> float:
    g.notes = []
    folder_text = norm(g.folder)
    if terms:
        hit_terms = sum(1 for t in terms if t in folder_text)
        if hit_terms == 0:
            return -50
        match = 40 * hit_terms / len(terms)
    else:
        match = 20
    tracks = [t for t in g.tracks if t.ext == AUDIO_EXT]
    n = len(tracks)
    s = match + min(n, 12) * 2  # complete albums beat a stray track or two
    if n == 1:
        g.notes.append("only 1 track")
    bad = 0
    for t in tracks:
        if t.duration and (t.duration < 30 or t.size * 8 / t.duration / 1000 < 320):
            bad += 1
    if bad:
        s -= 10 * bad
        g.notes.append(f"{bad} suspicious file(s)")
    depths = {t.bit_depth for t in tracks if t.bit_depth}
    if len(depths) > 1:
        s -= 5
        g.notes.append("mixed bit depths")
    q, nq = _quality(max(depths) if depths else 0, max((t.sample_rate for t in tracks), default=0), prefer_bits)
    a, na = _availability(g.free_slot, g.speed, g.queue)
    u, nu = _unwanted_penalty(g.name, terms)
    if any(norm(i.name).split(" ")[0] in IMAGE_NAMES for i in g.images):
        s += 3
    g.notes += nq + na + nu
    return s + q + a + u


def rank_folders(hits: list[FileHit], query: str, prefer_bits: int | None = 16) -> list[FolderHit]:
    terms = query_terms(query)
    out = []
    for g in group_folders(hits):
        g.score = score_folder(g, terms, prefer_bits)
        if g.score > 0:
            g.tracks.sort(key=lambda t: t.name.lower())
            out.append(g)
    return sorted(out, key=lambda g: (-g.score, -len(g.tracks)))


def pick_cover_image(images: list[FileHit]) -> FileHit | None:
    """The folder image most likely to be the front cover."""
    def rank(i: FileHit):
        first = norm(i.name).split(" ")[0] if norm(i.name) else ""
        return (first in IMAGE_NAMES, first in ("cover", "front", "folder"), i.size)
    return max(images, key=rank) if images else None


# ------------------------------------------------------------ filename -> tags fallback

_TRACK_RE = re.compile(r"^\s*(?:(?P<disc>\d)[-.])?(?P<track>\d{1,3})\s*[-._)]*\s*(?P<rest>.+)$")


def tags_from_path(path: str) -> dict[str, str]:
    """Best-effort tags from a path like 'Artist - Album (2025)/02 - Artist - Title.flac'."""
    parts = split_path(path)
    name = re.sub(r"\.flac$", "", parts[-1], flags=re.I)
    folder = parts[-2] if len(parts) > 1 else ""
    out: dict[str, str] = {}
    m = _TRACK_RE.match(name)
    if m and not re.match(r"^\d{4}\b", name):  # don't treat a leading year as a track number
        out["TRACKNUMBER"] = str(int(m.group("track")))
        if m.group("disc"):
            out["DISCNUMBER"] = m.group("disc")
        name = m.group("rest")
    pieces = [p.strip() for p in re.split(r"\s+-\s+", name) if p.strip()]
    if len(pieces) >= 2:
        out["ARTIST"], out["TITLE"] = pieces[0], " - ".join(pieces[1:])
    elif pieces:
        out["TITLE"] = pieces[0]
    fm = re.match(r"^(?P<artist>.+?)\s+-\s+(?P<album>.+?)(?:\s*[\(\[](?P<year>\d{4})[\)\]])?(?:\s*[\(\[].*)?$", folder)
    if fm:
        out.setdefault("ARTIST", fm.group("artist").strip())
        out["ALBUMARTIST"] = fm.group("artist").strip()
        out["ALBUM"] = fm.group("album").strip()
        if fm.group("year"):
            out["DATE"] = fm.group("year")
    return out
