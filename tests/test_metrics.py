"""Unit tests for the evaluation metrics, on hand-computed toy fixtures.

Every expected value below is worked out by hand in the comment next to it; nothing
is compared against the implementation's own output.
"""

from __future__ import annotations

import pytest

from evals.metrics import (
    PLAN_IOU_THRESHOLD,
    BoundingBox,
    detection_scores,
    flag_coincidence,
    intensity_recovery_error,
    iou,
    match_boxes,
    micro_average_detection_scores,
    normalization_ratio_error,
    qc_flag_accuracy,
)

BOX_A = BoundingBox(x=0, y=0, width=10, height=10)
BOX_HALF_SHIFTED = BoundingBox(x=5, y=0, width=10, height=10)
BOX_TOP_HALF = BoundingBox(x=0, y=0, width=10, height=5)
BOX_FAR = BoundingBox(x=100, y=100, width=10, height=10)


def test_iou_identical_boxes_is_one() -> None:
    assert iou(BOX_A, BOX_A) == 1.0


def test_iou_half_overlap_is_one_third() -> None:
    # intersection 5*10 = 50; union 100 + 100 - 50 = 150; 50/150 = 1/3.
    assert iou(BOX_A, BOX_HALF_SHIFTED) == pytest.approx(1.0 / 3.0)


def test_iou_disjoint_boxes_is_zero() -> None:
    assert iou(BOX_A, BOX_FAR) == 0.0


def test_iou_edge_touching_boxes_is_zero() -> None:
    assert iou(BOX_A, BoundingBox(x=10, y=0, width=10, height=10)) == 0.0


def test_iou_is_symmetric() -> None:
    assert iou(BOX_A, BOX_HALF_SHIFTED) == iou(BOX_HALF_SHIFTED, BOX_A)


def test_bounding_box_rejects_degenerate_extent() -> None:
    with pytest.raises(ValueError, match="positive extent"):
        BoundingBox(x=0, y=0, width=0, height=5)


def test_bounding_box_from_mapping_requires_all_keys() -> None:
    assert BoundingBox.from_mapping({"x": 1, "y": 2, "width": 3, "height": 4}) == BoundingBox(
        x=1, y=2, width=3, height=4
    )
    with pytest.raises(KeyError, match="height"):
        BoundingBox.from_mapping({"x": 1, "y": 2, "width": 3})


def test_match_at_exactly_the_threshold_counts_as_a_match() -> None:
    # intersection 50; union 100 + 50 - 50 = 100; IoU = 0.5 exactly, and the plan
    # specifies IoU >= 0.5.
    assert iou(BOX_A, BOX_TOP_HALF) == 0.5
    scores = detection_scores([BOX_A], [BOX_TOP_HALF], PLAN_IOU_THRESHOLD)
    assert scores.true_positives == 1
    assert scores.false_positives == 0
    assert scores.false_negatives == 0


def test_match_just_below_the_threshold_is_not_a_match() -> None:
    # intersection 4*10 = 40; union 100 + 40 - 40 = 100; IoU = 0.4 < 0.5.
    slightly_small = BoundingBox(x=0, y=0, width=10, height=4)
    assert iou(BOX_A, slightly_small) == pytest.approx(0.4)
    scores = detection_scores([BOX_A], [slightly_small], PLAN_IOU_THRESHOLD)
    assert (scores.true_positives, scores.false_positives, scores.false_negatives) == (0, 1, 1)
    assert scores.precision == 0.0
    assert scores.recall == 0.0
    assert scores.f1 == 0.0


def test_matching_is_one_to_one_so_duplicates_become_false_positives() -> None:
    duplicate = BoundingBox(x=1, y=0, width=10, height=10)  # IoU with BOX_A = 9/11 > 0.5
    scores = detection_scores([BOX_A], [BOX_A, duplicate], PLAN_IOU_THRESHOLD)
    assert (scores.true_positives, scores.false_positives, scores.false_negatives) == (1, 1, 0)
    assert scores.precision == 0.5
    assert scores.recall == 1.0
    assert scores.f1 == pytest.approx(2.0 / 3.0)  # 2*0.5*1.0/1.5


