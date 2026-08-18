"""Detection, asserted against constructed geometry with stated tolerances."""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
import pytest

from pipeline.config import DetectionConfig, load_config
from pipeline.detect import (
    DetectedLane,
    Roi,
    _detect_bands_in_lane,
    detect,
    detect_lanes,
    profile_noise_sigma,
    smooth_profile,
)
from pipeline.errors import DetectionError
from tests.conftest import Band, Blot, build_blot
from tests.test_pipeline_config import CONFIG_DIR

LANE_CENTERS_PX = (40.0, 100.0, 160.0)
BAND_ROWS_PX = (30.0, 85.0)
SIGMA_X_PX = 8.0
SIGMA_Y_PX = 4.0


@pytest.fixture
def detection() -> DetectionConfig:
    """Return the shipped detection parameters."""
    return load_config(CONFIG_DIR / "default.yaml").detection


def _half_extent_px(sigma: float, relative_height: float) -> float:
    """Return the half-width of a Gaussian at ``relative_height`` of its peak."""
    return sigma * math.sqrt(2.0 * math.log(1.0 / relative_height))


def test_smooth_profile_is_a_centred_moving_average() -> None:
    """Smoothing is the exact centred mean, keeps the length, and stays symmetric."""
    profile = np.array([0.0, 0.0, 3.0, 0.0, 0.0])

    smoothed = smooth_profile(profile, 3)

    np.testing.assert_allclose(smoothed, [0.0, 1.0, 1.0, 1.0, 0.0])
    np.testing.assert_allclose(smoothed, smoothed[::-1])
    assert smoothed.size == profile.size


def test_smooth_profile_rejects_even_windows() -> None:
    """An even window cannot be centred on its sample."""
    with pytest.raises(ValueError, match="odd"):
        smooth_profile(np.zeros(9), 4)


def test_profile_noise_sigma_recovers_a_known_noise_level() -> None:
    """The robust estimator recovers the noise of a smoothed profile within 15%."""
    raw = np.random.RandomState(7).normal(0.0, 50.0, 4000)
    smoothing = 5

    estimate = profile_noise_sigma(raw, smoothing)

    expected = 50.0 / math.sqrt(smoothing)
    assert abs(estimate - expected) / expected < 0.15


def test_profile_noise_sigma_refuses_a_profile_too_short_to_estimate_from() -> None:
    """Returning 0.0 here would silently turn every sigma threshold into a no-op.

    A ``PipelineError`` rather than a bare ``ValueError``, because a one-column lane
    reaches this through a legal config and the CLI has to report it as an error rather
    than a traceback.
    """
    with pytest.raises(DetectionError, match="at least 2 for one difference"):
        profile_noise_sigma(np.array([5.0]), 5)


def test_profile_noise_sigma_ignores_smooth_structure() -> None:
    """A noiseless ramp with a peak on it has (almost) no noise, whatever its amplitude."""
    x = np.arange(300, dtype=np.float64)
    structure = 5.0 * x + 900.0 * np.exp(-0.5 * ((x - 150.0) / 12.0) ** 2)

    assert profile_noise_sigma(structure, 5) < 1.0


def test_lanes_are_found_at_their_true_centres(
    three_lane_blot: Blot, detection: DetectionConfig
) -> None:
    """Three lanes are found, at their true centres, tiling the image without overlap."""
    corrected = three_lane_blot.signal

    lanes = detect_lanes(corrected, detection)

    assert [lane.lane_id for lane in lanes] == ["L0", "L1", "L2"]
    for lane, expected in zip(lanes, LANE_CENTERS_PX, strict=True):
        assert abs(lane.center_px - expected) <= 1.0
        assert lane.roi.y == 0
        assert lane.roi.height == three_lane_blot.pixels.shape[0]
        assert lane.roi.x <= expected < lane.roi.x + lane.roi.width
    for left, right in zip(lanes, lanes[1:], strict=False):
        assert left.roi.x + left.roi.width == right.roi.x


def test_lane_rois_are_one_pitch_wide(
    three_lane_blot: Blot, detection: DetectionConfig
) -> None:
    """Lane boundaries fall midway between neighbours, so each ROI is one pitch wide."""
    lanes = detect_lanes(three_lane_blot.signal, detection)

    pitch = LANE_CENTERS_PX[1] - LANE_CENTERS_PX[0]
    for lane in lanes:
        assert abs(lane.roi.width - pitch) <= 2


