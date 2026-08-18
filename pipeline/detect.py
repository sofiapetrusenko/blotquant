"""Lane and band detection by profile projection.

The shape of the method, in one paragraph. Lanes are vertical, so summing the
background-corrected image down its columns turns each lane into a peak of a 1D
column profile; the lanes are the peaks of that profile and their boundaries are the
midpoints between neighbouring lane centres. Inside one lane, bands are horizontal, so
averaging that lane's columns turns each band into a peak of a 1D row profile; the
bands are the peaks of that profile. A band's vertical extent is where its own peak
falls to a configured fraction of its height, and its horizontal extent is the same
rule applied to the column profile of the rows it occupies -- bounded by the lane,
because a band cannot be wider than the lane it migrated in.

Each band also carries the row profile its ROI was walked from (:class:`RowProfile`), so
that :mod:`pipeline.qc` can measure the peak's asymmetry (:func:`half_width_ratio`) at its
own configured level and turn it into the ``unresolved_shoulder`` flag. Nothing about
detection changes: one resolved maximum is still one band, one centre and one ROI, and no
threshold or criterion for that flag lives in this module.

Nothing here assumes how many lanes or bands exist, that lanes are evenly spaced, that
bands are the same size, that band rows are shared between lanes, or any particular
peak shape. Those would be assumptions about the synthetic generator (``synth/MODELS.md``,
"What counts as special-casing") rather than about gel-doc images.

Lanes may also be supplied by the caller instead of detected (:func:`detect`'s
``lane_rois``). Band detection inside them is the same code, with the same parameters:
the supplied rectangles replace :func:`detect_lanes`'s output and nothing else. Which of
the two produced a lane is recorded on it as :attr:`DetectedLane.roi_source` and travels
into the result document, so a caller-chosen region is never reported as a detector output.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.signal import find_peaks

from pipeline.config import DetectionConfig
from pipeline.errors import DetectionError, LaneRoiError

ROI_SOURCE_DETECTED = "detected"
"""``roi_source`` of a lane this module located from the image's own column profile."""

ROI_SOURCE_CALLER = "caller"
"""``roi_source`` of a lane the caller supplied as a rectangle."""

ROI_SOURCES: tuple[str, ...] = (ROI_SOURCE_DETECTED, ROI_SOURCE_CALLER)
"""The closed vocabulary of lane ROI provenance, mirrored by the result schema's enum."""

LANE_ROI_FIELDS: tuple[str, ...] = ("x", "y", "width", "height")
"""The four integers a caller-supplied lane rectangle is written as, in order."""

GAUSSIAN_MAD_TO_SIGMA = 1.4826
"""Median-absolute-deviation to standard deviation, for Gaussian data: ``1/Phi^-1(0.75)``."""

DIFFERENCE_VARIANCE_FACTOR = 2.0
"""Variance multiplier introduced by taking first differences of independent samples."""

NOISE_ESTIMATOR_MIN_SAMPLES = 2
"""Samples :func:`profile_noise_sigma` needs to return anything at all.

It measures a profile through its *first differences*, and a difference needs two samples.
A property of the estimator rather than a tunable value, which is why the guard inside
:func:`profile_noise_sigma` and the lane-extent bound in :func:`minimum_lane_extent_px`
both read it here and cannot drift apart.
"""


