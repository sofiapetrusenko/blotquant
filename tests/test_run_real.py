"""Tests for the Phase 3b-0 real-data runner.

Why these exist. On the run this tool was written for, all 19 crops failed at load, so the
counting path -- the part whose output the human applies a pre-registered stop rule to --
never executed once. A report generator whose numbers have never been produced is not a
measured artifact, so the counting path is exercised here against images that DO load (the
committed synthetic dev split) with a crop log written for the test.

These are not tests of the pipeline. They assert what this tool counts, what it refuses to
count, and that it never asserts a fact about a crop it did not read.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
from collections import Counter
from pathlib import Path

import pytest

from tools.phase3.run_real import (
    NOT_RUN_SHA_MISMATCH,
    PREREGISTERED_MIN_BAND_HEIGHT_PX,
    CropResult,
    RunConfig,
    analyse_result,
    main,
    read_crop_rows,
    reference_column,
    run_one,
    sha256_of,
    write_report,
)

DEV_IMAGES = [Path("data/images/dev_02.png"), Path("data/images/dev_05.png")]
CROP_LOG_HEADER = ["crop", "crop_sha256", "px", "parent", "parent_sha256", "panel_note"]


def _write_crop_log(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def fake_set(tmp_path: Path) -> RunConfig:
    """A two-crop approved set built from dev images, with a correct crop log."""
    crops_dir = tmp_path / "crops"
    crops_dir.mkdir()
    rows = []
    for index, source in enumerate(DEV_IMAGES):
        destination = crops_dir / f"fake_{index}.png"
        shutil.copyfile(source, destination)
        rows.append(
            {
                "crop": destination.name,
                "crop_sha256": sha256_of(destination),
                "px": "256x192",
                "parent": f"parent_{index}.jpg",
                "parent_sha256": "0" * 64,
                "panel_note": f"note_{index}",
            }
        )
    log = crops_dir / "crop_log.csv"
    _write_crop_log(log, rows)
    return RunConfig(
        crop_log=log,
        crops_dir=crops_dir,
        decision_path=Path("data/real/DECISION_unit_of_analysis.md"),
        pipeline_config=Path("configs/default.yaml"),
        out_dir=tmp_path / "out",
        min_band_height_px=PREREGISTERED_MIN_BAND_HEIGHT_PX,
        expected_crop_count=len(DEV_IMAGES),
    )


@pytest.fixture
def measured(fake_set: RunConfig) -> list[CropResult]:
    """The fake set actually run through the pipeline CLI, once, for report-shape tests."""
    return [run_one(row, fake_set) for row in read_crop_rows(fake_set)]


def test_read_crop_rows_refuses_a_set_of_unexpected_size(fake_set: RunConfig) -> None:
    """A set that is not the approved size is refused, not silently reported over."""
    wrong = RunConfig(**{**fake_set.__dict__, "expected_crop_count": 19})
    with pytest.raises(ValueError, match="expected 19"):
        read_crop_rows(wrong)


def test_missing_crop_raises_rather_than_shrinking_the_set(fake_set: RunConfig) -> None:
    """A listed crop absent from the tree is loud: the set is incomplete."""
    rows = read_crop_rows(fake_set)
    (fake_set.crops_dir / rows[0]["crop"]).unlink()
    with pytest.raises(FileNotFoundError, match="is listed in"):
        run_one(rows[0], fake_set)


def test_crop_whose_bytes_changed_is_not_measured(fake_set: RunConfig) -> None:
    """§9 requires byte-identical files, so a re-saved crop is refused, not measured."""
    rows = read_crop_rows(fake_set)
    target = fake_set.crops_dir / rows[0]["crop"]
    target.write_bytes(target.read_bytes() + b"\x00")

    result = run_one(rows[0], fake_set)

    assert result.exit_code == NOT_RUN_SHA_MISMATCH
    assert result.sha256_matches_log is False
    assert result.ok is False
    assert result.lanes is None, "a crop that was never run cannot have contributed a count"
    assert "NOT RUN" in result.stderr
    assert not (fake_set.out_dir / result.stem / f"{result.stem}.json").exists()


def test_a_loadable_crop_is_measured_and_counted(fake_set: RunConfig) -> None:
    """The counting path runs end to end on an image the pipeline accepts."""
    rows = read_crop_rows(fake_set)
    result = run_one(rows[0], fake_set)

    assert result.exit_code == 0, result.stderr
    assert result.ok is True
    assert result.result_path is not None and result.result_path.exists()
    assert result.lanes is not None and result.lanes > 0
    assert result.bands is not None and result.bands > 0
    assert result.usable_lanes is not None
    assert result.qualifying_bands is not None
    assert result.qualifying_bands <= result.bands
    assert result.usable_lanes <= result.lanes


def _document(band_heights: dict[str, list[int]], ratios: list[dict[str, object]]) -> dict:
    bands = []
    for lane_id, heights in band_heights.items():
        for index, height in enumerate(heights):
            bands.append(
                {
                    "band_id": f"{lane_id}_B{index}",
                    "lane_id": lane_id,
                    "roi": {"x": 0, "y": 0, "width": 30, "height": height},
                    "qc_flags": [],
                }
            )
    return {
        "lanes": [{"lane_id": lane_id} for lane_id in band_heights],
        "bands": bands,
        "image_qc_flags": [],
        "normalization": {"ratios": ratios},
    }


def test_band_criterion_is_a_height_threshold_at_the_pre_registered_value(
    fake_set: RunConfig,
) -> None:
    """A band qualifies at exactly 15 px and fails at 14 -- the boundary, not near it."""
    document = _document({"L0": [14, 15], "L1": [14], "L2": [99]}, [])
    summary = analyse_result(document, fake_set)

    assert summary["bands"] == 4
    assert summary["qualifying_bands"] == 2, "15 px qualifies, 14 px does not"
    assert summary["usable_lanes"] == 2, "L1 has no qualifying band and is not usable"


def test_excluded_ratios_are_reported_with_the_flags_that_excluded_them(
    fake_set: RunConfig,
) -> None:
    """§4: an excluded ratio is reported with its reason and flags, never as a bare count."""
    ratios = [
        {"excluded": False, "exclusion_reason": None, "qc_flags": []},
        {"excluded": True, "exclusion_reason": "band_qc_flagged", "qc_flags": ["saturated"]},
        {"excluded": True, "exclusion_reason": "band_qc_flagged", "qc_flags": ["saturated"]},
        {"excluded": True, "exclusion_reason": "lane_denominator_not_positive", "qc_flags": []},
        {
            "excluded": True,
            "exclusion_reason": "carries QC flags: saturated",
            "qc_flags": ["saturated"],
        },
    ]
    summary = analyse_result(_document({"L0": [20]}, ratios), fake_set)

    assert summary["emitted_ratios"] == 5
    assert summary["emitted_ratios_excluded"] == 4
    assert summary["excluded_ratio_reasons"] == Counter(
        {
            "band_qc_flagged (saturated)": 2,
            "lane_denominator_not_positive (no band flags recorded)": 1,
            # not "carries QC flags: saturated (saturated)" -- the reason already names them
            "carries QC flags: saturated": 1,
        }
    )


def test_report_never_names_a_crop_that_is_not_in_the_set(fake_set: RunConfig) -> None:
    """The report asserts facts about the set it was given, and about no other set."""
    exit_code = main(
        [
            "--crop-log",
            str(fake_set.crop_log),
            "--crops-dir",
            str(fake_set.crops_dir),
            "--config",
            str(fake_set.pipeline_config),
            "--out",
            str(fake_set.out_dir),
            "--expected-crops",
            str(len(DEV_IMAGES)),
        ]
    )
    assert exit_code == 0
    report = (fake_set.out_dir / "REPORT.md").read_text()

    # The property, not a list of literals a previous fix happened to name: the report may
    # contain a .png filename only if that filename is in the set it was given.
    given = {row["crop"] for row in read_crop_rows(fake_set)}
    named = set(re.findall(r"[A-Za-z0-9_.\-]+\.png", report))
    assert named <= given, f"report names files outside the set it was run over: {named - given}"
    assert given <= named, "every crop in the set should appear in the report"

    # Nor may it inherit the identity of the real Gate 2 set from a hard-coded string.
    for borrowed in (
        "Gate 2",
        "19 crops",
        "19 Gate 2",
        "first real-data run",
        "DEBT_DRAFTS",
        "the whole set",
        "this set is housekeeping-only",
    ):
        assert borrowed not in report, f"report asserts {borrowed!r} about a set that is not it"
    assert str(fake_set.crop_log) in report, "the report must name the set it actually read"

    # Where the report does invoke §8(a), it must be citing the pre-registration's set rather
    # than characterising the set in front of it. Cycle 3 caught the latter phrasing.
    for line in report.splitlines():
        if "housekeeping-only" in line:
            assert "pre-registered real-blot set" in line, line


def test_report_does_not_print_a_number_called_N(fake_set: RunConfig) -> None:
    """N is §2's, and §2 has terms this tool cannot evaluate. It reports a bound instead."""
    rows = read_crop_rows(fake_set)
    results = [run_one(row, fake_set) for row in rows]
    fake_set.out_dir.mkdir(parents=True, exist_ok=True)
    report = write_report(results, rows, fake_set, reference_column(rows)).read_text()

    assert "**N =" not in report
    assert "Upper bound on N" in report
    assert "usable lane" in report
    assert "stand-in" in report, "the invented term must be labelled as invented"
    assert "lane-width" in report, "§10's unimplemented half must be disclosed"


