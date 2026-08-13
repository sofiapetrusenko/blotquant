"""Run the pipeline over the synthetic dev split and print what it measured.

Four tables: detection and intensity recovery per image, QC flag accuracy against ground
truth's own labels, the ``unresolved_shoulder`` coincidence -- which is not an accuracy, because
ground truth has no such label -- and normalized-ratio error per mode.

Split discipline (PLAN.md): this runner evaluates :data:`EVALUATED_SPLIT` and nothing
else. There is no flag to point it at the test split -- the test split is run once per
phase by a separate, later harness, and a runner that *could* be pointed at it is a
runner that eventually is. Parameter iteration happens against this table only.

**The housekeeping normalization figures rest on an oracle**
(:data:`ORACLE_REFERENCE_ROLE`): the reference band is designated from ground truth's role,
which is an input a real blot does not supply. The disclosure is printed with the table, not
buried here, because a reader of the number is who needs it.

Every metric, including the micro-average that pools per-image counts and the flag scores,
comes from :mod:`evals.metrics`; this module does no scoring arithmetic of its own.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from evals.metrics import (
    PLAN_IOU_THRESHOLD,
    BoundingBox,
    DetectionScores,
    ErrorScores,
    FlagCoincidence,
    QcScores,
    detection_scores,
    flag_coincidence,
    intensity_recovery_error,
    micro_average_detection_scores,
    normalization_ratio_error,
    qc_flag_accuracy,
)
from pipeline.analyze import analyze_image
from pipeline.config import (
    HOUSEKEEPING_SINGLE,
    TOTAL_PROTEIN,
    PipelineConfig,
    load_config,
)
from pipeline.detect import Roi
from pipeline.errors import PipelineError
from pipeline.normalize import NormalizationBand, normalize
from pipeline.qc import (
    BAND_QC_FLAGS,
    IMAGE_QC_FLAGS,
    OVERLAPPING,
    UNRESOLVED_SHOULDER,
    max_same_lane_iou,
    roi_iou,
)

EVALUATED_SPLIT = "dev"
"""The only split this runner reads. Test is reported once per phase, elsewhere."""

SATURATED_FLAG = "saturated"
"""Ground-truth band flag whose true intensity is unrecoverable by construction."""

GROUND_TRUTH_BAND_FLAGS: tuple[str, ...] = (SATURATED_FLAG, OVERLAPPING)
"""The band flag vocabulary ground truth uses, and therefore the only one that can be scored
like for like. The pipeline also emits ``unresolved_shoulder``, which has no counterpart
here; it is reported separately as a coincidence rather than renamed onto ``overlapping``."""

ORACLE_REFERENCE_ROLE = "housekeeping"
"""The ground-truth ``role`` the housekeeping eval takes its reference bands from.

