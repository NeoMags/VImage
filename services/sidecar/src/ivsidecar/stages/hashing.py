"""Stage 3: content hashes.

- SHA-256 of the file bytes: asset identity (exact duplicates).
- 64-bit perceptual hash (DCT pHash) of the decoded, orientation-corrected image: near
  duplicates, compared by Hamming distance in `vec_phash`. Same construction as the common
  `imagehash.phash`: 32x32 greyscale, 2-D DCT-II, top-left 8x8 block, bit = value > median.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

import numpy as np

CHUNK = 1024 * 1024


def sha256_file(path: Path) -> bytes:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.digest()


@lru_cache(maxsize=1)
def _dct_matrix(n: int = 32) -> np.ndarray:
    # Orthonormal DCT-II basis: C[k, i] = a(k) cos(pi (2i + 1) k / 2n)
    k = np.arange(n)[:, None]
    i = np.arange(n)[None, :]
    c = np.cos(np.pi * (2 * i + 1) * k / (2 * n)) * np.sqrt(2.0 / n)
    c[0, :] = np.sqrt(1.0 / n)
    return c


def phash_from_grey32(pixels: np.ndarray) -> bytes:
    """pixels: 32x32 greyscale array. Returns 8 bytes (64 bits, row-major, MSB first)."""
    if pixels.shape != (32, 32):
        raise ValueError(f"expected 32x32, got {pixels.shape}")
    c = _dct_matrix(32)
    dct = c @ pixels.astype(np.float64) @ c.T
    low = np.round(dct[:8, :8], 6)  # drop float noise so flat images hash deterministically
    bits = (low > np.median(low)).flatten()
    return np.packbits(bits).tobytes()


def hamming(a: bytes, b: bytes) -> int:
    return int.from_bytes(bytes(x ^ y for x, y in zip(a, b, strict=True)), "big").bit_count()
