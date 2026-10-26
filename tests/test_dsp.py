import numpy as np

from flac_cleaner.dsp import requantize, resample


def test_24_to_16_error_is_tpdf_noise():
    rng = np.random.default_rng(1)
    x = rng.integers(-(2 ** 23), 2 ** 23, size=(200_000, 2), dtype=np.int64).astype(np.int32)
    y = requantize(x, 24, 16, np.random.default_rng(2))
    assert y.dtype == np.int32 and y.min() >= -32768 and y.max() <= 32767
    err = y - x / 256.0
    assert abs(err.mean()) < 0.01
    # TPDF (±1 LSB) + rounding of a busy signal: rms ≈ 0.5 LSB, never beyond 1.5 LSB
    assert 0.45 < err.std() < 0.55
    assert np.abs(err).max() <= 1.5


def test_full_scale_does_not_wrap():
    x = np.array([[2 ** 23 - 1, -(2 ** 23)]] * 1000, dtype=np.int32)
    y = requantize(x, 24, 16)
    assert y[:, 0].max() <= 32767 and y[:, 1].min() >= -32768
    assert y[:, 0].min() >= 32766  # stays at full scale, no wrap-around to negative


def test_dither_decorrelates_low_level_signal():
    # A sine of 0.3 LSB would vanish entirely with plain rounding; with dither it survives as noise-modulated signal.
    t = np.arange(48000)
    x = (np.sin(2 * np.pi * 1000 * t / 48000) * 0.3 * 256).astype(np.int32)[:, None]
    plain = requantize(x, 24, 16, dither=False)
    dith = requantize(x, 24, 16, np.random.default_rng(0))
    assert not plain.any()
    corr = np.corrcoef(dith[:, 0], x[:, 0])[0, 1]
    assert corr > 0.1


def test_no_dither_is_exact_for_16bit_values():
    x = (np.arange(-1000, 1000, dtype=np.int32) * 256)[:, None]
    assert np.array_equal(requantize(x, 24, 16, dither=False)[:, 0], np.arange(-1000, 1000))


def test_resample_96k_to_44k1():
    sr = 96000
    t = np.arange(sr * 2) / sr
    x = np.stack([np.sin(2 * np.pi * 1000 * t), np.sin(2 * np.pi * 5000 * t)], axis=1) * 10000
    y = resample(x, sr, 44100)
    assert y.shape == (88200, 2)
    for ch, f in ((0, 1000), (1, 5000)):
        spec = np.abs(np.fft.rfft(y[:, ch] * np.hanning(len(y))))
        assert abs(np.fft.rfftfreq(len(y), 1 / 44100)[spec.argmax()] - f) < 2
    mid = y[22050:66150, 0]
    assert abs(np.abs(mid).max() - 10000) < 50  # amplitude preserved


def test_resample_removes_content_above_new_nyquist():
    sr = 96000
    t = np.arange(sr) / sr
    x = (np.sin(2 * np.pi * 30000 * t) * 10000)[:, None]  # 30 kHz can't exist at 44.1 kHz
    y = resample(x, sr, 44100)
    assert np.abs(y[4410:-4410]).max() < 10
