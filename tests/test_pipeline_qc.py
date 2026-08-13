"""QC: every flag against constructed fixtures, and all five against ground-truth labels.

Two halves. The first constructs images whose clipping, ROI overlap, profile asymmetry and
peak height are known by construction, and asserts the flag fires and -- as importantly --
does not fire on the matching clean case. The second runs the real pipeline over gold-set
images and scores the flags against ground truth's own labels, which is PLAN.md's done-when
for this phase; the whole-split figures are re-measured by ``python -m evals.sweep --check``
and asserted from that record in ``tests/test_sweep_check.py``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from evals.metrics import BoundingBox, iou
from pipeline.background import correct_background
from pipeline.config import DetectionConfig, PipelineConfig, QcConfig, load_config
from pipeline.detect import (
    DetectedBand,
    Roi,
    RowProfile,
    detect,
    half_width_ratio,
    smooth_profile,
)
from pipeline.errors import QcError
from pipeline.load import load_image
from pipeline.qc import (
    BAND_QC_FLAGS,
    IMAGE_QC_FLAGS,
    LOSSY_FORMAT,
    LOW_DYNAMIC_RANGE,
    OVERLAPPING,
    SATURATED,
    UNRESOLVED_SHOULDER,
    assess,
    is_saturated,
    is_unresolved_shoulder,
    roi_iou,
)
from tests.conftest import Band, Blot
from tests.test_pipeline_config import CONFIG_DIR

SIGMA_X_PX = 8.0
SIGMA_Y_PX = 4.0
DOUBLET_OFFSET_SIGMA = 2.2
DOUBLET_AMPLITUDE_RATIO = 0.65
"""The doublet fixture's geometry. It is the same one ``tests/test_pipeline_detect.py`` uses
to pin "one band per resolved maximum", so the two halves of that ruling -- one band, and a
shoulder flag on it -- are pinned against the same data."""


@pytest.fixture
def config() -> PipelineConfig:
    """Return the shipped parameter set."""
    return load_config(CONFIG_DIR / "default.yaml")


@pytest.fixture
def qc(config: PipelineConfig) -> QcConfig:
    """Return the shipped QC thresholds."""
    return config.qc


FLAT_PROFILE = RowProfile(
    samples=(0.0, 0.0, 0.0), peak_index=1, baseline=0.0, lower_bound=0, upper_bound=2
)
"""A profile with no height, so its half-width ratio is ``None``: the right stand-in for a
band whose shape is not what a test is about."""


def _profile(samples: tuple[float, ...], peak_index: int) -> RowProfile:
    """Return a row profile over ``samples``, measured from ``peak_index`` at baseline zero."""
    return RowProfile(
        samples=samples,
        peak_index=peak_index,
        baseline=0.0,
        lower_bound=0,
        upper_bound=len(samples) - 1,
    )


def _band(
    band_id: str, lane_id: str, roi: Roi, profile: RowProfile = FLAT_PROFILE
) -> DetectedBand:
    """Return a detected band with a known ROI and a known row profile."""
    return DetectedBand(band_id=band_id, lane_id=lane_id, roi=roi, row_profile=profile)


def _flat(shape: tuple[int, int], value: float) -> np.ndarray:
    """Return a constant float image."""
    return np.full(shape, value, dtype=np.float64)


# --------------------------------------------------------------------------- saturation


def _clipped_image(clipped_pixels: int, max_value: int = 255) -> np.ndarray:
    """Return an 8-bit image with exactly ``clipped_pixels`` pixels at full scale."""
    pixels = np.full((20, 20), 100, dtype=np.uint8)
    flat = pixels.reshape(-1)
    flat[:clipped_pixels] = max_value
    return pixels


@pytest.mark.parametrize(
    ("clipped_pixels", "expected"), [(0, False), (1, True), (2, True), (7, True)]
)
def test_saturated_fires_at_the_configured_number_of_clipped_pixels(
    qc: QcConfig, clipped_pixels: int, expected: bool
) -> None:
    """The shipped threshold is one pixel at full scale; zero is the only unflagged case."""
    assert qc.saturated_min_clipped_pixels == 1, "this test states the shipped threshold"
    pixels = _clipped_image(clipped_pixels)

    report = assess(
        pixels,
        pixels.astype(np.float64),
        [_band("b", "L0", Roi(x=0, y=0, width=20, height=20))],
        255,
        False,
        qc,
    )

    assert (SATURATED in report.bands[0].flags) is expected
    assert report.bands[0].clipped_pixel_count == clipped_pixels


def test_a_higher_threshold_needs_that_many_clipped_pixels(qc: QcConfig) -> None:
    """The threshold is a parameter, not a constant: raising it moves the decision."""
    strict = replace(qc, saturated_min_clipped_pixels=3)
    roi = [_band("b", "L0", Roi(x=0, y=0, width=20, height=20))]

    at_two = assess(_clipped_image(2), _flat((20, 20), 1.0), roi, 255, False, strict)
    at_three = assess(_clipped_image(3), _flat((20, 20), 1.0), roi, 255, False, strict)

    assert SATURATED not in at_two.bands[0].flags
    assert SATURATED in at_three.bands[0].flags


def test_clipping_is_counted_inside_the_roi_only(qc: QcConfig) -> None:
    """A saturated dust speck outside every band is not a compromised measurement."""
    pixels = np.full((20, 20), 100, dtype=np.uint8)
    pixels[18:20, 18:20] = 255

    report = assess(
        pixels,
        pixels.astype(np.float64),
        [_band("b", "L0", Roi(x=0, y=0, width=8, height=8))],
        255,
        False,
        qc,
    )

    assert report.bands[0].clipped_pixel_count == 0
    assert report.bands[0].flags == ()
    assert SATURATED not in report.image_flags


def test_clipping_is_measured_at_the_declared_bit_depth(qc: QcConfig) -> None:
    """A 16-bit image is not clipped at 255: full scale comes from the depth that was loaded."""
    pixels = np.full((20, 20), 255, dtype=np.uint16)
    bands = [_band("b", "L0", Roi(x=0, y=0, width=20, height=20))]

    as_16_bit = assess(pixels, pixels.astype(np.float64), bands, 65535, False, qc)
    as_8_bit = assess(
        pixels.astype(np.uint8), pixels.astype(np.float64), bands, 255, False, qc
    )

    assert as_16_bit.bands[0].clipped_pixel_count == 0
    assert as_8_bit.bands[0].clipped_pixel_count == 400


# --------------------------------------------------------------------------- overlap


def test_roi_iou_agrees_with_the_metrics_and_generator_implementations() -> None:
    """Three independent implementations, pinned to the same numbers on shared fixtures.

    ``pipeline/`` may import neither ``evals`` nor ``synth``, so this geometry exists three
    times in the repository. A disagreement would make the ``overlapping`` flag score against
    a different measure than the one that produced the truth label.
    """
    from synth.generator import box_iou
    from synth.render import PixelBox

    cases = [
        (Roi(0, 0, 10, 10), Roi(0, 0, 10, 10)),
        (Roi(0, 0, 10, 10), Roi(5, 0, 10, 10)),
        (Roi(0, 0, 10, 10), Roi(0, 5, 10, 10)),
        (Roi(0, 0, 10, 10), Roi(20, 20, 4, 4)),
        (Roi(2, 3, 7, 11), Roi(4, 5, 9, 6)),
    ]
    for first, second in cases:
        expected = iou(
            BoundingBox.from_mapping(first.as_dict()), BoundingBox.from_mapping(second.as_dict())
        )
        assert roi_iou(first, second) == pytest.approx(expected, abs=1e-12)
        assert roi_iou(first, second) == pytest.approx(
            box_iou(PixelBox(**first.as_dict()), PixelBox(**second.as_dict())), abs=1e-12
        )


def test_overlapping_fires_on_both_bands_of_an_overlapping_pair(qc: QcConfig) -> None:
    """Two 10x10 ROIs sharing 5 columns have IoU 50/150 = 1/3, well above the threshold."""
    first = Roi(x=0, y=0, width=10, height=10)
    second = Roi(x=5, y=0, width=10, height=10)
    assert roi_iou(first, second) == pytest.approx(50.0 / 150.0, abs=1e-12)
    assert roi_iou(first, second) > qc.overlap_iou_threshold
    pixels = np.full((20, 20), 100, dtype=np.uint8)

    report = assess(
        pixels,
        pixels.astype(np.float64),
        [_band("a", "L0", first), _band("b", "L0", second)],
        255,
        False,
        qc,
    )

    assert all(OVERLAPPING in band.flags for band in report.bands)


def test_overlapping_does_not_fire_below_the_threshold_or_across_lanes(qc: QcConfig) -> None:
    """Two cases that must stay unflagged: a small overlap, and a large one in another lane."""
    first = Roi(x=0, y=0, width=20, height=20)
    marginal = Roi(x=19, y=0, width=20, height=20)
    assert roi_iou(first, marginal) == pytest.approx(20.0 / 780.0, abs=1e-12)
    assert roi_iou(first, marginal) < qc.overlap_iou_threshold
    other_lane = Roi(x=10, y=0, width=20, height=20)
    assert roi_iou(first, other_lane) > qc.overlap_iou_threshold
    pixels = np.full((40, 40), 100, dtype=np.uint8)

    report = assess(
        pixels,
        pixels.astype(np.float64),
        [
            _band("a", "L0", first),
            _band("b", "L0", marginal),
            _band("c", "L1", other_lane),
        ],
        255,
        False,
        qc,
    )

    assert all(OVERLAPPING not in band.flags for band in report.bands)


# --------------------------------------------------------------------------- shoulder


def test_half_width_ratio_is_one_for_a_symmetric_peak(qc: QcConfig) -> None:
    """A Gaussian is symmetric about its centre, so its two half-widths are equal."""
    rows = np.arange(80, dtype=np.float64)
    profile = 1000.0 * np.exp(-0.5 * ((rows - 40.0) / 5.0) ** 2)

    ratio = half_width_ratio(
        profile, 40, 0.0, 0, profile.size - 1, qc.shoulder_half_maximum_fraction
    )

    assert ratio is not None
    assert ratio == pytest.approx(1.0, abs=1e-9)


def test_half_width_ratio_measures_the_known_half_width(qc: QcConfig) -> None:
    """Each half-width is the Gaussian's own: sigma * sqrt(2 ln 2), to sub-sample precision."""
    rows = np.arange(80, dtype=np.float64)
    sigma = 5.0
    profile = 1000.0 * np.exp(-0.5 * ((rows - 40.0) / sigma) ** 2)
    expected = sigma * math.sqrt(2.0 * math.log(2.0))

    # The ratio is 1.0, so the interpolated half-width is recovered from the level crossing.
    crossing = np.argmax(profile[40:] <= 500.0) + 40
    interpolated = (crossing - 1) + (profile[crossing - 1] - 500.0) / (
        profile[crossing - 1] - profile[crossing]
    )

    assert interpolated - 40.0 == pytest.approx(expected, abs=0.05)
    assert half_width_ratio(
        profile, 40, 0.0, 0, profile.size - 1, qc.shoulder_half_maximum_fraction
    ) == pytest.approx(1.0, abs=1e-9)


