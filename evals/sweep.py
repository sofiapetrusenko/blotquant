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
)
from evals.run import EVALUATED_SPLIT, load_split, score_result
from pipeline.analyze import require_writable_destination
from pipeline.background import correct_background
from pipeline.config import PipelineConfig, load_config
from pipeline.detect import detect
from pipeline.load import load_image
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
        "note": (
            f"Paired bootstrap, {BOOTSTRAP_RESAMPLES} resamples of the {len(common)} bands "
            f"the whole sweep shares, seed {BOOTSTRAP_SEED}. `difference_from_shipped_percent` is "
            f"mean |error| minus the shipped {SHIPPED_APERTURE} value's, on the same "
            f"resample; its interval excludes 0 only where the ordering is resolved."
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


def _differences(recorded: dict[str, Any], committed: dict[str, Any]) -> Iterator[str]:
    """Yield one message per figure that has moved between two records.

    Also compares the rendered Markdown, because that is the file the transcription
    convention tells authors to copy from: a JSON that reproduces while the companion
    report has drifted would still mislead.
    """
    rendered = _markdown(recorded)
    if not MARKDOWN_PATH.is_file():
        yield f"{MARKDOWN_PATH}: missing"
    elif MARKDOWN_PATH.read_text(encoding="utf-8") != rendered:
        yield f"{MARKDOWN_PATH}: does not match what the current record renders"
    for key in ("split", "iou_threshold", "images", "truth_lanes", "truth_bands", "shipped"):
        if recorded.get(key) != committed.get(key):
            yield f"{key}: recorded {committed.get(key)!r}, measured {recorded.get(key)!r}"
    for name, sweep in recorded["sweeps"].items():
        if name not in committed.get("sweeps", {}):
            yield f"{name}: not in the committed record"
            continue
        old = {value["label"]: value for value in committed["sweeps"][name]["values"]}
        for value in sweep["values"]:
            previous = old.get(value["label"])
            if previous is None:
                yield f"{name}={value['label']}: not in the committed record"
                continue
            for field, measured in value.items():
                if previous.get(field) != measured:
                    yield (
                        f"{name}={value['label']}.{field}: recorded "
                        f"{previous.get(field)!r}, measured {measured!r}"
                    )


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
    recorded = run_sweeps()

    if args.check:
        if not JSON_PATH.is_file():
            print(f"error: no committed record at {JSON_PATH}", file=sys.stderr)
            return 1
        committed = json.loads(JSON_PATH.read_text(encoding="utf-8"))
        drift = list(_differences(recorded, committed))
        if drift:
            print(f"FAIL: {len(drift)} recorded figure(s) have moved:")
            for message in drift:
                print(f"  {message}")
            return 1
        print(f"OK: every figure in {JSON_PATH} reproduces")
        return 0

    require_writable_destination(JSON_PATH.parent)
    JSON_PATH.write_text(json.dumps(recorded, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    MARKDOWN_PATH.write_text(_markdown(recorded), encoding="utf-8")
    print(f"wrote {JSON_PATH} and {MARKDOWN_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