@dataclass(frozen=True)
class Roi:
    """An integer pixel rectangle covering columns ``x..x+width-1`` and rows ``y..y+height-1``."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        """Normalise the fields to plain ``int`` and reject empty or negative rectangles.

        Coercion is deliberate: profile indices arrive as numpy integers, and a numpy
        integer in an ROI would reach ``json.dumps`` and fail there instead of here.
        Non-integral values raise rather than being rounded.
        """
        for name in ("x", "y", "width", "height"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
                raise TypeError(
                    f"ROI {name} must be an integer number of pixels, got {value!r}"
                )
            object.__setattr__(self, name, int(value))
        if self.width <= 0 or self.height <= 0:
            raise ValueError(
                f"ROI must have positive extent, got width={self.width}, height={self.height}"
            )
        if self.x < 0 or self.y < 0:
            raise ValueError(f"ROI origin must be non-negative, got x={self.x}, y={self.y}")

    def as_dict(self) -> dict[str, int]:
        """Return the ROI in the ``{"x","y","width","height"}`` shape the schema defines."""
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    def slices(self) -> tuple[slice, slice]:
        """Return ``(rows, columns)`` slices selecting this ROI from an image array."""
        return slice(self.y, self.y + self.height), slice(self.x, self.x + self.width)


@dataclass(frozen=True)
class DetectedLane:
    """One lane: its identity, its centre column, its rectangle, and where the rectangle came from.

    ``roi_source`` is one of :data:`ROI_SOURCES`. It is a required field rather than an
    optional annotation because a document that reported a caller-chosen region as a
    detector output would be exactly the kind of false record this project exists to
    prevent, and an absent value would be indistinguishable from a writer that does not
    record it.

    ``center_px`` is the column the lane is centred on: the detected peak's column for a
    detected lane, and the rectangle's own centre for a caller-supplied one.
    """

    lane_id: str
    lane_index: int
    center_px: float
    roi: Roi
    roi_source: str

    def __post_init__(self) -> None:
        """Reject a lane whose ``roi_source`` is outside the closed vocabulary."""
        if self.roi_source not in ROI_SOURCES:
            raise ValueError(
                f"lane {self.lane_id!r} has roi_source {self.roi_source!r}; it must be one "
                f"of {list(ROI_SOURCES)}, because the result schema's enum is closed and a "
                f"lane's provenance is not free text"
            )


@dataclass(frozen=True)
class RowProfile:
    """The row-profile geometry one band's ROI was walked from.

    Carried on the band so that a *shape* test can be applied to the same profile, peak and
    bounds the ROI came from, without recomputing them and without detection deciding
    anything about shape. It holds no threshold and no criterion: every number here is a
    measurement or an index.
    """

    samples: tuple[float, ...]
    peak_index: int
    baseline: float
    lower_bound: int
    upper_bound: int

    def __post_init__(self) -> None:
        """Reject a profile whose indices do not describe a peak inside its own bounds.

        Detection cannot build an invalid one, but a caller can -- Phase 4's UI corrects ROIs by
        hand -- and an out-of-range index would otherwise surface as a bare ``IndexError`` from
        the middle of a level walk. :class:`Roi` rejects an empty rectangle for the same reason.
        """
        if len(self.samples) < 2:
            raise ValueError(
                f"a row profile needs at least two samples to have a width, got "
                f"{len(self.samples)}"
            )
        if not 0 <= self.lower_bound <= self.peak_index <= self.upper_bound < len(self.samples):
            raise ValueError(
                f"row profile indices must satisfy 0 <= lower_bound <= peak_index <= "
                f"upper_bound < {len(self.samples)}, got lower_bound={self.lower_bound}, "
                f"peak_index={self.peak_index}, upper_bound={self.upper_bound}"
            )

    def half_width_ratio(self, level_fraction: float) -> float | None:
        """Return how asymmetric this peak is, measured at ``level_fraction`` of its height.

        Delegates to :func:`half_width_ratio`; the level is the caller's parameter, never a
        constant here, because it sets the sensitivity of the shape test that reads it.
        """
        return half_width_ratio(
            np.asarray(self.samples, dtype=np.float64),
            self.peak_index,
            self.baseline,
            self.lower_bound,
            self.upper_bound,
            level_fraction,
        )


@dataclass(frozen=True)
class DetectedBand:
    """One detected band, the lane it belongs to, and the row profile it was measured from.

    ``row_profile`` carries no decision: :mod:`pipeline.qc` measures the peak's asymmetry
    from it, at the level and against the threshold its own config declares, and turns that
    into the ``unresolved_shoulder`` flag.

    Nothing here splits a band: one resolved maximum is one band, one centre and one ROI
    (NOTES.md, "Doublet resolution").
    """

    band_id: str
    lane_id: str
    roi: Roi
    row_profile: RowProfile


@dataclass(frozen=True)
class Detection:
    """Everything detection produces for one image."""

    lanes: tuple[DetectedLane, ...]
    bands: tuple[DetectedBand, ...]


def _describe(roi: Roi) -> str:
    """Return a rectangle's coordinates in the form every lane-ROI message quotes them."""
    return f"x={roi.x}, y={roi.y}, width={roi.width}, height={roi.height}"


