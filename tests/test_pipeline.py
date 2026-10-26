"""End-to-end: messy 24-bit file -> Ginkgo format. Needs the flac/metaflac tools."""
import io
import os

import numpy as np
import pytest
from mutagen._vorbis import VComment
from mutagen.flac import FLAC
from PIL import Image

from flac_cleaner import audio, processor
from flac_cleaner.flacmeta import STREAMINFO, VORBIS_COMMENT, read_layout, write_flac
from flac_cleaner.presets import Settings
from flac_cleaner.tags import STANDARD_FIELDS

try:
    audio.find_tool("flac")
    audio.find_tool("metaflac")
    HAVE_FLAC = True
except audio.ToolMissing:
    HAVE_FLAC = False

pytestmark = pytest.mark.skipif(not HAVE_FLAC, reason="flac tools not available")

MESSY_TAGS = [
    ("major_brand", "M4A "), ("TITLE", "夜に駆ける"), ("ARTIST", "YOASOBI"), ("ALBUM", "THE BOOK"),
    ("ALBUM ARTIST", "YOASOBI"), ("ALBUMARTISTS", "YOASOBI"), ("TRACK", "1"), ("TRACKTOTAL", "10"),
    ("DATE", "2021-01-06"), ("copyright", "℗ 2021 Sony Music"), ("LABEL", "Sony Music"),
    ("account_id", "person@icloud.com"), ("purchase_date", "2026-10-24"), ("iTunSMPB", " 0000"),
    ("ENCODER", "Lavf62.12.102"), ("BPM", "0"), ("MusicBrainz Album Id", "abc"),
]


def make_messy(path: str, bits=24, rate=44100, seconds=3):
    rng = np.random.default_rng(5)
    n = rate * seconds
    t = np.arange(n) / rate
    sig = (np.sin(2 * np.pi * 440 * t) * 0.4 * 2 ** (bits - 1)).astype(np.int64)
    x = np.stack([sig, sig // 2], 1) + rng.integers(-300, 300, (n, 2))
    tmp = path + ".enc"
    audio.encode(x.astype(np.int32), bits, rate, tmp)
    lay = read_layout(tmp)
    vc = VComment()
    vc.vendor = "Lavf62.12.102"
    for k, v in MESSY_TAGS:
        vc.append((k, v))
    # STREAMINFO + VORBIS_COMMENT only (no SEEKTABLE, no cover)
    write_flac(path + ".noid3", [(STREAMINFO, lay.get(STREAMINFO)), (VORBIS_COMMENT, vc.write(framing=False))],
               tmp, lay.audio_offset, lay.audio_end)
    id3 = b"ID3\x03\x00\x00\x00\x00\x00\x0a" + b"\x00" * 10  # 10-byte ID3v2 header + 10 bytes body
    with open(path, "wb") as out, open(path + ".noid3", "rb") as fh:
        out.write(id3 + fh.read())
    os.remove(tmp)
    os.remove(path + ".noid3")


def test_ginkgo_preset_on_messy_file(tmp_path):
    src_dir = tmp_path / "Soulseek ダウンロード"
    src_dir.mkdir()
    src = str(src_dir / "01 - 夜に駆ける (messy).flac")
    make_messy(src)
    cover = io.BytesIO()
    Image.new("RGB", (2000, 2000), (0, 120, 200)).save(cover, "PNG")

    out = tmp_path / "out"
    s = Settings(output_dir=str(out))
    item = processor.scan_file(src, str(src_dir))
    assert item.bits == 24 and item.cover is None
    item.manual_cover, item.manual_cover_name = cover.getvalue(), "cover.png"
    plan = processor.build_plans([item], s)[0]
    assert not plan.error
    entry = processor.apply_plan(plan, s)
    assert entry["status"] == "OK", entry
    assert "bit-exact" in entry["verify"]

    dst = plan.dst
    assert os.path.basename(dst) == "夜に駆ける_-_YOASOBI.flac"
    assert os.path.exists(src)  # original untouched
    lay = read_layout(dst)
    assert lay.block_names() == ["STREAMINFO", "SEEKTABLE", "VORBIS_COMMENT", "PICTURE"]
    f = FLAC(dst)
    assert [k for k, _ in f.tags] == STANDARD_FIELDS
    assert f.tags.vendor.startswith("FLAC Cleaner")
    pic = f.pictures[0]
    assert pic.type == 3 and pic.mime == "image/jpeg" and pic.width == pic.height == 640
    raw = open(dst, "rb").read()
    assert b"@icloud" not in raw and b"iTunSMPB" not in raw and b"Lavf" not in raw and not raw.startswith(b"ID3")
    assert audio.pcm_md5(dst) == audio.pcm_md5(src)  # bit-exact audio
    assert f.info.bits_per_sample == 24

    log = processor.write_log([entry], s, "Ginkgo format", "now")
    assert any(p.endswith("flac-cleaner-log.json") for p in log)
    assert "person@icloud.com" not in open(log[0], encoding="utf-8").read()

    # Second run: same name must not overwrite, gets " (2)" style suffix
    plan2 = processor.build_plans([processor.scan_file(src, str(src_dir))], s)[0]
    assert os.path.basename(plan2.dst) == "夜に駆ける_-_YOASOBI_(2).flac"


def test_convert_to_16bit(tmp_path):
    src = str(tmp_path / "in.flac")
    make_messy(src, bits=24, rate=96000, seconds=2)
    s = Settings(output_dir=str(tmp_path / "out"), convert_16bit=True, resample_44k=True)
    plan = processor.build_plans([processor.scan_file(src, str(tmp_path))], s)[0]
    assert plan.new_bits == 16 and plan.new_rate == 44100
    e = processor.apply_plan(plan, s)
    assert e["status"] == "OK", e
    f = FLAC(plan.dst)
    assert (f.info.bits_per_sample, f.info.sample_rate) == (16, 44100)
    assert abs(f.info.length - 2.0) < 0.01
    assert read_layout(plan.dst).block_names()[:3] == ["STREAMINFO", "SEEKTABLE", "VORBIS_COMMENT"]
    ok, _ = audio.test_file(plan.dst)
    assert ok


def test_overwrite_with_backup(tmp_path):
    src = str(tmp_path / "track.flac")
    make_messy(src, seconds=1)
    before = audio.pcm_md5(src)
    s = Settings(overwrite_originals=True, backup_originals=True)
    plan = processor.build_plans([processor.scan_file(src, str(tmp_path))], s)[0]
    e = processor.apply_plan(plan, s)
    assert e["status"] == "OK", e
    assert not os.path.exists(src)
    assert os.path.exists(e["backup"]) and audio.pcm_md5(e["backup"]) == before
    assert audio.pcm_md5(plan.dst) == before