**This is an oracle input and does not exist on a real blot.** Which band is the loading
control is not visible in the pixels; the pipeline refuses to infer it (human ruling) and the
caller supplies it. Here the caller is this eval, and it reads the answer out of ground
truth. Every housekeeping figure below is therefore conditional on a *correct* reference and
is not evidence that anything can find one. The printed report says so, in the table."""


@dataclass(frozen=True)
class ImageEvaluation:
    """Scores for one image, plus the matched intensity pairs it contributed."""

    image_id: str
    lanes: DetectionScores
    bands: DetectionScores
    true_intensities: tuple[float, ...]
    predicted_intensities: tuple[float, ...]
    saturated: tuple[bool, ...]


@dataclass(frozen=True)
class RatioComparison:
    """One normalized ratio against its ground-truth value, with the caveats it carries."""

    key: str
    true_ratio: float
    predicted_ratio: float
    excluded: bool
    reference_qc_flagged: bool


@dataclass(frozen=True)
class QcEvaluation:
    """QC flags and normalization ratios for one image, joined to ground truth.

    Band flags are joined on the *matched* band pairs only: an undetected truth band has no
    predicted flags, and a false-positive detection has no truth flags, so neither can be
    scored as a flag decision. Detection is scored separately, by
    :func:`evals.metrics.detection_scores`, and the number of pairs is reported beside the
    flag table so a reader knows what the denominator was.

    ``clipped_pixel_counts``, ``row_half_width_ratios`` and ``max_same_lane_ious`` are the
    pipeline's own observations for those same bands, so that a threshold sweep can re-apply
    :func:`pipeline.qc.is_saturated`, :func:`pipeline.qc.is_unresolved_shoulder` and
    :func:`pipeline.qc.is_overlapping` to them. That is what keeps the recorded sensitivity
    surfaces a property of the shipped criteria rather than a second copy of them, and it costs
    no extra pass over the images. The first two are read straight out of the result document;
    the third is :func:`pipeline.qc.max_same_lane_iou` applied to the document's own ROIs, which
    is the same function ``assess`` uses on the same rectangles.

    Four quantities here are *not* per matched band, because the evidence for the human's
    Ruling 1 is about the detector's output as a whole -- how often any two ROIs in one lane
    overlap at all, including where a false-positive detection overlaps a real band, which is
    exactly the case the geometric flag exists to catch. ``same_lane_pair_ious`` is the overlap
    of every same-lane *pair* of detected bands and ``detected_max_same_lane_ious`` the largest
    overlap per detected *band*, both over all detections;
    ``censored_row_half_width_ratios`` counts the matched bands whose asymmetry could not be
    measured, so that a blind spot in the shape test becomes visible if it ever grows from its
    current zero; and ``brightest_peak_value`` with ``max_value`` are what the image-level
    dynamic-range criterion reads.

    ``normalization_warnings`` are the housekeeping re-run's; ``shipped_normalization_warnings``
    are the ones the *shipped* mode actually emitted, which is what a reader of a real run sees.
    """

    image_id: str
    truth_image_flags: tuple[str, ...]
    predicted_image_flags: tuple[str, ...]
    band_keys: tuple[str, ...]
    truth_band_flags: tuple[tuple[str, ...], ...]
    predicted_band_flags: tuple[tuple[str, ...], ...]
    clipped_pixel_counts: tuple[int, ...]
    row_half_width_ratios: tuple[float | None, ...]
    max_same_lane_ious: tuple[float, ...]
    same_lane_pair_ious: tuple[float, ...]
    detected_max_same_lane_ious: tuple[float, ...]
    censored_row_half_width_ratios: int
    brightest_peak_value: float
    max_value: int
    housekeeping: tuple[RatioComparison, ...]
    total_protein: tuple[RatioComparison, ...]
    truth_lanes: int
    used_housekeeping_lanes: int
    skipped_housekeeping_lanes: int
    used_total_protein_lanes: int
    skipped_total_protein_lanes: int
    normalization_warnings: tuple[str, ...]
    shipped_normalization_warnings: tuple[str, ...]


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
) -> tuple[ImageEvaluation, QcEvaluation]:
    """Run the pipeline on one gold-set image and score it against its ground truth.

    Returns the detection/recovery scores and the QC/normalization scores. The pipeline runs
    once: the housekeeping ratios are produced by re-running :func:`pipeline.normalize.normalize`
    over the *same* measured intensities and QC flags with the mode replaced, rather than by
    analysing the image a second time.
    """
    result = analyze_image(
        data_dir / truth["image_path"], config, ground_truth_image_id=truth["image_id"]
    )
    evaluation = score_result(truth, result, iou_threshold)
    return evaluation, score_qc(truth, result, evaluation, config)


def _matched_band_ids(
    truth: dict[str, Any], result: Mapping[str, Any], evaluation: ImageEvaluation
) -> dict[str, str]:
    """Return the detected band id each matched truth band id was paired with."""
    return {
        truth["bands"][truth_index]["band_id"]: result["bands"][predicted_index]["band_id"]
        for truth_index, predicted_index, _ in evaluation.bands.matches
    }


def _bands_for_normalization(result: Mapping[str, Any]) -> list[NormalizationBand]:
    """Return the result's bands in the shape :func:`pipeline.normalize.normalize` takes."""
    return [
        NormalizationBand(
            band_id=band["band_id"],
            lane_id=band["lane_id"],
            intensity=float(band["integrated_intensity"]),
            qc_flags=tuple(band["qc_flags"]),
        )
        for band in result["bands"]
    ]


