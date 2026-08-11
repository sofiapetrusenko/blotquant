"""Numeric agreement between the rendering code and ``synth/MODELS.md``.

The determinism tests pin current behaviour byte-for-byte; they cannot tell whether
that behaviour is the one the models document. These tests assert the documented
formulas directly -- a sign flip in the smile term, a factor of two in the shot-noise
gain, or a gradient applied along the wrong axis fails here and nowhere else. That
matters beyond correctness: ``MODELS.md`` is the document a reviewer uses in Phase 3
to judge ``pipeline/`` for special-casing, so it has to describe the real generator.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from synth.config import DEFAULT_CONFIG, BackgroundParams, DefectParams, NoiseParams
from synth.render import (
    GelGeometry,
    apply_noise,
    coordinate_grids,
    quantize,
    render_background,
    render_band,
    render_dust,
    render_scratches,
)
from synth.rng import make_rng

CANVAS_WIDTH = 256
CANVAS_HEIGHT = 192
CENTER_X = (CANVAS_WIDTH - 1) / 2.0
CENTER_Y = (CANVAS_HEIGHT - 1) / 2.0
TILT_DEG = 4.0
TILT_SLOPE = math.tan(math.radians(TILT_DEG))
SMILE_PX = 9.0

# 400x400 samples: the relative standard error of a variance estimate is
# sqrt(2/n) = 0.35%, so a 3% tolerance is ~8 sigma while still catching a factor of 2.
NOISE_SAMPLE_SHAPE = (400, 400)
NOISE_VARIANCE_TOLERANCE = 0.03


# ------------------------------------------------------------------- MODELS.md §3


def test_tilt_is_a_rigid_rotation_of_lanes_and_rows() -> None:
    """Lane x shifts with +slope*dy; row y shifts with -slope*dx (MODELS.md §3)."""
    geometry = GelGeometry(TILT_SLOPE, 0.0, CENTER_X, CENTER_Y, CENTER_X)
    for delta in (-60.0, -10.0, 25.0, 70.0):
        assert geometry.lane_x_at(CENTER_Y + delta) == pytest.approx(TILT_SLOPE * delta)
        assert geometry.row_y_at(CENTER_X + delta) == pytest.approx(-TILT_SLOPE * delta)


def test_smile_sags_rows_quadratically_to_the_configured_amplitude() -> None:
    geometry = GelGeometry(0.0, SMILE_PX, CENTER_X, CENTER_Y, CENTER_X)
    assert geometry.row_y_at(CENTER_X) == pytest.approx(0.0)
    assert geometry.row_y_at(0.0) == pytest.approx(SMILE_PX)
    assert geometry.row_y_at(float(CANVAS_WIDTH - 1)) == pytest.approx(SMILE_PX)
    # Quadratic: half way out from the centre the sag is a quarter of the amplitude.
    assert geometry.row_y_at(CENTER_X + CENTER_X / 2.0) == pytest.approx(SMILE_PX / 4.0)


def test_rendered_band_centre_follows_the_tilted_lane() -> None:
    """The brightest column at each row tracks the lane's tilt in the rendered array."""
    xs, ys = coordinate_grids(CANVAS_WIDTH, CANVAS_HEIGHT)
    geometry = GelGeometry(TILT_SLOPE, 0.0, CENTER_X, CENTER_Y, CENTER_X)
    layer = render_band(
        xs,
        ys,
        amplitude_dn=1000.0,
        nominal_x=128.0,
        nominal_y=96.0,
        sigma_x_px=8.0,
        # Tall on purpose, so the lane can be sampled over many rows and the row's own
        # (rigidly rotated) tilt does not bias the brightest column.
        sigma_y_px=5000.0,
        flat_top_exponent=4.0,
        geometry=geometry,
    )
    for row in (30, 96, 160):
        expected = 128.0 + TILT_SLOPE * (row - CENTER_Y)
        assert abs(int(np.argmax(layer[row])) - expected) <= 1.0


def test_rendered_band_row_follows_the_smile() -> None:
    """The brightest row at each column tracks the quadratic sag in the rendered array."""
    xs, ys = coordinate_grids(CANVAS_WIDTH, CANVAS_HEIGHT)
    geometry = GelGeometry(0.0, SMILE_PX, CENTER_X, CENTER_Y, CENTER_X)
    layer = render_band(
        xs,
        ys,
        amplitude_dn=1000.0,
        nominal_x=128.0,
        nominal_y=96.0,
        sigma_x_px=400.0,  # wide on purpose, so the row can be sampled over many columns
        sigma_y_px=3.0,
        flat_top_exponent=4.0,
        geometry=geometry,
    )
    for column in (0, 64, 128, 200, 255):
        expected = 96.0 + SMILE_PX * ((column - CENTER_X) / CENTER_X) ** 2
        assert abs(int(np.argmax(layer[:, column])) - expected) <= 1.0


