"""Re-run every dev-split parameter sweep that NOTES.md and ``configs/*.yaml`` quote.

Why this is a committed tool rather than a scratch script: three review cycles found
stale figures in those two places, every time because a parameter moved and the recorded
surfaces were patched by hand instead of re-run. The surfaces now have one source --
``evals/dev_sweeps.json`` -- which this module regenerates:

* ``python -m evals.sweep`` re-runs everything and rewrites the JSON and the companion
  ``evals/dev_sweeps.md``.
* ``python -m evals.sweep --check`` re-runs everything and exits non-zero if any recorded
  figure has moved, naming each one. That is the mechanical check behind NOTES.md's
  reproducibility statement.

What ``--check`` compares exactly and what it compares within a tolerance is a policy, set
out on :class:`FigureTolerance` and in NOTES.md's "Why ``--check`` compares some figures
within a tolerance". In short: the record's structure, the split header, each sweep's prose
and the two shipped config digests are exact; the measured figures are compared within a
class tolerance resolved by ``(sweep, value label, field)``. They have to be, because a
last-bit difference in a profile mean between x86-64 and arm64 propagates through tie
resolution in peak finding to a whole-pixel ROI shift, and across the IoU 0.5 matching
boundary. Two consequences worth knowing before editing this module: no measured quantity
may be interpolated into a ``note``, since notes are compared exactly, and the guarantee
that a *parameter* cannot move unnoticed is carried by the exact digest comparison, never
by the figure tolerances.

Split discipline: like :mod:`evals.run`, this reads :data:`evals.run.EVALUATED_SPLIT` and
has no flag to point it anywhere else.

Every score comes from :mod:`evals.metrics` via :func:`evals.run.score_result`; the only
thing this module adds is (a) reuse of one background estimate across variants that
differ only in detection, which makes the detection sweeps tractable, and (b) the
fixed-subset selection statistic described on :class:`VariantScore`.

Runtime is minutes, not seconds: the background sweeps re-estimate the background for
every value over all 30 dev images.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
from scipy.ndimage import uniform_filter

from evals.metrics import (
    PLAN_IOU_THRESHOLD,
    ErrorScores,
    intensity_recovery_error,
    micro_average_detection_scores,
    normalization_ratio_error,
)
from evals.run import (
    EVALUATED_SPLIT,
    SATURATED_FLAG,
    QcEvaluation,
    band_flag_scores,
    evaluate_image,
    image_flag_scores,
    load_split,
    score_result,
    shoulder_coincidence,
)
from pipeline.analyze import require_writable_destination
from pipeline.background import correct_background
from pipeline.config import HOUSEKEEPING_SINGLE, TOTAL_PROTEIN, PipelineConfig, load_config
from pipeline.detect import detect
from pipeline.load import load_image
from pipeline.qc import (
    IMAGE_QC_FLAGS,
    LOW_DYNAMIC_RANGE,
    OVERLAPPING,
    is_low_dynamic_range,
    is_overlapping,
    is_saturated,
    is_unresolved_shoulder,
)
from pipeline.quantify import quantify_bands

CONFIG_DIR = Path("configs")
DATA_DIR = Path("data")
JSON_PATH = Path("evals/dev_sweeps.json")
MARKDOWN_PATH = Path("evals/dev_sweeps.md")

F1_DECIMALS = 4
"""Decimals kept for F1 figures, so ``--check`` compares exactly and portably."""

PERCENT_DECIMALS = 2
"""Decimals kept for percentage figures."""

UNFLAGGED = "unflagged"
"""Label for a truth band carrying no QC flag at all."""


@dataclass(frozen=True)
class VariantScore:
    """Every figure this project quotes about one parameter value.

    ``clean_*`` are computed over the bands that carry **no** truth QC flag and that were
    matched by *every* variant in the same sweep. That fixed subset is the selection
    statistic: aggregate error cannot select an aperture, because unresolved doublets
    over-read and a tighter aperture is rewarded for cancelling that rather than for
    measuring better, and it cannot compare variants whose matched sets differ either.
    """

    label: str
    lane_f1: float
    lane_true_positives: int
    lane_false_positives: int
    lane_false_negatives: int
    band_f1: float
    band_precision: float
    band_recall: float
    band_true_positives: int
    band_false_positives: int
    band_false_negatives: int
    matched: int
    mean_absolute_percent: float
    median_absolute_percent: float
    max_absolute_percent: float
    clean_count: int
    clean_signed_percent: float
    clean_mean_absolute_percent: float
    clean_median_absolute_percent: float

    def as_dict(self) -> dict[str, Any]:
        """Return the record written to :data:`JSON_PATH`."""
        return {field: getattr(self, field) for field in self.__dataclass_fields__}


@dataclass(frozen=True)
class Sweep:
    """One parameter varied over a list of values, with everything else held shipped."""

    name: str
    parameter: str
    note: str
    variants: tuple[tuple[str, PipelineConfig], ...]


def _round(value: float, decimals: int) -> float:
    """Return ``value`` rounded, as a plain float."""
    return float(round(value, decimals))


def _corrected_images(
    config: PipelineConfig, truths: Sequence[dict[str, Any]], cache: dict[str, Any]
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Return background/corrected arrays per image, reusing the cache across variants.

    Keyed by the background parameter echo, so variants that differ only in detection
    share one background pass and variants that change the background do not.
    """
    key = json.dumps(config.background.as_parameters(), sort_keys=True)
    if key not in cache:
        cache[key] = {
            truth["image_id"]: _correct(truth, config)
            for truth in truths
        }
    return cache[key]


def _correct(truth: dict[str, Any], config: PipelineConfig) -> tuple[np.ndarray, np.ndarray]:
    """Load one gold-set image and return its background surface and corrected pixels."""
    loaded = load_image(DATA_DIR / truth["image_path"])
    correction = correct_background(loaded.pixels, config.background)
    return correction.background, correction.corrected


def _score_variant(
    label: str,
    config: PipelineConfig,
    truths: Sequence[dict[str, Any]],
    cache: dict[str, Any],
) -> tuple[VariantScore, dict[tuple[str, str], tuple[float, float]]]:
    """Score one variant over the dev split.

    Returns its aggregate figures (with ``clean_*`` left at zero, filled in by
    :func:`_apply_clean_subset` once the whole sweep is known) and the per-band records
    of every matched, unflagged band.
    """
    corrected = _corrected_images(config, truths, cache)
    evaluations = []
    unflagged: dict[tuple[str, str], tuple[float, float]] = {}
    true_values: list[float] = []
    predicted_values: list[float] = []
    for truth in truths:
        background, image = corrected[truth["image_id"]]
        detection = detect(image, config.detection)
        measurements = quantify_bands(image, background, detection.bands, config.quantification)
        result = {
            "lanes": [{"roi": lane.roi.as_dict()} for lane in detection.lanes],
            "bands": [measurement.as_dict() for measurement in measurements],
        }
        evaluation = score_result(truth, result, PLAN_IOU_THRESHOLD)
        evaluations.append(evaluation)
        for truth_index, predicted_index, _ in evaluation.bands.matches:
            band = truth["bands"][truth_index]
            pair = (
                float(band["true_integrated_intensity_dn"]),
                measurements[predicted_index].integrated_intensity,
            )
            true_values.append(pair[0])
            predicted_values.append(pair[1])
            if not band["qc_flags"]:
                unflagged[(truth["image_id"], band["band_id"])] = pair

    lanes = micro_average_detection_scores(
        [item.lanes for item in evaluations], PLAN_IOU_THRESHOLD
    )
    bands = micro_average_detection_scores(
        [item.bands for item in evaluations], PLAN_IOU_THRESHOLD
    )
    error = intensity_recovery_error(true_values, predicted_values)
    score = VariantScore(
        label=label,
        lane_f1=_round(lanes.f1, F1_DECIMALS),
        lane_true_positives=lanes.true_positives,
        lane_false_positives=lanes.false_positives,
        lane_false_negatives=lanes.false_negatives,
        band_f1=_round(bands.f1, F1_DECIMALS),
        band_precision=_round(bands.precision, F1_DECIMALS),
        band_recall=_round(bands.recall, F1_DECIMALS),
        band_true_positives=bands.true_positives,
        band_false_positives=bands.false_positives,
        band_false_negatives=bands.false_negatives,
        matched=error.count,
        mean_absolute_percent=_round(error.mean_absolute_percent_error, PERCENT_DECIMALS),
        median_absolute_percent=_round(error.median_absolute_percent_error, PERCENT_DECIMALS),
        max_absolute_percent=_round(error.max_absolute_percent_error, PERCENT_DECIMALS),
        clean_count=0,
        clean_signed_percent=0.0,
        clean_mean_absolute_percent=0.0,
        clean_median_absolute_percent=0.0,
    )
    return score, unflagged


def _clean_statistics(
    records: dict[tuple[str, str], tuple[float, float]], keys: Sequence[tuple[str, str]]
) -> tuple[float, ErrorScores]:
    """Return the signed mean error and the error summary over ``keys``."""
    error = intensity_recovery_error(
        [records[key][0] for key in keys], [records[key][1] for key in keys]
    )
    return float(np.mean(error.per_item_percent_error)), error


def _apply_clean_subset(
    scores: Sequence[VariantScore],
    records: Sequence[dict[tuple[str, str], tuple[float, float]]],
) -> list[VariantScore]:
    """Fill in each variant's ``clean_*`` figures over the sweep's shared unflagged bands."""
    common = sorted(set.intersection(*(set(record) for record in records)))
    filled = []
    for score, record in zip(scores, records, strict=True):
        signed, error = _clean_statistics(record, common)
        filled.append(
            replace(
                score,
                clean_count=len(common),
                clean_signed_percent=_round(signed, PERCENT_DECIMALS),
                clean_mean_absolute_percent=_round(
                    error.mean_absolute_percent_error, PERCENT_DECIMALS
                ),
                clean_median_absolute_percent=_round(
                    error.median_absolute_percent_error, PERCENT_DECIMALS
                ),
            )
        )
    return filled


def _detection_variant(
    base: PipelineConfig, section: str, **overrides: Any
) -> PipelineConfig:
    """Return ``base`` with one ``detection`` field replaced."""
    detection = base.detection
    if section == "detection":
        detection = replace(detection, **overrides)
    elif section == "lane":
        detection = replace(detection, lane=replace(detection.lane, **overrides))
    else:
        detection = replace(detection, band=replace(detection.band, **overrides))
    return replace(base, detection=detection)