def _oracle_reference_ids(
    truth: dict[str, Any], result: Mapping[str, Any], matched: Mapping[str, str]
) -> list[str]:
    """Return the detected band ids matched to ground truth's reference-role bands.

    The oracle: see :data:`ORACLE_REFERENCE_ROLE`. A truth reference band that was not
    detected contributes nothing, so its lane ends up with no reference and normalization
    records that rather than guessing one.

    Raises :class:`ValueError` when two truth reference bands matched detections in the *same*
    detected lane. ``housekeeping_single`` would refuse that lane, and rightly -- but the fault
    is in this oracle designation, not in the pipeline, so it is reported as a scoring
    precondition rather than surfacing as a pipeline error in the failure list. No image of the
    committed dev split reaches it.
    """
    references = [
        matched[band["band_id"]]
        for band in truth["bands"]
        if band["role"] == ORACLE_REFERENCE_ROLE and band["band_id"] in matched
    ]
    lanes = [
        band["lane_id"] for band in result["bands"] if band["band_id"] in set(references)
    ]
    crowded = sorted({lane for lane in lanes if lanes.count(lane) > 1})
    if crowded:
        raise ValueError(
            f"the oracle reference designation put more than one {ORACLE_REFERENCE_ROLE!r} band "
            f"into detected lane(s) {crowded} of {truth['image_id']}, which no housekeeping mode "
            f"can normalize; this is a limitation of scoring from ground-truth roles, not a "
            f"pipeline failure"
        )
    return references


def _housekeeping_comparisons(
    truth: dict[str, Any],
    result: Mapping[str, Any],
    matched: Mapping[str, str],
    config: PipelineConfig,
) -> tuple[tuple[RatioComparison, ...], int, int, tuple[str, ...]]:
    """Return the housekeeping ratios, the lanes used, the lanes skipped, and the warnings.

    A truth lane is skipped when either of the two bands its truth ratio names went
    undetected, or when the two were detected into *different* lanes -- then the predicted
    ratio would not be the quotient the truth ratio describes. Skipped lanes are counted and
    reported, never quietly dropped, and ``used + skipped`` is the number of truth ratios the
    ground truth carries, which is one per truth lane.
    """
    normalization = normalize(
        _bands_for_normalization(result),
        [lane["lane_id"] for lane in result["lanes"]],
        replace(config.normalization, mode=HOUSEKEEPING_SINGLE),
        reference_band_ids=_oracle_reference_ids(truth, result, matched),
        lossy_format=bool(result["source"]["lossy_format"]),
    )
    by_numerator = {ratio.numerator_band_id: ratio for ratio in normalization.ratios}
    comparisons: list[RatioComparison] = []
    skipped = 0
    used = 0
    for entry in truth["normalization"]["ratios"]:
        numerator = matched.get(entry["numerator_band_id"])
        denominator = matched.get(entry["denominator_band_id"])
        ratio = by_numerator.get(numerator) if numerator is not None else None
        if denominator is None or ratio is None or ratio.denominator_band_ids != (denominator,):
            skipped += 1
            continue
        used += 1
        comparisons.append(
            RatioComparison(
                key=f"{truth['image_id']}:{entry['lane_id']}",
                true_ratio=float(entry["true_ratio"]),
                predicted_ratio=ratio.ratio,
                excluded=ratio.excluded,
                reference_qc_flagged=ratio.reference_qc_flagged,
            )
        )
    return tuple(comparisons), used, skipped, normalization.warnings


