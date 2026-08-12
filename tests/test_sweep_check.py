"""The comparison policy behind ``python -m evals.sweep --check``.

``--check`` re-measures the sweep record and compares it to the committed one. Part of
that comparison cannot be exact: the same code on the same dependency versions produces
slightly different figures on x86-64 and on arm64, because a last-bit difference in a
profile mean flips a plateau tie in peak finding, which moves an integer ROI by a whole
pixel, which crosses the IoU 0.5 matching boundary and changes the matched set. NOTES.md's
"Why ``--check`` compares some figures within a tolerance" records the measured chain.

So the policy is: structure, the header, the shipped config digests, the per-sweep prose
and ``dev_sweeps.md`` exactly; measured figures within a per-class tolerance, resolved by
``(sweep, value label, field)`` so that one field name may be a truth-derived quantity in
one row and a measured one in another. This module pins the policy from both sides -- what
it tolerates and what it still catches -- by driving :func:`evals.sweep._differences`, the
function ``--check`` itself calls, over synthetic records. Nothing here re-implements the
comparison.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from evals import sweep
from evals.sweep import (
    CONTEXT_TOLERANCES,
    DETECTION_COUNT,
    DETECTION_RATE,
    FIGURE_TOLERANCES,
    JSON_PATH,
    LABEL_FIELD,
    MARKDOWN_PATH,
    MONTE_CARLO,
    DuplicateValueLabelError,
    FigureTolerance,
    MalformedRecordError,
    UnclassifiedFigureError,
    _differences,
    _index_tolerances,
    _markdown,
    tolerance_for,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

SWEEP_NAME = "background.local_median.window_px"

NUMBER = re.compile(r"(?<![A-Za-z0-9._])-?\d+(?:\.\d+)?(?![A-Za-z0-9]*[A-Za-z])")
"""The same standalone-number rule ``tests/test_recorded_figures.py`` uses."""

PROSE_CONSTANTS: dict[tuple[str, str], str] = {
    ("band_roi_sizes", "51"): "the shipped window_px in its parameter line; digest-guarded",
    ("band_roi_sizes", "0.5"): "the 0.5/(1-f) background quantile, a closed form",
    ("band_roi_sizes", "1"): "the 1 of that same closed form",
    ("presmooth_variance", "500"): "NOISE_REALISATIONS",
    ("aperture_selector_uncertainty", "2000"): "BOOTSTRAP_RESAMPLES",
    ("aperture_selector_uncertainty", "20260811"): "BOOTSTRAP_SEED",
    ("aperture_selector_uncertainty", "0"): "'excludes 0', a literal rather than a measurement",
}
"""Every number a note or a parameter string may hold that is not one of its own value
labels, listed per sweep so that a constant of one note is not licensed in another. Each is
a module constant or a config parameter -- never a measurement.

