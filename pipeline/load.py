"""Bit-depth-aware image loading.

Guarantees about anything this module returns: the pixels are a 2D array of the exact
unsigned integer type the file declared (``uint8`` or ``uint16``), never rescaled,
never converted, never squashed to 8-bit; the container format was determined from the
file's own signature bytes rather than its extension; and the recorded ``bit_depth``,
``max_value`` and ``lossy_format`` describe that file. Anything else raises.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import tifffile

from pipeline.errors import (
    UnsupportedBitDepthError,
    UnsupportedFormatError,
    UnsupportedImageError,
)

TIFF = "tiff"
PNG = "png"
JPEG = "jpeg"

SUPPORTED_FORMATS: tuple[str, ...] = (TIFF, PNG, JPEG)
"""Container formats this pipeline reads (PLAN.md MVP scope)."""

LOSSY_FORMATS: frozenset[str] = frozenset({JPEG})
"""Formats whose pixel values are not the values that were written."""

_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", PNG),
    (b"\xff\xd8\xff", JPEG),
    (b"II\x2a\x00", TIFF),
    (b"MM\x00\x2a", TIFF),
    (b"II\x2b\x00", TIFF),
    (b"MM\x00\x2b", TIFF),
)
"""Magic bytes -> format. The extension is never trusted: it is metadata, not content."""

_BIT_DEPTH_BY_DTYPE: dict[Any, int] = {np.dtype(np.uint8): 8, np.dtype(np.uint16): 16}
"""Pixel types this pipeline supports, and the bit depth each one declares."""


@dataclass(frozen=True)
class LoadedImage:
    """An image as loaded, together with everything provenance needs to describe it."""

    path: Path
    pixels: np.ndarray
    image_format: str
    bit_depth: int
    max_value: int
    lossy_format: bool
    sha256: str

    @property
    def width_px(self) -> int:
        """Return the image width in pixels."""
        return int(self.pixels.shape[1])

    @property
    def height_px(self) -> int:
        """Return the image height in pixels."""
        return int(self.pixels.shape[0])

    def as_source(self, ground_truth_image_id: str | None = None) -> dict[str, Any]:
        """Return the ``source`` block of a result document.

        ``ground_truth_image_id`` is supplied by the caller (the eval harness) and is
        never derived from the path: inferring a gold-set identity from a filename
        inside the analysis path is exactly the special-casing PLAN.md forbids.
        """
        source: dict[str, Any] = {
            "path": str(self.path),
            "sha256": self.sha256,
            "image_format": self.image_format,
            "bit_depth": self.bit_depth,
            "max_value": self.max_value,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "lossy_format": self.lossy_format,
        }
        if ground_truth_image_id is not None:
            source["ground_truth_image_id"] = ground_truth_image_id
        return source


def detect_format(data: bytes) -> str:
    """Return the container format of ``data`` from its signature bytes.

    Raises :class:`UnsupportedFormatError` for anything that is not TIFF, PNG or JPEG.
    """
    for signature, image_format in _SIGNATURES:
        if data.startswith(signature):
            return image_format
    prefix = data[:8].hex() if data else "<empty file>"
    raise UnsupportedFormatError(
        f"unrecognised image container (first bytes: {prefix}); this pipeline reads "
        f"{list(SUPPORTED_FORMATS)} only. Convert the image to one of them"
    )


def _decode(data: bytes, image_format: str, path: Path) -> np.ndarray:
    """Decode ``data`` to an array, raising :class:`UnsupportedFormatError` on failure."""
    if image_format == TIFF:
        try:
            return np.asarray(tifffile.imread(io.BytesIO(data)))
        except (ValueError, tifffile.TiffFileError) as error:
            raise UnsupportedFormatError(f"cannot decode {path} as TIFF: {error}") from error
    decoded = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if decoded is None:
        raise UnsupportedFormatError(
            f"cannot decode {path} as {image_format}; the file signature says "
            f"{image_format} but the payload is not readable"
        )
    return np.asarray(decoded)


def load_image(path: Path) -> LoadedImage:
    """Load a single-channel 8- or 16-bit image.

    Guarantees that ``pixels`` holds the file's own values in the file's own pixel
    type. Raises :class:`FileNotFoundError` if the path is not a file,
    :class:`UnsupportedFormatError` for a container outside
    :data:`SUPPORTED_FORMATS`, :class:`UnsupportedImageError` for a multi-channel
    image, and :class:`UnsupportedBitDepthError` for any pixel type other than
    ``uint8``/``uint16`` -- in particular, 16-bit data is never squashed to 8-bit and
    float or signed data is never rescaled.
    """
    if not path.is_file():
        raise FileNotFoundError(f"image not found: {path}")
    data = path.read_bytes()
    image_format = detect_format(data)
    array = _decode(data, image_format, path)

    if array.ndim < 2 or array.size == 0:
        raise UnsupportedFormatError(
            f"{path} carries no image data (decoded to shape {array.shape}); the file "
            f"declares itself {image_format} but is empty or truncated"
        )
    if array.ndim != 2:
        raise UnsupportedImageError(
            f"{path} decoded to shape {array.shape}; this pipeline quantifies "
            f"single-channel grayscale gel-doc images only (PLAN.md MVP scope). "
            f"Export a single channel rather than letting the pipeline pick one"
        )
    dtype = array.dtype
    if dtype not in _BIT_DEPTH_BY_DTYPE:
        raise UnsupportedBitDepthError(
            f"{path} has pixel type {dtype}; supported types are uint8 and uint16. "
            f"The pipeline will not rescale or squash it, because that would change "
            f"every intensity it then reports"
        )
    bit_depth = _BIT_DEPTH_BY_DTYPE[dtype]
    return LoadedImage(
        path=path,
        pixels=array,
        image_format=image_format,
        bit_depth=bit_depth,
        max_value=2**bit_depth - 1,
        lossy_format=image_format in LOSSY_FORMATS,
        sha256="sha256:" + hashlib.sha256(data).hexdigest(),
    )
