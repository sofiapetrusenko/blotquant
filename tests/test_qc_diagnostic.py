"""Tests for the QC diagnostic (Phase 3b-1, read-only).

Why these exist. This diagnostic's conclusion — that the flags are measuring polarity rather
than clipping — is about to be the reason a phase changes direction. Two things must therefore
be true and are asserted here: the quotations really are extracted from the code rather than
transcribed (so they cannot go stale silently), and the pixel arithmetic behind the conclusion
is right in both directions, on images built with a known answer.

The polarity fixtures are synthetic and tiny. That is deliberate: an assertion against the real
crops would pass for whatever reason the real crops happen to have, which is the question.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tools.phase3.blot_identity import read_blot_identities
from tools.phase3.designations import read_designations
from tools.phase3.qc_diagnostic import (
    LIGHT_BACKGROUND_MEDIAN_FRACTION,
    _expected_bands_per_lane,
    diagnose,
    main,
    quoted_config,
    quoted_lines,
    quoted_source,
    report,
    ruled_lane_count,
)

CROP = "fake_0.png"


def _crop(path: Path, array: np.ndarray) -> None:
    Image.fromarray(array.astype(np.uint8), mode="L").save(path)


def _document(run_dir: Path, crop: str, bands: list[dict], flags: list[str]) -> None:
    stem = Path(crop).stem
    (run_dir / stem).mkdir(parents=True, exist_ok=True)
    (run_dir / stem / f"{stem}.json").write_text(
        json.dumps(
            {
                "source": {"path": f"crops/{crop}"},
                "lanes": [{"lane_id": "L0"}],
                "bands": bands,
                "image_qc_flags": flags,
                "provenance": {"parameters": {"qc": {"dynamic_range_min_peak_fraction": 0.25}}},
            }
        )
    )


def _band(band_id: str, roi: dict, clipped: int, peak: float, flags: list[str]) -> dict:
    return {
        "band_id": band_id,
        "lane_id": "L0",
        "roi": roi,
        "clipped_pixel_count": clipped,
        "peak_value": peak,
        "qc_flags": flags,
    }


def _tables(tmp_path: Path, targets: str = "T") -> tuple[Path, Path]:
    identity = tmp_path / "blot_identity.csv"
    with identity.open("w", newline="") as handle:
        handle.write("# ruled from the images: 12 lanes per panel each with its own MW\n")
        handle.write("# annotation, and nothing else.\n")
        writer = csv.writer(handle)
        writer.writerow(
            ["crop_filename", "parent_figure", "panel", "blot_id", "identity_source", "notes"]
        )
        writer.writerow([CROP, "fig", "A", "BLOT_0", "image_confirmed_human", ""])
    designations = tmp_path / "designations.csv"
    with designations.open("w", newline="") as handle:
        handle.write("# test\n")
        writer = csv.writer(handle)
        writer.writerow(
            ["crop_filename", "target_label", "reference_label", "source_of_designation", "notes"]
        )
        writer.writerow([CROP, targets, "R", "caption_confirmed_human", ""])
    return identity, designations


# --- the quotations are extracted, not transcribed ------------------------------------------


def test_the_rules_are_read_out_of_the_module_that_implements_them() -> None:
    """A quotation typed into the report could go stale; one read from the code cannot."""
    text = quoted_source("is_saturated", "_clipped_pixel_count")

    assert "def is_saturated(" in text
    assert "return clipped_pixel_count >= config.saturated_min_clipped_pixels" in text
    assert "np.count_nonzero(pixels[rows, columns] >= max_value)" in text


def test_quoting_a_renamed_function_fails_rather_than_printing_nothing() -> None:
    """A report quoting a rule it cannot find would describe a rule that is not in force."""
    with pytest.raises(AttributeError, match="has no 'is_saturated_v2'"):
        quoted_source("is_saturated_v2")


def test_the_image_flag_snippet_is_extracted_from_assess() -> None:
    """The derivation direction is the whole of §3's answer, so it is quoted, not asserted."""
    text = quoted_lines("assess", "image_flags: list[str] = []", 3)

    assert "if any(SATURATED in report.flags for report in reports):" in text
    assert "image_flags.append(SATURATED)" in text


