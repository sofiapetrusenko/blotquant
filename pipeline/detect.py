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

Nothing here assumes how many lanes or bands exist, that lanes are evenly spaced, that
bands are the same size, that band rows are shared between lanes, or any particular
peak shape. Those would be assumptions about the synthetic generator (``synth/MODELS.md``,
"What counts as special-casing") rather than about gel-doc images.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.signal import find_peaks

from pipeline.config import DetectionConfig
from pipeline.errors import DetectionError

GAUSSIAN_MAD_TO_SIGMA = 1.4826
"""Median-absolute-deviation to standard deviation, for Gaussian data: ``1/Phi^-1(0.75)``."""

DIFFERENCE_VARIANCE_FACTOR = 2.0
"""Variance multiplier introduced by taking first differences of independent samples."""


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
    """One detected lane: its identity, its measured centre column, and its rectangle."""

    lane_id: str
    lane_index: int
    center_px: float
    roi: Roi


@dataclass(frozen=True)
class DetectedBand:
    """One detected band and the lane it belongs to."""

    band_id: str
    lane_id: str
    roi: Roi


@dataclass(frozen=True)
class Detection:
    """Everything detection produces for one image."""

    lanes: tuple[DetectedLane, ...]
    bands: tuple[DetectedBand, ...]


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
    if profile.size < 2:
        raise DetectionError(
            f"noise cannot be estimated from {profile.size} sample(s): the estimator needs "
            f"at least one difference. Returning 0.0 would turn every sigma threshold into "
            f"a no-op instead of reporting that the profile is too short. A one-column lane "
            f"reaches this; raise detection.lane.min_separation_px"
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
    if corrected.ndim != 2:
        raise DetectionError(f"lane detection needs a 2D image, got shape {corrected.shape}")
    height_px, width_px = corrected.shape
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


def detect(corrected: np.ndarray, config: DetectionConfig) -> Detection:
    """Detect lanes and then bands within each lane.

    Guarantees that every returned band's ROI lies inside its lane's ROI, that band ids
    are unique, and that bands are ordered by lane and then top to bottom. Raises
    :class:`DetectionError` when no lane can be located; a lane with no band above
    threshold simply contributes no bands.
    """
    lanes = detect_lanes(corrected, config)
    bands: list[DetectedBand] = []
    for lane in lanes:
        bands.extend(_detect_bands_in_lane(corrected, lane, config))
    return Detection(lanes=lanes, bands=tuple(bands))