def test_bands_are_found_with_the_expected_geometry(
    three_lane_blot: Blot, detection: DetectionConfig
) -> None:
    """Six bands are found, centred on their true centres and sized by the extent rule.

    The expected extent is the Gaussian half-width at
    ``detection.band.extent_relative_height``; the measured one is slightly larger
    because the profile is smoothed before the walk, so the tolerance is 2 px per side.
    """
    result = detect(three_lane_blot.signal, detection)

    assert len(result.bands) == 6
    expected_half_height = _half_extent_px(SIGMA_Y_PX, detection.band.extent_relative_height)
    expected_half_width = _half_extent_px(SIGMA_X_PX, detection.band.extent_relative_height)
    for band, source in zip(result.bands, three_lane_blot.bands, strict=True):
        center_x = band.roi.x + (band.roi.width - 1) / 2.0
        center_y = band.roi.y + (band.roi.height - 1) / 2.0
        assert abs(center_x - source.center_x) <= 1.5
        assert abs(center_y - source.center_y) <= 1.5
        assert abs(band.roi.height - (2 * expected_half_height + 1)) <= 4.0
        assert abs(band.roi.width - (2 * expected_half_width + 1)) <= 4.0


def test_bands_are_reported_lane_by_lane_top_to_bottom(
    three_lane_blot: Blot, detection: DetectionConfig
) -> None:
    """Band ids are unique, carry their lane, and follow lane then row order."""
    result = detect(three_lane_blot.signal, detection)

    assert [band.band_id for band in result.bands] == [
        "L0_B0", "L0_B1", "L1_B0", "L1_B1", "L2_B0", "L2_B1",
    ]
    assert len({band.band_id for band in result.bands}) == len(result.bands)
    for band in result.bands:
        lane = next(item for item in result.lanes if item.lane_id == band.lane_id)
        assert lane.roi.x <= band.roi.x
        assert band.roi.y + band.roi.height <= lane.roi.y + lane.roi.height


def test_every_band_roi_lies_inside_the_image(
    three_lane_blot: Blot, detection: DetectionConfig
) -> None:
    """ROIs are clamped to the image, so quantification never indexes off the edge."""
    height, width = three_lane_blot.pixels.shape

    result = detect(three_lane_blot.signal, detection)

    for band in result.bands:
        assert 0 <= band.roi.x and band.roi.x + band.roi.width <= width
        assert 0 <= band.roi.y and band.roi.y + band.roi.height <= height


def test_two_separated_bands_in_one_lane_are_resolved(
    blot: Callable[..., Blot], detection: DetectionConfig
) -> None:
    """Peaks 4 sigma apart are two bands."""
    sigma_y = 4.0
    built = blot(
        width=120,
        height=120,
        bands=[
            Band(60.0, 40.0, 4000.0, 8.0, sigma_y),
            Band(60.0, 40.0 + 4.0 * sigma_y, 2600.0, 8.0, sigma_y),
        ],
        background_dn=1000.0,
    )

    result = detect(built.signal, detection)

    assert len(result.bands) == 2


def test_an_unresolved_shoulder_is_reported_as_one_band(
    blot: Callable[..., Blot], detection: DetectionConfig
) -> None:
    """A 2.2-sigma partner at 0.65 amplitude makes one maximum, and is reported as one band.

    This pins the Phase 1 ruling in NOTES.md: the detector reports one band per resolved
    maximum and does not deconvolve shoulders. The profile genuinely has a single
    maximum here -- asserted directly -- so reporting two would be inventing a feature
    the data does not show.
    """
    sigma_y = 4.0
    built = blot(
        width=120,
        height=120,
        bands=[
            Band(60.0, 40.0, 4000.0, 8.0, sigma_y),
            Band(60.0, 40.0 + 2.2 * sigma_y, 0.65 * 4000.0, 8.0, sigma_y),
        ],
        background_dn=1000.0,
    )
    lane_profile = built.signal[:, 40:80].mean(axis=1)
    maxima = [
        index
        for index in range(1, lane_profile.size - 1)
        if lane_profile[index] > lane_profile[index - 1]
        and lane_profile[index] > lane_profile[index + 1]
    ]
    assert len(maxima) == 1

    result = detect(built.signal, detection)

    assert len(result.bands) == 1


def test_a_flat_image_raises(detection: DetectionConfig) -> None:
    """An image with no horizontal structure has no lanes to find."""
    with pytest.raises(DetectionError, match="no spread"):
        detect_lanes(np.zeros((40, 60)), detection)


def test_no_lane_above_threshold_raises(detection: DetectionConfig) -> None:
    """An image with a spread but no peak at all raises, naming the parameter to change."""
    monotone = np.tile(np.linspace(0.0, 100.0, 60), (40, 1))

    with pytest.raises(DetectionError, match="min_prominence_fraction"):
        detect_lanes(monotone, detection)


def test_non_2d_input_raises(detection: DetectionConfig) -> None:
    """Detection needs a 2D image."""
    with pytest.raises(DetectionError, match="2D image"):
        detect_lanes(np.zeros((10, 10, 3)), detection)


