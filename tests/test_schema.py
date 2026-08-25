"""Schema tests.

Two things are checked: that the committed ground truth validates (the same check CI
runs), and that the schemas actually reject malformed documents -- a schema that
accepts everything would pass CI while guaranteeing nothing.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from pipeline import RESULT_SCHEMA_VERSION
from pipeline.detect import ROI_SOURCES
from pipeline.load import (
    BRIGHT_ON_DARK,
    CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN,
    CHANNEL_COLLAPSE_METHOD,
    POLARITIES,
)
from synth import GROUND_TRUTH_SCHEMA_VERSION

GROUND_TRUTH_SCHEMA = Path("schema/ground_truth.schema.json")
RESULT_SCHEMA = Path("schema/result.schema.json")


@pytest.fixture(scope="session")
def ground_truth_validator(repo_root: Path) -> Draft202012Validator:
    """Return a Draft 2020-12 validator for the ground-truth schema."""
    schema = json.loads((repo_root / GROUND_TRUTH_SCHEMA).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.fixture(scope="session")
def result_validator(repo_root: Path) -> Draft202012Validator:
    """Return a Draft 2020-12 validator for the result schema."""
    schema = json.loads((repo_root / RESULT_SCHEMA).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.fixture(scope="session")
def sample_ground_truth(committed_data_dir: Path) -> dict[str, Any]:
    """Return one committed ground-truth document."""
    return json.loads((committed_data_dir / "ground_truth" / "dev_00.json").read_text("utf-8"))


def valid_result() -> dict[str, Any]:
    """Return a minimal result document that satisfies ``result.schema.json``.

    Hand-written rather than produced by the pipeline, so that the contract is stated
    independently of the code that writes it; ``tests/test_pipeline_result.py`` checks the
    produced document against the same schema.
    """
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "result_id": "example",
        "source": {
            "path": "data/images/dev_00.jpg",
            "image_format": "jpeg",
            "bit_depth": 8,
            "width_px": 256,
            "height_px": 192,
            "lossy_format": True,
            "polarity": BRIGHT_ON_DARK,
        },
        "provenance": {
            "software_version": "0.1.0",
            "config_id": "default",
            "config_digest": "sha256:" + "0" * 64,
            "created_at": "2026-01-01T00:00:00Z",
            "parameters": {
                "background": {"method": "rolling_ball", "radius_px": 50},
                "detection": {"method": "profile_projection"},
                "quantification": {"method": "roi_sum"},
                "qc": {
                    "saturated_min_clipped_pixels": 1,
                    "overlap_iou_threshold": 0.05,
                    "shoulder_half_maximum_fraction": 0.5,
                    "shoulder_half_width_ratio": 1.5,
                    "dynamic_range_min_peak_fraction": 0.25,
                },
                "normalization": {"mode": "housekeeping_single", "exclude_qc_flagged": True},
            },
        },
        "lanes": [
            {
                "lane_id": "L0",
                "roi": {"x": 0, "y": 0, "width": 40, "height": 192},
                "roi_source": "detected",
            }
        ],
        "bands": [
            {
                "band_id": "L0_target",
                "lane_id": "L0",
                "roi": {"x": 10, "y": 50, "width": 30, "height": 16},
                "integrated_intensity": 1234.5,
                "background_estimate": 20.0,
                "peak_value": 210.0,
                "clipped_pixel_count": 4,
                "qc_flags": ["saturated", "unresolved_shoulder"],
                "excluded_from_normalization": True,
                "exclusion_reason": "carries QC flags: saturated, unresolved_shoulder",
            }
        ],
        "normalization": {
            "mode": "housekeeping_single",
            "exclude_qc_flagged": True,
            "reference_band_ids": ["L0_housekeeping"],
            "warnings": ["single_housekeeping_reference"],
            "ratios": [
                {
                    "lane_id": "L0",
                    "numerator_band_id": "L0_target",
                    "denominator_band_id": "L0_housekeeping",
                    "denominator_band_ids": ["L0_housekeeping"],
                    "ratio": 1.5,
                    "excluded": False,
                    "qc_flags": ["saturated"],
                    "reference_qc_flagged": False,
                    "reference_qc_flags": [],
                }
            ],
        },
        "image_qc_flags": ["lossy_format"],
    }


def test_schema_file_and_generator_constant_declare_the_same_version(repo_root: Path) -> None:
    """The schema file's own version and the constant the generator writes must match."""
    schema = json.loads((repo_root / GROUND_TRUTH_SCHEMA).read_text(encoding="utf-8"))
    assert schema["properties"]["schema_version"]["const"] == GROUND_TRUTH_SCHEMA_VERSION


