"""Evaluation metrics.

Pure functions over plain data: nothing here imports :mod:`synth` or
:mod:`pipeline`, reads files, or knows how ground truth was produced. Every metric
either returns a number it can justify, returns ``None``, or raises. A zero is
returned only where zero is the honest score for a real failure: detection precision
when nothing was predicted (documented on :func:`detection_scores`), and per-flag QC
F1 whenever the flag occurs in the truth or in the predictions but produced no true
positive. ``None`` is reserved for quantities with no reference at all -- per-flag QC
precision when the flag was never predicted, recall when it never occurred, and F1
only when the flag is absent from both (see :class:`FlagScores`).

Thresholds are always explicit parameters: no metric has a default threshold.
:data:`PLAN_IOU_THRESHOLD` names the value PLAN.md specifies for reported detection
scores, and callers must pass it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

PLAN_IOU_THRESHOLD = 0.5
"""The IoU threshold PLAN.md fixes for reported detection precision/recall/F1."""


@dataclass(frozen=True)
class BoundingBox:
    """An integer pixel ROI covering columns ``x .. x+width-1`` and rows ``y .. y+height-1``."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        """Reject degenerate boxes, whose IoU would be undefined."""
        if self.width <= 0 or self.height <= 0:
            raise ValueError(
                f"bounding box must have positive extent, got width={self.width}, "
                f"height={self.height}"
            )

    @property
    def area(self) -> int:
        """Return the box area in pixels."""
        return self.width * self.height

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, int]) -> BoundingBox:
        """Build a box from a ``{"x","y","width","height"}`` mapping (the ROI schema shape)."""
        missing = {"x", "y", "width", "height"} - set(mapping)
        if missing:
            raise KeyError(f"ROI mapping is missing required keys {sorted(missing)}")
        return cls(
            x=int(mapping["x"]),
            y=int(mapping["y"]),
            width=int(mapping["width"]),
            height=int(mapping["height"]),
        )


@dataclass(frozen=True)
class DetectionScores:
    """Detection outcome at one IoU threshold."""

    iou_threshold: float
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float
    matches: tuple[tuple[int, int, float], ...]


@dataclass(frozen=True)
class ErrorScores:
    """Distribution of relative errors, in percent."""

    count: int
    per_item_percent_error: tuple[float, ...]
    mean_absolute_percent_error: float
    median_absolute_percent_error: float
    max_absolute_percent_error: float


@dataclass(frozen=True)
class FlagScores:
    """Confusion counts for one QC flag.

    ``precision`` is ``None`` when the flag was never predicted and ``recall`` is
    ``None`` when it never occurred in the truth: a difficulty cell containing no
    saturated image would otherwise print 0.0 for a flag the pipeline handled
    perfectly.

    ``f1`` follows the standard convention and is ``None`` if and only if the flag is
    absent from both truth and predictions (``tp == fp == fn == 0``). Whenever the
    flag appears on either side without a true positive it is ``0.0``, computed as
    ``2*tp / (2*tp + fp + fn)`` -- a total failure on a flag is a real 0.0, and
    printing it as "n/a" is how that failure would escape the eval table.
    """

    flag: str
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    precision: float | None
    recall: float | None
    f1: float | None


@dataclass(frozen=True)
class QcScores:
    """QC flag accuracy across a set of items."""

    per_flag: Mapping[str, FlagScores]
    exact_set_match_accuracy: float
    item_count: int


def iou(first: BoundingBox, second: BoundingBox) -> float:
    """Return the intersection-over-union of two boxes, in [0, 1].

    The union is always positive: :meth:`BoundingBox.__post_init__` rejects
    non-positive extents, so ``union >= max(first.area, second.area) > 0``. That
    constructor check is the single guard against a degenerate box; there is no second
    one here.
    """
    overlap_x = max(0, min(first.x + first.width, second.x + second.width) - max(first.x, second.x))
    overlap_y = max(
        0, min(first.y + first.height, second.y + second.height) - max(first.y, second.y)
    )
    intersection = overlap_x * overlap_y
    union = first.area + second.area - intersection
    return intersection / union


def _ratio(numerator: int, denominator: int) -> float:
    """Return ``numerator / denominator``, or 0.0 when the denominator is zero."""
    return numerator / denominator if denominator else 0.0


def _defined_ratio(numerator: int, denominator: int) -> float | None:
    """Return ``numerator / denominator``, or ``None`` when the ratio is undefined."""
    return numerator / denominator if denominator else None


def match_boxes(
    truth: Sequence[BoundingBox],
    predicted: Sequence[BoundingBox],
    iou_threshold: float,
) -> tuple[tuple[tuple[int, int, float], ...], tuple[int, ...], tuple[int, ...]]:
    """Greedily match predictions to truth one-to-one, best IoU first.

    Returns ``(matches, unmatched_truth_indices, unmatched_predicted_indices)`` where
    each match is ``(truth_index, predicted_index, iou)`` with ``iou >= iou_threshold``.
    Ties are broken by index, so the result depends only on the inputs.
    """
    if not 0.0 < iou_threshold <= 1.0:
        raise ValueError(f"iou_threshold must be in (0, 1], got {iou_threshold}")
    candidates = [
        (iou(truth_box, predicted_box), truth_index, predicted_index)
        for truth_index, truth_box in enumerate(truth)
        for predicted_index, predicted_box in enumerate(predicted)
    ]
    candidates = [item for item in candidates if item[0] >= iou_threshold]
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

    used_truth: set[int] = set()
    used_predicted: set[int] = set()
    matches: list[tuple[int, int, float]] = []
    for overlap, truth_index, predicted_index in candidates:
        if truth_index in used_truth or predicted_index in used_predicted:
            continue
        used_truth.add(truth_index)
        used_predicted.add(predicted_index)
        matches.append((truth_index, predicted_index, overlap))
    matches.sort(key=lambda item: item[0])
    unmatched_truth = tuple(i for i in range(len(truth)) if i not in used_truth)
    unmatched_predicted = tuple(i for i in range(len(predicted)) if i not in used_predicted)
    return tuple(matches), unmatched_truth, unmatched_predicted


def detection_scores(
    truth: Sequence[BoundingBox],
    predicted: Sequence[BoundingBox],
    iou_threshold: float,
) -> DetectionScores:
    """Return precision/recall/F1 for band detection at ``iou_threshold``.

    Raises :class:`ValueError` when ``truth`` is empty: recall has no reference there,
    and a 0.0 in an eval table is indistinguishable from a real failure.

    An empty ``predicted`` against non-empty ``truth`` is a genuine outcome, not an
    undefined one -- the pipeline found nothing -- and is reported as
    ``recall = precision = f1 = 0.0``.
    """
    if not truth:
        raise ValueError(
            "detection scores are undefined with no truth boxes: recall has no "
            "reference. Evaluate on an image that has at least one true band"
        )
    matches, unmatched_truth, unmatched_predicted = match_boxes(truth, predicted, iou_threshold)
    true_positives = len(matches)
    false_negatives = len(unmatched_truth)
    false_positives = len(unmatched_predicted)
    precision = _ratio(true_positives, true_positives + false_positives)
    recall = _ratio(true_positives, true_positives + false_negatives)
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    return DetectionScores(
        iou_threshold=iou_threshold,
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        precision=precision,
        recall=recall,
        f1=f1,
        matches=matches,
    )


def micro_average_detection_scores(
    scores: Sequence[DetectionScores],
    iou_threshold: float,
) -> DetectionScores:
    """Pool per-image :class:`DetectionScores` into one score by summing their counts.

    Matching is **not** redone: each element must already have been produced by
    :func:`detection_scores` on one image, because a prediction from one image must never
    be matched to truth from another. Only the confusion counts are summed, and
    precision/recall/F1 are recomputed from the totals -- so this is a micro-average, not
    a mean of per-image F1 scores.

    ``matches`` is empty on the result: match indices are per-image and have no meaning
    once pooled. ``iou_threshold`` is required, and every input must carry that same
    threshold, otherwise the pooled number would mix two different questions.

    Unlike :func:`detection_scores` this does not raise on an empty total: a pooled zero
    here is reached only when every image legitimately reported zero, which
    :func:`detection_scores` has already refused to produce for empty truth. An empty
    ``scores`` sequence does raise, since there is nothing to average.
    """
    if not scores:
        raise ValueError("cannot micro-average an empty sequence of detection scores")
    mismatched = {score.iou_threshold for score in scores} - {iou_threshold}
    if mismatched:
        raise ValueError(
            f"every score must have been computed at iou_threshold={iou_threshold}, "
            f"found {sorted(mismatched)}"
        )
    true_positives = sum(score.true_positives for score in scores)
    false_positives = sum(score.false_positives for score in scores)
    false_negatives = sum(score.false_negatives for score in scores)
    precision = _ratio(true_positives, true_positives + false_positives)
    recall = _ratio(true_positives, true_positives + false_negatives)
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    return DetectionScores(
        iou_threshold=iou_threshold,
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        precision=precision,
        recall=recall,
        f1=f1,
        matches=(),
    )


def _relative_error_scores(
    truth_values: Sequence[float],
    predicted_values: Sequence[float],
    quantity: str,
) -> ErrorScores:
    """Return signed relative errors in percent, plus their absolute-value summary."""
    if len(truth_values) != len(predicted_values):
        raise ValueError(
            f"{quantity}: got {len(truth_values)} true values and "
            f"{len(predicted_values)} predicted values; they must correspond one-to-one"
        )
    if not truth_values:
        raise ValueError(f"{quantity}: cannot summarise an empty set of values")
    errors: list[float] = []
    paired = zip(truth_values, predicted_values, strict=True)
    for index, (true_value, predicted_value) in enumerate(paired):
        if true_value <= 0.0:
            raise ValueError(
                f"{quantity}: true value at index {index} is {true_value}; relative error "
                f"requires a strictly positive reference"
            )
        errors.append((predicted_value - true_value) / true_value * 100.0)
    absolute = sorted(abs(error) for error in errors)
    middle = len(absolute) // 2
    median = (
        absolute[middle]
        if len(absolute) % 2
        else 0.5 * (absolute[middle - 1] + absolute[middle])
    )
    return ErrorScores(
        count=len(errors),
        per_item_percent_error=tuple(errors),
        mean_absolute_percent_error=sum(absolute) / len(absolute),
        median_absolute_percent_error=median,
        max_absolute_percent_error=absolute[-1],
    )


def intensity_recovery_error(
    true_intensities: Sequence[float],
    predicted_intensities: Sequence[float],
) -> ErrorScores:
    """Return the relative intensity recovery error, in percent, per matched band.

    Inputs must already be aligned (e.g. by :func:`match_boxes`) and must be
    background-corrected integrated intensities in the same units.
    """
    return _relative_error_scores(
        true_intensities, predicted_intensities, "intensity recovery"
    )


def normalization_ratio_error(
    true_ratios: Mapping[str, float],
    predicted_ratios: Mapping[str, float],
) -> ErrorScores:
    """Return the relative error of normalized ratios, in percent, keyed by lane.

    Raises if the key sets differ: a missing lane is a pipeline failure to report,
    not a row to drop silently.
    """
    missing = set(true_ratios) - set(predicted_ratios)
    extra = set(predicted_ratios) - set(true_ratios)
    if missing or extra:
        raise ValueError(
            f"normalization ratios do not correspond: missing {sorted(missing)}, "
            f"unexpected {sorted(extra)}"
        )
    keys = sorted(true_ratios)
    return _relative_error_scores(
        [true_ratios[key] for key in keys],
        [predicted_ratios[key] for key in keys],
        "normalization ratio",
    )


def qc_flag_accuracy(
    true_flags: Mapping[str, Sequence[str]],
    predicted_flags: Mapping[str, Sequence[str]],
    evaluated_flags: Sequence[str],
) -> QcScores:
    """Return per-flag precision/recall/F1 and the exact-set-match rate.

    ``true_flags`` and ``predicted_flags`` map an item id (image or band) to its flag
    list; both must cover exactly the same ids. ``evaluated_flags`` is the explicit
    vocabulary being scored -- flags outside it in either input raise, so a typo in a
    flag name can never quietly score as a true negative.

    Per-flag precision and recall are ``None`` where they are undefined, and F1 is
    ``None`` only for a flag absent from both truth and predictions (see
    :class:`FlagScores`); a runner must render ``None`` as "n/a", never as 0.0, and
    must render a 0.0 F1 as the failure it is.
    """
    if not evaluated_flags:
        raise ValueError("evaluated_flags must name at least one QC flag to score")
    vocabulary = set(evaluated_flags)
    if len(vocabulary) != len(evaluated_flags):
        raise ValueError(f"evaluated_flags contains duplicates: {list(evaluated_flags)}")
    missing = set(true_flags) - set(predicted_flags)
    extra = set(predicted_flags) - set(true_flags)
    if missing or extra:
        raise ValueError(
            f"QC flag sets do not correspond: missing items {sorted(missing)}, "
            f"unexpected items {sorted(extra)}"
        )
    if not true_flags:
        raise ValueError("QC flag accuracy is undefined with no items to score")

    for source, mapping in (("truth", true_flags), ("prediction", predicted_flags)):
        for item_id, flags in mapping.items():
            unknown = set(flags) - vocabulary
            if unknown:
                raise ValueError(
                    f"{source} for item {item_id!r} carries flags {sorted(unknown)} that are "
                    f"not in the evaluated vocabulary {sorted(vocabulary)}"
                )

    item_ids = sorted(true_flags)
    per_flag: dict[str, FlagScores] = {}
    for flag in evaluated_flags:
        counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
        for item_id in item_ids:
            in_truth = flag in set(true_flags[item_id])
            in_prediction = flag in set(predicted_flags[item_id])
            if in_truth and in_prediction:
                counts["tp"] += 1
            elif in_prediction:
                counts["fp"] += 1
            elif in_truth:
                counts["fn"] += 1
            else:
                counts["tn"] += 1
        precision = _defined_ratio(counts["tp"], counts["tp"] + counts["fp"])
        recall = _defined_ratio(counts["tp"], counts["tp"] + counts["fn"])
        f1_denominator = 2 * counts["tp"] + counts["fp"] + counts["fn"]
        f1: float | None = 2 * counts["tp"] / f1_denominator if f1_denominator else None
        per_flag[flag] = FlagScores(
            flag=flag,
            true_positives=counts["tp"],
            false_positives=counts["fp"],
            false_negatives=counts["fn"],
            true_negatives=counts["tn"],
            precision=precision,
            recall=recall,
            f1=f1,
        )

    exact = sum(
        1
        for item_id in item_ids
        if set(true_flags[item_id]) & vocabulary == set(predicted_flags[item_id]) & vocabulary
    )
    return QcScores(
        per_flag=per_flag,
        exact_set_match_accuracy=exact / len(item_ids),
        item_count=len(item_ids),
    )