def _total_protein_comparisons(
    truth: dict[str, Any],
    result: Mapping[str, Any],
    evaluation: ImageEvaluation,
) -> tuple[tuple[RatioComparison, ...], int, int]:
    """Return the total-protein ratios, the lanes used, and the lanes skipped.

    The ground-truth analogue of a total-protein denominator is the sum of the *true*
    integrated intensities of the bands in that lane, since the pipeline's denominator is the
    lane's whole background-corrected integral. The two only describe the same set of bands
    when every truth band of the lane was detected inside the matched detected lane, so a
    lane is used only then. **Every truth lane is accounted for**: the walk is over the truth
    lanes, not over the matched pairs, so a lane that detection never matched is counted as
    skipped rather than falling out of both totals. That is what makes ``used + skipped`` equal
    the split's truth-lane count for this mode as it already does for housekeeping -- the two
    rows of the printed table would otherwise have different denominators without saying so.
    The pipeline's own ratios are read from the result rather than recomputed.
    """
    if result["normalization"]["mode"] != TOTAL_PROTEIN:
        return (), 0, len(truth["lanes"])
    matched_bands = dict(
        (truth_index, predicted_index)
        for truth_index, predicted_index, _ in evaluation.bands.matches
    )
    matched_lanes = {
        truth_index: predicted_index
        for truth_index, predicted_index, _ in evaluation.lanes.matches
    }
    ratios = {
        ratio["numerator_band_id"]: ratio for ratio in result["normalization"]["ratios"]
    }
    comparisons: list[RatioComparison] = []
    used = 0
    skipped = 0
    for truth_index in range(len(truth["lanes"])):
        if truth_index not in matched_lanes:
            # Detection did not find this lane at all. It is a detection failure, scored by
            # detection_scores -- but it is still a truth lane that produced no ratio, so it is
            # counted here rather than disappearing from both columns.
            skipped += 1
            continue
        predicted_index = matched_lanes[truth_index]
        truth_lane = truth["lanes"][truth_index]["lane_id"]
        detected_lane = result["lanes"][predicted_index]["lane_id"]
        indices = [
            index
            for index, band in enumerate(truth["bands"])
            if band["lane_id"] == truth_lane
        ]
        paired = [
            (index, matched_bands[index])
            for index in indices
            if index in matched_bands
            and result["bands"][matched_bands[index]]["lane_id"] == detected_lane
        ]
        if len(paired) != len(indices) or not paired:
            skipped += 1
            continue
        truth_total = sum(
            float(truth["bands"][index]["true_integrated_intensity_dn"]) for index in indices
        )
        usable = [
            (index, ratios[result["bands"][predicted]["band_id"]])
            for index, predicted in paired
            if result["bands"][predicted]["band_id"] in ratios
        ]
        if len(usable) != len(paired):
            skipped += 1
            continue
        used += 1
        for index, ratio in usable:
            comparisons.append(
                RatioComparison(
                    key=f"{truth['image_id']}:{truth['bands'][index]['band_id']}",
                    true_ratio=float(truth["bands"][index]["true_integrated_intensity_dn"])
                    / truth_total,
                    predicted_ratio=float(ratio["ratio"]),
                    excluded=bool(ratio["excluded"]),
                    reference_qc_flagged=bool(ratio["reference_qc_flagged"]),
                )
            )
    return tuple(comparisons), used, skipped


def score_qc(
    truth: dict[str, Any],
    result: Mapping[str, Any],
    evaluation: ImageEvaluation,
    config: PipelineConfig,
) -> QcEvaluation:
    """Join one image's QC flags and normalized ratios to its ground truth."""
    matched = _matched_band_ids(truth, result, evaluation)
    predicted_bands = {band["band_id"]: band for band in result["bands"]}
    keys: list[str] = []
    truth_flags: list[tuple[str, ...]] = []
    predicted_flags: list[tuple[str, ...]] = []
    clipped: list[int] = []
    ratios: list[float | None] = []
    detected_bands = [
        (band["band_id"], band["lane_id"], Roi(**band["roi"])) for band in result["bands"]
    ]
    overlaps_by_band = max_same_lane_iou(detected_bands)
    pair_ious = [
        roi_iou(first_roi, second_roi)
        for index, (_, first_lane, first_roi) in enumerate(detected_bands)
        for _, second_lane, second_roi in detected_bands[index + 1 :]
        if second_lane == first_lane
    ]
    overlaps: list[float] = []
    for truth_band in truth["bands"]:
        detected = matched.get(truth_band["band_id"])
        if detected is None:
            continue
        band = predicted_bands[detected]
        keys.append(f"{truth['image_id']}:{truth_band['band_id']}")
        truth_flags.append(tuple(truth_band["qc_flags"]))
        predicted_flags.append(tuple(band["qc_flags"]))
        clipped.append(int(band["clipped_pixel_count"]))
        ratio = band["row_half_width_ratio"]
        ratios.append(None if ratio is None else float(ratio))
        overlaps.append(overlaps_by_band[detected])
    housekeeping, used_housekeeping, skipped_housekeeping, warnings = _housekeeping_comparisons(
        truth, result, matched, config
    )
    total_protein, used_total_protein, skipped_total_protein = _total_protein_comparisons(
        truth, result, evaluation
    )
    return QcEvaluation(
        image_id=truth["image_id"],
        truth_image_flags=tuple(truth["image_qc_flags"]),
        predicted_image_flags=tuple(result["image_qc_flags"]),
        band_keys=tuple(keys),
        truth_band_flags=tuple(truth_flags),
        predicted_band_flags=tuple(predicted_flags),
        clipped_pixel_counts=tuple(clipped),
        row_half_width_ratios=tuple(ratios),
        max_same_lane_ious=tuple(overlaps),
        same_lane_pair_ious=tuple(pair_ious),
        detected_max_same_lane_ious=tuple(
            overlaps_by_band[band_id] for band_id, _, _ in detected_bands
        ),
        censored_row_half_width_ratios=sum(1 for ratio in ratios if ratio is None),
        brightest_peak_value=max(
            (float(band["peak_value"]) for band in result["bands"]), default=0.0
        ),
        max_value=int(result["source"]["max_value"]),
        housekeeping=housekeeping,
        total_protein=total_protein,
        truth_lanes=len(truth["lanes"]),
        used_housekeeping_lanes=used_housekeeping,
        skipped_housekeeping_lanes=skipped_housekeeping,
        used_total_protein_lanes=used_total_protein,
        skipped_total_protein_lanes=skipped_total_protein,
        normalization_warnings=warnings,
        shipped_normalization_warnings=tuple(result["normalization"]["warnings"]),
    )


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