def _background_variant(base: PipelineConfig, **overrides: Any) -> PipelineConfig:
    """Return ``base`` with one rolling-ball parameter replaced."""
    params = replace(base.background.params, **overrides)
    return replace(base, background=replace(base.background, params=params))


def build_sweeps(default: PipelineConfig, rolling_ball: PipelineConfig) -> list[Sweep]:
    """Return every sweep, each holding all other parameters at their shipped values."""
    def detection(section: str, name: str, values: Sequence[Any], note: str) -> Sweep:
        return Sweep(
            name=f"{section}.{name}" if section != "detection" else name,
            parameter=name,
            note=note,
            variants=tuple(
                (str(value), _detection_variant(default, section, **{name: value}))
                for value in values
            ),
        )

    return [
        detection(
            "detection", "profile_smoothing_px", (1, 3, 5, 7, 9, 11),
            "Moving average over both 1D profiles.",
        ),
        detection(
            "lane", "min_prominence_fraction",
            (0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50),
            "Lane peak prominence, as a fraction of the column profile's robust range.",
        ),
        detection(
            "lane", "robust_range_percentile", (0.0, 2.0, 5.0, 10.0, 20.0, 30.0),
            "0.0 makes the lane scale exactly max-minus-min.",
        ),
        detection(
            "lane", "min_separation_px", (1, 4, 8, 12, 16, 20, 24),
            "Minimum spacing between lane centres.",
        ),
        detection(
            "band", "min_prominence_fraction",
            (0.001, 0.05, 0.10, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50),
            "0.001 is the nearest legal stand-in for switching this criterion off; the "
            "validator requires a positive fraction.",
        ),
        detection(
            "band", "min_prominence_sigma", (0.0, 2.0, 5.0, 8.0, 12.0),
            "0.0 switches the noise criterion off, which the validator does accept.",
        ),
        detection(
            "band", "min_separation_px", (1, 4, 8, 12, 16, 17, 20, 24),
            "Minimum spacing between band peaks within a lane.",
        ),
        detection(
            "band", "baseline_percentile", (2.0, 5.0, 10.0, 20.0, 30.0),
            "Robust zero for a lane's row profile.",
        ),
        detection(
            "band", "extent_relative_height", (0.04, 0.05, 0.06, 0.07, 0.08, 0.10, 0.12, 0.20),
            "The ROI aperture. Selected on clean_mean_absolute_percent, not on band_f1.",
        ),
        detection(
            "band", "extent_min_sigma", (0.0, 1.0, 1.5, 2.0, 2.5, 3.0),
            "Noise floor under the ROI edge threshold.",
        ),
        Sweep(
            name="background.local_median.window_px",
            parameter="window_px",
            note="Local-median window, with default.yaml's other parameters shipped.",
            variants=tuple(
                (str(value), _background_variant(default, window_px=value))
                for value in (31, 41, 51, 61, 71, 81)
            ),
        ),
        Sweep(
            name="background.rolling_ball.radius_px",
            parameter="radius_px",
            note="Rolling-ball radius, with rolling_ball.yaml's other parameters shipped.",
            variants=tuple(
                (str(value), _background_variant(rolling_ball, radius_px=value))
                for value in (15.0, 25.0, 35.0, 50.0)
            ),
        ),
        Sweep(
            name="background.rolling_ball.presmooth_px",
            parameter="presmooth_px",
            note="Mean filter applied before the ball is rolled.",
            variants=tuple(
                (str(value), _background_variant(rolling_ball, presmooth_px=value))
                for value in (1, 3, 9, 21)
            ),
        ),
        Sweep(
            name="shipped_configs",
            parameter="config",
            note="The two shipped configs as committed. They differ only in background.",
            variants=(("default.yaml", default), ("rolling_ball.yaml", rolling_ball)),
        ),
    ]


APERTURE_SWEEP = "band.extent_relative_height"
"""The sweep whose selector uncertainty is bootstrapped."""

BOOTSTRAP_RESAMPLES = 2000
"""Paired bootstrap resamples used for the aperture selector's uncertainty."""

BOOTSTRAP_SEED = 20260811
"""Fixed seed, so the recorded interval is reproducible rather than merely plausible."""

NOISE_REALISATIONS = 500
"""White-noise fields averaged for the pre-smoothing variance measurement."""

NOISE_SEED = 4242
"""Fixed seed for that measurement."""

CANVAS_HEIGHT_PX = 192
CANVAS_WIDTH_PX = 256
"""Canvas of the committed gold set, used for the noise measurement's field size."""

SHIPPED_APERTURE = 0.06
"""The shipped ``extent_relative_height``, the reference point of the bootstrap."""


def _roi_size_record(
    truths: Sequence[dict[str, Any]], config: PipelineConfig, cache: dict[str, Any]
) -> dict[str, Any]:
    """Return the band-ROI size distribution, truth and detected.

    Recorded because it is the premise of both background windows: a local median only
    reports the background while a band covers a minority of its window, and a rolling
    ball only stays out of a band while its footprint is wider than the band.
    """
    window = config.background.params.window_px
    corrected = _corrected_images(config, truths, cache)
    rows = []
    truth_boxes = [band["roi"] for truth in truths for band in truth["bands"]]
    detected_boxes = []
    for truth in truths:
        background, image = corrected[truth["image_id"]]
        detection = detect(image, config.detection)
        detected_boxes.extend(band.roi.as_dict() for band in detection.bands)
    for label, boxes in (("truth", truth_boxes), ("detected", detected_boxes)):
        widths = np.array([box["width"] for box in boxes])
        heights = np.array([box["height"] for box in boxes])
        areas = widths * heights
        rows.append(
            {
                "label": label,
                "bands": len(boxes),
                "median_width_px": int(np.median(widths)),
                "max_width_px": int(widths.max()),
                "median_height_px": int(np.median(heights)),
                "max_height_px": int(heights.max()),
                "median_window_coverage_percent": _round(
                    100.0 * float(np.median(areas)) / window**2, PERCENT_DECIMALS
                ),
                "max_window_coverage_percent": _round(
                    100.0 * float(areas.max()) / window**2, PERCENT_DECIMALS
                ),
                "max_area_width_px": int(widths[int(np.argmax(areas))]),
                "max_area_height_px": int(heights[int(np.argmax(areas))]),
            }
        )
    return {
        "parameter": f"band ROI extent, against the shipped {window} px median window",
        "note": (
            "Band ROI sizes over the dev split. `window_coverage` is the ROI area as a "
            "percentage of the local-median window area; a coverage of f means the median "
            "reports the 0.5/(1-f) quantile of the background rather than its median."
        ),
        "values": rows,
    }


def _doublet_cost_record(
    truths: Sequence[dict[str, Any]], config: PipelineConfig, cache: dict[str, Any]
) -> dict[str, Any]:
    """Return what the one-band-per-maximum ruling costs, by band role and difficulty cell."""
    corrected = _corrected_images(config, truths, cache)
    roles: dict[str, int] = {}
    cells: dict[str, int] = {}
    partners = 0
    missed = 0
    for truth in truths:
        partners += sum(
            1 for band in truth["bands"] if band["role"] == "target_secondary"
        )
        background, image = corrected[truth["image_id"]]
        detection = detect(image, config.detection)
        measurements = quantify_bands(image, background, detection.bands, config.quantification)
        result = {
            "lanes": [{"roi": lane.roi.as_dict()} for lane in detection.lanes],
            "bands": [measurement.as_dict() for measurement in measurements],
        }
        evaluation = score_result(truth, result, PLAN_IOU_THRESHOLD)
        matched = {index for index, _, _ in evaluation.bands.matches}
        shape = truth["generation"]["difficulty"]["band_shape"]
        for index, band in enumerate(truth["bands"]):
            if index in matched:
                continue
            missed += 1
            roles[band["role"]] = roles.get(band["role"], 0) + 1
            cells[shape] = cells.get(shape, 0) + 1
    return {
        "parameter": "missed bands",
        "note": (
            "What the 'one band per resolved maximum' ruling costs, split by the role and "
            "the band_shape cell of the truth bands it misses."
        ),
        "values": [
            {
                "label": "missed_by_role",
                "missed_total": missed,
                "target_secondary": roles.get("target_secondary", 0),
                "target": roles.get("target", 0),
                "housekeeping": roles.get("housekeeping", 0),
                "target_secondary_bands_in_split": partners,
            },
            {
                "label": "missed_by_band_shape",
                "doublet": cells.get("doublet", 0),
                "sharp": cells.get("sharp", 0),
                "smeared": cells.get("smeared", 0),
            },
        ],
    }


