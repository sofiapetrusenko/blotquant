"""Container encoding and decoding, and the canonical pixel digest.

The canonical definition of a generated image is the quantized numpy array, not the
file on disk: container bytes depend on the zlib/libjpeg build, whereas the array
does not. The ground truth therefore records :func:`pixel_digest` of the canonical
array, and the determinism tests compare digests, not file hashes.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import numpy as np
import tifffile

from synth.config import EncodingConfig, max_value_for_bit_depth
from synth.errors import UnsupportedFormatError

FORMAT_EXTENSIONS: dict[str, str] = {"tiff": ".tiff", "png": ".png", "jpeg": ".jpg"}
"""Container format -> file extension. Also the set of supported formats."""

LOSSY_FORMATS: frozenset[str] = frozenset({"jpeg"})
"""Formats that do not round-trip exactly; these earn a ``lossy_format`` QC label."""

MAX_BIT_DEPTH_BY_FORMAT: dict[str, int] = {"tiff": 16, "png": 16, "jpeg": 8}
"""Highest bit depth each container can carry in this project."""


def _require_supported(image_format: str, bit_depth: int) -> None:
    """Raise :class:`UnsupportedFormatError` unless the format can carry the bit depth."""
    if image_format not in FORMAT_EXTENSIONS:
        raise UnsupportedFormatError(
            f"unsupported image format {image_format!r}; supported formats are "
            f"{sorted(FORMAT_EXTENSIONS)}"
        )
    max_depth = MAX_BIT_DEPTH_BY_FORMAT[image_format]
    if bit_depth > max_depth:
        raise UnsupportedFormatError(
            f"{image_format} cannot carry {bit_depth}-bit data (maximum {max_depth}-bit); "
            f"choose a different container instead of downsampling the data"
        )
    # Validates the depth itself (8/16 only) and raises UnsupportedBitDepthError.
    max_value_for_bit_depth(bit_depth)


def extension_for(image_format: str) -> str:
    """Return the file extension used for ``image_format``."""
    if image_format not in FORMAT_EXTENSIONS:
        raise UnsupportedFormatError(
            f"unsupported image format {image_format!r}; supported formats are "
            f"{sorted(FORMAT_EXTENSIONS)}"
        )
    return FORMAT_EXTENSIONS[image_format]


def pixel_digest(array: np.ndarray) -> str:
    """Return a platform-independent ``sha256:...`` digest of a 2D integer image.

    Shape and dtype are folded into the digest, and bytes are taken in explicit
    little-endian order so the digest does not depend on host endianness.
    """
    if array.ndim != 2:
        raise ValueError(f"expected a 2D grayscale array, got shape {array.shape}")
    if array.dtype == np.uint8:
        canonical = array.astype("<u1")
    elif array.dtype == np.uint16:
        canonical = array.astype("<u2")
    else:
        raise ValueError(
            f"expected a uint8 or uint16 array, got dtype {array.dtype}; "
            f"quantize before hashing"
        )
    header = f"{canonical.dtype.str}|{array.shape[0]}x{array.shape[1]}|".encode()
    payload = header + np.ascontiguousarray(canonical).tobytes()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def write_image(
    path: Path,
    array: np.ndarray,
    image_format: str,
    bit_depth: int,
    encoding: EncodingConfig,
) -> None:
    """Write ``array`` to ``path`` in ``image_format``, raising on any writer failure."""
    _require_supported(image_format, bit_depth)
    expected_dtype = np.uint8 if bit_depth == 8 else np.uint16
    if array.dtype != expected_dtype:
        raise UnsupportedFormatError(
            f"{bit_depth}-bit output requires a {np.dtype(expected_dtype).name} array, "
            f"got {array.dtype}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)

    if image_format == "tiff":
        tifffile.imwrite(
            str(path),
            array,
            photometric="minisblack",
            compression=encoding.tiff_compression,
        )
        return

    if image_format == "png":
        params = [cv2.IMWRITE_PNG_COMPRESSION, encoding.png_compression_level]
    else:
        params = [cv2.IMWRITE_JPEG_QUALITY, encoding.jpeg_quality]
    if not cv2.imwrite(str(path), array, params):
        raise UnsupportedFormatError(
            f"OpenCV refused to write {image_format} to {path}; check the extension "
            f"and the array dtype ({array.dtype})"
        )


def read_image(path: Path, image_format: str, bit_depth: int) -> np.ndarray:
    """Read a written image back as a 2D array of the expected unsigned integer type.

    Verification helper for the determinism tests only. ``pipeline/`` must not import
    it -- ``pipeline/load.py`` owns loading, and importing anything from ``synth/``
    would break the anti-circularity invariant.
    """
    _require_supported(image_format, bit_depth)
    if not path.is_file():
        raise FileNotFoundError(f"image not found: {path}")

    if image_format == "tiff":
        array = np.asarray(tifffile.imread(str(path)))
    else:
        flag = cv2.IMREAD_UNCHANGED if image_format == "png" else cv2.IMREAD_GRAYSCALE
        decoded = cv2.imread(str(path), flag)
        if decoded is None:
            raise UnsupportedFormatError(f"OpenCV could not decode {path} as {image_format}")
        array = np.asarray(decoded)

    if array.ndim != 2:
        raise UnsupportedFormatError(
            f"expected a single-channel image at {path}, got shape {array.shape}"
        )
    expected_dtype = np.uint8 if bit_depth == 8 else np.uint16
    if array.dtype != expected_dtype:
        raise UnsupportedFormatError(
            f"{path} decoded as {array.dtype}, expected {np.dtype(expected_dtype).name}"
        )
    return array