ERROR_COLUMNS_WIDTH = 28
"""Width of the three error columns together, so the ``n/a`` case keeps the table aligned."""


def _format_error(scores: ErrorScores | None) -> str:
    """Return a compact ``mean/median/max`` rendering, or ``n/a`` when nothing matched.

    The ``n/a`` fills all three columns rather than one, so a row with nothing to score does not
    shift every column after it -- the normalization table has a column after these.
    """
    if scores is None:
        return f"{'n/a':>{ERROR_COLUMNS_WIDTH}}"
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


def _rate(value: float | None) -> str:
    """Render a rate, or ``n/a`` where it is genuinely undefined -- never as 0.0."""
    return "  n/a" if value is None else f"{value:.3f}"


def image_flag_scores(evaluations: Sequence[QcEvaluation]) -> QcScores:
    """Return QC flag accuracy over the images, against ground truth's image flags."""
    return qc_flag_accuracy(
        {item.image_id: list(item.truth_image_flags) for item in evaluations},
        {item.image_id: list(item.predicted_image_flags) for item in evaluations},
        list(IMAGE_QC_FLAGS),
    )


def band_flag_scores(evaluations: Sequence[QcEvaluation]) -> QcScores:
    """Return QC flag accuracy over matched bands, for the ground-truth flag vocabulary only.

    The pipeline's ``unresolved_shoulder`` is dropped from the predictions here, explicitly
    and only here, because :func:`evals.metrics.qc_flag_accuracy` scores a declared
    vocabulary and ground truth has no such flag. It is *not* mapped onto ``overlapping``:
    the two ask different questions, and it is reported on its own by
    :func:`shoulder_coincidence`.
    """
    truth: dict[str, list[str]] = {}
    predicted: dict[str, list[str]] = {}
    for item in evaluations:
        for key, truth_flags, predicted_flags in zip(
            item.band_keys, item.truth_band_flags, item.predicted_band_flags, strict=True
        ):
            truth[key] = list(truth_flags)
            predicted[key] = [flag for flag in predicted_flags if flag in GROUND_TRUTH_BAND_FLAGS]
    return qc_flag_accuracy(truth, predicted, list(GROUND_TRUTH_BAND_FLAGS))


def shoulder_coincidence(evaluations: Sequence[QcEvaluation]) -> FlagCoincidence:
    """Return how often ``unresolved_shoulder`` fires on truth ``overlapping`` bands, and not.

    A diagnostic, not an accuracy: the shape test and the geometric truth label are different
    questions asked of the same bands (see :class:`evals.metrics.FlagCoincidence`).
    """
    predicted: dict[str, list[str]] = {}
    truth: dict[str, list[str]] = {}
    for item in evaluations:
        for key, truth_flags, predicted_flags in zip(
            item.band_keys, item.truth_band_flags, item.predicted_band_flags, strict=True
        ):
            predicted[key] = list(predicted_flags)
            truth[key] = list(truth_flags)
    return flag_coincidence(
        truth,
        predicted,
        OVERLAPPING,
        UNRESOLVED_SHOULDER,
        list(GROUND_TRUTH_BAND_FLAGS),
        list(BAND_QC_FLAGS),
    )


