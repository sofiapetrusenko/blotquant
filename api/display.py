"""The 8-bit PNG derivative a browser can show, and the record of what it went through.

**This code must not move into ``pipeline/``.** The pipeline measures pixels and refuses to
rescale them: :func:`pipeline.load.load_image` raises
:class:`pipeline.errors.UnsupportedBitDepthError` rather than squashing an unsupported
pixel type, and its message says why -- rescaling changes every intensity the pipeline then
reports. That refusal is shipped behaviour, not an implementation detail. Rendering an
image for a human to look at is presentation, and it lives on the other side of that line:
the result document keeps the measured values in the source's own DN, and what this module
produces is a derivative, labelled as one in the response.

**One mapping ships, and it is linear full-scale**: ``out = round(px * 255 / max_value)``,
where ``max_value`` is full scale of the source bit depth. It never clips -- no source value
is replaced by the output maximum, every source value is scaled by the same factor -- and a
full-scale (saturated) source pixel maps to 255.

Reducing 16 bits to 8 does bin ``max_value / 255`` source DN -- 257 of them at 16 bits --
into each output value, and the top output level is a bin rather than a single
source value: at 16 bits every source value from 65407 up renders as 255 -- 129 of them,
the top bin being half-width because the mapping rounds rather than floors. **A pixel reading
255 in the PNG is therefore not evidence of saturation.** The bin width is recorded as
``mapping.source_dn_per_output_level`` in every response, so a consumer can see that the
mapping is not injective instead of reading saturation off the brightest colour the format
has. Whether a band is saturated is a QC judgement made on the measured pixels and reported
in ``result``. Recording the step closes the same hazard the percentile-mode refusal below
was written to prevent, on the axis that refusal does not reach.

A percentile or window mode is deliberately **not** offered. Windowing maps the brightest
pixel *present* to 255, so an image whose peak sits at 40% of full scale renders with pure
white bands -- and a viewer comparing that white against the absence of a ``saturated`` QC
flag would conclude the flag had missed something. For a tool whose entire premise is that
saturation must be visible and honest, a display mode that manufactures apparent saturation
is not a convenience, it is a defect. The cost is accepted knowingly: a faint blot renders
faint, which is what it is.

The mapping's name, the source and output maxima, and the fact that it scales without
clipping are recorded in the response beside the PNG, because a viewer comparing a bright
region against a QC flag needs to know what the picture went through to get there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from api.errors import DisplayError

MAPPING_NAME = "linear_full_scale"
"""The only display mapping this service implements; recorded in every response."""

MAPPING_FORMULA = "out = round(px * 255 / max_value)"
"""The mapping, written out, so the record does not depend on knowing the name."""

OUTPUT_MAX_VALUE = 255
"""Full scale of the 8-bit derivative. PNG is 8-bit here because browsers display 8-bit."""

MEDIA_TYPE = "image/png"
"""Container the derivative is encoded in, via ``cv2.imencode`` -- no imaging dependency."""

DERIVATIVE_NOTE = (
    "8-bit rendering for display only. It is a derivative of the measured image, not the "
    "measured data: every reported intensity in `result` is in the source's own DN at its "
    "own bit depth, and was computed from the unrescaled pixels. One output level spans "
    "`mapping.source_dn_per_output_level` source DN, so a pixel reading 255 here is not "
    "evidence of saturation: saturation is a QC judgement made on the measured pixels and "
    "reported in `result`."
)
"""What the response says about the PNG, in words, beside the machine-readable flags."""

DISPLAY_BLOCK_KEYS: tuple[str, ...] = (
    "is_derivative",
    "note",
    "media_type",
    "width_px",
    "height_px",
    "mapping",
)
"""Every key :meth:`DisplayDerivative.as_block` emits, and the labelling a response needs.

