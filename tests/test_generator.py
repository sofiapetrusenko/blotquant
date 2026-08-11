"""Generator behaviour: matrix coverage, the ground-truth intensity contract, QC
labels, and the failure modes that must be loud."""

from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path

import numpy as np
import pytest

from evals.metrics import BoundingBox
from evals.metrics import iou as metrics_iou
from synth.config import (
    DEFAULT_CONFIG,
    BackgroundParams,
    GeneratorConfig,
    NoiseParams,
    max_value_for_bit_depth,
    require_level,
)
from synth.encoding import extension_for, read_image, write_image
from synth.errors import (
    ConfigError,
    GoldSetMismatchError,
    RenderError,
    UnsupportedBitDepthError,
    UnsupportedFormatError,
)
from synth.generator import (
    _remove_stale,
    box_iou,
    generate_dataset,
    generate_image,
    plan_bands,
)
from synth.matrix import SPLITS, MatrixCell, balanced_permutation, build_matrix
from synth.render import (
    GelGeometry,
    PixelBox,
    apply_noise,
    band_bbox,
    coordinate_grids,
    quantize,
    render_background,
    render_band,
    render_dust,
    render_scratches,
)
from synth.rng import derive_seed, make_rng

MASTER_SEED = 42
FIXTURE_SEED = 7

# The default config with both noise levels stripped of noise. Combined with a flat
# background, no defects and no geometric distortion, this is the only setting in
# which a plain box sum over the ROI must reproduce the recorded true intensity.
_SILENT = NoiseParams(shot_noise_enabled=False, photon_full_well=4096.0, read_noise_fraction=0.0)
NOISELESS_CONFIG = dataclasses.replace(
    DEFAULT_CONFIG,
    noises={"low": _SILENT, "high": _SILENT},
)
QUANTIZATION_TOLERANCE_PERCENT = 0.5

# Ground truth is serialised with `json_float_decimals` decimals, so a value read back
# from it can differ from the same value computed in full precision by half a unit in
# the last recorded place. Derived from the config, not written as a literal.
SERIALISED_FLOAT_TOLERANCE = 0.5 * 10.0**-DEFAULT_CONFIG.json_float_decimals


def fixture_cell(**overrides: object) -> MatrixCell:
    """Return a fully specified matrix cell for numeric fixtures."""
    levels: dict[str, object] = {
        "format_depth": "tiff16",
        "background": "flat",
        "noise": "low",
        "band_shape": "sharp",
        "lane_geometry": "straight",
        "defect": "none",
        "exposure": "normal",
        "lane_count": 6,
    }
    levels.update(overrides)
    return MatrixCell.from_levels(
        DEFAULT_CONFIG.matrix,
        image_id="fixture",
        split="dev",
        index_in_split=0,
        levels=levels,
    )


# --------------------------------------------------------------------------- matrix


def test_matrix_has_the_split_sizes_the_plan_requires() -> None:
    cells = build_matrix(DEFAULT_CONFIG, MASTER_SEED)
    assert len(cells) == 40
    assert sum(1 for cell in cells if cell.split == "dev") == 30
    assert sum(1 for cell in cells if cell.split == "test") == 10
    assert len({cell.image_id for cell in cells}) == len(cells)


def test_every_axis_level_appears_in_every_split() -> None:
    cells = build_matrix(DEFAULT_CONFIG, MASTER_SEED)
    for split in SPLITS:
        levels_in_split = [cell.levels() for cell in cells if cell.split == split]
        for axis, levels in DEFAULT_CONFIG.matrix.axis_levels().items():
            seen = {entry[axis] for entry in levels_in_split}
            assert seen == set(levels), f"{split} split misses {set(levels) - seen} on axis {axis}"


def test_matrix_is_a_pure_function_of_the_seed() -> None:
    assert build_matrix(DEFAULT_CONFIG, MASTER_SEED) == build_matrix(DEFAULT_CONFIG, MASTER_SEED)
    assert build_matrix(DEFAULT_CONFIG, MASTER_SEED) != build_matrix(DEFAULT_CONFIG, 43)


def test_jpeg_cells_are_always_eight_bit() -> None:
    for cell in build_matrix(DEFAULT_CONFIG, MASTER_SEED):
        if cell.image_format == "jpeg":
            assert cell.bit_depth == 8


def test_balanced_permutation_is_balanced_and_deterministic() -> None:
    levels = ("a", "b", "c")
    first = balanced_permutation(levels, 10, seed=123)
    assert first == balanced_permutation(levels, 10, seed=123)
    counts = {level: first.count(level) for level in levels}
    assert sorted(counts.values()) == [3, 3, 4]


def test_balanced_permutation_refuses_a_split_too_small_to_cover_the_axis() -> None:
    with pytest.raises(ConfigError, match="smaller than"):
        balanced_permutation(("a", "b", "c"), 2, seed=1)


def test_cells_resolve_container_and_depth_from_the_configured_pairs() -> None:
    for level, (container, depth) in DEFAULT_CONFIG.matrix.format_depths.items():
        cell = fixture_cell(format_depth=level)
        assert (cell.image_format, cell.bit_depth) == (container, depth)


def test_a_cell_without_a_level_for_every_axis_raises() -> None:
    with pytest.raises(ConfigError, match="no level for axes"):
        MatrixCell.from_levels(
            DEFAULT_CONFIG.matrix,
            image_id="fixture",
            split="dev",
            index_in_split=0,
            levels={"format_depth": "tiff8"},
        )


def test_an_unknown_format_depth_level_raises() -> None:
    with pytest.raises(ConfigError, match="format_depth level 'nebulous'"):
        fixture_cell(format_depth="nebulous")


# ------------------------------------------------------------ ground-truth contract