def test_result_schema_file_and_pipeline_constant_declare_the_same_version(
    repo_root: Path,
) -> None:
    """The result schema's version and the constant the pipeline writes must match.

    Mirrors the ground-truth check above. Before Phase 2 the result schema had a pattern and
    no ``const``, so the two could drift -- and did: the pipeline declared 1.0.0 on documents
    that failed 1.0.0. A ``const`` makes that a schema error rather than a convention.
    """
    schema = json.loads((repo_root / RESULT_SCHEMA).read_text(encoding="utf-8"))
    assert schema["properties"]["schema_version"]["const"] == RESULT_SCHEMA_VERSION


def test_result_schema_and_detection_declare_the_same_roi_source_vocabulary(
    repo_root: Path,
) -> None:
    """The lane-provenance vocabulary is written twice and must stay one vocabulary.

    :data:`pipeline.detect.ROI_SOURCES` says it is "mirrored by the result schema's enum", and
    the two are enforced independently -- the tuple by ``DetectedLane.__post_init__`` in
    process, the enum by the schema on the document. A third value added to one and not the
    other would be accepted by the writer and refused by the contract, or the reverse. Same
    spirit as the version check above, and order matters: the enum is the vocabulary as
    written, not a set.
    """
    schema = json.loads((repo_root / RESULT_SCHEMA).read_text(encoding="utf-8"))
    enum = schema["properties"]["lanes"]["items"]["properties"]["roi_source"]["enum"]

    assert tuple(enum) == ROI_SOURCES


def test_result_schema_and_loader_declare_the_same_collapse_rule(repo_root: Path) -> None:
    """The ruled method and the ruled bound are written twice and must stay one rule.

    ``schema/result.schema.json`` restates ``pipeline.load.CHANNEL_COLLAPSE_METHOD`` as a
    ``const`` and ``pipeline.load.CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN`` as a ``maximum``. Both
    come from the same ratified amendment, but nothing in the files ties them together, so an
    edit to either constant would leave the contract declaring one bound while the loader
    enforces another -- the writer and the contract disagreeing about what the ruling says.
    Same spirit as the ``roi_source`` and ``schema_version`` pins above.
    """
    schema = json.loads((repo_root / RESULT_SCHEMA).read_text(encoding="utf-8"))
    collapse = schema["properties"]["source"]["properties"]["channel_collapse"]

    assert collapse["properties"]["method"]["const"] == CHANNEL_COLLAPSE_METHOD
    assert collapse["properties"]["max_divergence_dn"]["maximum"] == (
        CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN
    )


def test_result_schema_rejects_a_collapse_method_the_amendment_does_not_permit(
    result_validator: Draft202012Validator,
) -> None:
    """The ``const`` is the point of the field: a plane the ruling did not name is not a variant."""
    document = valid_result()
    document["source"]["channel_collapse"] = {"method": "red", "max_divergence_dn": 0}

    assert list(result_validator.iter_errors(document))


def test_result_schema_rejects_a_collapse_above_the_ruled_bound(
    result_validator: Draft202012Validator,
) -> None:
    """A document claiming a collapse above the bound is not one this pipeline can have written."""
    document = valid_result()
    over = CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN + 1
    document["source"]["channel_collapse"] = {"method": "green", "max_divergence_dn": over}

    assert list(result_validator.iter_errors(document))


