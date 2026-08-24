"""Bit-depth-aware image loading.

Guarantees about anything this module returns: the pixels are a 2D array of the exact
unsigned integer type the file declared (``uint8`` or ``uint16``), never rescaled,
never converted, never squashed to 8-bit; the container format was determined from the
file's own signature bytes rather than its extension; and the recorded ``bit_depth``,
``max_value`` and ``lossy_format`` describe that file. Anything else raises.

**Channel collapse.** One narrow exception to "2D or raise", and it is a ruling rather
than a convenience: ``data/real/AMENDMENT_2026-08-19_channel_collapse.md`` (ratified
2026-08-20, digest-pinned in ``tools/check_claims.py``) permits a 3-channel input to be
collapsed to one channel *only where the collapse decides nothing* -- channels
byte-identical, or a maximum per-pixel divergence at or below the bound the human fixed
in advance of measuring any crop. Above that bound the loader still refuses, and the
refusal names the divergence it measured. The bound and the method are not parameters
and are deliberately not in ``PipelineConfig``: the amendment fixes both, and DEBT S19
says so in terms ("Not a config change: the bound and the method are fixed by the
amendment, not selected"). They are module constants so that a reader of a refusal can
find the ruling that produced it.
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

RGB_CHANNEL_COUNT = 3
"""Third-axis extent a decoded image must have for the ruled collapse to apply at all."""

CHANNEL_COLLAPSE_METHOD = "green"
"""The collapse method fixed by the 2026-08-19 §7 amendment, ruling (b): take green.

Recorded verbatim into provenance. Green because a colour PNG derived from a greyscale
scan carries the same signal in all three planes up to codec noise, and green is the plane
a chroma-subsampled encoder preserves most faithfully. The amendment is explicit that the
choice is *recorded rather than argued from the data*: at or below the bound no plane
choice can move a reported ratio by more than the aperture error the pipeline already
carries, which is why the bound and the method are separable.
"""

GREEN_CHANNEL_INDEX = 1
"""Index of the green plane in a decoded 3-channel array.

Green is the middle plane in both RGB and OpenCV's BGR ordering, so this index is correct
whichever way the decoder hands the planes over -- and the collapse is therefore not
resting on a channel-order assumption that a decoder upgrade could silently invert.
"""

CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN = 2
"""Ruled bound, in DN of an 8-bit full scale, on the divergence a collapse may cross.

**Named before the divergence table was measured** -- the human fixed it in the session
record of 2026-08-19, in advance of seeing any per-crop value, and the amendment does not
revise it now that the values are visible. That ordering is the whole justification: a
bound chosen after the data would be a measurement parameter selected without data to
select it, which is what §7 refuses; the same bound chosen before the data is a criterion.
Not a config key for exactly that reason -- a value in ``configs/`` is a value somebody may
tune, and this one may not be tuned.
"""

COLLAPSIBLE_BIT_DEPTH = 8
"""The only bit depth the ruled bound is defined for.