def parse_lane_roi(text: str, position: int) -> Roi:
    """Parse one caller-supplied ``x,y,w,h`` rectangle, at 1-based ``position``.

    Guarantees that the four values were written as integers and were not coerced: a float,
    a blank field, a wrong number of fields or a non-positive extent raises
    :class:`LaneRoiError` naming ``position`` and the text, rather than being rounded,
    padded or dropped. The returned :class:`Roi` is inside no particular image and under no
    particular parameter set; bounds, minimum extent and overlap are checked by
    :func:`validate_lane_rois`, which knows both the image size and the detection config.
    """
    fields = [field.strip() for field in text.split(",")]
    if len(fields) != len(LANE_ROI_FIELDS):
        raise LaneRoiError(
            f"lane ROI {position} ({text!r}) has {len(fields)} comma-separated value(s); "
            f"a lane rectangle is written as {','.join(LANE_ROI_FIELDS)}, four integers, "
            f"for example 10,0,60,240"
        )
    values: list[int] = []
    for name, field in zip(LANE_ROI_FIELDS, fields, strict=True):
        try:
            values.append(int(field))
        except ValueError as error:
            raise LaneRoiError(
                f"lane ROI {position} ({text!r}) has {name}={field!r}, which is not an "
                f"integer number of pixels; all four of {','.join(LANE_ROI_FIELDS)} are "
                f"whole pixels and none of them is rounded for you"
            ) from error
    x, y, width, height = values
    if width < 1 or height < 1:
        raise LaneRoiError(
            f"lane ROI {position} (x={x}, y={y}, width={width}, height={height}) has a "
            f"non-positive extent; width and height are pixel counts and must both be >= 1"
        )
    if x < 0 or y < 0:
        raise LaneRoiError(
            f"lane ROI {position} (x={x}, y={y}, width={width}, height={height}) starts "
            f"outside the image; x and y are pixel indices and must both be >= 0"
        )
    return Roi(x=x, y=y, width=width, height=height)


def parse_lane_rois(texts: Sequence[str]) -> tuple[Roi, ...]:
    """Parse caller-supplied ``x,y,w,h`` rectangles, in the order they were given.

    The order is the lane order: :func:`detect` numbers the supplied lanes ``L0``, ``L1``,
    ... as they arrive, so it is preserved rather than sorted.
    """
    return tuple(parse_lane_roi(text, position) for position, text in enumerate(texts, start=1))


def _overlap(first: Roi, second: Roi) -> tuple[int, int]:
    """Return the ``(width, height)`` of two rectangles' intersection, ``(0, 0)`` if disjoint."""
    width = min(first.x + first.width, second.x + second.width) - max(first.x, second.x)
    height = min(first.y + first.height, second.y + second.height) - max(first.y, second.y)
    if width <= 0 or height <= 0:
        return (0, 0)
    return (width, height)


def minimum_lane_extent_px(config: DetectionConfig) -> int:
    """Return the smallest side, in pixels, a lane rectangle can carry band detection over.

    The same bound applies to both sides, because both become a 1D profile with one sample
    per pixel: the lane's rows become the row profile bands are found in, and its columns
    become the column profile each band's width is measured from
    (:func:`_detect_bands_in_lane`). Two requirements of that profile machinery set it, and
    the bound is the larger so that both hold:

    * :func:`profile_noise_sigma` measures the profile through its first differences, so the
      profile must carry :data:`NOISE_ESTIMATOR_MIN_SAMPLES` samples for a single difference
      to exist. Below that it raises, because returning 0.0 would turn every sigma threshold
      into a no-op.
    * That estimate is reported for the *smoothed* profile: it is divided by
      ``sqrt(profile_smoothing_px)`` on the grounds that each smoothed sample is a mean of
      that many distinct samples. :func:`smooth_profile` pads the ends by repeating the edge
      sample, so in a profile shorter than the window *no* sample averages
      ``profile_smoothing_px`` distinct pixels, and the sigma every detection threshold is
      compared against would describe a profile that was never computed. At exactly
      ``profile_smoothing_px`` samples the centre sample does, which is what makes that
      length the boundary rather than one above it.

    Derived from ``config`` rather than fixed here: ``profile_smoothing_px`` is a parameter,
    so the bound moves with the parameter set that is actually running.
    """
    return max(NOISE_ESTIMATOR_MIN_SAMPLES, config.profile_smoothing_px)