def test_report_says_when_the_criterion_was_overridden(fake_set: RunConfig) -> None:
    """A non-pre-registered threshold cannot borrow §8(c)'s authority silently."""
    overridden = RunConfig(**{**fake_set.__dict__, "min_band_height_px": 4})
    rows = read_crop_rows(overridden)
    results = [run_one(row, overridden) for row in rows]
    report = write_report(results, rows, overridden, reference_column(rows)).read_text()

    assert "overridden on the command line to 4 px" in report
    assert "not pre-registered counts" in report


def test_write_report_refuses_an_empty_result_set(fake_set: RunConfig) -> None:
    """A report over nothing would state a count of nothing as a measurement."""
    rows = read_crop_rows(fake_set)
    with pytest.raises(ValueError, match="no crop results"):
        write_report([], rows, fake_set, None)


def test_reference_designation_is_read_from_the_log_when_it_is_recorded(
    fake_set: RunConfig, measured: list[CropResult]
) -> None:
    """A recorded band id is read and reported -- and the report says it is not measured
    against, because nothing here passes it to --reference-band."""
    rows = read_crop_rows(fake_set)
    for index, row in enumerate(rows):
        row["reference_band_id"] = f"L{index}_B0"
    _write_crop_log(fake_set.crop_log, rows)

    reread = read_crop_rows(fake_set)
    column = reference_column(reread)
    assert column == "reference_band_id"

    report = write_report(measured, reread, fake_set, column).read_text()
    assert "no reference assigned" not in report
    assert "L0_B0" in report
    assert "--reference-band" in report, "the report must say the designation is not used"
    assert "does not measure against it" in report