def test_roi_box_sum_recovers_the_recorded_true_intensity() -> None:
    """A noiseless, flat-background image is the numeric check on the truth contract.

    Bands flagged as overlapping are excluded: their ROIs contain a neighbour's
    signal, so a box sum is expected to over-read there.
    """
    for band_shape in ("sharp", "smeared", "doublet"):
        for format_depth in ("tiff8", "tiff16"):
            cell = fixture_cell(band_shape=band_shape, format_depth=format_depth)
            generated = generate_image(cell, NOISELESS_CONFIG, FIXTURE_SEED)
            max_value = max_value_for_bit_depth(cell.bit_depth)
            background_dn = NOISELESS_CONFIG.backgrounds["flat"].base_fraction * max_value
            pixels = generated.pixels.astype(np.float64)
            checked = 0
            for band in generated.ground_truth["bands"]:
                if band["overlapping_band_ids"]:
                    continue
                roi = band["roi"]
                window = pixels[
                    roi["y"] : roi["y"] + roi["height"], roi["x"] : roi["x"] + roi["width"]
                ]
                measured = float((window - background_dn).sum())
                truth = band["true_integrated_intensity_dn"]
                error_percent = abs(measured - truth) / truth * 100.0
                assert error_percent < QUANTIZATION_TOLERANCE_PERCENT, (
                    f"{band['band_id']} ({band_shape}, {format_depth}): {error_percent:.3f}%"
                )
                checked += 1
            assert checked > 0


def test_true_ratio_equals_the_quotient_of_the_recorded_intensities() -> None:
    generated = generate_image(fixture_cell(), NOISELESS_CONFIG, FIXTURE_SEED)
    bands = {band["band_id"]: band for band in generated.ground_truth["bands"]}
    for ratio in generated.ground_truth["normalization"]["ratios"]:
        expected = (
            bands[ratio["numerator_band_id"]]["true_integrated_intensity_dn"]
            / bands[ratio["denominator_band_id"]]["true_integrated_intensity_dn"]
        )
        assert ratio["true_ratio"] == pytest.approx(expected, rel=1e-5)


def test_target_amplitudes_follow_the_configured_lane_pattern() -> None:
    cell = fixture_cell(lane_count=6)
    generated = generate_image(cell, NOISELESS_CONFIG, FIXTURE_SEED)
    peak = generated.ground_truth["generation"]["parameters"]["derived"]["reference_peak_dn"]
    targets = [
        band["amplitude_dn"]
        for band in sorted(generated.ground_truth["bands"], key=lambda item: item["lane_index"])
        if band["role"] == "target"
    ]
    expected = [
        peak * level for level in NOISELESS_CONFIG.band_layout.target_relative_levels[:6]
    ]
    assert targets == pytest.approx(expected, rel=1e-6)


# ------------------------------------------------------------------ band geometry


def test_sigma_y_is_derived_from_sigma_x_and_the_configured_aspect_ratio() -> None:
    """``sigma_y = sigma_x / aspect_ratio`` (MODELS.md §4), for every shape level."""
    for band_shape in DEFAULT_CONFIG.matrix.band_shapes:
        aspect_ratio = DEFAULT_CONFIG.band_shapes[band_shape].aspect_ratio
        for lane_count in DEFAULT_CONFIG.matrix.lane_counts:
            cell = fixture_cell(band_shape=band_shape, lane_count=lane_count)
            ground_truth = generate_image(cell, DEFAULT_CONFIG, FIXTURE_SEED).ground_truth
            derived = ground_truth["generation"]["parameters"]["derived"]
            expected_sigma_x = (
                DEFAULT_CONFIG.band_layout.sigma_x_fraction_of_pitch * derived["lane_pitch_px"]
            )
            assert derived["band_sigma_x_px"] == pytest.approx(
                expected_sigma_x, abs=SERIALISED_FLOAT_TOLERANCE
            )
            assert derived["band_sigma_y_px"] == pytest.approx(
                expected_sigma_x / aspect_ratio, abs=SERIALISED_FLOAT_TOLERANCE
            )
            for band in ground_truth["bands"]:
                assert band["sigma_x_px"] == derived["band_sigma_x_px"]
                assert band["sigma_y_px"] == derived["band_sigma_y_px"]


def test_band_aspect_ratio_is_invariant_across_lane_counts() -> None:
    """The point of deriving sigma_y from sigma_x: the shape does not follow the pitch.

    ``sigma_x`` scales with lane pitch, so an absolute ``sigma_y`` would make a band
    rounder at 6 lanes than at 4. Every band of a level must report the same ratio.
    """
    for band_shape in DEFAULT_CONFIG.matrix.band_shapes:
        expected = DEFAULT_CONFIG.band_shapes[band_shape].aspect_ratio
        ratios = set()
        for lane_count in DEFAULT_CONFIG.matrix.lane_counts:
            cell = fixture_cell(band_shape=band_shape, lane_count=lane_count)
            ground_truth = generate_image(cell, DEFAULT_CONFIG, FIXTURE_SEED).ground_truth
            for band in ground_truth["bands"]:
                ratio = band["sigma_x_px"] / band["sigma_y_px"]
                assert ratio == pytest.approx(expected, rel=1e-6), band["band_id"]
                ratios.add(round(ratio, 6))
        assert len(ratios) == 1, f"{band_shape} is not lane-count invariant: {ratios}"


def test_every_band_of_every_matrix_cell_clears_the_minimum_aspect_ratio() -> None:
    """The guarantee, asserted over the whole difficulty matrix, not a sample.

    A western blot band is constrained laterally by its lane and is always
    substantially wider than tall; a near-circular band is a dot-blot artifact.
    """
    minimum = DEFAULT_CONFIG.band_layout.min_aspect_ratio
    checked = 0
    for cell in build_matrix(DEFAULT_CONFIG, MASTER_SEED):
        for band in generate_image(cell, DEFAULT_CONFIG, MASTER_SEED).ground_truth["bands"]:
            ratio = band["sigma_x_px"] / band["sigma_y_px"]
            assert ratio >= minimum, f"{cell.image_id}/{band['band_id']}: {ratio:.4f}"
            checked += 1
    assert checked > 0