def validate_lane_rois(
    rois: Sequence[Roi], width_px: int, height_px: int, config: DetectionConfig
) -> None:
    """Raise :class:`LaneRoiError` unless every supplied rectangle is usable on this image.

    Four conditions, each reported against the 1-based position the caller supplied the
    rectangle at, because that is the only handle the caller has on it:

    * at least one rectangle -- an empty supplied list is a caller who meant to suppress
      detection and then named nothing to replace it with;
    * every rectangle wholly inside the image, whose dimensions the message states;
    * every rectangle at least :func:`minimum_lane_extent_px` pixels on each side, naming
      the side at fault -- below that the lane has no profile band detection can run over,
      and the failure would otherwise surface from inside the noise estimator as a
      :class:`DetectionError` about a condition the caller cannot see;
    * no two rectangles overlapping, naming *both* positions -- overlapping lanes would
      count the shared pixels into two lanes' total-protein integrals.
    """
    if not rois:
        raise LaneRoiError(
            "no lane ROI was supplied, but supplying lane ROIs is what switches lane "
            "detection off; supply at least one rectangle or supply none at all and let "
            "detection run"
        )
    for position, roi in enumerate(rois, start=1):
        if roi.x + roi.width > width_px or roi.y + roi.height > height_px:
            raise LaneRoiError(
                f"lane ROI {position} ({_describe(roi)}) is not wholly inside the image, "
                f"which is {width_px}x{height_px} px: it reaches column "
                f"{roi.x + roi.width - 1} and row {roi.y + roi.height - 1}, and the last "
                f"addressable pixel is column {width_px - 1}, row {height_px - 1}"
            )
    minimum_px = minimum_lane_extent_px(config)
    for position, roi in enumerate(rois, start=1):
        for side, extent_px, remedy, profile in (
            ("height", roi.height, "taller", "row"),
            ("width", roi.width, "wider", "column"),
        ):
            if extent_px < minimum_px:
                raise LaneRoiError(
                    f"lane ROI {position} ({_describe(roi)}) has {side}={extent_px} px, "
                    f"below the {minimum_px} px a lane needs on each side: band detection "
                    f"reduces that side to a {profile} profile of one sample per pixel, "
                    f"which must be long enough to hold the noise estimator's first "
                    f"difference ({NOISE_ESTIMATOR_MIN_SAMPLES} samples) and to give one "
                    f"smoothed sample an average of "
                    f"detection.profile_smoothing_px={config.profile_smoothing_px} distinct "
                    f"pixels. Supply a {remedy} rectangle: the minimum is fixed by the "
                    f"config this run selected and no parameter in it can be changed for a "
                    f"single request"
                )
    for first_position, first in enumerate(rois, start=1):
        for second_position, second in enumerate(rois[first_position:], start=first_position + 1):
            overlap_width, overlap_height = _overlap(first, second)
            if overlap_width and overlap_height:
                raise LaneRoiError(
                    f"lane ROI {first_position} ({_describe(first)}) and lane ROI "
                    f"{second_position} ({_describe(second)}) overlap over "
                    f"{overlap_width}x{overlap_height} px; lanes must be disjoint, because "
                    f"pixels shared between two lanes would be counted into both lanes' "
                    f"total-protein integrals"
                )


def caller_lanes(
    rois: Sequence[Roi], width_px: int, height_px: int, config: DetectionConfig
) -> tuple[DetectedLane, ...]:
    """Return the supplied rectangles as lanes, in the order given, marked ``caller``.

    Validates them first (:func:`validate_lane_rois`), which is why ``config`` is here: one
    of the four conditions is the minimum lane extent, and that follows from
    ``detection.profile_smoothing_px``. Lane ids and indices follow the supplied order, and
    ``center_px`` is the rectangle's own centre column -- the mean of its first and last
    column index, which is the same quantity a detected lane's peak column is.
    """
    validate_lane_rois(rois, width_px, height_px, config)
    return tuple(
        DetectedLane(
            lane_id=f"L{index}",
            lane_index=index,
            center_px=roi.x + 0.5 * (roi.width - 1),
            roi=roi,
            roi_source=ROI_SOURCE_CALLER,
        )
        for index, roi in enumerate(rois)
    )