The exemption for a sweep's own value labels is unconditional, and that is a real limit:
a note that quoted an argmax ("best at 51") would read as a label and pass. No note does,
and nothing cheap distinguishes a label named as a parameter value from the same label named
as a result, so the guard is stated here rather than overclaimed."""


@pytest.fixture(scope="module")
def record() -> dict[str, Any]:
    """Return the committed sweep record."""
    return json.loads((REPO_ROOT / JSON_PATH).read_text(encoding="utf-8"))


BASE_VALUE: dict[str, Any] = {
    "label": "51",
    "band_false_positives": 40,
    "band_f1": 0.8506,
    "mean_absolute_percent": 18.31,
    "overlapping_signed_mean_percent": 62.68,
    "overlapping_share_percent": 18.64,
    "difference_from_shipped_percent": 0.15,
    "median_height_px": 18,
    "max_absolute_percent": 154.73,
    "max_window_coverage_percent": 180.85,
    "whole_image_variance_reduction": 75.04,
    "median_lane_pitch_px": 45,
}
"""One synthetic sweep value carrying exactly one figure from each of the eleven tolerance
classes, so a perturbation of any single class can be tested without disturbing the rest."""

WITHIN_TOLERANCE: tuple[tuple[str, Any, Any], ...] = (
    # field, committed, measured -- each moved further than CI moved it, but inside its
    # class. The comment gives the movement and what it has to cover.
    ("band_false_positives", 40, 36),  # 4 counts; CI moved 3
    ("band_f1", 0.8506, 0.8686),  # 0.018; CI moved 0.0092
    ("mean_absolute_percent", 18.31, 19.2),  # 0.89 pp; CI moved 0.56
    ("overlapping_signed_mean_percent", 62.68, 66.0),  # 3.32 pp; two bands of a ~50 subset
    ("overlapping_share_percent", 18.64, 20.4),  # 1.76 pp; four bands of the matched set
    ("difference_from_shipped_percent", 0.15, 0.4),  # 0.25 pp of paired bootstrap
    ("median_height_px", 18, 20),  # 2 px, the whole-pixel ROI shift with headroom
    ("max_absolute_percent", 154.73, 168.0),  # 8.6% relative; CI moved 4.3%
    ("max_window_coverage_percent", 180.85, 220.0),  # 21.6%, inside its factors' product
    ("whole_image_variance_reduction", 75.04, 75.045),  # 6.7e-5 relative
    ("median_lane_pitch_px", 45, 45),  # a truth-derived figure cannot move at all
)

BEYOND_TOLERANCE: tuple[tuple[str, Any, Any], ...] = (
    ("band_false_positives", 40, 34),  # 6 counts
    ("band_f1", 0.8506, 0.8806),  # 0.03, about the extent_min_sigma 2.0 -> 0.0 effect
    ("mean_absolute_percent", 18.31, 19.8),  # 1.49 pp
    ("overlapping_signed_mean_percent", 62.68, 67.5),  # 4.82 pp
    ("overlapping_share_percent", 18.64, 21.2),  # 2.56 pp
    ("difference_from_shipped_percent", 0.15, 0.7),  # 0.55 pp
    ("median_height_px", 18, 22),  # 4 px
    ("max_absolute_percent", 154.73, 180.0),  # 16% relative
    ("max_window_coverage_percent", 180.85, 240.0),  # 32.7% relative
    ("whole_image_variance_reduction", 75.04, 75.1),  # 8e-4 relative
    ("median_lane_pitch_px", 45, 46),  # a truth-derived figure: any movement at all
)

CI_DRIFT: dict[str, tuple[tuple[str, Any, Any], ...]] = {
    "61": (
        ("mean_absolute_percent", 17.72, 17.71),
        ("clean_mean_absolute_percent", 5.74, 5.73),
    ),
    "81": (
        ("band_f1", 0.7938, 0.7994),
        ("band_precision", 0.8658, 0.875),
        ("band_recall", 0.733, 0.7358),
        ("band_true_positives", 258, 259),
        ("band_false_positives", 40, 37),
        ("band_false_negatives", 94, 93),
        ("matched", 258, 259),
        ("mean_absolute_percent", 18.31, 18.87),
        ("median_absolute_percent", 4.19, 4.47),
        ("max_absolute_percent", 154.73, 161.38),
        ("clean_signed_percent", -4.12, -4.13),
        ("clean_mean_absolute_percent", 4.9, 4.91),
        ("clean_median_absolute_percent", 2.73, 2.76),
    ),
}
"""The exact drift the x86-64 CI runner reported against this arm64-generated record: the
failure the policy exists for, pinned so a future tightening cannot reintroduce it. The
committed side of every pair is asserted against the record itself below, so regenerating
the record cannot leave this a test of a fiction."""


def _record(values: Sequence[dict[str, Any]] = (BASE_VALUE,), **header: Any) -> dict[str, Any]:
    """Return a record in the shape :func:`evals.sweep.run_sweeps` produces."""
    return {
        "split": "dev",
        "iou_threshold": 0.5,
        "images": 30,
        "truth_lanes": 150,
        "truth_bands": 352,
        "shipped": {"default": "digest-default", "rolling_ball": "digest-rolling-ball"},
        "sweeps": {
            SWEEP_NAME: {
                "parameter": "window_px",
                "note": "A synthetic sweep standing in for the committed record's shape.",
                "values": [dict(value) for value in values],
            }
        },
        **header,
    }


def _moved(**figures: Any) -> dict[str, Any]:
    """Return a record whose single value has ``figures`` replaced."""
    return _record(({**BASE_VALUE, **figures},))


def _drift_record(index: int) -> dict[str, Any]:
    """Return the record :data:`CI_DRIFT` describes, committed (0) or measured (1)."""
    return _record(
        tuple(
            {LABEL_FIELD: label, **{field: pair[index] for field, *pair in figures}}
            for label, figures in CI_DRIFT.items()
        )
    )


def _roi_size_record(**detected: Any) -> dict[str, Any]:
    """Return a record shaped like ``band_roi_sizes``: one truth row and one detected row."""
    truth = {LABEL_FIELD: "truth", "bands": 352, "median_height_px": 12}
    return {
        **_record(),
        "sweeps": {
            "band_roi_sizes": {
                "parameter": "band ROI extent",
                "note": "Two rows: one read from ground truth, one measured.",
                "values": [
                    truth,
                    {LABEL_FIELD: "detected", "bands": 304, "median_height_px": 18, **detected},
                ],
            }
        },
    }


Comparison = Callable[[dict[str, Any], dict[str, Any]], list[str]]


@pytest.fixture
def compare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Comparison:
    """Return the real ``--check`` comparison, with the Markdown surface pointed at a tmpdir.

    The Markdown file is written from the *committed* record, which is what a repository in
    order looks like; the tests that exercise the Markdown check write it themselves.
    """

    def run(committed: dict[str, Any], measured: dict[str, Any]) -> list[str]:
        path = tmp_path / MARKDOWN_PATH.name
        path.write_text(_markdown(committed), encoding="utf-8")
        monkeypatch.setattr(sweep, "MARKDOWN_PATH", path)
        return list(_differences(measured, committed))

    return run


def test_an_unchanged_record_reproduces(compare: Comparison) -> None:
    """The baseline: identical records differ nowhere."""
    assert compare(_record(), _record()) == []


def test_the_drift_observed_in_ci_is_tolerated(compare: Comparison) -> None:
    """Every figure the x86-64 runner moved is inside its class. This is the reported bug."""
    assert compare(_drift_record(0), _drift_record(1)) == []


def test_the_pinned_ci_drift_is_drift_from_the_committed_record(record: dict[str, Any]) -> None:
    """The committed side of each pinned pair is the record's own figure, still."""
    values = {
        str(value[LABEL_FIELD]): value for value in record["sweeps"][SWEEP_NAME]["values"]
    }

    stale = [
        f"{label}.{field}: pinned {committed!r}, record holds {values[label][field]!r}"
        for label, figures in CI_DRIFT.items()
        for field, committed, _ in figures
        if values[label][field] != committed
    ]

    assert stale == [], "re-read the CI log against the regenerated record"


