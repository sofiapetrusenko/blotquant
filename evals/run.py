"""Run the pipeline over the synthetic dev split and print the detection/recovery table.

Split discipline (PLAN.md): this runner evaluates :data:`EVALUATED_SPLIT` and nothing
else. There is no flag to point it at the test split -- the test split is run once per
phase by a separate, later harness, and a runner that *could* be pointed at it is a
runner that eventually is. Parameter iteration happens against this table only.

Every metric, including the micro-average that pools per-image counts, comes from
:mod:`evals.metrics`; this module does no scoring arithmetic of its own.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.metrics import (
    PLAN_IOU_THRESHOLD,
    BoundingBox,
    DetectionScores,
    ErrorScores,
    detection_scores,
    intensity_recovery_error,
    micro_average_detection_scores,
)
from pipeline.analyze import analyze_image
from pipeline.config import PipelineConfig, load_config
from pipeline.errors import PipelineError

EVALUATED_SPLIT = "dev"
"""The only split this runner reads. Test is reported once per phase, elsewhere."""

SATURATED_FLAG = "saturated"
"""Ground-truth band flag whose true intensity is unrecoverable by construction."""


@dataclass(frozen=True)
class ImageEvaluation:
    """Scores for one image, plus the matched intensity pairs it contributed."""

    image_id: str
    lanes: DetectionScores
    bands: DetectionScores
    true_intensities: tuple[float, ...]
    predicted_intensities: tuple[float, ...]
    saturated: tuple[bool, ...]


def load_split(data_dir: Path) -> list[dict[str, Any]]:
    """Return every ground-truth document of :data:`EVALUATED_SPLIT`, ordered by image id."""
    truth_dir = data_dir / "ground_truth"
    if not truth_dir.is_dir():
        raise FileNotFoundError(
            f"no ground truth at {truth_dir}; generate it with "
            f"'python -m synth --seed 42 --out {data_dir}/'"
        )
    documents = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(truth_dir.glob("*.json"))
    ]
    selected = [document for document in documents if document["split"] == EVALUATED_SPLIT]
    if not selected:
        raise FileNotFoundError(
            f"{truth_dir} holds no image in the {EVALUATED_SPLIT!r} split; there is "
            f"nothing to evaluate"
        )
    return sorted(selected, key=lambda document: document["image_id"])


def evaluate_image(
    truth: dict[str, Any], data_dir: Path, config: PipelineConfig, iou_threshold: float
) -> ImageEvaluation:
    """Run the pipeline on one gold-set image and score it against its ground truth."""
    result = analyze_image(
        data_dir / truth["image_path"], config, ground_truth_image_id=truth["image_id"]
    )
    return score_result(truth, result, iou_threshold)


def score_result(
    truth: dict[str, Any], result: Mapping[str, Any], iou_threshold: float
) -> ImageEvaluation:
    """Score one already-computed result document against its ground truth.

    Split out from :func:`evaluate_image` so that a caller which has already run the
    pipeline -- ``evals/sweep.py`` reuses one background estimate across many detection
    variants -- scores its results through exactly this code rather than its own copy.
    ``result`` needs only the ``lanes`` and ``bands`` arrays.
    """
    lane_scores = detection_scores(
        [BoundingBox.from_mapping(lane["roi"]) for lane in truth["lanes"]],
        [BoundingBox.from_mapping(lane["roi"]) for lane in result["lanes"]],
        iou_threshold,
    )
    band_scores = detection_scores(
        [BoundingBox.from_mapping(band["roi"]) for band in truth["bands"]],
        [BoundingBox.from_mapping(band["roi"]) for band in result["bands"]],
        iou_threshold,
    )
    true_values: list[float] = []
    predicted_values: list[float] = []
    saturated: list[bool] = []
    for truth_index, predicted_index, _ in band_scores.matches:
        true_band = truth["bands"][truth_index]
        true_values.append(float(true_band["true_integrated_intensity_dn"]))
        predicted_values.append(float(result["bands"][predicted_index]["integrated_intensity"]))
        saturated.append(SATURATED_FLAG in true_band["qc_flags"])
    return ImageEvaluation(
        image_id=truth["image_id"],
        lanes=lane_scores,
        bands=band_scores,
        true_intensities=tuple(true_values),
        predicted_intensities=tuple(predicted_values),
        saturated=tuple(saturated),
    )


def _select(values: Sequence[float], keep: Sequence[bool]) -> list[float]:
    """Return the entries of ``values`` whose ``keep`` flag is true."""
    return [value for value, flag in zip(values, keep, strict=True) if flag]


def _format_error(scores: ErrorScores | None) -> str:
    """Return a compact ``mean/median/max`` rendering, or ``n/a`` when nothing matched."""
    if scores is None:
        return f"{'n/a':>10}"
    return (
        f"{scores.mean_absolute_percent_error:10.2f}"
        f"{scores.median_absolute_percent_error:9.2f}"
        f"{scores.max_absolute_percent_error:9.2f}"
    )


def _image_error(evaluation: ImageEvaluation) -> ErrorScores | None:
    """Return the recovery error for one image, or ``None`` if it matched no band."""
    if not evaluation.true_intensities:
        return None
    return intensity_recovery_error(evaluation.true_intensities, evaluation.predicted_intensities)


def print_report(
    evaluations: Sequence[ImageEvaluation],
    failures: Sequence[tuple[str, str]],
    config: PipelineConfig,
    iou_threshold: float,
    stream: Any,
) -> None:
    """Print the detection and recovery table for the evaluated split."""
    print(
        f"blotquant eval v0 -- split={EVALUATED_SPLIT} config={config.config_id} "
        f"background={config.background.method} IoU>={iou_threshold}",
        file=stream,
    )
    header = (
        f"{'image':<10}{'lane tp/fp/fn':>15}{'lane F1':>9}"
        f"{'band tp/fp/fn':>15}{'band F1':>9}{'n':>5}"
        f"{'mean|e|%':>10}{'med|e|%':>9}{'max|e|%':>9}"
    )
    print(header, file=stream)
    print("-" * len(header), file=stream)
    for evaluation in evaluations:
        lanes, bands = evaluation.lanes, evaluation.bands
        lane_counts = f"{lanes.true_positives}/{lanes.false_positives}/{lanes.false_negatives}"
        band_counts = f"{bands.true_positives}/{bands.false_positives}/{bands.false_negatives}"
        print(
            f"{evaluation.image_id:<10}{lane_counts:>15}{lanes.f1:>9.3f}"
            f"{band_counts:>15}{bands.f1:>9.3f}{len(evaluation.true_intensities):>5}"
            f"{_format_error(_image_error(evaluation))}",
            file=stream,
        )
    print("-" * len(header), file=stream)

    lanes = micro_average_detection_scores(
        [evaluation.lanes for evaluation in evaluations], iou_threshold
    )
    bands = micro_average_detection_scores(
        [evaluation.bands for evaluation in evaluations], iou_threshold
    )
    all_true = [value for evaluation in evaluations for value in evaluation.true_intensities]
    all_predicted = [
        value for evaluation in evaluations for value in evaluation.predicted_intensities
    ]
    all_saturated = [flag for evaluation in evaluations for flag in evaluation.saturated]
    pooled = intensity_recovery_error(all_true, all_predicted) if all_true else None
    unsaturated_keep = [not flag for flag in all_saturated]
    unsaturated = (
        intensity_recovery_error(
            _select(all_true, unsaturated_keep), _select(all_predicted, unsaturated_keep)
        )
        if any(unsaturated_keep)
        else None
    )
    total_counts = f"{bands.true_positives}/{bands.false_positives}/{bands.false_negatives}"
    lane_total = f"{lanes.true_positives}/{lanes.false_positives}/{lanes.false_negatives}"
    print(
        f"{'TOTAL':<10}{lane_total:>15}{lanes.f1:>9.3f}"
        f"{total_counts:>15}{bands.f1:>9.3f}{len(all_true):>5}"
        f"{_format_error(pooled)}",
        file=stream,
    )
    print(file=stream)
    print(
        f"lane detection : P={lanes.precision:.3f} R={lanes.recall:.3f} F1={lanes.f1:.3f} "
        f"({lanes.true_positives} tp, {lanes.false_positives} fp, {lanes.false_negatives} fn)",
        file=stream,
    )
    print(
        f"band detection : P={bands.precision:.3f} R={bands.recall:.3f} F1={bands.f1:.3f} "
        f"({bands.true_positives} tp, {bands.false_positives} fp, {bands.false_negatives} fn)",
        file=stream,
    )
    if pooled is not None:
        print(
            f"intensity recovery, all {pooled.count} matched bands            : "
            f"mean |error| {pooled.mean_absolute_percent_error:.2f}%  "
            f"median {pooled.median_absolute_percent_error:.2f}%  "
            f"max {pooled.max_absolute_percent_error:.2f}%",
            file=stream,
        )
    if unsaturated is not None:
        print(
            f"intensity recovery, {unsaturated.count} without a truth 'saturated' flag: "
            f"mean |error| {unsaturated.mean_absolute_percent_error:.2f}%  "
            f"median {unsaturated.median_absolute_percent_error:.2f}%  "
            f"max {unsaturated.max_absolute_percent_error:.2f}%",
            file=stream,
        )
    print(
        f"  ({sum(all_saturated)} matched bands carry the truth 'saturated' flag. Their true "
        f"intensity is the intensity the band would have had without clipping, which no "
        f"pipeline can recover from a clipped image, so their error is expected to be "
        f"large. They are included in the headline figure above and shown separately, "
        f"never dropped.)",
        file=stream,
    )
    if failures:
        print(file=stream)
        print(f"FAILED on {len(failures)} image(s):", file=stream)
        for image_id, message in failures:
            print(f"  {image_id}: {message}", file=stream)
        print(
            "  (a PipelineError or FileNotFoundError is an analysis failure; a ValueError "
            "may instead be a scoring precondition from evals/metrics.py -- e.g. an image "
            "with no true bands -- which is a gold-set problem, not a pipeline one.)",
            file=stream,
        )


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the eval runner."""
    parser = argparse.ArgumentParser(
        prog="python -m evals.run",
        description=(
            f"Run the pipeline over the synthetic {EVALUATED_SPLIT} split and print "
            f"detection F1 and intensity recovery error."
        ),
    )
    parser.add_argument(
        "--config", type=Path, required=True, help="pipeline YAML parameter set to evaluate"
    )
    parser.add_argument(
        "--data",
        type=Path,
        default=Path("data"),
        help="gold-set root holding ground_truth/ and images/",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the eval. Returns 0 when every image was analysed, 1 when any image failed."""
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    truths = load_split(args.data)

    evaluations: list[ImageEvaluation] = []
    failures: list[tuple[str, str]] = []
    for truth in truths:
        try:
            evaluations.append(evaluate_image(truth, args.data, config, PLAN_IOU_THRESHOLD))
        except (PipelineError, FileNotFoundError, ValueError) as error:
            failures.append((truth["image_id"], f"{type(error).__name__}: {error}"))
            print(f"error: {truth['image_id']}: {error}", file=sys.stderr)
    if not evaluations:
        print("error: every image failed; no scores to report", file=sys.stderr)
        return 1
    print_report(evaluations, failures, config, PLAN_IOU_THRESHOLD, sys.stdout)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
