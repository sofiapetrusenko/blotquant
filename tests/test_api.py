"""The HTTP API: the envelope, the error mapping, and the two-pass housekeeping flow.

The app is constructed per test with its storage root and its config directory pointed at
``tmp_path``, which is the reason both are constructor arguments: nothing here can reach a
path baked into an import, and no test can write where a deployment would.
"""

from __future__ import annotations

import ast
import base64
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
import tifffile
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from api import app as app_module
from api.__main__ import build_parser
from api.__main__ import main as api_main
from api.app import create_app
from api.errors import status_for
from pipeline import RESULT_SCHEMA_VERSION
from pipeline.errors import NormalizationError, PipelineError, ReferenceBandError
from tests.conftest import Blot
from tests.test_pipeline_config import CONFIG_DIR

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schema" / "result.schema.json"

PNG_MEDIA_TYPE = "image/png"


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Return a config directory holding the shipped default plus a housekeeping variant.

    The housekeeping config is a copy of the shipped one with a single key replaced, so the
    two-pass test exercises the real parameter set rather than a hand-written stub.
    """
    directory = tmp_path / "configs"
    directory.mkdir()
    mapping = yaml.safe_load((CONFIG_DIR / "default.yaml").read_text(encoding="utf-8"))
    (directory / "default.yaml").write_text(yaml.safe_dump(mapping), encoding="utf-8")
    mapping["id"] = "housekeeping"
    mapping["normalization"]["mode"] = "housekeeping_single"
    (directory / "housekeeping.yaml").write_text(yaml.safe_dump(mapping), encoding="utf-8")
    return directory


@pytest.fixture
def client(tmp_path: Path, config_dir: Path) -> Iterator[TestClient]:
    """Return a client for an app storing results under ``tmp_path``."""
    app = create_app(storage_root=tmp_path / "results", config_dir=config_dir)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def blot_bytes(tmp_path: Path, three_lane_blot: Blot) -> bytes:
    """Return the three-lane fixture encoded as a 16-bit PNG (200x120 px, 3 lanes)."""
    path = tmp_path / "fixture_blot.png"
    assert cv2.imwrite(str(path), three_lane_blot.pixels)
    return path.read_bytes()


def _upload(payload: bytes, name: str = "fixture_blot.png") -> dict[str, Any]:
    """Return the ``files`` mapping for a multipart upload of ``payload``."""
    return {"image": (name, payload, PNG_MEDIA_TYPE)}


def _schema_errors(document: dict[str, Any]) -> list[str]:
    """Return the result schema's complaints, as written and nothing relaxed."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return [
        f"{list(error.path)}: {error.message}"
        for error in Draft202012Validator(schema).iter_errors(document)
    ]


def test_analyze_returns_a_schema_valid_document_in_an_envelope(
    client: TestClient, blot_bytes: bytes
) -> None:
    """The envelope carries the document the pipeline wrote, unmodified and valid."""
    response = client.post("/analyze", files=_upload(blot_bytes), data={"config": "default"})

    assert response.status_code == 200
    envelope = response.json()
    assert set(envelope) == {"result", "display"}
    assert _schema_errors(envelope["result"]) == []
    assert len(envelope["result"]["lanes"]) == 3
    assert [lane["roi_source"] for lane in envelope["result"]["lanes"]] == ["detected"] * 3


def test_analyze_returns_a_display_derivative_marked_as_one(
    client: TestClient, blot_bytes: bytes
) -> None:
    """The PNG is labelled a derivative, records its mapping, and decodes to the right size."""
    response = client.post("/analyze", files=_upload(blot_bytes), data={"config": "default"})

    display = response.json()["display"]
    assert display["is_derivative"] is True
    assert display["media_type"] == PNG_MEDIA_TYPE
    assert "not the measured data" in display["note"]
    assert display["mapping"]["name"] == "linear_full_scale"
    assert display["mapping"]["source_max_value"] == 65535
    assert display["mapping"]["output_max_value"] == 255
    assert display["mapping"]["clips"] is False
    assert display["mapping"]["scales"] is True
    decoded = cv2.imdecode(
        np.frombuffer(base64.b64decode(display["png_base64"]), dtype=np.uint8),
        cv2.IMREAD_UNCHANGED,
    )
    assert decoded.dtype == np.uint8
    assert decoded.shape == (120, 200)


