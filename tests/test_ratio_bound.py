"""Tests for the ratio bound (Phase 3b-1, read-only).

Why these exist. This file's output is the number a human decides on: whether the band-mapping
gate is worth holding. A bound that is wrong in the optimistic direction would send someone
into hours of work for nothing; wrong in the pessimistic direction, it would abandon a phase
that could have finished. So the arithmetic is asserted against hand-built documents whose
answers are known by construction, and the two places it could quietly go wrong -- a lane with
no detected bands, and the reference band's exemption from exclusion -- each get their own test.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from pipeline.qc import BAND_QC_FLAGS
from tools.phase3.blot_identity import read_blot_identities
from tools.phase3.designations import read_designations
from tools.phase3.ratio_bound import (
    PREREGISTERED_MIN_BAND_HEIGHT_PX,
    LaneCount,
    _check_criterion_agrees,
    blocking_flags,
    distribution,
    lane_counts,
    main,
    report,
)

CROP = "fake_0.png"
BLOT = "BLOT_0"


def _band(band_id: str, lane_id: str, height: int, flags: list[str]) -> dict[str, object]:
    return {
        "band_id": band_id,
        "lane_id": lane_id,
        "roi": {"x": 0, "y": 0, "width": 10, "height": height},
        "integrated_intensity": 1.0,
        "qc_flags": flags,
    }


def _write_document(run_dir: Path, crop: str, lanes: list[str], bands: list[dict]) -> None:
    stem = Path(crop).stem
    (run_dir / stem).mkdir(parents=True, exist_ok=True)
    (run_dir / stem / f"{stem}.json").write_text(
        json.dumps(
            {
                "source": {"path": f"data/real/crops/{crop}"},
                "lanes": [{"lane_id": lane} for lane in lanes],
                "bands": bands,
            }
        )
    )


def _identity(tmp_path: Path, rows: list[tuple[str, str]]) -> Path:
    path = tmp_path / "blot_identity.csv"
    with path.open("w", newline="") as handle:
        handle.write("# test\n")
        writer = csv.writer(handle)
        writer.writerow(
            ["crop_filename", "parent_figure", "panel", "blot_id", "identity_source", "notes"]
        )
        for crop, blot in rows:
            writer.writerow([crop, "fig", "A", blot, "image_confirmed_human", ""])
    return path


def _designations(tmp_path: Path, rows: list[tuple[str, str]]) -> Path:
    path = tmp_path / "designations.csv"
    with path.open("w", newline="") as handle:
        handle.write("# test\n")
        writer = csv.writer(handle)
        writer.writerow(
            ["crop_filename", "target_label", "reference_label", "source_of_designation", "notes"]
        )
        for crop, source in rows:
            target = "" if source == "reference_strip_confirmed_human" else "T"
            writer.writerow([crop, target, "R", source, ""])
    return path


# --- the blocking set ---------------------------------------------------------------------


def test_the_blocking_set_is_the_pipelines_own_band_vocabulary() -> None:
    """Derived, not restated: a flag added to the vocabulary must block here without an edit."""
    assert blocking_flags() == BAND_QC_FLAGS
    assert set(blocking_flags()) == {"saturated", "overlapping", "unresolved_shoulder"}


def test_the_criterion_matches_the_runners() -> None:
    """Two modules naming the same pre-registered minimum must not drift apart."""
    _check_criterion_agrees()
    assert PREREGISTERED_MIN_BAND_HEIGHT_PX == 15


# --- the distribution ---------------------------------------------------------------------


def test_the_distribution_buckets_three_and_above_together() -> None:
    """The table has four rows, and everything from 3 up lands in the last one."""
    assert distribution([0, 0, 1, 2, 3, 7]) == {"0": 2, "1": 1, "2": 1, "3+": 2}


def test_an_empty_distribution_is_all_zeroes_not_an_error() -> None:
    """A run with no lanes yields a table of zeroes rather than a KeyError."""
    assert distribution([]) == {"0": 0, "1": 0, "2": 0, "3+": 0}


# --- counting ------------------------------------------------------------------------------


def test_a_lane_with_no_detected_bands_is_counted_as_a_lane(tmp_path: Path) -> None:
    """The one that would silently inflate every proportion below it if it were dropped."""
    run_dir = tmp_path / "run"
    _write_document(run_dir, CROP, ["L0", "L1"], [_band("L0_B0", "L0", 20, [])])
    table = read_blot_identities(_identity(tmp_path, [(CROP, BLOT)]))

    counts = lane_counts(run_dir, table, blocking_flags(), 15)

    assert len(counts) == 2, "L1 has no bands and is still a lane"
    empty = next(c for c in counts if c.lane_id == "L1")
    assert (empty.bands, empty.unflagged, empty.unflagged_tall) == (0, 0, 0)


def test_a_flagged_band_is_not_counted_as_unflagged(tmp_path: Path) -> None:
    """Any band flag blocks; the rules draw no advisory distinction at band level."""
    run_dir = tmp_path / "run"
    _write_document(
        run_dir,
        CROP,
        ["L0"],
        [
            _band("L0_B0", "L0", 20, []),
            _band("L0_B1", "L0", 20, ["overlapping"]),
            _band("L0_B2", "L0", 20, ["saturated", "unresolved_shoulder"]),
        ],
    )
    table = read_blot_identities(_identity(tmp_path, [(CROP, BLOT)]))

    (lane,) = lane_counts(run_dir, table, blocking_flags(), 15)

    assert (lane.bands, lane.unflagged) == (3, 1)


def test_the_height_criterion_is_a_second_column_not_a_filter(tmp_path: Path) -> None:
    """A short unflagged band still counts as unflagged; it only drops the second column."""
    run_dir = tmp_path / "run"
    _write_document(
        run_dir,
        CROP,
        ["L0"],
        [_band("L0_B0", "L0", 20, []), _band("L0_B1", "L0", 14, [])],
    )
    table = read_blot_identities(_identity(tmp_path, [(CROP, BLOT)]))

    (lane,) = lane_counts(run_dir, table, blocking_flags(), 15)

    assert lane.unflagged == 2, "both are unflagged"
    assert lane.unflagged_tall == 1, "only one clears the pre-registered minimum"
    assert lane.could_pair is True
    assert lane.could_pair_tall is False


def test_a_band_exactly_at_the_criterion_qualifies(tmp_path: Path) -> None:
    """§8(c) is '>= 15 px', so 15 is inside and 14 is outside."""
    run_dir = tmp_path / "run"
    _write_document(run_dir, CROP, ["L0"], [_band("L0_B0", "L0", 15, [])])
    table = read_blot_identities(_identity(tmp_path, [(CROP, BLOT)]))

    (lane,) = lane_counts(run_dir, table, blocking_flags(), 15)

    assert lane.unflagged_tall == 1


def test_the_blot_id_comes_from_the_ruled_table(tmp_path: Path) -> None:
    """A bound grouped by filename would count naming conventions against the blot floor."""
    run_dir = tmp_path / "run"
    _write_document(run_dir, CROP, ["L0"], [_band("L0_B0", "L0", 20, [])])
    table = read_blot_identities(_identity(tmp_path, [(CROP, "RULED_ELSEWHERE")]))

    (lane,) = lane_counts(run_dir, table, blocking_flags(), 15)

    assert lane.blot_id == "RULED_ELSEWHERE"


def test_a_crop_with_no_ruled_identity_stops_the_bound(tmp_path: Path) -> None:
    """The guarded reader raises rather than grouping a crop under a default blot."""
    run_dir = tmp_path / "run"
    _write_document(run_dir, CROP, ["L0"], [_band("L0_B0", "L0", 20, [])])
    table = read_blot_identities(_identity(tmp_path, [("other.png", BLOT)]))

    with pytest.raises(Exception, match="no row in the blot identity table"):
        lane_counts(run_dir, table, blocking_flags(), 15)


# --- the two bounds -------------------------------------------------------------------------


def _lane(unflagged: int, bands: int, unflagged_tall: int, any_tall: int) -> LaneCount:
    return LaneCount(
        crop_filename=CROP,
        blot_id=BLOT,
        lane_id="L0",
        bands=bands,
        unflagged=unflagged,
        unflagged_tall=unflagged_tall,
        any_band_tall=any_tall,
    )


def test_two_unflagged_bands_is_the_conservative_pairing_bound() -> None:
    """One clean band cannot make a ratio with itself."""
    assert _lane(2, 2, 0, 0).could_pair is True
    assert _lane(1, 3, 0, 0).could_pair is False


def test_the_reference_exemption_widens_the_bound() -> None:
    """normalize.py does not exclude the reference, so one clean band plus any second suffices."""
    lane = _lane(unflagged=1, bands=3, unflagged_tall=0, any_tall=0)
    assert lane.could_pair is False
    assert lane.could_pair_flagged_reference is True


def test_a_lone_clean_band_with_no_second_band_cannot_pair_either_way() -> None:
    """There is nothing to be a reference: the exemption widens the bound, it does not remove it."""
    lane = _lane(unflagged=1, bands=1, unflagged_tall=1, any_tall=1)
    assert lane.could_pair is False
    assert lane.could_pair_flagged_reference is False


def test_the_reference_exempt_tall_bound_requires_both_bands_to_clear_the_criterion() -> None:
    """A tall clean band paired with a short reference does not survive the second column."""
    assert _lane(1, 2, 1, 1).could_pair_flagged_reference_tall is False
    assert _lane(1, 2, 1, 2).could_pair_flagged_reference_tall is True


# --- the report ------------------------------------------------------------------------------


def test_the_report_states_no_verdict_and_no_statistic(tmp_path: Path) -> None:
    """The stop point is the deliverable; this file must not pre-empt the gate or the rule."""
    run_dir = tmp_path / "run"
    _write_document(run_dir, CROP, ["L0"], [_band("L0_B0", "L0", 20, [])])
    identities = read_blot_identities(_identity(tmp_path, [(CROP, BLOT)]))
    designations = read_designations(_designations(tmp_path, [(CROP, "caption_confirmed_human")]))

    text = "\n".join(
        report(
            lane_counts(run_dir, identities, blocking_flags(), 15),
            designations,
            blocking_flags(),
            15,
            run_dir,
        )
    )

    assert "no stop-rule branch selected" in text
    assert "WITHDRAWN AS EVIDENCE ABOUT N" in text, (
        "R2's withdrawal is emitted by the generator, so re-running cannot erase it"
    )
    assert "N is UNKNOWN, not small" in text
    for forbidden in ("Spearman", "Bland", "r_s"):
        assert forbidden not in text

    # The two stop-rule branch names are permitted inside R2's withdrawal notice, which names
    # them in order to say neither is selected, and nowhere else. Checking the whole document
    # would have to choose between forbidding the notice and forbidding nothing.
    body = text.split("meaningful after polarity is handled under R4.", 1)[1]
    for forbidden in ("verdict-eligible", "descriptive-only"):
        assert forbidden not in body


def test_the_report_excludes_the_reference_strip_from_the_blot_count(tmp_path: Path) -> None:
    """A blot ruled to contribute no ratio is shown, and is not counted toward the floor."""
    run_dir = tmp_path / "run"
    _write_document(run_dir, CROP, ["L0"], [_band("L0_B0", "L0", 20, [])])
    _write_document(run_dir, "strip.png", ["L0"], [_band("L0_B0", "L0", 20, [])])
    identities = read_blot_identities(
        _identity(tmp_path, [(CROP, BLOT), ("strip.png", "BLOT_STRIP")])
    )
    designations = read_designations(
        _designations(
            tmp_path,
            [(CROP, "caption_confirmed_human"), ("strip.png", "reference_strip_confirmed_human")],
        )
    )

    text = "\n".join(
        report(
            lane_counts(run_dir, identities, blocking_flags(), 15),
            designations,
            blocking_flags(),
            15,
            run_dir,
        )
    )

    assert "`1` of the 2 blots are ruled to contribute ratios" in text
    assert "| `BLOT_STRIP` |" in text and "| no |" in text


def test_an_empty_run_directory_is_refused_rather_than_reported_as_zero(tmp_path: Path) -> None:
    """Zero lanes from an empty directory would read as a finding about the blots."""
    identity = _identity(tmp_path, [(CROP, BLOT)])
    designations = _designations(tmp_path, [(CROP, "caption_confirmed_human")])
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(ValueError, match="no result documents"):
        main(
            [
                "--run-dir",
                str(empty),
                "--blot-identity",
                str(identity),
                "--designations",
                str(designations),
            ]
        )


def test_the_counts_are_internally_consistent(tmp_path: Path) -> None:
    """The distribution must account for every lane and every clean band, with none invented."""
    run_dir = tmp_path / "run"
    _write_document(
        run_dir,
        CROP,
        ["L0", "L1", "L2"],
        [
            _band("L0_B0", "L0", 20, []),
            _band("L0_B1", "L0", 20, []),
            _band("L1_B0", "L1", 20, ["saturated"]),
            _band("L2_B0", "L2", 20, []),
        ],
    )
    identities = read_blot_identities(_identity(tmp_path, [(CROP, BLOT)]))

    counts = lane_counts(run_dir, identities, blocking_flags(), 15)
    buckets = distribution([c.unflagged for c in counts])

    assert sum(buckets.values()) == len(counts) == 3
    assert sum(c.unflagged for c in counts) == 3, "three unflagged bands, one flagged"
    assert buckets == {"0": 1, "1": 1, "2": 1, "3+": 0}