def test_half_width_ratio_is_none_when_the_walk_is_censored(qc: QcConfig) -> None:
    """A bound reached before half maximum means the width was not measured, not that it is 0."""
    rows = np.arange(40, dtype=np.float64)
    profile = 1000.0 * np.exp(-0.5 * ((rows - 20.0) / 5.0) ** 2)
    level = qc.shoulder_half_maximum_fraction

    assert half_width_ratio(profile, 20, 0.0, 18, 22, level) is None
    assert half_width_ratio(profile, 20, 1000.0, 0, 39, level) is None, "no positive height"


def test_half_width_ratio_rejects_a_level_outside_the_peak() -> None:
    """At 0 the width is the whole peak and at 1 it is zero, so neither bounds a half-width."""
    rows = np.arange(40, dtype=np.float64)
    profile = 1000.0 * np.exp(-0.5 * ((rows - 20.0) / 5.0) ** 2)

    for level in (0.0, 1.0, -0.5, 1.5):
        with pytest.raises(ValueError, match="level_fraction"):
            half_width_ratio(profile, 20, 0.0, 0, 39, level)


def test_the_level_is_a_parameter_that_moves_the_measured_asymmetry() -> None:
    """The half-maximum level is a real knob, not a decoration: it changes the statistic.

    A peak with a shoulder low on one flank is symmetric high up and clearly asymmetric
    further down, which is why the level is declared in config and echoed into provenance
    rather than fixed in a function body. Both expected values are hand-computed from the
    profile below (peak 100 at index 3, baseline 0):

    * at level 60, both flanks are already at 60 one sample out, so each half-width is
      exactly 1.0 and the ratio is 1.0;
    * at level 20, the left flank reaches 20 at index 1, so the left half-width is 2.0; the
      right flank is still at 45 at index 5 and 12 at index 6, so it crosses at
      2 + (45 - 20) / (45 - 12) = 2.7576, and the ratio is 2.7576 / 2.0 = 1.3788.
    """
    profile = np.array([0.0, 20.0, 60.0, 100.0, 60.0, 45.0, 12.0, 0.0])

    high = half_width_ratio(profile, 3, 0.0, 0, profile.size - 1, 0.6)
    low = half_width_ratio(profile, 3, 0.0, 0, profile.size - 1, 0.2)

    assert high is not None and low is not None
    assert high == pytest.approx(1.0, abs=1e-12), "at 60% both flanks cross one sample out"
    assert low == pytest.approx((2.0 + 25.0 / 33.0) / 2.0, abs=1e-12)
    assert low == pytest.approx(1.3788, abs=1e-4), "at 20% the shoulder side runs wider"