def test_result_schema_accepts_a_collapse_at_the_bound(
    result_validator: Draft202012Validator,
) -> None:
    """The admitting side, so the two rejections above are not passing for the wrong reason."""
    document = valid_result()
    document["source"]["channel_collapse"] = {
        "method": CHANNEL_COLLAPSE_METHOD,
        "max_divergence_dn": CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN,
    }

    assert not list(result_validator.iter_errors(document))


def test_result_schema_rejects_another_schema_version(
    result_validator: Draft202012Validator,
) -> None:
    """A document declaring a different contract version is not this contract's document."""
    document = valid_result()
    document["schema_version"] = "1.0.0"

    errors = list(result_validator.iter_errors(document))

    assert any("was expected" in error.message for error in errors)


def test_result_schema_rejects_an_unknown_band_qc_flag(
    result_validator: Draft202012Validator,
) -> None:
    """The band flag vocabulary is closed: a flag no consumer can interpret is rejected."""
    document = valid_result()
    document["bands"][0]["qc_flags"] = ["probably_fine"]

    errors = list(result_validator.iter_errors(document))

    assert any("is not one of" in error.message for error in errors)


def test_result_schema_rejects_an_unknown_normalization_warning(
    result_validator: Draft202012Validator,
) -> None:
    """So is the warning vocabulary: a warning is a code a consumer can act on."""
    document = valid_result()
    document["normalization"]["warnings"] = ["looks_a_bit_off"]

    errors = list(result_validator.iter_errors(document))

    assert any("is not one of" in error.message for error in errors)


def test_result_schema_requires_a_reason_when_a_ratio_is_excluded(
    result_validator: Draft202012Validator,
) -> None:
    """An excluded ratio without a recorded reason is a silent drop wearing a flag."""
    document = valid_result()
    document["normalization"]["ratios"][0]["excluded"] = True

    errors = list(result_validator.iter_errors(document))

    assert any("exclusion_reason" in error.message for error in errors)


def test_every_committed_ground_truth_file_validates(
    committed_data_dir: Path, ground_truth_validator: Draft202012Validator
) -> None:
    files = sorted((committed_data_dir / "ground_truth").glob("*.json"))
    assert files, "no committed ground truth to validate"
    for path in files:
        errors = list(ground_truth_validator.iter_errors(json.loads(path.read_text("utf-8"))))
        assert not errors, f"{path.name}: " + "; ".join(
            f"{'/'.join(map(str, error.path)) or '<root>'}: {error.message}" for error in errors
        )


@pytest.mark.parametrize(
    ("mutation", "expected_message"),
    [
        ("drop_bands", "'bands' is a required property"),
        ("unknown_top_level_key", "Additional properties are not allowed"),
        ("bad_split", "is not one of"),
        ("bad_bit_depth", "is not one of"),
        ("unknown_qc_flag", "is not one of"),
        ("negative_roi", "is less than the minimum"),
        ("bad_digest", "does not match"),
        ("bad_schema_version", "was expected"),
    ],
)
def test_ground_truth_schema_rejects_malformed_documents(
    sample_ground_truth: dict[str, Any],
    ground_truth_validator: Draft202012Validator,
    mutation: str,
    expected_message: str,
) -> None:
    document = copy.deepcopy(sample_ground_truth)
    if mutation == "drop_bands":
        del document["bands"]
    elif mutation == "unknown_top_level_key":
        document["surprise"] = 1
    elif mutation == "bad_split":
        document["split"] = "validation"
    elif mutation == "bad_bit_depth":
        document["bit_depth"] = 12
    elif mutation == "unknown_qc_flag":
        document["bands"][0]["qc_flags"] = ["probably_fine"]
    elif mutation == "negative_roi":
        document["bands"][0]["roi"]["x"] = -1
    elif mutation == "bad_digest":
        document["pixel_sha256"] = "not-a-digest"
    elif mutation == "bad_schema_version":
        document["schema_version"] = "2.0.0"
    errors = list(ground_truth_validator.iter_errors(document))
    assert errors, f"schema accepted a document mutated by {mutation}"
    assert any(expected_message in error.message for error in errors), [
        error.message for error in errors
    ]