def test_missed_band_lowers_recall_only() -> None:
    scores = detection_scores([BOX_A, BOX_FAR], [BOX_A], PLAN_IOU_THRESHOLD)
    assert (scores.true_positives, scores.false_positives, scores.false_negatives) == (1, 0, 1)
    assert scores.precision == 1.0
    assert scores.recall == 0.5
    assert scores.f1 == pytest.approx(2.0 / 3.0)


def test_greedy_matching_prefers_the_better_overlap() -> None:
    good = BoundingBox(x=0, y=0, width=10, height=10)  # IoU 1.0 with BOX_A
    worse = BoundingBox(x=3, y=0, width=10, height=10)  # IoU 7/13 with BOX_A
    matches, unmatched_truth, unmatched_predicted = match_boxes(
        [BOX_A], [worse, good], PLAN_IOU_THRESHOLD
    )
    assert matches == ((0, 1, 1.0),)
    assert unmatched_truth == ()
    assert unmatched_predicted == (0,)


def test_detection_scores_reject_an_evaluation_without_truth() -> None:
    with pytest.raises(ValueError, match="undefined"):
        detection_scores([], [], PLAN_IOU_THRESHOLD)
    with pytest.raises(ValueError, match="no truth boxes"):
        detection_scores([], [BOX_A], PLAN_IOU_THRESHOLD)


def test_finding_nothing_is_a_real_outcome_not_an_undefined_one() -> None:
    scores = detection_scores([BOX_A, BOX_FAR], [], PLAN_IOU_THRESHOLD)
    assert (scores.true_positives, scores.false_positives, scores.false_negatives) == (0, 0, 2)
    assert scores.precision == 0.0
    assert scores.recall == 0.0
    assert scores.f1 == 0.0


@pytest.mark.parametrize("threshold", [0.0, -0.1, 1.5])
def test_match_boxes_rejects_out_of_range_thresholds(threshold: float) -> None:
    with pytest.raises(ValueError, match="iou_threshold"):
        match_boxes([BOX_A], [BOX_A], threshold)


def test_intensity_recovery_error_is_signed_per_band_and_absolute_in_summary() -> None:
    scores = intensity_recovery_error([100.0, 200.0, 400.0], [110.0, 200.0, 300.0])
    # +10%, 0%, -25%; absolute values sorted: 0, 10, 25.
    assert scores.per_item_percent_error == pytest.approx((10.0, 0.0, -25.0))
    assert scores.mean_absolute_percent_error == pytest.approx(35.0 / 3.0)
    assert scores.median_absolute_percent_error == pytest.approx(10.0)
    assert scores.max_absolute_percent_error == pytest.approx(25.0)
    assert scores.count == 3


def test_intensity_recovery_error_median_of_even_count() -> None:
    scores = intensity_recovery_error([100.0, 100.0], [120.0, 90.0])
    # absolute errors 20 and 10 -> median 15.
    assert scores.median_absolute_percent_error == pytest.approx(15.0)


def test_intensity_recovery_error_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="one-to-one"):
        intensity_recovery_error([1.0, 2.0], [1.0])


def test_intensity_recovery_error_rejects_empty_input() -> None:
    with pytest.raises(ValueError, match="empty"):
        intensity_recovery_error([], [])


@pytest.mark.parametrize("bad_true", [0.0, -5.0])
def test_intensity_recovery_error_rejects_non_positive_reference(bad_true: float) -> None:
    with pytest.raises(ValueError, match="strictly positive"):
        intensity_recovery_error([100.0, bad_true], [100.0, 1.0])


def test_normalization_ratio_error_matches_by_lane_key() -> None:
    scores = normalization_ratio_error(
        {"L0": 2.0, "L1": 4.0},
        {"L0": 2.2, "L1": 3.0},
    )
    # L0: +10%; L1: -25%.
    assert scores.per_item_percent_error == pytest.approx((10.0, -25.0))
    assert scores.mean_absolute_percent_error == pytest.approx(17.5)


