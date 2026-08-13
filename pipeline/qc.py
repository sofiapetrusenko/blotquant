"""Quality control: the flags that decide whether a number is publishable.

QC **annotates, never drops**. Every band keeps its measured intensity and carries its
flags beside it; whether a flagged band is excluded from a normalization *ratio* is
:mod:`pipeline.normalize`'s decision, is explicit, and is recorded (PLAN.md's key
invariants).

Five flags, three on a band and three on the image (``saturated`` is both):

* band ``saturated`` -- at least ``qc.saturated_min_clipped_pixels`` pixels inside the ROI
  sit at full scale, so the band's integrated intensity is a lower bound and not a
  measurement.
* band ``overlapping`` -- this ROI overlaps a same-lane neighbour's by at least
  ``qc.overlap_iou_threshold``, so the shared pixels are counted into both integrals.
  Purely geometric: the observation is :func:`max_same_lane_iou` and the criterion is
  :func:`is_overlapping`.
* band ``unresolved_shoulder`` -- the band's row profile is too asymmetric for a single
  band to explain: its two half-widths, measured at ``qc.shoulder_half_maximum_fraction``
  of the peak's height, differ by at least ``qc.shoulder_half_width_ratio``. A statement
  about *shape*, and a different question from ``overlapping``: it fires where the profile
  carries structure the detector reported as one maximum, whether or not two ROIs happen to
  overlap. It never creates a second band, a second centre or a second ROI.
* image ``saturated`` -- at least one band is saturated. A statement about bands, not
  about pixels: clipping that falls outside every ROI is a bright artifact or a hot
  background, not a compromised measurement, and this project's flags exist to qualify
  *measurements*.
* image ``lossy_format`` -- the file was decoded from a lossy container, so its pixel
  values are not the values the detector wrote.
* image ``low_dynamic_range`` -- the brightest band uses less than
  ``qc.dynamic_range_min_peak_fraction`` of the available range, so weaker bands in the
  same image sit too close to the noise to be quantified.

Every threshold arrives in :class:`pipeline.config.QcConfig` and is echoed into result
provenance. None is chosen by reference to the synthetic generator; NOTES.md's Phase 2
section records each criterion, and which of them coincide with a generator value.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from pipeline.config import QcConfig
from pipeline.detect import DetectedBand, Roi
from pipeline.errors import QcError

SATURATED = "saturated"
OVERLAPPING = "overlapping"
UNRESOLVED_SHOULDER = "unresolved_shoulder"
LOSSY_FORMAT = "lossy_format"
LOW_DYNAMIC_RANGE = "low_dynamic_range"

BAND_QC_FLAGS: tuple[str, ...] = (SATURATED, OVERLAPPING, UNRESOLVED_SHOULDER)
"""Band flag vocabulary, in the order flags are reported. ``saturated`` and ``overlapping``
are shared with the ground-truth vocabulary so an eval can score them like for like;
``unresolved_shoulder`` has no ground-truth counterpart and is a different question."""

IMAGE_QC_FLAGS: tuple[str, ...] = (SATURATED, LOSSY_FORMAT, LOW_DYNAMIC_RANGE)
"""Image flag vocabulary, in the order flags are reported."""


@dataclass(frozen=True)
class BandQc:
    """One band's QC verdict, plus every observation the verdict rests on.

    The three observations are reported beside the flags, not consumed by them, so that a
    reader of a result document can re-apply any threshold to the numbers the decision was
    made on -- and so that an eval can vary a threshold without re-deriving the observation.
    ``row_half_width_ratio`` is ``None`` when the shape statistic was censored (see
    :func:`pipeline.detect.half_width_ratio`), which is not the same as symmetric.
    """

    band_id: str
    flags: tuple[str, ...]
    clipped_pixel_count: int
    peak_value: float
    row_half_width_ratio: float | None


@dataclass(frozen=True)
class QcReport:
    """QC for one image: a verdict per band, in detection order, and the image's own flags."""

    bands: tuple[BandQc, ...]
    image_flags: tuple[str, ...]

    def flags_by_band_id(self) -> Mapping[str, tuple[str, ...]]:
        """Return each band's flags keyed by band id."""
        return {band.band_id: band.flags for band in self.bands}