def test_supplied_lane_rois_become_the_lanes_and_are_recorded_as_the_callers(
    client: TestClient, blot_bytes: bytes
) -> None:
    """Two rectangles are supplied for an image detection reads as three lanes."""
    response = client.post(
        "/analyze",
        files=_upload(blot_bytes),
        data={"config": "default", "lane_roi": ["5,0,70,120", "120,0,70,120"]},
    )

    assert response.status_code == 200
    lanes = response.json()["result"]["lanes"]
    assert [lane["roi"]["x"] for lane in lanes] == [5, 120]
    assert [lane["roi_source"] for lane in lanes] == ["caller", "caller"]


def test_the_same_image_with_different_lane_rois_gets_a_different_id(
    client: TestClient, blot_bytes: bytes
) -> None:
    """Otherwise ``GET /results/{id}`` would serve one analysis for another."""
    detected = client.post("/analyze", files=_upload(blot_bytes), data={"config": "default"})
    supplied = client.post(
        "/analyze",
        files=_upload(blot_bytes),
        data={"config": "default", "lane_roi": ["5,0,70,120", "120,0,70,120"]},
    )

    assert detected.json()["result"]["result_id"] != supplied.json()["result"]["result_id"]


def test_a_stored_result_comes_back_in_the_same_envelope(
    client: TestClient, blot_bytes: bytes
) -> None:
    """``GET`` returns what ``POST`` returned, including the display derivative."""
    posted = client.post(
        "/analyze",
        files=_upload(blot_bytes),
        data={"config": "default", "lane_roi": ["5,0,70,120"]},
    ).json()

    fetched = client.get(f"/results/{posted['result']['result_id']}")

    assert fetched.status_code == 200
    assert fetched.json() == posted


def test_the_two_pass_housekeeping_flow_works_over_the_api(
    client: TestClient, blot_bytes: bytes
) -> None:
    """Band ids only exist after a run, so naming a loading control takes two posts.

    Pass one runs a mode that needs no reference and yields the ids; pass two names the
    per-lane references under a housekeeping mode. This is the flow the endpoint's OpenAPI
    description documents, executed exactly as written.
    """
    first = client.post("/analyze", files=_upload(blot_bytes), data={"config": "default"})
    assert first.status_code == 200
    first_result = first.json()["result"]
    assert first_result["normalization"]["mode"] == "total_protein"
    references = [
        band["band_id"] for band in first_result["bands"] if band["band_id"].endswith("_B1")
    ]
    assert len(references) == 3, "one reference band per lane"

    second = client.post(
        "/analyze",
        files=_upload(blot_bytes),
        data={"config": "housekeeping", "reference_band_id": references},
    )

    assert second.status_code == 200
    second_result = second.json()["result"]
    assert _schema_errors(second_result) == []
    assert second_result["normalization"]["mode"] == "housekeeping_single"
    assert second_result["normalization"]["reference_band_ids"] == references
    assert "single_housekeeping_reference" in second_result["normalization"]["warnings"]
    assert second_result["result_id"] != first_result["result_id"]
    assert client.get(f"/results/{second_result['result_id']}").status_code == 200


def test_a_lane_roi_off_the_image_is_a_400_with_the_message_intact(
    client: TestClient, blot_bytes: bytes
) -> None:
    """The actionable part of the error is the part that must survive the HTTP layer."""
    response = client.post(
        "/analyze",
        files=_upload(blot_bytes),
        data={"config": "default", "lane_roi": ["5,0,70,120", "170,0,70,120"]},
    )

    assert response.status_code == 400
    body = response.json()
    assert "lane ROI 2" in body["detail"]
    assert "200x120" in body["detail"]
    assert body["error"] == "LaneRoiError"
    assert "Traceback" not in response.text


def test_overlapping_lane_rois_are_a_400_naming_both(
    client: TestClient, blot_bytes: bytes
) -> None:
    response = client.post(
        "/analyze",
        files=_upload(blot_bytes),
        data={"config": "default", "lane_roi": ["0,0,60,120", "50,0,60,120"]},
    )

    assert response.status_code == 400
    assert "lane ROI 1" in response.json()["detail"]
    assert "lane ROI 2" in response.json()["detail"]


