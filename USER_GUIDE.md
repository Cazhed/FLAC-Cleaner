# FLAC Cleaner: user guide

## The default "Ginkgo format" preset

The app opens with the **Ginkgo format** preset, which makes every file look like the reference
`Ginkgo_-_Panchiko.flac`:

- **Tags:** only TITLE, ARTIST, ALBUM, ALBUMARTIST, DATE, TRACKNUMBER, TOTALTRACKS, DISCNUMBER,
  TOTALDISCS, COPYRIGHT and PUBLISHER are kept. Spelling variants (for example `ALBUM ARTIST`,
  `album_artist`, `ALBUMARTISTS`, `TRACK`, `TRACKTOTAL`, `TRACKC`) are merged into the standard field.
  `6/8` style numbers are split, and `06` becomes `6`. DATE becomes `YYYY-MM-DD` when a full date
  exists. Missing disc numbers become 1 of 1.
- **Privacy:** `account_id`, `purchase_date`, `iTunSMPB` and anything containing an email address are
  always removed. There is no setting to keep them.
- **Filename:** `{title} - {artist}` with spaces turned into underscores. For example,
  `06 In Love With Myself.flac` becomes `In_Love_With_Myself_-_Kalman.flac`.
- **Cover:** square, baseline JPEG, 640×640, quality 90, embedded as the front cover.
- **Audio:** untouched (bit-exact copy). A SEEKTABLE is added when missing.

There's also **Ginkgo format + HD cover (1400 px)**, which is the same but keeps covers at up to
1400×1400.

## Step by step

1. **Add files.** Drag FLAC files or whole folders into the window, or use **Add Files…** /
   **Add Folder…**. Folders are scanned recursively. The table shows bit depth, sample rate, the
   current cover size and how many tags each file has. Orange means a 24-bit file, or a cover that's
   missing or low-res.
2. **Choose an output folder** under *Output* (right side). Cleaned copies go there and your
   originals stay as they are. "Keep sub-folder structure" mirrors the folders you added.
3. **Covers.**
   - *Automatic:* tick **Find HD covers online automatically** (you'll be asked to confirm), then click
     **Find HD covers** or go straight to **Preview changes…**. The best match for each album is
     downloaded and shown in the *Cover of selected track* panel, with its size, source and match
     score. The lookup results box lists every candidate that was considered (★ = chosen).
   - *Manual:* drop an image onto a track row, or select all the tracks of an album and drop the
     image onto the **New** cover box (or use **Choose image…**). A cover you choose yourself always
     wins over an online one. **Clear chosen cover** undoes it.
4. **Preview changes…** opens a list with old and new names, the number of tags removed and changed,
   the cover action (with a thumbnail) and any conversion. Click a row to see the full cover preview
   with pixel size and KB, the final tags, which private fields were removed and the lookup results.
   Nothing has been written yet.
5. **Apply** writes the files. Each file is checked afterwards: it must decode without errors, and
   the audio MD5 must match the source (bit-exact) or, after a conversion, match the new STREAMINFO.
   Failures are reported per file and the original is left alone.
6. The log `flac-cleaner-log.json` is written to the output folder and to
   `%APPDATA%\FLACCleaner`. **Open log file** shows it. Removed private fields are listed by name
   only, never by value.

Open the results in foobar2000. The cover should appear in the Album Art panel, and *Properties*
should list exactly the tags above.

## Options

| Option | What it does |
|---|---|
| Also keep GENRE / ISRC / LABEL / MusicBrainz IDs / ReplayGain / LYRICS | Keeps these in addition to the standard tags (variants merged, e.g. `MusicBrainz Album Id` → `MUSICBRAINZ_ALBUMID`) |
| Template | Tokens: `{title}` `{artist}` `{album}` `{albumartist}` `{track}` `{disc}` `{year}`; `{track:02}` gives `02`. The example line updates live |
| Low-res if under | Covers smaller than this are replaced when a better one is available (default 600 px) |
| Resize to | 640, 1000, 1400 or 3000 px. Images are never upscaled. Nearly square images are centre-cropped; clearly non-square ones are padded |
| Convert existing covers… | Re-encodes progressive/PNG/oversized covers to a square baseline JPEG and drops extra pictures |
| Convert 24-bit to 16-bit | Uses TPDF dither. **Lossy**: the extra resolution is gone for good, so keep your originals |
| Resample to 44.1 kHz | For 48/88.2/96/176.4/192 kHz sources only. Also lossy |
| Overwrite originals | Off by default and asks for confirmation. By default the originals are first moved into a `FLAC Cleaner backup` sub-folder |

Name clashes never overwrite anything. A second file with the same name becomes `Name_(2).flac`
(or `Name (2).flac` without underscores).

**Presets:** **Save as…** stores the current settings under a name. The output folder, overwrite mode
and online lookup are not part of a preset, so they stay as you set them. The two built-in presets
can't be overwritten or deleted.

## Cover lookup details

- Cover Art Archive is used first when the file has a MusicBrainz release ID. That is an exact match
  for the release (your "In Love With Myself" file has one).
- Otherwise the app searches iTunes (up to 3000 px) and Deezer (1000 px) by album artist and album.
  For "Various Artists" compilations it searches by album title only.
- The chosen cover is the most trustworthy match first, then the sharpest one (up to your resize
  target). Below a 60 % match nothing is embedded, and the preview shows "lookup found nothing".
- If the online cover is not bigger than the one already in the file, the existing cover is kept.
