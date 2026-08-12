"""Quantification, asserted against integrals that are known exactly."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest

from pipeline.background import correct_background
from pipeline.config import (
    LOCAL_MEDIAN,
    BackgroundConfig,
    LocalMedianParams,
    QuantificationConfig,
    load_config,
)
from pipeline.detect import DetectedBand, Roi
from pipeline.quantify import integrate_roi, mean_over_roi, quantify_bands
from tests.conftest import Band, Blot, build_blot
from tests.test_pipeline_config import CONFIG_DIR

MEDIAN = BackgroundConfig(method=LOCAL_MEDIAN, params=LocalMedianParams(window_px=31))
KEEP_NEGATIVES = QuantificationConfig(method="roi_sum", clamp_negative_pixels=False)
CLAMP_NEGATIVES = QuantificationConfig(method="roi_sum", clamp_negative_pixels=True)
FLAT_DN = 1000.0


def test_a_box_of_known_total_is_recovered_exactly() -> None:
    """On a zero background the ROI sum is the exact sum of its pixels."""
    corrected = np.zeros((40, 40))
    corrected[10:20, 12:24] = 7.0
    roi = Roi(x=12, y=10, width=12, height=10)

    assert integrate_roi(corrected, roi, clamp_negative_pixels=False) == pytest.approx(840.0)


def test_a_gaussian_band_is_recovered_to_within_one_percent(
    blot: Callable[..., Blot],
) -> None:
    """A background-corrected ROI sum matches the band layer summed over the same ROI.

    The 1% tolerance covers quantization (round-half-up at 1 DN on a 5000 DN peak) and
    the local median's residual under the band; nothing else enters on a noiseless,
    flat-background fixture.
    """
    band = Band(center_x=40.0, center_y=30.0, amplitude=5000.0, sigma_x=5.0, sigma_y=2.5)
    built = blot(width=80, height=60, bands=[band], background_dn=FLAT_DN)
    roi = Roi(x=28, y=23, width=25, height=15)

    corrected = correct_background(built.pixels, MEDIAN).corrected

    measured = integrate_roi(corrected, roi, clamp_negative_pixels=False)
    rows, columns = roi.slices()
    expected = float(built.signal[rows, columns].sum())
    assert abs(measured - expected) / expected < 0.01


def test_clamping_negatives_turns_noise_into_signal(blot: Callable[..., Blot]) -> None:
    """Over band-free pixels the unclamped sum is ~0 and the clamped one is ~0.4 sigma each.

    That is why ``clamp_negative_pixels`` defaults to false and is recorded: on a
    600-pixel ROI at 80 DN noise, clamping alone invents ~19000 DN of "signal".
    """
    noise_sigma_dn = 80.0
    built = blot(
        width=80,
        height=60,
        bands=[],
        background_dn=FLAT_DN,
        noise_sigma_dn=noise_sigma_dn,
    )
    roi = Roi(x=10, y=10, width=30, height=20)
    corrected = correct_background(built.pixels, MEDIAN).corrected

    kept = integrate_roi(corrected, roi, clamp_negative_pixels=False)
    clamped = integrate_roi(corrected, roi, clamp_negative_pixels=True)

    pixel_count = roi.width * roi.height
    assert abs(kept) < 4.0 * noise_sigma_dn * np.sqrt(pixel_count)
    assert clamped == pytest.approx(0.399 * noise_sigma_dn * pixel_count, rel=0.2)


def test_background_estimate_is_the_mean_level_over_the_roi() -> None:
    """The reported background is a level in DN, so raw sum = intensity + level * area."""
    surface = np.arange(40 * 40, dtype=np.float64).reshape(40, 40)
    roi = Roi(x=5, y=6, width=7, height=8)

    rows, columns = roi.slices()
    assert mean_over_roi(surface, roi) == pytest.approx(float(surface[rows, columns].mean()))


def test_quantify_bands_reconstructs_the_raw_roi_sum(blot: Callable[..., Blot]) -> None:
    """intensity + background_estimate * area returns the uncorrected ROI sum."""
    built = blot(
        width=80,
        height=60,
        bands=[Band(40.0, 30.0, 4000.0, 5.0, 2.5)],
        background_dn=FLAT_DN,
    )
    correction = correct_background(built.pixels, MEDIAN)
    roi = Roi(x=28, y=23, width=25, height=15)
    band = DetectedBand(band_id="L0_B0", lane_id="L0", roi=roi)

    measured = quantify_bands(
        correction.corrected, correction.background, [band], KEEP_NEGATIVES
    )

    assert len(measured) == 1
    rows, columns = roi.slices()
    raw = float(built.pixels[rows, columns].sum())
    reconstructed = (
        measured[0].integrated_intensity + measured[0].background_estimate * roi.width * roi.height
    )
    assert reconstructed == pytest.approx(raw, rel=1e-9)


def test_quantify_bands_preserves_identity_and_order() -> None:
    """Every detected band gets exactly one measurement, in the order detection gave."""
    corrected = np.ones((40, 40))
    bands = [
        DetectedBand(f"L0_B{index}", "L0", Roi(x=2, y=2 + 10 * index, width=6, height=6))
        for index in range(3)
    ]

    measured = quantify_bands(corrected, np.zeros((40, 40)), bands, KEEP_NEGATIVES)

    assert [item.band_id for item in measured] == ["L0_B0", "L0_B1", "L0_B2"]
    assert all(item.lane_id == "L0" for item in measured)
    assert all(item.integrated_intensity == pytest.approx(36.0) for item in measured)


def test_the_shipped_config_selects_the_implemented_method() -> None:
    """The quantification method named in configs/default.yaml is the one implemented."""
    config = load_config(CONFIG_DIR / "default.yaml").quantification

    assert config.method == "roi_sum"
    assert config.clamp_negative_pixels is False


def test_roi_outside_the_image_raises() -> None:
    """An ROI that runs past the image edge raises rather than silently truncating."""
    with pytest.raises(ValueError, match="extends past"):
        integrate_roi(np.zeros((10, 10)), Roi(x=6, y=0, width=6, height=4), False)


def test_non_2d_image_raises() -> None:
    """Quantification needs a 2D image."""
    with pytest.raises(ValueError, match="2D image"):
        integrate_roi(np.zeros((4, 4, 3)), Roi(x=0, y=0, width=2, height=2), False)


def test_mismatched_background_shape_raises() -> None:
    """A background surface of a different shape cannot describe this image."""
    with pytest.raises(ValueError, match="same shape"):
        quantify_bands(np.zeros((10, 10)), np.zeros((10, 12)), [], KEEP_NEGATIVES)


def test_clamping_is_a_recorded_parameter_not_a_convention() -> None:
    """The two settings genuinely differ, so the recorded value is load-bearing."""
    corrected = np.array([[-5.0, 5.0], [-5.0, 5.0]])
    roi = Roi(x=0, y=0, width=2, height=2)

    assert integrate_roi(corrected, roi, KEEP_NEGATIVES.clamp_negative_pixels) == 0.0
    assert integrate_roi(corrected, roi, CLAMP_NEGATIVES.clamp_negative_pixels) == 10.0


def test_build_blot_quantizes_without_rescaling() -> None:
    """The fixture's own quantization is round-half-up and clip, so tests compare like for like."""
    built = build_blot(width=4, height=4, bands=[], background_dn=1000.4)

    assert built.pixels.dtype == np.uint16
    assert int(built.pixels[0, 0]) == 1000