def test_a_lane_roi_too_small_to_profile_is_a_400_not_a_pipeline_internal_422(
    client: TestClient, blot_bytes: bytes
) -> None:
    """Over HTTP the caller cannot change a config value, so the remedy has to be the rectangle.

    A one-row rectangle used to reach the noise estimator and come back as a 422
    ``DetectionError`` telling the caller to raise ``detection.lane.min_separation_px`` --
    which does not apply to a supplied rectangle and which no request can set, configs being
    selected by name. It is a fault of the rectangle and reads as one.
    """
    response = client.post(
        "/analyze",
        files=_upload(blot_bytes),
        data={"config": "default", "lane_roi": ["5,0,70,120", "120,0,70,1"]},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "LaneRoiError"
    assert "lane ROI 2" in body["detail"]
    assert "x=120, y=0, width=70, height=1" in body["detail"]
    assert "Supply a taller rectangle" in body["detail"]
    assert "min_separation_px" not in body["detail"]


def test_a_malformed_lane_roi_string_is_a_400(client: TestClient, blot_bytes: bytes) -> None:
    """No silent coercion over HTTP either."""
    response = client.post(
        "/analyze",
        files=_upload(blot_bytes),
        data={"config": "default", "lane_roi": ["5,0,70"]},
    )

    assert response.status_code == 400
    assert "comma-separated value(s)" in response.json()["detail"]


def test_an_unknown_config_is_a_400_listing_the_ones_that_exist(
    client: TestClient, blot_bytes: bytes
) -> None:
    """A typo is a message, never a fallback to some default parameter set."""
    response = client.post("/analyze", files=_upload(blot_bytes), data={"config": "defualt"})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "unknown config 'defualt'" in detail
    assert "'default'" in detail and "'housekeeping'" in detail
    assert "does not accept a posted config" in detail


def test_an_unsupported_pixel_type_is_a_415_that_says_which(
    client: TestClient, tmp_path: Path
) -> None:
    """A float TIFF is refused rather than squashed, and the refusal reaches the caller."""
    path = tmp_path / "float.tiff"
    tifffile.imwrite(str(path), np.zeros((60, 60), dtype=np.float32))

    response = client.post(
        "/analyze",
        files={"image": ("float.tiff", path.read_bytes(), "image/tiff")},
        data={"config": "default"},
    )

    assert response.status_code == 415
    assert "float32" in response.json()["detail"]
    assert response.json()["error"] == "UnsupportedBitDepthError"


def test_bytes_that_are_not_an_image_are_a_415(client: TestClient) -> None:
    """The container is decided by signature bytes, so a renamed text file is caught."""
    response = client.post(
        "/analyze",
        files=_upload(b"this is not a blot", name="blot.png"),
        data={"config": "default"},
    )

    assert response.status_code == 415
    assert "unrecognised image container" in response.json()["detail"]


def test_a_reference_band_that_names_nothing_is_a_400(
    client: TestClient, blot_bytes: bytes
) -> None:
    """A caller mistake in the request, and the message says which id was not found."""
    response = client.post(
        "/analyze",
        files=_upload(blot_bytes),
        data={"config": "housekeeping", "reference_band_id": ["L9_B9"]},
    )

    assert response.status_code == 400
    assert "L9_B9" in response.json()["detail"]
    assert response.json()["error"] == "ReferenceBandError"


class _UnclassifiedError(PipelineError):
    """A ``PipelineError`` subclass nobody has put in ``STATUS_BY_ERROR``."""


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        # The caller's designation of the reference bands: theirs to fix, so 4xx.
        (ReferenceBandError("reference band id(s) ['L9_B9'] name no measured band"), 400),
        # Its parent covers invariants of the analysis -- a duplicate band id is a detection
        # defect, and a 400 would tell the caller to fix a request they got right.
        (NormalizationError("band id(s) ['L0_B1'] occur more than once"), 500),
        # An unmapped subclass is an unreviewed failure mode, so it is this service's problem
        # until someone classifies it, never the caller's.
        (_UnclassifiedError("nobody has classified this one"), 500),
    ],
)
def test_the_status_mapping_blames_the_right_party(error: PipelineError, expected: int) -> None:
    """The subclass split is the whole point of it, and the default is the server's fault."""
    assert status_for(error) == expected