def test_normalization_ratio_error_rejects_missing_lane() -> None:
    with pytest.raises(ValueError, match="missing \\['L1'\\]"):
        normalization_ratio_error({"L0": 1.0, "L1": 1.0}, {"L0": 1.0})


def test_normalization_ratio_error_rejects_unexpected_lane() -> None:
    with pytest.raises(ValueError, match="unexpected \\['L9'\\]"):
        normalization_ratio_error({"L0": 1.0}, {"L0": 1.0, "L9": 1.0})


def test_qc_flag_accuracy_confusion_counts() -> None:
    truth = {
        "tp_item": ["saturated"],
        "fp_item": [],
        "fn_item": ["saturated"],
        "tn_item": [],
    }
    predicted = {
        "tp_item": ["saturated"],
        "fp_item": ["saturated"],
        "fn_item": [],
        "tn_item": [],
    }
    scores = qc_flag_accuracy(truth, predicted, ["saturated"])
    flag = scores.per_flag["saturated"]
    counts = (flag.true_positives, flag.false_positives, flag.false_negatives, flag.true_negatives)
    assert counts == (1, 1, 1, 1)
    assert flag.precision == 0.5
    assert flag.recall == 0.5
    assert flag.f1 == 0.5
    assert scores.exact_set_match_accuracy == 0.5  # tp_item and tn_item agree exactly
    assert scores.item_count == 4


def test_a_flag_absent_from_truth_and_prediction_scores_as_undefined_not_zero() -> None:
    """A difficulty cell with no saturated image must not print 0.0 for `saturated`."""
    truth = {"a": ["saturated"], "b": []}
    scores = qc_flag_accuracy(truth, dict(truth), ["saturated", "low_dynamic_range"])
    absent = scores.per_flag["low_dynamic_range"]
    counts = (
        absent.true_positives,
        absent.false_positives,
        absent.false_negatives,
        absent.true_negatives,
    )
    assert counts == (0, 0, 0, 2)
    assert absent.precision is None
    assert absent.recall is None
    assert absent.f1 is None
    assert scores.per_flag["saturated"].f1 == 1.0
    assert scores.exact_set_match_accuracy == 1.0


def test_a_flag_never_predicted_has_zero_f1_and_undefined_precision() -> None:
    """Total failure on a flag is F1 = 0.0, not "n/a" -- 0.0 must reach the eval table."""
    scores = qc_flag_accuracy(
        {"a": ["saturated"], "b": ["saturated"]}, {"a": [], "b": []}, ["saturated"]
    )
    flag = scores.per_flag["saturated"]
    counts = (flag.true_positives, flag.false_positives, flag.false_negatives, flag.true_negatives)
    assert counts == (0, 0, 2, 0)
    assert flag.precision is None
    assert flag.recall == 0.0
    assert flag.f1 == 0.0


def test_a_flag_only_ever_predicted_has_zero_f1_and_undefined_recall() -> None:
    """Mirror case: the flag exists only in the predictions, so F1 = 0.0."""
    scores = qc_flag_accuracy(
        {"a": [], "b": []}, {"a": ["saturated"], "b": ["saturated"]}, ["saturated"]
    )
    flag = scores.per_flag["saturated"]
    counts = (flag.true_positives, flag.false_positives, flag.false_negatives, flag.true_negatives)
    assert counts == (0, 2, 0, 0)
    assert flag.precision == 0.0
    assert flag.recall is None
    assert flag.f1 == 0.0


def test_a_flag_missed_and_misfired_with_no_true_positive_has_zero_f1() -> None:
    """tp = 0 with both error kinds present: precision and recall are 0.0, so is F1."""
    scores = qc_flag_accuracy(
        {"a": ["saturated"], "b": []}, {"a": [], "b": ["saturated"]}, ["saturated"]
    )
    flag = scores.per_flag["saturated"]
    counts = (flag.true_positives, flag.false_positives, flag.false_negatives, flag.true_negatives)
    assert counts == (0, 1, 1, 0)
    assert flag.precision == 0.0
    assert flag.recall == 0.0
    assert flag.f1 == 0.0


