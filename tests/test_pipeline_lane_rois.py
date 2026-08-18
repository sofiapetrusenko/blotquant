"""Caller-supplied lane ROIs: parsing, validation, and what the result records about them.

The point of the feature is a provenance one, so most of these assertions are about the
document rather than about the pixels: a lane the caller chose must never come back
described as something the detector found.

The minimum lane extent is the exception, and it gets its own section. It is a *derived*
bound -- ``max(NOISE_ESTIMATOR_MIN_SAMPLES, profile_smoothing_px)`` -- and its two halves fail
in opposite ways. Below the first, :func:`pipeline.detect.profile_noise_sigma` raises and the
failure is impossible to miss. Below the second, everything runs and the sigma every detection
threshold is compared against describes a noise reduction that never happened: silent, and
therefore the half that has to be pinned by a test of the *reason* rather than of the number.
Those tests measure the smoothing operator directly, so removing the ``profile_smoothing_px``
term from the bound cannot pass them.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from jsonschema import Draft202012Validator

from pipeline.__main__ import main
from pipeline.analyze import analyze_image
from pipeline.config import DetectionConfig, load_config
from pipeline.detect import (
    NOISE_ESTIMATOR_MIN_SAMPLES,
    ROI_SOURCES,
    DetectedLane,
    Roi,
    caller_lanes,
    detect,
    minimum_lane_extent_px,
    parse_lane_roi,
    parse_lane_rois,
    profile_noise_sigma,
    smooth_profile,
    validate_lane_rois,
)
from pipeline.errors import DetectionError, LaneRoiError
from tests.conftest import Blot
from tests.test_pipeline_config import CONFIG_DIR

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schema" / "result.schema.json"

# The fixture blot is 200x120 px with lanes centred at 40, 100 and 160.
FIXTURE_WIDTH_PX = 200
FIXTURE_HEIGHT_PX = 120


def _detection_config() -> DetectionConfig:
    """Return the shipped default detection parameters.

    Validation reads them because the minimum lane extent is derived from
    ``profile_smoothing_px``; every assertion below takes the bound from
    :func:`minimum_lane_extent_px` rather than restating a number this config could change.
    """
    return load_config(CONFIG_DIR / "default.yaml").detection


@pytest.fixture
def blot_png(tmp_path: Path, three_lane_blot: Blot) -> Path:
    """Write the three-lane fixture as a 16-bit PNG and return its path."""
    path = tmp_path / "fixture_blot.png"
    assert cv2.imwrite(str(path), three_lane_blot.pixels)
    return path


def _schema_errors(document: dict[str, Any]) -> list[str]:
    """Return the schema's complaints about ``document``, as written and nothing relaxed."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return [
        f"{list(error.path)}: {error.message}"
        for error in Draft202012Validator(schema).iter_errors(document)
    ]


def test_a_lane_roi_outside_the_image_names_which_one_and_the_image_size() -> None:
    """The message has to identify the rectangle: a caller who supplied four cannot guess."""
    rois = [Roi(0, 0, 60, 120), Roi(70, 0, 60, 120), Roi(170, 0, 60, 120)]

    with pytest.raises(LaneRoiError) as raised:
        validate_lane_rois(rois, FIXTURE_WIDTH_PX, FIXTURE_HEIGHT_PX, _detection_config())

    message = str(raised.value)
    assert "lane ROI 3" in message
    assert "x=170, y=0, width=60, height=120" in message
    assert "200x120" in message


def test_a_lane_roi_taller_than_the_image_is_refused() -> None:
    """Bounds are checked in both axes, not only the one lanes usually run out of."""
    with pytest.raises(LaneRoiError, match=r"lane ROI 1 .*is not wholly inside"):
        validate_lane_rois(
            [Roi(0, 100, 60, 40)], FIXTURE_WIDTH_PX, FIXTURE_HEIGHT_PX, _detection_config()
        )