def test_smeared_bands_stay_more_diffuse_than_sharp_ones() -> None:
    """The levels must remain qualitatively distinct, not merely all above the floor.

    Asserted on rendered pixels, not on the config: the ROI height a band's own
    threshold contour produces is what a pipeline actually sees. The expected spread
    is derived from the configured aspect ratios, so tuning a level moves the
    expectation with it rather than silently weakening the check.
    """
    shapes = DEFAULT_CONFIG.band_shapes
    for lane_count in DEFAULT_CONFIG.matrix.lane_counts:
        heights: dict[str, int] = {}
        for band_shape in ("sharp", "doublet", "smeared"):
            cell = fixture_cell(band_shape=band_shape, lane_count=lane_count)
            ground_truth = generate_image(cell, NOISELESS_CONFIG, FIXTURE_SEED).ground_truth
            primary = next(band for band in ground_truth["bands"] if band["role"] == "target")
            heights[band_shape] = primary["roi"]["height"]
        assert heights["smeared"] > heights["doublet"] > heights["sharp"], lane_count

        # Height scales as 1/aspect_ratio, up to the ±1 px the integer bbox adds to each.
        expected = shapes["sharp"].aspect_ratio / shapes["smeared"].aspect_ratio
        measured = heights["smeared"] / heights["sharp"]
        assert measured == pytest.approx(expected, abs=2.0 / heights["sharp"]), lane_count

        # A hard floor, independent of the config: the check above tracks whatever the
        # aspect ratios happen to say, so it would still pass if the two levels were
        # retuned until they were nearly the same shape. That is the failure this test
        # is named for, so it gets an assertion that does not move with the config.
        assert heights["smeared"] >= 1.5 * heights["sharp"], (
            f"lane_count={lane_count}: the band_shape axis has collapsed, "
            f"smeared {heights['smeared']} px vs sharp {heights['sharp']} px"
        )


# --------------------------------------------------------------------- doublet shape


def test_doublet_partner_sits_at_the_configured_multiple_of_sigma_y() -> None:
    """The offset is in units of sigma_y, so it scales with the band, not the canvas."""
    shape = DEFAULT_CONFIG.band_shapes["doublet"]
    for lane_count in DEFAULT_CONFIG.matrix.lane_counts:
        cell = fixture_cell(band_shape="doublet", lane_count=lane_count)
        ground_truth = generate_image(cell, DEFAULT_CONFIG, FIXTURE_SEED).ground_truth
        bands = {band["band_id"]: band for band in ground_truth["bands"]}
        for lane_index in range(lane_count):
            primary = bands[f"fixture_L{lane_index}_target"]
            partner = bands[f"fixture_L{lane_index}_target_secondary"]
            offset_px = partner["nominal_center_y_px"] - primary["nominal_center_y_px"]
            assert offset_px == pytest.approx(
                shape.doublet_offset_sigma * primary["sigma_y_px"], rel=1e-6
            )
            assert offset_px / primary["sigma_y_px"] == pytest.approx(
                shape.doublet_offset_sigma, rel=1e-6
            )
            assert partner["amplitude_dn"] == pytest.approx(
                primary["amplitude_dn"] * shape.doublet_amplitude_ratio, rel=1e-6
            )
            assert partner["roi"]["y"] > primary["roi"]["y"]
            assert "overlapping" in primary["qc_flags"]
            assert "overlapping" in partner["qc_flags"]


def test_doublet_renders_a_resolved_shoulder_toward_its_partner() -> None:
    """The partner must be present in the rendered layer at every lane count.

    Measured as asymmetry of the lane-centre column about the primary band's centre:
    one ``doublet_offset_sigma`` *below* the centre there is only the primary's own
    tail, while the same distance *above* it sits the partner's peak.

    Scope: this runs on a noiseless, straight, defect-free fixture at the brightest
    lane, so it asserts the *model*, not the delivered set. In the committed images the
    same probe is noise-limited and the partner is not always visible pixel by pixel --
    ``synth/MODELS.md`` §4 states the measured range.
    """
    shape = DEFAULT_CONFIG.band_shapes["doublet"]
    for lane_count in DEFAULT_CONFIG.matrix.lane_counts:
        cell = fixture_cell(band_shape="doublet", lane_count=lane_count)
        generated = generate_image(cell, NOISELESS_CONFIG, FIXTURE_SEED)
        derived = generated.ground_truth["generation"]["parameters"]["derived"]
        background_dn = (
            NOISELESS_CONFIG.backgrounds["flat"].base_fraction
            * generated.ground_truth["max_value"]
        )
        column = int(round(derived["lane_center_x_px"][0]))
        centre_y = derived["row_center_y_px"][0]
        offset_px = shape.doublet_offset_sigma * derived["band_sigma_y_px"]
        pixels = generated.pixels.astype(np.float64)
        above = pixels[int(round(centre_y - offset_px)), column] - background_dn
        below = pixels[int(round(centre_y + offset_px)), column] - background_dn
        assert below > 5.0 * above, f"lane_count={lane_count}: {below:.1f} vs {above:.1f}"


def _target_column_peak_count(cell: MatrixCell, config: GeneratorConfig) -> int:
    """Return the number of local maxima on the target row's lane-centre column."""
    generated = generate_image(cell, config, FIXTURE_SEED)
    derived = generated.ground_truth["generation"]["parameters"]["derived"]
    column = int(round(derived["lane_center_x_px"][0]))
    centre_y = derived["row_center_y_px"][0]
    sigma_y = derived["band_sigma_y_px"]
    first = int(centre_y - 4.0 * sigma_y)
    last = int(centre_y + 8.0 * sigma_y)
    profile = generated.pixels[first:last, column].astype(np.float64)
    return sum(
        1
        for index in range(1, len(profile) - 1)
        if profile[index] > profile[index - 1] and profile[index] >= profile[index + 1]
    )


def test_doublet_modality_is_governed_only_by_the_configured_offset() -> None:
    """At the configured 2.2 sigma the partner is a shoulder, not a second peak.

    Two Gaussians of amplitude ratio ``doublet_amplitude_ratio`` merge into a single
    maximum below 2.4605 sigma of separation in the continuous profile, and below 2.55
    sigma once sampled onto the pixel grid at all three lane counts, so the committed
    ``doublet`` level renders one peak with a pronounced shoulder. That is
    a property of ``doublet_offset_sigma`` alone: it is expressed in units of sigma_y,
    so it is unchanged by ``aspect_ratio`` and identical at every lane count. Raising
    only the offset splits the profile, which is what pins the cause here.
    """
    widened = dataclasses.replace(
        DEFAULT_CONFIG,
        band_shapes=dict(
            DEFAULT_CONFIG.band_shapes,
            doublet=dataclasses.replace(
                DEFAULT_CONFIG.band_shapes["doublet"], doublet_offset_sigma=2.8
            ),
        ),
        noises=NOISELESS_CONFIG.noises,
    )
    for lane_count in DEFAULT_CONFIG.matrix.lane_counts:
        cell = fixture_cell(band_shape="doublet", lane_count=lane_count)
        assert _target_column_peak_count(cell, NOISELESS_CONFIG) == 1, lane_count
        assert _target_column_peak_count(cell, widened) == 2, lane_count


