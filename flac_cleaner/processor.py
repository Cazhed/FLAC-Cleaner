"""Scanning, planning (preview) and applying changes to FLAC files."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Callable

from mutagen._vorbis import VComment
from mutagen.flac import FLAC

from . import __version__, audio, dsp
from .cover import ImageInfo, image_info, is_clean_jpeg, make_picture, process_cover
from .flacmeta import (PICTURE, SEEKTABLE, STREAMINFO, VORBIS_COMMENT, FlacLayout, ordered_blocks,
                       read_layout, seektable_is_useful, streaminfo_md5, write_flac)
from .lookup import AlbumQuery, Candidate, Cancelled, choose_best, find_candidates
from .naming import fields_from_tags, render, sanitize, unique_path
from .presets import Settings, app_dir
from .tags import ALIASES, TagResult, _first, _index, clean_tags

TEMP_PREFIX = ".flaccleaner-"
BACKUP_DIR = "FLAC Cleaner backup"
LOG_NAME = "flac-cleaner-log.json"
RESAMPLE_FROM = {48000, 88200, 96000, 176400, 192000}
SUSPECT_VENDOR = ("lavf", "itunes", "@")


@dataclass
class TrackItem:
    path: str
    root: str
    bits: int = 0
    rate: int = 0
    channels: int = 0
    duration: float = 0.0
    md5_set: bool = True
    vendor: str = ""
    tags: list[tuple[str, str]] = field(default_factory=list)
    cover: ImageInfo | None = None
    picture_count: int = 0
    error: str = ""
    manual_cover: bytes | None = field(default=None, repr=False)
    manual_cover_name: str = ""
    found: Candidate | None = field(default=None, repr=False)
    lookup_note: str = ""
    looked_up: bool = False
    status: str = "Ready"

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    def tag(self, std: str) -> str:
        v = _first(_index(self.tags), ALIASES.get(std, [std]))
        return v[0] if v else ""

    def album_query(self) -> AlbumQuery:
        idx = _index(self.tags)
        total = self.tag("TOTALTRACKS") or (self.tag("TRACKNUMBER").partition("/")[2])
        return AlbumQuery(
            artist=self.tag("ARTIST"), album=self.tag("ALBUM"), albumartist=self.tag("ALBUMARTIST"),
            mbid=(idx.get("MUSICBRAINZALBUMID") or [""])[0],
            rgid=(idx.get("MUSICBRAINZRELEASEGROUPID") or [""])[0],
            total_tracks=int(total) if total.strip().isdigit() else None)

    def album_key(self) -> str:
        q = self.album_query()
        if q.mbid:
            return "mbid:" + q.mbid.lower()
        return "name:" + (q.albumartist or q.artist).casefold().strip() + "\x00" + q.album.casefold().strip()


@dataclass
class Plan:
    item: TrackItem
    dst: str = ""
    tags: TagResult | None = None
    cover_action: str = "keep"       # keep | embed | none
    cover_text: str = ""
    cover_jpeg: bytes | None = field(default=None, repr=False)
    cover_warning: str = ""
    new_bits: int = 0
    new_rate: int = 0
    conversion_text: str = ""
    error: str = ""

    @property
    def converts(self) -> bool:
        return (self.new_bits, self.new_rate) != (self.item.bits, self.item.rate)


# ------------------------------------------------------------------ scanning

def collect_flac_paths(paths: list[str]) -> list[tuple[str, str]]:
    """Expand files/folders into (file, root) pairs. Root is the folder the user added."""
    out, seen = [], set()
    for p in paths:
        p = os.path.abspath(p)
        if os.path.isdir(p):
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames[:] = sorted(d for d in dirnames if d != BACKUP_DIR)
                for fn in sorted(filenames):
                    if fn.lower().endswith(".flac") and not fn.startswith(TEMP_PREFIX):
                        out.append((os.path.join(dirpath, fn), p))
        elif p.lower().endswith(".flac") and os.path.isfile(p):
            out.append((p, os.path.dirname(p)))
    uniq = []
    for f, r in out:
        k = os.path.normcase(f)
        if k not in seen:
            seen.add(k)
            uniq.append((f, r))
    return uniq


def scan_file(path: str, root: str) -> TrackItem:
    item = TrackItem(path=path, root=root)
    try:
        f = FLAC(path)
        i = f.info
        item.bits, item.rate, item.channels = i.bits_per_sample, i.sample_rate, i.channels
        item.duration, item.md5_set = i.length, bool(i.md5_signature)
        item.tags = list(f.tags) if f.tags else []
        item.vendor = f.tags.vendor if f.tags else ""
        item.picture_count = len(f.pictures)
        pic = _front_picture(f)
        if pic is not None:
            try:
                item.cover = image_info(pic.data)
            except Exception:
                item.cover = ImageInfo(pic.width, pic.height, "unreadable", len(pic.data))
    except Exception as e:
        item.error = str(e) or e.__class__.__name__
        item.status = "Error: " + item.error
    return item


def _front_picture(f: FLAC):
    pics = f.pictures
    front = [p for p in pics if p.type == 3]
    return (front or pics or [None])[0]


def existing_cover_bytes(path: str) -> bytes | None:
    try:
        p = _front_picture(FLAC(path))
        return p.data if p is not None else None
    except Exception:
        return None


# ------------------------------------------------------------------ cover lookup

def needs_cover(item: TrackItem, s: Settings) -> bool:
    return s.cover_enabled and (item.cover is None or item.cover.side < s.cover_min_px)


def lookup_covers(items: list[TrackItem], s: Settings, cancel: Callable[[], bool],
                  progress: Callable[[int, int, str], None], force: bool = False) -> None:
    groups: dict[str, list[TrackItem]] = {}
    for it in items:
        if it.error or it.manual_cover or not needs_cover(it, s) or (it.looked_up and not force):
            continue
        groups.setdefault(it.album_key(), []).append(it)
    target = s.cover_size if s.cover_resize else None
    for n, (key, members) in enumerate(groups.items(), 1):
        q = members[0].album_query()
        label = f"{q.albumartist or q.artist} – {q.album}" if q.album else members[0].name
        progress(n - 1, len(groups), f"Looking up cover: {label}")
        if not q.album and not q.mbid:
            note, best = "No ALBUM tag, can't search", None
        else:
            notes: list[str] = []
            cands = find_candidates(q, cancel, notes.append)
            best = choose_best(cands, target)
            lines = [("★ " if c is best else "  ") + c.describe() for c in cands] + notes
            note = "\n".join(lines) if lines else "No results"
        for it in members:
            it.found, it.lookup_note, it.looked_up = best, note, True
    progress(len(groups), len(groups), "Cover lookup finished")


# ------------------------------------------------------------------ planning

class _CoverCache:
    def __init__(self):
        self._d: dict[tuple, bytes] = {}

    def get(self, data: bytes, size: int | None) -> bytes:
        key = (hashlib.sha1(data).hexdigest(), size)
        if key not in self._d:
            self._d[key] = process_cover(data, size)
        return self._d[key]


def output_dir_for(item: TrackItem, s: Settings) -> str:
    if s.overwrite_originals:
        return os.path.dirname(item.path)
    base = s.output_dir
    if s.mirror_folders:
        rel = os.path.relpath(os.path.dirname(item.path), item.root)
        if rel != "." and not rel.startswith(".."):
            return os.path.join(base, rel)
    return base


def build_plans(items: list[TrackItem], s: Settings, cancel: Callable[[], bool] = lambda: False,
                progress: Callable[[int, int, str], None] = lambda *a: None) -> list[Plan]:
    if not s.overwrite_originals and not s.output_dir:
        raise ValueError("Choose an output folder first (or enable 'Overwrite originals').")
    cache = _CoverCache()
    taken: set[str] = set()
    target = s.cover_size if s.cover_resize else None
    plans = []
    for n, it in enumerate(items):
        if cancel():
            raise Cancelled()
        progress(n, len(items), f"Planning {it.name}")
        p = Plan(item=it, new_bits=it.bits, new_rate=it.rate)
        plans.append(p)
        if it.error:
            p.error = it.error
            continue
        try:
            p.tags = clean_tags(it.tags, s.tag_options())
            # -- filename
            stem = os.path.splitext(it.name)[0]
            if s.rename:
                stem = render(s.template, fields_from_tags(p.tags.as_dict()), s.spaces_to_underscores, stem)
            else:
                stem = sanitize(stem) or "Untitled"
            p.dst = unique_path(output_dir_for(it, s), stem, ".flac", taken,
                                allow=it.path if s.overwrite_originals else None,
                                underscore=s.spaces_to_underscores)
            # -- cover
            _plan_cover(p, s, cache, target)
            # -- conversion
            if s.convert_16bit and it.bits > 16:
                p.new_bits = 16
            if s.resample_44k and it.rate in RESAMPLE_FROM:
                p.new_rate = 44100
            if p.converts and p.new_bits not in (16, 24):
                p.new_bits = 24 if p.new_bits > 16 else 16
            if p.converts:
                parts = []
                if p.new_bits != it.bits:
                    parts.append(f"{it.bits}→{p.new_bits}-bit (TPDF dither)")
                if p.new_rate != it.rate:
                    parts.append(f"{it.rate / 1000:g}→{p.new_rate / 1000:g} kHz")
                p.conversion_text = ", ".join(parts)
            else:
                p.conversion_text = "None (audio copied bit-exact)"
        except Exception as e:
            p.error = str(e) or e.__class__.__name__
    progress(len(items), len(items), "Preview ready")
    return plans


def _plan_cover(p: Plan, s: Settings, cache: _CoverCache, target: int | None) -> None:
    it = p.item
    if not s.cover_enabled:
        p.cover_action, p.cover_text = "keep", "Unchanged (cover handling off)"
        return
    source_bytes, source_label = None, ""
    if it.manual_cover:
        source_bytes, source_label = it.manual_cover, f"your image ({it.manual_cover_name})"
    elif needs_cover(it, s) and it.found is not None and (it.cover is None or it.found.side > it.cover.side):
        source_bytes = it.found.data
        source_label = f"{it.found.source}, match {it.found.match * 100:.0f}%"
    if source_bytes is not None:
        jpeg = cache.get(source_bytes, target)
        info = image_info(jpeg)
        p.cover_action, p.cover_jpeg = "embed", jpeg
        p.cover_text = f"Embed {info.width}×{info.height} JPEG, {info.size_bytes / 1024:.0f} KB from {source_label}"
        if info.side < 600:
            p.cover_warning = f"Cover is only {info.side} px (smaller than 600 px)"
        return
    if it.cover is None:
        p.cover_action = "none"
        p.cover_text = "No cover" + (" (lookup found nothing)" if it.looked_up else
                                     " (enable online lookup or drop an image)")
        p.cover_warning = "No cover"
        return
    low = it.cover.side < s.cover_min_px
    if s.cover_normalize_existing and not is_clean_jpeg(it.cover, target):
        data = existing_cover_bytes(it.path)
        if data:
            jpeg = cache.get(data, target)
            info = image_info(jpeg)
            p.cover_action, p.cover_jpeg = "embed", jpeg
            p.cover_text = (f"Re-encode existing {it.cover.width}×{it.cover.height} {it.cover.fmt} → "
                            f"{info.width}×{info.height} baseline JPEG, {info.size_bytes / 1024:.0f} KB")
    elif it.picture_count > 1:
        data = existing_cover_bytes(it.path)
        if data:
            p.cover_action, p.cover_jpeg = "embed", data
            p.cover_text = f"Keep {it.cover.describe()} (drop {it.picture_count - 1} extra picture(s))"
    if p.cover_action == "keep":
        p.cover_text = f"Keep existing {it.cover.describe()}"
    if low:
        p.cover_warning = f"Low-res cover ({it.cover.side} px)" + (", no better cover found" if it.looked_up else "")


# ------------------------------------------------------------------ applying

def _vendor(item: TrackItem) -> str:
    v = item.vendor or ""
    if not v or any(x in v.lower() for x in SUSPECT_VENDOR):
        return f"FLAC Cleaner {__version__}"
    return v


def _vorbis_block(tags: list[tuple[str, str]], vendor: str) -> bytes:
    vc = VComment()
    vc.vendor = vendor
    for k, v in tags:
        vc.append((k, v))
    return vc.write(framing=False)


def _tmp(folder: str) -> str:
    return os.path.join(folder, f"{TEMP_PREFIX}{uuid.uuid4().hex[:12]}.tmp")


def apply_plan(p: Plan, s: Settings) -> dict:
    """Write one file according to its plan. Returns a log entry."""
    it = p.item
    entry = {
        "source": it.path, "output": p.dst, "old_name": it.name, "new_name": os.path.basename(p.dst),
        "tags_removed": p.tags.removed if p.tags else [],
        "privacy_removed": p.tags.privacy_removed if p.tags else [],
        "tags_changed": [list(c) for c in p.tags.changed] if p.tags else [],
        "cover": p.cover_text, "conversion": p.conversion_text, "backup": None,
    }
    out_dir = os.path.dirname(p.dst)
    os.makedirs(out_dir, exist_ok=True)
    temps: list[str] = []
    try:
        src_layout = read_layout(it.path)
        base = it.path
        vendor = _vendor(it)
        if p.converts:
            pcm = audio.decode(it.path, it.channels, it.bits)
            x = dsp.resample(pcm, it.rate, p.new_rate) if p.new_rate != it.rate else pcm
            y = dsp.requantize(x, it.bits, p.new_bits, dither=True)
            del pcm, x
            enc = _tmp(out_dir)
            temps.append(enc)
            audio.encode(y, p.new_bits, p.new_rate, enc)
            del y
            base = enc
        layout: FlacLayout = read_layout(base)
        seektable = layout.get(SEEKTABLE)
        if s.seektable and not seektable_is_useful(seektable):
            st_tmp = _tmp(out_dir)
            temps.append(st_tmp)
            write_flac(st_tmp, [(STREAMINFO, layout.get(STREAMINFO))], base, layout.audio_offset, layout.audio_end)
            audio.add_seektable(st_tmp)
            seektable = read_layout(st_tmp).get(SEEKTABLE)
        if p.converts:  # keep the encoder's vendor string for converted files
            try:
                vendor = VComment(layout.get(VORBIS_COMMENT), framing=False).vendor or vendor
            except Exception:
                pass
        vorbis = _vorbis_block(p.tags.tags, vendor)
        if p.cover_action == "embed" and p.cover_jpeg:
            pictures = [make_picture(p.cover_jpeg).write()]
        elif p.cover_action == "keep":
            pictures = [d for t, d in src_layout.blocks if t == PICTURE]
        else:
            pictures = []
        blocks = ordered_blocks(src_layout, vorbis, pictures, seektable or b"")
        blocks[0] = (STREAMINFO, layout.get(STREAMINFO))
        if p.new_rate != it.rate:  # CUESHEET offsets are in samples; they'd be wrong after resampling
            blocks = [b for b in blocks if b[0] != 5]
        if not s.seektable and not seektable:
            blocks = [b for b in blocks if b[0] != SEEKTABLE]
        final_tmp = _tmp(out_dir)
        temps.append(final_tmp)
        write_flac(final_tmp, blocks, base, layout.audio_offset, layout.audio_end)

        entry["verify"] = _verify(final_tmp, it, p, src_layout) if s.verify else "skipped"
        if entry["verify"].startswith("FAILED"):
            raise RuntimeError(entry["verify"])

        entry["backup"] = _commit(final_tmp, p, s)
        temps.remove(final_tmp)
        entry["status"] = "OK"
    except Cancelled:
        raise
    except Exception as e:
        entry["status"] = "ERROR"
        entry["error"] = str(e) or e.__class__.__name__
    finally:
        for t in temps:
            try:
                os.remove(t)
            except OSError:
                pass
    return entry


def _verify(path: str, it: TrackItem, p: Plan, src_layout: FlacLayout) -> str:
    ok, msg = audio.test_file(path)
    if not ok:
        return "FAILED: decode error " + msg
    if p.converts:
        return "OK: decodes cleanly, STREAMINFO MD5 matches decoded audio"
    out_md5 = streaminfo_md5(read_layout(path))
    if any(out_md5) and out_md5 == streaminfo_md5(src_layout):
        return "OK: decodes cleanly, audio MD5 identical to source (bit-exact)"
    if audio.pcm_md5(path) == audio.pcm_md5(it.path):
        return "OK: decodes cleanly, decoded PCM identical to source (source had no MD5)"
    return "FAILED: decoded audio differs from source"


def _commit(tmp: str, p: Plan, s: Settings) -> str | None:
    it = p.item
    if not s.overwrite_originals:
        dst = p.dst
        if os.path.exists(dst):  # appeared since preview; never overwrite
            dst = unique_path(os.path.dirname(dst), os.path.splitext(os.path.basename(dst))[0],
                              underscore=s.spaces_to_underscores)
            p.dst = dst
        os.replace(tmp, dst)
        return None
    backup = None
    if s.backup_originals:
        bdir = os.path.join(os.path.dirname(it.path), BACKUP_DIR)
        os.makedirs(bdir, exist_ok=True)
        backup = unique_path(bdir, os.path.splitext(it.name)[0])
        os.replace(it.path, backup)
        try:
            os.replace(tmp, p.dst)
        except Exception:
            os.replace(backup, it.path)
            raise
    else:
        os.replace(tmp, p.dst)
        if os.path.normcase(os.path.abspath(p.dst)) != os.path.normcase(os.path.abspath(it.path)):
            os.remove(it.path)
    return backup


# ------------------------------------------------------------------ log

def write_log(entries: list[dict], s: Settings, preset: str, started: str) -> list[str]:
    run = {
        "started": started, "finished": _dt.datetime.now().isoformat(timespec="seconds"),
        "app_version": __version__, "preset": preset,
        "mode": "overwrite originals" if s.overwrite_originals else "output folder",
        "output_dir": None if s.overwrite_originals else s.output_dir,
        "settings": s.to_dict(), "files": entries,
    }
    targets = [os.path.join(app_dir(), LOG_NAME)]
    if not s.overwrite_originals and s.output_dir:
        targets.insert(0, os.path.join(s.output_dir, LOG_NAME))
    written = []
    for path in targets:
        try:
            data = {"runs": []}
            if os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as fh:
                        data = json.load(fh)
                except ValueError:
                    os.replace(path, path + ".corrupt")
            data.setdefault("runs", []).append(run)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
            written.append(path)
        except OSError:
            pass
    return written
