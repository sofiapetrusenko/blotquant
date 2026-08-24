"""Tests for the pending label-to-band mapping table (W7 pass 1).

Why these exist. This table is the input to a human gate of the same class as G1 and G2, and
the only thing standing between "the pipeline detected a band here" and "this band is the
loading control" is the read path refusing to answer. A read path whose refusals are untested
is a default waiting to happen -- which is the standard the designation and identity tables
were held to, and this one is held to it too.

The positive direction is tested with a hand-written confirmed table rather than a committed
one, because nothing in the tree is confirmed: the human has not held the gate.
"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest

from pipeline.errors import PipelineError
from tools.phase3.band_mapping import (
    COLUMNS,
    CONFIRMED_SOURCES,
    DETECTED_PENDING,
    MOLECULAR_WEIGHT_CONFIRMED,
    PENDING_SOURCES,
    BandMapping,
    BandMappingError,
    confirmed_band_ids,
    mappings_for_document,
    rank_by_integrated_intensity,
    read_band_mappings,
    write_band_mappings,
)
from tools.phase3.crop_names import CropSetError
from tools.phase3.run_real import (
    PREREGISTERED_MIN_BAND_HEIGHT_PX,
    RunConfig,
    detection_pass,
    sha256_of,
)

DEV_IMAGES = [Path("data/images/dev_02.png"), Path("data/images/dev_05.png")]


def _mapping(**overrides: object) -> BandMapping:
    """A well-formed pending row, with named fields overridden."""
    fields: dict[str, object] = {
        "crop_filename": "crop_a.png",
        "blot_id": "FIG1_A",
        "detected_band_id": "L0_B0",
        "lane_id": "L0",
        "y_position": 12,
        "height_px": 20,
        "integrated_intensity_rank": 1,
        "mapping_source": DETECTED_PENDING,
    }
    fields.update(overrides)
    return BandMapping(**fields)  # type: ignore[arg-type]


def _document(bands: list[dict[str, object]]) -> dict[str, object]:
    """The one part of a result document this module reads."""
    return {"bands": bands}


def _band(band_id: str, lane_id: str, y: int, height: int, intensity: float) -> dict[str, object]:
    return {
        "band_id": band_id,
        "lane_id": lane_id,
        "roi": {"x": 0, "y": y, "width": 10, "height": height},
        "integrated_intensity": intensity,
    }


# --- the row shape ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["crop_filename", "blot_id", "detected_band_id", "lane_id", "mapping_source"],
)
def test_a_row_with_a_blank_identifier_cannot_be_constructed(field: str) -> None:
    """Every row names a crop, a blot, a band, a lane and its mapping state."""
    with pytest.raises(BandMappingError, match=f"blank {field}"):
        _mapping(**{field: "   "})


def test_a_negative_y_position_is_refused() -> None:
    """A band ROI's y is a pixel row; a negative one did not come from a result document."""
    with pytest.raises(BandMappingError, match="cannot be negative"):
        _mapping(y_position=-1)


def test_a_zero_height_band_is_refused() -> None:
    """A detected band occupies at least one pixel row."""
    with pytest.raises(BandMappingError, match="at least one pixel row"):
        _mapping(height_px=0)


def test_a_zero_rank_is_refused() -> None:
    """Ranks start at 1, so 0 would read as 'unranked' on a column where every row is ranked."""
    with pytest.raises(BandMappingError, match="ranks start at 1"):
        _mapping(integrated_intensity_rank=0)


def test_a_y_position_of_zero_is_allowed() -> None:
    """A band touching the top of the crop is a real band, not a malformed row."""
    assert _mapping(y_position=0).y_position == 0


def test_the_error_class_is_a_crop_set_error() -> None:
    """A caller wanting every crop-set table failure in one clause catches the base."""
    assert issubclass(BandMappingError, CropSetError)


# --- ranking ---------------------------------------------------------------------------


