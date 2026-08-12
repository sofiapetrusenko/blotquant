"""The result document, the CLI, determinism, and the anti-circularity invariant."""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from jsonschema import Draft202012Validator

from pipeline import PIPELINE_VERSION, RESULT_SCHEMA_VERSION
from pipeline.__main__ import main
from pipeline.analyze import (
    PHASE2_CONDITIONAL_BAND_FIELDS,
    PHASE2_RESULT_FIELDS,
    analyze_image,
    require_writable_destination,
    write_result,
)
from pipeline.config import load_config
from tests.conftest import Blot
from tests.test_pipeline_config import CONFIG_DIR

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schema" / "result.schema.json"
PIPELINE_DIR = REPO_ROOT / "pipeline"


@pytest.fixture
def blot_png(tmp_path: Path, three_lane_blot: Blot) -> Path:
    """Write the three-lane fixture as a 16-bit PNG and return its path."""
    path = tmp_path / "fixture_blot.png"
    assert cv2.imwrite(str(path), three_lane_blot.pixels)
    return path


@pytest.fixture
def result(blot_png: Path) -> dict[str, Any]:
    """Return the result of analysing the fixture with the shipped default config."""
    return analyze_image(blot_png, load_config(CONFIG_DIR / "default.yaml"))


def _schema() -> dict[str, Any]:
    """Return the result schema as written."""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _phase1_schema() -> dict[str, Any]:
    """Return the result schema with exactly the Phase 2 requirements relaxed.

    ``list.remove`` raises if a name is absent, so this fails loudly if the schema
    stops requiring something :data:`PHASE2_RESULT_FIELDS` claims it requires.
    """
    schema = copy.deepcopy(_schema())
    for name in PHASE2_RESULT_FIELDS["<root>"]:
        schema["required"].remove(name)
    band = schema["properties"]["bands"]["items"]
    for name in PHASE2_RESULT_FIELDS["bands[]"]:
        band["required"].remove(name)
    # With excluded_from_normalization absent, the schema's conditional fires and asks
    # for exclusion_reason; see PHASE2_CONDITIONAL_BAND_FIELDS.
    assert "exclusion_reason" in PHASE2_CONDITIONAL_BAND_FIELDS
    del band["allOf"]
    parameters = schema["properties"]["provenance"]["properties"]["parameters"]
    for name in PHASE2_RESULT_FIELDS["provenance.parameters"]:
        parameters["required"].remove(name)
    return schema


def test_result_validates_against_the_schema_minus_phase_2(result: dict[str, Any]) -> None:
    """Every field Phase 1 emits has the name, type and shape the schema defines."""
    errors = list(Draft202012Validator(_phase1_schema()).iter_errors(result))

    assert errors == [], [f"{list(error.path)}: {error.message}" for error in errors]


def test_the_only_gap_to_full_validity_is_the_phase_2_fields(result: dict[str, Any]) -> None:
    """The document fails the full schema for exactly the declared reasons and no others.

    This is the deliberate part of the Phase 1 decision: rather than emitting empty
    ``normalization``/``qc_flags`` structures that would read as "ran and found
    nothing", the fields are absent, and this test states precisely which ones.
    """
    missing: set[str] = set()
    for error in Draft202012Validator(_schema()).iter_errors(result):
        assert error.validator == "required", f"unexpected error: {error.message}"
        assert isinstance(error.instance, dict)
        missing.update(
            name for name in error.validator_value if name not in error.instance
        )

    expected = {name for names in PHASE2_RESULT_FIELDS.values() for name in names}
    expected.update(PHASE2_CONDITIONAL_BAND_FIELDS)
    assert missing == expected


def test_provenance_records_version_config_and_every_parameter(
    result: dict[str, Any],
) -> None:
    """Provenance carries the software version, the config identity and its full echo."""
    provenance = result["provenance"]
    config = load_config(CONFIG_DIR / "default.yaml")

    assert provenance["software_version"] == PIPELINE_VERSION
    assert provenance["config_id"] == "default"
    assert provenance["config_digest"] == config.digest()
    assert provenance["parameters"] == config.as_parameters()
    assert result["schema_version"] == RESULT_SCHEMA_VERSION


def test_source_describes_the_file_as_loaded(result: dict[str, Any], blot_png: Path) -> None:
    """The source block reports the container and depth that were actually read."""
    source = result["source"]

    assert source["path"] == str(blot_png)
    assert source["image_format"] == "png"
    assert source["bit_depth"] == 16
    assert source["max_value"] == 65535
    assert source["lossy_format"] is False
    assert (source["width_px"], source["height_px"]) == (200, 120)
    assert "ground_truth_image_id" not in source


def test_ground_truth_id_is_recorded_only_when_the_caller_supplies_it(
    blot_png: Path,
) -> None:
    """The join key comes from the caller; nothing infers it from the file name."""
    result = analyze_image(
        blot_png, load_config(CONFIG_DIR / "default.yaml"), ground_truth_image_id="dev_99"
    )

    assert result["source"]["ground_truth_image_id"] == "dev_99"