def test_result_schema_accepts_a_conforming_result(
    result_validator: Draft202012Validator,
) -> None:
    assert list(result_validator.iter_errors(valid_result())) == []


def test_result_schema_requires_an_exclusion_reason_when_a_band_is_excluded(
    result_validator: Draft202012Validator,
) -> None:
    document = valid_result()
    del document["bands"][0]["exclusion_reason"]
    errors = list(result_validator.iter_errors(document))
    assert any("exclusion_reason" in error.message for error in errors)


def test_result_schema_requires_an_explicit_background_method(
    result_validator: Draft202012Validator,
) -> None:
    document = valid_result()
    del document["provenance"]["parameters"]["background"]["method"]
    errors = list(result_validator.iter_errors(document))
    assert any("method" in error.message for error in errors)


def test_result_schema_rejects_an_unknown_normalization_mode(
    result_validator: Draft202012Validator,
) -> None:
    document = valid_result()
    document["normalization"]["mode"] = "vibes"
    errors = list(result_validator.iter_errors(document))
    assert any("is not one of" in error.message for error in errors)


def test_result_schema_requires_every_lane_to_say_where_its_roi_came_from(
    result_validator: Draft202012Validator,
) -> None:
    """A lane without ``roi_source`` cannot be told from one a writer never recorded it on."""
    document = valid_result()
    del document["lanes"][0]["roi_source"]

    errors = list(result_validator.iter_errors(document))

    assert any("'roi_source' is a required property" in error.message for error in errors)


def test_result_schema_rejects_an_invented_lane_roi_source(
    result_validator: Draft202012Validator,
) -> None:
    """The provenance vocabulary is closed: a lane is detected or supplied, nothing else."""
    document = valid_result()
    document["lanes"][0]["roi_source"] = "probably_detected"

    errors = list(result_validator.iter_errors(document))

    assert any("is not one of" in error.message for error in errors)


def test_result_schema_requires_a_declared_polarity(
    result_validator: Draft202012Validator,
) -> None:
    """1.4.0 makes it required, and the requirement is the contract, not the docstring.

    An absent ``channel_collapse`` means the file was single-channel, which is information. An
    absent polarity would mean nobody said -- and a document measured without a declared polarity
    is what the 2026-08-24 amendment exists to prevent being produced.
    """
    document = valid_result()
    del document["source"]["polarity"]

    errors = list(result_validator.iter_errors(document))

    assert any("polarity" in error.message and "required" in error.message for error in errors)


def test_result_schema_rejects_a_polarity_the_amendment_does_not_define(
    result_validator: Draft202012Validator,
) -> None:
    """An enum, so a third convention fails validation rather than reading as a variant.

    ``auto`` is the value a caller would most plausibly invent, and it is the one the amendment
    refuses by name: it would be an auto-detection heuristic wearing a vocabulary word.
    """
    document = valid_result()
    document["source"]["polarity"] = "auto"

    errors = list(result_validator.iter_errors(document))

    assert any("is not one of" in error.message for error in errors)


def test_the_schema_polarity_enum_is_the_loaders_vocabulary(repo_root: Path) -> None:
    """Written twice and must stay one vocabulary, like ``roi_source`` and ``schema_version``."""
    schema = json.loads((repo_root / RESULT_SCHEMA).read_text(encoding="utf-8"))
    enum = schema["properties"]["source"]["properties"]["polarity"]["enum"]

    assert tuple(enum) == POLARITIES


def test_result_schema_requires_provenance(result_validator: Draft202012Validator) -> None:
    document = valid_result()
    del document["provenance"]
    errors = list(result_validator.iter_errors(document))
    assert any("'provenance' is a required property" in error.message for error in errors)