def test_overlapping_lane_rois_name_both_rectangles() -> None:
    """Shared pixels would be counted into two lanes' total-protein integrals."""
    rois = [Roi(0, 0, 60, 120), Roi(50, 0, 60, 120)]

    with pytest.raises(LaneRoiError) as raised:
        validate_lane_rois(rois, FIXTURE_WIDTH_PX, FIXTURE_HEIGHT_PX, _detection_config())

    message = str(raised.value)
    assert "lane ROI 1" in message
    assert "lane ROI 2" in message
    assert "10x120" in message


def test_lane_rois_that_merely_touch_do_not_overlap() -> None:
    """Adjacent lanes share a boundary, not a pixel; the check is on area, not on edges."""
    validate_lane_rois(
        [Roi(0, 0, 60, 120), Roi(60, 0, 60, 120)],
        FIXTURE_WIDTH_PX,
        FIXTURE_HEIGHT_PX,
        _detection_config(),
    )


def test_supplying_no_lane_roi_at_all_is_refused_by_the_validator() -> None:
    """An empty supplied list means detection was switched off and nothing replaced it."""
    with pytest.raises(LaneRoiError, match="no lane ROI was supplied"):
        validate_lane_rois([], FIXTURE_WIDTH_PX, FIXTURE_HEIGHT_PX, _detection_config())


def test_a_lane_roi_too_short_for_a_row_profile_names_the_side_and_the_minimum() -> None:
    """A rectangle too small to profile is a fault of the *rectangle*, so it reads like one.

    Left to band detection it surfaces from inside the noise estimator as a
    :class:`~pipeline.errors.DetectionError` about profile samples, naming neither which of
    the supplied rectangles was at fault nor anything the caller could do about it. Reported
    here it names the 1-based position, the coordinates, the side, the minimum, where the
    minimum comes from, and the remedy the caller actually has.
    """
    config = _detection_config()
    minimum_px = minimum_lane_extent_px(config)

    with pytest.raises(LaneRoiError) as raised:
        validate_lane_rois(
            [Roi(0, 0, 60, 120), Roi(70, 0, 60, minimum_px - 1)],
            FIXTURE_WIDTH_PX,
            FIXTURE_HEIGHT_PX,
            config,
        )

    message = str(raised.value)
    assert "lane ROI 2" in message
    assert f"x=70, y=0, width=60, height={minimum_px - 1}" in message
    assert f"height={minimum_px - 1} px" in message
    assert f"below the {minimum_px} px" in message
    assert "row profile" in message
    assert f"detection.profile_smoothing_px={config.profile_smoothing_px}" in message
    assert "Supply a taller rectangle" in message


def test_a_lane_roi_too_narrow_for_a_column_profile_names_the_side_and_the_minimum() -> None:
    """The bound holds in both directions: a band's width is measured from a column profile."""
    config = _detection_config()
    minimum_px = minimum_lane_extent_px(config)

    with pytest.raises(LaneRoiError) as raised:
        validate_lane_rois(
            [Roi(70, 0, minimum_px - 1, 120)], FIXTURE_WIDTH_PX, FIXTURE_HEIGHT_PX, config
        )

    message = str(raised.value)
    assert "lane ROI 1" in message
    assert f"width={minimum_px - 1} px" in message
    assert f"below the {minimum_px} px" in message
    assert "column profile" in message
    assert "Supply a wider rectangle" in message


@pytest.mark.parametrize("axis", ["height", "width"])
def test_a_lane_too_small_is_refused_before_band_detection_can_raise(
    three_lane_blot: Blot, axis: str
) -> None:
    """The regression, through :func:`detect` rather than through the validator alone.

    A one-pixel side previously passed validation, reached ``_detect_bands_in_lane`` and left
    as a :class:`~pipeline.errors.DetectionError`. :class:`LaneRoiError` is not a subclass of
    it, so this pins which of the two the caller sees.
    """
    config = _detection_config()
    roi = Roi(0, 0, 60, 1) if axis == "height" else Roi(0, 0, 1, 120)

    with pytest.raises(LaneRoiError) as raised:
        detect(three_lane_blot.signal, config, [roi])

    assert "lane ROI 1" in str(raised.value)
    assert f"{axis}=1 px" in str(raised.value)