def _lane_geometry_record(truths: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Return the truth lane-ROI width against the lane pitch, per lane-geometry level.

    The tilted row is the IoU ceiling a detector that does not reproduce
    ``synth/MODELS.md`` §4a's tilt widening can reach.
    """
    rows = []
    for level in ("straight", "tilted", "smile"):
        widths = []
        pitches = []
        for truth in truths:
            if truth["generation"]["difficulty"]["lane_geometry"] != level:
                continue
            widths.extend(lane["roi"]["width"] for lane in truth["lanes"])
            pitches.append(truth["generation"]["parameters"]["derived"]["lane_pitch_px"])
        rows.append(
            {
                "label": level,
                "median_truth_roi_width_px": int(np.median(widths)),
                "max_truth_roi_width_px": int(max(widths)),
                "median_lane_pitch_px": int(np.median(pitches)),
                "width_over_pitch_iou_ceiling": _round(
                    float(np.median(pitches)) / float(np.median(widths)), F1_DECIMALS
                ),
            }
        )
    return {
        "parameter": "truth lane ROI width vs lane pitch",
        "note": (
            "A detected lane ROI is one pitch wide and full height. Where the truth ROI is "
            "wider than a pitch, the quotient is the best IoU such a detection can score."
        ),
        "values": rows,
    }


def _presmooth_variance_record() -> dict[str, Any]:
    """Return the variance reduction of the rolling ball's pre-smoothing filter.

    Uses no gold-set data: it is a property of ``uniform_filter`` at the canvas size and
    border mode :mod:`pipeline.background` uses. Recorded here so the figure the config
    quotes is checked like every other one.
    """
    generator = np.random.RandomState(NOISE_SEED)
    rows = []
    for size in (3, 9, 21):
        whole = []
        interior = []
        pad = size // 2
        for _ in range(NOISE_REALISATIONS):
            noise = generator.normal(0.0, 100.0, (CANVAS_HEIGHT_PX, CANVAS_WIDTH_PX))
            smoothed = uniform_filter(noise, size=size, mode="nearest")
            whole.append(noise.var() / smoothed.var())
            interior.append(
                noise[pad:-pad, pad:-pad].var() / smoothed[pad:-pad, pad:-pad].var()
            )
        rows.append(
            {
                "label": str(size),
                "samples_in_window": size * size,
                "whole_image_variance_reduction": _round(float(np.mean(whole)), PERCENT_DECIMALS),
                "interior_variance_reduction": _round(
                    float(np.mean(interior)), PERCENT_DECIMALS
                ),
            }
        )
    return {
        "parameter": "presmooth_px",
        "note": (
            f"Variance reduction of a size x size uniform_filter with mode='nearest' on "
            f"white noise, averaged over {NOISE_REALISATIONS} realisations of a "
            f"{CANVAS_HEIGHT_PX}x{CANVAS_WIDTH_PX} field. The uncorrelated ideal is the "
            f"sample count; the whole-image figure is below it because border pixels "
            f"average fewer independent samples."
        ),
        "values": rows,
    }


def _aperture_uncertainty_record(
    records: Sequence[dict[tuple[str, str], tuple[float, float]]],
    labels: Sequence[str],
) -> dict[str, Any]:
    """Return a paired bootstrap of the aperture selector, against the shipped value.

    The selector picks the aperture by a difference of about 0.15 percentage points in
    mean |error| while trading 0.0335 of band F1, so whether that difference is resolved
    at all is a fair question. Resampling the shared bands answers it.
    """
    common = sorted(set.intersection(*(set(record) for record in records)))
    generator = np.random.RandomState(BOOTSTRAP_SEED)
    errors = {
        label: np.array(
            [
                abs(record[key][1] - record[key][0]) / record[key][0] * 100.0
                for key in common
            ]
        )
        for label, record in zip(labels, records, strict=True)
    }
    draws = generator.randint(0, len(common), size=(BOOTSTRAP_RESAMPLES, len(common)))
    resampled = {label: errors[label][draws].mean(axis=1) for label in labels}
    shipped = str(SHIPPED_APERTURE)
    rows = []
    for label in labels:
        difference = resampled[label] - resampled[shipped]
        rows.append(
            {
                "label": label,
                "clean_mean_absolute_percent": _round(
                    float(errors[label].mean()), PERCENT_DECIMALS
                ),
                "bootstrap_standard_error_percent": _round(
                    float(resampled[label].std(ddof=1)), PERCENT_DECIMALS
                ),
                "difference_from_shipped_percent": _round(
                    float(difference.mean()), PERCENT_DECIMALS
                ),
                "difference_ci_low_percent": _round(
                    float(np.percentile(difference, 2.5)), PERCENT_DECIMALS
                ),
                "difference_ci_high_percent": _round(
                    float(np.percentile(difference, 97.5)), PERCENT_DECIMALS
                ),
            }
        )
    return {
        "parameter": "extent_relative_height",
        # No measured quantity is interpolated into this note. Notes are compared with zero
        # tolerance by --check, and the size of the shared subset is a matched-set count:
        # exactly the quantity a flipped tie moves. It is recorded, and checked, as
        # clean_count on the band.extent_relative_height sweep instead.
        "note": (
            f"Paired bootstrap, {BOOTSTRAP_RESAMPLES} resamples of the unflagged bands every "
            f"variant of the aperture sweep matches -- the subset the band.extent_relative_"
            f"height sweep's `clean` columns use, whose size that sweep records as "
            f"`clean_count` -- seed {BOOTSTRAP_SEED}. "
            f"`difference_from_shipped_percent` is mean |error| minus the shipped "
            f"{SHIPPED_APERTURE} value's, on the same resample; its interval excludes 0 only "
            f"where the ordering is resolved."
        ),
        "values": rows,
    }


def _matched_band_subsets(
    truths: Sequence[dict[str, Any]], config: PipelineConfig
) -> dict[str, Any]:
    """Return the shipped config's matched bands broken down by their truth QC flags.

    Recorded in the same shape as a sweep so that the same checker covers it. This is the
    premise of the confound argument -- what share of *matched* bands is an unresolved
    doublet -- plus the over-read those bands actually show, as opposed to
    ``synth/MODELS.md`` §8's noise-free best case, plus the subset error figures NOTES.md
    reports beside the headline.
    """
    cache: dict[str, Any] = {}
    corrected = _corrected_images(config, truths, cache)
    counts = {UNFLAGGED: 0, "overlapping": 0, "saturated": 0, "both": 0}
    subsets: dict[str, list[tuple[float, float]]] = {"all": [], "no_saturated": [], UNFLAGGED: []}
    overlapping_errors: list[float] = []
    primary_errors: list[float] = []
    for truth in truths:
        background, image = corrected[truth["image_id"]]
        detection = detect(image, config.detection)
        measurements = quantify_bands(image, background, detection.bands, config.quantification)
        result = {
            "lanes": [{"roi": lane.roi.as_dict()} for lane in detection.lanes],
            "bands": [measurement.as_dict() for measurement in measurements],
        }
        evaluation = score_result(truth, result, PLAN_IOU_THRESHOLD)
        for truth_index, predicted_index, _ in evaluation.bands.matches:
            band = truth["bands"][truth_index]
            flags = set(band["qc_flags"])
            if not flags:
                counts[UNFLAGGED] += 1
            elif flags == {"overlapping"}:
                counts["overlapping"] += 1
            elif flags == {"saturated"}:
                counts["saturated"] += 1
            else:
                counts["both"] += 1
            true_value = float(band["true_integrated_intensity_dn"])
            pair = (true_value, measurements[predicted_index].integrated_intensity)
            subsets["all"].append(pair)
            if "saturated" not in flags:
                subsets["no_saturated"].append(pair)
            if not flags:
                subsets[UNFLAGGED].append(pair)
            if "overlapping" in flags:
                signed = (pair[1] - true_value) / true_value * 100.0
                overlapping_errors.append(signed)
                if band["role"] != "target_secondary":
                    primary_errors.append(signed)

    matched = len(subsets["all"])
    overlapping_total = counts["overlapping"] + counts["both"]
    values = []
    for label, pairs in subsets.items():
        error = intensity_recovery_error([pair[0] for pair in pairs], [pair[1] for pair in pairs])
        values.append(
            {
                "label": label,
                "bands": error.count,
                "mean_absolute_percent": _round(
                    error.mean_absolute_percent_error, PERCENT_DECIMALS
                ),
                "median_absolute_percent": _round(
                    error.median_absolute_percent_error, PERCENT_DECIMALS
                ),
            }
        )
    values.append(
        {
            "label": "flag_counts",
            "matched": matched,
            "unflagged": counts[UNFLAGGED],
            "overlapping_only": counts["overlapping"],
            "saturated_only": counts["saturated"],
            "both_flags": counts["both"],
            "overlapping_total": overlapping_total,
            "overlapping_primary_bands": len(primary_errors),
            "overlapping_share_percent": _round(
                100.0 * overlapping_total / matched, PERCENT_DECIMALS
            ),
            "overlapping_signed_mean_percent": _round(
                float(np.mean(overlapping_errors)), PERCENT_DECIMALS
            ),
            "overlapping_primary_signed_mean_percent": _round(
                float(np.mean(primary_errors)), PERCENT_DECIMALS
            ),
            "overlapping_primary_signed_median_percent": _round(
                float(np.median(primary_errors)), PERCENT_DECIMALS
            ),
        }
    )
    return {
        "parameter": "truth QC flags",
        "note": (
            "Matched bands of the shipped configs/default.yaml, split by the QC flags their "
            "ground truth carries. Not a parameter sweep; recorded in the same shape so the "
            "same checker covers it."
        ),
        "values": values,
    }


def _qc_flag_record(evaluations: Sequence[QcEvaluation]) -> dict[str, Any]:
    """Return QC flag accuracy against ground truth, per flag and per scope.

    Only the confusion **counts** are recorded. Precision, recall and F1 are arithmetic on
    them, and each is ``None`` in a case the record cannot hold -- a flag absent from both
    sides has no precision -- so recording a rate would turn an honestly undefined score into
    a comparison failure. NOTES.md quotes the rates as arithmetic on these counts.

    The shoulder row is not an accuracy: ``unresolved_shoulder`` has no ground-truth
    counterpart, so it is recorded as a coincidence with truth's ``overlapping`` label
    (:class:`evals.metrics.FlagCoincidence`) and is labelled as such.
    """
    image = image_flag_scores(evaluations)
    band = band_flag_scores(evaluations)
    shoulder = shoulder_coincidence(evaluations)
    values: list[dict[str, Any]] = []
    for scope, scores in (("image", image), ("band", band)):
        for flag, item in scores.per_flag.items():
            values.append(
                {
                    "label": f"{scope}.{flag}",
                    "items": scores.item_count,
                    "true_positives": item.true_positives,
                    "false_positives": item.false_positives,
                    "false_negatives": item.false_negatives,
                    "true_negatives": item.true_negatives,
                }
            )
    values.append(
        {
            "label": "band.unresolved_shoulder_coincidence",
            "items": shoulder.items,
            "reference_items": shoulder.reference_items,
            "non_reference_items": shoulder.non_reference_items,
            "fired_with_reference": shoulder.fired_with_reference,
            "fired_without_reference": shoulder.fired_without_reference,
        }
    )
    return {
        "parameter": "qc thresholds, as shipped in configs/default.yaml",
        "note": (
            "QC flags against the ground-truth labels, scored on the images and on the "
            "matched band pairs by evals.metrics.qc_flag_accuracy. Counts only: the rates are "
            "arithmetic on them, and are undefined rather than zero in cases this record "
            "cannot express. The last row is not an accuracy -- unresolved_shoulder has no "
            "ground-truth counterpart, so it is recorded as a coincidence with truth's "
            "overlapping label."
        ),
        "values": values,
    }


def _normalization_record(evaluations: Sequence[QcEvaluation]) -> dict[str, Any]:
    """Return the normalized-ratio error per mode, against ground truth.

    The ``housekeeping_single`` row rests on an **oracle** reference: it designates the
    reference band from ground truth's role, which is an input a real blot does not supply
    (:data:`evals.run.ORACLE_REFERENCE_ROLE`). ``housekeeping_multi`` is absent because the
    gold set has one reference band per lane and that mode takes at least two; it is verified
    against hand-computed fixtures in ``tests/test_pipeline_normalize.py`` instead.
    """
    values: list[dict[str, Any]] = []
    for label, comparisons, used, skipped in (
        (
            HOUSEKEEPING_SINGLE,
            [item for evaluation in evaluations for item in evaluation.housekeeping],
            sum(item.used_housekeeping_lanes for item in evaluations),
            sum(item.skipped_housekeeping_lanes for item in evaluations),
        ),
        (
            TOTAL_PROTEIN,
            [item for evaluation in evaluations for item in evaluation.total_protein],
            sum(item.used_total_protein_lanes for item in evaluations),
            sum(item.skipped_total_protein_lanes for item in evaluations),
        ),
    ):
        if not comparisons:
            raise RuntimeError(
                f"the {label} mode produced no ratio to score on the "
                f"{EVALUATED_SPLIT} split, so its row would have no figures; that is a change "
                f"in what the pipeline or the join produces, not a record to write"
            )
        included = [item for item in comparisons if not item.excluded]
        included_error = normalization_ratio_error(
            {item.key: item.true_ratio for item in included},
            {item.key: item.predicted_ratio for item in included},
        )
        every_error = normalization_ratio_error(
            {item.key: item.true_ratio for item in comparisons},
            {item.key: item.predicted_ratio for item in comparisons},
        )
        values.append(
            {
                "label": label,
                "ratios": len(comparisons),
                "included_ratios": len(included),
                "excluded_ratios": len(comparisons) - len(included),
                "reference_flagged_ratios": sum(
                    1 for item in comparisons if item.reference_qc_flagged
                ),
                "used_lanes": used,
                "skipped_lanes": skipped,
                "included_mean_absolute_percent": _round(
                    included_error.mean_absolute_percent_error, PERCENT_DECIMALS
                ),
                "included_median_absolute_percent": _round(
                    included_error.median_absolute_percent_error, PERCENT_DECIMALS
                ),
                # Recorded so that NORMALIZATION_ERROR's derivation -- one ratio's leverage on
                # the subset mean is bounded by max|e| / n -- is checkable from the record
                # rather than from a figure only the runner prints.
                "included_max_absolute_percent": _round(
                    included_error.max_absolute_percent_error, PERCENT_DECIMALS
                ),
                "all_mean_absolute_percent": _round(
                    every_error.mean_absolute_percent_error, PERCENT_DECIMALS
                ),
                "all_median_absolute_percent": _round(
                    every_error.median_absolute_percent_error, PERCENT_DECIMALS
                ),
                "all_max_absolute_percent": _round(
                    every_error.max_absolute_percent_error, PERCENT_DECIMALS
                ),
            }
        )
    return {
        "parameter": "normalization mode",
        "note": (
            "Normalized ratios against ground truth, by mode, with configs/default.yaml's "
            "other parameters shipped. `included` is the default policy -- QC-flagged bands "
            "excluded from the ratios -- and `all` adds them back, since a flagged ratio is "
            "reported rather than dropped. `skipped_lanes` counts lanes whose truth ratio "
            "could not be joined to a predicted one. ORACLE: the housekeeping row takes its "
            "reference band from ground truth's role, an input no real blot supplies, so its "
            "figures are conditional on a correct reference. housekeeping_multi needs at "
            "least two references per lane and the gold set has one, so it is verified "
            "against hand-computed fixtures in the test suite instead of scored here."
        ),
        "values": values,
    }


SATURATION_THRESHOLDS: tuple[int, ...] = (1, 2, 3, 5, 10)
"""Clipped-pixel counts the ``saturated`` flag's sensitivity is recorded over. The shipped
value is the first; the generator's own label rule sits at the third."""

Observation = tuple[frozenset[str], int, float | None, float]
"""One matched band as the threshold surfaces see it: its truth flags, and the three
observations the shipped criteria are applied to (clipped pixels, profile asymmetry, largest
same-lane ROI overlap)."""

DYNAMIC_RANGE_THRESHOLDS: tuple[float, ...] = (0.15, 0.2, 0.25, 0.3, 0.4)
"""Peak fractions the ``low_dynamic_range`` flag's sensitivity is recorded over: the shipped
value, the generator's own labelling fraction, two below it and the value that would score
perfectly. That last row is the point of the surface -- it is the evidence that a better score
was available on this gold set and was declined, because reaching it means matching the
generator's scratch amplitude. A claim that load-bearing may not rest on prose."""

OVERLAP_THRESHOLDS: tuple[float, ...] = (0.001, 0.05, 0.15, 0.3)
"""ROI overlaps the geometric ``overlapping`` flag's sensitivity is recorded over: the smallest
overlap the validator accepts, the shipped value, the generator's own labelling threshold, and
one beyond it. This surface is the evidence for the human's Ruling 1 -- that a purely geometric
flag has almost no recall once a doublet is one band -- so it is recorded rather than quoted."""

SHOULDER_THRESHOLDS: tuple[float, ...] = (1.2, 1.3, 1.5, 1.75, 2.0, 2.5)
"""Half-width ratios the shape test's sensitivity is recorded over, the shipped value among
them, so the claim that it separates the two populations best is re-measured rather than
transcribed."""


def _matched_band_observations(
    evaluations: Sequence[QcEvaluation],
) -> tuple[tuple[frozenset[str], int, float | None, float], ...]:
    """Return, per matched band of the dev split, the truth labels and the two observations.

    Read straight off the :class:`evals.run.QcEvaluation` records the QC row is built from, so
    the sensitivity surfaces below cost no extra pass over the images and -- more importantly --
    apply the *shipped* criteria (:func:`pipeline.qc.is_saturated`,
    :func:`pipeline.qc.is_unresolved_shoulder`) to the pipeline's own numbers rather than
    restating either.
    """
    return tuple(
        (frozenset(truth_flags), clipped, ratio, overlap)
        for evaluation in evaluations
        for truth_flags, clipped, ratio, overlap in zip(
            evaluation.truth_band_flags,
            evaluation.clipped_pixel_counts,
            evaluation.row_half_width_ratios,
            evaluation.max_same_lane_ious,
            strict=True,
        )
    )


def _saturation_sensitivity_record(
    observations: Sequence[Observation], config: PipelineConfig
) -> dict[str, Any]:
    """Return the ``saturated`` flag's confusion counts over candidate clipped-pixel counts.

    Each row re-applies :func:`pipeline.qc.is_saturated` with one field of the shipped QC
    config replaced, so the row at the shipped value is the shipped flag by construction;
    ``tests/test_sweep_check.py`` pins it against the ``qc_flag_accuracy`` row as well.

    The shipped value is one clipped pixel, from a criterion stated without reference to the
    generator; the generator's own label rule is three. Recording the whole surface is what
    makes the cost of that choice a re-measured figure rather than a claim.
    """
    values = []
    for threshold in SATURATION_THRESHOLDS:
        variant = replace(config.qc, saturated_min_clipped_pixels=threshold)
        counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
        for truth_flags, clipped, _, _overlap in observations:
            in_truth = SATURATED_FLAG in truth_flags
            fires = is_saturated(clipped, variant)
            key = "tp" if in_truth and fires else "fp" if fires else "fn" if in_truth else "tn"
            counts[key] += 1
        values.append(
            {
                "label": str(threshold),
                "items": len(observations),
                "true_positives": counts["tp"],
                "false_positives": counts["fp"],
                "false_negatives": counts["fn"],
                "true_negatives": counts["tn"],
            }
        )
    return {
        "parameter": "saturated_min_clipped_pixels",
        "note": (
            "The band `saturated` flag against ground truth's labels, over candidate "
            "clipped-pixel thresholds, on the matched bands of the shipped config. Every row "
            "applies pipeline.qc.is_saturated to the pipeline's own recorded clipped-pixel "
            "counts with only that threshold replaced, so the row at the shipped value is the "
            "shipped flag. Counts only, as in qc_flag_accuracy."
        ),
        "values": values,
    }


def _overlap_sensitivity_record(
    observations: Sequence[Observation],
    evaluations: Sequence[QcEvaluation],
    truths: Sequence[dict[str, Any]],
    config: PipelineConfig,
) -> dict[str, Any]:
    """Return the geometric ``overlapping`` flag's confusion counts over candidate IoU thresholds.

    The evidence behind the human's Ruling 1, recorded rather than quoted: once a doublet is
    reported as one band with one ROI (Phase 1's ruling), there is no second ROI for a geometric
    flag to overlap with, so its recall against truth's ``overlapping`` label collapses whatever
    the threshold. Each row re-applies :func:`pipeline.qc.is_overlapping` to the pipeline's own
    largest same-lane overlap per band, with only that threshold replaced.

    Two split-level counts are repeated on every row, because they are what the ruling was
    argued from and they do not depend on the threshold: ``same_lane_band_pairs``, how many pairs
    of detected bands share a lane at all, and ``truth_overlapping_bands``, how many bands
    ground truth labels ``overlapping`` across the whole split -- read out of ground truth, so
    exact.
    """
    truth_labelled = sum(
        1 for truth in truths for band in truth["bands"] if OVERLAPPING in band["qc_flags"]
    )
    pair_ious = [
        overlap for evaluation in evaluations for overlap in evaluation.same_lane_pair_ious
    ]
    detected_ious = [
        overlap
        for evaluation in evaluations
        for overlap in evaluation.detected_max_same_lane_ious
    ]
    pairs = len(pair_ious)
    values = []
    for threshold in OVERLAP_THRESHOLDS:
        variant = replace(config.qc, overlap_iou_threshold=threshold)
        counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
        for truth_flags, _, _, overlap in observations:
            in_truth = OVERLAPPING in truth_flags
            fires = is_overlapping(overlap, variant)
            key = "tp" if in_truth and fires else "fp" if fires else "fn" if in_truth else "tn"
            counts[key] += 1
        values.append(
            {
                "label": str(threshold),
                "items": len(observations),
                "true_positives": counts["tp"],
                "false_positives": counts["fp"],
                "false_negatives": counts["fn"],
                "true_negatives": counts["tn"],
                "same_lane_band_pairs": pairs,
                "pairs_at_or_above_threshold": sum(
                    1 for overlap in pair_ious if is_overlapping(overlap, variant)
                ),
                "detected_bands_flagged": sum(
                    1 for overlap in detected_ious if is_overlapping(overlap, variant)
                ),
                "truth_overlapping_bands": truth_labelled,
            }
        )
    return {
        "parameter": "overlap_iou_threshold",
        "note": (
            "The band `overlapping` flag against ground truth's labels, over candidate ROI "
            "overlap thresholds, on the matched bands of the shipped config. Every row applies "
            "pipeline.qc.is_overlapping to the pipeline's own largest same-lane overlap per "
            "band with only that threshold replaced. The two repeated counts are the "
            "threshold-independent evidence for the human's first ruling in this phase: how "
            "many "
            "pairs of detected bands share a lane at all, and how many bands ground truth "
            "labels overlapping across the split. `pairs_at_or_above_threshold` and "
            "`detected_bands_flagged` count over every detected band, not only the matched ones."
        ),
        "values": values,
    }


def _dynamic_range_sensitivity_record(
    evaluations: Sequence[QcEvaluation], config: PipelineConfig
) -> dict[str, Any]:
    """Return the ``low_dynamic_range`` flag's confusion counts over candidate peak fractions.

    Image-scope, so the items are the 30 dev images rather than matched bands. Each row
    re-applies :func:`pipeline.qc.is_low_dynamic_range` to the brightest band peak each result
    document reports, with only that threshold replaced.

    This surface exists because of what its last row says. A fraction of 0.4 scores perfectly on
    this gold set and is **not** shipped, since a scratch adds exactly 0.25 of full scale
    (``synth/MODELS.md`` §5) and reaching that score means matching the generator's scratch
    amplitude. The claim "a better score was available and was declined" is the whole
    anti-circularity argument for this threshold, so it is measured here rather than asserted in
    NOTES.md. ``max_peak_fraction_among_truth_low`` is the threshold-independent reason the
    shipped value misses what it misses: the brightest peak measured on any image ground truth
    labels ``low_dynamic_range``.
    """
    truth_low = [
        evaluation
        for evaluation in evaluations
        if LOW_DYNAMIC_RANGE in evaluation.truth_image_flags
    ]
    largest = max(
        (
            evaluation.brightest_peak_value / evaluation.max_value
            for evaluation in truth_low
        ),
        default=0.0,
    )
    values = []
    for threshold in DYNAMIC_RANGE_THRESHOLDS:
        variant = replace(config.qc, dynamic_range_min_peak_fraction=threshold)
        counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
        for evaluation in evaluations:
            in_truth = LOW_DYNAMIC_RANGE in evaluation.truth_image_flags
            fires = is_low_dynamic_range(
                evaluation.brightest_peak_value, evaluation.max_value, variant
            )
            key = "tp" if in_truth and fires else "fp" if fires else "fn" if in_truth else "tn"
            counts[key] += 1
        values.append(
            {
                "label": str(threshold),
                "items": len(evaluations),
                "true_positives": counts["tp"],
                "false_positives": counts["fp"],
                "false_negatives": counts["fn"],
                "true_negatives": counts["tn"],
                "truth_low_dynamic_range_images": len(truth_low),
                "max_peak_fraction_among_truth_low": _round(largest, F1_DECIMALS),
            }
        )
    return {
        "parameter": "dynamic_range_min_peak_fraction",
        "note": (
            "The image `low_dynamic_range` flag against ground truth's labels, over candidate "
            "peak fractions. Every row applies pipeline.qc.is_low_dynamic_range to the brightest "
            "band peak each result document reports, with only that threshold replaced, so the "
            "row at the shipped value is the shipped flag. The last row is the one to read: it "
            "scores perfectly and is refused, because reaching it means matching the amplitude "
            "the generator gives a scratch. `max_peak_fraction_among_truth_low` is why the "
            "shipped value misses what it misses -- a scratch crossing a lane raises the "
            "brightest measured peak on a faint image."
        ),
        "values": values,
    }


def _shoulder_sensitivity_record(
    observations: Sequence[Observation],
    evaluations: Sequence[QcEvaluation],
    config: PipelineConfig,
) -> dict[str, Any]:
    """Return the shape test's firing rates over candidate half-width ratios.

    Not an accuracy: ``unresolved_shoulder`` has no ground-truth label, so each row is a
    coincidence with truth's ``overlapping`` -- how often the flag fires on bands that carry
    that label and on bands that do not. Recorded so a reader can see the whole surface the
    shipped value sits on; **no test asserts that the shipped value is its optimum**, because
    that would make maximal coincidence with a truth label a property CI enforces, and the
    diagnostic is explicitly not a scoring target.
    """
    censored = sum(evaluation.censored_row_half_width_ratios for evaluation in evaluations)
    values = []
    for threshold in SHOULDER_THRESHOLDS:
        variant = replace(config.qc, shoulder_half_width_ratio=threshold)
        reference = 0
        non_reference = 0
        fired_with = 0
        fired_without = 0
        for truth_flags, _, ratio, _overlap in observations:
            fires = is_unresolved_shoulder(ratio, variant)
            if OVERLAPPING in truth_flags:
                reference += 1
                fired_with += int(fires)
            else:
                non_reference += 1
                fired_without += int(fires)
        values.append(
            {
                "label": str(threshold),
                "items": len(observations),
                "reference_items": reference,
                "non_reference_items": non_reference,
                "fired_with_reference": fired_with,
                "fired_without_reference": fired_without,
                "censored_asymmetry_bands": censored,
            }
        )
    return {
        "parameter": "shoulder_half_width_ratio",
        "note": (
            "The shape test's firing rate on matched bands whose ground truth carries "
            "`overlapping` and on those it does not, over candidate half-width ratios, at the "
            "shipped half-maximum level. Every row applies pipeline.qc.is_unresolved_shoulder "
            "to the pipeline's own recorded asymmetry with only that threshold replaced. Not "
            "an accuracy: the two flags ask different questions, so this is a coincidence, the "
            "rates are arithmetic on these counts, and the surface is recorded to be read "
            "rather than to be optimised. `censored_asymmetry_bands` counts the matched bands "
            "whose asymmetry could not be measured at all, and so can never flag: it is "
            "threshold-independent, and it is recorded because a blind spot that grew silently "
            "would look like a flag that had simply stopped firing."
        ),
        "values": values,
    }


def run_sweeps() -> dict[str, Any]:
    """Run every sweep and return the complete record."""
    default = load_config(CONFIG_DIR / "default.yaml")
    rolling_ball = load_config(CONFIG_DIR / "rolling_ball.yaml")
    truths = load_split(DATA_DIR)
    cache: dict[str, Any] = {}
    recorded: dict[str, Any] = {
        "split": EVALUATED_SPLIT,
        "iou_threshold": PLAN_IOU_THRESHOLD,
        "images": len(truths),
        "truth_lanes": sum(len(truth["lanes"]) for truth in truths),
        "truth_bands": sum(len(truth["bands"]) for truth in truths),
        "shipped": {
            "default": default.digest(),
            "rolling_ball": rolling_ball.digest(),
        },
        "sweeps": {},
    }
    aperture: tuple[list[str], list[dict[tuple[str, str], tuple[float, float]]]] | None = None
    for sweep in build_sweeps(default, rolling_ball):
        print(f"  {sweep.name} ({len(sweep.variants)} values)", file=sys.stderr)
        scores = []
        records = []
        for label, config in sweep.variants:
            score, unflagged = _score_variant(label, config, truths, cache)
            scores.append(score)
            records.append(unflagged)
        recorded["sweeps"][sweep.name] = {
            "parameter": sweep.parameter,
            "note": sweep.note,
            "values": [score.as_dict() for score in _apply_clean_subset(scores, records)],
        }
        if sweep.name == APERTURE_SWEEP:
            aperture = ([label for label, _ in sweep.variants], records)
    if aperture is None:
        raise RuntimeError(f"the {APERTURE_SWEEP} sweep did not run; its bootstrap needs it")
    print("  derived records", file=sys.stderr)
    recorded["sweeps"]["matched_band_subsets"] = _matched_band_subsets(truths, default)
    recorded["sweeps"]["band_roi_sizes"] = _roi_size_record(truths, default, cache)
    recorded["sweeps"]["doublet_cost"] = _doublet_cost_record(truths, default, cache)
    recorded["sweeps"]["lane_roi_geometry"] = _lane_geometry_record(truths)
    recorded["sweeps"]["presmooth_variance"] = _presmooth_variance_record()
    qc_evaluations = [
        evaluate_image(truth, DATA_DIR, default, PLAN_IOU_THRESHOLD)[1] for truth in truths
    ]
    recorded["sweeps"]["qc_flag_accuracy"] = _qc_flag_record(qc_evaluations)
    recorded["sweeps"]["normalization_modes"] = _normalization_record(qc_evaluations)
    observations = _matched_band_observations(qc_evaluations)
    recorded["sweeps"]["qc.saturated_min_clipped_pixels"] = _saturation_sensitivity_record(
        observations, default
    )
    recorded["sweeps"]["qc.overlap_iou_threshold"] = _overlap_sensitivity_record(
        observations, qc_evaluations, truths, default
    )
    recorded["sweeps"]["qc.shoulder_half_width_ratio"] = _shoulder_sensitivity_record(
        observations, qc_evaluations, default
    )
    recorded["sweeps"]["qc.dynamic_range_min_peak_fraction"] = _dynamic_range_sensitivity_record(
        qc_evaluations, default
    )
    recorded["sweeps"]["aperture_selector_uncertainty"] = _aperture_uncertainty_record(
        aperture[1], aperture[0]
    )
    return recorded


def _sweep_table(values: Sequence[dict[str, Any]]) -> list[str]:
    """Render the standard one-row-per-value sweep table."""
    lines = [
        "| value | lane F1 | lane tp/fp/fn | band F1 | band tp/fp/fn | matched "
        "| mean \\|e\\|% | med \\|e\\|% | clean n | clean signed% | clean mean% "
        "| clean med% |",
        "|" + "---|" * 12,
    ]
    for value in values:
        lines.append(
            f"| {value['label']} | {value['lane_f1']:.4f} "
            f"| {value['lane_true_positives']}/{value['lane_false_positives']}"
            f"/{value['lane_false_negatives']} | {value['band_f1']:.4f} "
            f"| {value['band_true_positives']}/{value['band_false_positives']}"
            f"/{value['band_false_negatives']} | {value['matched']} "
            f"| {value['mean_absolute_percent']:.2f} "
            f"| {value['median_absolute_percent']:.2f} | {value['clean_count']} "
            f"| {value['clean_signed_percent']:.2f} "
            f"| {value['clean_mean_absolute_percent']:.2f} "
            f"| {value['clean_median_absolute_percent']:.2f} |"
        )
    return lines


def _field_list(values: Sequence[dict[str, Any]]) -> list[str]:
    """Render records that are not parameter sweeps, one bullet per field."""
    lines = []
    for value in values:
        lines.append(f"- **{value['label']}**")
        for field, recorded in value.items():
            if field == "label":
                continue
            suffix = "%" if field.endswith("percent") else ""
            rendered = (
                f"{recorded:.2f}" if isinstance(recorded, float) else str(recorded)
            )
            lines.append(f"  - {field}: {rendered}{suffix}")
    return lines


def _markdown(recorded: dict[str, Any]) -> str:
    """Render ``recorded`` as a human-readable report."""
    lines = [
        "# Dev-split parameter sweeps",
        "",
        "Generated by `python -m evals.sweep`. Do not edit by hand: `python -m evals.sweep",
        "--check` re-runs every sweep and fails if any figure here has moved.",
        "",
        f"Split `{recorded['split']}`, IoU >= {recorded['iou_threshold']}, "
        f"{recorded['images']} images, {recorded['truth_lanes']} truth lanes, "
        f"{recorded['truth_bands']} truth bands.",
        "",
        "In every sweep, one parameter varies and all others hold their shipped values.",
        "`clean` columns are the fixed subset of unflagged bands matched by every variant",
        "in that sweep -- the selection statistic, described in `evals/sweep.py`.",
        "",
    ]
    for name, sweep in recorded["sweeps"].items():
        lines += [f"## `{name}`", "", sweep["note"], ""]
        if all("lane_f1" in value for value in sweep["values"]):
            lines += _sweep_table(sweep["values"])
        else:
            lines += _field_list(sweep["values"])
        lines.append("")
    return "\n".join(lines) + "\n"


LABEL_FIELD = "label"
"""The field that identifies a value inside its sweep. Values are paired on it, so it is
never compared as a figure."""

EXACT_HEADER_FIELDS = ("split", "iou_threshold", "images", "truth_lanes", "truth_bands", "shipped")
"""Record header fields compared with zero tolerance. None of them is a measurement: they
are the split identity, the truth counts, and the two shipped config digests."""

SWEEP_METADATA_FIELDS = ("parameter", "note")
"""Per-sweep prose, compared with zero tolerance.

That is only sound while no measured quantity is interpolated into it. A matched-set count
inside a note would be compared exactly here while the identical count is tolerated as a
figure -- the false CI failure this whole policy exists to prevent, wearing prose. So the
notes carry no measurement, and ``tests/test_sweep_check.py`` holds that line: every number
in a note or parameter must be a value label of its own sweep or one of a listed set of
constants.
"""


class RecordError(ValueError):
    """The committed and measured records cannot be compared as they stand."""


class UnclassifiedFigureError(RecordError):
    """A recorded figure that no tolerance class claims, or that is not a number.

    Raised rather than defaulted, in either direction: silently comparing a new field
    exactly would make ``--check`` fail on platform noise, and silently comparing it
    leniently would hide a regression. The author of a new figure classifies it.
    """


class DuplicateValueLabelError(RecordError):
    """Two values of one sweep share a label, so they cannot be paired for comparison."""


class MalformedRecordError(RecordError):
    """A record is missing structure the comparison needs, such as a sweep's ``values``.

    Only a hand-edited or truncated JSON reaches this; :func:`run_sweeps` cannot produce
    it. Raised as a :class:`RecordError` so ``--check`` reports it as a failure of the
    committed record rather than as a traceback out of the comparison.
    """


@dataclass(frozen=True)
class FigureTolerance:
    """How far a class of recorded figure may move before ``--check`` calls it drift.

    ``bound`` is in the figure's own units, or a fraction of the recorded value when
    ``relative`` is set. Two kinds of evidence set these bounds, and every constant below
    says which it used:

    * **Observed drift** -- what the x86-64 CI runner measured against this arm64-generated
      record, rounded up to a round figure: 1.3x it for the integer counts, which move in
      whole units, and 1.8x to 2.3x for the continuous statistics. Each constant states the
      drift it was taken from, so the multiplier can be checked rather than trusted.
    * **Measured leverage** -- for the classes that did not drift, how far one band entering
      or leaving a subset moves the statistic. Measured over the dev split as a one-off
      diagnostic, then multiplied by the number of bands a tie flip is observed to move
      (one, for the matched set) with headroom, or by the count class's own bound where the
      figure is computed from those counts.

    Neither is a worst case and the policy does not pretend to be one: a class seen to drift
    beyond its bound is a bound to re-derive from that observation, not one to widen
    pre-emptively. What no bound may be is *tighter than a figure it is computed from* --
    that guarantees a false failure on a movement the record already permits.

    No bound is ever justified as "wide enough to keep a parameter change visible". That
    guarantee is the exact comparison of ``shipped``.
    """

    name: str
    bound: float
    relative: bool = False

    def __post_init__(self) -> None:
        if self.bound < 0.0:
            raise ValueError(
                f"tolerance {self.name!r} has a negative bound {self.bound!r}; a tolerance "
                f"is a distance, so it must be zero or positive"
            )

    def allowance(self, committed: float) -> float:
        """Return the largest movement away from ``committed`` this class permits."""
        if self.relative:
            return self.bound * abs(float(committed))
        return self.bound

    def exceeded_by(self, committed: float, measured: float) -> bool:
        """Return whether ``measured`` sits further from ``committed`` than this class allows."""
        return abs(float(measured) - float(committed)) > self.allowance(committed)

    def describe(self) -> str:
        """Return this tolerance in words, for a drift message."""
        if self.relative:
            return f"the {self.name} tolerance of +/-{self.bound:.2%} of the recorded value"
        return f"the {self.name} tolerance of +/-{self.bound:g}"


PLATFORM_INDEPENDENT = FigureTolerance("platform-independent", bound=0.0)
"""Zero. Figures computed from the committed ground truth, from a shipped config parameter
or from integer arithmetic alone, with no float reduction over pipeline output between them
and the record. Ground truth is committed and a config parameter is digest-guarded, so
there is nothing here for an architecture to disagree about, and a movement means the gold
set moved."""

DETECTION_COUNT = FigureTolerance("detection count", bound=4.0)
"""+/-4 counts. Observed CI drift: 3, on the false-positive count of one background window;
the matched count in the same row moved by 1. A 1-ULP difference in a profile mean flips a
plateau tie in peak finding, which shifts an integer ROI by a pixel, which crosses IoU
0.5."""

DETECTION_RATE = FigureTolerance("detection rate", bound=0.02)
"""+/-0.02 on F1, precision and recall. Observed CI drift: 0.0092, on band precision at one
background window. Roughly 2x. ``tests/test_sweep_check.py`` asserts from the committed
record that three real parameter effects on band F1 -- extent_min_sigma, the background
window and profile smoothing -- all stay clear of this bound, so it still discriminates."""

ERROR_POINT = FigureTolerance("percentage-point error", bound=1.0)
"""+/-1.0 percentage point, for error statistics over the full matched set and over the
sweeps' clean subsets. Observed CI drift: 0.56, on aggregate mean |error| at one background
window; 1.8x, and about two bands of the 0.54 pp per-band leverage measured on the aggregate
mean.

It is deliberately no wider. The rule above -- re-derive from an observation, do not widen
pre-emptively -- was tested here: 1.5 pp was proposed from leverage alone, and it would have
let the difference between two neighbouring background windows pass on the shipped
configuration, which is a regression this check exists to catch. Nothing was ever observed
to move an error statistic past 1.0.

A tighter bound for the ``clean_*`` columns was considered and rejected. Their leverage is
0.16 pp per band on the aperture sweep, which would justify 0.5 pp, but the smallest shared
subset in the record is barely half that sweep's, over a background whose mean |error| is
several times larger, so one band there is worth several times as much -- and nothing
measures it. A bound that holds on one sweep and fails on another is the failure being
fixed."""

SUBSET_ERROR_POINT = FigureTolerance("flagged-subset error", bound=4.0)
"""+/-4.0 percentage points, for the signed error statistics over the overlapping-band
subset. Observed CI drift: none. Measured leverage: that subset is a few dozen bands whose
signed errors are large, where one band moves the mean by 1.81 pp -- more than three times
its leverage on the full matched set, purely because the subset is small. Two such bands,
rounded up. What these figures are quoted for -- that the measured over-read on
unresolved doublets exceeds the generator's noise-free best case -- clears the bound by an
order of magnitude."""

COUNT_SHARE = FigureTolerance("count share", bound=2.0)
"""+/-2.0 percentage points, for a percentage of one recorded count in another. Observed CI
drift: none. Derived from the counts it divides rather than from leverage: the count class
allows four bands, which on a matched set of a few hundred is around one and a half points
of a share, and a share may not be held tighter than the counts it is computed from without
failing on a movement those counts are allowed."""

BOOTSTRAP_POINT = FigureTolerance("paired bootstrap", bound=0.3)
"""+/-0.3 percentage points, for the aperture bootstrap's paired statistics. Observed CI
drift: none -- that record reproduced exactly. Measured leverage: these are differences
taken on the same resample of the same shared subset, so the shifts largely cancel and one
band moves them by at most 0.058 pp; the shared subset is allowed to move four bands, so
0.3 pp.

**This class does not police the aperture ordering, and nothing here does.** The bootstrap
exists to resolve a difference several times smaller than 0.3 pp, and it reports intervals
narrower still, so a measurement in which a looser aperture beat the shipped one -- interval
excluding zero, in the opposite direction -- passes ``--check`` silently. The bound cannot
be tightened to fix that: it follows from the shared subset's own permitted movement, and a
tighter one would fail on drift the record already allows. What is checked is the
*transcription*: the figures NOTES.md quotes are tied to this record by
``tests/test_recorded_figures.py``. What is not checked is a *re-measurement* that inverts
them. NOTES.md's "Open items" records that gap rather than papering over it."""

ROI_MEDIAN_PIXEL = FigureTolerance("median ROI pixel size", bound=2.0)
"""+/-2 px, for the *median* width and height of the detected ROI set. Observed CI drift:
none. A tie flip moves one ROI edge by a whole pixel, and a median over three hundred ROIs
moves by at most an order-statistic step of that size; twice it. Medians only: an extremum
is a selection over a set whose membership moves, and is classified below."""

EXTREME_RELATIVE = FigureTolerance("extreme value", bound=0.10, relative=True)
"""+/-10% of the recorded value, for extreme order statistics -- the largest recovery error,
the largest normalized-ratio error and the extreme ROI dimensions. Observed CI drift: 4.3%, on
the largest recovery error at one background window. An extremum is one band's figure and moves
wholesale when a different band becomes the extreme, which is exactly what the detection-count
class permits to happen; a pixel-sized bound would be tighter than the mechanism rather than
merely tight."""

ROI_AREA = FigureTolerance("ROI area share", bound=0.25, relative=True)
"""+/-25% of the recorded value, for an ROI area expressed as a share of the background
window. Observed CI drift: none. Derived so as not to be tighter than the ROI areas it
reports: the maximum coverage is the extreme ROI's area, whose two sides are each allowed
+/-10%, which is 21% of area; the median coverage is the median of the areas, and an ROI of
the median's size taking the 2 px step per side that the median dimensions allow is nearly
a fifth of its area. 25% sits just above both, which is where it comes from. What
the figures are quoted for -- that a band covers a minority of the median window, so the
median still reports background rather than the band -- clears this by a wide margin."""

QC_FLAG_COUNT = FigureTolerance("QC flag count", bound=4.0)
"""+/-4 counts, for every count in the QC and normalization records -- confusion counts, the
shoulder coincidence's tallies, ratio counts and skipped-lane counts. Observed CI drift: none;
these records are new this phase and have never run on the other architecture.

Derived, not measured, and derived from the counts these are computed *over*: every one of
them is a tally over the matched band pairs, whose membership the detection-count class
already allows to move by four. A bound tighter than that would fail on a movement the record
permits two sweeps over. It is therefore a loose alarm on the image-scope rows in particular,
where a whole image would have to change flags -- and it is worth saying plainly that this
class catches staleness and gross regression, not a one-band change in a QC decision. The
band-level *behaviour* is pinned tightly in ``tests/test_pipeline_qc.py`` against the gold
set instead, which is where a one-band change does fail."""

NORMALIZATION_ERROR = FigureTolerance("normalized-ratio error", bound=0.35, relative=True)
"""+/-35% of the recorded value, for the mean and median error of the normalized ratios.
Observed CI drift: none.

**Derived for the two means, from figures the record itself holds.** One ratio entering or
leaving a subset moves its mean by at most that subset's largest |error| divided by its size --
which is why ``included_max_absolute_percent`` and ``all_max_absolute_percent`` are recorded --
and the count class above allows four ratios to move. ``tests/test_sweep_check.py`` recomputes
that for both means of every row and asserts this bound covers it. On the four rows the record
holds today the leverage is 30.4%, 11.2%, 25.8% and 29.8% of the recorded mean -- a ratio error
here can be several times the mean -- so three of the four clear the 35% bound by under 5
percentage points (0.57, 2.91 and 1.77 pp; the fourth by 5.47). The bound is not generous
relative to its own derivation, whatever it looks like as a percentage.

**The two medians inherit the same bound, and that is not derived from a median's own
leverage.** A median moves by an order-statistic step rather than by an extreme, so its leverage
is smaller than the means' -- the bound is therefore wider than a median needs, which risks
missing drift rather than failing on it, and the medians' real guard is the transcription check
in ``tests/test_recorded_figures.py``. Measuring the step and giving the medians a tighter class
is left as an open item rather than guessed at here.

35% is wide, and the reason is structural rather than a choice: a ratio is a quotient of two
measurements, so it carries both their errors, and its distribution has a long tail. It is
recorded as an open item in NOTES.md rather than presented as a regression alarm."""

MONTE_CARLO = FigureTolerance("Monte-Carlo variance ratio", bound=1e-4, relative=True)
"""+/-0.01% of the recorded value. Observed CI drift: none. These touch no gold set: they
are means over 500 seeded white-noise realisations, where only float64 accumulation noise
(~1e-12 relative) can differ by architecture, so this is six orders of headroom and still
leaves the smallest real effect the record distinguishes -- whole-image against interior
border handling, which ``tests/test_sweep_check.py`` asserts from the record -- visible by
orders of magnitude. For the four of the six figures it governs that sit under 100 it is
below the two-decimal recording step, so for those it is exactly exact; for the two largest
it admits a single step of that rounding and no more."""

_DEFAULT_TOLERANCE_GROUPS: tuple[tuple[FigureTolerance, tuple[str, ...]], ...] = (
    (
        PLATFORM_INDEPENDENT,
        (
            "samples_in_window",
            "target_secondary_bands_in_split",
            "median_truth_roi_width_px",
            "max_truth_roi_width_px",
            "median_lane_pitch_px",
            "width_over_pitch_iou_ceiling",
            "truth_overlapping_bands",
        ),
    ),
    (
        DETECTION_COUNT,
        (
            "lane_true_positives",
            "lane_false_positives",
            "lane_false_negatives",
            "band_true_positives",
            "band_false_positives",
            "band_false_negatives",
            "matched",
            "clean_count",
            "bands",
            "missed_total",
            "target",
            "target_secondary",
            "housekeeping",
            "doublet",
            "sharp",
            "smeared",
            "unflagged",
            "overlapping_only",
            "saturated_only",
            "both_flags",
            "overlapping_total",
            "overlapping_primary_bands",
        ),
    ),
    (DETECTION_RATE, ("lane_f1", "band_f1", "band_precision", "band_recall")),
    (
        ERROR_POINT,
        (
            "mean_absolute_percent",
            "median_absolute_percent",
            "clean_signed_percent",
            "clean_mean_absolute_percent",
            "clean_median_absolute_percent",
        ),
    ),
    (
        SUBSET_ERROR_POINT,
        (
            "overlapping_signed_mean_percent",
            "overlapping_primary_signed_mean_percent",
            "overlapping_primary_signed_median_percent",
        ),
    ),
    (COUNT_SHARE, ("overlapping_share_percent",)),
    (
        BOOTSTRAP_POINT,
        (
            "bootstrap_standard_error_percent",
            "difference_from_shipped_percent",
            "difference_ci_low_percent",
            "difference_ci_high_percent",
        ),
    ),
    (ROI_MEDIAN_PIXEL, ("median_width_px", "median_height_px")),
    (
        EXTREME_RELATIVE,
        (
            "max_absolute_percent",
            "included_max_absolute_percent",
            "all_max_absolute_percent",
            "max_peak_fraction_among_truth_low",
            "max_width_px",
            "max_height_px",
            "max_area_width_px",
            "max_area_height_px",
        ),
    ),
    (ROI_AREA, ("median_window_coverage_percent", "max_window_coverage_percent")),
    (MONTE_CARLO, ("whole_image_variance_reduction", "interior_variance_reduction")),
    (
        QC_FLAG_COUNT,
        (
            "items",
            "true_positives",
            "false_positives",
            "false_negatives",
            "true_negatives",
            "reference_items",
            "non_reference_items",
            "fired_with_reference",
            "fired_without_reference",
            "ratios",
            "included_ratios",
            "excluded_ratios",
            "reference_flagged_ratios",
            "used_lanes",
            "skipped_lanes",
            "same_lane_band_pairs",
            "pairs_at_or_above_threshold",
            "detected_bands_flagged",
            "censored_asymmetry_bands",
            "truth_low_dynamic_range_images",
        ),
    ),
    (
        NORMALIZATION_ERROR,
        (
            "included_mean_absolute_percent",
            "included_median_absolute_percent",
            "all_mean_absolute_percent",
            "all_median_absolute_percent",
        ),
    ),
)
"""The class each figure name takes by default, read as the most permissive of the meanings
that name carries in the record. A name that means something tighter in one particular row
is tightened there, in :data:`_CONTEXT_TOLERANCE_GROUPS`, not here."""

IMAGE_FLAG_COUNT = FigureTolerance("QC image count", bound=2.0)
"""+/-2 counts, for the image-scope rows of the QC record. Observed CI drift: none.

Tighter than :data:`QC_FLAG_COUNT`, and derived rather than inherited: an image flag is one
decision per image over a 30-image split, where four counts would be 13% of the population. The
mechanism that can move one is a whole-pixel ROI shift changing a band's clipped-pixel count or
its peak, and the CI drift that class comes from moved 2 of 303 band ROIs -- so at most two
images can flip on the drift that has actually been observed. ``lossy_format`` cannot move at
all, being read from the container bytes.

An override may only tighten, which this does; ``tests/test_sweep_check.py`` asserts that
against the committed record."""

_CONTEXT_TOLERANCE_GROUPS: dict[
    tuple[str, str], tuple[tuple[FigureTolerance, tuple[str, ...]], ...]
] = {
    **{
        context: (
            # `items` is the image count, which the record header compares exactly, so it takes
            # the exact class rather than inheriting a tolerance meant for decisions.
            (PLATFORM_INDEPENDENT, ("items",)),
            (
                IMAGE_FLAG_COUNT,
                (
                    "true_positives",
                    "false_positives",
                    "false_negatives",
                    "true_negatives",
                ),
            ),
        )
        for context in (
            *(("qc_flag_accuracy", f"image.{flag}") for flag in IMAGE_QC_FLAGS),
            *(
                ("qc.dynamic_range_min_peak_fraction", str(threshold))
                for threshold in DYNAMIC_RANGE_THRESHOLDS
            ),
        )
    },
    ("band_roi_sizes", "truth"): (
        (
            PLATFORM_INDEPENDENT,
            (
                "bands",
                "median_width_px",
                "max_width_px",
                "median_height_px",
                "max_height_px",
                "median_window_coverage_percent",
                "max_window_coverage_percent",
                "max_area_width_px",
                "max_area_height_px",
            ),
        ),
    ),
}
"""Overrides keyed by ``(sweep name, value label)``, because a figure's class is a property
of the quantity and not of its name.

``band_roi_sizes`` records the same nine figures twice: once over the *detected* ROIs, where
every one of them moves with the detection, and once over the *truth* ROIs, which are read
straight out of ``data/ground_truth/`` and divided by a config parameter. Keyed by name
alone, the truth row would inherit the detected row's tolerances -- ``bands`` = the truth
band count, tolerated +/-4 while the identical number in the record header is compared
exactly, and gold-set drift passing a check whose whole claim is that it re-measures from
the committed gold set.

An override may only tighten; ``tests/test_sweep_check.py`` asserts that against the
committed record, so this table cannot become a place where an inconvenient figure is
quietly let out."""


def _index_tolerances(
    groups: Sequence[tuple[FigureTolerance, Sequence[str]]],
) -> dict[str, FigureTolerance]:
    """Return one tolerance per figure name, rejecting a name claimed by two classes."""
    index: dict[str, FigureTolerance] = {}
    for tolerance, fields in groups:
        for field in fields:
            if field in index:
                raise ValueError(
                    f"the recorded figure {field!r} is claimed by both the "
                    f"{index[field].name!r} and the {tolerance.name!r} tolerance class; "
                    f"give it exactly one class per context"
                )
            index[field] = tolerance
    return index


FIGURE_TOLERANCES: dict[str, FigureTolerance] = _index_tolerances(_DEFAULT_TOLERANCE_GROUPS)
"""Figure name -> the tolerance ``--check`` compares it within, absent a context override."""

CONTEXT_TOLERANCES: dict[tuple[str, str], dict[str, FigureTolerance]] = {
    context: _index_tolerances(groups) for context, groups in _CONTEXT_TOLERANCE_GROUPS.items()
}
"""(sweep, value label) -> the figure names that take a different class in that one row."""


def tolerance_for(sweep: str, label: str, field: str) -> FigureTolerance:
    """Return the tolerance governing ``field`` of value ``label`` in ``sweep``, or raise.

    Resolution is by ``(sweep, label, field)`` first and by field name second, so one name
    may mean a platform-independent quantity in one row and a measured one in another.
    """
    context = CONTEXT_TOLERANCES.get((sweep, label), {})
    if field in context:
        return context[field]
    tolerance = FIGURE_TOLERANCES.get(field)
    if tolerance is None:
        raise UnclassifiedFigureError(
            f"the recorded figure {field!r} (in {sweep}={label}) belongs to no tolerance "
            f"class, so 'python -m evals.sweep --check' cannot compare it. Add it to a group "
            f"in _DEFAULT_TOLERANCE_GROUPS in evals/sweep.py -- or, if the name already means "
            f"something else elsewhere in the record, to _CONTEXT_TOLERANCE_GROUPS -- "
            f"choosing the class from how the figure moves when a band's ROI shifts by a "
            f"pixel, and record the choice in NOTES.md"
        )
    return tolerance


def _absent_names(
    kind: str, prefix: str, committed: set[str], measured: set[str]
) -> Iterator[str]:
    """Yield one message per name on only one side. Structure is compared exactly, both ways."""
    for name in sorted(measured - committed):
        yield f"{prefix}{name}: this {kind} is not in the committed record"
    for name in sorted(committed - measured):
        yield f"{prefix}{name}: this {kind} is in the committed record but was not measured"


def _value_differences(
    sweep: str, label: str, committed: dict[str, Any], measured: dict[str, Any]
) -> Iterator[str]:
    """Yield one message per figure of one sweep value that has moved beyond its tolerance."""
    where = f"{sweep}={label}"
    yield from _absent_names("field", f"{where}.", set(committed), set(measured))
    for field in sorted(set(committed) & set(measured)):
        if field == LABEL_FIELD:
            continue
        tolerance = tolerance_for(sweep, label, field)
        for value, origin in ((committed[field], "committed"), (measured[field], "measured")):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise UnclassifiedFigureError(
                    f"the {origin} value of {where}.{field} is {value!r}, which is not a "
                    f"number; '--check' compares figures numerically, so a sweep value may "
                    f"hold only numbers beside its {LABEL_FIELD!r}"
                )
        if tolerance.exceeded_by(committed[field], measured[field]):
            yield (
                f"{where}.{field}: recorded {committed[field]!r}, measured "
                f"{measured[field]!r}, which is outside {tolerance.describe()}"
            )


def _header_differences(measured: dict[str, Any], committed: dict[str, Any]) -> Iterator[str]:
    """Yield one message per header field that differs. All are compared exactly."""
    for key in EXACT_HEADER_FIELDS:
        if measured.get(key) == committed.get(key):
            continue
        message = f"{key}: recorded {committed.get(key)!r}, measured {measured.get(key)!r}"
        if key == "shipped":
            # The parameter-move guarantee. Kept exact on purpose: no figure tolerance is
            # ever wide enough to hide a parameter change, because a changed parameter
            # changes the digest, and the digest is compared bit for bit.
            message += (
                " -- a shipped config parameter has moved, so the whole record is stale; "
                "re-run 'python -m evals.sweep'"
            )
        yield message


def _values_by_label(sweep: str, values: Sequence[dict[str, Any]], origin: str) -> dict[
    str, dict[str, Any]
]:
    """Return one sweep's values keyed by label, rejecting a label that occurs twice."""
    by_label: dict[str, dict[str, Any]] = {}
    for position, value in enumerate(values):
        if LABEL_FIELD not in value:
            raise MalformedRecordError(
                f"value {position} of the {sweep} sweep in the {origin} record has no "
                f"{LABEL_FIELD!r}; every value is identified by one, and --check pairs the "
                f"two records on it"
            )
        label = str(value[LABEL_FIELD])
        if label in by_label:
            raise DuplicateValueLabelError(
                f"the {origin} record has two values labelled {label!r} in the {sweep} "
                f"sweep; labels are how --check pairs values, so they must be unique -- note "
                f"that a numeric and a string label rendering the same text collide here"
            )
        by_label[label] = value
    return by_label


def _sweep_values(name: str, sweep: dict[str, Any], origin: str) -> Sequence[dict[str, Any]]:
    """Return one sweep's list of values, or raise if the record does not carry one."""
    values = sweep.get("values")
    if not isinstance(values, list):
        raise MalformedRecordError(
            f"the {name} sweep in the {origin} record has no list of 'values' "
            f"(found {type(values).__name__}); regenerate it with 'python -m evals.sweep'"
        )
    return values


def _sweep_differences(measured: dict[str, Any], committed: dict[str, Any]) -> Iterator[str]:
    """Yield one message per sweep, value, field or figure that differs."""
    measured_sweeps: dict[str, Any] = measured.get("sweeps", {})
    committed_sweeps: dict[str, Any] = committed.get("sweeps", {})
    yield from _absent_names("sweep", "", set(committed_sweeps), set(measured_sweeps))
    for name in sorted(set(committed_sweeps) & set(measured_sweeps)):
        fresh, previous = measured_sweeps[name], committed_sweeps[name]
        for field in SWEEP_METADATA_FIELDS:
            if fresh.get(field) != previous.get(field):
                yield (
                    f"{name}.{field}: recorded {previous.get(field)!r}, measured "
                    f"{fresh.get(field)!r}"
                )
        fresh_values = _values_by_label(name, _sweep_values(name, fresh, "measured"), "measured")
        previous_values = _values_by_label(
            name, _sweep_values(name, previous, "committed"), "committed"
        )
        yield from _absent_names(
            "value", f"{name}=", set(previous_values), set(fresh_values)
        )
        for label in sorted(set(previous_values) & set(fresh_values)):
            yield from _value_differences(
                name, label, previous_values[label], fresh_values[label]
            )


def _markdown_differences(committed: dict[str, Any]) -> Iterator[str]:
    """Yield a message if ``MARKDOWN_PATH`` is not what the *committed* record renders.

    Rendered from the committed JSON rather than from the fresh measurement on purpose:
    that makes this a transcription check between two committed files, which is exact and
    platform-independent. Rendering the measurement instead would drag every figure in the
    report back to an exact comparison through the back door, defeating the tolerances.
    """
    if not MARKDOWN_PATH.is_file():
        yield f"{MARKDOWN_PATH}: missing"
    elif MARKDOWN_PATH.read_text(encoding="utf-8") != _markdown(committed):
        yield (
            f"{MARKDOWN_PATH}: does not match what the committed record renders; "
            f"re-run 'python -m evals.sweep'"
        )


def _differences(measured: dict[str, Any], committed: dict[str, Any]) -> Iterator[str]:
    """Yield one message per difference between a fresh measurement and the committed record.

    Structure, the header, the per-sweep prose and ``evals/dev_sweeps.md`` are compared
    exactly; the measured figures are compared within their :class:`FigureTolerance` class.
    """
    yield from _markdown_differences(committed)
    yield from _header_differences(measured, committed)
    yield from _sweep_differences(measured, committed)


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the sweep tool."""
    parser = argparse.ArgumentParser(
        prog="python -m evals.sweep",
        description=(
            f"Re-run every dev-split parameter sweep quoted in NOTES.md and configs/. "
            f"Reads the {EVALUATED_SPLIT} split only."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare against the committed record instead of rewriting it; exit 1 on drift",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Regenerate or verify the sweep record. Returns 0 on success, 1 on drift."""
    args = build_parser().parse_args(argv)
    print(f"running sweeps over the {EVALUATED_SPLIT} split", file=sys.stderr)
    # "measured" throughout, never "recorded": in every message this tool prints, the
    # recorded figure is the committed one and the measured figure is this fresh run's.
    measured = run_sweeps()

    if args.check:
        if not JSON_PATH.is_file():
            print(f"error: no committed record at {JSON_PATH}", file=sys.stderr)
            return 1
        committed = json.loads(JSON_PATH.read_text(encoding="utf-8"))
        try:
            drift = list(_differences(measured, committed))
        except RecordError as error:
            # An unclassified or non-numeric figure, or a duplicated value label: the
            # records cannot be compared at all. Reported as a failure of this tool rather
            # than as a traceback, because it is the author's to fix, not a crash.
            print(f"FAIL: {JSON_PATH} cannot be compared with the fresh measurement:")
            print(f"  {error}")
            return 1
        if drift:
            print(f"FAIL: the committed record does not reproduce, {len(drift)} difference(s):")
            for message in drift:
                print(f"  {message}")
            return 1
        print(
            f"OK: {JSON_PATH} reproduces -- structure, header and config digests exactly, "
            f"every figure within its tolerance class"
        )
        return 0

    require_writable_destination(JSON_PATH.parent)
    JSON_PATH.write_text(json.dumps(measured, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    MARKDOWN_PATH.write_text(_markdown(measured), encoding="utf-8")
    print(f"wrote {JSON_PATH} and {MARKDOWN_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