def roi_iou(first: Roi, second: Roi) -> float:
    """Return the intersection-over-union of two ROIs, in [0, 1].

    A separate implementation from ``evals.metrics.iou`` and from the generator's own
    ``box_iou`` -- ``pipeline/`` may import neither -- so ``tests/test_pipeline_qc.py``
    pins all three to the same numbers on shared fixtures rather than assuming they agree.
    The union is always positive because :class:`pipeline.detect.Roi` rejects an empty
    rectangle.
    """
    overlap_x = max(0, min(first.x + first.width, second.x + second.width) - max(first.x, second.x))
    overlap_y = max(
        0, min(first.y + first.height, second.y + second.height) - max(first.y, second.y)
    )
    intersection = overlap_x * overlap_y
    union = first.width * first.height + second.width * second.height - intersection
    return intersection / union


def _clipped_pixel_count(pixels: np.ndarray, roi: Roi, max_value: int) -> int:
    """Return how many pixels inside ``roi`` sit at full scale.

    Full scale is the only *observable* definition of clipping: a pixel that reads
    ``max_value`` is indistinguishable from one that was truncated there, so this is what a
    pipeline can see and therefore what it reports. It is derived from the bit depth the
    file declared, not from a configured level.
    """
    rows, columns = roi.slices()
    return int(np.count_nonzero(pixels[rows, columns] >= max_value))


def max_same_lane_iou(bands: Sequence[tuple[str, str, Roi]]) -> dict[str, float]:
    """Return, per band id, the largest ROI overlap it has with a same-lane neighbour.

    Takes ``(band_id, lane_id, roi)`` triples so that a caller holding a result document can
    ask the same question of it as :func:`assess` asks of a detection -- the observation the
    ``overlapping`` flag is decided on, separated from the threshold that decides it.

    Same-lane only: two bands in different lanes are different measurements of different
    samples, and their rectangles abut by construction because lane rectangles partition the
    image (:func:`pipeline.detect.detect_lanes`). A band with no same-lane neighbour has no
    overlap to report and gets ``0.0``, which is the honest answer for "how much of this ROI is
    shared" rather than a missing value.
    """
    largest = {band_id: 0.0 for band_id, _, _ in bands}
    for index, (band_id, lane_id, roi) in enumerate(bands):
        for other_id, other_lane, other_roi in bands[index + 1 :]:
            if other_lane != lane_id:
                continue
            overlap = roi_iou(roi, other_roi)
            largest[band_id] = max(largest[band_id], overlap)
            largest[other_id] = max(largest[other_id], overlap)
    return largest


def is_overlapping(max_same_lane_roi_iou: float, config: QcConfig) -> bool:
    """Return whether an ROI sharing this much with a same-lane neighbour is overlapping.

    The criterion itself, exported for the same reason as :func:`is_saturated`: anything
    varying the threshold applies *this* rule to the observation rather than restating the
    comparison.
    """
    return max_same_lane_roi_iou >= config.overlap_iou_threshold


def is_saturated(clipped_pixel_count: int, config: QcConfig) -> bool:
    """Return whether a band with this many full-scale pixels is saturated under ``config``.

    The criterion itself, exported so that anything varying the threshold -- an eval recording
    what a different value would have decided -- applies *this* rule to the pipeline's own
    observation rather than restating the comparison. ``evals/sweep.py``'s sensitivity surface
    calls it, and ``tests/test_sweep_check.py`` pins its shipped row against the flag.
    """
    return clipped_pixel_count >= config.saturated_min_clipped_pixels


def is_unresolved_shoulder(row_half_width_ratio: float | None, config: QcConfig) -> bool:
    """Return whether a peak of this asymmetry carries an unresolved shoulder under ``config``.

    ``None`` is a censored measurement, not a symmetric one, and never flags. Exported for the
    same reason as :func:`is_saturated`.
    """
    if row_half_width_ratio is None:
        return False
    return row_half_width_ratio >= config.shoulder_half_width_ratio