def test_rank_is_within_the_lane_not_across_the_image() -> None:
    """Two lanes each get a rank 1; ranking across lanes would compare unrelated bands."""
    bands = [
        _band("L0_B0", "L0", 10, 20, 5.0),
        _band("L0_B1", "L0", 40, 20, 9.0),
        _band("L1_B0", "L1", 10, 20, 1.0),
        _band("L1_B1", "L1", 40, 20, 2.0),
    ]
    ranks = rank_by_integrated_intensity(bands)
    assert ranks == {"L0_B1": 1, "L0_B0": 2, "L1_B1": 1, "L1_B0": 2}


def test_ties_are_broken_by_band_id_so_two_runs_agree() -> None:
    """A report that changes between two runs of the same bytes is not a record."""
    bands = [
        _band("L0_B1", "L0", 40, 20, 7.0),
        _band("L0_B0", "L0", 10, 20, 7.0),
    ]
    first = rank_by_integrated_intensity(bands)
    second = rank_by_integrated_intensity(list(reversed(bands)))
    assert first == second == {"L0_B0": 1, "L0_B1": 2}


def test_rank_does_not_depend_on_vertical_position() -> None:
    """The rank is intensity, not geometry -- geometry is what §2 forbids choosing on."""
    bands = [_band("L0_B0", "L0", 5, 20, 1.0), _band("L0_B1", "L0", 90, 20, 8.0)]
    assert rank_by_integrated_intensity(bands)["L0_B1"] == 1


# --- building rows from a document ------------------------------------------------------


def test_every_band_becomes_exactly_one_pending_row() -> None:
    """One row per detected band, and no row claims a confirmation."""
    bands = [_band("L0_B0", "L0", 5, 20, 1.0), _band("L0_B1", "L0", 90, 30, 8.0)]
    rows = mappings_for_document("crop_a.png", "FIG1_A", _document(bands))

    assert [row.detected_band_id for row in rows] == ["L0_B0", "L0_B1"]
    assert [row.height_px for row in rows] == [20, 30]
    assert [row.y_position for row in rows] == [5, 90]
    assert {row.mapping_source for row in rows} == {DETECTED_PENDING}
    assert {row.blot_id for row in rows} == {"FIG1_A"}


def test_a_document_with_no_bands_yields_no_rows() -> None:
    """A crop the pipeline found nothing in contributes no candidate, and does not crash."""
    assert mappings_for_document("crop_a.png", "FIG1_A", _document([])) == []


# --- writing ---------------------------------------------------------------------------


def test_the_writer_refuses_a_destination_inside_the_gold_set(tmp_path: Path) -> None:
    """`--out` is a path from the command line; the PLAN.md invariant is enforced, not assumed."""
    with pytest.raises(PipelineError, match="ground truth"):
        write_band_mappings([_mapping()], tmp_path / "data" / "ground_truth" / "x.csv")


def test_the_writer_refuses_two_rows_for_one_band(tmp_path: Path) -> None:
    """One band cannot be mapped twice: a human would have two places to confirm it."""
    path = tmp_path / "mapping.csv"
    with pytest.raises(BandMappingError, match="appears more than once"):
        write_band_mappings([_mapping(), _mapping()], path)
    assert not path.exists(), "the refusal must happen before anything is written"


def test_the_same_band_id_on_two_crops_is_not_a_duplicate(tmp_path: Path) -> None:
    """Band ids are per crop; `L0_B0` on two crops is two bands, not one written twice."""
    path = write_band_mappings(
        [_mapping(), _mapping(crop_filename="crop_b.png", blot_id="FIG1_B")],
        tmp_path / "mapping.csv",
    )
    assert len(read_band_mappings(path)) == 2


def test_the_written_file_carries_the_header_note_and_the_columns(tmp_path: Path) -> None:
    """The file says what it is, and the CSV header is the first non-comment line."""
    path = write_band_mappings([_mapping()], tmp_path / "mapping.csv")
    lines = path.read_text().splitlines()

    assert lines[0].startswith("# blotquant real-blot label-to-band mapping")
    assert any("NOTHING IN THIS FILE IS CONFIRMED" in line for line in lines)
    assert any("Guessing the loading control from the data is forbidden" in line for line in lines)
    first_data = next(line for line in lines if not line.startswith("#"))
    assert first_data == ",".join(COLUMNS)


