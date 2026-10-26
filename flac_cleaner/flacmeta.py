"""Minimal FLAC container reader/writer.

Used to rebuild the metadata section in a fixed order (STREAMINFO, SEEKTABLE,
VORBIS_COMMENT, PICTURE) with no padding, while copying audio frames byte for
byte. Also strips ID3v2 headers and ID3v1 trailers some tools wrongly add.
"""
from __future__ import annotations

import os
import shutil
import struct
from dataclasses import dataclass

STREAMINFO, PADDING, APPLICATION, SEEKTABLE, VORBIS_COMMENT, CUESHEET, PICTURE = range(7)
BLOCK_NAMES = {0: "STREAMINFO", 1: "PADDING", 2: "APPLICATION", 3: "SEEKTABLE",
               4: "VORBIS_COMMENT", 5: "CUESHEET", 6: "PICTURE"}


class FlacFormatError(Exception):
    pass


@dataclass
class FlacLayout:
    blocks: list[tuple[int, bytes]]
    audio_offset: int
    audio_end: int

    def block_names(self) -> list[str]:
        return [BLOCK_NAMES.get(t, str(t)) for t, _ in self.blocks]

    def get(self, btype: int) -> bytes | None:
        for t, d in self.blocks:
            if t == btype:
                return d
        return None


def _id3v2_size(head: bytes) -> int:
    if head[:3] != b"ID3" or len(head) < 10:
        return 0
    sz = 0
    for b in head[6:10]:
        sz = (sz << 7) | (b & 0x7F)
    footer = 10 if head[5] & 0x10 else 0
    return 10 + sz + footer


def read_layout(path: str) -> FlacLayout:
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        start = 0
        head = fh.read(10)
        while head[:3] == b"ID3":  # some files carry more than one
            start += _id3v2_size(head)
            fh.seek(start)
            head = fh.read(10)
        fh.seek(start)
        if fh.read(4) != b"fLaC":
            raise FlacFormatError("Not a FLAC file (no fLaC marker)")
        blocks: list[tuple[int, bytes]] = []
        while True:
            hdr = fh.read(4)
            if len(hdr) < 4:
                raise FlacFormatError("Truncated metadata")
            last = bool(hdr[0] & 0x80)
            btype = hdr[0] & 0x7F
            length = int.from_bytes(hdr[1:4], "big")
            data = fh.read(length)
            if len(data) < length:
                raise FlacFormatError("Truncated metadata block")
            blocks.append((btype, data))
            if last:
                break
        audio_offset = fh.tell()
        audio_end = size
        if size - audio_offset > 128:
            fh.seek(size - 128)
            if fh.read(3) == b"TAG":
                audio_end = size - 128
    if not blocks or blocks[0][0] != STREAMINFO:
        raise FlacFormatError("First metadata block is not STREAMINFO")
    return FlacLayout(blocks, audio_offset, audio_end)


def write_flac(dst: str, blocks: list[tuple[int, bytes]], src: str, audio_offset: int, audio_end: int) -> None:
    """Write blocks (already ordered) followed by the audio frames of src."""
    with open(dst, "wb") as out:
        out.write(b"fLaC")
        for i, (btype, data) in enumerate(blocks):
            if len(data) >= 1 << 24:
                raise FlacFormatError(f"{BLOCK_NAMES.get(btype)} block too large ({len(data)} bytes)")
            flag = 0x80 if i == len(blocks) - 1 else 0
            out.write(bytes([flag | btype]) + len(data).to_bytes(3, "big"))
            out.write(data)
        with open(src, "rb") as fh:
            fh.seek(audio_offset)
            remaining = audio_end - audio_offset
            while remaining > 0:
                chunk = fh.read(min(remaining, 4 << 20))
                if not chunk:
                    raise FlacFormatError("Unexpected end of audio data")
                out.write(chunk)
                remaining -= len(chunk)


def ordered_blocks(layout: FlacLayout, vorbis: bytes, pictures: list[bytes],
                   seektable: bytes | None = None) -> list[tuple[int, bytes]]:
    """STREAMINFO, SEEKTABLE, VORBIS_COMMENT, PICTURE(s), then CUESHEET/APPLICATION. No PADDING."""
    blocks = [(STREAMINFO, layout.get(STREAMINFO))]
    st = seektable if seektable is not None else layout.get(SEEKTABLE)
    if st:
        blocks.append((SEEKTABLE, st))
    blocks.append((VORBIS_COMMENT, vorbis))
    blocks.extend((PICTURE, p) for p in pictures)
    blocks.extend((t, d) for t, d in layout.blocks if t in (CUESHEET, APPLICATION))
    return blocks


def seektable_is_useful(data: bytes | None) -> bool:
    """A SEEKTABLE with at least one non-placeholder point."""
    if not data:
        return False
    for i in range(0, len(data) - 17, 18):
        if struct.unpack(">Q", data[i:i + 8])[0] != 0xFFFFFFFFFFFFFFFF:
            return True
    return False


def streaminfo_md5(layout: FlacLayout) -> bytes:
    return layout.get(STREAMINFO)[18:34]


def copy_file(src: str, dst: str) -> None:
    shutil.copyfile(src, dst)
