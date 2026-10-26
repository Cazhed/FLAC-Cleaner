from flac_cleaner.presets import Settings
from flac_cleaner.processor import TrackItem
from flac_cleaner.slsk import apply_track_info, expected_output
from flac_cleaner.slsk_rank import FileHit, rank_tracks
from flac_cleaner.tags import clean_tags
from flac_cleaner.tracklist import clean_title, looks_like_csv, parse_csv, parse_text, search_queries

EXPORTIFY = (
    '"Track URI","Track Name","Artist URI(s)","Artist Name(s)","Album URI","Album Name","Album Artist URI(s)",'
    '"Album Artist Name(s)","Album Release Date","Album Image URL","Disc Number","Track Number",'
    '"Track Duration (ms)","Track Preview URL","Explicit","Popularity","ISRC","Added By","Added At",'
    '"Label","Copyrights"\n'
    '"spotify:track:1","Ginkgo","spotify:artist:a","Panchiko","spotify:album:x","Ginkgo","spotify:artist:a",'
    '"Panchiko","2025-04-04","https://i.scdn.co/image/abc","1","2","157390","","false","40","GBKPL2500002",'
    '"me","2026-01-01T00:00:00Z","Nettwerk Music Group",'
    '"C 2025 Panchiko Limited, P 2025 Panchiko Limited under exclusive license to Nettwerk Music Group Inc."\n'
    '"spotify:track:2","Hold On (feat. Someone) - 2011 Remaster","spotify:artist:b,spotify:artist:c",'
    '"Tyler, The Creator,Someone","spotify:album:y","Album, Vol. 1","spotify:artist:b","Tyler, The Creator",'
    '"2019","","1","7","200000","","false","1","USAAA1900001","me","2026-01-01T00:00:00Z","",""\n'
)


def test_parse_exportify_csv():
    assert looks_like_csv(EXPORTIFY)
    a, b = parse_csv(EXPORTIFY)
    assert a.title == "Ginkgo" and a.artists == ["Panchiko"] and a.album == "Ginkgo"
    assert a.release_date == "2025-04-04" and (a.disc, a.track) == (1, 2)
    assert a.duration == 157 and a.isrc == "GBKPL2500002" and a.image_url.startswith("https://i.scdn.co")
    assert a.label == "Nettwerk Music Group" and a.from_spotify
    assert a.copyright == "2025 Panchiko Limited under exclusive license to Nettwerk Music Group Inc."
    # two artist URIs, so the comma inside "Tyler, The Creator" is kept intact
    assert b.artists == ["Tyler, The Creator", "Someone"] and b.album_artists == ["Tyler, The Creator"]


def test_parse_text_lines_and_links():
    tracks, links = parse_text("Panchiko - Ginkgo\n1. Kalman – In Love With Myself\n\n"
                               "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M\nJust A Title\n")
    assert [(t.artist, t.title) for t in tracks] == [("Panchiko", "Ginkgo"), ("Kalman", "In Love With Myself"),
                                                     ("", "Just A Title")]
    assert len(links) == 1


def test_queries_strip_version_noise():
    _, b = parse_csv(EXPORTIFY)
    assert clean_title(b.title) == "Hold On"
    qs = search_queries(b)
    assert qs[0] == "Tyler The Creator Hold On"
    assert "Hold On" in qs


def test_duration_picks_right_version():
    hits = [FileHit("long", r"x\Panchiko - Ginkgo (Extended).flac", 50_000_000, duration=290, bit_depth=16,
                    sample_rate=44100, free_slot=True),
            FileHit("right", r"x\Panchiko - Ginkgo.flac", 25_000_000, duration=158, bit_depth=16,
                    sample_rate=44100, free_slot=True),
            FileHit("close", r"y\Panchiko - Ginkgo.flac", 25_000_000, duration=170, bit_depth=16,
                    sample_rate=44100, free_slot=True)]
    ranked = rank_tracks(hits, "Panchiko Ginkgo", 16, expected_duration=157)
    assert [h.username for h in ranked] == ["right", "close"]  # 290 s version rejected


def test_live_version_not_penalised_when_spotify_title_says_live():
    hits = [FileHit("u", r"x\Band - Song (Live).flac", 30_000_000, duration=200, free_slot=True)]
    assert rank_tracks(hits, "Band Song", 16, 200, allowed_words="Song - Live")[0].score > \
        rank_tracks([FileHit("u", r"x\Band - Song (Live).flac", 30_000_000, duration=200, free_slot=True)],
                    "Band Song", 16, 200)[0].score


def test_spotify_tags_replace_file_tags():
    a, _ = parse_csv(EXPORTIFY)
    it = TrackItem(path="x.flac", root=".", tags=[("title", "ginkgo (www.site.com)"), ("ARTIST", "panchiko"),
                                                   ("ALBUM ARTIST", "VA"), ("TOTALTRACKS", "13"),
                                                   ("account_id", "a@b.com")])
    apply_track_info(it, a)
    d = clean_tags(it.tags).as_dict()
    assert d["TITLE"] == ["Ginkgo"] and d["ARTIST"] == ["Panchiko"] and d["ALBUMARTIST"] == ["Panchiko"]
    assert d["DATE"] == ["2025-04-04"] and d["TRACKNUMBER"] == ["2"] and d["TOTALTRACKS"] == ["13"]
    assert d["PUBLISHER"] == ["Nettwerk Music Group"]
    assert "ISRC" not in d  # Ginkgo whitelist: ISRC only with "Also keep ISRC"


def test_expected_output_name(tmp_path):
    a, _ = parse_csv(EXPORTIFY)
    assert expected_output(a, Settings(), str(tmp_path)).endswith("Ginkgo_-_Panchiko.flac")