def test_qc_flag_accuracy_scores_each_flag_independently() -> None:
    truth = {"a": ["saturated", "overlapping"], "b": ["overlapping"]}
    predicted = {"a": ["saturated"], "b": ["overlapping"]}
    scores = qc_flag_accuracy(truth, predicted, ["saturated", "overlapping"])
    assert scores.per_flag["saturated"].recall == 1.0
    assert scores.per_flag["overlapping"].recall == 0.5
    assert scores.exact_set_match_accuracy == 0.5


def test_qc_flag_accuracy_rejects_flags_outside_the_vocabulary() -> None:
    with pytest.raises(ValueError, match="not in the evaluated vocabulary"):
        qc_flag_accuracy({"a": ["saturated"]}, {"a": ["typoed_flag"]}, ["saturated"])


def test_qc_flag_accuracy_rejects_item_set_mismatch() -> None:
    with pytest.raises(ValueError, match="do not correspond"):
        qc_flag_accuracy({"a": [], "b": []}, {"a": []}, ["saturated"])


def test_qc_flag_accuracy_rejects_empty_inputs() -> None:
    with pytest.raises(ValueError, match="at least one QC flag"):
        qc_flag_accuracy({"a": []}, {"a": []}, [])
    with pytest.raises(ValueError, match="no items"):
        qc_flag_accuracy({}, {}, ["saturated"])


def test_qc_flag_accuracy_rejects_duplicate_vocabulary() -> None:
    with pytest.raises(ValueError, match="duplicates"):
        qc_flag_accuracy({"a": []}, {"a": []}, ["saturated", "saturated"])

TRUTH_FLAGS = ("saturated", "overlapping")
PREDICTED_FLAGS = ("saturated", "overlapping", "unresolved_shoulder")
"""The two band flag vocabularies :func:`flag_coincidence` compares across: ground truth's,
and the pipeline's, which has one flag truth has no label for."""


def test_flag_coincidence_reports_a_rate_on_each_side_of_the_reference_label() -> None:
    """The shoulder diagnostic's arithmetic, on a case whose counts are read off by hand.

    Deliberately not precision/recall: the predicted flag and the reference label are
    different questions, so there is no "true positive" to speak of.
    """
    predicted = {
        "a": ["unresolved_shoulder"],
        "b": ["unresolved_shoulder"],
        "c": [],
        "d": ["unresolved_shoulder"],
        "e": [],
    }
    truth = {"a": ["overlapping"], "b": ["overlapping"], "c": ["overlapping"], "d": [], "e": []}

    scores = flag_coincidence(
        truth, predicted, "overlapping", "unresolved_shoulder", TRUTH_FLAGS, PREDICTED_FLAGS
    )

    assert (scores.items, scores.reference_items, scores.non_reference_items) == (5, 3, 2)
    assert scores.fired_with_reference == 2
    assert scores.fired_without_reference == 1
    assert scores.rate_with_reference == pytest.approx(2 / 3)
    assert scores.rate_without_reference == pytest.approx(1 / 2)


def test_flag_coincidence_leaves_an_empty_side_undefined() -> None:
    """No item carries the reference label, so a rate on that side has no denominator."""
    scores = flag_coincidence(
        {"a": []},
        {"a": ["unresolved_shoulder"]},
        "overlapping",
        "unresolved_shoulder",
        TRUTH_FLAGS,
        PREDICTED_FLAGS,
    )

    assert scores.reference_items == 0
    assert scores.rate_with_reference is None
    assert scores.rate_without_reference == pytest.approx(1.0)


def test_flag_coincidence_requires_the_same_items_on_both_sides() -> None:
    """A comparison over two different item sets is not a comparison."""
    with pytest.raises(ValueError, match="do not correspond"):
        flag_coincidence(
            {"a": []}, {"b": []}, "overlapping", "unresolved_shoulder", TRUTH_FLAGS,
            PREDICTED_FLAGS,
        )