def test_a_lane_without_bands_contributes_none(
    blot: Callable[..., Blot], detection: DetectionConfig
) -> None:
    """A lane whose row profile carries no peak yields no bands rather than an error."""
    built = blot(
        width=200,
        height=120,
        bands=[Band(cx, 30.0, 4000.0, 8.0, 4.0) for cx in (40.0, 160.0)],
        background_dn=1000.0,
    )
    corrected = built.signal.copy()
    corrected[:, 70:130] = 0.0

    result = detect(corrected, detection)

    assert len(result.bands) == 2
    assert {band.lane_id for band in result.bands} == {"L0", "L1"}


def test_a_peak_whose_lane_has_no_horizontal_spread_yields_no_band(
    detection: DetectionConfig,
) -> None:
    """The documented guard in ``_detect_bands_in_lane``: no column spread, no ROI.

    Reaching it needs a lane whose pixels are exactly constant across its full width
    over the peak's rows, which cannot happen via ``detect`` on real pixels, so the
    private helper is called directly rather than leaving the branch unexercised.
    """
    stripe = np.zeros((60, 40))
    stripe[25:35, :] = 500.0
    lane = DetectedLane(
        lane_id="L0", lane_index=0, center_px=20.0, roi=Roi(0, 0, 40, 60), roi_source="detected"
    )

    assert _detect_bands_in_lane(stripe, lane, detection) == []
    # The same stripe with any column-to-column variation does produce a band.
    stripe[25:35, 15:25] += 500.0
    assert len(_detect_bands_in_lane(stripe, lane, detection)) == 1


def test_band_rois_are_offset_by_their_lane_origin(detection: DetectionConfig) -> None:
    """Band rows are image rows, not lane-slice rows, even for a lane not at y=0."""
    stripe = np.zeros((60, 40))
    stripe[25:35, 15:25] = 500.0
    offset_lane = DetectedLane(
        lane_id="L0",
        lane_index=0,
        center_px=20.0,
        roi=Roi(x=5, y=20, width=30, height=30),
        roi_source="detected",
    )

    bands = _detect_bands_in_lane(stripe, offset_lane, detection)

    assert len(bands) == 1
    assert bands[0].roi.y >= offset_lane.roi.y
    assert bands[0].roi.x >= offset_lane.roi.x
    assert bands[0].roi.y + bands[0].roi.height <= offset_lane.roi.y + offset_lane.roi.height


def test_roi_rejects_degenerate_and_negative_rectangles() -> None:
    """An ROI with no extent, or off the top-left of the image, is not constructible."""
    with pytest.raises(ValueError, match="positive extent"):
        Roi(x=0, y=0, width=0, height=5)
    with pytest.raises(ValueError, match="non-negative"):
        Roi(x=-1, y=0, width=5, height=5)


def test_roi_coerces_numpy_integers_but_rejects_floats() -> None:
    """Profile indices arrive as numpy integers; a non-integral value is an error."""
    roi = Roi(x=np.int64(3), y=np.int64(4), width=np.int64(5), height=np.int64(6))

    assert roi.as_dict() == {"x": 3, "y": 4, "width": 5, "height": 6}
    assert all(type(value) is int for value in roi.as_dict().values())
    with pytest.raises(TypeError, match="integer number of pixels"):
        Roi(x=1.5, y=0, width=5, height=5)


SINGLE_LANE_SIGMA_X_PX = 8.0
"""Lane half-width of the single-lane fixtures, in the same units as the band fixtures."""


def _single_lane_blot(center_x: float) -> Blot:
    """Return a noiseless blot with exactly one lane, centred at ``center_x``."""
    return build_blot(
        width=200,
        height=120,
        bands=[
            Band(
                center_x=center_x,
                center_y=cy,
                amplitude=4000.0,
                sigma_x=SINGLE_LANE_SIGMA_X_PX,
                sigma_y=4.0,
            )
            for cy in (30.0, 85.0)
        ],
        background_dn=1000.0,
    )


@pytest.mark.parametrize("center_x", [40.0, 100.0, 160.0], ids=["left", "centre", "right"])
def test_a_single_lane_is_measured_from_its_own_extent(
    center_x: float, detection: DetectionConfig
) -> None:
    """One lane yields one rectangle around *that lane*, wherever it sits.

    The width comes from the column profile's extent under the shared
    ``extent_relative_height`` rule, so it is the Gaussian half-width at that threshold,
    doubled: ``2 * sigma_x * sqrt(2 ln(1/h))``, widened slightly by the profile smoothing.
    What it must never be is the canvas width, which is what an earlier revision returned
    whenever a lone lane happened to sit off centre.
    """
    built = _single_lane_blot(center_x)

    lanes = detect_lanes(built.signal, detection)

    assert len(lanes) == 1
    roi = lanes[0].roi
    expected_width = 2 * _half_extent_px(
        SINGLE_LANE_SIGMA_X_PX, detection.lane.extent_relative_height
    )
    assert roi.width == pytest.approx(expected_width, abs=6.0)
    assert roi.width < built.pixels.shape[1] / 2
    measured_centre = roi.x + (roi.width - 1) / 2.0
    assert measured_centre == pytest.approx(center_x, abs=2.0)
    assert roi.y == 0
    assert roi.height == built.pixels.shape[0]