def _ratio_error(comparisons: Sequence[RatioComparison]) -> ErrorScores | None:
    """Return the normalization ratio error over ``comparisons``, or ``None`` if empty."""
    if not comparisons:
        return None
    return normalization_ratio_error(
        {item.key: item.true_ratio for item in comparisons},
        {item.key: item.predicted_ratio for item in comparisons},
    )


def _print_qc_report(evaluations: Sequence[QcEvaluation], stream: Any) -> None:
    """Print QC flag accuracy against ground truth, plus the shoulder diagnostic."""
    image_scores = image_flag_scores(evaluations)
    band_scores = band_flag_scores(evaluations)
    print(file=stream)
    print(
        f"QC flag accuracy against ground truth: {image_scores.item_count} image(s), "
        f"{band_scores.item_count} matched band(s). A flag is scored only where truth and "
        f"prediction describe the same thing; 'n/a' is an undefined rate, never a zero.",
        file=stream,
    )
    header = f"{'scope  flag':<30}{'tp':>5}{'fp':>5}{'fn':>5}{'tn':>5}{'P':>8}{'R':>8}{'F1':>8}"
    print(header, file=stream)
    print("-" * len(header), file=stream)
    for scope, scores in (("image", image_scores), ("band ", band_scores)):
        for flag, item in scores.per_flag.items():
            print(
                f"{scope + '  ' + flag:<30}{item.true_positives:>5}{item.false_positives:>5}"
                f"{item.false_negatives:>5}{item.true_negatives:>5}"
                f"{_rate(item.precision):>8}{_rate(item.recall):>8}{_rate(item.f1):>8}",
                file=stream,
            )
    print(
        f"exact flag-set match: images {image_scores.exact_set_match_accuracy:.3f}, "
        f"matched bands {band_scores.exact_set_match_accuracy:.3f}",
        file=stream,
    )
    shoulder = shoulder_coincidence(evaluations)
    print(
        f"{shoulder.predicted_flag} is NOT scored above: ground truth has no such label, and "
        f"it is a test on the row profile's shape where truth's {shoulder.reference_flag} is a "
        f"test on ROI geometry. Reported as a coincidence instead, on the same "
        f"{shoulder.items} matched bands:",
        file=stream,
    )
    print(
        f"  fires on {shoulder.fired_with_reference}/{shoulder.reference_items} bands whose "
        f"truth carries {shoulder.reference_flag} (rate {_rate(shoulder.rate_with_reference)}), "
        f"and on {shoulder.fired_without_reference}/{shoulder.non_reference_items} that do not "
        f"(rate {_rate(shoulder.rate_without_reference)}).",
        file=stream,
    )