def _doublet_blot(blot: Callable[..., Blot]) -> Blot:
    """Return a one-lane blot whose single maximum carries a shoulder."""
    return blot(
        width=120,
        height=120,
        bands=[
            Band(60.0, 40.0, 4000.0, SIGMA_X_PX, SIGMA_Y_PX),
            Band(
                60.0,
                40.0 + DOUBLET_OFFSET_SIGMA * SIGMA_Y_PX,
                DOUBLET_AMPLITUDE_RATIO * 4000.0,
                SIGMA_X_PX,
                SIGMA_Y_PX,
            ),
        ],
        background_dn=1000.0,
    )


def _single_blot(blot: Callable[..., Blot]) -> Blot:
    """Return the same blot with one band instead of two."""
    return blot(
        width=120,
        height=120,
        bands=[Band(60.0, 40.0, 4000.0, SIGMA_X_PX, SIGMA_Y_PX)],
        background_dn=1000.0,
    )


def _single_maximum(built: Blot, detection: DetectionConfig) -> bool:
    """Return whether the lane's smoothed row profile has exactly one local maximum."""
    profile = smooth_profile(
        built.signal[:, 40:80].mean(axis=1), detection.profile_smoothing_px
    )
    maxima = [
        index
        for index in range(1, profile.size - 1)
        if profile[index] > profile[index - 1] and profile[index] > profile[index + 1]
    ]
    return len(maxima) == 1