def test_the_table_round_trips_through_the_file(tmp_path: Path) -> None:
    """What is read back is what was written, measurements included."""
    rows = [
        _mapping(),
        _mapping(detected_band_id="L0_B1", y_position=44, integrated_intensity_rank=2),
    ]
    path = write_band_mappings(rows, tmp_path / "mapping.csv")
    assert read_band_mappings(path)["crop_a.png"] == rows


# --- reading ---------------------------------------------------------------------------


def test_a_missing_table_is_not_generated_on_demand(tmp_path: Path) -> None:
    """The table is a detection artefact ruled on by a human, not a default."""
    with pytest.raises(BandMappingError, match="is missing"):
        read_band_mappings(tmp_path / "nope.csv")


def test_reordered_columns_are_refused(tmp_path: Path) -> None:
    """A reordered column changes what every row means, so it is refused, not tolerated."""
    path = tmp_path / "mapping.csv"
    reordered = [COLUMNS[1], COLUMNS[0], *COLUMNS[2:]]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=reordered)
        writer.writeheader()
        writer.writerow(_mapping().as_row())

    with pytest.raises(BandMappingError, match="expected exactly"):
        read_band_mappings(path)


def test_a_repeated_band_on_one_crop_is_refused_on_read(tmp_path: Path) -> None:
    """Two mappings for one band cannot both be the human's."""
    path = tmp_path / "mapping.csv"
    text = write_band_mappings([_mapping()], path).read_text()
    path.write_text(text + text.splitlines()[-1] + "\n")

    with pytest.raises(BandMappingError, match="more than once"):
        read_band_mappings(path)


def test_a_non_integer_measurement_is_refused(tmp_path: Path) -> None:
    """These columns are measurements copied from a result document, not free text."""
    path = tmp_path / "mapping.csv"
    write_band_mappings([_mapping()], path)
    path.write_text(path.read_text().replace(",12,20,1,", ",about 12,20,1,"))

    with pytest.raises(BandMappingError, match="not an\n?\\s*integer|not an integer"):
        read_band_mappings(path)


def test_a_blank_crop_cell_is_refused(tmp_path: Path) -> None:
    """A row that names no crop would be a mapping onto nothing."""
    path = tmp_path / "mapping.csv"
    write_band_mappings([_mapping()], path)
    path.write_text(path.read_text().replace("crop_a.png,FIG1_A", ",FIG1_A"))

    with pytest.raises(BandMappingError, match="blank crop_filename"):
        read_band_mappings(path)


# --- the read path, which is the point --------------------------------------------------


def test_asking_for_a_confirmation_on_a_pending_crop_raises(tmp_path: Path) -> None:
    """Every crop in the tree is in this state, and this is the answer it must give."""
    path = write_band_mappings([_mapping()], tmp_path / "mapping.csv")
    table = read_band_mappings(path)

    with pytest.raises(BandMappingError, match="pending mapping_source"):
        confirmed_band_ids(table, "crop_a.png")


def test_a_crop_with_no_rows_raises_rather_than_returning_nothing(tmp_path: Path) -> None:
    """'No rows' is 'unrecorded', not 'no bands' -- collapsing them drops a crop silently."""
    path = write_band_mappings([_mapping()], tmp_path / "mapping.csv")
    table = read_band_mappings(path)

    with pytest.raises(BandMappingError, match="no rows in the band-mapping table"):
        confirmed_band_ids(table, "never_cropped.png")


