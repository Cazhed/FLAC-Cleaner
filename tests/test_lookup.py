import io
import json

from PIL import Image

from flac_cleaner import lookup
from flac_cleaner.lookup import AlbumQuery, Candidate, choose_best, score


def _jpeg(px):
    b = io.BytesIO()
    Image.new("RGB", (px, px), (10, 20, 30)).save(b, "JPEG")
    return b.getvalue()


def c(source, match, px):
    return Candidate(source, "u", "label", match, px, px, b"x")


def test_score_prefers_same_album():
    q = AlbumQuery(artist="Panchiko", album="Ginkgo", albumartist="Panchiko", total_tracks=13)
    good = score(q, "Panchiko", "Ginkgo", 13)
    deluxe = score(q, "Panchiko", "Ginkgo (Deluxe Edition)", 17)
    single = score(q, "Panchiko", "Ginkgo - Single", 1)
    other = score(q, "Someone Else", "Different Record", 10)
    assert good == 1.0
    assert good > deluxe > single
    assert other < lookup.MINIMUM


def test_score_compilation_uses_album():
    q = AlbumQuery(artist="Kalman", album="Cyberpunk: Edgerunners 2 (Original Series Soundtrack)",
                   albumartist="Various Artists", total_tracks=8)
    assert q.search_artist == ""
    assert score(q, "Various Artists", "Cyberpunk: Edgerunners 2 (Original Series Soundtrack)", 8) > 0.95
    assert score(q, "Various Artists", "Cyberpunk: Edgerunners (Original Series Soundtrack)", 20) < \
        score(q, "Various Artists", "Cyberpunk: Edgerunners 2 (Original Series Soundtrack)", 8)


def test_choose_best_prefers_confident_match_then_resolution():
    # exact release cover at 1200 vs a 95% match at 3000: with a 1400 target the bigger one wins
    best = choose_best([c("Cover Art Archive", 1.0, 1200), c("iTunes", 0.96, 3000)], 1400)
    assert best.source == "iTunes"
    # with a 640 target both are big enough, so the better match wins
    best = choose_best([c("Cover Art Archive", 1.0, 1200), c("iTunes", 0.96, 3000)], 640)
    assert best.source == "Cover Art Archive"
    # a weak match never beats a confident one, even if larger
    best = choose_best([c("Deezer", 0.85, 1000), c("iTunes", 0.65, 3000)], None)
    assert best.source == "Deezer"
    assert choose_best([c("iTunes", 0.4, 3000)], None) is None
    assert choose_best([], 640) is None


def test_itunes_url_upgrade_and_best(monkeypatch):
    big, small = _jpeg(1400), _jpeg(1000)
    calls = []

    def fake_get(url, cancel=None, limit=0, timeout=0):
        calls.append(url)
        if "itunes.apple.com/search" in url:
            return 200, json.dumps({"results": [
                {"artistName": "Panchiko", "collectionName": "Ginkgo", "trackCount": 13,
                 "artworkUrl100": "https://is1.mzstatic.com/image/a/100x100bb.jpg"},
                {"artistName": "Panchiko", "collectionName": "D>E>A>T>H>M>E>T>A>L", "trackCount": 4,
                 "artworkUrl100": "https://is1.mzstatic.com/image/b/100x100bb.jpg"}]}).encode()
        if "mzstatic" in url:
            return 200, big
        if "api.deezer.com" in url:
            return 200, json.dumps({"data": [{"title": "Ginkgo", "artist": {"name": "Panchiko"},
                                              "nb_tracks": 13, "cover_xl": "https://e-cdns/1000x1000.jpg"}]}).encode()
        if "e-cdns" in url:
            return 200, small
        return 404, b""

    monkeypatch.setattr(lookup, "http_get", fake_get)
    q = AlbumQuery(artist="Panchiko", album="Ginkgo", albumartist="Panchiko", total_tracks=13)
    cands = lookup.find_candidates(q)
    assert any("3000x3000bb" in u for u in calls)
    best = choose_best(cands, 1400)
    assert best.source == "iTunes" and best.width == 1400 and best.match == 1.0
    assert not any("coverartarchive" in u for u in calls)  # no MBID, so CAA is skipped


def test_caa_used_with_mbid(monkeypatch):
    img = _jpeg(1500)
    monkeypatch.setattr(lookup, "http_get",
                        lambda url, *a, **k: (200, img) if "coverartarchive.org/release/abc/front" in url else (404, b""))
    cands = lookup.find_candidates(AlbumQuery(artist="X", album="Y", mbid="abc"))
    assert len(cands) == 1 and cands[0].source == "Cover Art Archive" and cands[0].width == 1500