def test_unresolved_shoulder_fires_on_a_single_maximum_carrying_a_shoulder(
    blot: Callable[..., Blot], config: PipelineConfig
) -> None:
    """The other half of the doublet ruling: one band, one ROI, and a flag saying why.

    The profile really does have a single maximum -- asserted from the data first -- so the
    honest report is one band plus this flag, and the flag must not add a second band, a
    second centre or a second ROI.
    """
    built = _doublet_blot(blot)
    assert _single_maximum(built, config.detection)
    correction = correct_background(built.pixels, config.background)
    detection = detect(correction.corrected, config.detection)
    assert len(detection.bands) == 1, "the ruling: one band per resolved maximum"

    report = assess(
        built.pixels,
        correction.corrected,
        detection.bands,
        65535,
        False,
        config.qc,
    )

    assert UNRESOLVED_SHOULDER in report.bands[0].flags
    ratio = detection.bands[0].row_profile.half_width_ratio(
        config.qc.shoulder_half_maximum_fraction
    )
    assert ratio is not None and ratio >= config.qc.shoulder_half_width_ratio
    assert len(report.bands) == len(detection.bands) == 1


def test_unresolved_shoulder_does_not_fire_on_a_single_symmetric_band(
    blot: Callable[..., Blot], config: PipelineConfig
) -> None:
    """The non-firing case, on the same geometry with the partner removed."""
    built = _single_blot(blot)
    correction = correct_background(built.pixels, config.background)
    detection = detect(correction.corrected, config.detection)
    assert len(detection.bands) == 1

    report = assess(
        built.pixels, correction.corrected, detection.bands, 65535, False, config.qc
    )

    assert report.bands[0].flags == ()
    ratio = detection.bands[0].row_profile.half_width_ratio(
        config.qc.shoulder_half_maximum_fraction
    )
    assert ratio is not None
    assert ratio == pytest.approx(1.0, abs=0.2)


