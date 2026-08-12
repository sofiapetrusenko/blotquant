"""The eval runner: split discipline, aggregation arithmetic, and honest reporting."""

from __future__ import annotations

import io
import json
import re
import shutil
from pathlib import Path

import numpy as np
import pytest
import tifffile

from evals.metrics import (
    PLAN_IOU_THRESHOLD,
    DetectionScores,
    micro_average_detection_scores,
)
from evals.run import (
    EVALUATED_SPLIT,
    ImageEvaluation,
    build_parser,
    evaluate_image,
    load_split,
    main,
    print_report,
)
from pipeline.config import load_config
from tests.test_pipeline_config import CONFIG_DIR

DEFAULT_CONFIG = CONFIG_DIR / "default.yaml"
MINIMUM_BAND_F1 = 0.5
"""A floor, not a target: it catches a pipeline that stopped finding bands at all.

Deliberately far below the measured score (NOTES.md carries that), because parameter
iteration on the dev split must not have to edit the test suite.
"""


def _scores(true_positives: int, false_positives: int, false_negatives: int) -> DetectionScores:
    """Return a DetectionScores carrying only the counts micro_average reads."""
    return DetectionScores(
        iou_threshold=PLAN_IOU_THRESHOLD,
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        precision=0.0,
        recall=0.0,
        f1=0.0,
        matches=(),
    )


def test_the_runner_evaluates_the_dev_split_only(committed_data_dir: Path) -> None:
    """Split discipline: nothing here reads the test split."""
    assert EVALUATED_SPLIT == "dev"

    documents = load_split(committed_data_dir)

    assert documents, "the committed gold set has no dev images"
    assert {document["split"] for document in documents} == {"dev"}
    expected = sum(
        1
        for path in (committed_data_dir / "ground_truth").glob("*.json")
        if json.loads(path.read_text(encoding="utf-8"))["split"] == "dev"
    )
    assert len(documents) == expected


def test_the_runner_offers_no_way_to_select_another_split() -> None:
    """There is no --split flag: pointing this runner at test must take a code change."""
    options = {
        option for action in build_parser()._actions for option in action.option_strings
    }

    assert "--split" not in options
    assert options == {"-h", "--help", "--config", "--data"}


def test_missing_ground_truth_raises(tmp_path: Path) -> None:
    """A gold set that is not there fails loudly, naming the command that makes one."""
    with pytest.raises(FileNotFoundError, match="python -m synth"):
        load_split(tmp_path)


def test_a_gold_set_without_the_evaluated_split_raises(tmp_path: Path) -> None:
    """Ground truth that holds no dev image has nothing to evaluate."""
    truth_dir = tmp_path / "ground_truth"
    truth_dir.mkdir()
    (truth_dir / "only.json").write_text(json.dumps({"split": "test"}), encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="nothing to evaluate"):
        load_split(tmp_path)


def test_micro_average_pools_counts_rather_than_averaging_f1() -> None:
    """Pooled precision/recall/F1 come from summed confusion counts."""
    pooled = micro_average_detection_scores(
        [_scores(8, 2, 2), _scores(2, 0, 8)],
        PLAN_IOU_THRESHOLD,
    )

    assert (pooled.true_positives, pooled.false_positives, pooled.false_negatives) == (10, 2, 10)
    assert pooled.precision == pytest.approx(10 / 12)
    assert pooled.recall == pytest.approx(10 / 20)
    assert pooled.f1 == pytest.approx(2 * (10 / 12) * 0.5 / ((10 / 12) + 0.5))
    assert pooled.matches == ()


def test_micro_average_of_no_predictions_is_zero_not_undefined() -> None:
    """Finding nothing is a real 0.0, matching evals.metrics' documented convention."""
    pooled = micro_average_detection_scores([_scores(0, 0, 5)], PLAN_IOU_THRESHOLD)

    assert pooled.precision == 0.0
    assert pooled.recall == 0.0
    assert pooled.f1 == 0.0


def test_one_gold_set_image_scores_end_to_end(committed_data_dir: Path) -> None:
    """A committed dev image runs through the pipeline and joins to its ground truth."""
    truth = load_split(committed_data_dir)[0]
    config = load_config(CONFIG_DIR / "default.yaml")

    evaluation = evaluate_image(truth, committed_data_dir, config, PLAN_IOU_THRESHOLD)

    assert evaluation.image_id == truth["image_id"]
    assert evaluation.lanes.true_positives + evaluation.lanes.false_negatives == len(
        truth["lanes"]
    )
    assert evaluation.bands.true_positives + evaluation.bands.false_negatives == len(
        truth["bands"]
    )
    assert (
        len(evaluation.true_intensities)
        == len(evaluation.predicted_intensities)
        == len(evaluation.saturated)
        == evaluation.bands.true_positives
    )
    assert all(value > 0.0 for value in evaluation.true_intensities)


