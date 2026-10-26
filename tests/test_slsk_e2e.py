"""End-to-end: log in to a local mock Soulseek server, search, download from another
client and clean the result into Ginkgo format. No real network is used."""
import asyncio
import io
import os
import socket
import time

import pytest
from mutagen.flac import FLAC
from PIL import Image

pytest.importorskip("aioslsk")

from aioslsk.client import SoulSeekClient  # noqa: E402
from aioslsk.settings import CredentialsSettings, SharedDirectorySettingEntry  # noqa: E402
from aioslsk.settings import Settings as SlskSettings  # noqa: E402

from flac_cleaner.flacmeta import read_layout  # noqa: E402
from flac_cleaner.presets import Settings  # noqa: E402
from flac_cleaner.slsk import LoginError, SoulseekService  # noqa: E402
from flac_cleaner.tags import STANDARD_FIELDS  # noqa: E402

from .soulseek_mock import BAD_PASSWORD, MockServer  # noqa: E402
from .test_pipeline import HAVE_FLAC, make_messy  # noqa: E402

pytestmark = pytest.mark.skipif(not HAVE_FLAC, reason="flac tools not available")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def world(tmp_path):
    share = tmp_path / "bob share"
    album = share / "Kalman - Edgerunners Test (2026)"
    album.mkdir(parents=True)
    for i, title in enumerate(["Song A", "Song B", "Song C"], 1):
        make_messy(str(album / f"0{i} - Kalman - {title}.flac"), bits=16, seconds=31)
    single = share / "Misc"
    single.mkdir()
    make_messy(str(single / "Kalman - In Love With Myself.flac"), bits=16, seconds=31)
    make_messy(str(single / "Kalman - In Love With Myself (Instrumental).flac"), bits=16, seconds=31)
    img = io.BytesIO()
    Image.new("RGB", (1200, 1200), (200, 50, 50)).save(img, "JPEG")
    (album / "cover.jpg").write_bytes(img.getvalue())

    events = []
    svc = SoulseekService(lambda kind, obj: events.append((kind, obj)))
    srv = MockServer()
    port = svc.call(srv.start()).result(10)
    svc.config.server = f"127.0.0.1:{port}"
    svc.config.listen_port = free_port()
    svc.config.upnp = False
    svc.config.download_dir = str(tmp_path / "downloads")
    svc.config.cleaned_dir = str(tmp_path / "cleaned")
    svc.config.share_downloads = False
    svc.settings_provider = lambda: Settings()  # Ginkgo preset, online lookup off

    bs = SlskSettings(credentials=CredentialsSettings(username="bob", password="pw"))
    bs.network.server.hostname, bs.network.server.port = "127.0.0.1", port
    bp = free_port()
    bs.network.listening.port, bs.network.listening.obfuscated_port = bp, free_port()
    bs.network.upnp.enabled = False
    bs.debug.search_for_parent = False
    bs.rooms.auto_join = False
    bs.shares.download = str(tmp_path / "bobdl")
    bs.shares.directories = [SharedDirectorySettingEntry(path=str(share))]
    bob = SoulSeekClient(bs)

    async def start_bob():
        await bob.start()
        await bob.login()
        for _ in range(40):  # wait for the share scan
            if bob.shares.get_stats()[1] >= 6:
                break
            await asyncio.sleep(0.25)

    svc.call(start_bob()).result(30)
    yield svc, events, tmp_path
    svc.call(bob.stop()).result(15)
    svc.call(srv.stop()).result(10)
    svc.shutdown()


def wait_job(job, timeout=90):
    end = time.time() + timeout
    while time.time() < end and not job.done:
        time.sleep(0.2)
    assert job.done, f"job stuck: {job.status}"


def test_bad_password(world):
    svc, _, _ = world
    with pytest.raises(LoginError):
        svc.login("alice", BAD_PASSWORD).result(20)
    assert not svc.online