def test_an_unknown_result_id_is_a_404_naming_the_id(client: TestClient) -> None:
    response = client.get("/results/0123456789abcdef")

    assert response.status_code == 404
    assert "0123456789abcdef" in response.json()["detail"]
    assert "Traceback" not in response.text


def test_an_id_that_is_not_an_id_is_a_400(client: TestClient) -> None:
    """A request that can never work is not a 404, which would suggest trying again."""
    response = client.get("/results/not%20an%20id")

    assert response.status_code == 400
    assert response.json()["error"] == "MalformedResultIdError"


def test_a_successful_response_carries_no_error_field(
    client: TestClient, blot_bytes: bytes
) -> None:
    """A 200 must never carry an error string for a caller to notice or miss."""
    envelope = client.post(
        "/analyze", files=_upload(blot_bytes), data={"config": "default"}
    ).json()

    assert "error" not in envelope
    assert "detail" not in envelope
    assert "error" not in envelope["result"]


def test_an_invalid_document_is_a_500_and_is_never_served_as_a_200(
    client: TestClient, blot_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The schema gate, forced: the envelope exists to carry a *valid* document.

    The analysis is made to emit one extra top-level key, which the schema forbids
    (``additionalProperties: false``). Without the gate this response would be a 200 putting
    an invalid document into circulation with this service's name on it.
    """
    genuine = app_module.analyze_image

    def _with_an_extra_key(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {**genuine(*args, **kwargs), "sample_name": "lysate A"}

    monkeypatch.setattr(app_module, "analyze_image", _with_an_extra_key)

    response = client.post("/analyze", files=_upload(blot_bytes), data={"config": "default"})

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "ResultSchemaError"
    assert "result" not in body, "an invalid document must not reach the caller at all"
    assert "sample_name" in body["detail"]
    assert "defect in this service" in body["detail"]


def test_an_invalid_document_is_not_written_to_the_store_either(
    client: TestClient, blot_bytes: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refusing to *serve* an invalid document is only half of it: it must not be kept.

    A document that failed the schema and was stored anyway would be served by every later
    ``GET`` of that id, which is the same invalid document in circulation one request later.
    The endpoint validates before it saves; without this test a reorder of those two lines
    would pass the suite. The id is captured from inside the patched analysis because a 500
    carries no result to read it from.
    """
    genuine = app_module.analyze_image
    captured: dict[str, str] = {}

    def _with_an_extra_key(*args: Any, **kwargs: Any) -> dict[str, Any]:
        document = genuine(*args, **kwargs)
        captured["result_id"] = document["result_id"]
        return {**document, "sample_name": "lysate A"}

    monkeypatch.setattr(app_module, "analyze_image", _with_an_extra_key)

    assert (
        client.post("/analyze", files=_upload(blot_bytes), data={"config": "default"})
    ).status_code == 500

    result_id = captured["result_id"]
    assert not (tmp_path / "results" / result_id).exists()
    assert client.get(f"/results/{result_id}").status_code == 404


def test_a_stored_document_from_an_older_contract_says_so_rather_than_blaming_a_defect(
    client: TestClient, blot_bytes: bytes, tmp_path: Path
) -> None:
    """On ``GET`` no analysis ran, so "it is a defect in this service" would be wrong.

    ``schema_version`` is a ``const``, so every result stored by an earlier build fails
    validation the moment the constant moves. The actionable fact is the version gap, and the
    message has to carry the id and both versions for an operator to act on it.
    """
    result_id = (
        client.post("/analyze", files=_upload(blot_bytes), data={"config": "default"})
        .json()["result"]["result_id"]
    )
    stored = tmp_path / "results" / result_id / "result.json"
    document = json.loads(stored.read_text(encoding="utf-8"))
    document["schema_version"] = "1.1.0"
    stored.write_text(json.dumps(document), encoding="utf-8")

    response = client.get(f"/results/{result_id}")

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert response.json()["error"] == "ResultSchemaError"
    assert result_id in detail
    assert "'1.1.0'" in detail and f"'{RESULT_SCHEMA_VERSION}'" in detail
    assert "Re-analyse the image" in detail
    assert "defect in this service" not in detail


def test_a_damaged_stored_display_record_leaves_as_a_json_error_like_every_other(
    client: TestClient, blot_bytes: bytes, tmp_path: Path
) -> None:
    """One JSON error shape is a promise, so no path may escape as a bare decode failure."""
    result_id = (
        client.post("/analyze", files=_upload(blot_bytes), data={"config": "default"})
        .json()["result"]["result_id"]
    )
    (tmp_path / "results" / result_id / "display.json").write_text("{ truncat", encoding="utf-8")

    response = client.get(f"/results/{result_id}")

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "CorruptStoredResultError"
    assert "display.json" in body["detail"]
    assert result_id in body["detail"]
    assert "Traceback" not in response.text


def test_a_stored_document_with_undecodable_bytes_is_refused_rather_than_repaired(
    client: TestClient, blot_bytes: bytes, tmp_path: Path
) -> None:
    """The damage that a lenient decode would *hide*, which is the one worth pinning.

    A truncated file cannot be parsed and fails loudly whatever the decoder does. Bytes
    corrupted inside a JSON *string* are the opposite case: under ``errors="replace"`` they
    become U+FFFD, the document parses, the schema still accepts it -- a mangled ``band_id``
    is still a string -- and a silently altered document is served as a 200. So the assertion
    is not merely that a 500 comes back, but that the id in the document was never rewritten.
    """
    result = client.post(
        "/analyze", files=_upload(blot_bytes), data={"config": "default"}
    ).json()["result"]
    result_id = result["result_id"]
    band_id = result["bands"][0]["band_id"]
    stored = tmp_path / "results" / result_id / "result.json"
    damaged = stored.read_bytes().replace(
        band_id.encode("ascii"), band_id.encode("ascii")[:-1] + b"\xff"
    )
    assert damaged != stored.read_bytes(), "the band id must appear in the stored document"
    stored.write_bytes(damaged)

    response = client.get(f"/results/{result_id}")

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "CorruptStoredResultError"
    assert "not valid UTF-8" in body["detail"]
    assert "result.json" in body["detail"]
    assert "�" not in response.text, "no damaged byte may be repaired into a served value"


def test_a_display_record_damaged_into_an_unlabelled_one_is_refused(
    client: TestClient, blot_bytes: bytes, tmp_path: Path
) -> None:
    """An empty object parses, so nothing but this check stops an unlabelled derivative.

    The PNG is a rendering of measured pixels and the block is what says so. Served without
    ``is_derivative``, ``note`` and ``mapping``, the image would arrive looking like the
    measurement -- the one thing the display boundary exists to prevent.
    """
    result_id = (
        client.post("/analyze", files=_upload(blot_bytes), data={"config": "default"})
        .json()["result"]["result_id"]
    )
    (tmp_path / "results" / result_id / "display.json").write_text("{}", encoding="utf-8")

    response = client.get(f"/results/{result_id}")

    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "CorruptStoredResultError"
    assert result_id in body["detail"]
    for key in ("is_derivative", "note", "mapping"):
        assert key in body["detail"]


def test_the_app_refuses_a_config_directory_that_does_not_exist(tmp_path: Path) -> None:
    """A mistyped ``--config-dir`` is a deployment defect and must fail like one.

    Left unchecked the catalog simply finds no files, and every request comes back 400
    ``UnknownConfigError`` telling the caller their config name is wrong -- about a name that
    is right. The failure belongs at construction, where the wrong directory was chosen.
    """
    with pytest.raises(PipelineError, match="config directory") as raised:
        create_app(storage_root=tmp_path / "results", config_dir=tmp_path / "typo")

    assert status_for(raised.value) == 500, "a deployment defect is never the caller's fault"


def test_a_422_from_request_validation_is_told_apart_by_the_error_key(
    client: TestClient, blot_bytes: bytes, tmp_path: Path
) -> None:
    """Two different things return 422, and a consumer has to be able to branch on which.

    FastAPI's own validation returns a *list* under ``detail`` and no ``error`` key; a
    request that reached the pipeline and could not be carried out returns a *string* and
    names the class.
    """
    missing_config = client.post("/analyze", files=_upload(blot_bytes))
    flat = tmp_path / "flat.png"
    assert cv2.imwrite(str(flat), np.full((60, 60), 1000, dtype=np.uint16))
    no_lanes = client.post(
        "/analyze", files=_upload(flat.read_bytes(), name="flat.png"), data={"config": "default"}
    )

    assert missing_config.status_code == no_lanes.status_code == 422
    assert isinstance(missing_config.json()["detail"], list)
    assert "error" not in missing_config.json()
    assert isinstance(no_lanes.json()["detail"], str)
    assert no_lanes.json()["error"] == "DetectionError"


def test_the_server_cli_requires_both_directories_and_guesses_neither(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Where results are written and which parameter sets are offered are decisions.

    A default for either would make a running server's behaviour depend on something nobody
    wrote down, so each one missing is an exit rather than a fallback.
    """
    parser = build_parser()

    for argv in ([], ["--config-dir", "configs"], ["--storage-root", "results"]):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)
        assert "required" in capsys.readouterr().err

    args = parser.parse_args(["--storage-root", "results", "--config-dir", "configs"])
    assert (args.storage_root, args.config_dir) == (Path("results"), Path("configs"))


def test_the_server_cli_reports_a_bad_setup_the_way_the_pipeline_cli_does(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A misconfigured server exits 1 with the actionable message, not with a traceback.

    ``create_app`` validates both directories before anything binds, so the failure lands in
    the CLI wrapper. ``python -m pipeline`` prints ``error: <message>`` to stderr and returns
    1 for the same class of problem, and a user should not have to learn two failure shapes
    for two CLIs in one project.
    """
    exit_code = api_main(
        ["--storage-root", str(tmp_path / "out"), "--config-dir", str(tmp_path / "absent")]
    )

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("error: ")
    assert "does not exist or is not a directory" in captured.err
    assert "--config-dir" in captured.err
    assert "Traceback" not in captured.err


def test_the_app_refuses_a_storage_root_inside_the_gold_set(
    tmp_path: Path, config_dir: Path
) -> None:
    """PLAN.md's first invariant holds for this writer too, and fails at construction."""
    with pytest.raises(PipelineError, match="python -m synth"):
        create_app(storage_root=tmp_path / "data" / "ground_truth", config_dir=config_dir)


def test_the_openapi_document_describes_the_two_pass_flow(client: TestClient) -> None:
    """The flow is not discoverable from the endpoint signature, so it is written down.

    What has to be documented is the *requirement* -- that band ids only exist after an
    analysis, so naming a housekeeping band takes a second post of the same image -- not
    merely that a field called ``reference_band_id`` exists. The field name is asserted too,
    because it is the mechanism, but on its own it would let the description lose the flow.
    """
    description = client.get("/openapi.json").json()["paths"]["/analyze"]["post"]["description"]

    assert "Band ids only exist after an image has been analysed" in description
    assert "takes two posts" in description
    assert "Post the *same* image again" in description
    assert "two different documents with two different ids" in description
    assert "reference_band_id" in description
    assert "roi_source" in description


def _imported_roots(directory: Path) -> dict[str, set[str]]:
    """Return, per source file in ``directory``, the top-level packages it imports."""
    sources = sorted(directory.glob("*.py"))
    assert sources, f"no sources found under {directory}"
    roots: dict[str, set[str]] = {}
    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add(node.module or "")
        roots[source.name] = {name.split(".")[0] for name in names}
    return roots


def test_the_api_never_imports_the_generator_or_the_evals() -> None:
    """Anti-circularity reaches this package too: api/ imports neither synth/ nor evals/."""
    for name, roots in _imported_roots(REPO_ROOT / "api").items():
        assert not roots & {"synth", "evals"}, f"{name} imports the generator or the evals"


def test_the_pipeline_never_imports_the_api() -> None:
    """The measurement layer must not depend on the presentation layer.

    The direction of this dependency is the whole reason the display renderer lives in
    ``api/``: ``pipeline/`` refuses to rescale pixels, and an import from the package that
    rescales them for display would put that refusal one edit away from being undone.
    """
    for name, roots in _imported_roots(REPO_ROOT / "pipeline").items():
        assert "api" not in roots, f"pipeline/{name} imports the api"
