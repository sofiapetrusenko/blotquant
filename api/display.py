"""The display layer: the 8-bit PNG a browser can show, and the verdict a card reads.

Two outputs, one rule between them -- **presentation is derived, never measured**. Neither the
derivative nor the verdict adds a field to a result document, and neither exists in
``pipeline/``. See :func:`render_display` for the first and :func:`lane_verdicts` for the
second; the verdict's mapping is ruled and pre-registered in
``docs/PRE_REGISTRATION_2026-08-25_verdict_mapping.md`` and is summarised on the function.

The rest of this docstring is about the derivative.

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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from api.errors import DisplayError
from pipeline.load import CANONICAL_POLARITY
from pipeline.qc import BAND_QC_FLAGS

MAPPING_NAME = "linear_full_scale"
"""The only display mapping this service implements; recorded in every response."""

MAPPING_FORMULA = "out = round(px * 255 / max_value)"
"""The mapping, written out, so the record does not depend on knowing the name."""

INVERTED_MAPPING_FORMULA = "out = round((max_value - px) * 255 / max_value)"
"""The same mapping applied to a source the loader inverted on the way in.

``px`` in :data:`MAPPING_FORMULA` is the pixel as **delivered**, so on a ``dark_on_bright``
upload the identity claim it makes for an 8-bit source is false: what is rendered is the
canonical-polarity array the pipeline measured, which is ``max_value - px``. Rather than render
the delivered pixels and hide the one transformation that most changes the answer, the derivative
shows what was measured and the block says so. Added 2026-08-24 with the polarity amendment.
"""

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
    source_polarity: str
    """The polarity declared for the source, carried so the mapping block can be true.

    The derivative renders the array the pipeline measured. For a ``dark_on_bright`` source that
    is not the array the caller uploaded, and a mapping block that did not say so would be a
    false provenance statement in the one block a reader consults to learn how the picture was
    made.
    """

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
                "formula": (
                    MAPPING_FORMULA
                    if self.source_polarity == CANONICAL_POLARITY
                    else INVERTED_MAPPING_FORMULA
                ),
                "source_max_value": self.source_max_value,
                "source_polarity": self.source_polarity,
                "source_inverted": self.source_polarity != CANONICAL_POLARITY,
                "output_max_value": OUTPUT_MAX_VALUE,
                "scales": True,
                "clips": False,
                "source_dn_per_output_level": self.source_max_value / OUTPUT_MAX_VALUE,
            },
        }


def render_display(
    pixels: np.ndarray, max_value: int, source_polarity: str
) -> DisplayDerivative:
    """Render ``pixels`` to an 8-bit PNG under the linear full-scale mapping.

    Guarantees, all asserted numerically in ``tests/test_api_display.py``:

    * a source pixel of ``max_value`` maps to 255 and a source pixel of 0 maps to 0;
    * no output is clipped -- the mapping is a scaling of the *whole* source range, so no
      input can exceed 255 after it;
    * an 8-bit source is reproduced value for value **when its polarity is canonical**, because
      ``max_value`` is then 255 and the factor is exactly 1. A ``dark_on_bright`` source was
      inverted by the loader before it reached here, so what is reproduced value for value is
      the *measured* array rather than the delivered one -- and the mapping block says which,
      through ``source_polarity``, ``source_inverted`` and the formula it reports.

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
            f"{pixels.shape}; the loader delivers 2D pixels for every image it accepts -- a "
            f"multi-channel input is either collapsed to one plane or refused -- so this is "
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
        source_polarity=source_polarity,
    )


# --------------------------------------------------------------------------------------
# The display-layer verdict, ruled by Sofia 2026-08-25 and pre-registered in
# docs/PRE_REGISTRATION_2026-08-25_verdict_mapping.md.
# --------------------------------------------------------------------------------------

PASS = "pass"
FLAGGED = "flagged"
BLOCKED = "blocked"

VERDICTS: tuple[str, ...] = (PASS, FLAGGED, BLOCKED)
"""The closed verdict vocabulary. Three classes, because three were ruled.

A fourth class for "the lane emitted no ratio at all" was considered and refused: the ruling
names three, and the distinction is carried by :data:`BLOCKED_REASONS` instead, where it is
visible in the data rather than only in the prose that explains it.
"""