def test_every_roi_lies_inside_the_canvas() -> None:
    for cell in build_matrix(DEFAULT_CONFIG, MASTER_SEED)[:6]:
        ground_truth = generate_image(cell, DEFAULT_CONFIG, MASTER_SEED).ground_truth
        for region in ground_truth["bands"] + ground_truth["lanes"]:
            roi = region["roi"]
            assert 0 <= roi["x"] and roi["x"] + roi["width"] <= ground_truth["width_px"]
            assert 0 <= roi["y"] and roi["y"] + roi["height"] <= ground_truth["height_px"]


def test_lane_roi_follows_the_configured_width_and_tilt_allowance() -> None:
    """The documented lane-ROI rule (MODELS.md §4), reconstructed from config alone."""
    canvas = DEFAULT_CONFIG.canvas
    lane_layout = DEFAULT_CONFIG.lane_layout
    for geometry_level in ("straight", "tilted", "smile"):
        cell = fixture_cell(lane_geometry=geometry_level)
        ground_truth = generate_image(cell, DEFAULT_CONFIG, FIXTURE_SEED).ground_truth
        derived = ground_truth["generation"]["parameters"]["derived"]
        pitch = derived["lane_pitch_px"]
        slope = abs(derived["tilt_slope"])
        half_width = lane_layout.roi_width_fraction_of_pitch * pitch / 2.0
        excursion = slope * lane_layout.roi_tilt_excursion_fraction_of_height * canvas.height_px
        for lane in ground_truth["lanes"]:
            center = lane["nominal_center_x_px"]
            roi = lane["roi"]
            assert roi["x"] == max(0, math.floor(center - half_width - excursion))
            assert roi["x"] + roi["width"] - 1 == min(
                canvas.width_px - 1, math.ceil(center + half_width + excursion)
            )
            assert roi["y"] == 0
            assert roi["height"] == canvas.height_px


def test_lane_roi_width_is_read_from_config_not_hard_coded() -> None:
    """Halving the configured width fraction must narrow every unclamped lane ROI."""
    cell = fixture_cell(lane_geometry="straight")
    narrow_config = dataclasses.replace(
        DEFAULT_CONFIG,
        lane_layout=dataclasses.replace(
            DEFAULT_CONFIG.lane_layout, roi_width_fraction_of_pitch=0.5
        ),
    )
    default_lanes = generate_image(cell, DEFAULT_CONFIG, FIXTURE_SEED).ground_truth["lanes"]
    narrow_lanes = generate_image(cell, narrow_config, FIXTURE_SEED).ground_truth["lanes"]
    pitch = DEFAULT_CONFIG.canvas.width_px - 2 * DEFAULT_CONFIG.canvas.margin_x_px
    pitch /= cell.lane_count
    for default_lane, narrow_lane in zip(default_lanes, narrow_lanes, strict=True):
        assert narrow_lane["roi"]["width"] < default_lane["roi"]["width"]
        assert narrow_lane["roi"]["width"] == pytest.approx(pitch / 2.0, abs=2.0)


def test_lane_roi_tilt_allowance_widens_only_tilted_cells() -> None:
    """The excursion term is proportional to |tilt_slope|, so smile cells are unwidened."""
    straight = generate_image(
        fixture_cell(lane_geometry="straight"), DEFAULT_CONFIG, FIXTURE_SEED
    ).ground_truth["lanes"]
    smile = generate_image(
        fixture_cell(lane_geometry="smile"), DEFAULT_CONFIG, FIXTURE_SEED
    ).ground_truth["lanes"]
    tilted = generate_image(
        fixture_cell(lane_geometry="tilted"), DEFAULT_CONFIG, FIXTURE_SEED
    ).ground_truth["lanes"]
    assert [lane["roi"] for lane in smile] == [lane["roi"] for lane in straight]
    interior = slice(1, -1)  # edge lanes are clamped to the canvas, so they widen less
    for tilted_lane, straight_lane in zip(tilted[interior], straight[interior], strict=True):
        assert tilted_lane["roi"]["width"] > straight_lane["roi"]["width"]


def test_lane_layout_is_echoed_into_every_ground_truth_file() -> None:
    ground_truth = generate_image(fixture_cell(), DEFAULT_CONFIG, FIXTURE_SEED).ground_truth
    echoed = ground_truth["generation"]["parameters"]["lane_layout"]
    assert echoed == dataclasses.asdict(DEFAULT_CONFIG.lane_layout)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("roi_width_fraction_of_pitch", 0.0),
        ("roi_width_fraction_of_pitch", -1.0),
        ("roi_tilt_excursion_fraction_of_height", -0.1),
    ],
)
def test_impossible_lane_roi_fractions_raise_at_construction(field: str, value: float) -> None:
    with pytest.raises(ConfigError, match=field):
        dataclasses.replace(DEFAULT_CONFIG.lane_layout, **{field: value})


def test_dust_attenuation_lowers_the_recorded_true_intensity() -> None:
    clean = generate_image(fixture_cell(defect="none"), NOISELESS_CONFIG, FIXTURE_SEED)
    dusty = generate_image(fixture_cell(defect="dust"), NOISELESS_CONFIG, FIXTURE_SEED)
    clean_total = sum(
        band["true_integrated_intensity_dn"] for band in clean.ground_truth["bands"]
    )
    dusty_total = sum(
        band["true_integrated_intensity_dn"] for band in dusty.ground_truth["bands"]
    )
    assert dusty_total < clean_total
    assert len(dusty.ground_truth["defects"]) == DEFAULT_CONFIG.defects["dust"].dust_count