Named here so that a *stored* display record can be checked against it before it is served:
the block travels to disk and back, and a damaged one that still parses as JSON would put an
unlabelled derivative -- no ``is_derivative``, no ``note``, no ``mapping`` -- into a response
that promises none exists. ``tests/test_api_display.py`` pins it against ``as_block``'s own
output so the two cannot drift.
"""


@dataclass(frozen=True)
class DisplayDerivative:
    """An 8-bit PNG rendering of one measured image, with the mapping that produced it."""

    png: bytes
    width_px: int
    height_px: int
    source_max_value: int

    def as_block(self) -> dict[str, Any]:
        """Return the response's ``display`` block *without* the encoded image.

        The PNG is excluded so that this exact JSON can be stored on disk beside the image
        bytes and handed back byte-identically by ``GET /results/{id}``. Both endpoints add
        the base64 image to it in one place, :func:`api.app._envelope`, so the shape a caller
        sees is defined once.
        """
        return {
            "is_derivative": True,
            "note": DERIVATIVE_NOTE,
            "media_type": MEDIA_TYPE,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "mapping": {
                "name": MAPPING_NAME,
                "formula": MAPPING_FORMULA,
                "source_max_value": self.source_max_value,
                "output_max_value": OUTPUT_MAX_VALUE,
                "scales": True,
                "clips": False,
                "source_dn_per_output_level": self.source_max_value / OUTPUT_MAX_VALUE,
            },
        }


def render_display(pixels: np.ndarray, max_value: int) -> DisplayDerivative:
    """Render ``pixels`` to an 8-bit PNG under the linear full-scale mapping.

    Guarantees, all asserted numerically in ``tests/test_api_display.py``:

    * a source pixel of ``max_value`` maps to 255 and a source pixel of 0 maps to 0;
    * no output is clipped -- the mapping is a scaling of the *whole* source range, so no
      input can exceed 255 after it;
    * an 8-bit source is reproduced value for value, because ``max_value`` is then 255 and
      the factor is exactly 1.

    Rounding is half-to-even (:func:`numpy.rint`), which is what Python's ``round`` in
    :data:`MAPPING_FORMULA` also does. The tie-breaking rule is unobservable at either
    supported depth: at 8 bits the factor is 1, and at 16 bits ``px * 255 / 65535`` lands
    exactly on a half only for ``px = 128.5 * odd``, which is never an integer.

    Raises :class:`api.errors.DisplayError` for an image or a full scale it cannot render;
    both would mean the pipeline had already measured something this module disagrees with.
    """
    if pixels.ndim != 2:
        raise DisplayError(
            f"the display derivative needs a 2D single-channel image, got shape "
            f"{pixels.shape}; the pipeline only measures single-channel images, so this is "
            f"an internal inconsistency rather than an input problem"
        )
    if pixels.size == 0:
        raise DisplayError("cannot render a display derivative of an empty image")
    if max_value < 1:
        raise DisplayError(
            f"the source's full scale must be at least 1 DN, got {max_value}; it comes from "
            f"the loaded image's bit depth and cannot legitimately be smaller"
        )
    scaled = np.rint(np.asarray(pixels, dtype=np.float64) * (OUTPUT_MAX_VALUE / max_value))
    # "Nothing was clipped" is recorded in every response, so it is checked rather than
    # assumed: an out-of-range value here would wrap silently through astype(uint8) and the
    # record would then be false. It can only happen if a pixel exceeds the full scale its
    # own bit depth declares, which the loader makes impossible.
    highest = float(scaled.max())
    if highest > OUTPUT_MAX_VALUE:
        raise DisplayError(
            f"the linear full-scale mapping produced {highest:g}, above the 8-bit maximum "
            f"{OUTPUT_MAX_VALUE}, so a pixel exceeded the declared full scale {max_value}; "
            f"refusing to emit a derivative that would clip while reporting that it does not"
        )
    rendered = scaled.astype(np.uint8)
    encoded, buffer = cv2.imencode(".png", rendered)
    if not encoded:
        raise DisplayError(
            f"cv2.imencode refused to encode a {rendered.shape} uint8 array as PNG; no "
            f"display derivative can be offered for this image"
        )
    return DisplayDerivative(
        png=buffer.tobytes(),
        width_px=int(rendered.shape[1]),
        height_px=int(rendered.shape[0]),
        source_max_value=int(max_value),
    )
