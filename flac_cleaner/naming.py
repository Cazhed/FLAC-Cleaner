"""Filename templates and Windows-safe sanitizing."""
from __future__ import annotations

import os
import re
import unicodedata

DEFAULT_TEMPLATE = "{title} - {artist}"
MAX_NAME_LEN = 200
TOKEN_RE = re.compile(r"\{(\w+)(?::(\d+))?\}")
TOKENS = ["title", "artist", "album", "albumartist", "track", "disc", "year"]

_CHAR_MAP = {
    "/": "-", "\\": "-", ":": " -", "|": "-",
    "<": "(", ">": ")", '"': "'", "?": "", "*": "",
}
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def sanitize(text: str) -> str:
    """Make text safe for use inside a Windows filename (not a path)."""
    text = unicodedata.normalize("NFC", text or "")
    text = "".join(_CHAR_MAP.get(c, c) for c in text if ord(c) >= 32 and c != "\x7f")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fields_from_tags(tags: dict[str, list[str]]) -> dict[str, str]:
    def g(k: str) -> str:
        v = tags.get(k) or []
        return v[0] if v else ""

    date = g("DATE")
    year = date[:4] if re.match(r"^\d{4}", date) else ""
    return {
        "title": g("TITLE"),
        "artist": " & ".join(tags.get("ARTIST") or []) if len(tags.get("ARTIST") or []) > 1 else g("ARTIST"),
        "album": g("ALBUM"),
        "albumartist": g("ALBUMARTIST") or g("ARTIST"),
        "track": g("TRACKNUMBER"),
        "disc": g("DISCNUMBER"),
        "year": year,
    }


def render(template: str, fields: dict[str, str], replace_spaces: bool = False,
           fallback: str = "Untitled") -> str:
    """Render a template into a filename stem (without extension)."""
    template = re.sub(r"\.flac\s*$", "", template.strip(), flags=re.I) or DEFAULT_TEMPLATE

    def sub(m: re.Match) -> str:
        name, width = m.group(1).lower(), m.group(2)
        val = fields.get(name, "")
        if width and val.isdigit():
            val = val.zfill(int(width))
        return sanitize(val)

    stem = TOKEN_RE.sub(sub, template)
    stem = sanitize(stem)
    # Collapse separators left dangling by empty tokens, e.g. "Title - " -> "Title"
    stem = re.sub(r"^(?:\s*[-_.]\s*)+|(?:\s*[-_]\s*)+$", "", stem).strip()
    if not stem:
        stem = sanitize(fallback) or "Untitled"
    if replace_spaces:
        stem = stem.replace(" ", "_")
    return finalize_stem(stem)


def finalize_stem(stem: str, ext: str = ".flac", max_len: int = MAX_NAME_LEN) -> str:
    stem = stem.rstrip(" .")
    if stem.split(".")[0].upper() in _RESERVED:
        stem = stem + "_"
    limit = max_len - len(ext)
    if len(stem) > limit:
        stem = stem[:limit].rstrip(" ._-")
    return stem or "Untitled"


def unique_path(directory: str, stem: str, ext: str = ".flac", taken: set[str] | None = None,
                allow: str | None = None, underscore: bool = False) -> str:
    """Return a path in directory that is not taken and does not exist.

    ``taken`` holds case-folded paths already reserved in this batch.
    ``allow`` is a path that may be reused (the source file itself in overwrite mode).
    """
    taken = taken if taken is not None else set()
    allow_key = os.path.normcase(os.path.abspath(allow)).casefold() if allow else None
    n = 1
    while True:
        suffix = "" if n == 1 else (f"_({n})" if underscore else f" ({n})")
        s = finalize_stem(stem, ext, MAX_NAME_LEN - len(suffix)) + suffix
        path = os.path.join(directory, s + ext)
        key = os.path.normcase(os.path.abspath(path)).casefold()
        if key not in taken and (key == allow_key or not os.path.exists(path)):
            taken.add(key)
            return path
        n += 1