@pytest.mark.parametrize(("field", "committed", "measured"), WITHIN_TOLERANCE, ids=lambda v: str(v))
def test_a_within_tolerance_move_is_tolerated(
    compare: Comparison, field: str, committed: Any, measured: Any
) -> None:
    """One figure of each class, moved further than CI moved it but inside the class."""
    assert compare(_moved(**{field: committed}), _moved(**{field: measured})) == []


@pytest.mark.parametrize(("field", "committed", "measured"), BEYOND_TOLERANCE, ids=lambda v: str(v))
def test_a_beyond_tolerance_move_fails_and_names_the_field(
    compare: Comparison, field: str, committed: Any, measured: Any
) -> None:
    """One figure of each class, moved past the class, must be reported by name."""
    problems = compare(_moved(**{field: committed}), _moved(**{field: measured}))

    assert len(problems) == 1
    assert problems[0].startswith(f"{SWEEP_NAME}=51.{field}: ")
    assert repr(committed) in problems[0] and repr(measured) in problems[0]


def test_a_field_takes_its_class_from_the_row_it_is_in(compare: Comparison) -> None:
    """``band_roi_sizes`` holds the same figures twice: truth ROIs and detected ROIs.

    The truth row is read out of the committed gold set, so it is compared exactly; the
    detected row moves with the detection. One band, two classes, resolved by context.
    """
    committed = _roi_size_record()
    detected_moved = _roi_size_record(bands=305)
    truth_moved = _roi_size_record()
    truth_moved["sweeps"]["band_roi_sizes"]["values"][0]["bands"] = 353

    assert compare(committed, detected_moved) == []
    problems = compare(committed, truth_moved)
    assert len(problems) == 1
    assert problems[0].startswith("band_roi_sizes=truth.bands: ")


