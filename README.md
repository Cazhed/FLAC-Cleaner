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

The **Search & Download** tab (v1.1) logs in to your Soulseek account, searches for a song or an album,
automatically picks the best FLAC source, downloads it and runs it through the same cleanup, so the files
land in your "Cleaned files" folder already in Ginkgo format with an HD cover.

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

## Search & Download (Soulseek)

- Built-in Soulseek client ([aioslsk](https://pypi.org/project/aioslsk/)), so nothing else needs to be
  installed. Don't run Nicotine+ or SoulseekQt with the same account at the same time.
- **Song** mode ranks individual files. **Album** mode ranks whole folders, asks the uploader for the full
  folder listing and downloads every FLAC in it, plus `cover.jpg`/`folder.jpg` if present.
- Ranking: all search words must appear in the path. FLAC only. Files shorter than 30 s or under
  320 kbps (fake "FLAC") are dropped. Versions such as instrumental, remix, live, sped up or karaoke are
  penalised unless you searched for them. The preferred bit depth gets a bonus (16-bit by default, to
  match the Ginkgo file). Then free upload slot, short queue and speed. Complete album folders beat stray
  tracks.
- If a source fails or sends nothing for 150 s (adjustable: "Give up on a queued user after"), the next
  best source is tried, up to 4. Starting the same search twice reuses the running download.
- **Song lists / playlists (v1.2):** "Song list / playlist CSV…" takes pasted `Artist - Title` lines or a
  Spotify playlist exported as CSV (e.g. with exportify.app). Each song is searched on its own, two at a
  time, with at most 34 searches per 220 s to stay under Soulseek's flood limits. When the list has Spotify
  info, the Spotify length is used to reject other versions (a file more than 20 s off is dropped; within
  3 s gets a bonus), and the cleaned file gets Spotify's title, artist, album, album artist, date, track
  and disc number, label and copyright plus the 640×640 Spotify cover. Songs already in the target folder
  are skipped. Spotify links themselves can't be read: since February 2026 Spotify only issues API keys to
  Premium accounts.
- After downloading, the file goes through the Clean files settings. Missing tags are filled from the
  file and folder name ("02 - Artist - Title", "Artist - Album (Year)"). The HD cover comes from the
  online lookup (when enabled), otherwise from the uploader's folder image. Albums go into an
  `Album Artist - Album` sub-folder.
- The password is stored encrypted with Windows DPAPI (only your Windows account can read it), in
  `%APPDATA%\FLACCleaner\soulseek.json`. Library warnings go to `soulseek.log` in the same folder.
- Your "Original downloads" folder is shared by default, because many Soulseek users block people who
  share nothing. Windows Firewall may ask once to allow incoming connections. Allow it, or downloads
  from some users will fail.

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
  slsk.py                Soulseek service: login, search, download jobs, then the cleaning step
  slsk_rank.py           ranking of search results (songs and album folders), filename → tags
  tracklist.py           song lists: Spotify playlist CSV (Exportify) and "Artist - Title" text
  secret.py              DPAPI password storage
  presets.py             settings, presets ("Ginkgo format"), app data folder
  gui/                   PySide6 interface (main window, preview dialog, Search & Download tab)
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

`python -m pytest` runs 54 tests: tag whitelist/merging/privacy, filename templates and sanitising,
cover resizing and JPEG conversion, dither and resampling, cover lookup scoring (network mocked),
end-to-end runs on generated messy files (Japanese paths and tags, ID3 header, no SEEKTABLE) that check
the block layout, the exact tag set and bit-exact audio, Soulseek result ranking, song-list parsing,
and full
login → search → download → clean runs (single songs, albums and Spotify-tagged song lists) against
a local mock Soulseek server
(`tests/soulseek_mock.py`). The real Soulseek network is never contacted by the tests.

Troubleshooting a build: `FLACCleaner.exe --selftest <folder> <output-folder>` processes every FLAC in
`<folder>` with the Ginkgo preset (no GUI) and writes `selftest.json` with the results.

## Third-party components

PySide6 (LGPL-3.0), mutagen (GPL-2.0+), Pillow (MIT-CMU), NumPy (BSD-3-Clause), aioslsk (GPL-3.0), and the Xiph.Org
`flac`/`metaflac` tools (GPL-2.0) with libFLAC/libogg (BSD-3-Clause). See `bin/licenses`.
Because it includes aioslsk, FLAC Cleaner as a whole is distributed under the GPL-3.0 (see `LICENSE`).