ALL_RATIOS_EXCLUDED = "all_ratios_excluded"
NO_RATIO_EMITTED = "no_ratio_emitted"

BLOCKED_REASONS: tuple[str, ...] = (ALL_RATIOS_EXCLUDED, NO_RATIO_EMITTED)
"""Why a blocked lane has no number. Only a blocked verdict carries one.

``all_ratios_excluded`` is the ruled case -- the lane produced ratios and every one of them was
excluded, so the input to each number was excluded. ``no_ratio_emitted`` is edge case E1 of the
pre-registration: a lane with no bands, or one whose only bands are its designated references,
produces no ratio at all. There is no number either way, which is why both are ``blocked``; the
reason says which, because the ruled sentence's *"because the input to it was excluded"* is true
of the first and not of the second.
"""


@dataclass(frozen=True)
class LaneVerdict:
    """One lane's display verdict, and every observation the verdict rests on.

    The counts and flags travel beside the verdict for the reason
    :class:`pipeline.qc.BandQc` carries its observations beside its flags: a reader can
    re-apply any other rule to the numbers the decision was made on without re-deriving them,
    and a UI can show *why* a card reads the way it does without a second pass over the
    document.
    """

    lane_id: str
    roi_source: str
    verdict: str
    blocked_reason: str | None
    qc_flags: tuple[str, ...]
    band_count: int
    ratio_count: int
    usable_ratio_count: int

    def as_dict(self) -> dict[str, Any]:
        """Return this verdict as JSON-ready data.

        ``blocked_reason`` is emitted only when there is one, on the opposite convention from
        ``excluded_from_normalization`` in :mod:`pipeline.analyze`: there, an omitted false
        would inherit a schema requirement written for excluded bands. Here nothing constrains
        the key, and a ``blocked_reason: null`` on a passing lane would invite a reader to look
        for a blocking cause that does not exist.
        """
        document: dict[str, Any] = {
            "lane_id": self.lane_id,
            "roi_source": self.roi_source,
            "verdict": self.verdict,
        }
        if self.blocked_reason is not None:
            document["blocked_reason"] = self.blocked_reason
        document["qc_flags"] = list(self.qc_flags)
        document["band_count"] = self.band_count
        document["ratio_count"] = self.ratio_count
        document["usable_ratio_count"] = self.usable_ratio_count
        return document


def _required(entry: Mapping[str, Any], key: str, what: str) -> Any:
    """Return ``entry[key]``, or raise :class:`DisplayError` naming what is missing.

    A stored result document is this derivation's only input, so a missing required key is a
    damaged document rather than a value to substitute a default for. Loud failure over silent
    fallback: a lane whose ``qc_flags`` had quietly defaulted to empty would read ``pass``.
    """
    if key not in entry:
        raise DisplayError(
            f"{what} has no {key!r}; the verdict is derived from a stored result document "
            f"alone, and {key!r} is required of it by schema/result.schema.json. This "
            f"document cannot be read back as the one this service wrote"
        )
    return entry[key]


def _ordered_flags(flags: Sequence[str]) -> tuple[str, ...]:
    """Return the distinct flags in :data:`pipeline.qc.BAND_QC_FLAGS` order, unknowns last.

    The vocabulary order rather than alphabetical, so a verdict's flag list reads the same way
    round as every other flag list in the project (``pipeline.normalize._ordered_flags`` makes
    the same choice for the same reason). A flag outside the vocabulary is sorted after the
    known ones rather than refused: this function reads *stored* documents, and a document
    written by a later vocabulary must still be legible to it.
    """
    distinct = set(flags)
    known = [flag for flag in BAND_QC_FLAGS if flag in distinct]
    unknown = sorted(distinct.difference(BAND_QC_FLAGS))
    return (*known, *unknown)