def is_low_dynamic_range(brightest_peak_value: float, max_value: int, config: QcConfig) -> bool:
    """Return whether an image whose brightest band peaks this low has too little range.

    ``brightest_peak_value`` is the largest background-corrected band peak in the image, in DN,
    and ``max_value`` is full scale for its bit depth. Exported for the same reason as
    :func:`is_saturated`: anything varying the threshold applies *this* rule to the pipeline's
    own observation rather than restating the comparison.
    """
    return brightest_peak_value < config.dynamic_range_min_peak_fraction * max_value


def _band_flags(
    clipped: int,
    overlap: float,
    row_half_width_ratio: float | None,
    config: QcConfig,
) -> tuple[str, ...]:
    """Return one band's flags, in :data:`BAND_QC_FLAGS` order."""
    flags: list[str] = []
    if is_saturated(clipped, config):
        flags.append(SATURATED)
    if is_overlapping(overlap, config):
        flags.append(OVERLAPPING)
    if is_unresolved_shoulder(row_half_width_ratio, config):
        flags.append(UNRESOLVED_SHOULDER)
    return tuple(flags)


def assess(
    pixels: np.ndarray,
    corrected: np.ndarray,
    bands: Sequence[DetectedBand],
    max_value: int,
    lossy_format: bool,
    config: QcConfig,
) -> QcReport:
    """Return the QC report for one image.

    ``pixels`` are the values as loaded, because clipping is a property of the delivered
    file; ``corrected`` is the background-corrected image, because a band's peak *above
    background* is what dynamic range is about. Both must have the same shape, and every
    ROI must lie inside them.

    Guarantees: one :class:`BandQc` per input band, in the same order and with the same
    ids; every flag drawn from :data:`BAND_QC_FLAGS` / :data:`IMAGE_QC_FLAGS`; no band is
    dropped, whatever it carries. Raises :class:`pipeline.errors.QcError` for a
    non-positive ``max_value``, mismatched shapes, or an ROI outside the image.
    """
    if pixels.ndim != 2 or corrected.ndim != 2:
        raise QcError(
            f"QC needs 2D images, got pixels {pixels.shape} and corrected {corrected.shape}"
        )
    if pixels.shape != corrected.shape:
        raise QcError(
            f"the loaded image {pixels.shape} and the corrected image {corrected.shape} must "
            f"have the same shape; QC reads clipping from one and peak height from the other"
        )
    if max_value <= 0:
        raise QcError(
            f"max_value must be positive, got {max_value}; it is the full-scale value of the "
            f"source bit depth and is what clipping and dynamic range are measured against"
        )
    height_px, width_px = pixels.shape
    for band in bands:
        if band.roi.x + band.roi.width > width_px or band.roi.y + band.roi.height > height_px:
            raise QcError(
                f"band {band.band_id} has ROI {band.roi.as_dict()}, which extends past the "
                f"{width_px}x{height_px} image; detection must clamp ROIs before QC"
            )

    overlaps = max_same_lane_iou([(band.band_id, band.lane_id, band.roi) for band in bands])
    reports: list[BandQc] = []
    for band in bands:
        rows, columns = band.roi.slices()
        clipped = _clipped_pixel_count(pixels, band.roi, max_value)
        ratio = band.row_profile.half_width_ratio(config.shoulder_half_maximum_fraction)
        reports.append(
            BandQc(
                band_id=band.band_id,
                flags=_band_flags(clipped, overlaps[band.band_id], ratio, config),
                clipped_pixel_count=clipped,
                peak_value=float(corrected[rows, columns].max()),
                row_half_width_ratio=ratio,
            )
        )

    image_flags: list[str] = []
    if any(SATURATED in report.flags for report in reports):
        image_flags.append(SATURATED)
    if lossy_format:
        image_flags.append(LOSSY_FORMAT)
    # No band at all is the strongest possible statement of insufficient range: nothing in
    # the image cleared detection, so nothing in it is quantifiable.
    brightest = max((report.peak_value for report in reports), default=0.0)
    if is_low_dynamic_range(brightest, max_value, config):
        image_flags.append(LOW_DYNAMIC_RANGE)
    return QcReport(bands=tuple(reports), image_flags=tuple(image_flags))
