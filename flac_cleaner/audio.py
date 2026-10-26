"""Wrappers around the bundled flac / metaflac command-line tools."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys

import numpy as np

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW


class ToolMissing(Exception):
    pass


def _tool_dirs() -> list[str]:
    dirs = []
    if getattr(sys, "frozen", False):
        dirs.append(os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "bin"))
        dirs.append(os.path.join(os.path.dirname(sys.executable), "bin"))
    here = os.path.dirname(os.path.abspath(__file__))
    dirs.append(os.path.join(os.path.dirname(here), "bin"))
    return dirs


def find_tool(name: str) -> str:
    exe = name + (".exe" if sys.platform == "win32" else "")
    for d in _tool_dirs():
        p = os.path.join(d, exe)
        if os.path.isfile(p):
            return p
    p = shutil.which(name)
    if p:
        return p
    raise ToolMissing(f"{exe} not found. Put it in the 'bin' folder next to the app.")


def _run(args: list[str], stdin=None, input_bytes: bytes | None = None, capture: bool = True):
    return subprocess.run(args, stdin=stdin, input=input_bytes, capture_output=capture,
                          creationflags=_NO_WINDOW)


def flac_version() -> str:
    r = _run([find_tool("flac"), "--version"])
    return r.stdout.decode(errors="replace").strip()


def test_file(path: str) -> tuple[bool, str]:
    """Decode the whole file; libFLAC also checks frame CRCs and the STREAMINFO MD5."""
    with open(path, "rb") as fh:
        r = _run([find_tool("flac"), "-t", "-s", "-"], stdin=fh)
    msg = (r.stderr or b"").decode(errors="replace").strip()
    return r.returncode == 0, msg


def pcm_md5(path: str) -> str:
    """MD5 of the decoded PCM, computed the same way as the STREAMINFO MD5."""
    with open(path, "rb") as fh:
        p = subprocess.Popen([find_tool("flac"), "-d", "-c", "-s", "--force-raw-format",
                              "--endian=little", "--sign=signed", "-"],
                             stdin=fh, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             creationflags=_NO_WINDOW)
        h = hashlib.md5()
        for chunk in iter(lambda: p.stdout.read(1 << 20), b""):
            h.update(chunk)
        if p.wait() != 0:
            raise RuntimeError("Decoding failed")
    return h.hexdigest()


def decode(path: str, channels: int, bits: int) -> np.ndarray:
    """Decode to an int32 array shaped (frames, channels)."""
    with open(path, "rb") as fh:
        r = _run([find_tool("flac"), "-d", "-c", "-s", "--force-raw-format",
                  "--endian=little", "--sign=signed", "-"], stdin=fh)
    if r.returncode != 0:
        raise RuntimeError("Decoding failed: " + r.stderr.decode(errors="replace").strip())
    raw = r.stdout
    width = (bits + 7) // 8
    if width == 1:
        a = np.frombuffer(raw, dtype=np.int8).astype(np.int32)
    elif width == 2:
        a = np.frombuffer(raw, dtype="<i2").astype(np.int32)
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        a = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        a = np.where(a & 0x800000, a - 0x1000000, a)
    else:
        a = np.frombuffer(raw, dtype="<i4").astype(np.int32)
    return a.reshape(-1, channels)


def encode(samples: np.ndarray, bits: int, rate: int, dst: str) -> None:
    """Encode int32 samples (frames, channels) at ``bits`` to dst (with seektable, no padding)."""
    ch = samples.shape[1]
    if bits == 16:
        raw = samples.astype("<i2").tobytes()
    elif bits == 24:
        s = samples.astype("<i4").view(np.uint8).reshape(-1, 4)[:, :3]
        raw = np.ascontiguousarray(s).tobytes()
    else:
        raise ValueError("Only 16 or 24-bit output is supported")
    r = _run([find_tool("flac"), "-8", "-s", "-f", "--no-padding", "--force-raw-format",
              "--endian=little", "--sign=signed", f"--channels={ch}", f"--bps={bits}",
              f"--sample-rate={rate}", "-o", dst, "-"], input_bytes=raw)
    if r.returncode != 0:
        raise RuntimeError("Encoding failed: " + r.stderr.decode(errors="replace").strip())


def add_seektable(path: str) -> None:
    r = _run([find_tool("metaflac"), "--add-seekpoint=10s", path])
    if r.returncode != 0:
        raise RuntimeError("Could not add SEEKTABLE: " + r.stderr.decode(errors="replace").strip())