def test_song_search_download_clean(world, monkeypatch):
    svc, events, tmp = world
    monkeypatch.setattr("flac_cleaner.slsk.SEARCH_SECONDS", 3.0)
    svc.login("alice", "pw").result(20)
    assert svc.online
    job = svc.start_job("kalman in love with myself", "song")
    wait_job(job)
    assert job.ok, (job.status, job.detail, job.attempts)
    results = [obj for kind, obj in events if kind == "results"]
    assert results and "Instrumental" not in results[0][1][0].name  # original ranked first
    assert len(job.outputs) == 1
    out = job.outputs[0]
    assert os.path.dirname(out) == str(tmp / "cleaned")
    f = FLAC(out)
    assert [k for k, _ in f.tags] == STANDARD_FIELDS
    assert read_layout(out).block_names()[:3] == ["STREAMINFO", "SEEKTABLE", "VORBIS_COMMENT"]
    assert b"@icloud" not in open(out, "rb").read()


def test_album_search_download_clean(world, monkeypatch):
    svc, events, tmp = world
    monkeypatch.setattr("flac_cleaner.slsk.SEARCH_SECONDS", 3.0)
    svc.login("alice", "pw").result(20)
    job = svc.start_job("kalman edgerunners test", "album")
    wait_job(job)
    assert job.ok, (job.status, job.detail, job.attempts)
    assert len(job.outputs) == 3
    folders = {os.path.dirname(o) for o in job.outputs}
    assert len(folders) == 1
    for o in job.outputs:
        pics = FLAC(o).pictures
        # cover.jpg from the remote folder was downloaded and embedded (files had none)
        assert pics and pics[0].width == pics[0].height == 640


def test_song_list_with_spotify_info(world, monkeypatch, tmp_path):
    import http.server
    import threading

    from flac_cleaner.slsk import expected_output
    from flac_cleaner.tracklist import TrackInfo

    svc, events, tmp = world
    monkeypatch.setattr("flac_cleaner.slsk.SEARCH_SECONDS", 3.0)
    img_dir = tmp_path / "img"
    img_dir.mkdir()
    buf = io.BytesIO()
    Image.new("RGB", (640, 640), (10, 200, 10)).save(buf, "JPEG")
    (img_dir / "cover.jpg").write_bytes(buf.getvalue())

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(img_dir), **k)

        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/cover.jpg"

    svc.login("alice", "pw").result(20)
    a = TrackInfo("Song A", ["Kalman"], "Edgerunners Test", ["Kalman"], "2026-10-20", 1, 1, 31000,
                  "USX000000001", url, "Test Label", "2026 Test Rights", True)
    b = TrackInfo("Song B", ["Kalman"], "Edgerunners Test", ["Kalman"], "2026-10-20", 1, 2, 31000, "", url,
                  "", "", True)
    ja = svc.start_job("Kalman Song A", "song", track=a, subfolder="My Playlist")
    jb = svc.start_job("Kalman Song B", "song", track=b, subfolder="My Playlist")
    assert svc.start_job("Kalman Song B", "song", track=b, subfolder="My Playlist") is jb  # no duplicate
    wait_job(ja, 120)
    wait_job(jb, 120)
    httpd.shutdown()
    assert ja.ok and jb.ok, (ja.status, ja.detail, jb.status, jb.detail)
    out = ja.outputs[0]
    assert out == str(tmp / "cleaned" / "My Playlist" / "Song_A_-_Kalman.flac")
    f = FLAC(out)
    tags = {k: v for k, v in f.tags}
    assert tags["TITLE"] == "Song A" and tags["ALBUM"] == "Edgerunners Test" and tags["DATE"] == "2026-10-20"
    assert tags["TRACKNUMBER"] == "1" and tags["PUBLISHER"] == "Test Label" and tags["COPYRIGHT"] == "2026 Test Rights"
    pic = f.pictures[0]
    assert (pic.width, pic.height) == (640, 640)
    r, g, _ = Image.open(io.BytesIO(pic.data)).convert("RGB").getpixel((320, 320))
    assert g > 150 and r < 60  # the green "Spotify" cover from the list, not the red folder image

    # Running the list again skips what's already there
    jr = svc.start_job("Kalman Song A", "song", track=a, subfolder="My Playlist",
                       skip_path=expected_output(a, Settings(), str(tmp / "cleaned" / "My Playlist")))
    wait_job(jr, 30)
    assert jr.status.startswith("Already in your library")