def test_a_context_override_may_only_tighten(record: dict[str, Any]) -> None:
    """The override table cannot become a place where an inconvenient figure is let out."""
    loosened = []
    for (name, label), overrides in CONTEXT_TOLERANCES.items():
        value = next(
            item
            for item in record["sweeps"][name]["values"]
            if str(item[LABEL_FIELD]) == label
        )
        for field, override in overrides.items():
            default = FIGURE_TOLERANCES[field]
            if override.allowance(value[field]) > default.allowance(value[field]):
                loosened.append(f"{name}={label}.{field}: {override.name} > {default.name}")

    assert loosened == []


def test_a_background_one_notch_worse_fails_on_the_shipped_row(
    record: dict[str, Any], compare: Comparison
) -> None:
    """Why ``ERROR_POINT`` is 1.0 pp and not the 1.5 pp leverage alone would allow.

    The record measures what a background window one notch from the shipped one does: four
    matched bands lost and the clean recovery error moved by more than a point. Pass that
    off as a measurement of the shipped row and the check has to say so, or a real
    regression on the shipped configuration ships in silence.
    """
    committed = json.loads(json.dumps(record))
    values = {
        str(value[LABEL_FIELD]): value for value in committed["sweeps"][SWEEP_NAME]["values"]
    }
    measured = json.loads(json.dumps(committed))
    shipped_row = next(
        value
        for value in measured["sweeps"][SWEEP_NAME]["values"]
        if str(value[LABEL_FIELD]) == "51"
    )
    shipped_row.update(
        {field: figure for field, figure in values["61"].items() if field != LABEL_FIELD}
    )

    problems = compare(committed, measured)

    assert any("clean_mean_absolute_percent" in problem for problem in problems), problems


def test_a_shipped_digest_change_fails_even_when_every_figure_is_within_tolerance(
    compare: Comparison,
) -> None:
    """The parameter-move guarantee: it is the digest that carries it, not the tolerances."""
    measured = _moved(**{field: value for field, _, value in WITHIN_TOLERANCE})
    measured["shipped"] = {
        "default": "a-different-digest",
        "rolling_ball": "digest-rolling-ball",
    }

    problems = compare(_record(), measured)

    assert len(problems) == 1, "the figures must have been tolerated, leaving only the digest"
    assert problems[0].startswith("shipped: ")
    assert "re-run" in problems[0]


@pytest.mark.parametrize("key", ("split", "iou_threshold", "images", "truth_lanes", "truth_bands"))
def test_a_header_change_fails_exactly(compare: Comparison, key: str) -> None:
    """Header fields are identity and truth counts, so any change at all is drift."""
    measured = _record()
    measured[key] = "moved" if key == "split" else measured[key] + 1

    problems = compare(_record(), measured)

    assert [problem.split(":")[0] for problem in problems] == [key]


def test_a_measured_only_sweep_fails(compare: Comparison) -> None:
    """Structure is compared exactly: a sweep the committed record does not have is drift."""
    measured = _record()
    measured["sweeps"]["invented"] = {"parameter": "x", "note": "n", "values": []}

    assert compare(_record(), measured) == ["invented: this sweep is not in the committed record"]


def test_a_committed_only_sweep_fails(compare: Comparison) -> None:
    """And so is one the committed record has that the measurement did not produce.

    The comparison used to iterate the measurement only, so this direction was invisible.
    """
    committed = _record()
    committed["sweeps"]["dropped"] = {"parameter": "x", "note": "n", "values": []}

    problems = compare(committed, _record())

    assert problems == ["dropped: this sweep is in the committed record but was not measured"]


def test_a_measured_only_value_fails(compare: Comparison) -> None:
    """A value label the committed record does not carry is drift."""
    measured = _record((BASE_VALUE, {**BASE_VALUE, LABEL_FIELD: "61"}))

    problems = compare(_record(), measured)

    assert problems == [f"{SWEEP_NAME}=61: this value is not in the committed record"]


def test_a_committed_only_value_fails(compare: Comparison) -> None:
    """As is a value label the measurement stopped producing."""
    committed = _record((BASE_VALUE, {**BASE_VALUE, LABEL_FIELD: "61"}))

    problems = compare(committed, _record())

    assert problems == [
        f"{SWEEP_NAME}=61: this value is in the committed record but was not measured"
    ]


def test_a_measured_only_field_fails(compare: Comparison) -> None:
    """A figure added to a value without regenerating the record is drift, not an addition."""
    problems = compare(_record(), _moved(clean_count=211))

    assert problems == [
        f"{SWEEP_NAME}=51.clean_count: this field is not in the committed record"
    ]


