"""Cover image processing: square, resize, baseline JPEG, FLAC PICTURE block."""
from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps, ImageStat
from mutagen.flac import Picture

Image.MAX_IMAGE_PIXELS = 200_000_000  # big scans are fine, decompression bombs are not

COVER_SIZES = [640, 1000, 1400, 3000]
LOW_RES_PX = 600


@dataclass
class ImageInfo:
    width: int
    height: int
    fmt: str          # "JPEG", "PNG", ...
    size_bytes: int
    progressive: bool = False

    @property
    def side(self) -> int:
        return min(self.width, self.height)

    def describe(self) -> str:
        return f"{self.width}×{self.height} {self.fmt}, {self.size_bytes / 1024:.0f} KB"


def image_info(data: bytes) -> ImageInfo:
    with Image.open(io.BytesIO(data)) as im:
        prog = bool(im.info.get("progressive") or im.info.get("progression"))
        return ImageInfo(im.width, im.height, im.format or "?", len(data), prog)


def is_clean_jpeg(info: ImageInfo, target: int | None) -> bool:
    """True when an existing cover already matches the output rules and can be kept byte-for-byte."""
    return (info.fmt == "JPEG" and not info.progressive and info.width == info.height
            and (target is None or info.width <= target))


def _to_rgb(im: Image.Image) -> Image.Image:
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    if im.mode == "CMYK":
        # Adobe JPEGs store inverted CMYK; Pillow handles the conversion.
        return im.convert("RGB")
    return im.convert("RGB") if im.mode != "RGB" else im


def _make_square(im: Image.Image) -> Image.Image:
    w, h = im.size
    if w == h:
        return im
    side_min, side_max = min(w, h), max(w, h)
    if (side_max - side_min) / side_max <= 0.05:
        left, top = (w - side_min) // 2, (h - side_min) // 2
        return im.crop((left, top, left + side_min, top + side_min))
    # Clearly not square: pad with the average edge colour instead of cutting art off.
    edge = ImageStat.Stat(ImageOps.expand(im, border=0).resize((16, 16))).mean
    color = tuple(int(c) for c in edge[:3])
    canvas = Image.new("RGB", (side_max, side_max), color)
    canvas.paste(im, ((side_max - w) // 2, (side_max - h) // 2))
    return canvas


def process_cover(data: bytes, size: int | None = 1000, quality: int = 90) -> bytes:
    """Return a square, baseline JPEG no larger than ``size`` (never upscaled)."""
    with Image.open(io.BytesIO(data)) as src:
        src.load()
        im = ImageOps.exif_transpose(src)
        im = _to_rgb(im)
    im = _make_square(im)
    if size and im.width > size:
        im = im.resize((size, size), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=quality, optimize=True, progressive=False)
    return out.getvalue()


def make_picture(jpeg: bytes) -> Picture:
    info = image_info(jpeg)
    pic = Picture()
    pic.type = 3  # Cover (front)
    pic.mime = "image/jpeg" if info.fmt == "JPEG" else f"image/{info.fmt.lower()}"
    pic.desc = "Cover"
    pic.width, pic.height = info.width, info.height
    pic.depth = 24
    pic.data = jpeg
    return pic


def thumbnail_png(data: bytes, px: int = 256) -> bytes:
    with Image.open(io.BytesIO(data)) as im:
        im = _to_rgb(im)
        im.thumbnail((px, px))
        out = io.BytesIO()
        im.save(out, "PNG")
        return out.getvalue()