def test_bands_and_lanes_are_measured(result: dict[str, Any]) -> None:
    """The fixture's three lanes and six bands come back with positive intensities."""
    assert len(result["lanes"]) == 3
    assert len(result["bands"]) == 6
    for band in result["bands"]:
        assert band["integrated_intensity"] > 0.0
        assert band["background_estimate"] == pytest.approx(1000.0, abs=25.0)
        assert band["lane_id"] in {lane["lane_id"] for lane in result["lanes"]}


@pytest.mark.parametrize("config_name", ["default.yaml", "rolling_ball.yaml"])
def test_the_same_image_and_config_produce_identical_results(
    blot_png: Path, config_name: str
) -> None:
    """Determinism: two runs differ only in the timestamp, for either background method."""
    config = load_config(CONFIG_DIR / config_name)

    first = analyze_image(blot_png, config)
    second = analyze_image(blot_png, config)

    del first["provenance"]["created_at"]
    del second["provenance"]["created_at"]
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


@pytest.mark.parametrize("config_name", ["default.yaml", "rolling_ball.yaml"])
def test_both_shipped_configs_produce_a_schema_subset_result(
    blot_png: Path, config_name: str
) -> None:
    """Neither background method can emit a field the Phase 1 schema subset forbids."""
    result = analyze_image(blot_png, load_config(CONFIG_DIR / config_name))

    errors = list(Draft202012Validator(_phase1_schema()).iter_errors(result))
    assert errors == [], [f"{list(error.path)}: {error.message}" for error in errors]
    assert result["provenance"]["parameters"]["background"]["method"] in {
        "local_median",
        "rolling_ball",
    }


def test_the_result_id_is_content_addressed(blot_png: Path, tmp_path: Path) -> None:
    """The id depends on the image bytes and the config, not on the path or the clock."""
    config = load_config(CONFIG_DIR / "default.yaml")
    copied = tmp_path / "renamed.png"
    copied.write_bytes(blot_png.read_bytes())

    assert analyze_image(blot_png, config)["result_id"] == analyze_image(copied, config)[
        "result_id"
    ]
    other = load_config(CONFIG_DIR / "rolling_ball.yaml")
    assert analyze_image(blot_png, other)["result_id"] != analyze_image(blot_png, config)[
        "result_id"
    ]