def smooth_profile(profile: np.ndarray, window_px: int) -> np.ndarray:
    """Return ``profile`` smoothed by a centred moving average of ``window_px`` samples.

    Edges are handled by edge padding, so the output has the same length as the input
    and no artificial roll-off at the borders. ``window_px`` must be odd and positive
    so the window is centred on its sample.
    """
    if window_px < 1 or window_px % 2 == 0:
        raise ValueError(
            f"smoothing window must be a positive odd number of samples, got {window_px}"
        )
    values = np.asarray(profile, dtype=np.float64)
    if window_px == 1:
        return values.copy()
    pad = window_px // 2
    padded = np.pad(values, pad, mode="edge")
    kernel = np.full(window_px, 1.0 / window_px, dtype=np.float64)
    return np.convolve(padded, kernel, mode="valid")


def profile_noise_sigma(raw_profile: np.ndarray, smoothing_px: int) -> float:
    """Return the noise standard deviation of ``raw_profile`` after smoothing, in profile units.

    Estimated from the median absolute deviation of the profile's first differences.
    Differencing removes any structure that is smooth on the scale of a sample -- the
    bands and the background -- so what is left is dominated by the pixel noise, and
    the MAD makes the estimate insensitive to the handful of large differences at a
    band's own edges. Both correction factors are properties of the estimator, not
    tunable parameters: :data:`GAUSSIAN_MAD_TO_SIGMA` converts MAD to sigma and
    :data:`DIFFERENCE_VARIANCE_FACTOR` undoes the differencing. Dividing by
    ``sqrt(smoothing_px)`` converts the raw profile's noise to the smoothed profile's,
    which is what the thresholds are compared against.
    """
    if smoothing_px < 1:
        raise ValueError(f"smoothing window must be positive, got {smoothing_px}")
    profile = np.asarray(raw_profile, dtype=np.float64)
    if profile.size < NOISE_ESTIMATOR_MIN_SAMPLES:
        raise DetectionError(
            f"noise cannot be estimated from {profile.size} sample(s): the estimator needs "
            f"at least {NOISE_ESTIMATOR_MIN_SAMPLES} for one difference. Returning 0.0 "
            f"would turn every sigma threshold into a no-op instead of reporting that the "
            f"profile is too short. A one-column *detected* lane reaches this; raise "
            f"detection.lane.min_separation_px. A supplied lane rectangle cannot: "
            f"validate_lane_rois refuses one below minimum_lane_extent_px first, and says "
            f"so in terms of the rectangle"
        )
    differences = np.diff(profile)
    deviation = float(np.median(np.abs(differences - np.median(differences))))
    raw_sigma = deviation * GAUSSIAN_MAD_TO_SIGMA / np.sqrt(DIFFERENCE_VARIANCE_FACTOR)
    return float(raw_sigma / np.sqrt(smoothing_px))


def _peak_extent(
    profile: np.ndarray,
    peak_index: int,
    threshold: float,
    lower_bound: int,
    upper_bound: int,
) -> tuple[int, int]:
    """Return the inclusive index range around ``peak_index`` that stays above ``threshold``.

    The walk stops at the first sample at or below ``threshold`` and never leaves
    ``[lower_bound, upper_bound]``, so it cannot cross into a neighbouring peak.
    """
    left = peak_index
    while left > lower_bound and profile[left - 1] > threshold:
        left -= 1
    right = peak_index
    while right < upper_bound and profile[right + 1] > threshold:
        right += 1
    return left, right


def _find_peaks(
    profile: np.ndarray, min_separation_px: int, min_prominence: float
) -> tuple[np.ndarray, dict[str, Any]]:
    """Return the peaks of ``profile`` and their properties, as :func:`scipy.signal.find_peaks`."""
    peaks, properties = find_peaks(
        profile, distance=min_separation_px, prominence=min_prominence
    )
    return peaks, properties


def _lane_edges(centers: np.ndarray, pitch_px: float, width_px: int) -> np.ndarray:
    """Return the ``len(centers)+1`` column boundaries that partition the image between lanes.

    Interior boundaries sit midway between neighbouring lane centres; the outer two sit
    half a pitch beyond the first and last centre, clamped to the image. A partition is
    used rather than a tight box around each lane's signal because the same rectangle
    has to serve the total-protein lane integral, which needs the whole lane.
    """
    interior = 0.5 * (centers[:-1] + centers[1:])
    first = max(0.0, float(centers[0]) - 0.5 * pitch_px)
    last = min(float(width_px), float(centers[-1]) + 0.5 * pitch_px)
    return np.concatenate(([first], interior, [last]))