def test_a_committed_only_field_fails(compare: Comparison) -> None:
    """And a figure that has stopped being measured is drift, not a silent removal."""
    measured = _record(({key: value for key, value in BASE_VALUE.items() if key != "band_f1"},))

    problems = compare(_record(), measured)

    assert problems == [
        f"{SWEEP_NAME}=51.band_f1: this field is in the committed record but was not measured"
    ]


def test_two_values_sharing_a_label_are_rejected(compare: Comparison) -> None:
    """Labels are how values are paired, so a collision cannot be resolved by guessing.

    A numeric and a string label that render the same text collide too, which is why this
    raises instead of silently keeping the last one.
    """
    measured = _record((BASE_VALUE, {**BASE_VALUE, LABEL_FIELD: 51}))

    with pytest.raises(DuplicateValueLabelError, match="'51'"):
        compare(_record(), measured)


def test_a_sweep_without_values_is_reported_not_raised_as_a_key_error(
    compare: Comparison,
) -> None:
    """A truncated record is the author's to fix, so it fails as a ``RecordError``.

    ``main`` catches that class and prints it; a bare ``KeyError`` would traceback past it.
    """
    measured = _record()
    del measured["sweeps"][SWEEP_NAME]["values"]

    with pytest.raises(MalformedRecordError, match="no list of 'values'"):
        compare(_record(), measured)


def test_a_value_without_a_label_is_reported_not_raised_as_a_key_error(
    compare: Comparison,
) -> None:
    """Same for a value that cannot be paired because it names nothing."""
    measured = _record(({key: value for key, value in BASE_VALUE.items() if key != LABEL_FIELD},))

    with pytest.raises(MalformedRecordError, match="has no 'label'"):
        compare(_record(), measured)


@pytest.mark.parametrize("field", sweep.SWEEP_METADATA_FIELDS)
def test_sweep_metadata_is_compared_exactly(compare: Comparison, field: str) -> None:
    """``parameter`` and ``note`` describe what was measured, so they are not figures."""
    measured = _record()
    measured["sweeps"][SWEEP_NAME][field] = "something else"

    problems = compare(_record(), measured)

    assert [problem.split(":")[0] for problem in problems] == [f"{SWEEP_NAME}.{field}"]


def test_no_prose_field_quotes_a_measured_quantity(record: dict[str, Any]) -> None:
    """Notes are compared exactly, so a measurement inside one would be compared exactly.

    That is how the first version of this policy still had the bug it was fixing: the
    aperture note interpolated the size of its shared band subset -- a matched-set count,
    the very quantity a flipped tie moves -- and would have failed CI on a figure that is
    tolerated everywhere else in the record.
    """
    quoted = []
    for name, sweep_record in record["sweeps"].items():
        labels = {str(value[LABEL_FIELD]) for value in sweep_record["values"]}
        for field in sweep.SWEEP_METADATA_FIELDS:
            quoted += [
                f"{name}.{field}: {token!r}"
                for token in NUMBER.findall(sweep_record[field])
                if token not in labels and (name, token) not in PROSE_CONSTANTS
            ]

    assert quoted == [], (
        "a number in an exactly-compared note must be a value label of its own sweep or a "
        "constant listed in PROSE_CONSTANTS for that sweep; if it is a measurement, record "
        "it as a figure instead and let its tolerance class cover it"
    )


def test_an_unclassified_figure_raises_rather_than_being_compared(compare: Comparison) -> None:
    """A new figure no class claims must stop ``--check``, not default to exact or lenient.

    It raises even when the two records agree: the classification is required to compare
    the field at all, and the author of a new figure is the one who can choose the class.
    """
    with pytest.raises(UnclassifiedFigureError, match="invented_statistic_percent"):
        compare(_moved(invented_statistic_percent=1.0), _moved(invented_statistic_percent=1.0))


def test_the_unclassified_message_says_what_to_do(compare: Comparison) -> None:
    """An actionable error names both places a class can be added."""
    with pytest.raises(UnclassifiedFigureError, match="_DEFAULT_TOLERANCE_GROUPS"):
        compare(_moved(invented_statistic_percent=1.0), _moved(invented_statistic_percent=2.0))


def test_a_non_numeric_figure_raises(compare: Comparison) -> None:
    """Tolerances are numeric, so a figure that is not a number cannot be compared."""
    with pytest.raises(UnclassifiedFigureError, match="band_f1"):
        compare(_moved(band_f1="0.8506"), _moved(band_f1="0.8506"))


