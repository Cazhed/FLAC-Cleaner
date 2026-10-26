"""Settings, presets and app data locations."""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, fields

from .tags import TagOptions


@dataclass
class Settings:
    # Tags
    keep_genre: bool = False
    keep_isrc: bool = False
    keep_label: bool = False
    keep_musicbrainz: bool = False
    keep_replaygain: bool = False
    keep_lyrics: bool = False
    normalize_date: bool = True
    fill_disc: bool = True
    # Filename
    rename: bool = True
    template: str = "{title} - {artist}"
    spaces_to_underscores: bool = True
    # Cover
    cover_enabled: bool = True          # add/replace covers where missing or low-res
    cover_min_px: int = 600             # covers smaller than this count as low-res
    cover_resize: bool = True
    cover_size: int = 640
    cover_normalize_existing: bool = True  # re-encode existing covers to baseline JPEG at cover_size
    online_lookup: bool = False         # off until the user turns it on
    # Audio
    convert_16bit: bool = False
    resample_44k: bool = False
    seektable: bool = True
    verify: bool = True
    # Output
    overwrite_originals: bool = False
    backup_originals: bool = True
    output_dir: str = ""
    mirror_folders: bool = True

    def tag_options(self) -> TagOptions:
        return TagOptions(**{f.name: getattr(self, f.name) for f in fields(TagOptions)})

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in names})


# Settings that belong to a preset (output location and network permission are per-user, not per-preset).
PRESET_EXCLUDE = {"output_dir", "overwrite_originals", "backup_originals", "online_lookup"}

BUILTIN_PRESETS: dict[str, Settings] = {
    "Ginkgo format": Settings(),
    "Ginkgo format + HD cover (1400 px)": Settings(cover_size=1400),
}


def app_dir() -> str:
    base = os.environ.get("APPDATA") if sys.platform == "win32" else os.path.expanduser("~/.config")
    d = os.path.join(base or os.path.expanduser("~"), "FLACCleaner")
    os.makedirs(os.path.join(d, "presets"), exist_ok=True)
    return d


def _preset_path(name: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip() or "preset"
    return os.path.join(app_dir(), "presets", safe + ".json")


def list_presets() -> list[str]:
    names = list(BUILTIN_PRESETS)
    folder = os.path.join(app_dir(), "presets")
    for fn in sorted(os.listdir(folder)):
        if fn.lower().endswith(".json"):
            try:
                with open(os.path.join(folder, fn), encoding="utf-8") as fh:
                    n = json.load(fh).get("name") or fn[:-5]
            except (OSError, ValueError):
                continue
            if n not in names:
                names.append(n)
    return names


def load_preset(name: str, base: Settings | None = None) -> Settings:
    base = base or Settings()
    if name in BUILTIN_PRESETS:
        d = BUILTIN_PRESETS[name].to_dict()
    else:
        with open(_preset_path(name), encoding="utf-8") as fh:
            d = json.load(fh).get("settings", {})
    merged = base.to_dict()
    merged.update({k: v for k, v in d.items() if k not in PRESET_EXCLUDE})
    return Settings.from_dict(merged)


def save_preset(name: str, s: Settings) -> None:
    if name in BUILTIN_PRESETS:
        raise ValueError("Built-in presets can't be overwritten. Choose another name.")
    d = {k: v for k, v in s.to_dict().items() if k not in PRESET_EXCLUDE}
    with open(_preset_path(name), "w", encoding="utf-8") as fh:
        json.dump({"name": name, "settings": d}, fh, indent=2, ensure_ascii=False)


def delete_preset(name: str) -> None:
    if name not in BUILTIN_PRESETS and os.path.exists(_preset_path(name)):
        os.remove(_preset_path(name))


def load_last_settings() -> tuple[Settings, str]:
    try:
        with open(os.path.join(app_dir(), "settings.json"), encoding="utf-8") as fh:
            d = json.load(fh)
        return Settings.from_dict(d.get("settings", {})), d.get("preset", "Ginkgo format")
    except (OSError, ValueError):
        return Settings(), "Ginkgo format"


def save_last_settings(s: Settings, preset: str) -> None:
    try:
        with open(os.path.join(app_dir(), "settings.json"), "w", encoding="utf-8") as fh:
            json.dump({"preset": preset, "settings": s.to_dict()}, fh, indent=2, ensure_ascii=False)
    except OSError:
        pass