def _print_normalization_report(
    evaluations: Sequence[QcEvaluation], config: PipelineConfig, stream: Any
) -> None:
    """Print normalized-ratio error per mode, with the oracle disclosure in the table."""
    housekeeping = [item for evaluation in evaluations for item in evaluation.housekeeping]
    total_protein = [item for evaluation in evaluations for item in evaluation.total_protein]
    print(file=stream)
    print("normalization ratio error, by mode:", file=stream)
    header = (
        f"{'mode':<20}{'ratios':>8}{'included':>10}{'ref-flagged':>13}"
        f"{'mean|e|%':>10}{'med|e|%':>9}{'max|e|%':>9}{'reference':>28}"
    )
    print(header, file=stream)
    print("-" * len(header), file=stream)
    truth_lanes = sum(item.truth_lanes for item in evaluations)
    rows = (
        (
            HOUSEKEEPING_SINGLE,
            housekeeping,
            "ORACLE: ground-truth role",
            sum(item.used_housekeeping_lanes for item in evaluations),
            sum(item.skipped_housekeeping_lanes for item in evaluations),
            "skipped because a band of the truth ratio was undetected, or the two landed in "
            "different lanes",
        ),
        (
            TOTAL_PROTEIN,
            total_protein,
            "detected lane ROI integral",
            sum(item.used_total_protein_lanes for item in evaluations),
            sum(item.skipped_total_protein_lanes for item in evaluations),
            "skipped because the lane was not detected at all, or not every truth band of it "
            "was detected into it, so the two denominators would cover different bands",
        ),
    )
    # The table rows first, so the table is contiguous; the per-mode notes follow it.
    for mode, comparisons, reference, _used, _skipped, _note in rows:
        included = [item for item in comparisons if not item.excluded]
        error = _ratio_error(included)
        flagged = sum(1 for item in comparisons if item.reference_qc_flagged)
        print(
            f"{mode:<20}{len(comparisons):>8}{len(included):>10}{flagged:>13}"
            f"{_format_error(error)}{reference:>28}",
            file=stream,
        )
    for mode, comparisons, _, used, skipped, note in rows:
        everything = _ratio_error(comparisons)
        excluded = sum(1 for item in comparisons if item.excluded)
        print(
            f"  {mode}: {truth_lanes} truth lanes = {used} used + {skipped} {note}. Both modes "
            f"account for every truth lane, so the two rows share a denominator.",
            file=stream,
        )
        if everything is not None:
            print(
                f"  {mode}: over all {everything.count} ratios, including the {excluded} "
                f"excluded by QC: mean |error| {everything.mean_absolute_percent_error:.2f}%, "
                f"median {everything.median_absolute_percent_error:.2f}%",
                file=stream,
            )
    print(
        f"  ORACLE DISCLOSURE: the {HOUSEKEEPING_SINGLE} row designates its reference band "
        f"from ground truth's {ORACLE_REFERENCE_ROLE!r} role. That input does not exist on a "
        f"real blot: the pipeline refuses to infer a reference and requires the caller to name "
        f"one. These figures are therefore conditional on a CORRECT reference and are not "
        f"evidence that a reference can be found.",
        file=stream,
    )
    print(
        f"  {config.normalization.mode} is the mode configs/{config.config_id}.yaml ships, so "
        f"it is the one whose ratios come out of the result documents themselves; the other "
        f"mode is normalized from the same measured intensities and QC flags with the mode "
        f"replaced. housekeeping_multi needs at least two reference bands per lane and the "
        f"gold set has one, so it is verified against hand-computed fixtures in "
        f"tests/test_pipeline_normalize.py rather than scored here.",
        file=stream,
    )
    for label, collected in (
        (config.normalization.mode, "shipped_normalization_warnings"),
        (HOUSEKEEPING_SINGLE, "normalization_warnings"),
    ):
        warnings = sorted(
            {warning for item in evaluations for warning in getattr(item, collected)}
        )
        print(f"  {label} warnings raised across the split: {warnings}", file=stream)


def print_report(
    evaluations: Sequence[ImageEvaluation],
    failures: Sequence[tuple[str, str]],
    config: PipelineConfig,
    iou_threshold: float,
    stream: Any,
    qc_evaluations: Sequence[QcEvaluation],
) -> None:
    """Print the detection, recovery, QC and normalization tables for the evaluated split.

    ``qc_evaluations`` is required and has no default. An empty sequence still omits the QC and
    normalization sections rather than printing them empty -- an empty section would read as "QC
    ran and found nothing" -- but the caller has to say so, because a default would let a caller
    lose two whole sections by forgetting an argument. That is the shape ``normalize`` refuses
    for ``lossy_format``, for the same reason.
    """
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
    if qc_evaluations:
        _print_qc_report(qc_evaluations, stream)
        _print_normalization_report(qc_evaluations, config, stream)
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
    qc_evaluations: list[QcEvaluation] = []
    failures: list[tuple[str, str]] = []
    for truth in truths:
        try:
            evaluation, qc_evaluation = evaluate_image(
                truth, args.data, config, PLAN_IOU_THRESHOLD
            )
        except (PipelineError, FileNotFoundError, ValueError) as error:
            failures.append((truth["image_id"], f"{type(error).__name__}: {error}"))
            print(f"error: {truth['image_id']}: {error}", file=sys.stderr)
            continue
        evaluations.append(evaluation)
        qc_evaluations.append(qc_evaluation)
    if not evaluations:
        print("error: every image failed; no scores to report", file=sys.stderr)
        return 1
    print_report(
        evaluations, failures, config, PLAN_IOU_THRESHOLD, sys.stdout, qc_evaluations
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