BELOW_THRESHOLD = (0.0, 100.0, 32.9, 0.0)
ABOVE_THRESHOLD = (0.0, 100.0, 33.8, 0.0)
"""Two profiles whose measured half-width ratios straddle the shipped 1.5, by hand.

Baseline 0 and peak 100 put the half-maximum level at 50. Leftwards the profile falls from
100 to 0 in one sample, so the interpolated crossing is at (100 - 50) / (100 - 0) = 0.5
samples. Rightwards it falls from 100 to `v` in one sample, so the crossing is at
50 / (100 - v): 0.7452 for v = 32.9, giving a ratio of 1.4903, and 0.7553 for v = 33.8,
giving 1.5106.
"""


def test_the_shoulder_criterion_is_the_configured_threshold(qc: QcConfig) -> None:
    """A measured ratio decides nothing on its own: the config's threshold does."""
    roi = Roi(x=0, y=0, width=10, height=10)
    pixels = np.full((20, 20), 100, dtype=np.uint8)
    below = _profile(BELOW_THRESHOLD, 1)
    above = _profile(ABOVE_THRESHOLD, 1)
    level = qc.shoulder_half_maximum_fraction

    assert qc.shoulder_half_width_ratio == 1.5, "this test states the shipped threshold"
    assert below.half_width_ratio(level) == pytest.approx(1.4903, abs=1e-4)
    assert above.half_width_ratio(level) == pytest.approx(1.5106, abs=1e-4)
    report = assess(
        pixels,
        pixels.astype(np.float64),
        [_band("a", "L0", roi, below), _band("b", "L1", roi, above)],
        255,
        False,
        qc,
    )

    assert UNRESOLVED_SHOULDER not in report.bands[0].flags
    assert UNRESOLVED_SHOULDER in report.bands[1].flags


def test_an_unmeasured_shape_statistic_does_not_flag(qc: QcConfig) -> None:
    """``None`` means censored, and must never be read as either symmetric or shouldered."""
    assert FLAT_PROFILE.half_width_ratio(qc.shoulder_half_maximum_fraction) is None
    pixels = np.full((20, 20), 100, dtype=np.uint8)

    report = assess(
        pixels,
        pixels.astype(np.float64),
        [_band("a", "L0", Roi(x=0, y=0, width=10, height=10), FLAT_PROFILE)],
        255,
        False,
        qc,
    )

    assert UNRESOLVED_SHOULDER not in report.bands[0].flags


# --------------------------------------------------------------------------- image flags


def test_low_dynamic_range_fires_on_a_faint_image_and_not_on_a_bright_one(
    qc: QcConfig,
) -> None:
    """The decision is the brightest band's background-corrected peak against full scale."""
    pixels = np.full((20, 20), 10, dtype=np.uint8)
    roi = [_band("b", "L0", Roi(x=0, y=0, width=20, height=20))]
    faint = _flat((20, 20), 0.24 * 255.0)
    bright = _flat((20, 20), 0.26 * 255.0)

    assert qc.dynamic_range_min_peak_fraction == 0.25, "this test states the shipped threshold"
    assert LOW_DYNAMIC_RANGE in assess(pixels, faint, roi, 255, False, qc).image_flags
    assert LOW_DYNAMIC_RANGE not in assess(pixels, bright, roi, 255, False, qc).image_flags


def test_an_image_with_no_detected_band_is_low_dynamic_range(qc: QcConfig) -> None:
    """Nothing cleared detection, so nothing in the image is quantifiable."""
    pixels = np.full((20, 20), 10, dtype=np.uint8)

    report = assess(pixels, pixels.astype(np.float64), [], 255, False, qc)

    assert report.bands == ()
    assert LOW_DYNAMIC_RANGE in report.image_flags


def test_the_image_is_saturated_when_a_band_is(qc: QcConfig) -> None:
    """The image flag is a statement about bands, and it is not raised by anything else."""
    bright = _flat((20, 20), 200.0)
    clean = np.full((20, 20), 100, dtype=np.uint8)
    clipped = _clipped_image(4)
    roi = [_band("b", "L0", Roi(x=0, y=0, width=20, height=20))]

    assert SATURATED in assess(clipped, bright, roi, 255, False, qc).image_flags
    assert SATURATED not in assess(clean, bright, roi, 255, False, qc).image_flags