def _image_shape(corrected: np.ndarray) -> tuple[int, int]:
    """Return ``(height_px, width_px)``, raising :class:`DetectionError` for a non-2D image."""
    if corrected.ndim != 2:
        raise DetectionError(f"lane detection needs a 2D image, got shape {corrected.shape}")
    return int(corrected.shape[0]), int(corrected.shape[1])


def detect_lanes(corrected: np.ndarray, config: DetectionConfig) -> tuple[DetectedLane, ...]:
    """Return the lanes of a background-corrected image, left to right.

    Guarantees that the returned rectangles span the full image height, are ordered by
    ``x``, do not overlap, and each contain their own measured centre column. Raises
    :class:`DetectionError` if the image carries no column-to-column variation, or if
    no lane clears ``detection.lane.min_prominence_fraction``.

    Lane width comes from one of two measurements, never from the canvas:

    * **With two or more lanes**, the pitch is the median spacing of the detected centres,
      so a gel with uneven lanes gets uneven lane rectangles.
    * **With exactly one lane** there is no spacing to take a median of, so its width is
      measured from its own column-profile extent, under the same
      ``extent_relative_height`` / ``extent_min_sigma`` rule the band extent uses
      (:func:`_extent_threshold`), with ``detection.lane``'s own configured pair. The
      rectangle is then centred on the detected centre with that measured width, so an
      off-centre single lane gets an off-centre rectangle rather than the whole canvas.
    """
    height_px, width_px = _image_shape(corrected)
    raw_profile = corrected.mean(axis=0)
    profile = smooth_profile(raw_profile, config.profile_smoothing_px)
    percentile = config.lane.robust_range_percentile
    low, high = np.percentile(profile, [percentile, 100.0 - percentile])
    span = float(high - low)
    if span <= 0.0:
        raise DetectionError(
            f"the column profile has no spread between its {percentile:g}th and "
            f"{100.0 - percentile:g}th percentiles, so no lane can be located; the image "
            f"carries no horizontal structure after background correction"
        )
    peaks, _ = _find_peaks(
        profile, config.lane.min_separation_px, config.lane.min_prominence_fraction * span
    )
    if peaks.size == 0:
        raise DetectionError(
            f"no lane peak reached a prominence of "
            f"{config.lane.min_prominence_fraction * span:.4g} "
            f"({config.lane.min_prominence_fraction:.3g} of the column profile's robust "
            f"range {span:.4g}); lower detection.lane.min_prominence_fraction or check "
            f"that the image contains lanes"
        )
    if peaks.size == 1:
        left_edge, right_edge = _measure_extent(
            profile,
            int(peaks[0]),
            float(low),
            profile_noise_sigma(raw_profile, config.profile_smoothing_px),
            config.lane.extent_relative_height,
            config.lane.extent_min_sigma,
        )
        pitch_px = float(right_edge - left_edge + 1)
    else:
        pitch_px = float(np.median(np.diff(peaks)))
    edges = _lane_edges(peaks.astype(np.float64), pitch_px, width_px)

    lanes: list[DetectedLane] = []
    for index in range(peaks.size):
        left = int(round(edges[index]))
        right = int(round(edges[index + 1])) - 1
        left = max(0, min(left, width_px - 1))
        right = max(0, min(right, width_px - 1))
        if right < left:
            raise DetectionError(
                f"lane {index} collapsed to an empty rectangle (columns {left}..{right}); "
                f"raise detection.lane.min_separation_px so lanes are not detected on top "
                f"of one another"
            )
        lanes.append(
            DetectedLane(
                lane_id=f"L{index}",
                lane_index=index,
                center_px=float(peaks[index]),
                roi=Roi(x=left, y=0, width=right - left + 1, height=int(height_px)),
                roi_source=ROI_SOURCE_DETECTED,
            )
        )
    return tuple(lanes)


def _extent_threshold(
    peak_height: float, noise_sigma: float, relative_height: float, min_sigma: float
) -> float:
    """Return how far above the baseline an extent edge sits, for a peak of ``peak_height``.

    The larger of a fixed fraction of the peak's height and a fixed multiple of the
    profile's noise: the fraction sets how much of the peak's signal the extent encloses,
    and the noise floor stops the edge from walking off into noise when the fraction
    falls below it.

    Both the band extent and the single-lane width call this with their own configured
    pair, so the two are the same rule rather than two rules that resemble each other.
    """
    return max(relative_height * peak_height, min_sigma * noise_sigma)