def test_scratches_are_contaminants_not_band_signal() -> None:
    """A scratch changes the pixels but never the recorded band intensities."""
    clean = generate_image(fixture_cell(defect="none"), NOISELESS_CONFIG, FIXTURE_SEED)
    scratched = generate_image(fixture_cell(defect="scratch"), NOISELESS_CONFIG, FIXTURE_SEED)
    assert not np.array_equal(clean.pixels, scratched.pixels)
    for left, right in zip(
        clean.ground_truth["bands"], scratched.ground_truth["bands"], strict=True
    ):
        assert left["true_integrated_intensity_dn"] == right["true_integrated_intensity_dn"]


# ------------------------------------------------------------------------ QC labels


def test_saturating_exposure_clips_and_normal_exposure_does_not() -> None:
    saturating = generate_image(fixture_cell(exposure="saturating"), DEFAULT_CONFIG, FIXTURE_SEED)
    normal = generate_image(fixture_cell(exposure="normal"), DEFAULT_CONFIG, FIXTURE_SEED)

    saturated_bands = [
        band for band in saturating.ground_truth["bands"] if "saturated" in band["qc_flags"]
    ]
    assert saturated_bands
    for band in saturated_bands:
        assert band["clipped_pixel_count"] >= DEFAULT_CONFIG.qc.saturated_min_clipped_pixels
        assert band["observed_peak_dn"] == saturating.ground_truth["max_value"]
    assert "saturated" in saturating.ground_truth["image_qc_flags"]

    assert normal.ground_truth["image_stats"]["clipped_pixel_count"] == 0
    assert "saturated" not in normal.ground_truth["image_qc_flags"]


def test_low_exposure_is_the_only_low_dynamic_range_label() -> None:
    low = generate_image(fixture_cell(exposure="low"), DEFAULT_CONFIG, FIXTURE_SEED)
    normal = generate_image(fixture_cell(exposure="normal"), DEFAULT_CONFIG, FIXTURE_SEED)
    assert "low_dynamic_range" in low.ground_truth["image_qc_flags"]
    assert "low_dynamic_range" not in normal.ground_truth["image_qc_flags"]


def test_lossy_format_label_tracks_the_container() -> None:
    jpeg = generate_image(fixture_cell(format_depth="jpeg8"), DEFAULT_CONFIG, FIXTURE_SEED)
    png = generate_image(fixture_cell(format_depth="png8"), DEFAULT_CONFIG, FIXTURE_SEED)
    assert jpeg.ground_truth["lossy_format"] is True
    assert "lossy_format" in jpeg.ground_truth["image_qc_flags"]
    assert png.ground_truth["lossy_format"] is False
    assert "lossy_format" not in png.ground_truth["image_qc_flags"]


def test_overlap_label_agrees_with_the_configured_iou_threshold() -> None:
    doublet = generate_image(fixture_cell(band_shape="doublet"), DEFAULT_CONFIG, FIXTURE_SEED)
    sharp = generate_image(fixture_cell(band_shape="sharp"), DEFAULT_CONFIG, FIXTURE_SEED)

    boxes = {
        band["band_id"]: PixelBox(**band["roi"]) for band in doublet.ground_truth["bands"]
    }
    flagged = [
        band for band in doublet.ground_truth["bands"] if "overlapping" in band["qc_flags"]
    ]
    assert flagged
    for band in flagged:
        assert band["overlapping_band_ids"]
        for partner in band["overlapping_band_ids"]:
            assert (
                box_iou(boxes[band["band_id"]], boxes[partner])
                >= DEFAULT_CONFIG.qc.overlap_iou_threshold
            )
    for band in sharp.ground_truth["bands"]:
        assert band["overlapping_band_ids"] == []
        assert "overlapping" not in band["qc_flags"]


# -------------------------------------------------------------------- failure modes


@pytest.mark.parametrize("bit_depth", [1, 12, 24, 32, 0, -8])
def test_unsupported_bit_depth_raises_instead_of_squashing(bit_depth: int) -> None:
    with pytest.raises(UnsupportedBitDepthError, match="not supported"):
        max_value_for_bit_depth(bit_depth)
    with pytest.raises(UnsupportedBitDepthError):
        quantize(np.zeros((4, 4)), bit_depth)


def test_jpeg_refuses_sixteen_bit_data(tmp_path: Path) -> None:
    array = np.zeros((8, 8), dtype=np.uint16)
    with pytest.raises(UnsupportedFormatError, match="cannot carry 16-bit"):
        write_image(tmp_path / "x.jpg", array, "jpeg", 16, DEFAULT_CONFIG.encoding)


def test_unknown_container_format_raises() -> None:
    with pytest.raises(UnsupportedFormatError, match="unsupported image format"):
        extension_for("bmp")


def test_writer_rejects_a_dtype_that_contradicts_the_bit_depth(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedFormatError, match="requires a uint16 array"):
        write_image(
            tmp_path / "x.tiff", np.zeros((8, 8), dtype=np.uint8), "tiff", 16,
            DEFAULT_CONFIG.encoding,
        )


def test_reader_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="image not found"):
        read_image(tmp_path / "absent.tiff", "tiff", 8)


def test_missing_config_level_raises_with_the_known_levels(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="configured levels are"):
        require_level(DEFAULT_CONFIG.backgrounds, "nebulous", "background")
    with pytest.raises(ConfigError, match="band_shape level 'nebulous'"):
        generate_image(fixture_cell(band_shape="nebulous"), DEFAULT_CONFIG, FIXTURE_SEED)


def test_negative_background_raises_rather_than_being_clamped() -> None:
    xs, ys = coordinate_grids(16, 16)
    params = BackgroundParams(
        base_fraction=0.0,
        gradient_x_fraction=-0.5,
        gradient_y_fraction=0.0,
        blob_count=0,
        blob_sigma_px_min=0.0,
        blob_sigma_px_max=0.0,
        blob_amplitude_fraction_min=0.0,
        blob_amplitude_fraction_max=0.0,
        blob_bright_probability=0.0,
    )
    with pytest.raises(RenderError, match="negative DN"):
        render_background(xs, ys, params, 255, make_rng(1, "test"))