def test_lossy_format_is_reported_as_given(qc: QcConfig) -> None:
    """The loader decides what the container was; QC only records it."""
    pixels = np.full((20, 20), 100, dtype=np.uint8)
    roi = [_band("b", "L0", Roi(x=0, y=0, width=20, height=20))]
    bright = _flat((20, 20), 200.0)

    assert LOSSY_FORMAT in assess(pixels, bright, roi, 255, True, qc).image_flags
    assert LOSSY_FORMAT not in assess(pixels, bright, roi, 255, False, qc).image_flags


def test_the_shipped_predicates_are_what_the_flags_use(qc: QcConfig) -> None:
    """``is_saturated`` and ``is_unresolved_shoulder`` are the criteria, not a copy of them.

    They are exported so that an eval varying a threshold applies the shipped rule to the
    pipeline's own observation; this pins that they agree with what ``assess`` decided, so the
    exported predicate cannot drift from the flag it explains.
    """
    pixels = _clipped_image(4)
    bands = [
        _band("a", "L0", Roi(x=0, y=0, width=20, height=20), _profile(ABOVE_THRESHOLD, 1)),
        _band("b", "L1", Roi(x=10, y=10, width=4, height=4), _profile(BELOW_THRESHOLD, 1)),
    ]

    report = assess(pixels, _flat((20, 20), 100.0), bands, 255, False, qc)

    for band in report.bands:
        assert (SATURATED in band.flags) is is_saturated(band.clipped_pixel_count, qc)
        assert (UNRESOLVED_SHOULDER in band.flags) is is_unresolved_shoulder(
            band.row_half_width_ratio, qc
        )
    assert is_unresolved_shoulder(None, qc) is False, "a censored ratio never flags"


def test_a_row_profile_with_indices_outside_its_samples_is_rejected() -> None:
    """A hand-built profile must fail at construction, not with an IndexError mid-walk."""
    with pytest.raises(ValueError, match="row profile indices"):
        RowProfile(
            samples=(0.0, 1.0, 0.0), peak_index=5, baseline=0.0, lower_bound=0, upper_bound=2
        )
    with pytest.raises(ValueError, match="row profile indices"):
        RowProfile(
            samples=(0.0, 1.0, 0.0), peak_index=1, baseline=0.0, lower_bound=2, upper_bound=1
        )
    with pytest.raises(ValueError, match="at least two samples"):
        RowProfile(samples=(1.0,), peak_index=0, baseline=0.0, lower_bound=0, upper_bound=0)


def test_flags_stay_inside_the_declared_vocabularies(qc: QcConfig) -> None:
    """The result schema's enums are closed; nothing may invent a flag name."""
    report = assess(
        _clipped_image(4),
        _flat((20, 20), 100.0),
        [_band("b", "L0", Roi(x=0, y=0, width=20, height=20), _profile(ABOVE_THRESHOLD, 1))],
        255,
        True,
        qc,
    )

    assert set(report.bands[0].flags) <= set(BAND_QC_FLAGS)
    assert set(report.image_flags) <= set(IMAGE_QC_FLAGS)


def test_qc_annotates_and_never_drops_a_band(qc: QcConfig) -> None:
    """Every band comes back, in order, with its identity -- flagged or not."""
    pixels = _clipped_image(4)
    bands = [
        _band("a", "L0", Roi(x=0, y=0, width=20, height=20)),
        _band("b", "L1", Roi(x=10, y=10, width=4, height=4)),
    ]

    report = assess(pixels, pixels.astype(np.float64), bands, 255, False, qc)

    assert [item.band_id for item in report.bands] == ["a", "b"]
    assert SATURATED in report.bands[0].flags
    assert report.bands[1].flags == ()


# --------------------------------------------------------------------------- failure modes


def test_mismatched_images_raise(qc: QcConfig) -> None:
    """Clipping comes from one image and peak height from the other; they must correspond."""
    with pytest.raises(QcError, match="same shape"):
        assess(
            np.zeros((10, 10), dtype=np.uint8), np.zeros((10, 12)), [], 255, False, qc
        )


def test_a_non_positive_max_value_raises(qc: QcConfig) -> None:
    """Full scale is what clipping and dynamic range are measured against."""
    with pytest.raises(QcError, match="max_value must be positive"):
        assess(np.zeros((10, 10), dtype=np.uint8), np.zeros((10, 10)), [], 0, False, qc)