def _level_crossing(
    profile: np.ndarray, peak: int, level: float, step: int, bound: int
) -> float | None:
    """Return the sub-sample offset from ``peak`` at which ``profile`` falls to ``level``.

    Walks in direction ``step`` no further than ``bound`` and interpolates linearly between
    the last sample above ``level`` and the first at or below it. Returns ``None`` when the
    profile has not reached ``level`` by ``bound``: the width is then censored by a
    neighbouring peak or by the lane's edge, and inventing the crossing beyond the bound
    would report a width that was never measured.

    The caller guarantees ``profile[peak] > level`` (:func:`half_width_ratio` requires a
    positive height and a level fraction below one), and the walk only advances while the
    next sample is above ``level``, so at the interpolation ``above > level >= below``
    strictly and the denominator below cannot be zero.
    """
    index = peak
    while index != bound and profile[index + step] > level:
        index += step
    if index == bound:
        # Every sample between the peak and the bound is above the level, so the crossing
        # lies outside the range this peak may be measured over. Nothing beyond the bound is
        # read: it belongs to a neighbouring peak, or is off the end of the profile.
        return None
    above = float(profile[index])
    below = float(profile[index + step])
    return abs(index - peak) + (above - level) / (above - below)


def half_width_ratio(
    profile: np.ndarray,
    peak: int,
    baseline: float,
    lower_bound: int,
    upper_bound: int,
    level_fraction: float,
) -> float | None:
    """Return how asymmetric one peak of ``profile`` is, as a ratio of its two half-widths.

    ``level_fraction`` is the height, as a fraction of the peak's height above ``baseline``,
    at which both widths are measured; it must lie strictly between 0 and 1 and is the
    caller's parameter, because it sets how far down the peak's flanks the comparison is made
    and therefore how sensitive the statistic is to a shoulder.

    Guarantees a value ``>= 1.0``: the wider half-width divided by the narrower one, so a
    symmetric peak measures 1.0 whichever side is wider, and the statistic is scale-free --
    independent of the peak's height, of the profile's units and of the band's size. A single
    band's profile is symmetric about its own centre (diffusion about a migration position
    has no preferred direction), so a ratio materially above 1.0 means the profile carries
    structure a single band does not explain.

    ``None`` when the statistic is undefined or censored: a non-positive peak height, or a
    profile that does not reach the level on both sides within
    ``[lower_bound, upper_bound]``. The caller must not read ``None`` as "symmetric".
    """
    if not 0.0 < level_fraction < 1.0:
        raise ValueError(
            f"level_fraction must be in (0, 1), got {level_fraction}; at 0 the width is the "
            f"whole peak and at 1 it is zero, so neither bounds a half-width"
        )
    height = float(profile[peak]) - baseline
    if height <= 0.0:
        return None
    level = baseline + level_fraction * height
    left = _level_crossing(profile, peak, level, -1, lower_bound)
    right = _level_crossing(profile, peak, level, +1, upper_bound)
    if left is None or right is None or left <= 0.0 or right <= 0.0:
        return None
    return max(left, right) / min(left, right)


def _measure_extent(
    profile: np.ndarray,
    peak: int,
    baseline: float,
    noise_sigma: float,
    relative_height: float,
    min_sigma: float,
) -> tuple[int, int]:
    """Return the inclusive index range a peak occupies under the shared extent rule."""
    height = float(profile[peak]) - baseline
    threshold = baseline + _extent_threshold(height, noise_sigma, relative_height, min_sigma)
    return _peak_extent(profile, peak, threshold, 0, profile.size - 1)