def test_band_profile_is_a_flat_top_across_the_lane_and_a_gaussian_along_migration() -> None:
    """``A*exp(-0.5|u|^p)`` across the lane, ``A*exp(-0.5 v^2)`` along migration."""
    xs, ys = coordinate_grids(CANVAS_WIDTH, CANVAS_HEIGHT)
    geometry = GelGeometry(0.0, 0.0, CENTER_X, CENTER_Y, CENTER_X)
    amplitude, sigma_x, sigma_y, exponent = 1000.0, 10.0, 5.0, 4.0
    layer = render_band(
        xs,
        ys,
        amplitude_dn=amplitude,
        nominal_x=128.0,
        nominal_y=96.0,
        sigma_x_px=sigma_x,
        sigma_y_px=sigma_y,
        flat_top_exponent=exponent,
        geometry=geometry,
    )
    assert layer[96, 128] == pytest.approx(amplitude)
    for offset in (5.0, 10.0, 20.0):
        expected_x = amplitude * math.exp(-0.5 * (offset / sigma_x) ** exponent)
        assert layer[96, int(128 + offset)] == pytest.approx(expected_x, rel=1e-9)
        expected_y = amplitude * math.exp(-0.5 * (offset / sigma_y) ** 2)
        assert layer[int(96 + offset), 128] == pytest.approx(expected_y, rel=1e-9)


# ------------------------------------------------------------------- MODELS.md §2


def test_gradient_background_matches_the_documented_ramp() -> None:
    width, height, max_value = 64, 48, 255
    xs, ys = coordinate_grids(width, height)
    params = DEFAULT_CONFIG.backgrounds["gradient"]
    layer, blobs = render_background(xs, ys, params, max_value, make_rng(1, "background-test"))
    assert blobs == []
    for x, y in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1), (31, 23)):
        expected = max_value * (
            params.base_fraction
            + params.gradient_x_fraction * x / (width - 1)
            + params.gradient_y_fraction * y / (height - 1)
        )
        assert layer[y, x] == pytest.approx(expected, rel=1e-12)


def test_flat_background_is_constant_at_the_configured_fraction() -> None:
    xs, ys = coordinate_grids(32, 24)
    params = DEFAULT_CONFIG.backgrounds["flat"]
    layer, blobs = render_background(xs, ys, params, 65535, make_rng(1, "background-test"))
    assert blobs == []
    assert np.all(layer == params.base_fraction * 65535)


def test_recorded_speckle_blob_reproduces_the_pixels_it_wrote() -> None:
    """A blob's recorded centre, sigma and signed amplitude must explain the layer."""
    max_value = 65535
    params = BackgroundParams(
        base_fraction=0.10,
        gradient_x_fraction=0.0,
        gradient_y_fraction=0.0,
        blob_count=1,
        blob_sigma_px_min=12.0,
        blob_sigma_px_max=12.0,
        blob_amplitude_fraction_min=0.04,
        blob_amplitude_fraction_max=0.04,
        blob_bright_probability=1.0,
    )
    xs, ys = coordinate_grids(96, 96)
    layer, blobs = render_background(xs, ys, params, max_value, make_rng(3, "background-test"))
    assert len(blobs) == 1
    blob = blobs[0]
    assert blob["sigma_px"] == pytest.approx(12.0)
    assert blob["amplitude_dn"] == pytest.approx(0.04 * max_value)
    base = params.base_fraction * max_value
    for x, y in ((10, 10), (48, 48), (80, 30), (60, 70)):
        radius_squared = (x - blob["center_x"]) ** 2 + (y - blob["center_y"]) ** 2
        expected = base + blob["amplitude_dn"] * math.exp(
            -0.5 * radius_squared / blob["sigma_px"] ** 2
        )
        assert layer[y, x] == pytest.approx(expected, rel=1e-9)


def test_speckle_blobs_can_be_dark_as_well_as_bright() -> None:
    params = BackgroundParams(
        base_fraction=0.30,
        gradient_x_fraction=0.0,
        gradient_y_fraction=0.0,
        blob_count=20,
        blob_sigma_px_min=6.0,
        blob_sigma_px_max=6.0,
        blob_amplitude_fraction_min=0.05,
        blob_amplitude_fraction_max=0.05,
        blob_bright_probability=0.5,
    )
    xs, ys = coordinate_grids(96, 96)
    _, blobs = render_background(xs, ys, params, 255, make_rng(5, "background-test"))
    signs = {math.copysign(1.0, blob["amplitude_dn"]) for blob in blobs}
    assert signs == {1.0, -1.0}


