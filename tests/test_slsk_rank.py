from flac_cleaner.slsk_rank import (FileHit, pick_cover_image, rank_folders, rank_tracks, tags_from_path)


def hit(path, size=30_000_000, dur=180, bits=16, rate=44100, user="u1", free=True, speed=800_000, queue=0):
    return FileHit(user, path, size, duration=dur, sample_rate=rate, bit_depth=bits, free_slot=free,
                   speed=speed, queue=queue)


def test_track_ranking_prefers_clean_original():
    hits = [
        hit(r"@@a\Panchiko\Ginkgo\02 - Ginkgo (Instrumental).flac", user="inst"),
        hit(r"@@b\Panchiko - Ginkgo\02 - Ginkgo.flac", user="good"),
        hit(r"@@c\Panchiko - Ginkgo\02 - Ginkgo.flac", user="busy", free=False, queue=40),
        hit(r"@@d\Panchiko - Ginkgo\02 - Ginkgo.mp3", user="mp3"),
        hit(r"@@e\Panchiko - Ginkgo\02 - Ginkgo.flac", size=2_000_000, user="fake"),  # ~90 kbps "FLAC"
        hit(r"@@f\Panchiko - Ginkgo\02 - Ginkgo.flac", dur=20, size=2_000_000, user="snippet"),
        hit(r"@@g\Other Band\Something.flac", user="unrelated"),
    ]
    ranked = rank_tracks(hits, "Panchiko Ginkgo", prefer_bits=16)
    users = [h.username for h in ranked]
    assert users[0] == "good"
    assert "mp3" not in users and "fake" not in users and "snippet" not in users and "unrelated" not in users
    assert users.index("busy") > users.index("good")
    assert users.index("inst") > users.index("good")


def test_instrumental_allowed_when_asked():
    hits = [hit(r"x\Ginkgo (Instrumental).flac", user="inst"), hit(r"x\Ginkgo.flac", user="orig")]
    assert rank_tracks(hits, "ginkgo instrumental")[0].username == "inst"


def test_bit_depth_preference():
    hits = [hit(r"x\Panchiko - Ginkgo.flac", bits=24, rate=96000, user="hires", size=90_000_000),
            hit(r"x\Panchiko - Ginkgo.flac", bits=16, user="cd")]
    assert rank_tracks(hits, "panchiko ginkgo", 16)[0].username == "cd"
    assert rank_tracks(hits, "panchiko ginkgo", 24)[0].username == "hires"


def test_folder_ranking_prefers_complete_album():
    album = [hit(fr"@@a\Music\Panchiko - Ginkgo (2025)\{i:02} - Track {i}.flac", user="full") for i in range(1, 14)]
    album.append(FileHit("full", r"@@a\Music\Panchiko - Ginkgo (2025)\cover.jpg", 400_000))
    partial = [hit(r"@@b\Panchiko - Ginkgo\02 - Ginkgo.flac", user="partial")]
    mp3s = [hit(fr"@@c\Panchiko - Ginkgo [MP3]\{i:02}.mp3", user="mp3") for i in range(1, 14)]
    ranked = rank_folders(album + partial + mp3s, "Panchiko Ginkgo", 16)
    assert ranked[0].username == "full" and len(ranked[0].tracks) == 13
    assert ranked[0].images and pick_cover_image(ranked[0].images).name == "cover.jpg"
    assert all(g.username != "mp3" for g in ranked)


def test_pick_cover_image():
    imgs = [FileHit("u", r"a\scan_back.jpg", 900_000), FileHit("u", r"a\Folder.jpg", 100_000),
            FileHit("u", r"a\cover.jpg", 300_000)]
    assert pick_cover_image(imgs).name == "cover.jpg"


def test_tags_from_path():
    t = tags_from_path(r"@@x\Music\Panchiko - Ginkgo (2025)\02 - Panchiko - Ginkgo.flac")
    assert t == {"TRACKNUMBER": "2", "ARTIST": "Panchiko", "TITLE": "Ginkgo", "ALBUMARTIST": "Panchiko",
                 "ALBUM": "Ginkgo", "DATE": "2025"}
    t = tags_from_path(r"x\YOASOBI - THE BOOK\1-03 夜に駆ける.flac")
    assert t["TRACKNUMBER"] == "3" and t["DISCNUMBER"] == "1" and t["TITLE"] == "夜に駆ける"
    assert tags_from_path(r"x\1979 - Song.flac").get("TRACKNUMBER") is None


def test_password_roundtrip():
    from flac_cleaner import secret
    tok = secret.protect("pässwörd 123")
    assert "pässwörd" not in tok
    assert secret.unprotect(tok) == "pässwörd 123"
    assert secret.unprotect("") == ""