def test_an_anchor_that_is_not_in_the_function_fails() -> None:
    """The snippet must come from the code; a missing anchor is a stale report, not a blank."""
    with pytest.raises(ValueError, match="is not in pipeline.qc.assess"):
        quoted_lines("assess", "if lossy_format_v2:", 2)


def test_the_config_quote_carries_the_key_and_its_own_comment() -> None:
    """The threshold and the reasoning that set it travel together."""
    text = quoted_config("saturated_min_clipped_pixels")

    assert text.rstrip().endswith("saturated_min_clipped_pixels: 1")
    assert "QC annotates rather than drops" in text


def test_an_absent_config_key_fails_loudly() -> None:
    """Describing a threshold that is not in the shipped config would be describing nothing."""
    with pytest.raises(KeyError, match="not in"):
        quoted_config("saturated_min_clipped_pixels_v2")


# --- the ruled lane count -------------------------------------------------------------------


def test_the_ruled_lane_count_is_read_from_the_identity_table(tmp_path: Path) -> None:
    """G2 is a human ruling; the diagnostic reads it rather than restating the number."""
    identity, _ = _tables(tmp_path)
    count, sentence = ruled_lane_count(identity)

    assert count == 12
    assert "annotation" in sentence, "the wrapped header line must be reassembled, not truncated"


def test_a_table_stating_no_lane_count_is_refused(tmp_path: Path) -> None:
    """The comparison needs the human's count; it is not invented when absent."""
    path = tmp_path / "no_count.csv"
    path.write_text("# nothing about lanes here\ncrop_filename\n")

    with pytest.raises(ValueError, match="does not state a per-panel lane count"):
        ruled_lane_count(path)


# --- the pixel arithmetic, in both directions ------------------------------------------------


def test_a_light_ground_image_is_read_as_dark_bands(tmp_path: Path) -> None:
    """The real corpus's case: white paper, a dark band, and full-scale pixels in the ROI."""
    crops = tmp_path / "crops"
    crops.mkdir()
    array = np.full((20, 20), 255, dtype=np.uint8)
    array[8:12, 8:12] = 30
    _crop(crops / CROP, array)
    run_dir = tmp_path / "run"
    _document(
        run_dir,
        CROP,
        [_band("L0_B0", {"x": 6, "y": 6, "width": 8, "height": 8}, 48, 10.0, ["saturated"])],
        ["saturated", "low_dynamic_range"],
    )
    identity, _ = _tables(tmp_path)

    (crop,) = diagnose(run_dir, crops, read_blot_identities(identity))

    assert crop.background_is_light is True
    assert crop.polarity == "bands darker than background"
    assert crop.at_zero == 0, "a dark band on this fixture does not reach 0"
    assert crop.bands_with_a_max_pixel == 1
    assert crop.saturated_roi_max_fraction == (48 / 64,)
    assert crop.clipped_counts_agree == 1


def test_a_dark_ground_image_is_not_read_as_dark_bands(tmp_path: Path) -> None:
    """The other direction: a gel-doc-style image must not trip the same verdict."""
    crops = tmp_path / "crops"
    crops.mkdir()
    array = np.full((20, 20), 5, dtype=np.uint8)
    array[8:12, 8:12] = 255
    _crop(crops / CROP, array)
    run_dir = tmp_path / "run"
    _document(
        run_dir,
        CROP,
        [_band("L0_B0", {"x": 8, "y": 8, "width": 4, "height": 4}, 16, 250.0, ["saturated"])],
        ["saturated"],
    )
    identity, _ = _tables(tmp_path)

    (crop,) = diagnose(run_dir, crops, read_blot_identities(identity))

    assert crop.background_is_light is False
    assert crop.polarity != "bands darker than background"
    assert crop.saturated_roi_max_fraction == (1.0,), "here the whole ROI really is clipped"


def test_a_recorded_clipped_count_that_disagrees_is_counted_as_a_disagreement(
    tmp_path: Path,
) -> None:
    """The agreement figure must be able to be less than the band count, or it says nothing."""
    crops = tmp_path / "crops"
    crops.mkdir()
    _crop(crops / CROP, np.full((20, 20), 255, dtype=np.uint8))
    run_dir = tmp_path / "run"
    _document(
        run_dir,
        CROP,
        [_band("L0_B0", {"x": 0, "y": 0, "width": 4, "height": 4}, 99, 1.0, ["saturated"])],
        ["saturated"],
    )
    identity, _ = _tables(tmp_path)

    (crop,) = diagnose(run_dir, crops, read_blot_identities(identity))

    assert crop.bands_with_a_max_pixel == 1
    assert crop.clipped_counts_agree == 0, "16 full-scale pixels were recorded as 99"