def test_an_unrecognised_source_is_never_read_as_a_confirmation(tmp_path: Path) -> None:
    """The allow-list direction: not pending must not mean confirmed."""
    path = write_band_mappings(
        [_mapping(mapping_source="looks_confirmed_to_me")], tmp_path / "mapping.csv"
    )
    table = read_band_mappings(path)

    with pytest.raises(BandMappingError, match="does not know"):
        confirmed_band_ids(table, "crop_a.png")


def test_one_pending_row_among_confirmed_ones_still_raises(tmp_path: Path) -> None:
    """A partly ruled crop is not a ruled crop; the unruled band would drop out silently."""
    path = write_band_mappings(
        [
            _mapping(mapping_source=MOLECULAR_WEIGHT_CONFIRMED),
            _mapping(detected_band_id="L0_B1", integrated_intensity_rank=2),
        ],
        tmp_path / "mapping.csv",
    )
    table = read_band_mappings(path)

    with pytest.raises(BandMappingError, match="1 of 2 bands"):
        confirmed_band_ids(table, "crop_a.png")


def test_a_fully_confirmed_crop_returns_its_band_ids(tmp_path: Path) -> None:
    """The positive direction, on a hand-written table: nothing in the tree is confirmed."""
    path = write_band_mappings(
        [
            _mapping(mapping_source=MOLECULAR_WEIGHT_CONFIRMED),
            _mapping(
                detected_band_id="L0_B1",
                integrated_intensity_rank=2,
                mapping_source=MOLECULAR_WEIGHT_CONFIRMED,
            ),
        ],
        tmp_path / "mapping.csv",
    )
    assert confirmed_band_ids(read_band_mappings(path), "crop_a.png") == ("L0_B0", "L0_B1")


def test_the_vocabularies_do_not_overlap() -> None:
    """A word cannot mean both 'nobody has looked' and 'a human ruled'."""
    assert not (PENDING_SOURCES & CONFIRMED_SOURCES)
    assert DETECTED_PENDING in PENDING_SOURCES
    assert MOLECULAR_WEIGHT_CONFIRMED in CONFIRMED_SOURCES


# --- the wiring, end to end --------------------------------------------------------------


@pytest.fixture
def wired_set(tmp_path: Path) -> RunConfig:
    """A two-crop approved set with confirmed designation and identity tables beside it."""
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
                # A crop-log column that LOOKS like a designation. Nothing may read it.
                "reference_band_id": f"L{index}_B0",
            }
        )
    log = crops_dir / "crop_log.csv"
    with log.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    designations = tmp_path / "designations.csv"
    with designations.open("w", newline="") as handle:
        handle.write("# test table\n")
        writer = csv.writer(handle)
        writer.writerow(
            ["crop_filename", "target_label", "reference_label", "source_of_designation", "notes"]
        )
        writer.writerow(["fake_0.png", "TARGET", "REF", "caption_confirmed_human", ""])
        writer.writerow(["fake_1.png", "A|B", "REF", "caption_confirmed_human", ""])

    identity = tmp_path / "blot_identity.csv"
    with identity.open("w", newline="") as handle:
        handle.write("# test table\n")
        writer = csv.writer(handle)
        writer.writerow(
            ["crop_filename", "parent_figure", "panel", "blot_id", "identity_source", "notes"]
        )
        writer.writerow(["fake_0.png", "parent_0", "A", "BLOT_0", "image_confirmed_human", ""])
        writer.writerow(["fake_1.png", "parent_1", "B", "BLOT_1", "image_confirmed_human", ""])

    return RunConfig(
        crop_log=log,
        crops_dir=crops_dir,
        decision_path=Path("data/real/DECISION_unit_of_analysis.md"),
        pipeline_config=Path("configs/default.yaml"),
        out_dir=tmp_path / "out",
        min_band_height_px=PREREGISTERED_MIN_BAND_HEIGHT_PX,
        expected_crop_count=len(DEV_IMAGES),
        designations_path=designations,
        blot_identity_path=identity,
        detection_only=True,
    )