def test_every_figure_in_the_committed_record_is_classified(record: dict[str, Any]) -> None:
    """A figure added to the record without a tolerance class fails pytest, not only CI."""
    unclassified = []
    for name, sweep_record in record["sweeps"].items():
        for value in sweep_record["values"]:
            label = str(value[LABEL_FIELD])
            for field in value:
                if field == LABEL_FIELD:
                    continue
                try:
                    tolerance_for(name, label, field)
                except UnclassifiedFigureError as error:
                    unclassified.append(str(error))

    assert unclassified == []


def test_no_figure_is_claimed_by_two_tolerance_classes() -> None:
    """One field, one class per context -- so a reader can tell which bound governs it."""
    with pytest.raises(ValueError, match="band_f1"):
        _index_tolerances(((DETECTION_RATE, ("band_f1",)), (DETECTION_COUNT, ("band_f1",))))


def test_a_negative_tolerance_is_rejected() -> None:
    """A tolerance is a distance from the recorded figure."""
    with pytest.raises(ValueError, match="negative bound"):
        FigureTolerance("nonsense", bound=-1.0)


def test_the_detection_rate_bound_still_separates_real_parameter_effects(
    record: dict[str, Any],
) -> None:
    """What ``DETECTION_RATE``'s docstring claims, asserted from the record rather than
    transcribed into prose that could go stale."""
    def band_f1(name: str, label: str) -> float:
        value = next(
            item for item in record["sweeps"][name]["values"] if str(item[LABEL_FIELD]) == label
        )
        return float(value["band_f1"])

    effects = (
        ("band.extent_min_sigma", "2.0", "0.0"),
        (SWEEP_NAME, "51", "81"),
        ("profile_smoothing_px", "5", "9"),
    )

    for name, shipped, other in effects:
        moved = abs(band_f1(name, shipped) - band_f1(name, other))
        assert moved > DETECTION_RATE.bound, f"{name} {shipped} -> {other} moves only {moved}"


def test_the_monte_carlo_bound_still_separates_border_handling(record: dict[str, Any]) -> None:
    """Same for ``MONTE_CARLO``: the effect it must keep visible is orders above its bound."""
    row = next(
        value
        for value in record["sweeps"]["presmooth_variance"]["values"]
        if str(value[LABEL_FIELD]) == "9"
    )

    effect = abs(row["whole_image_variance_reduction"] - row["interior_variance_reduction"])

    assert effect / row["interior_variance_reduction"] > 100 * MONTE_CARLO.bound


def test_the_markdown_check_renders_the_committed_record_not_the_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This is what makes the Markdown check platform-independent.

    The measurement renders differently -- it moved a figure, within tolerance -- and that
    must not fail the check, because the file on disk is a transcription of the committed
    record and nothing else.
    """
    committed, measured = _record(), _moved(band_false_positives=37)
    path = tmp_path / MARKDOWN_PATH.name
    path.write_text(_markdown(committed), encoding="utf-8")
    monkeypatch.setattr(sweep, "MARKDOWN_PATH", path)

    assert _markdown(measured) != _markdown(committed), "otherwise this test proves nothing"
    assert list(_differences(measured, committed)) == []


def test_a_stale_markdown_file_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The report is still checked: it must be what the committed record renders."""
    committed = _record()
    path = tmp_path / MARKDOWN_PATH.name
    path.write_text(_markdown(_moved(band_false_positives=37)), encoding="utf-8")
    monkeypatch.setattr(sweep, "MARKDOWN_PATH", path)

    problems = list(_differences(_record(), committed))

    assert len(problems) == 1
    assert problems[0].startswith(f"{path}: does not match")


def test_a_missing_markdown_file_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An absent report is drift, not an empty one."""
    path = tmp_path / MARKDOWN_PATH.name
    monkeypatch.setattr(sweep, "MARKDOWN_PATH", path)

    assert list(_differences(_record(), _record())) == [f"{path}: missing"]


def test_the_committed_markdown_is_what_the_committed_record_renders(
    record: dict[str, Any],
) -> None:
    """The two committed surfaces agree, checked here as well as in ``--check``.

    Both files are in the repository, so this comparison is exact on every platform.
    """
    assert (REPO_ROOT / MARKDOWN_PATH).read_text(encoding="utf-8") == _markdown(record)
