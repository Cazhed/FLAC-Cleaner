# FLAC Cleaner

A Windows desktop app that turns messy `.flac` files (e.g. from Soulseek) into clean, consistent files
that look like the reference "Ginkgo" file:

| | Result |
|---|---|
| Tags | Only TITLE, ARTIST, ALBUM, ALBUMARTIST, DATE, TRACKNUMBER, TOTALTRACKS, DISCNUMBER, TOTALDISCS, COPYRIGHT, PUBLISHER (uppercase, duplicates merged) |
| Privacy | `account_id`, `purchase_date`, `iTunSMPB` and any value containing an email address are **always** removed |
| Filename | `{title} - {artist}.flac`, spaces → underscores (`Ginkgo_-_Panchiko.flac`) |
| Cover | Square baseline JPEG embedded as PICTURE type 3 (front cover). Missing/low-res covers can be found online automatically |
| FLAC blocks | STREAMINFO, SEEKTABLE, VORBIS_COMMENT, PICTURE (no padding, ID3 junk stripped) |
| Audio | Copied bit-exact by default. Optional 24→16-bit with TPDF dither and resampling to 44.1 kHz |

Originals are never touched unless you explicitly choose "Overwrite originals" (with a backup folder by default).
Every change is listed in a preview first and logged to `flac-cleaner-log.json`.

## Running

Double-click `FLACCleaner.exe`. No installer and no admin rights are needed.
Requires 64-bit Windows 10 (1809 or newer) or Windows 11.

Windows SmartScreen may warn the first time because the exe isn't code-signed: click
**More info → Run anyway**.

See [USER_GUIDE.md](USER_GUIDE.md) for a walkthrough.

## HD cover lookup

Off until you tick **Find HD covers online automatically**. For every album whose cover is missing or
smaller than the "low-res" threshold, the app tries, in order:

1. **Cover Art Archive** using `MUSICBRAINZ_ALBUMID` (exact release), then the release group.
2. **iTunes Search API** (artwork URL upgraded from `100x100bb` to `3000x3000bb`).
3. **Deezer API** (`cover_xl`, 1000×1000).

Every result gets a match score (album title, artist, edition words like "Deluxe", single/EP, track count).
The best one is picked automatically: confident matches first, then the highest resolution (up to your
target size), then the better match. It's downloaded, resized (never upscaled), shown in the preview with
its pixel size and KB, and embedded when you click Apply. Results under 600 px are flagged.

Only the artist name, album name and MusicBrainz IDs are sent. A clear User-Agent is used and requests
are rate-limited (iTunes: one every 3 s). Lookups happen once per album, not per track.

## Project layout

```
FLACCleaner.pyw          entry point (GUI)
FLACCleaner.spec         PyInstaller build recipe
build.bat                one-click build on Windows
flac_cleaner/
  tags.py                whitelist, merging, privacy rules, date normalisation
  naming.py              filename template, Windows-safe sanitising, collisions
  cover.py               resize / square / baseline JPEG / PICTURE block
  lookup.py              Cover Art Archive, iTunes, Deezer lookup + best-match choice
  dsp.py                 TPDF dither, FFT resampler
  audio.py               flac.exe / metaflac.exe wrappers (decode, encode, test, MD5)
  flacmeta.py            raw FLAC metadata reader/writer (block order, padding, ID3 stripping)
  processor.py           scan → plan (preview) → apply → verify → log
  presets.py             settings, presets ("Ginkgo format"), app data folder
  gui/                   PySide6 interface (main window, preview dialog)
bin/                     flac.exe, metaflac.exe 1.5.0 + DLLs and licenses (bundled into the exe)
tests/                   pytest suite
```

## Building the exe yourself

On Windows with Python 3.11+ installed, run `build.bat`. It creates a virtual environment,
installs the requirements, runs the tests and produces `dist\FLACCleaner.exe`.

Manual steps:

```bat
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest
pyinstaller --noconfirm FLACCleaner.spec
```

To run from source without building: `python FLACCleaner.pyw`.

## Tests

`python -m pytest` runs 36 tests: tag whitelist/merging/privacy, filename templates and sanitising,
cover resizing and JPEG conversion, dither and resampling, cover lookup scoring (network mocked), and
end-to-end runs on generated messy files (Japanese paths and tags, ID3 header, no SEEKTABLE) that check
the block layout, the exact tag set and bit-exact audio.

Troubleshooting a build: `FLACCleaner.exe --selftest <folder> <output-folder>` processes every FLAC in
`<folder>` with the Ginkgo preset (no GUI) and writes `selftest.json` with the results.

## Third-party components

PySide6 (LGPL-3.0), mutagen (GPL-2.0+), Pillow (MIT-CMU), NumPy (BSD-3-Clause), and the Xiph.Org
`flac`/`metaflac` tools (GPL-2.0) with libFLAC/libogg (BSD-3-Clause). See `bin/licenses`.