def test_shared_parent_is_reported_as_an_ambiguous_blot_count(
    fake_set: RunConfig, measured: list[CropResult]
) -> None:
    """Two crops from one parent make the blot count ambiguous, and the report says so."""
    rows = read_crop_rows(fake_set)
    for row in rows:
        row["parent"] = "one_figure.jpg"
    _write_crop_log(fake_set.crop_log, rows)
    reread = read_crop_rows(fake_set)

    report = write_report(measured, reread, fake_set, None).read_text()
    assert "blot_id" in report
    assert "one_figure.jpg" in report
    assert f"is {len(DEV_IMAGES)} blots" in report


def test_distinct_parents_do_not_trigger_the_ambiguity_paragraph(
    fake_set: RunConfig, measured: list[CropResult]
) -> None:
    """The paragraph is a property of the set, not boilerplate: it stays away when it does not
    apply."""
    rows = read_crop_rows(fake_set)
    report = write_report(measured, rows, fake_set, None).read_text()
    assert "blot_id" not in report
    # ...and the counts section must not claim an ambiguity, nor cross-reference an
    # explanation this report does not contain.
    assert "range rather than a number" not in report
    assert "crop count and blot count coincide here" in report


def test_result_document_shape_assumptions_hold_against_a_real_run(fake_set: RunConfig) -> None:
    """analyse_result reads keys the pipeline actually emits, not keys assumed to exist."""
    rows = read_crop_rows(fake_set)
    result = run_one(rows[0], fake_set)
    assert result.result_path is not None
    document = json.loads(result.result_path.read_text())

    for key in ("lanes", "bands", "image_qc_flags", "normalization"):
        assert key in document
    assert "ratios" in document["normalization"]
    for band in document["bands"]:
        assert "height" in band["roi"]
        assert "qc_flags" in band
    # Only these four are required by schema/result.schema.json; exclusion_reason and
    # qc_flags are optional, which is why analyse_result reads them with .get.
    for ratio in document["normalization"]["ratios"]:
        for key in ("lane_id", "numerator_band_id", "ratio", "excluded"):
            assert key in ratio
        if ratio["excluded"]:
            assert ratio.get("exclusion_reason"), "an excluded ratio must say why"