def _detect_bands_in_lane(
    corrected: np.ndarray, lane: DetectedLane, config: DetectionConfig
) -> list[DetectedBand]:
    """Return the bands of one lane, top to bottom, as :class:`DetectedBand` objects.

    A peak that cleared both prominence criteria still yields no band in one case: if
    the lane's columns carry no spread at all over the peak's rows, there is no
    horizontal extent to measure and no ROI to report. That needs the lane to be exactly
    constant across its whole width there, which real pixels never are; it is a
    documented guard rather than a routine outcome, and ``tests/test_pipeline_detect.py``
    constructs the case.
    """
    rows, columns = lane.roi.slices()
    lane_pixels = corrected[rows, columns]
    raw_row_profile = lane_pixels.mean(axis=1)
    row_profile = smooth_profile(raw_row_profile, config.profile_smoothing_px)
    row_sigma = profile_noise_sigma(raw_row_profile, config.profile_smoothing_px)
    baseline = float(np.percentile(row_profile, config.band.baseline_percentile))
    span = float(row_profile.max()) - baseline
    if span <= 0.0:
        return []
    peaks, properties = _find_peaks(
        row_profile,
        config.band.min_separation_px,
        max(
            config.band.min_prominence_fraction * span,
            config.band.min_prominence_sigma * row_sigma,
        ),
    )
    left_bases = properties["left_bases"].astype(int)
    right_bases = properties["right_bases"].astype(int)

    bands: list[DetectedBand] = []
    for order, peak in enumerate(peaks.astype(int)):
        height = float(row_profile[peak]) - baseline
        lower = max(int(left_bases[order]), int(peaks[order - 1]) + 1 if order > 0 else 0)
        upper = min(
            int(right_bases[order]),
            int(peaks[order + 1]) - 1 if order + 1 < peaks.size else row_profile.size - 1,
        )
        top, bottom = _peak_extent(
            row_profile,
            peak,
            baseline
            + _extent_threshold(
                height,
                row_sigma,
                config.band.extent_relative_height,
                config.band.extent_min_sigma,
            ),
            lower,
            upper,
        )
        raw_column_profile = lane_pixels[top : bottom + 1, :].mean(axis=0)
        column_profile = smooth_profile(raw_column_profile, config.profile_smoothing_px)
        column_sigma = profile_noise_sigma(raw_column_profile, config.profile_smoothing_px)
        column_baseline = float(np.percentile(column_profile, config.band.baseline_percentile))
        column_peak = int(np.argmax(column_profile))
        column_height = float(column_profile[column_peak]) - column_baseline
        if column_height <= 0.0:
            continue
        left, right = _peak_extent(
            column_profile,
            column_peak,
            column_baseline
            + _extent_threshold(
                column_height,
                column_sigma,
                config.band.extent_relative_height,
                config.band.extent_min_sigma,
            ),
            0,
            column_profile.size - 1,
        )
        bands.append(
            DetectedBand(
                band_id=f"{lane.lane_id}_B{len(bands)}",
                lane_id=lane.lane_id,
                # The same profile, peak and bounds the ROI was walked from, so a shape test
                # reading this describes the band's own peak and cannot stray into a
                # neighbour's. Measuring and thresholding it is pipeline.qc's job, at
                # pipeline.qc's configured level.
                row_profile=RowProfile(
                    samples=tuple(float(sample) for sample in row_profile),
                    peak_index=int(peak),
                    baseline=baseline,
                    lower_bound=int(lower),
                    upper_bound=int(upper),
                ),
                # top/bottom and left/right index the lane slice, so both are offset by
                # the lane's own origin rather than relying on it being (0, 0).
                roi=Roi(
                    x=lane.roi.x + left,
                    y=lane.roi.y + top,
                    width=right - left + 1,
                    height=bottom - top + 1,
                ),
            )
        )
    return bands


def detect(
    corrected: np.ndarray, config: DetectionConfig, lane_rois: Sequence[Roi] | None = None
) -> Detection:
    """Detect lanes and then bands within each lane.

    Guarantees that every returned band's ROI lies inside its lane's ROI, that band ids
    are unique, and that bands are ordered by lane and then top to bottom. Raises
    :class:`DetectionError` when no lane can be located; a lane with no band above
    threshold simply contributes no bands.

    When ``lane_rois`` is given, :func:`detect_lanes` **does not run**: the supplied
    rectangles are the lanes, in the order given, and they carry
    ``roi_source="caller"``. Band detection inside them is unchanged -- the same code, the
    same configured parameters -- so a caller correcting a lane boundary changes which
    pixels are searched and nothing else. Raises :class:`LaneRoiError` for a rectangle this
    image cannot carry, or one too small for ``config``'s profile machinery to run over
    (:func:`validate_lane_rois`).
    """
    if lane_rois is None:
        lanes = detect_lanes(corrected, config)
    else:
        height_px, width_px = _image_shape(corrected)
        lanes = caller_lanes(
            lane_rois, width_px=width_px, height_px=height_px, config=config
        )
    bands: list[DetectedBand] = []
    for lane in lanes:
        bands.extend(_detect_bands_in_lane(corrected, lane, config))
    return Detection(lanes=lanes, bands=tuple(bands))