# ------------------------------------------------------------------- MODELS.md §5


def test_recorded_dust_speck_reproduces_the_attenuation_it_applied() -> None:
    """Mask = 1 - (1 - transmittance) * exp(-0.5 r^2/radius^2), not 1 - t*exp(...)."""
    transmittance, radius = 0.35, 4.0
    params = DefectParams(
        dust_count=1,
        dust_radius_px_min=radius,
        dust_radius_px_max=radius,
        dust_transmittance=transmittance,
        scratch_count=0,
        scratch_sigma_px=0.0,
        scratch_amplitude_fraction=0.0,
        scratch_length_px_min=0.0,
        scratch_length_px_max=0.0,
    )
    xs, ys = coordinate_grids(96, 96)
    mask, specks = render_dust(xs, ys, params, make_rng(29, "defect-test"))
    assert len(specks) == 1
    speck = specks[0]
    assert speck["kind"] == "dust"
    assert speck["radius_px"] == pytest.approx(radius)
    assert speck["transmittance"] == pytest.approx(transmittance)
    assert float(mask.max()) <= 1.0
    for x, y in ((10, 10), (48, 48), (60, 40), (30, 70)):
        radius_squared = (x - speck["center_x"]) ** 2 + (y - speck["center_y"]) ** 2
        expected = 1.0 - (1.0 - transmittance) * math.exp(-0.5 * radius_squared / radius**2)
        assert mask[y, x] == pytest.approx(expected, rel=1e-9)


def test_dust_attenuates_toward_the_configured_transmittance_at_its_centre() -> None:
    transmittance, radius = 0.2, 6.0
    params = DefectParams(
        dust_count=1,
        dust_radius_px_min=radius,
        dust_radius_px_max=radius,
        dust_transmittance=transmittance,
        scratch_count=0,
        scratch_sigma_px=0.0,
        scratch_amplitude_fraction=0.0,
        scratch_length_px_min=0.0,
        scratch_length_px_max=0.0,
    )
    xs, ys = coordinate_grids(96, 96)
    mask, specks = render_dust(xs, ys, params, make_rng(31, "defect-test"))
    centre_x = int(round(specks[0]["center_x"]))
    centre_y = int(round(specks[0]["center_y"]))
    # Rounding to the pixel grid moves the sample point at most sqrt(0.5) px off centre.
    assert mask[centre_y, centre_x] == pytest.approx(transmittance, abs=0.02)
    assert float(mask.min()) == pytest.approx(mask[centre_y, centre_x], abs=0.02)


def test_recorded_scratch_geometry_reproduces_the_pixels_it_wrote() -> None:
    """Amplitude, cross-sectional sigma and endpoints must all explain the layer."""
    max_value, sigma, length, amplitude_fraction = 65535, 1.5, 100.0, 0.25
    params = DefectParams(
        dust_count=0,
        dust_radius_px_min=0.0,
        dust_radius_px_max=0.0,
        dust_transmittance=1.0,
        scratch_count=1,
        scratch_sigma_px=sigma,
        scratch_amplitude_fraction=amplitude_fraction,
        scratch_length_px_min=length,
        scratch_length_px_max=length,
    )
    xs, ys = coordinate_grids(192, 192)
    layer, scratches = render_scratches(xs, ys, params, max_value, make_rng(37, "defect-test"))
    assert len(scratches) == 1
    scratch = scratches[0]
    assert scratch["kind"] == "scratch"
    assert scratch["sigma_px"] == pytest.approx(sigma)
    assert scratch["amplitude_dn"] == pytest.approx(amplitude_fraction * max_value)

    dx = scratch["x1"] - scratch["x0"]
    dy = scratch["y1"] - scratch["y0"]
    assert math.hypot(dx, dy) == pytest.approx(length)
    unit_x, unit_y = dx / length, dy / length
    normal_x, normal_y = -unit_y, unit_x
    midpoint_x = 0.5 * (scratch["x0"] + scratch["x1"])
    midpoint_y = 0.5 * (scratch["y0"] + scratch["y1"])

    checked = 0
    for along in (-20.0, 0.0, 20.0):
        for across in (0.0, 1.0, 2.0, 3.0):
            x = int(round(midpoint_x + along * unit_x + across * normal_x))
            y = int(round(midpoint_y + along * unit_y + across * normal_y))
            if not (0 <= x < 192 and 0 <= y < 192):
                continue
            # Distance to the infinite line; the projection stays inside the segment
            # for these sample points, so it equals the distance to the segment.
            projection = (x - scratch["x0"]) * unit_x + (y - scratch["y0"]) * unit_y
            assert 0.0 < projection < length
            distance = abs((x - scratch["x0"]) * normal_x + (y - scratch["y0"]) * normal_y)
            expected = scratch["amplitude_dn"] * math.exp(-0.5 * (distance / sigma) ** 2)
            assert layer[y, x] == pytest.approx(expected, rel=1e-9)
            checked += 1
    assert checked >= 8