def test_poisson_stage_refuses_negative_input() -> None:
    with pytest.raises(RenderError, match="negative DN"):
        apply_noise(
            np.full((4, 4), -1.0),
            DEFAULT_CONFIG.noises["low"],
            255,
            make_rng(1, "test"),
        )


def test_band_rendering_rejects_non_positive_sigma() -> None:
    xs, ys = coordinate_grids(16, 16)
    geometry = GelGeometry(0.0, 0.0, 7.5, 7.5, 7.5)
    with pytest.raises(ConfigError, match="sigmas must be positive"):
        render_band(
            xs,
            ys,
            amplitude_dn=10.0,
            nominal_x=8.0,
            nominal_y=8.0,
            sigma_x_px=0.0,
            sigma_y_px=2.0,
            flat_top_exponent=4.0,
            geometry=geometry,
        )


def test_invisible_band_raises_instead_of_yielding_an_empty_roi() -> None:
    with pytest.raises(RenderError, match="no pixel above"):
        band_bbox(np.zeros((8, 8)), amplitude_dn=100.0, relative_threshold=0.05)


@pytest.mark.parametrize("threshold", [0.0, 1.0, -0.1, 1.5])
def test_roi_threshold_outside_the_unit_interval_raises(threshold: float) -> None:
    with pytest.raises(ConfigError, match="bbox_relative_threshold"):
        band_bbox(np.ones((8, 8)), amplitude_dn=1.0, relative_threshold=threshold)


def test_band_rendering_rejects_a_sub_gaussian_exponent() -> None:
    xs, ys = coordinate_grids(16, 16)
    with pytest.raises(ConfigError, match="flat_top_exponent"):
        render_band(
            xs,
            ys,
            amplitude_dn=10.0,
            nominal_x=8.0,
            nominal_y=8.0,
            sigma_x_px=2.0,
            sigma_y_px=2.0,
            flat_top_exponent=1.0,
            geometry=GelGeometry(0.0, 0.0, 7.5, 7.5, 7.5),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("photon_full_well", 0.0, "photon_full_well"),
        ("read_noise_fraction", -0.1, "read_noise_fraction"),
    ],
)
def test_invalid_noise_parameters_raise_before_any_random_draw(
    field: str, value: float, message: str
) -> None:
    params = dataclasses.replace(DEFAULT_CONFIG.noises["low"], **{field: value})
    with pytest.raises(ConfigError, match=message):
        apply_noise(np.zeros((4, 4)), params, 255, make_rng(1, "test"))


def test_impossible_dust_transmittance_raises() -> None:
    xs, ys = coordinate_grids(16, 16)
    params = dataclasses.replace(DEFAULT_CONFIG.defects["dust"], dust_transmittance=0.0)
    with pytest.raises(ConfigError, match="dust_transmittance"):
        render_dust(xs, ys, params, make_rng(1, "test"))


def test_scratch_without_a_cross_section_raises() -> None:
    xs, ys = coordinate_grids(16, 16)
    params = dataclasses.replace(DEFAULT_CONFIG.defects["scratch"], scratch_sigma_px=0.0)
    with pytest.raises(ConfigError, match="scratch_sigma_px"):
        render_scratches(xs, ys, params, 255, make_rng(1, "test"))


def test_empty_axis_and_impossible_canvas_raise() -> None:
    with pytest.raises(ConfigError, match="empty level list"):
        balanced_permutation((), 3, seed=1)
    with pytest.raises(ConfigError, match="canvas must be positive"):
        coordinate_grids(0, 8)
    with pytest.raises(ConfigError, match="leaves no room"):
        dataclasses.replace(
            DEFAULT_CONFIG,
            canvas=dataclasses.replace(DEFAULT_CONFIG.canvas, margin_x_px=200),
        )
    with pytest.raises(ConfigError, match="must not be empty"):
        dataclasses.replace(
            DEFAULT_CONFIG,
            band_layout=dataclasses.replace(
                DEFAULT_CONFIG.band_layout, target_relative_levels=()
            ),
        )


def test_derive_seed_rejects_bad_arguments() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        derive_seed(-1, "label")
    with pytest.raises(ValueError, match="non-empty"):
        derive_seed(1, "")
    with pytest.raises(TypeError, match="must be an int"):
        derive_seed("42", "label")  # type: ignore[arg-type]


def test_plan_bands_requires_the_target_and_reference_rows() -> None:
    canvas = dataclasses.replace(
        DEFAULT_CONFIG.canvas, row_y_fractions=(0.5,), row_roles=("mystery",)
    )
    config = dataclasses.replace(DEFAULT_CONFIG, canvas=canvas)
    with pytest.raises(ConfigError, match="row_roles must contain"):
        plan_bands(fixture_cell(), config, make_rng(1, "test"))


@pytest.mark.parametrize(
    "axis",
    ["backgrounds", "noises", "band_shapes", "lane_geometries", "defects", "exposures"],
)
def test_a_matrix_level_without_parameters_raises_at_construction(axis: str) -> None:
    """The mismatch must be caught by the config, not discovered mid-render."""
    matrix = dataclasses.replace(
        DEFAULT_CONFIG.matrix,
        **{axis: (*getattr(DEFAULT_CONFIG.matrix, axis), "nebulous")},
    )
    with pytest.raises(ConfigError, match=rf"matrix\.{axis} names levels with no parameters"):
        dataclasses.replace(DEFAULT_CONFIG, matrix=matrix)


@pytest.mark.parametrize("level", ["sharp", "smeared", "doublet"])
def test_a_band_shape_below_the_minimum_aspect_ratio_raises_at_construction(level: str) -> None:
    """A near-circular band is refused loudly, never clamped into range silently.

    ``min(aspect_ratio, min_aspect_ratio)`` would be exactly the silent fallback the
    project forbids: the config would then describe a shape the generator never
    rendered.
    """
    minimum = DEFAULT_CONFIG.band_layout.min_aspect_ratio
    too_round = dict(
        DEFAULT_CONFIG.band_shapes,
        **{
            level: dataclasses.replace(
                DEFAULT_CONFIG.band_shapes[level], aspect_ratio=minimum - 0.1
            )
        },
    )
    with pytest.raises(ConfigError, match=rf"band_shape level '{level}' has aspect_ratio"):
        dataclasses.replace(DEFAULT_CONFIG, band_shapes=too_round)


