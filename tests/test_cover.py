import io

from PIL import Image

from flac_cleaner.cover import image_info, is_clean_jpeg, make_picture, process_cover


def _img(w, h, mode="RGB", fmt="PNG", **kw):
    im = Image.new(mode, (w, h), (200, 30, 30, 128) if mode == "RGBA" else (200, 30, 30))
    b = io.BytesIO()
    im.save(b, fmt, **kw)
    return b.getvalue()


def _is_progressive(data):
    with Image.open(io.BytesIO(data)) as im:
        return bool(im.info.get("progressive") or im.info.get("progression"))


def test_resize_to_target_baseline_jpeg():
    out = process_cover(_img(3000, 3000), 1000)
    info = image_info(out)
    assert (info.width, info.height, info.fmt) == (1000, 1000, "JPEG")
    assert not _is_progressive(out)


def test_progressive_input_becomes_baseline():
    src = _img(800, 800, fmt="JPEG", progressive=True, quality=80)
    assert _is_progressive(src)
    out = process_cover(src, 640)
    assert not _is_progressive(out)
    assert image_info(out).width == 640


def test_never_upscale():
    info = image_info(process_cover(_img(500, 500), 1400))
    assert info.width == 500


def test_nearly_square_is_cropped_and_wide_is_padded():
    a = image_info(process_cover(_img(1400, 1380), None))
    assert a.width == a.height == 1380
    b = image_info(process_cover(_img(1600, 900), 1000))
    assert b.width == b.height == 1000


def test_rgba_and_cmyk_inputs():
    assert image_info(process_cover(_img(700, 700, "RGBA"), 640)).fmt == "JPEG"
    cmyk = io.BytesIO()
    Image.new("CMYK", (700, 700)).save(cmyk, "JPEG")
    assert image_info(process_cover(cmyk.getvalue(), 640)).width == 640


def test_picture_block():
    pic = make_picture(process_cover(_img(1000, 1000), 640))
    assert pic.type == 3 and pic.mime == "image/jpeg"
    assert (pic.width, pic.height, pic.depth) == (640, 640, 24)


def test_clean_jpeg_detection():
    j = process_cover(_img(640, 640), 640)
    assert is_clean_jpeg(image_info(j), 640)
    assert not is_clean_jpeg(image_info(j), 500)
    assert not is_clean_jpeg(image_info(_img(640, 640)), 640)  # PNG