def test_flag_coincidence_refuses_an_empty_item_set() -> None:
    """Both rates would be undefined, which is not a score to print."""
    with pytest.raises(ValueError, match="no items"):
        flag_coincidence(
            {}, {}, "overlapping", "unresolved_shoulder", TRUTH_FLAGS, PREDICTED_FLAGS
        )


def test_flag_coincidence_rejects_a_flag_name_outside_its_own_vocabulary() -> None:
    """A typo would otherwise report a rate of 0.0, which reads like a real measurement."""
    truth = {"a": ["overlapping"]}
    predicted = {"a": ["unresolved_shoulder"]}

    with pytest.raises(ValueError, match="not in the predicted vocabulary"):
        flag_coincidence(
            truth, predicted, "overlapping", "unresolved_sholder", TRUTH_FLAGS, PREDICTED_FLAGS
        )
    with pytest.raises(ValueError, match="not in the reference vocabulary"):
        flag_coincidence(
            truth, predicted, "overlaping", "unresolved_shoulder", TRUTH_FLAGS, PREDICTED_FLAGS
        )


def test_flag_coincidence_rejects_flags_outside_the_declared_vocabularies() -> None:
    """The two sides have different vocabularies, and each is checked against its own.

    In particular the pipeline's ``unresolved_shoulder`` is legal on the predicted side and
    illegal on the reference side, which is the whole asymmetry this function exists for.
    """
    with pytest.raises(ValueError, match="reference flags for item"):
        flag_coincidence(
            {"a": ["unresolved_shoulder"]},
            {"a": []},
            "overlapping",
            "unresolved_shoulder",
            TRUTH_FLAGS,
            PREDICTED_FLAGS,
        )
    with pytest.raises(ValueError, match="predicted flags for item"):
        flag_coincidence(
            {"a": []},
            {"a": ["probably_fine"]},
            "overlapping",
            "unresolved_shoulder",
            TRUTH_FLAGS,
            PREDICTED_FLAGS,
        )


def test_micro_average_pools_counts_and_recomputes_from_totals() -> None:
    """The pooled score comes from summed confusion counts, not from averaged F1s.

    Four boxes found on a busy image and one missed on a sparse one: pooling the counts
    gives tp=4, fp=0, fn=1 -> P=1.0, R=0.8, F1=0.889, whereas averaging the two per-image
    F1 scores (1.0 and 0.0) would give 0.5. The two differ, which is the point.
    """
    busy = [BoundingBox(20 * index, 0, 10, 10) for index in range(4)]
    found_all = detection_scores(busy, busy, PLAN_IOU_THRESHOLD)
    found_none = detection_scores([BoundingBox(0, 0, 10, 10)], [], PLAN_IOU_THRESHOLD)

    pooled = micro_average_detection_scores([found_all, found_none], PLAN_IOU_THRESHOLD)

    assert (pooled.true_positives, pooled.false_positives, pooled.false_negatives) == (4, 0, 1)
    assert pooled.precision == pytest.approx(1.0)
    assert pooled.recall == pytest.approx(0.8)
    assert pooled.f1 == pytest.approx(2 * 1.0 * 0.8 / 1.8)
    assert pooled.matches == ()
    assert pooled.f1 != pytest.approx(0.5 * (found_all.f1 + found_none.f1))


def test_micro_average_requires_a_single_threshold() -> None:
    """Pooling scores taken at different thresholds would mix two questions."""
    truth = [BoundingBox(0, 0, 10, 10)]
    predicted = [BoundingBox(0, 0, 10, 10)]
    strict = detection_scores(truth, predicted, 0.9)
    loose = detection_scores(truth, predicted, 0.5)

    with pytest.raises(ValueError, match="iou_threshold"):
        micro_average_detection_scores([strict, loose], 0.5)


def test_micro_average_of_nothing_raises() -> None:
    """There is no pooled score over zero images."""
    with pytest.raises(ValueError, match="empty sequence"):
        micro_average_detection_scores([], PLAN_IOU_THRESHOLD)