def test_the_threshold_comes_from_the_runs_own_parameter_echo(tmp_path: Path) -> None:
    """Comparing against a later threshold would re-decide the flag instead of explaining it."""
    crops = tmp_path / "crops"
    crops.mkdir()
    _crop(crops / CROP, np.full((20, 20), 200, dtype=np.uint8))
    run_dir = tmp_path / "run"
    stem = Path(CROP).stem
    band = _band("L0_B0", {"x": 0, "y": 0, "width": 4, "height": 4}, 0, 9.0, [])
    _document(run_dir, CROP, [band], [])
    path = run_dir / stem / f"{stem}.json"
    document = json.loads(path.read_text())
    document["provenance"]["parameters"]["qc"]["dynamic_range_min_peak_fraction"] = 0.10
    path.write_text(json.dumps(document))
    identity, _ = _tables(tmp_path)

    (crop,) = diagnose(run_dir, crops, read_blot_identities(identity))

    assert crop.low_dynamic_range_threshold == pytest.approx(0.10 * 255)


def test_the_light_background_threshold_is_the_midpoint() -> None:
    """Set at the midpoint rather than tuned; a value chosen to fit is the failure being hunted."""
    assert LIGHT_BACKGROUND_MEDIAN_FRACTION == 0.5


# --- what a lane should hold -----------------------------------------------------------------


def test_the_expected_band_count_is_read_from_the_ruled_designations(tmp_path: Path) -> None:
    """One band per target plus the shared reference: two targets allow three, not two."""
    _, one_target = _tables(tmp_path, targets="T")
    assert _expected_bands_per_lane(read_designations(one_target), CROP) == 2

    _, two_targets = _tables(tmp_path, targets="A|B")
    assert _expected_bands_per_lane(read_designations(two_targets), CROP) == 3


def test_a_no_ratio_row_expects_the_strip_itself(tmp_path: Path) -> None:
    """A reference strip carries no target, so what it allows per lane is one band."""
    path = tmp_path / "strip.csv"
    with path.open("w", newline="") as handle:
        handle.write("# test\n")
        writer = csv.writer(handle)
        writer.writerow(
            ["crop_filename", "target_label", "reference_label", "source_of_designation", "notes"]
        )
        writer.writerow([CROP, "", "R", "reference_strip_confirmed_human", ""])

    assert _expected_bands_per_lane(read_designations(path), CROP) == 1


# --- the report ------------------------------------------------------------------------------


def test_the_report_names_its_verdict_and_what_it_does_not_settle(tmp_path: Path) -> None:
    """A diagnostic that only reported the artefact would be over-read as a clean bill."""
    crops = tmp_path / "crops"
    crops.mkdir()
    array = np.full((20, 20), 255, dtype=np.uint8)
    array[8:12, 8:12] = 30
    _crop(crops / CROP, array)
    run_dir = tmp_path / "run"
    _document(
        run_dir,
        CROP,
        [_band("L0_B0", {"x": 6, "y": 6, "width": 8, "height": 8}, 48, 10.0, ["saturated"])],
        ["saturated"],
    )
    identity, designations = _tables(tmp_path)

    text = "\n".join(
        report(
            diagnose(run_dir, crops, read_blot_identities(identity)),
            identity,
            run_dir,
            read_designations(designations),
        )
    )

    assert "Measurement artefact, not a corpus property" in text
    assert "What is *not* settled by this" in text
    assert "nothing here is a ruling" in text


def test_an_empty_run_directory_is_refused(tmp_path: Path) -> None:
    """Zero saturation from an empty run would read as a finding about the corpus."""
    identity, designations = _tables(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(ValueError, match="no result documents"):
        main(
            [
                "--run-dir",
                str(empty),
                "--crops-dir",
                str(tmp_path),
                "--blot-identity",
                str(identity),
                "--designations",
                str(designations),
            ]
        )