def test_cli_writes_a_result_and_reports_success(
    blot_png: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``python -m pipeline run`` writes ``<stem>.json`` and exits 0."""
    out = tmp_path / "results"

    code = main(
        ["run", str(blot_png), "--config", str(CONFIG_DIR / "default.yaml"), "--out", str(out)]
    )

    assert code == 0
    written = out / "fixture_blot.json"
    assert written.is_file()
    document = json.loads(written.read_text(encoding="utf-8"))
    assert len(document["bands"]) == 6
    assert "wrote" in capsys.readouterr().out


def test_cli_reports_a_missing_image(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A missing input exits 1 with a message on stderr, not a traceback."""
    code = main(
        [
            "run",
            str(tmp_path / "absent.png"),
            "--config",
            str(CONFIG_DIR / "default.yaml"),
            "--out",
            str(tmp_path / "out"),
        ]
    )

    assert code == 1
    assert "error:" in capsys.readouterr().err


def test_cli_reports_a_bad_config(
    blot_png: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A config that is complete except for the background method exits 1 and says so."""
    import yaml

    mapping = yaml.safe_load((CONFIG_DIR / "default.yaml").read_text(encoding="utf-8"))
    del mapping["background"]["method"]
    config = tmp_path / "no_method.yaml"
    config.write_text(yaml.safe_dump(mapping), encoding="utf-8")

    code = main(["run", str(blot_png), "--config", str(config), "--out", str(tmp_path / "out")])

    assert code == 1
    assert "background.method is required" in capsys.readouterr().err


def test_cli_reports_an_unsupported_image(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A float TIFF exits 1 rather than being squashed to an integer type."""
    import tifffile

    path = tmp_path / "float.tiff"
    tifffile.imwrite(str(path), np.zeros((60, 60), dtype=np.float32))

    code = main(
        ["run", str(path), "--config", str(CONFIG_DIR / "default.yaml"), "--out", str(tmp_path)]
    )

    assert code == 1
    assert "float32" in capsys.readouterr().err


def _pipeline_sources() -> list[Path]:
    """Return every Python source file in the pipeline package."""
    sources = sorted(PIPELINE_DIR.glob("*.py"))
    assert sources, f"no pipeline sources found under {PIPELINE_DIR}"
    return sources


def test_pipeline_never_imports_the_generator_or_the_evals() -> None:
    """Anti-circularity (PLAN.md): pipeline/ imports neither synth/ nor evals/."""
    forbidden = {"synth", "evals"}
    for source in _pipeline_sources():
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert name.split(".")[0] not in forbidden, f"{source.name} imports {name}"


def _non_docstring_strings(tree: ast.AST) -> list[str]:
    """Return every string literal in ``tree`` that is not documentation.

    A string used as a bare statement is documentation: that covers module, class and
    function docstrings and the attribute docstrings this codebase writes under module
    constants. Everything else is a value the code actually uses.
    """
    documentation = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in documentation
    ]


def test_pipeline_never_names_the_gold_set() -> None:
    """No pipeline *code* names the ground truth, the gold-set layout or its artifacts.

    Docstrings are excluded on purpose: several of them say that these must not be
    read, which is the opposite of a violation.
    """
    needles = (
        "ground_truth/",
        "data/images",
        "pixel_sha256",
        "generation_config",
        "dev_",
        "test_",
    )
    for source in _pipeline_sources():
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for literal in _non_docstring_strings(tree):
            for needle in needles:
                assert needle not in literal, f"{source.name} has a literal naming {needle}"


def test_pipeline_runs_on_a_committed_gold_set_image(
    committed_data_dir: Path, tmp_path: Path
) -> None:
    """End to end on a real gold-set file, through the CLI, with no manual intervention."""
    images = sorted(committed_data_dir.glob("images/dev_*"))
    assert images, "the committed gold set has no dev images"

    code = main(
        [
            "run",
            str(images[0]),
            "--config",
            str(CONFIG_DIR / "default.yaml"),
            "--out",
            str(tmp_path / "results"),
        ]
    )

    assert code == 0
    document = json.loads((tmp_path / "results" / f"{images[0].stem}.json").read_text())
    assert len(document["lanes"]) >= 1
    assert len(document["bands"]) >= 1


def _refused(out_dir: Path) -> bool:
    """Return True if the gold-set write guard refuses ``out_dir``."""
    from pipeline.errors import PipelineError

    try:
        require_writable_destination(out_dir)
    except PipelineError:
        return True
    return False


@pytest.mark.parametrize(
    "relative",
    [
        "ground_truth",
        "data/ground_truth",
        "data/ground_truth/nested/deeper",
        "GROUND_TRUTH",
        "data/GROUND_TRUTH",
        "data/Ground_Truth",
        "data/gRoUnD_tRuTh/nested",
    ],
)
def test_the_gold_set_is_refused_whatever_the_letter_case(tmp_path: Path, relative: str) -> None:
    """Case-insensitive filesystems make ``GROUND_TRUTH`` the same directory as the real one.

    This is the whole point of the guard: on macOS and Windows an exact-case component
    match is not a boundary at all.
    """
    assert _refused(tmp_path / relative)


@pytest.mark.parametrize(
    "relative",
    ["results", "ground_truth2", "my_ground_truth_notes", "truth", "data/images", "ground/truth"],
)
def test_ordinary_directories_stay_writable(tmp_path: Path, relative: str) -> None:
    """Only a whole path component equal to the reserved name is refused."""
    assert not _refused(tmp_path / relative)


def test_a_relative_path_climbing_into_the_gold_set_is_refused(tmp_path: Path) -> None:
    """The check resolves ``..`` rather than matching the path as text."""
    (tmp_path / "ground_truth").mkdir()
    (tmp_path / "results").mkdir()

    assert _refused(tmp_path / "results" / ".." / "ground_truth")


def test_a_symlink_into_the_gold_set_is_refused(tmp_path: Path) -> None:
    """A symlink is followed, so it cannot be used to smuggle a write into the gold set."""
    target = tmp_path / "data" / "ground_truth"
    target.mkdir(parents=True)
    link = tmp_path / "innocent_looking"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as error:  # pragma: no cover - platform dependent
        pytest.skip(f"symlinks unavailable here: {error}")

    assert _refused(link)
    assert _refused(link / "nested")


def test_write_result_refuses_the_gold_set_and_writes_nothing(
    result: dict[str, Any], tmp_path: Path
) -> None:
    """The refusal happens before any directory is created or any file written."""
    from pipeline.errors import PipelineError

    destination = tmp_path / "data" / "GROUND_TRUTH"

    with pytest.raises(PipelineError, match="written only by 'python -m synth'"):
        write_result(result, destination, "dev_00")

    assert not destination.exists()


def test_the_cli_refuses_the_gold_set(
    blot_png: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """End to end: ``--out`` pointing into the gold set exits 1 with a message."""
    code = main(
        [
            "run",
            str(blot_png),
            "--config",
            str(CONFIG_DIR / "default.yaml"),
            "--out",
            str(tmp_path / "ground_truth"),
        ]
    )

    assert code == 1
    assert "python -m synth" in capsys.readouterr().err
    assert not (tmp_path / "ground_truth").exists()


def test_the_committed_gold_set_is_refused(committed_data_dir: Path) -> None:
    """The real path this guard exists for, in both letter cases."""
    assert _refused(committed_data_dir / "ground_truth")
    assert _refused(committed_data_dir / "GROUND_TRUTH")