def test_sha256_of_matches_hashlib(tmp_path: Path) -> None:
    """The digest the byte-identity gate turns on is the ordinary one."""
    path = tmp_path / "f.bin"
    path.write_bytes(b"blotquant")
    assert sha256_of(path) == hashlib.sha256(b"blotquant").hexdigest()


def test_crop_result_is_not_ok_without_a_parsed_document() -> None:
    """Exit 0 alone is not success: a result document must have been read."""
    result = CropResult(
        crop="c.png",
        stem="c",
        px="1x1",
        panel_note="",
        sha256_matches_log=True,
        exit_code=0,
        wall_clock_s=0.0,
        stdout="",
        stderr="",
    )
    assert result.ok is False


def test_shared_parent_finding_survives_a_reference_column(fake_set: RunConfig) -> None:
    """Adding the column draft D4 asks for must not erase draft D5's evidence."""
    rows = read_crop_rows(fake_set)
    for row in rows:
        row["parent"] = "one_figure.jpg"
    _write_crop_log(fake_set.crop_log, rows)
    without = write_report(
        [run_one(r, fake_set) for r in read_crop_rows(fake_set)],
        read_crop_rows(fake_set),
        fake_set,
        None,
    ).read_text()
    assert "blot_id" in without

    rows = read_crop_rows(fake_set)
    for index, row in enumerate(rows):
        row["reference_band_id"] = f"L{index}_B0"
    _write_crop_log(fake_set.crop_log, rows)
    reread = read_crop_rows(fake_set)
    with_column = write_report(
        [run_one(r, fake_set) for r in reread], reread, fake_set, reference_column(reread)
    ).read_text()

    assert "blot_id" in with_column, "the ambiguity finding must survive a reference column"
    assert "one_figure.jpg" in with_column
    assert "range rather than a number" in with_column


def test_a_blank_reference_cell_is_not_a_designation(fake_set: RunConfig) -> None:
    """The kickoff: a crop lacking an assignment is listed, not reported as designated."""
    rows = read_crop_rows(fake_set)
    rows[0]["reference_band_id"] = "L0_B0"
    rows[1]["reference_band_id"] = "   "
    _write_crop_log(fake_set.crop_log, rows)
    reread = read_crop_rows(fake_set)

    report = write_report(
        [run_one(r, fake_set) for r in reread], reread, fake_set, reference_column(reread)
    ).read_text()

    assert "no reference assigned -- excluded, needs human ruling" in report
    assert "| `fake_1.png` | `` |" not in report, "a blank cell must not be printed as an id"
    assert "1 of 2 crops carry a designation" in report


def test_missing_crop_log_column_is_refused_before_anything_runs(fake_set: RunConfig) -> None:
    """A malformed log fails loudly up front, not with a KeyError after the set has run."""
    rows = read_crop_rows(fake_set)
    for row in rows:
        del row["parent"]
    _write_crop_log(fake_set.crop_log, rows)

    with pytest.raises(ValueError, match="missing required column"):
        read_crop_rows(fake_set)