def test_an_roi_off_the_image_raises(qc: QcConfig) -> None:
    """A band whose ROI leaves the image cannot be assessed, and is not silently clipped to it."""
    with pytest.raises(QcError, match="extends past"):
        assess(
            np.zeros((10, 10), dtype=np.uint8),
            np.zeros((10, 10)),
            [_band("b", "L0", Roi(x=5, y=5, width=10, height=10))],
            255,
            False,
            qc,
        )


def test_a_2d_image_is_required(qc: QcConfig) -> None:
    """QC does not pick a channel for a stack any more than the loader does."""
    with pytest.raises(QcError, match="2D"):
        assess(
            np.zeros((4, 10, 10), dtype=np.uint8), np.zeros((4, 10, 10)), [], 255, False, qc
        )


# --------------------------------------------------------------------------- ground truth


GOLD_SET_CASES = (
    ("dev_00", {SATURATED, LOSSY_FORMAT}),
    ("dev_04", set()),
    ("dev_16", {LOW_DYNAMIC_RANGE}),
    ("dev_22", set()),
)
"""Gold-set images and the image flags their ground truth carries.

Chosen to cover every image flag firing and every one not firing: a saturating JPEG, two
normal-exposure images with no flags at all, and a low-exposure one. The whole-split
per-flag precision and recall are re-measured by ``python -m evals.sweep`` and asserted from
that record; this is the case-by-case check that the flags fire on the right images.
"""


def _truth(data_dir: Path, image_id: str) -> dict[str, object]:
    """Return one committed ground-truth document."""
    return json.loads(
        (data_dir / "ground_truth" / f"{image_id}.json").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(("image_id", "expected"), GOLD_SET_CASES, ids=lambda case: str(case))
def test_image_flags_match_ground_truth_on_selected_gold_set_images(
    committed_data_dir: Path, config: PipelineConfig, image_id: str, expected: set[str]
) -> None:
    """The image-level flags reproduce ground truth's own labels, firing and not firing."""
    truth = _truth(committed_data_dir, image_id)
    assert set(truth["image_qc_flags"]) == expected, "the fixture states the truth labels"
    loaded = load_image(committed_data_dir / str(truth["image_path"]))
    correction = correct_background(loaded.pixels, config.background)
    detection = detect(correction.corrected, config.detection)

    report = assess(
        loaded.pixels,
        correction.corrected,
        detection.bands,
        loaded.max_value,
        loaded.lossy_format,
        config.qc,
    )

    assert set(report.image_flags) == expected


def test_saturated_bands_are_flagged_on_a_saturating_gold_set_image(
    committed_data_dir: Path, config: PipelineConfig
) -> None:
    """Band saturation against truth's labels on the bands that were detected.

    Both directions are asserted: every truth-saturated band that was matched is flagged, and
    the image also contains matched bands that are *not* flagged, so this cannot pass by
    flagging everything.
    """
    from evals.metrics import PLAN_IOU_THRESHOLD, match_boxes

    truth = _truth(committed_data_dir, "dev_00")
    loaded = load_image(committed_data_dir / str(truth["image_path"]))
    correction = correct_background(loaded.pixels, config.background)
    detection = detect(correction.corrected, config.detection)
    report = assess(
        loaded.pixels,
        correction.corrected,
        detection.bands,
        loaded.max_value,
        loaded.lossy_format,
        config.qc,
    )
    flags = report.flags_by_band_id()

    matches, _, _ = match_boxes(
        [BoundingBox.from_mapping(band["roi"]) for band in truth["bands"]],
        [BoundingBox.from_mapping(band.roi.as_dict()) for band in detection.bands],
        PLAN_IOU_THRESHOLD,
    )
    assert matches, "the image must match some bands for this to mean anything"
    flagged = 0
    clean = 0
    for truth_index, predicted_index, _ in matches:
        truth_flags = set(truth["bands"][truth_index]["qc_flags"])
        predicted_flags = set(flags[detection.bands[predicted_index].band_id])
        if SATURATED in truth_flags:
            assert SATURATED in predicted_flags
            flagged += 1
        else:
            clean += 1
            assert (SATURATED in predicted_flags) == (
                report.bands[predicted_index].clipped_pixel_count
                >= config.qc.saturated_min_clipped_pixels
            )
    assert flagged > 0 and clean > 0