def test_the_detection_pass_writes_a_pending_row_for_every_detected_band(
    wired_set: RunConfig,
) -> None:
    """End to end: run detection, and every band the documents carry gets one pending row."""
    assert detection_pass(wired_set) == 0

    table = read_band_mappings(wired_set.out_dir / "band_mapping_pending.csv")
    detected = 0
    for path in sorted(wired_set.out_dir.glob("*/*.json")):
        detected += len(json.loads(path.read_text())["bands"])

    assert sum(len(rows) for rows in table.values()) == detected
    assert detected > 0, "the fixture must actually detect something for this to mean anything"
    sources = {row.mapping_source for rows in table.values() for row in rows}
    assert sources == {DETECTED_PENDING}


def test_the_blot_id_comes_from_the_ruled_table_not_the_filename(wired_set: RunConfig) -> None:
    """A blot id assembled from filenames would make the stop rule count naming conventions."""
    detection_pass(wired_set)
    table = read_band_mappings(wired_set.out_dir / "band_mapping_pending.csv")

    assert {row.blot_id for row in table["fake_0.png"]} == {"BLOT_0"}
    assert {row.blot_id for row in table["fake_1.png"]} == {"BLOT_1"}


def test_a_crop_log_reference_column_is_not_read_as_a_designation(wired_set: RunConfig) -> None:
    """The crop log carries `reference_band_id`; the ruled table says `REF`, and it wins."""
    detection_pass(wired_set)
    report = (wired_set.out_dir / "DETECTION_REPORT.md").read_text()

    assert "`REF`" in report, "the designation must come from the ruled table"
    assert "L0_B0 |" not in report and "reference_band_id" not in report


def test_the_two_target_mechanism_survives_into_the_report(wired_set: RunConfig) -> None:
    """A crop with two targets is reported as two, not folded into one hyphenated label."""
    detection_pass(wired_set)
    report = (wired_set.out_dir / "DETECTION_REPORT.md").read_text()

    assert "A\\|B" in report
    assert "two targets sharing one reference" in report


def test_a_pending_designation_stops_the_detection_pass(wired_set: RunConfig) -> None:
    """An unconfirmed row is refused rather than measured against, at the run's own boundary."""
    text = wired_set.designations_path.read_text()
    wired_set.designations_path.write_text(
        text.replace("caption_confirmed_human", "parsed_pending_human", 1)
    )

    with pytest.raises(CropSetError, match="no human has confirmed|pending"):
        detection_pass(wired_set)


def test_a_pending_identity_stops_the_detection_pass(wired_set: RunConfig) -> None:
    """Same on the other table: a proposed blot id never reaches the mapping file."""
    text = wired_set.blot_identity_path.read_text()
    wired_set.blot_identity_path.write_text(
        text.replace("image_confirmed_human", "proposed_pending_human", 1)
    )

    with pytest.raises(CropSetError, match="no human has ruled|pending"):
        detection_pass(wired_set)


def test_an_unruled_table_stops_the_run_before_anything_is_written(wired_set: RunConfig) -> None:
    """The refusal is demanded up front, not after twelve subprocesses have produced a file."""
    text = wired_set.designations_path.read_text()
    wired_set.designations_path.write_text(
        text.replace("caption_confirmed_human", "parsed_pending_human", 1)
    )

    with pytest.raises(CropSetError):
        detection_pass(wired_set)

    mapping = wired_set.out_dir / "band_mapping_pending.csv"
    assert not mapping.exists(), "a run that refused must not leave a partial mapping table"
    assert not (wired_set.out_dir / "DETECTION_REPORT.md").exists()


def test_the_report_computes_no_ratio_no_n_and_no_statistic(wired_set: RunConfig) -> None:
    """The stop point is the deliverable: this pass must not pre-empt the mapping gate."""
    detection_pass(wired_set)
    report = (wired_set.out_dir / "DETECTION_REPORT.md").read_text()

    assert "computed no ratio, no N, and no agreement statistic" in report
    for forbidden in ("Spearman", "Bland", "r_s", "**N**", "N =", "verdict-eligible"):
        assert forbidden not in report
