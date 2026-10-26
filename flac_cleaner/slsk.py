"""Soulseek search & download, followed by the FLAC Cleaner pipeline.

The aioslsk client runs on its own asyncio event loop in a background thread.
The GUI talks to it through :class:`SoulseekService`, whose methods are safe to
call from any thread, and receives updates through the ``emit`` callback.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import itertools
import json
import logging
import logging.handlers
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

from . import processor
from .naming import render
from .presets import Settings, app_dir
from .slsk_rank import (FileHit, FolderHit, hits_from_results, pick_cover_image, rank_folders, rank_tracks,
                        tags_from_path)
from .tags import ALIASES, _first, _index, clean_tags, norm_key
from .tracklist import TrackInfo, search_queries, wanted_words

SEARCH_SECONDS = 8.0
STALL_SECONDS = 150        # give up on a source that hasn't sent a byte for this long
MAX_ATTEMPTS = 4           # how many sources to try before giving up
SEARCH_LIMIT = (34, 220)   # at most 34 searches per 220 s, to stay clear of the server's flood protection
DEFAULT_PORT = 2234


# ------------------------------------------------------------------ config

@dataclass
class SlskConfig:
    username: str = ""
    password_token: str = ""            # DPAPI-encrypted, see secret.py
    remember: bool = True
    auto_login: bool = False
    download_dir: str = ""
    cleaned_dir: str = ""
    share_downloads: bool = True
    keep_raw: bool = True
    prefer_bits: int = 16               # 16, 24 or 0 (any)
    listen_port: int = DEFAULT_PORT
    upnp: bool = True
    server: str = "server.slsknet.org:2416"
    give_up_seconds: int = STALL_SECONDS

    @classmethod
    def path(cls) -> str:
        return os.path.join(app_dir(), "soulseek.json")

    @classmethod
    def load(cls) -> "SlskConfig":
        music = os.path.join(os.path.expanduser("~"), "Music")
        c = cls(download_dir=os.path.join(music, "Soulseek Downloads"),
                cleaned_dir=os.path.join(music, "FLAC Cleaner"))
        try:
            with open(cls.path(), encoding="utf-8") as fh:
                d = json.load(fh)
            for k, v in d.items():
                if hasattr(c, k):
                    setattr(c, k, v)
        except (OSError, ValueError):
            pass
        return c

    def save(self) -> None:
        d = asdict(self)
        if not self.remember:
            d["password_token"] = ""
            d["auto_login"] = False
        try:
            with open(self.path(), "w", encoding="utf-8") as fh:
                json.dump(d, fh, indent=2, ensure_ascii=False)
        except OSError:
            pass

    def server_addr(self) -> tuple[str, int]:
        host, _, port = self.server.rpartition(":")
        return (host or self.server), int(port or 2416)


# ------------------------------------------------------------------ jobs

@dataclass
class Job:
    id: int
    query: str
    mode: str                                   # "song" | "album"
    status: str = "Waiting"
    progress: float = 0.0
    speed: float = 0.0
    source: str = ""
    done: bool = False
    ok: bool = False
    outputs: list[str] = field(default_factory=list)
    detail: str = ""
    forced: Optional[object] = None             # FileHit / FolderHit picked by hand
    search_only: bool = False
    track: Optional[TrackInfo] = None           # from a playlist/CSV: drives search, tags and cover
    subfolder: str = ""                         # cleaned files go into <cleaned>/<subfolder>
    skip_path: str = ""                         # if this file already exists, skip the download
    cancel_requested: bool = False
    attempts: list[str] = field(default_factory=list)


class LoginError(Exception):
    pass


class SoulseekService:
    def __init__(self, emit: Callable[[str, object], None]):
        self.emit = emit
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.client = None
        self.username = ""
        self._thread: Optional[threading.Thread] = None
        self._jobs: dict[int, Job] = {}
        self._ids = itertools.count(1)
        self._clean_pool = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="clean")
        self._job_sema: Optional[asyncio.Semaphore] = None
        self._search_times: list[float] = []
        self.settings_provider: Callable[[], Settings] = Settings
        self.config = SlskConfig.load()
        _setup_logging()

    # ---------------- loop plumbing
    def _ensure_loop(self):
        if self.loop is None:
            self.loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self.loop.run_forever, name="soulseek", daemon=True)
            self._thread.start()

    def call(self, coro) -> concurrent.futures.Future:
        self._ensure_loop()
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    @property
    def online(self) -> bool:
        return self.client is not None and self.client.session is not None

    # ---------------- login
    def login(self, username: str, password: str) -> concurrent.futures.Future:
        return self.call(self._login(username, password))

    def logout(self) -> concurrent.futures.Future:
        return self.call(self._logout())

    async def _login(self, username: str, password: str) -> str:
        from aioslsk.client import SoulSeekClient
        from aioslsk.exceptions import AuthenticationError
        from aioslsk.naming import DefaultNamingStrategy, KeepDirectoryStrategy, NumberDuplicateStrategy
        from aioslsk.settings import CredentialsSettings, SharedDirectorySettingEntry
        from aioslsk.settings import Settings as SlskSettings

        await self._logout()
        cfg = self.config
        os.makedirs(cfg.download_dir, exist_ok=True)
        s = SlskSettings(credentials=CredentialsSettings(username=username, password=password))
        host, port = cfg.server_addr()
        s.network.server.hostname, s.network.server.port = host, port
        s.network.server.reconnect.auto = True
        s.network.listening.port = cfg.listen_port
        s.network.listening.obfuscated_port = cfg.listen_port + 1
        s.network.upnp.enabled = cfg.upnp
        s.rooms.auto_join = False
        s.shares.download = cfg.download_dir
        s.shares.directories = ([SharedDirectorySettingEntry(path=cfg.download_dir)] if cfg.share_downloads else [])
        if host in ("127.0.0.1", "localhost"):
            s.debug.search_for_parent = False
        client = SoulSeekClient(s)
        client.shares.naming_strategies = [DefaultNamingStrategy(), KeepDirectoryStrategy(), NumberDuplicateStrategy()]
        self.emit("status", "Connecting…")
        try:
            await client.start()
            await client.login()
        except AuthenticationError as e:
            await self._safe_stop(client)
            reason = {"INVALIDPASS": "wrong password", "INVALIDUSERNAME": "invalid username"}.get(e.reason, e.reason)
            raise LoginError(f"Login refused: {reason or 'unknown reason'}") from None
        except Exception as e:
            await self._safe_stop(client)
            raise LoginError(f"Can't reach the Soulseek server ({e.__class__.__name__}: {e})") from None
        self.client, self.username = client, username
        self._job_sema = asyncio.Semaphore(2)
        self.emit("status", f"Online as {username}")
        return username

    @staticmethod
    async def _safe_stop(client):
        try:
            await asyncio.wait_for(client.stop(), 10)
        except Exception:
            pass

    async def _logout(self):
        if self.client is not None:
            c, self.client = self.client, None
            await self._safe_stop(c)
            self.emit("status", "Offline")

    def shutdown(self, timeout: float = 8.0):
        if self.loop is None:
            return
        try:
            self.call(self._logout()).result(timeout)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._clean_pool.shutdown(wait=False, cancel_futures=True)

    # ---------------- search
    async def _search_slot(self, on_wait: Callable[[int], None]) -> None:
        limit, window = SEARCH_LIMIT
        while True:
            now = time.monotonic()
            self._search_times = [t for t in self._search_times if now - t < window]
            if len(self._search_times) < limit:
                self._search_times.append(now)
                return
            on_wait(int(window - (now - self._search_times[0])) + 1)
            await asyncio.sleep(1)

    async def search(self, query: str, seconds: Optional[float] = None,
                     on_update: Callable[[int], None] = lambda n: None,
                     on_wait: Callable[[int], None] = lambda s: None) -> list[FileHit]:
        if not self.online:
            raise LoginError("Not logged in")
        seconds = SEARCH_SECONDS if seconds is None else seconds
        await self._search_slot(on_wait)
        req = await self.client.searches.search(query)
        start, last = time.monotonic(), -1
        try:
            while time.monotonic() - start < seconds:
                await asyncio.sleep(0.5)
                n = len(req.results)
                if n != last:
                    on_update(n)
                    last = n
        finally:
            try:
                self.client.searches.remove_request(req)
            except Exception:
                pass
        return hits_from_results(list(req.results))

    async def browse_folder(self, username: str, folder: str) -> Optional[list[FileHit]]:
        """Full listing of one remote folder (search results only list matching files)."""
        from aioslsk.commands import PeerGetDirectoryContentCommand
        try:
            dirs = await self.client(PeerGetDirectoryContentCommand(username, folder), response=True, timeout=20)
        except Exception:
            return None
        hits = []
        for d in dirs or []:
            if d.name.rstrip("\\/").lower() != folder.rstrip("\\/").lower():
                continue
            for f in d.files:
                attrs = {getattr(k, "value", k): v for k, v in f.get_attribute_map().items()}
                hits.append(FileHit(username, d.name.rstrip("\\") + "\\" + f.filename, f.filesize,
                                    bitrate=attrs.get(0, 0), duration=attrs.get(1, 0),
                                    sample_rate=attrs.get(4, 0), bit_depth=attrs.get(5, 0)))
        return hits or None

    # ---------------- jobs
    def start_job(self, query: str, mode: str, forced=None, search_only: bool = False,
                  track: Optional[TrackInfo] = None, subfolder: str = "", skip_path: str = "") -> Job:
        if forced is None and not search_only:
            for j in self._jobs.values():  # same search already running: don't start it twice
                if not j.done and not j.search_only and j.forced is None and j.mode == mode \
                        and j.query.casefold() == query.casefold() and j.track == track:
                    return j
        job = Job(next(self._ids), query, mode, forced=forced, search_only=search_only, track=track,
                  subfolder=subfolder, skip_path=skip_path)
        self._jobs[job.id] = job
        self.emit("job", job)
        self.call(self._run_job(job))
        return job

    def cancel_job(self, job: Job):
        job.cancel_requested = True

    def _update(self, job: Job, **kw):
        for k, v in kw.items():
            setattr(job, k, v)
        self.emit("job", job)

    async def _run_job(self, job: Job):
        if not self.online:
            return self._update(job, status="Error: not logged in", done=True)
        try:
            async with self._job_sema:
                await self._run_job_inner(job)
        except asyncio.CancelledError:
            self._update(job, status="Cancelled", done=True)
        except Exception as e:
            self._update(job, status=f"Error: {e}", done=True, ok=False)

    async def _find_candidates(self, job: Job, prefer) -> list:
        if job.mode == "album":
            queries = [job.query]
        elif job.track is not None:
            queries = search_queries(job.track) or [job.query]
        else:
            queries = [job.query]
        candidates: list = []
        for n, q in enumerate(queries):
            label = f"Searching “{q}”…" if n else "Searching…"
            self._update(job, status=label)
            hits = await self.search(
                q, on_update=lambda c, label=label: self._update(job, status=f"{label} {c} users replied"),
                on_wait=lambda sec: self._update(job, status=f"Waiting {sec} s (Soulseek search limit)…"))
            if job.cancel_requested:
                return []
            if job.mode == "album":
                candidates = rank_folders(hits, q, prefer)
            elif job.track is not None:
                candidates = rank_tracks(hits, q, prefer, job.track.duration, wanted_words(job.track))
            else:
                candidates = rank_tracks(hits, q, prefer)
            if candidates:
                break
        return candidates

    async def _run_job_inner(self, job: Job):
        prefer = self.config.prefer_bits or None
        if job.skip_path and os.path.exists(job.skip_path):
            return self._update(job, status="Already in your library, skipped", done=True, ok=True,
                                outputs=[job.skip_path], progress=1.0)
        if job.forced is not None:
            candidates = [job.forced]
        else:
            candidates = await self._find_candidates(job, prefer)
            if job.cancel_requested:
                return self._update(job, status="Cancelled", done=True)
            self.emit("results", (job, candidates))
            if not candidates:
                return self._update(job, status="No usable FLAC results", done=True)
            if job.search_only:
                return self._update(job, status=f"Found {len(candidates)} candidate(s). Double-click one to download",
                                    done=True, ok=True)

        for cand in candidates[:MAX_ATTEMPTS]:
            if job.cancel_requested:
                return self._update(job, status="Cancelled", done=True)
            ok, paths, image = await self._download_candidate(job, cand)
            if ok:
                self._update(job, status="Cleaning…", progress=1.0, speed=0)
                root = self.config.download_dir
                settings = self.settings_provider()
                fut = asyncio.get_running_loop().run_in_executor(
                    self._clean_pool, clean_downloaded, paths, job.mode, settings, self.config, image, root,
                    job.track, job.subfolder)
                entries = await fut
                good = [e for e in entries if e.get("status") == "OK"]
                outs = [e["output"] for e in good]
                if not self.config.keep_raw:
                    _remove_raw(paths)
                status = (f"Done: {len(good)} file(s)" if len(good) == len(entries) and good
                          else f"Done with problems: {len(good)}/{len(entries)} cleaned")
                detail = "\n".join(f"{os.path.basename(e.get('output') or e.get('source', ''))}: "
                                   f"{e.get('verify') or e.get('error', '')}" for e in entries)
                return self._update(job, status=status, done=True, ok=bool(good), outputs=outs, detail=detail)
        self._update(job, status="Failed: no source finished the download", done=True,
                     detail="\n".join(job.attempts))

    async def _download_candidate(self, job: Job, cand) -> tuple[bool, list[str], Optional[bytes]]:
        user = cand.username
        if isinstance(cand, FolderHit):
            self._update(job, status=f"Listing folder from {user}…", source=user, progress=0)
            listing = await self.browse_folder(user, cand.folder)
            tracks = [h for h in (listing or cand.tracks) if h.ext == "flac"]
            images = [h for h in (listing or []) if h.name.lower().endswith((".jpg", ".jpeg", ".png"))] or cand.images
            cover = pick_cover_image(images)
            wanted = tracks + ([cover] if cover else [])
            label = f"{cand.name} ({len(tracks)} tracks)"
        else:
            wanted, cover, label = [cand], None, cand.name
        self._update(job, status=f"Requesting {label} from {user}…", source=user, progress=0)

        transfers = []
        for h in wanted:
            transfers.append(await self._start_download(user, h.path))
        total = sum(h.size for h in wanted) or 1
        last_bytes, last_change = -1, time.monotonic()
        while True:
            await asyncio.sleep(0.5)
            done_bytes = sum(t.bytes_transfered for t in transfers)
            if done_bytes != last_bytes:
                last_bytes, last_change = done_bytes, time.monotonic()
            speed = sum(t.get_speed() for t in transfers if t.is_transferring())
            states = {t.state.VALUE.name for t in transfers}
            if job.cancel_requested:
                await self._abort(transfers)
                return False, [], None
            failed = [t for t in transfers if t.state.VALUE.name in ("FAILED", "ABORTED")
                      and not (cover and t.remote_path == cover.path)]
            if failed:
                reason = failed[0].fail_reason or failed[0].abort_reason or "failed"
                job.attempts.append(f"{user}: {reason}")
                await self._abort(transfers)
                self._update(job, status=f"{user} failed ({reason}), trying another source…")
                return False, [], None
            if all(t.is_finalized() for t in transfers):
                break
            give_up = max(15, int(self.config.give_up_seconds or STALL_SECONDS))
            if time.monotonic() - last_change > give_up:
                job.attempts.append(f"{user}: no data for {give_up} s (queued)")
                await self._abort(transfers)
                self._update(job, status=f"{user} is too slow / queued, trying another source…")
                return False, [], None
            queued = [t.place_in_queue for t in transfers if t.state.VALUE.name == "QUEUED" and t.place_in_queue]
            st = (f"Downloading {label} from {user}" if "DOWNLOADING" in states
                  else f"Queued at {user}" + (f" (place {min(queued)})" if queued else "") if "QUEUED" in states
                  else f"Connecting to {user}…")
            self._update(job, status=st, progress=min(done_bytes / total, 0.999), speed=speed)

        paths, image = [], None
        for h, t in zip(wanted, transfers):
            if t.state.VALUE.name != "COMPLETE" or not t.local_path:
                continue
            if cover and h is cover:
                try:
                    with open(t.local_path, "rb") as fh:
                        image = fh.read()
                except OSError:
                    pass
            else:
                paths.append(t.local_path)
        return bool(paths), paths, image

    async def _start_download(self, user: str, path: str):
        from aioslsk.transfer.model import TransferDirection
        old = self.client.transfers.find_transfer(user, path, TransferDirection.DOWNLOAD)
        if old is not None and old.is_finalized():
            await self.client.transfers.remove(old)
        return await self.client.transfers.download(user, path)

    async def _abort(self, transfers):
        for t in transfers:
            if not t.is_finalized():
                try:
                    await self.client.transfers.abort(t)
                except Exception:
                    pass


def _setup_logging() -> None:
    """Library warnings go to %APPDATA%\\FLACCleaner\\soulseek.log (rotated), not the console."""
    log = logging.getLogger("aioslsk")
    if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in log.handlers):
        return
    try:
        h = logging.handlers.RotatingFileHandler(os.path.join(app_dir(), "soulseek.log"), maxBytes=1_000_000,
                                                 backupCount=1, encoding="utf-8")
    except OSError:
        return
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.WARNING)
    log.propagate = False


def _remove_raw(paths: list[str]) -> None:
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


# ------------------------------------------------------------------ cleaning step

def _fill_missing_tags(item: processor.TrackItem) -> None:
    """Add tags parsed from the file/folder name for fields the file doesn't have."""
    guess = tags_from_path(item.path.replace(os.sep, "\\"))
    idx = _index(item.tags)
    for k, v in guess.items():
        if not _first(idx, ALIASES.get(k, [k])):
            item.tags.append((k, v))


