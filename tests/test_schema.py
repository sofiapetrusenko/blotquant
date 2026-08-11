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

    Phase 0 defines the contract; no code produces results yet, so this fixture is
    the only executable statement of what a conforming result looks like.
    """
    return {
        "schema_version": "1.0.0",
        "result_id": "example",
        "source": {
            "path": "data/images/dev_00.jpg",
            "image_format": "jpeg",
            "bit_depth": 8,
            "width_px": 256,
            "height_px": 192,
            "lossy_format": True,
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
                "normalization": {"mode": "housekeeping_single", "exclude_qc_flagged": True},
            },
        },
        "lanes": [{"lane_id": "L0", "roi": {"x": 0, "y": 0, "width": 40, "height": 192}}],
        "bands": [
            {
                "band_id": "L0_target",
                "lane_id": "L0",
                "roi": {"x": 10, "y": 50, "width": 30, "height": 16},
                "integrated_intensity": 1234.5,
                "background_estimate": 20.0,
                "qc_flags": ["saturated"],
                "excluded_from_normalization": True,
                "exclusion_reason": "saturated",
            }
        ],
        "normalization": {
            "mode": "housekeeping_single",
            "exclude_qc_flagged": True,
            "warnings": ["single_housekeeping_reference"],
            "ratios": [
                {
                    "lane_id": "L0",
                    "numerator_band_id": "L0_target",
                    "denominator_band_id": "L0_housekeeping",
                    "ratio": 1.5,
                    "excluded": False,
                }
            ],
        },
        "image_qc_flags": ["lossy_format"],
    }


def test_schema_file_and_generator_constant_declare_the_same_version(repo_root: Path) -> None:
    """The schema file's own version and the constant the generator writes must match."""
    schema = json.loads((repo_root / GROUND_TRUTH_SCHEMA).read_text(encoding="utf-8"))
    assert schema["properties"]["schema_version"]["const"] == GROUND_TRUTH_SCHEMA_VERSION


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


def test_result_schema_requires_provenance(result_validator: Draft202012Validator) -> None:
    document = valid_result()
    del document["provenance"]
    errors = list(result_validator.iter_errors(document))
    assert any("'provenance' is a required property" in error.message for error in errors)
