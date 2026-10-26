import os

from flac_cleaner.naming import fields_from_tags, render, sanitize, unique_path

GINKGO = {"TITLE": ["Ginkgo"], "ARTIST": ["Panchiko"], "ALBUM": ["Ginkgo"], "TRACKNUMBER": ["2"],
          "DISCNUMBER": ["1"], "DATE": ["2025-04-04"]}


def test_ginkgo_default():
    assert render("{title} - {artist}", fields_from_tags(GINKGO), replace_spaces=True) == "Ginkgo_-_Panchiko"
    assert render("{title} - {artist}.flac", fields_from_tags(GINKGO)) == "Ginkgo - Panchiko"


def test_tokens_and_padding():
    f = fields_from_tags(GINKGO)
    assert render("{track:02}. {title} ({year})", f) == "02. Ginkgo (2025)"
    assert render("{albumartist}/{album}/{disc}-{track}", f) == "Panchiko-Ginkgo-1-2"


def test_forbidden_characters():
    s = sanitize('a<b>c:d"e/f\\g|h?i*j')
    for ch in '<>:"/\\|?*':
        assert ch not in s
    assert render("{title}", {"title": "Re:Zero?"}) == "Re -Zero"


def test_trailing_dots_spaces_and_reserved():
    assert render("{title}", {"title": "Ends with dots... "}) == "Ends with dots"
    assert render("{title}", {"title": "CON"}) == "CON_"
    assert render("{title} - {artist}", {"title": "", "artist": ""}, fallback="orig") == "orig"
    assert render("{title} - {artist}", {"title": "Only", "artist": ""}) == "Only"


def test_length_cap():
    stem = render("{title}", {"title": "x" * 500})
    assert len(stem + ".flac") <= 200


def test_unicode_names():
    assert render("{title} - {artist}", {"title": "夜に駆ける", "artist": "YOASOBI"}, True) == "夜に駆ける_-_YOASOBI"
    assert render("{title}", {"title": "Café"}) == "Café"


def test_collisions(tmp_path):
    d = str(tmp_path)
    open(os.path.join(d, "Song.flac"), "w").close()
    taken = set()
    a = unique_path(d, "Song", taken=taken)
    b = unique_path(d, "Song", taken=taken)
    assert os.path.basename(a) == "Song (2).flac"
    assert os.path.basename(b) == "Song (3).flac"
    assert os.path.basename(unique_path(d, "Song", taken=set(), underscore=True)) == "Song_(2).flac"
    # in overwrite mode the source path itself may be reused
    src = os.path.join(d, "Song.flac")
    assert unique_path(d, "Song", taken=set(), allow=src) == src
