"""Background estimation, asserted numerically against constructed surfaces."""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
import pytest

from pipeline.background import correct_background, estimate_background
from pipeline.config import (
    LOCAL_MEDIAN,
    ROLLING_BALL,
    BackgroundConfig,
    LocalMedianParams,
    RollingBallParams,
)
from pipeline.errors import BackgroundError
from tests.conftest import Band, Blot, build_blot

MEDIAN_WINDOW_PX = 31
MEDIAN = BackgroundConfig(
    method=LOCAL_MEDIAN, params=LocalMedianParams(window_px=MEDIAN_WINDOW_PX)
)
BALL = BackgroundConfig(
    method=ROLLING_BALL, params=RollingBallParams(radius_px=10.0, presmooth_px=3)
)
FLAT_DN = 1000.0
SLOPE_DN_PER_PX = 1.0


@pytest.mark.parametrize("config", [MEDIAN, BALL], ids=["local_median", "rolling_ball"])
def test_flat_background_is_recovered_exactly(config: BackgroundConfig) -> None:
    """On a constant image the estimate is that constant, to within float noise."""
    image = np.full((60, 80), FLAT_DN)

    estimate = estimate_background(image, config)

    assert estimate.shape == image.shape
    np.testing.assert_allclose(estimate, FLAT_DN, atol=1e-6)


def _ramp_image() -> np.ndarray:
    """Return an image whose background ramps left to right at a known slope."""
    columns = np.arange(80, dtype=np.float64)[None, :]
    return np.broadcast_to(FLAT_DN + SLOPE_DN_PER_PX * columns, (60, 80)).copy()


@pytest.mark.parametrize(
    ("config", "tolerance_dn"),
    [(MEDIAN, 1e-6), (BALL, 5.0)],
    ids=["local_median", "rolling_ball"],
)
def test_linear_ramp_is_recovered(config: BackgroundConfig, tolerance_dn: float) -> None:
    """A background that ramps across the image is recovered.

    The median is exact on a plane. A ball of finite radius is not: it cannot follow an
    arbitrary slope, and at 1 DN/px with a 10 px radius it sits ~4 DN low everywhere.
    That is a property of the method, recorded here rather than hidden.
    """
    image = _ramp_image()

    estimate = estimate_background(image, config)

    np.testing.assert_allclose(estimate, image, atol=tolerance_dn)


@pytest.mark.parametrize("config", [MEDIAN, BALL], ids=["local_median", "rolling_ball"])
def test_the_border_is_no_worse_than_the_interior_on_a_ramp(config: BackgroundConfig) -> None:
    """Edge replication leaves no rim artefact, so no extra padding is warranted.

    This is the assertion NOTES.md leans on when it says a slope-preserving border
    extension was measured and dropped: there is nothing at the border for it to fix.
    """
    image = _ramp_image()

    error = np.abs(estimate_background(image, config) - image)

    border = max(float(error[:, :3].max()), float(error[:, -3:].max()))
    interior = float(error[:, 15:-15].max())
    assert border <= interior + 0.5


@pytest.mark.parametrize("config", [MEDIAN, BALL], ids=["local_median", "rolling_ball"])
def test_a_band_is_not_absorbed_into_the_background(config: BackgroundConfig) -> None:
    """Under a band, the estimate stays on the background instead of climbing the band."""
    band = Band(center_x=40.0, center_y=30.0, amplitude=4000.0, sigma_x=5.0, sigma_y=2.5)
    blot = build_blot(width=80, height=60, bands=[band], background_dn=FLAT_DN)

    estimate = estimate_background(blot.pixels, config)

    error_at_peak = abs(float(estimate[30, 40]) - FLAT_DN)
    assert error_at_peak < 0.005 * band.amplitude


def test_local_median_reads_high_under_a_band_and_rolling_ball_reads_low_in_noise() -> None:
    """The two documented biases are real and run in opposite directions.

    ``local_median`` is contaminated upward by the band pixels sharing its window;
    ``rolling_ball`` is an opening, so on a noisy image it settles onto the noise
    minimum and reads low. NOTES.md quotes both; this pins them.
    """
    band = Band(center_x=40.0, center_y=30.0, amplitude=3000.0, sigma_x=5.0, sigma_y=2.5)
    blot = build_blot(
        width=80, height=60, bands=[band], background_dn=FLAT_DN, noise_sigma_dn=60.0
    )

    median_estimate = estimate_background(blot.pixels, MEDIAN)
    ball_estimate = estimate_background(blot.pixels, BALL)

    assert float(median_estimate[30, 40]) > FLAT_DN
    band_free = ball_estimate[:, :20]
    assert float(band_free.mean()) < FLAT_DN


@pytest.mark.parametrize("config", [MEDIAN, BALL], ids=["local_median", "rolling_ball"])
def test_correction_is_exactly_the_difference(config: BackgroundConfig) -> None:
    """``corrected`` is the input minus the estimate, with no clamping."""
    blot = build_blot(
        width=80,
        height=60,
        bands=[Band(40.0, 30.0, 2000.0, 5.0, 2.5)],
        background_dn=FLAT_DN,
        noise_sigma_dn=40.0,
    )

    correction = correct_background(blot.pixels, config)

    np.testing.assert_allclose(
        correction.corrected, blot.pixels.astype(np.float64) - correction.background, atol=0.0
    )
    assert float(correction.corrected.min()) < 0.0