The amendment measures divergence "in DN against an 8-bit full scale of 255" and was ruled
on a set of 8-bit crops. Applying a 2 DN bound to a 16-bit image would silently reinterpret
it as roughly 0.003% of full scale rather than 0.8%, i.e. extrapolate a human ruling into a
domain the human never saw. A 16-bit multi-channel input is therefore refused, loudly and
by name, rather than collapsed under a bound that does not reach it.
"""


@dataclass(frozen=True)
class ChannelCollapse:
    """The record of a ruled 3-channel -> 1-channel collapse, as it reaches provenance.

    Exactly the two fields the 2026-08-19 §7 amendment fixes, in the shape it writes them::

        channel_collapse: { method: green, max_divergence_dn: <measured> }

    ``max_divergence_dn`` is carried even when it is zero, because the amendment's stated
    purpose for it is that "a reader of a result can see how close to the bound that image
    sat" -- and zero is the most informative value that question has. The *absence* of this
    block on a result is what records a single-channel source: a document that carries one
    had a multi-channel file behind it, and a document that does not, did not.
    """

    method: str
    max_divergence_dn: int

    def as_dict(self) -> dict[str, Any]:
        """Return this collapse as it appears under ``source`` in a result document."""
        return {"method": self.method, "max_divergence_dn": self.max_divergence_dn}


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
    channel_collapse: ChannelCollapse | None = None

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

        ``channel_collapse`` appears only when one happened. It is not emitted as ``null``
        for a single-channel file: a present-but-null block would say "a collapse was
        considered and declined", which is not what the loader did.
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
        if self.channel_collapse is not None:
            source["channel_collapse"] = self.channel_collapse.as_dict()
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


def channel_divergence_dn(array: np.ndarray) -> int:
    """Return the maximum over pixels of ``max(|R-G|, |R-B|, |G-B|)``, in DN.

    The measurement the 2026-08-19 §7 amendment rules on, computed exactly as the amendment
    states it. Differences are taken in a signed wide type: subtracting one ``uint8`` plane
    from another wraps, and a wrapped difference would report 1 DN where the true divergence
    is 255 -- the one crop in the Gate 2 set whose divergence *is* 255 would then have
    collapsed silently.

    Independent of channel order, since it is a maximum over all three unordered pairs, so it
    does not care whether the decoder handed over RGB or BGR.

    **The returned DN is in the array's own full scale**, so on ``uint16`` input it is out of
    255ths and is *not* comparable to :data:`CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN`, whose bound
    the amendment states against an 8-bit full scale. :func:`_collapse_channels` never makes
    that comparison, because it refuses anything but 8-bit first; a direct caller must not.

    Public, so it gates its own input rather than relying on its caller's checks: widening a
    float or 64-bit array to ``int32`` would truncate silently, and a truncated divergence reads
    low, which is the direction that admits an image the ruling refuses.
    """
    if array.ndim != 3 or array.shape[2] != RGB_CHANNEL_COUNT:
        raise UnsupportedImageError(
            f"channel divergence is defined for a {RGB_CHANNEL_COUNT}-channel array; got "
            f"shape {array.shape}"
        )
    if array.dtype not in _BIT_DEPTH_BY_DTYPE:
        raise UnsupportedBitDepthError(
            f"channel divergence is defined for uint8 and uint16 arrays; got {array.dtype}. "
            f"Widening this type to compute the difference would truncate it, and a truncated "
            f"divergence reads low"
        )
    planes = array.astype(np.int32)
    first, second, third = planes[:, :, 0], planes[:, :, 1], planes[:, :, 2]
    divergence = np.maximum(
        np.maximum(np.abs(first - second), np.abs(first - third)), np.abs(second - third)
    )
    return int(divergence.max())


def _collapse_channels(array: np.ndarray, path: Path) -> tuple[np.ndarray, ChannelCollapse]:
    """Return the green plane of ``array`` and the record of the collapse, or raise.

    Raises :class:`UnsupportedImageError` -- the same class this loader has always refused
    multi-channel input with -- for a shape the ruling does not cover, a bit depth its bound is
    not defined for, or a measured divergence above the bound.

    Two of the three carry the loader's long-standing "single-channel grayscale gel-doc images
    only" wording. The bit-depth arm deliberately does not: the input *is* a 3-channel image
    that would otherwise be admissible, and what disqualifies it is the depth, so repeating the
    channel-count sentence there would name the wrong reason. Only the divergence arm quotes a
    measurement, and it names both the value and the bound, so whoever reads it can place the
    crop against the amendment's own table without re-deriving either.
    """
    if array.ndim != 3 or array.shape[2] != RGB_CHANNEL_COUNT:
        raise UnsupportedImageError(
            f"{path} decoded to shape {array.shape}; this pipeline quantifies "
            f"single-channel grayscale gel-doc images only (PLAN.md MVP scope). The ruled "
            f"channel collapse (data/real/AMENDMENT_2026-08-19_channel_collapse.md) covers "
            f"{RGB_CHANNEL_COUNT}-channel input only, so it does not reach this shape. "
            f"Export a single channel rather than letting the pipeline pick one"
        )
    bit_depth = _BIT_DEPTH_BY_DTYPE.get(array.dtype)
    if bit_depth != COLLAPSIBLE_BIT_DEPTH:
        # Two different situations share this branch and the message distinguishes them: a
        # supported-but-wrong depth (uint16), where exporting one channel does fix it, and an
        # unsupported pixel type, where a single channel of it would raise on bit depth anyway.
        remedy = (
            f"Export a single {COLLAPSIBLE_BIT_DEPTH}-bit or 16-bit channel"
            if bit_depth is not None
            else "This pixel type is not one this pipeline reads at any channel count"
        )
        raise UnsupportedImageError(
            f"{path} is a {RGB_CHANNEL_COUNT}-channel image of pixel type {array.dtype}; the "
            f"ruled channel collapse is defined only for {COLLAPSIBLE_BIT_DEPTH}-bit input, "
            f"because its <= {CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN} DN bound is stated against "
            f"an {COLLAPSIBLE_BIT_DEPTH}-bit full scale. Applying that bound here would "
            f"reinterpret it as a different fraction of full scale than the one that was "
            f"ruled on. {remedy}"
        )
    measured = channel_divergence_dn(array)
    if measured > CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN:
        raise UnsupportedImageError(
            f"{path} decoded to shape {array.shape}; this pipeline quantifies "
            f"single-channel grayscale gel-doc images only (PLAN.md MVP scope). Its maximum "
            f"per-pixel channel divergence is {measured} DN, above the ruled bound of "
            f"{CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN} DN "
            f"(data/real/AMENDMENT_2026-08-19_channel_collapse.md, ruling (a)), so the "
            f"collapse to a single channel is not permitted for it: at this divergence the "
            f"choice of plane could change the numbers reported. The bound was fixed before "
            f"any crop was measured and is not moved to admit an image. Export a single "
            f"channel rather than letting the pipeline pick one"
        )
    # ascontiguousarray, not a bare slice: the slice is a strided view onto the interleaved
    # decode buffer, and every consumer downstream is entitled to assume a plain 2D array.
    plane = np.ascontiguousarray(array[:, :, GREEN_CHANNEL_INDEX])
    return plane, ChannelCollapse(
        method=CHANNEL_COLLAPSE_METHOD, max_divergence_dn=measured
    )


def load_image(path: Path) -> LoadedImage:
    """Load a single-channel 8- or 16-bit image.

    Guarantees that ``pixels`` holds the file's own values in the file's own pixel
    type. Raises :class:`FileNotFoundError` if the path is not a file,
    :class:`UnsupportedFormatError` for a container outside
    :data:`SUPPORTED_FORMATS`, :class:`UnsupportedImageError` for a multi-channel
    image the ruled collapse does not admit, and :class:`UnsupportedBitDepthError` for any
    pixel type other than ``uint8``/``uint16`` -- in particular, 16-bit data is never
    squashed to 8-bit and float or signed data is never rescaled.

    A 3-channel 8-bit image whose maximum per-pixel channel divergence is at or below
    :data:`CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN` is collapsed to its green plane rather than
    refused, and the operation is recorded on the returned
    :class:`LoadedImage` as :class:`ChannelCollapse` so that it reaches result provenance.
    Everything else multi-channel still raises, and ``sha256`` stays the digest of the file
    *as delivered* -- the collapse happens on the way into the pipeline and never rewrites
    an approved artefact, so the digest must keep identifying the bytes on disk.
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
    collapse: ChannelCollapse | None = None
    if array.ndim != 2:
        array, collapse = _collapse_channels(array, path)
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
        channel_collapse=collapse,
    )