def lane_verdicts(result: Mapping[str, Any]) -> tuple[LaneVerdict, ...]:
    """Return one :class:`LaneVerdict` per lane of ``result``, in the document's lane order.

    **A display-layer derivation over a stored result document, and nothing more.** It adds no
    field to the measurement record, touches no code path in :mod:`pipeline`, reads no pixels,
    no config object and no file, and is fully recoverable from the document alone (Ruling 1,
    2026-08-25). ``python -m pipeline run`` produces documents this function reads; it does not
    produce verdicts, and nothing here is written back into one.

    The rule, evaluated in this order for a lane ``L``:

    1. no ratio of ``L`` survives with ``excluded: false`` -- **blocked**;
    2. otherwise no QC flag attaches to ``L`` -- **pass**;
    3. otherwise -- **flagged**.

    A flag "attaches to ``L``" if it appears on one of its bands, on one of its ratios, or on a
    ratio's ``reference_qc_flags`` -- the last of these because a lane whose every number was
    divided by a flagged denominator must not read ``pass`` on the strength of unflagged
    numerators (edge case E4).

    **``image_qc_flags`` is not read.** That is Ruling 3 of 2026-08-25 expressed as a data
    dependency: image-level saturation does not become a blocking cause, so a clean lane inside
    a saturated image is a ``pass`` lane. Building it the other way would have been choosing a
    code path against a corpus already known to carry the flag on every image, which Gate 1
    ruling 3 forbids.

    Raises :class:`api.errors.DisplayError` for a document that cannot be read back as one this
    service wrote: a missing required key, or a band or ratio naming a lane the document does
    not list.
    """
    lanes = _required(result, "lanes", "the result document")
    bands = _required(result, "bands", "the result document")
    normalization = _required(result, "normalization", "the result document")
    ratios = _required(normalization, "ratios", "the result document's normalization block")

    lane_ids = [_required(lane, "lane_id", "a lane") for lane in lanes]
    bands_by_lane: dict[str, list[Mapping[str, Any]]] = {lane_id: [] for lane_id in lane_ids}
    ratios_by_lane: dict[str, list[Mapping[str, Any]]] = {lane_id: [] for lane_id in lane_ids}
    for band in bands:
        lane_id = _required(band, "lane_id", f"band {band.get('band_id', '<unidentified>')!r}")
        if lane_id not in bands_by_lane:
            raise DisplayError(
                f"band {band.get('band_id', '<unidentified>')!r} names lane {lane_id!r}, "
                f"which is not among the document's lanes {lane_ids}; a verdict cannot be "
                f"derived for a lane the document does not describe"
            )
        bands_by_lane[lane_id].append(band)
    for ratio in ratios:
        lane_id = _required(ratio, "lane_id", "a ratio")
        if lane_id not in ratios_by_lane:
            raise DisplayError(
                f"a ratio names lane {lane_id!r}, which is not among the document's lanes "
                f"{lane_ids}; a verdict cannot be derived for a lane the document does not "
                f"describe"
            )
        ratios_by_lane[lane_id].append(ratio)

    verdicts: list[LaneVerdict] = []
    for lane in lanes:
        lane_id = _required(lane, "lane_id", "a lane")
        lane_bands = bands_by_lane[lane_id]
        lane_ratios = ratios_by_lane[lane_id]
        usable = [
            ratio
            for ratio in lane_ratios
            if not _required(ratio, "excluded", f"a ratio of lane {lane_id!r}")
        ]
        flags: list[str] = []
        for band in lane_bands:
            flags.extend(
                _required(band, "qc_flags", f"band {band.get('band_id', '<unidentified>')!r}")
            )
        for ratio in lane_ratios:
            # Optional on a ratio under schema/result.schema.json, unlike a band's, so an
            # absent list is a legal document rather than a damaged one and reads as no flags.
            # It can never hide a flag: normalize designates references from a lane's own
            # bands, so every reference flag also appears on a band read above.
            flags.extend(ratio.get("qc_flags", ()))
            flags.extend(ratio.get("reference_qc_flags", ()))
        ordered = _ordered_flags(flags)
        if not usable:
            verdict, reason = BLOCKED, (
                ALL_RATIOS_EXCLUDED if lane_ratios else NO_RATIO_EMITTED
            )
        elif not ordered:
            verdict, reason = PASS, None
        else:
            verdict, reason = FLAGGED, None
        verdicts.append(
            LaneVerdict(
                lane_id=lane_id,
                roi_source=_required(lane, "roi_source", f"lane {lane_id!r}"),
                verdict=verdict,
                blocked_reason=reason,
                qc_flags=ordered,
                band_count=len(lane_bands),
                ratio_count=len(lane_ratios),
                usable_ratio_count=len(usable),
            )
        )
    return tuple(verdicts)