def test_no_defect_level_leaves_the_image_untouched() -> None:
    params = DEFAULT_CONFIG.defects["none"]
    xs, ys = coordinate_grids(64, 64)
    mask, specks = render_dust(xs, ys, params, make_rng(41, "defect-test"))
    layer, scratches = render_scratches(xs, ys, params, 255, make_rng(41, "defect-test"))
    assert specks == [] and scratches == []
    assert np.all(mask == 1.0)
    assert np.all(layer == 0.0)


# ------------------------------------------------------------------- MODELS.md §7


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.49, 0), (0.5, 1), (1.5, 2), (2.5, 3), (-0.4, 0), (-3.0, 0), (254.6, 255), (300.0, 255)],
)
def test_quantization_rounds_half_up_and_clips_to_full_scale(value: float, expected: int) -> None:
    """``floor(x + 0.5)`` then clip -- not banker's rounding, which ties to even."""
    result = quantize(np.full((1, 1), value), 8)
    assert int(result[0, 0]) == expected
    assert result.dtype == np.uint8


# ------------------------------------------------------------------- MODELS.md §6


@pytest.mark.parametrize("photon_full_well", [512.0, 4096.0])
@pytest.mark.parametrize("max_value", [255, 65535])
def test_noise_variance_matches_the_documented_gain_and_read_noise(
    photon_full_well: float, max_value: int
) -> None:
    """Var = gain*level + (read_noise_fraction*M)^2 with gain = M/photon_full_well."""
    params = NoiseParams(
        shot_noise_enabled=True,
        photon_full_well=photon_full_well,
        read_noise_fraction=0.012,
    )
    level = 0.30 * max_value
    noisy = apply_noise(
        np.full(NOISE_SAMPLE_SHAPE, level), params, max_value, make_rng(11, "noise-test")
    )
    gain = max_value / photon_full_well
    expected_variance = gain * level + (params.read_noise_fraction * max_value) ** 2
    assert float(noisy.mean()) == pytest.approx(level, rel=0.01)
    assert float(noisy.var()) == pytest.approx(expected_variance, rel=NOISE_VARIANCE_TOLERANCE)


def test_read_noise_alone_matches_its_configured_sigma() -> None:
    max_value = 65535
    params = NoiseParams(
        shot_noise_enabled=False, photon_full_well=512.0, read_noise_fraction=0.004
    )
    level = 0.5 * max_value
    noisy = apply_noise(
        np.full(NOISE_SAMPLE_SHAPE, level), params, max_value, make_rng(13, "noise-test")
    )
    assert float(noisy.mean()) == pytest.approx(level, rel=0.001)
    assert float(noisy.std()) == pytest.approx(
        params.read_noise_fraction * max_value, rel=NOISE_VARIANCE_TOLERANCE
    )


def test_shot_noise_is_signal_dependent() -> None:
    """Doubling the signal doubles the shot-noise variance; read noise is constant."""
    max_value = 65535
    params = NoiseParams(
        shot_noise_enabled=True, photon_full_well=512.0, read_noise_fraction=0.0
    )
    rng_label = "noise-test"
    low = apply_noise(
        np.full(NOISE_SAMPLE_SHAPE, 0.2 * max_value), params, max_value, make_rng(17, rng_label)
    )
    high = apply_noise(
        np.full(NOISE_SAMPLE_SHAPE, 0.4 * max_value), params, max_value, make_rng(19, rng_label)
    )
    assert float(high.var()) / float(low.var()) == pytest.approx(2.0, rel=NOISE_VARIANCE_TOLERANCE)


def test_disabling_shot_noise_removes_exactly_the_poisson_term() -> None:
    max_value = 255
    level = 0.5 * max_value
    with_shot = NoiseParams(
        shot_noise_enabled=True, photon_full_well=512.0, read_noise_fraction=0.0
    )
    without_shot = NoiseParams(
        shot_noise_enabled=False, photon_full_well=512.0, read_noise_fraction=0.0
    )
    noisy = apply_noise(
        np.full(NOISE_SAMPLE_SHAPE, level), with_shot, max_value, make_rng(23, "noise-test")
    )
    clean = apply_noise(
        np.full(NOISE_SAMPLE_SHAPE, level), without_shot, max_value, make_rng(23, "noise-test")
    )
    assert float(noisy.var()) > 0.0
    assert float(clean.var()) == 0.0
    assert np.all(clean == level)
