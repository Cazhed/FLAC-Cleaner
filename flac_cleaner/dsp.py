"""Bit depth reduction with TPDF dither, and optional resampling to 44.1 kHz."""
from __future__ import annotations

from math import gcd

import numpy as np


def requantize(x: np.ndarray, src_bits: int, dst_bits: int, rng: np.random.Generator | None = None,
               dither: bool = True) -> np.ndarray:
    """Convert samples to ``dst_bits`` with TPDF dither.

    ``x`` holds integer samples at ``src_bits`` (or float samples already in that
    integer scale, e.g. after resampling). Returns int32 samples at ``dst_bits``.
    Triangular dither of ±1 LSB (peak) is added before rounding, which turns
    truncation distortion into a constant low-level noise floor.
    """
    rng = rng or np.random.default_rng()
    y = x.astype(np.float64) * (2.0 ** (dst_bits - src_bits))
    if dither:
        y += rng.random(y.shape) - rng.random(y.shape)
    y = np.rint(y)
    lo, hi = -(2 ** (dst_bits - 1)), 2 ** (dst_bits - 1) - 1
    return np.clip(y, lo, hi).astype(np.int32)


def _fast_len(n: int, multiple: int) -> int:
    """Smallest length >= n that is a multiple of ``multiple`` with small prime factors."""
    m = -(-n // multiple) * multiple
    while True:
        k = m // multiple
        r = k
        for p in (2, 3, 5, 7):
            while r % p == 0:
                r //= p
        if r == 1:
            return m
        m += multiple


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """FFT-based band-limited resampling (per channel). Returns float64 in the input scale.

    Shape (n, channels). The signal is zero-padded to avoid wrap-around and the
    top 4% of the passband is tapered to soften the brick-wall filter.
    """
    if sr_in == sr_out:
        return x.astype(np.float64)
    g = gcd(sr_in, sr_out)
    up, down = sr_out // g, sr_in // g
    n = x.shape[0]
    pad = sr_in // 2  # half a second each side
    total = _fast_len(n + 2 * pad, down)
    n_out_total = total * up // down
    n_out = int(round(n * sr_out / sr_in))
    start_out = int(round(pad * sr_out / sr_in))
    out = np.empty((n_out, x.shape[1]), dtype=np.float64)
    keep = min(total, n_out_total) // 2 + 1
    taper_len = max(1, int(keep * 0.04))
    taper = np.ones(keep)
    taper[-taper_len:] = 0.5 * (1 + np.cos(np.linspace(0, np.pi, taper_len)))
    for c in range(x.shape[1]):
        buf = np.zeros(total, dtype=np.float64)
        buf[pad:pad + n] = x[:, c]
        spec = np.fft.rfft(buf)[:keep] * taper
        y = np.fft.irfft(spec, n=n_out_total) * (n_out_total / total)
        out[:, c] = y[start_out:start_out + n_out]
    return out
