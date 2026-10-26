from flac_cleaner.tags import STANDARD_FIELDS, TagOptions, clean_tags

MESSY = [
    ("major_brand", "M4A "), ("copyright", "℗ 2026 Some Label"), ("ALBUM", "Cyberpunk: Edgerunners 2"),
    ("ALBUM ARTIST", "Various Artists"), ("ALBUM_ARTIST", "Various Artists"), ("ALBUMARTISTS", "Various Artists"),
    ("ALBUMARTIST_CREDIT", "Various Artists"), ("ARTIST", "Kalman"), ("ARTISTS", "Kalman"), ("ARTISTS", "Eli"),
    ("TITLE", "In Love With Myself"), ("TRACK", "6"), ("TRACKNUMBER", "6"), ("TRACKTOTAL", "8"), ("TRACKC", "8"),
    ("DATE", "2026-10-20"), ("YEAR", "2026"), ("BPM", "0"), ("rating", "0"), ("media_type", "1"),
    ("iTunSMPB", " 00000000 00000840"), ("purchase_date", "2026-10-24 20:41:40"),
    ("account_id", "someone@icloud.com"), ("ENCODER", "Lavf62.12.102"), ("GENRE", "melodic death metal"),
    ("ISRC", "USQX92605876"), ("LABEL", "Milan Records"), ("PUBLISHER", "Milan Records"),
    ("MusicBrainz Album Id", "2c9adf32"), ("MUSICBRAINZ_ALBUMID", "2c9adf32"),
    ("R128_TRACK_GAIN", "-3610"), ("LYRICS", ""), ("COMMENT", "contact me: a.b@example.org"),
]


def test_whitelist_and_merge():
    r = clean_tags(MESSY)
    d = r.as_dict()
    assert set(d) == set(STANDARD_FIELDS)  # fill_disc adds DISCNUMBER/TOTALDISCS
    assert d["ALBUMARTIST"] == ["Various Artists"]
    assert d["TRACKNUMBER"] == ["6"] and d["TOTALTRACKS"] == ["8"]
    assert d["DISCNUMBER"] == ["1"] and d["TOTALDISCS"] == ["1"]
    assert d["COPYRIGHT"] == ["℗ 2026 Some Label"]
    assert d["PUBLISHER"] == ["Milan Records"]
    assert [k for k, _ in r.tags] == STANDARD_FIELDS  # standard order, uppercase names


def test_privacy_always_stripped_even_with_extras():
    opts = TagOptions(keep_genre=True, keep_isrc=True, keep_label=True, keep_musicbrainz=True,
                      keep_replaygain=True, keep_lyrics=True)
    r = clean_tags(MESSY, opts)
    flat = " ".join(f"{k}={v}" for k, v in r.tags)
    assert "@" not in flat
    assert "iTunSMPB" not in flat and "purchase" not in flat.lower()
    assert set(r.privacy_removed) >= {"account_id", "purchase_date", "iTunSMPB", "COMMENT"}


def test_optional_groups():
    opts = TagOptions(keep_genre=True, keep_musicbrainz=True, keep_replaygain=True, keep_lyrics=True)
    d = clean_tags(MESSY, opts).as_dict()
    assert d["GENRE"] == ["melodic death metal"]
    assert d["MUSICBRAINZ_ALBUMID"] == ["2c9adf32"]  # two spellings merged, no duplicate
    assert d["R128_TRACK_GAIN"] == ["-3610"]
    assert "LYRICS" not in d  # empty values are dropped
    assert "ISRC" not in d


def test_track_slash_total_and_leading_zeros():
    d = clean_tags([("TITLE", "x"), ("TRACKNUMBER", "03/12"), ("DISCNUMBER", "1/2")]).as_dict()
    assert d["TRACKNUMBER"] == ["3"] and d["TOTALTRACKS"] == ["12"]
    assert d["DISCNUMBER"] == ["1"] and d["TOTALDISCS"] == ["2"]


def test_date_normalization():
    assert clean_tags([("DATE", "2025-04-04T00:00:00Z")]).as_dict()["DATE"] == ["2025-04-04"]
    assert clean_tags([("DATE", "2025/4/4")]).as_dict()["DATE"] == ["2025-04-04"]
    assert clean_tags([("DATE", "20250404")]).as_dict()["DATE"] == ["2025-04-04"]
    # year only, but a full date with the same year exists elsewhere
    assert clean_tags([("DATE", "2025"), ("RELEASEDATE", "2025-04-04")]).as_dict()["DATE"] == ["2025-04-04"]
    # year only and nothing better: keep what exists
    assert clean_tags([("DATE", "2025")]).as_dict()["DATE"] == ["2025"]
    assert clean_tags([("YEAR", "1999")]).as_dict()["DATE"] == ["1999"]
    assert clean_tags([("DATE", "2025"), ("RELEASEDATE", "2025-04-04")],
                      TagOptions(normalize_date=False)).as_dict()["DATE"] == ["2025"]


def test_unicode_values_survive():
    d = clean_tags([("title", "夜に駆ける"), ("artist", "YOASOBI"), ("album", "봄날"), ("albumartist", "Björk")]).as_dict()
    assert d["TITLE"] == ["夜に駆ける"] and d["ALBUM"] == ["봄날"] and d["ALBUMARTIST"] == ["Björk"]


def test_multiple_artists_kept():
    d = clean_tags([("ARTIST", "A"), ("ARTIST", "B"), ("ARTIST", "A")]).as_dict()
    assert d["ARTIST"] == ["A", "B"]


def test_no_disc_fill_when_disabled():
    d = clean_tags([("TITLE", "x")], TagOptions(fill_disc=False)).as_dict()
    assert "DISCNUMBER" not in d