def test_the_report_states_how_saturated_bands_are_treated() -> None:
    """Saturated bands are counted in the headline figure and also shown separately."""
    evaluation = ImageEvaluation(
        image_id="fixture",
        lanes=_scores(2, 0, 0),
        bands=_scores(2, 0, 0),
        true_intensities=(100.0, 200.0),
        predicted_intensities=(110.0, 100.0),
        saturated=(False, True),
    )
    stream = io.StringIO()

    print_report([evaluation], [], load_config(CONFIG_DIR / "default.yaml"), 0.5, stream)

    text = stream.getvalue()
    assert "all 2 matched bands" in text
    assert "1 without a truth 'saturated' flag" in text
    assert "never dropped" in text
    assert "split=dev" in text


def _write_gold_set(
    tmp_path: Path, committed_data_dir: Path, *, good: int, broken: int
) -> Path:
    """Build a gold-set-shaped directory with ``good`` real images and ``broken`` bad ones.

    Real entries are copied from the committed dev split so they carry usable ground
    truth; broken entries point at a float TIFF, which the loader refuses.
    """
    root = tmp_path / "goldset"
    (root / "ground_truth").mkdir(parents=True)
    (root / "images").mkdir()
    for truth in load_split(committed_data_dir)[:good]:
        image = Path(truth["image_path"])
        shutil.copy(committed_data_dir / image, root / image)
        (root / "ground_truth" / f"{truth['image_id']}.json").write_text(
            json.dumps(truth), encoding="utf-8"
        )
    for index in range(broken):
        image_id = f"broken_{index}"
        tifffile.imwrite(str(root / "images" / f"{image_id}.tiff"), np.zeros((60, 60), np.float32))
        (root / "ground_truth" / f"{image_id}.json").write_text(
            json.dumps(
                {
                    "image_id": image_id,
                    "image_path": f"images/{image_id}.tiff",
                    "split": EVALUATED_SPLIT,
                    "lanes": [],
                    "bands": [],
                }
            ),
            encoding="utf-8",
        )
    return root


def test_main_scores_the_whole_dev_split_without_intervention(
    committed_data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """PLAN.md's done-when, enforced: every dev image analyses and the table prints.

    This is the one test that runs the entire dev split through the pipeline, and by far
    the slowest in the suite. Without it, a regression that made most images raise would
    leave pytest green, because every other gold-set test analyses a single image.
    """
    code = main(["--config", str(DEFAULT_CONFIG), "--data", str(committed_data_dir)])

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "FAILED" not in captured.out
    assert captured.err == ""
    expected_ids = {truth["image_id"] for truth in load_split(committed_data_dir)}
    printed_ids = set(re.findall(r"^(\S+)\s+\d+/\d+/\d+", captured.out, flags=re.MULTILINE))
    assert expected_ids <= printed_ids
    assert "TOTAL" in captured.out
    assert "intensity recovery, all" in captured.out
    assert "without a truth 'saturated' flag" in captured.out
    band_f1 = float(re.search(r"band detection : P=\S+ R=\S+ F1=(\S+)", captured.out).group(1))
    assert band_f1 > MINIMUM_BAND_F1


def test_main_reports_a_failing_image_and_still_scores_the_rest(
    committed_data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failure is loud on stderr, listed in the report, and turns the exit code non-zero."""
    root = _write_gold_set(tmp_path, committed_data_dir, good=1, broken=1)

    code = main(["--config", str(DEFAULT_CONFIG), "--data", str(root)])

    captured = capsys.readouterr()
    assert code == 1
    assert "error: broken_0" in captured.err
    assert "FAILED on 1 image(s)" in captured.out
    assert "UnsupportedBitDepthError" in captured.out
    assert "TOTAL" in captured.out
    good_id = load_split(committed_data_dir)[0]["image_id"]
    assert good_id in captured.out


def test_main_exits_nonzero_when_every_image_fails(
    committed_data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With nothing analysable there are no scores to report, and that is not a success."""
    root = _write_gold_set(tmp_path, committed_data_dir, good=0, broken=2)

    code = main(["--config", str(DEFAULT_CONFIG), "--data", str(root)])

    captured = capsys.readouterr()
    assert code == 1
    assert "every image failed" in captured.err
    assert "TOTAL" not in captured.out


def test_main_propagates_a_bad_config(committed_data_dir: Path, tmp_path: Path) -> None:
    """A malformed config is not a per-image failure and must not be swallowed."""
    from pipeline.errors import ConfigError

    config = tmp_path / "broken.yaml"
    config.write_text("id: broken\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        main(["--config", str(config), "--data", str(committed_data_dir)])


def test_the_report_lists_failures(tmp_path: Path) -> None:
    """An image the pipeline could not analyse is named in the report."""
    evaluation = ImageEvaluation(
        image_id="ok",
        lanes=_scores(1, 0, 0),
        bands=_scores(1, 0, 0),
        true_intensities=(50.0,),
        predicted_intensities=(52.0,),
        saturated=(False,),
    )
    stream = io.StringIO()

    print_report(
        [evaluation],
        [("broken", "DetectionError: no lane peak reached")],
        load_config(CONFIG_DIR / "default.yaml"),
        0.5,
        stream,
    )

    text = stream.getvalue()
    assert "FAILED on 1 image(s)" in text
    assert "broken: DetectionError" in text