def test_a_lane_roi_at_and_just_above_the_minimum_extent_is_accepted_and_analyses(
    three_lane_blot: Blot,
) -> None:
    """The boundary belongs on the accepted side, and acceptance has to mean it runs.

    ``minimum_lane_extent_px`` is the shortest profile the machinery is defined on, not the
    shortest it is refused on, so a rectangle *at* it validates. One pixel above it, over the
    fixture's first lane centre, the lane must analyse all the way through and report that
    lane's two bands -- which is the thing the bound exists to protect.
    """
    config = _detection_config()
    minimum_px = minimum_lane_extent_px(config)

    at_minimum = detect(
        three_lane_blot.signal,
        config,
        [Roi(40 - minimum_px // 2, 0, minimum_px, FIXTURE_HEIGHT_PX)],
    )
    above = detect(
        three_lane_blot.signal,
        config,
        [Roi(40 - (minimum_px + 1) // 2, 0, minimum_px + 1, FIXTURE_HEIGHT_PX)],
    )
    short_side = detect(
        three_lane_blot.signal,
        config,
        [Roi(0, 28, FIXTURE_WIDTH_PX, minimum_px + 1)],
    )

    assert [lane.roi_source for lane in at_minimum.lanes] == ["caller"]
    assert [band.lane_id for band in above.bands] == ["L0", "L0"]
    assert [lane.roi.height for lane in short_side.lanes] == [minimum_px + 1]


# ---------------------------------------------------------------------------------------
# Why the minimum lane extent is what it is.
#
# Everything above takes the bound from minimum_lane_extent_px, which is right for testing
# what the bound *does* and useless for testing whether the bound is correct: weaken the
# function and those tests weaken with it. What follows measures the two properties the
# bound is derived from, on the smoothing operator and the noise estimator themselves, and
# only then asserts that minimum_lane_extent_px is the shortest profile length both hold at.
# ---------------------------------------------------------------------------------------

SMOOTHING_WINDOWS_PX = (3, 5, 7, 9)
"""Odd windows the smoothing properties are asserted over, spanning the shipped value of 5."""

NOISE_TRIALS = 20000
"""Independent noise profiles the measured noise-reduction test averages over.

Enough that the standard error of a standard deviation estimated from them is about 0.5%,
which is what makes a 2% tolerance on the measured reduction meaningful.
"""

NOISE_SEED = 20240817
"""Fixed, because a numeric assertion that passes on some seeds is not an assertion."""


def _smoothing_weights(length_px: int, window_px: int) -> np.ndarray:
    """Return the weight :func:`smooth_profile` gives each input sample in each output sample.

    Row ``i`` is the weight vector output sample ``i`` applies to the whole input profile,
    recovered by smoothing each unit impulse in turn. A moving average over an edge-padded
    profile is linear, so these weights *are* the operation rather than a model of it --
    checked against :func:`smooth_profile` on an arbitrary profile in
    :func:`test_the_recovered_smoothing_weights_are_the_operation_itself` before anything
    else relies on them.
    """
    impulses = np.eye(length_px, dtype=np.float64)
    return np.column_stack(
        [smooth_profile(impulses[:, index], window_px) for index in range(length_px)]
    )


def _distinct_samples_averaged(length_px: int, window_px: int) -> np.ndarray:
    """Return, per output sample, how many *distinct* input samples it is an average of.

    The count of non-zero weights: edge padding repeats a sample rather than adding one, so
    an output sample near an end averages the same few pixels several times over.
    """
    return np.count_nonzero(_smoothing_weights(length_px, window_px), axis=1)


def _noise_variance_factor(length_px: int, window_px: int) -> np.ndarray:
    """Return, per output sample, what smoothing multiplies independent noise *variance* by.

    ``sum(w**2)`` over that sample's weights, which is what a weighted mean does to the
    variance of independent samples. :func:`profile_noise_sigma` divides its raw estimate by
    ``sqrt(window_px)``, which is the claim that this factor equals ``1 / window_px``.
    """
    return np.sum(_smoothing_weights(length_px, window_px) ** 2, axis=1)


def _measured_noise_reduction(length_px: int, window_px: int) -> np.ndarray:
    """Return the noise reduction :func:`smooth_profile` actually achieves, per output sample.

    Measured rather than derived: independent Gaussian profiles are smoothed by the function
    itself and the spread of each output sample is taken across them, in units of the input
    noise. This is the number ``profile_noise_sigma``'s ``1 / sqrt(window_px)`` claims to be.
    """
    generator = np.random.default_rng(NOISE_SEED)
    raw = generator.normal(0.0, 1.0, size=(NOISE_TRIALS, length_px))
    smoothed = np.array([smooth_profile(profile, window_px) for profile in raw])
    return np.asarray(smoothed.std(axis=0, ddof=1))


def test_the_recovered_smoothing_weights_are_the_operation_itself() -> None:
    """The premise of every assertion below: smoothing is exactly this weight matrix.

    Without it the weights would be a plausible-looking model of ``smooth_profile`` and the
    properties derived from them would say nothing about the function that actually runs.
    """
    profile = np.array([3.0, -1.0, 4.0, 1.0, -5.0, 9.0, 2.0])

    weights = _smoothing_weights(profile.size, 5)

    np.testing.assert_allclose(weights @ profile, smooth_profile(profile, 5), atol=1e-12)
    np.testing.assert_allclose(weights.sum(axis=1), 1.0, atol=1e-12)


@pytest.mark.parametrize("window_px", SMOOTHING_WINDOWS_PX)
def test_a_profile_below_the_window_has_no_sample_averaging_a_whole_window(
    window_px: int,
) -> None:
    """The first of the two reasons, as a property of the smoothing operator.

    ``smooth_profile`` pads by repeating the edge sample, so a profile shorter than the window
    has *no* output sample that averages ``window_px`` distinct pixels -- the best any sample
    manages is the whole profile, which is fewer. At exactly ``window_px`` samples the centre
    one does, and it is the only one that does, which is what makes that length the boundary
    rather than one above it.
    """
    below = _distinct_samples_averaged(window_px - 1, window_px)
    at = _distinct_samples_averaged(window_px, window_px)

    assert below.max() == window_px - 1
    assert below.max() < window_px, "no sample may average a full window's worth of pixels"
    assert at[window_px // 2] == window_px
    assert np.count_nonzero(at == window_px) == 1, "the centre sample, and only it"


@pytest.mark.parametrize("window_px", SMOOTHING_WINDOWS_PX)
def test_below_the_window_the_reported_sigma_describes_a_reduction_that_never_happened(
    window_px: int,
) -> None:
    """The second reason, and the one whose failure mode is silent.

    ``profile_noise_sigma`` divides its raw estimate by ``sqrt(window_px)``, which asserts
    that smoothing cut the noise variance to ``1 / window_px``. On a profile shorter than the
    window *every* sample's variance factor is strictly larger, so the sigma every detection
    threshold is compared against understates the noise the smoothed profile carries --
    quietly, with no error anywhere. At exactly ``window_px`` samples the centre sample
    reaches the claimed factor exactly.
    """
    claimed = 1.0 / window_px
    below = _noise_variance_factor(window_px - 1, window_px)
    at = _noise_variance_factor(window_px, window_px)

    assert below.min() > claimed, "the claimed reduction is unreachable below the window"
    assert at.min() == pytest.approx(claimed)
    assert at[window_px // 2] == pytest.approx(claimed)


def test_the_sigma_a_short_profile_is_given_is_measurably_below_the_noise_it_carries() -> None:
    """The same gap, measured on real noise at the shipped window, with a number on it.

    Two things are pinned. First, ``profile_noise_sigma``'s ``sqrt(window)`` division is
    exactly a claim about the smoothed profile: the sigma it reports is the raw sigma it would
    report divided by ``sqrt(window)``. Second, on a profile one sample short of the window,
    the noise ``smooth_profile`` actually leaves is at least 15% above that -- 18% at the best
    sample and 48% at the worst, for the shipped window of 5 -- while at the full window the
    centre sample matches it to within 2%. A threshold set at "n sigma" on the short profile
    is therefore not the threshold it says it is, and nothing raises.
    """
    config = _detection_config()
    window_px = config.profile_smoothing_px
    assert window_px > NOISE_ESTIMATOR_MIN_SAMPLES, (
        "this test's subject is the smoothing half of the bound; under a config whose "
        "profile_smoothing_px does not exceed the noise estimator's own minimum there is "
        "nothing here to pin"
    )
    claimed = 1.0 / math.sqrt(window_px)
    raw = np.random.default_rng(NOISE_SEED).normal(0.0, 100.0, window_px - 1)

    reported = profile_noise_sigma(raw, window_px)
    below = _measured_noise_reduction(window_px - 1, window_px)
    at = _measured_noise_reduction(window_px, window_px)

    assert reported == pytest.approx(profile_noise_sigma(raw, 1) / math.sqrt(window_px))
    assert below.min() / claimed >= 1.15
    assert below.max() / claimed >= 1.4
    assert at[window_px // 2] / claimed == pytest.approx(1.0, rel=0.02)
    np.testing.assert_allclose(
        below,
        np.sqrt(_noise_variance_factor(window_px - 1, window_px)),
        rtol=0.03,
        err_msg="measured noise must match the weights the properties above are derived from",
    )


@pytest.mark.parametrize("window_px", SMOOTHING_WINDOWS_PX)
def test_the_minimum_lane_extent_is_the_shortest_profile_both_of_its_reasons_hold_at(
    window_px: int,
) -> None:
    """The bound itself, asserted against the properties rather than against a number.

    At ``minimum_lane_extent_px`` a sample averages a full window of distinct pixels and
    reaches the noise reduction the reported sigma claims, and the noise estimator has its
    difference; one pixel below, neither of the first two holds. A bound that dropped the
    ``profile_smoothing_px`` term would still satisfy the estimator's requirement and fail
    every assertion here, which is the point: the term's absence is otherwise invisible.
    """
    config = replace(_detection_config(), profile_smoothing_px=window_px)
    minimum_px = minimum_lane_extent_px(config)
    claimed = 1.0 / window_px

    assert _distinct_samples_averaged(minimum_px, window_px).max() == window_px
    assert _noise_variance_factor(minimum_px, window_px).min() == pytest.approx(claimed)
    assert _distinct_samples_averaged(minimum_px - 1, window_px).max() < window_px
    assert _noise_variance_factor(minimum_px - 1, window_px).min() > claimed

    profile = np.random.default_rng(NOISE_SEED).normal(0.0, 100.0, minimum_px)
    assert profile_noise_sigma(profile, window_px) > 0.0
    with pytest.raises(DetectionError, match="at least"):
        profile_noise_sigma(profile[: NOISE_ESTIMATOR_MIN_SAMPLES - 1], window_px)


def test_with_no_smoothing_the_bound_falls_back_to_the_noise_estimators_own_minimum() -> None:
    """The other term, in the one case that isolates it.

    At ``profile_smoothing_px = 1`` smoothing is the identity and has no requirement to make,
    so the bound is the estimator's two samples and nothing larger. Without this the bound
    could be ``profile_smoothing_px`` alone and admit a one-pixel lane.
    """
    config = replace(_detection_config(), profile_smoothing_px=1)

    assert minimum_lane_extent_px(config) == NOISE_ESTIMATOR_MIN_SAMPLES


def test_the_shipped_parameter_sets_bound_is_set_by_their_smoothing_window() -> None:
    """Which of the two terms actually binds under the parameters that ship.

    Recorded because it decides what the tests above are worth in production: under the
    shipped configs the smoothing window is the larger term, so the silent half of the bound
    is the half in force.
    """
    for name in ("default", "rolling_ball"):
        config = load_config(CONFIG_DIR / f"{name}.yaml").detection
        assert config.profile_smoothing_px > NOISE_ESTIMATOR_MIN_SAMPLES
        assert minimum_lane_extent_px(config) == config.profile_smoothing_px


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0,0,60", "has 3 comma-separated value(s)"),
        ("0,0,60,120,5", "has 5 comma-separated value(s)"),
        ("", "has 1 comma-separated value(s)"),
        ("0,0,60.5,120", "width='60.5', which is not an integer"),
        ("a,0,60,120", "x='a', which is not an integer"),
        ("0,0,0,120", "non-positive extent"),
        ("0,0,60,-1", "non-positive extent"),
        ("-1,0,60,120", "starts outside the image"),
    ],
)
def test_a_malformed_lane_roi_string_raises_and_says_what_is_wrong(
    text: str, expected: str
) -> None:
    """No silent coercion: a float is not rounded and a short list is not padded."""
    with pytest.raises(LaneRoiError) as raised:
        parse_lane_roi(text, position=2)

    assert "lane ROI 2" in str(raised.value)
    assert expected in str(raised.value)


def test_parsed_lane_rois_keep_the_order_they_were_given_in() -> None:
    """The supplied order is the lane order, so it is preserved rather than sorted."""
    rois = parse_lane_rois(["120,0,60,120", "0,0,60,120"])

    assert [roi.x for roi in rois] == [120, 0]


def test_a_caller_lane_is_centred_on_its_own_rectangle() -> None:
    """``center_px`` is the rectangle's centre column, in the same units a peak column is."""
    lanes = caller_lanes(
        [Roi(10, 0, 41, 120)], FIXTURE_WIDTH_PX, FIXTURE_HEIGHT_PX, _detection_config()
    )

    assert lanes[0].center_px == pytest.approx(30.0)
    assert lanes[0].lane_id == "L0"
    assert lanes[0].lane_index == 0


def test_supplied_lane_rois_suppress_lane_detection(three_lane_blot: Blot) -> None:
    """Two lanes are supplied for an image detection reads as three, and two come back.

    The strongest available statement that ``detect_lanes`` did not run: its answer on this
    fixture is a well-established three lanes, and the supplied rectangles are neither three
    in number nor at the boundaries it would have chosen.
    """
    config = load_config(CONFIG_DIR / "default.yaml").detection
    supplied = [Roi(5, 10, 70, 100), Roi(120, 10, 70, 100)]

    detected = detect(three_lane_blot.signal, config)
    given = detect(three_lane_blot.signal, config, supplied)

    assert len(detected.lanes) == 3
    assert [lane.roi_source for lane in detected.lanes] == ["detected"] * 3
    assert [lane.roi for lane in given.lanes] == supplied
    assert [lane.roi_source for lane in given.lanes] == ["caller", "caller"]
    assert [lane.lane_id for lane in given.lanes] == ["L0", "L1"]


def test_feeding_the_detected_lanes_back_reproduces_the_bands_exactly(
    three_lane_blot: Blot,
) -> None:
    """The phase's first requirement, pinned rather than made plausible.

    Detection's own lane rectangles are handed straight back as ``lane_rois``. Band detection
    inside a lane reads only that lane's rectangle and id, so every band must come back
    identical -- ROI, row profile and all -- and the *only* difference anywhere must be the
    ``roi_source`` recording who chose the rectangle.
    """
    config = load_config(CONFIG_DIR / "default.yaml").detection

    detected = detect(three_lane_blot.signal, config)
    fed_back = detect(three_lane_blot.signal, config, [lane.roi for lane in detected.lanes])

    assert fed_back.bands == detected.bands, "the same rectangles must find the same bands"
    assert [lane.roi for lane in fed_back.lanes] == [lane.roi for lane in detected.lanes]
    assert [lane.lane_id for lane in fed_back.lanes] == [lane.lane_id for lane in detected.lanes]
    assert [lane.roi_source for lane in detected.lanes] == ["detected"] * 3
    assert [lane.roi_source for lane in fed_back.lanes] == ["caller"] * 3


def test_a_lane_cannot_be_built_with_an_invented_roi_source() -> None:
    """The in-process guard behind the schema's enum: forgery is refused before serialisation.

    The JSON enum only catches a bad value once a document is written. This catches a writer
    inventing one at all, which is what stops a third provenance value from ever existing.
    """
    with pytest.raises(ValueError, match="roi_source"):
        DetectedLane(
            lane_id="L0",
            lane_index=0,
            center_px=30.0,
            roi=Roi(0, 0, 60, 120),
            roi_source="corrected_by_user",
        )

    for source in ROI_SOURCES:
        lane = DetectedLane(
            lane_id="L0", lane_index=0, center_px=30.0, roi=Roi(0, 0, 60, 120), roi_source=source
        )
        assert lane.roi_source == source


def test_bands_are_still_found_inside_a_supplied_lane(three_lane_blot: Blot) -> None:
    """Band detection is unchanged: the rectangles only change which pixels are searched."""
    config = load_config(CONFIG_DIR / "default.yaml").detection

    given = detect(three_lane_blot.signal, config, [Roi(20, 0, 40, 120)])

    assert [band.lane_id for band in given.bands] == ["L0", "L0"]
    for band in given.bands:
        assert band.roi.x >= 20
        assert band.roi.x + band.roi.width <= 60


def test_every_lane_in_the_document_says_where_its_roi_came_from(blot_png: Path) -> None:
    """``roi_source`` is on every lane in both directions, and the document still validates."""
    config = load_config(CONFIG_DIR / "default.yaml")
    supplied = parse_lane_rois(["5,0,70,120", "120,0,70,120"])

    detected = analyze_image(blot_png, config)
    given = analyze_image(blot_png, config, lane_rois=supplied)

    assert [lane["roi_source"] for lane in detected["lanes"]] == ["detected"] * 3
    assert [lane["roi_source"] for lane in given["lanes"]] == ["caller"] * 2
    assert [lane["roi"] for lane in given["lanes"]] == [roi.as_dict() for roi in supplied]
    assert _schema_errors(detected) == []
    assert _schema_errors(given) == []


def test_the_result_id_covers_the_supplied_lane_rois(blot_png: Path) -> None:
    """Supplied rectangles change every ROI in the document, so they cannot share an id."""
    config = load_config(CONFIG_DIR / "default.yaml")
    first = parse_lane_rois(["5,0,70,120", "120,0,70,120"])
    second = parse_lane_rois(["10,0,60,120", "130,0,60,120"])

    detected = analyze_image(blot_png, config)
    given = analyze_image(blot_png, config, lane_rois=first)
    other = analyze_image(blot_png, config, lane_rois=second)
    repeated = analyze_image(blot_png, config, lane_rois=first)

    assert len({detected["result_id"], given["result_id"], other["result_id"]}) == 3
    assert given["result_id"] == repeated["result_id"], "still content-addressed"


def test_the_result_id_covers_the_order_the_lane_rois_were_given_in(blot_png: Path) -> None:
    """The order is the lane order, so it names different lanes and is a different document."""
    config = load_config(CONFIG_DIR / "default.yaml")
    forward = parse_lane_rois(["5,0,70,120", "120,0,70,120"])
    backward = parse_lane_rois(["120,0,70,120", "5,0,70,120"])

    first = analyze_image(blot_png, config, lane_rois=forward)
    second = analyze_image(blot_png, config, lane_rois=backward)

    assert first["lanes"][0]["roi"] != second["lanes"][0]["roi"]
    assert first["result_id"] != second["result_id"]


def test_a_result_with_supplied_lanes_is_deterministic(blot_png: Path) -> None:
    """Determinism holds with caller lanes as it does without them."""
    config = load_config(CONFIG_DIR / "default.yaml")
    supplied = parse_lane_rois(["5,0,70,120", "120,0,70,120"])

    first = analyze_image(blot_png, config, lane_rois=supplied)
    second = analyze_image(blot_png, config, lane_rois=supplied)

    del first["provenance"]["created_at"]
    del second["provenance"]["created_at"]
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_an_empty_lane_roi_list_is_refused_rather_than_re_detecting(blot_png: Path) -> None:
    """Passing nothing and passing an empty sequence are *different* requests.

    A UI whose user has just deleted their last rectangle must not be answered with a full
    re-detection and a different set of numbers. ``analyze_image`` must therefore reach the
    same refusal :func:`validate_lane_rois` already writes for the same input.
    """
    config = load_config(CONFIG_DIR / "default.yaml")

    with pytest.raises(LaneRoiError, match="no lane ROI was supplied"):
        analyze_image(blot_png, config, lane_rois=[])

    absent = analyze_image(blot_png, config)
    assert [lane["roi_source"] for lane in absent["lanes"]] == ["detected"] * 3


def test_the_cli_accepts_repeated_lane_rois(
    blot_png: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--lane-roi`` end to end: the lanes written out are exactly the ones supplied.

    The summary line is asserted too, and it is not decoration: the operator watching a CLI
    run sees the line, not the JSON, so it is where a caller-chosen rectangle silently
    reported as a detector output would first be visible -- or first be missed.
    """
    code = main(
        [
            "run",
            str(blot_png),
            "--config",
            str(CONFIG_DIR / "default.yaml"),
            "--out",
            str(tmp_path / "out"),
            "--lane-roi",
            "5,0,70,120",
            "--lane-roi",
            "120,0,70,120",
        ]
    )

    assert code == 0
    assert "2 lane(s) [caller]" in capsys.readouterr().out
    document = json.loads((tmp_path / "out" / "fixture_blot.json").read_text(encoding="utf-8"))
    assert [lane["roi"]["x"] for lane in document["lanes"]] == [5, 120]
    assert [lane["roi_source"] for lane in document["lanes"]] == ["caller", "caller"]
    assert _schema_errors(document) == []


def test_the_cli_says_detected_when_it_detected_the_lanes(
    blot_png: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other half of the annotation: without ``--lane-roi`` the line says ``detected``.

    Asserted separately from the supplied-lane run because a line that said ``caller`` for
    everything, or ``detected`` for everything, would pass either test alone.
    """
    code = main(
        [
            "run",
            str(blot_png),
            "--config",
            str(CONFIG_DIR / "default.yaml"),
            "--out",
            str(tmp_path / "out"),
        ]
    )

    assert code == 0
    assert "3 lane(s) [detected]" in capsys.readouterr().out


def test_the_cli_reports_a_bad_lane_roi_without_writing_a_result(
    blot_png: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A rectangle off the image exits 1 with the actionable message, and writes nothing."""
    code = main(
        [
            "run",
            str(blot_png),
            "--config",
            str(CONFIG_DIR / "default.yaml"),
            "--out",
            str(tmp_path / "out"),
            "--lane-roi",
            "5,0,70,120",
            "--lane-roi",
            "170,0,70,120",
        ]
    )

    assert code == 1
    error = capsys.readouterr().err
    assert "lane ROI 2" in error
    assert "200x120" in error
    assert not (tmp_path / "out" / "fixture_blot.json").exists()