def test_exit_zero_without_a_result_document_is_reported(
    fake_set: RunConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful run that wrote nothing is a finding, not an unexplained blank failure."""
    import subprocess as sp

    # Patching the module attribute is what run_real resolves at call time; monkeypatch
    # restores it. The pipeline is not invoked at all here -- the point is the branch that
    # runs when a process exits 0 and writes nothing, which no real image produces.
    def fake_run(command: list[str], **kwargs: object) -> sp.CompletedProcess:
        return sp.CompletedProcess(command, 0, stdout="pretended to work\n", stderr="")

    monkeypatch.setattr("tools.phase3.run_real.subprocess.run", fake_run)
    result = run_one(read_crop_rows(fake_set)[0], fake_set)

    assert result.exit_code == 0
    assert result.ok is False
    assert "NO RESULT DOCUMENT" in result.stderr
    assert "pretended to work" in result.stdout


def test_an_unreadable_result_document_is_reported(
    fake_set: RunConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the exit-0 fix: a document that exists but will not parse."""
    import subprocess as sp

    rows = read_crop_rows(fake_set)
    stem = Path(rows[0]["crop"]).stem

    def fake_run(command: list[str], **kwargs: object) -> sp.CompletedProcess:
        target = fake_set.out_dir / stem
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{stem}.json").write_text("{not json at all")
        return sp.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.phase3.run_real.subprocess.run", fake_run)
    result = run_one(rows[0], fake_set)

    assert result.exit_code == 0
    assert result.ok is False
    assert "UNREADABLE RESULT DOCUMENT" in result.stderr
    assert result.lanes is None, "a document that will not parse must not be counted"


def test_stdout_survives_into_the_report_when_there_is_no_document(
    fake_set: RunConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The message promises the stdout follows, so the report must actually print it."""
    import subprocess as sp

    def fake_run(command: list[str], **kwargs: object) -> sp.CompletedProcess:
        return sp.CompletedProcess(command, 0, stdout="IMPORTANT STDOUT LINE\n", stderr="")

    monkeypatch.setattr("tools.phase3.run_real.subprocess.run", fake_run)
    rows = read_crop_rows(fake_set)
    results = [run_one(row, fake_set) for row in rows]
    report = write_report(results, rows, fake_set, None).read_text()

    assert "NO RESULT DOCUMENT" in report
    assert "IMPORTANT STDOUT LINE" in report


def test_a_not_run_crop_is_not_counted_as_a_failure(fake_set: RunConfig) -> None:
    """A crop the pipeline never saw has no exit code and is reported separately."""
    rows = read_crop_rows(fake_set)
    target = fake_set.crops_dir / rows[0]["crop"]
    target.write_bytes(target.read_bytes() + b"\x00")
    results = [run_one(row, fake_set) for row in rows]
    report = write_report(results, rows, fake_set, None).read_text()

    assert "1 were not run" in report
    assert "## Crops that were not run" in report
    assert "— exit -1" not in report, "a crop that never ran has no exit code to print"
    assert "the process's own stderr" not in report or "not run" in report


def test_quotations_are_verified_against_the_pre_registration(
    fake_set: RunConfig, tmp_path: Path
) -> None:
    """A quotation attributed to a file must actually be in that file."""
    from tools.phase3.run_real import verify_quotations

    verify_quotations(fake_set)

    absent = RunConfig(**{**fake_set.__dict__, "decision_path": tmp_path / "nope.md"})
    with pytest.raises(FileNotFoundError, match="quotes three passages"):
        verify_quotations(absent)

    wrong = tmp_path / "wrong.md"
    wrong.write_text("a file that says nothing about band heights\n")
    with pytest.raises(ValueError, match="is not in"):
        verify_quotations(RunConfig(**{**fake_set.__dict__, "decision_path": wrong}))


def test_process_stderr_survives_the_no_document_explanation(
    fake_set: RunConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This tool's explanation is added to the process's stderr, never substituted for it."""
    import subprocess as sp

    def fake_run(command: list[str], **kwargs: object) -> sp.CompletedProcess:
        return sp.CompletedProcess(command, 0, stdout="", stderr="REAL DIAGNOSTIC\n")

    monkeypatch.setattr("tools.phase3.run_real.subprocess.run", fake_run)
    rows = read_crop_rows(fake_set)
    results = [run_one(row, fake_set) for row in rows]
    report = write_report(results, rows, fake_set, None).read_text()

    assert "NO RESULT DOCUMENT" in results[0].stderr
    assert "REAL DIAGNOSTIC" in results[0].stderr, "the process's own stderr must not be lost"
    assert "REAL DIAGNOSTIC" in report


def test_a_none_panel_note_does_not_crash_the_report(fake_set: RunConfig) -> None:
    """csv.DictReader yields None for a missing trailing field; the writer must survive it."""
    rows = read_crop_rows(fake_set)
    results = [run_one(row, fake_set) for row in rows]
    rows[0]["panel_note"] = None  # type: ignore[assignment]

    report = write_report(results, rows, fake_set, None).read_text()
    assert "not recorded" in report