def test_the_minimum_aspect_ratio_is_read_from_config_not_hard_coded() -> None:
    """Raising the floor above a shipped level must be what makes it illegal."""
    tighter = dataclasses.replace(
        DEFAULT_CONFIG.band_layout,
        min_aspect_ratio=DEFAULT_CONFIG.band_shapes["smeared"].aspect_ratio + 0.1,
    )
    with pytest.raises(ConfigError, match="band_shape level 'smeared'"):
        dataclasses.replace(DEFAULT_CONFIG, band_layout=tighter)


@pytest.mark.parametrize("aspect_ratio", [0.0, -1.0, -0.5])
def test_a_non_positive_aspect_ratio_raises(aspect_ratio: float) -> None:
    with pytest.raises(ConfigError, match="aspect_ratio must be positive"):
        dataclasses.replace(DEFAULT_CONFIG.band_shapes["sharp"], aspect_ratio=aspect_ratio)


@pytest.mark.parametrize("minimum", [0.99, 0.0, -1.0])
def test_a_minimum_aspect_ratio_below_one_raises(minimum: float) -> None:
    """A floor under 1.0 would admit bands taller than wide, which no lane produces."""
    with pytest.raises(ConfigError, match="min_aspect_ratio must be at least 1.0"):
        dataclasses.replace(DEFAULT_CONFIG.band_layout, min_aspect_ratio=minimum)


def test_inconsistent_canvas_config_raises_at_construction() -> None:
    with pytest.raises(ConfigError, match="equal length"):
        dataclasses.replace(
            DEFAULT_CONFIG,
            canvas=dataclasses.replace(DEFAULT_CONFIG.canvas, row_roles=("target",)),
        )


# ------------------------------------------------------------------ dataset writing


def test_generate_dataset_writes_images_ground_truth_and_the_config_echo(tmp_path: Path) -> None:
    result = generate_dataset(tmp_path, MASTER_SEED, DEFAULT_CONFIG)
    assert len(result.images) == 40
    assert result.removed_files == ()
    for item in result.images:
        assert item.image_path.is_file()
        assert item.ground_truth_path.is_file()
    echo = json.loads((tmp_path / "generation_config.json").read_text(encoding="utf-8"))
    assert echo["master_seed"] == MASTER_SEED
    assert echo["config_digest"] == DEFAULT_CONFIG.digest()


def test_generate_dataset_refuses_to_overwrite_a_gold_set_from_another_seed(
    tmp_path: Path,
) -> None:
    generate_dataset(tmp_path, MASTER_SEED, DEFAULT_CONFIG)
    with pytest.raises(GoldSetMismatchError, match="--force"):
        generate_dataset(tmp_path, 43, DEFAULT_CONFIG)
    generate_dataset(tmp_path, 43, DEFAULT_CONFIG, force=True)