def test_recovered_band_signal_matches_the_layer_that_was_added(
    blot: Callable[..., Blot],
) -> None:
    """On a flat, noiseless background the corrected image is the band layer itself."""
    band = Band(center_x=40.0, center_y=30.0, amplitude=5000.0, sigma_x=5.0, sigma_y=2.5)
    built = blot(width=80, height=60, bands=[band], background_dn=FLAT_DN)

    corrected = correct_background(built.pixels, MEDIAN).corrected

    recovered = float(corrected.sum())
    expected = float(built.signal.sum())
    assert abs(recovered - expected) / expected < 0.01


def test_window_larger_than_the_image_raises() -> None:
    """A footprint that cannot fit is a configuration error, not a constant estimate."""
    config = BackgroundConfig(method=LOCAL_MEDIAN, params=LocalMedianParams(window_px=101))

    with pytest.raises(BackgroundError, match="shortest axis"):
        estimate_background(np.zeros((40, 40)), config)


def test_ball_larger_than_the_image_raises() -> None:
    """The same guard applies to the rolling ball's diameter."""
    config = BackgroundConfig(
        method=ROLLING_BALL, params=RollingBallParams(radius_px=40.0, presmooth_px=3)
    )

    with pytest.raises(BackgroundError, match="shortest axis"):
        estimate_background(np.zeros((40, 40)), config)


def test_unknown_method_raises() -> None:
    """A method with no estimator raises rather than falling back to another one."""
    config = BackgroundConfig(method="mystery", params=LocalMedianParams(window_px=21))

    with pytest.raises(BackgroundError, match="no background estimator"):
        estimate_background(np.zeros((60, 60)), config)


@pytest.mark.parametrize("shape", [(60,), (4, 4, 3)])
def test_non_2d_input_raises(shape: tuple[int, ...]) -> None:
    """Background estimation needs a 2D grayscale image."""
    with pytest.raises(BackgroundError, match="2D grayscale"):
        estimate_background(np.zeros(shape), MEDIAN)


def test_empty_image_raises() -> None:
    """An empty image has no background to estimate."""
    with pytest.raises(BackgroundError, match="non-empty"):
        estimate_background(np.zeros((0, 0)), MEDIAN)


BAND_EXTENT_TO_SIGMA = math.sqrt(2.0 * math.log(1.0 / 0.06))
"""Half-extent of a Gaussian band's ROI in sigmas, at the shipped ``extent_relative_height``.

The fixture bands are Gaussian on both axes, so the same factor applies to both. A real band
here is flat-topped across the lane, whose shoulders are fatter, so a fixture sized to the
same ROI puts slightly *less* signal inside the window than the gold set does -- the bias
measured below is a lower bound on the gold set's.
"""

MEDIAN_TRUTH_ROI_PX = (34, 12)
LARGEST_TRUTH_ROI_PX = (42, 28)
"""Band ROI sizes measured over the dev split; see ``evals/dev_sweeps.md``, ``band_roi_sizes``.

The largest is the largest by *area*, which is what determines how much of a median window
a band can fill.
"""

WORST_CASE_BIAS_FRACTION = 0.0185
"""Background over-estimate at the largest dev band, as a fraction of its amplitude."""

SHIPPED_WINDOW_PX = 51
SHIPPED_MEDIAN = BackgroundConfig(
    method=LOCAL_MEDIAN, params=LocalMedianParams(window_px=SHIPPED_WINDOW_PX)
)
"""The window ``configs/default.yaml`` ships, since these two tests are about that claim.

The module's smaller ``MEDIAN`` exists so the other tests can use small fixtures.
"""


def _band_filling(width_px: int, height_px: int, amplitude: float) -> Band:
    """Return a band whose ROI at the shipped edge threshold is ``width_px x height_px``."""
    return Band(
        center_x=70.0,
        center_y=60.0,
        amplitude=amplitude,
        sigma_x=(width_px / 2.0) / BAND_EXTENT_TO_SIGMA,
        sigma_y=(height_px / 2.0) / BAND_EXTENT_TO_SIGMA,
    )


def test_a_median_sized_band_leaves_the_median_window_on_the_background() -> None:
    """The premise behind ``window_px``, at the median dev band: the estimate is unaffected."""
    amplitude = 4000.0
    band = _band_filling(*MEDIAN_TRUTH_ROI_PX, amplitude)
    built = build_blot(width=140, height=120, bands=[band], background_dn=FLAT_DN)

    estimate = estimate_background(built.pixels, SHIPPED_MEDIAN)

    assert abs(float(estimate[60, 70]) - FLAT_DN) < 0.001 * amplitude


def test_the_largest_band_biases_the_median_window_high_by_the_recorded_amount() -> None:
    """And at the largest dev band the premise degrades, by a stated amount in a stated direction.

    A 42 x 28 px ROI fills 45% of a 51 x 51 window. On a noiseless flat background the bias
    is purely the band's own tails filling that window; with noise there is additionally the
    quantile shift a contaminated window implies (the median reports roughly the 0.91 quantile
    at this coverage). NOTES.md quotes this figure as the honest limit of the local-median
    argument; this is where it is measured. The sign matters: the background reads *high*, so
    the band reads low by the same proportion.
    """
    amplitude = 4000.0
    band = _band_filling(*LARGEST_TRUTH_ROI_PX, amplitude)
    built = build_blot(width=140, height=120, bands=[band], background_dn=FLAT_DN)

    error = float(estimate_background(built.pixels, SHIPPED_MEDIAN)[60, 70]) - FLAT_DN

    assert error > 0.0
    assert error == pytest.approx(WORST_CASE_BIAS_FRACTION * amplitude, rel=0.1)