def test_a_single_off_centre_lane_does_not_span_the_canvas(
    detection: DetectionConfig,
) -> None:
    """The specific defect this replaced: an off-centre lone lane got a canvas-wide ROI.

    With ``pitch_px = width_px`` the rectangle was clamped asymmetrically -- a lane at
    x = 40 of 200 produced x = 0, width 140 -- so most of it was empty gel.
    """
    left = detect_lanes(_single_lane_blot(40.0).signal, detection)[0].roi
    right = detect_lanes(_single_lane_blot(160.0).signal, detection)[0].roi

    assert (left.x, left.width) != (0, 140)
    assert (right.x, right.width) != (60, 140)
    assert left.x > 0
    assert right.x + right.width < 200
    assert left.width == pytest.approx(right.width, abs=2.0)


SINGLE_LANE_BAND_WIDTH_PX = 33
MULTI_LANE_BAND_WIDTH_PX = 39
"""Measured band widths for one identical band, sliced by a lone-lane and a multi-lane ROI.

The multi-lane figure matches the closed form ``2*sigma_x*sqrt(2 ln(1/h)) + 1`` = 38.95 px.
The single-lane figure does not, and that is the point: see
:func:`test_the_lane_slice_changes_the_band_width_it_yields`.
"""


def _three_lane_blot() -> Blot:
    """Return a three-lane blot whose bands are identical to the single-lane fixture's."""
    return build_blot(
        width=200,
        height=120,
        bands=[
            Band(
                center_x=cx,
                center_y=cy,
                amplitude=4000.0,
                sigma_x=SINGLE_LANE_SIGMA_X_PX,
                sigma_y=4.0,
            )
            for cx in (40.0, 100.0, 160.0)
            for cy in (30.0, 85.0)
        ],
        background_dn=1000.0,
    )


def test_the_lane_slice_changes_the_band_width_it_yields(detection: DetectionConfig) -> None:
    """The same band measures narrower inside a lone-lane ROI than inside a multi-lane one.

    This is a real, accepted consequence of measuring a lone lane's width from its own
    extent, not an artefact of the fixtures, and NOTES.md records it as making absolute
    integrated intensities convention-dependent. The mechanism: the band's horizontal
    extent is walked on a column profile taken over the lane slice, and a tighter slice
    raises that profile's ``baseline_percentile``, which lifts the edge threshold with it.

    Both numbers are pinned so the divergence is captured rather than described. The
    multi-lane width is the one that matches the closed form; the single-lane width is
    ~15% smaller, excluding ~6 px of true band signal.
    """
    single = detect(_single_lane_blot(100.0).signal, detection)
    multi = detect(_three_lane_blot().signal, detection)

    closed_form = 2 * _half_extent_px(
        SINGLE_LANE_SIGMA_X_PX, detection.band.extent_relative_height
    ) + 1
    centre_lane = [band for band in multi.bands if band.lane_id == "L1"]

    assert {band.roi.width for band in multi.bands} == {MULTI_LANE_BAND_WIDTH_PX}
    assert MULTI_LANE_BAND_WIDTH_PX == pytest.approx(closed_form, abs=1.0)
    assert {band.roi.width for band in single.bands} == {SINGLE_LANE_BAND_WIDTH_PX}
    assert len(centre_lane) == len(single.bands) == 2
    assert MULTI_LANE_BAND_WIDTH_PX - SINGLE_LANE_BAND_WIDTH_PX == 6
    assert SINGLE_LANE_BAND_WIDTH_PX / MULTI_LANE_BAND_WIDTH_PX == pytest.approx(0.85, abs=0.01)


def test_a_single_lane_still_yields_its_bands(detection: DetectionConfig) -> None:
    """A lone lane still produces its two bands, inside its own rectangle.

    The containment assertions here are structural -- ``_detect_bands_in_lane`` derives a
    band's columns from a profile whose length *is* the lane width, so no lane rectangle
    can fail them. What this test actually guarantees is the band *count* on the
    single-lane path; the width consequence is pinned in the test above.
    """
    built = _single_lane_blot(40.0)

    result = detect(built.signal, detection)

    assert len(result.lanes) == 1
    assert len(result.bands) == 2
    for band in result.bands:
        assert band.roi.x >= result.lanes[0].roi.x
        assert band.roi.x + band.roi.width <= result.lanes[0].roi.x + result.lanes[0].roi.width