def test_generate_dataset_refuses_to_leave_stale_ground_truth_behind(tmp_path: Path) -> None:
    """A leftover file that this run would not rewrite, but is otherwise well formed."""
    generate_dataset(tmp_path, MASTER_SEED, DEFAULT_CONFIG)
    ground_truth_dir = tmp_path / "ground_truth"
    (ground_truth_dir / "dev_99.json").write_text(
        (ground_truth_dir / "dev_00.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    with pytest.raises(GoldSetMismatchError, match="would not rewrite"):
        generate_dataset(tmp_path, MASTER_SEED, DEFAULT_CONFIG)


@pytest.mark.parametrize(
    ("content", "expected_message"),
    [
        ("{}", "belongs to a gold set from seed None"),
        ('{"generation": "nope"}', "belongs to a gold set from seed None"),
        ("[]", "does not contain a JSON object"),
        ('{"generation": {"master_seed": 42', "could not be read as JSON"),
        ("", "could not be read as JSON"),
    ],
)
def test_generate_dataset_refuses_ground_truth_it_cannot_recognise(
    tmp_path: Path, content: str, expected_message: str
) -> None:
    """Corrupt or truncated ground truth must give an actionable error, not a traceback."""
    generate_dataset(tmp_path, MASTER_SEED, DEFAULT_CONFIG)
    (tmp_path / "ground_truth" / "dev_00.json").write_text(content, encoding="utf-8")
    with pytest.raises(GoldSetMismatchError, match=expected_message):
        generate_dataset(tmp_path, MASTER_SEED, DEFAULT_CONFIG)


def test_a_config_edit_is_caught_even_with_the_ground_truth_deleted(tmp_path: Path) -> None:
    """A config-only change alters no filename, so the config echo is the only witness."""
    generate_dataset(tmp_path, MASTER_SEED, DEFAULT_CONFIG)
    for path in (tmp_path / "ground_truth").glob("*.json"):
        path.unlink()
    modified = dataclasses.replace(
        DEFAULT_CONFIG,
        qc=dataclasses.replace(DEFAULT_CONFIG.qc, overlap_iou_threshold=0.9),
    )
    with pytest.raises(GoldSetMismatchError, match="SYNTH_VERSION"):
        generate_dataset(tmp_path, MASTER_SEED, modified)


def test_a_reseed_is_caught_even_with_the_ground_truth_deleted(tmp_path: Path) -> None:
    generate_dataset(tmp_path, MASTER_SEED, DEFAULT_CONFIG)
    for path in (tmp_path / "ground_truth").glob("*.json"):
        path.unlink()
    with pytest.raises(GoldSetMismatchError, match="belongs to a gold set from seed 42"):
        generate_dataset(tmp_path, 43, DEFAULT_CONFIG)


def test_a_stale_subdirectory_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    """The refusal must precede the write loop, with or without --force.

    Aborting mid-write would leave ground truth describing the new seed next to a
    config echo and orphaned images describing the old one.
    """
    generate_dataset(tmp_path, MASTER_SEED, DEFAULT_CONFIG)
    (tmp_path / "images" / "old").mkdir()
    ground_truth_dir = tmp_path / "ground_truth"
    before_ground_truth = {
        path.name: path.read_text(encoding="utf-8") for path in ground_truth_dir.glob("*.json")
    }
    before_images = {path.name for path in (tmp_path / "images").iterdir()}
    before_echo = (tmp_path / "generation_config.json").read_text(encoding="utf-8")

    for force in (False, True):
        with pytest.raises(GoldSetMismatchError, match="subdirectories this run would not"):
            generate_dataset(tmp_path, 43, DEFAULT_CONFIG, force=force)

    assert {
        path.name: path.read_text(encoding="utf-8") for path in ground_truth_dir.glob("*.json")
    } == before_ground_truth
    assert {path.name for path in (tmp_path / "images").iterdir()} == before_images
    assert (tmp_path / "generation_config.json").read_text(encoding="utf-8") == before_echo


def test_remove_stale_refuses_a_directory_even_if_the_guard_is_bypassed(tmp_path: Path) -> None:
    (tmp_path / "leftover").mkdir()
    with pytest.raises(GoldSetMismatchError, match="--force will not delete directories"):
        _remove_stale(tmp_path, expected=set())


def test_generate_dataset_refuses_to_leave_stale_images_behind(tmp_path: Path) -> None:
    """A reseed changes containers, so orphaned images are the real corruption mode."""
    generate_dataset(tmp_path, MASTER_SEED, DEFAULT_CONFIG)
    (tmp_path / "images" / "dev_00.bmp").write_bytes(b"stale")
    with pytest.raises(GoldSetMismatchError, match="would not rewrite"):
        generate_dataset(tmp_path, MASTER_SEED, DEFAULT_CONFIG)


def test_a_forced_reseed_leaves_exactly_one_image_per_ground_truth_file(
    tmp_path: Path,
) -> None:
    first = generate_dataset(tmp_path, MASTER_SEED, DEFAULT_CONFIG)
    second = generate_dataset(tmp_path, 43, DEFAULT_CONFIG, force=True)
    ground_truth_files = sorted((tmp_path / "ground_truth").glob("*.json"))
    image_files = sorted(path for path in (tmp_path / "images").iterdir() if path.is_file())
    assert len(ground_truth_files) == len(second.images) == 40
    assert {path.name for path in image_files} == {
        item.image_path.name for item in second.images
    }
    # Whatever was deleted is reported, never silently dropped.
    orphans = {item.image_path.name for item in first.images} - {
        item.image_path.name for item in second.images
    }
    assert set(second.removed_files) == orphans
    assert orphans


def test_deleting_the_ground_truth_does_not_open_a_back_door_to_the_images(
    tmp_path: Path,
) -> None:
    """With every provenance record gone, the orphan check alone must still refuse."""
    first = generate_dataset(tmp_path, MASTER_SEED, DEFAULT_CONFIG)
    before = {path.name for path in (tmp_path / "images").iterdir()}
    for path in (tmp_path / "ground_truth").glob("*.json"):
        path.unlink()
    (tmp_path / "generation_config.json").unlink()

    with pytest.raises(GoldSetMismatchError, match="would not rewrite"):
        generate_dataset(tmp_path, 43, DEFAULT_CONFIG)

    assert {path.name for path in (tmp_path / "images").iterdir()} == before
    assert len(first.images) == 40


def test_regenerating_the_same_set_after_losing_the_ground_truth_is_allowed(
    tmp_path: Path,
) -> None:
    """Same seed and config: no orphan can result, so the run proceeds."""
    generate_dataset(tmp_path, MASTER_SEED, DEFAULT_CONFIG)
    for path in (tmp_path / "ground_truth").glob("*.json"):
        path.unlink()
    result = generate_dataset(tmp_path, MASTER_SEED, DEFAULT_CONFIG)
    assert len(result.images) == 40
    assert result.removed_files == ()


def test_generate_dataset_refuses_a_config_change_without_a_version_bump(
    tmp_path: Path,
) -> None:
    generate_dataset(tmp_path, MASTER_SEED, DEFAULT_CONFIG)
    modified = dataclasses.replace(
        DEFAULT_CONFIG,
        qc=dataclasses.replace(DEFAULT_CONFIG.qc, overlap_iou_threshold=0.9),
    )
    with pytest.raises(GoldSetMismatchError, match="SYNTH_VERSION"):
        generate_dataset(tmp_path, MASTER_SEED, modified)


def test_generator_and_eval_iou_agree() -> None:
    """The label-making IoU and the score-making IoU must not drift apart.

    ``evals/`` may not import ``synth/``, so the two implementations are separate;
    this pins them to the same numbers.
    """
    boxes = [
        PixelBox(x=0, y=0, width=10, height=10),
        PixelBox(x=5, y=0, width=10, height=10),
        PixelBox(x=0, y=0, width=10, height=5),
        PixelBox(x=3, y=2, width=7, height=9),
        PixelBox(x=100, y=100, width=4, height=4),
    ]
    for first in boxes:
        for second in boxes:
            expected = metrics_iou(
                BoundingBox(**dataclasses.asdict(first)),
                BoundingBox(**dataclasses.asdict(second)),
            )
            assert box_iou(first, second) == pytest.approx(expected)


def test_config_digest_changes_when_any_parameter_changes() -> None:
    modified = dataclasses.replace(
        DEFAULT_CONFIG,
        qc=dataclasses.replace(DEFAULT_CONFIG.qc, overlap_iou_threshold=0.9),
    )
    assert modified.digest() != DEFAULT_CONFIG.digest()


def test_config_digest_covers_the_difficulty_matrix_axes() -> None:
    """An axis edit changes the dataset, so the freeze guard must see it."""
    dropped_level = dataclasses.replace(
        DEFAULT_CONFIG,
        matrix=dataclasses.replace(DEFAULT_CONFIG.matrix, lane_counts=(4, 5)),
    )
    reordered_pairs = dataclasses.replace(
        DEFAULT_CONFIG,
        matrix=dataclasses.replace(
            DEFAULT_CONFIG.matrix,
            format_depths=dict(reversed(list(DEFAULT_CONFIG.matrix.format_depths.items()))),
        ),
    )
    assert dropped_level.digest() != DEFAULT_CONFIG.digest()
    assert reordered_pairs.digest() != DEFAULT_CONFIG.digest()