def apply_track_info(item: processor.TrackItem, t: TrackInfo) -> None:
    """Replace the file's own values with the Spotify/CSV values for every field the list provides."""
    new = t.tags()
    drop = set()
    for std in new:
        drop.update(ALIASES.get(std, [norm_key(std)]))
    item.tags = [(k, v) for k, v in item.tags if norm_key(k) not in drop] + list(new.items())


def fetch_cover(url: str) -> Optional[bytes]:
    from .cover import image_info
    from .lookup import http_get
    try:
        status, data = http_get(url, limit=20 * 1024 * 1024, timeout=30)
        if status == 200 and data:
            image_info(data)  # raises if it isn't an image
            return data
    except Exception:
        pass
    return None


def expected_output(t: TrackInfo, settings: Settings, folder: str) -> str:
    """Where the cleaned file for this list entry will end up (used to skip what you already have)."""
    from .naming import fields_from_tags, render as _render
    tags = clean_tags(list(t.tags().items()), settings.tag_options()).as_dict()
    stem = _render(settings.template, fields_from_tags(tags), settings.spaces_to_underscores, t.title)
    return os.path.join(folder, stem + ".flac")


def clean_downloaded(paths: list[str], mode: str, settings: Settings, cfg: SlskConfig,
                     folder_image: Optional[bytes], download_root: str,
                     track: Optional[TrackInfo] = None, subfolder: str = "") -> list[dict]:
    """Run the FLAC Cleaner pipeline on freshly downloaded files. Returns log entries."""
    s = copy.deepcopy(settings)
    s.overwrite_originals = False
    s.output_dir = os.path.join(cfg.cleaned_dir, subfolder) if subfolder else cfg.cleaned_dir
    s.mirror_folders = False
    items = []
    track_cover = fetch_cover(track.image_url) if track is not None and track.image_url else None
    for p in paths:
        it = processor.scan_file(p, os.path.dirname(p))
        _fill_missing_tags(it)
        if track is not None and track.from_spotify:  # typed 'Artist - Title' lines only guide the search
            apply_track_info(it, track)
            if track_cover and s.cover_enabled:
                it.manual_cover, it.manual_cover_name = track_cover, "Spotify cover"
        items.append(it)
    good = [i for i in items if not i.error]
    if mode == "album" and good:
        tags = clean_tags(good[0].tags, s.tag_options()).as_dict()
        aa = (tags.get("ALBUMARTIST") or tags.get("ARTIST") or [""])[0]
        album = (tags.get("ALBUM") or [""])[0]
        if album:
            folder = render("{albumartist} - {album}", {"albumartist": aa, "album": album},
                            s.spaces_to_underscores, album)
            s.output_dir = os.path.join(cfg.cleaned_dir, folder)
    if s.online_lookup:
        try:
            processor.lookup_covers(good, s, lambda: False, lambda *a: None)
        except Exception:
            pass
    if folder_image:
        for it in good:
            if processor.needs_cover(it, s) and it.found is None and not it.manual_cover:
                it.manual_cover, it.manual_cover_name = folder_image, "cover image from the download folder"
    plans = processor.build_plans(items, s)
    entries = []
    for p in plans:
        e = processor.apply_plan(p, s) if not p.error else {"source": p.item.path, "status": "ERROR",
                                                             "error": p.error}
        e["downloaded_from_soulseek"] = True
        entries.append(e)
    processor.write_log(entries, s, "Search & Download", time.strftime("%Y-%m-%dT%H:%M:%S"))
    return entries
