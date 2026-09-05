"""The committed gallery, checked against the contract it claims to satisfy.

``web/public/gallery/`` is committed, so its documents ship. Nothing re-measures them in CI --
that would need the API running and the crops re-analysed -- so what CI can check is that each
stored document is a valid result document, that ``index.json`` says about it what the ruled
derivation says about it, and that each card is the one thing a card is allowed to be: one
caller-supplied lane.

**An empty gallery fails these tests rather than skipping them.** A skipped test over an unbuilt
gallery is indistinguishable from a passing one, and the gallery is the phase's deliverable:
build it with ``python -m tools.gallery.build`` against a running API.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from jsonschema import Draft202012Validator

from api.display import DISPLAY_BLOCK_KEYS, lane_verdicts
from tools.gallery.build import (
    DISPLAY_METADATA_NAME,
    DISPLAY_NAME,
    GALLERY_ROOT,
    INDEX_NAME,
    MANIFEST_PATH,
    PNG_BASE64_KEY,
    PNG_SIGNATURE,
    RESULT_NAME,
    START_COMMAND,
    GalleryBuildError,
    _card_verdict,
    _load_manifest,
    _media_type,
    _preflight,
    _write_card,
    build,
    main,
)


def _refuse_to_connect(request: httpx.Request) -> httpx.Response:
    """Stand in for nothing listening on the port."""
    raise httpx.ConnectError("connection refused", request=request)


@pytest.fixture(scope="module")
def png_bytes() -> bytes:
    """A real 1x1 PNG, so the signature and decode checks are exercised on a genuine image."""
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "result.schema.json"


@pytest.fixture(scope="module")
def card_directories() -> list[Path]:
    """Return every card directory in the committed gallery, sorted, failing if there are none."""
    if not GALLERY_ROOT.is_dir():
        pytest.fail(
            f"the gallery has not been built: {GALLERY_ROOT} does not exist. Start the API "
            f"('python -m api --storage-root results/ --config-dir configs/') and run "
            f"'python -m tools.gallery.build'"
        )
    directories = sorted(child for child in GALLERY_ROOT.iterdir() if child.is_dir())
    if not directories:
        pytest.fail(
            f"the gallery at {GALLERY_ROOT} holds no cards. Build it with "
            f"'python -m tools.gallery.build' against a running API"
        )
    return directories


@pytest.fixture(scope="module")
def index() -> list[dict[str, Any]]:
    """Return the committed ``index.json``, failing if it is absent or empty."""
    path = GALLERY_ROOT / INDEX_NAME
    if not path.is_file():
        pytest.fail(f"no {path}; build the gallery with 'python -m tools.gallery.build'")
    rows = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(rows, list) and rows, f"{path} lists no cards"
    return rows


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    """Return a validator for the result schema as written, with nothing relaxed."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _document(directory: Path) -> dict[str, Any]:
    """Return one card's stored result document."""
    return json.loads((directory / RESULT_NAME).read_text(encoding="utf-8"))


def test_every_stored_document_validates_against_the_result_schema(
    card_directories: list[Path], validator: Draft202012Validator
) -> None:
    """The gallery ships documents; a document that fails the contract must not ship.

    The same validator ``api/app.py`` applies before serving anything, so a stored card is held
    to exactly the contract the live service holds a fresh result to.
    """
    for directory in card_directories:
        errors = sorted(validator.iter_errors(_document(directory)), key=lambda e: list(e.path))
        assert not errors, (
            f"{directory / RESULT_NAME} fails schema/result.schema.json: "
            + "; ".join(
                f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
                for error in errors
            )
        )


def test_every_stored_document_has_its_display_derivative(
    card_directories: list[Path],
) -> None:
    """A card is a document and the picture it was measured from; neither ships alone."""
    for directory in card_directories:
        png = directory / DISPLAY_NAME
        assert png.is_file(), f"{directory.name} has no {DISPLAY_NAME}"
        assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), (
            f"{png} does not begin with the PNG signature"
        )


def test_every_stored_derivative_is_labelled_as_one(card_directories: list[Path]) -> None:
    """The picture ships with the block that says it is a rendering, not the measured data.

    Three claims, and they are different. The labelling keys are all present, so nothing on the
    card view has to invent a caption for a derivative. ``mapping.source_dn_per_output_level``
    is there and is a number, because it is the field that says a 255 in the PNG is not evidence
    of saturation -- the single most misreadable thing about showing a blot next to a QC flag.
    And the encoded image is *not* in ``display.json``: the bytes are ``display.png``, and the
    same picture committed twice in two encodings could drift between them.
    """
    for directory in card_directories:
        path = directory / DISPLAY_METADATA_NAME
        assert path.is_file(), f"{directory.name} has no {DISPLAY_METADATA_NAME}"
        block = json.loads(path.read_text(encoding="utf-8"))
        assert [key for key in DISPLAY_BLOCK_KEYS if key not in block] == [], (
            f"{path} is missing labelling keys; every one of {list(DISPLAY_BLOCK_KEYS)} is "
            f"emitted by api.display.DisplayDerivative.as_block"
        )
        assert PNG_BASE64_KEY not in block, (
            f"{path} carries {PNG_BASE64_KEY!r}; the image bytes belong in "
            f"{DISPLAY_NAME} alone"
        )
        assert block["is_derivative"] is True
        assert isinstance(block["mapping"]["source_dn_per_output_level"], (int, float)), (
            f"{path}: mapping.source_dn_per_output_level is not a number, so the card cannot "
            f"say how many source DN one output level of the PNG spans"
        )


def _png_dimensions(path: Path) -> tuple[int, int]:
    """Return ``(width, height)`` read from a PNG's IHDR chunk.

    The bytes, not a declaration about them. IHDR is fixed-position: an 8-byte signature, a
    4-byte length, the type ``IHDR``, then width and height as big-endian uint32.
    """
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    assert header[12:16] == b"IHDR", f"{path} does not begin with an IHDR chunk"
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def test_the_stored_derivative_dimensions_are_the_sources_own(
    card_directories: list[Path],
) -> None:
    """A result ROI indexes the stored PNG one to one, which is what the overlay relies on.

    The web overlay draws lane and band rectangles from ``result`` straight onto ``display.png``
    with an SVG ``viewBox`` of the source's dimensions. That is only correct while the
    derivative is rendered at the source's own size: a resized derivative would put every
    rectangle in the wrong place while still looking like a picture of a blot.

    **The PNG's own IHDR is read**, not just the two JSON declarations of its size. Comparing
    ``display.json`` against ``result.json`` compares two claims; a genuinely resized image whose
    block still reported the source dimensions would pass that and break every overlay. The
    artefact the browser draws on is the one under test, so all three are asserted equal.
    """
    for directory in card_directories:
        block = json.loads((directory / DISPLAY_METADATA_NAME).read_text(encoding="utf-8"))
        source = _document(directory)["source"]
        pixels = _png_dimensions(directory / DISPLAY_NAME)
        assert pixels == (source["width_px"], source["height_px"]), (
            f"{directory.name}: {DISPLAY_NAME} is {pixels[0]}x{pixels[1]} but the source is "
            f"{source['width_px']}x{source['height_px']}; ROI coordinates would not land on "
            f"the pixels they name"
        )
        assert (block["width_px"], block["height_px"]) == pixels, (
            f"{directory.name}: {DISPLAY_METADATA_NAME} declares "
            f"{block['width_px']}x{block['height_px']} but {DISPLAY_NAME} is "
            f"{pixels[0]}x{pixels[1]}"
        )


def test_every_card_is_one_caller_supplied_lane(card_directories: list[Path]) -> None:
    """Ruling 2, 2026-08-25: a gallery card is one caller-supplied ROI with its verdict.

    Two things are asserted and they are different claims. Exactly one lane, because a card
    with two lanes has no single verdict to show. And ``roi_source == "caller"``, because a card
    built from a rectangle the detector chose would be showing the caller path while having
    measured the detected one.
    """
    for directory in card_directories:
        lanes = _document(directory)["lanes"]
        assert len(lanes) == 1, f"{directory.name} holds {len(lanes)} lanes, not one"
        assert lanes[0]["roi_source"] == "caller", (
            f"{directory.name} reports roi_source {lanes[0]['roi_source']!r}; every gallery "
            f"card is measured from a caller-supplied rectangle"
        )


def test_the_index_lists_the_manifests_cards_in_the_manifests_order(
    index: list[dict[str, Any]], card_directories: list[Path]
) -> None:
    """``index.json`` is the manifest's selection and its order, and the stored set matches it.

    Order matters to the screens: the ``/`` gallery renders the first blocked card larger, so a
    reordered index changes what the site shows without changing a single measurement.
    """
    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest_ids = [str(entry["id"]) for entry in manifest["entries"]]
    assert [row["id"] for row in index] == manifest_ids
    assert sorted(directory.name for directory in card_directories) == sorted(manifest_ids)


def test_the_index_verdicts_are_what_the_ruled_derivation_says(
    index: list[dict[str, Any]], card_directories: list[Path]
) -> None:
    """Re-derive every recorded verdict from the stored document and require agreement.

    ``index.json`` is written by the build from :func:`api.display.lane_verdicts`; this asserts
    it still agrees with that function over the documents that were committed beside it, so a
    hand-edited index or a stale one is caught. ``blocked_reason`` is present exactly when the
    card is blocked, on the convention :meth:`api.display.LaneVerdict.as_dict` uses.
    """
    documents = {directory.name: _document(directory) for directory in card_directories}
    for row in index:
        (verdict,) = lane_verdicts(documents[row["id"]])
        assert row["verdict"] == verdict.verdict, row["id"]
        assert row["flag_count"] == len(verdict.qc_flags), row["id"]
        assert row.get("blocked_reason") == verdict.blocked_reason, row["id"]
        assert set(row) <= {"id", "title", "verdict", "blocked_reason", "flag_count"}, row["id"]
        assert row["title"], f"{row['id']} has no title"


# ---------------------------------------------------------------------------------------
# Failure modes of the build itself.
#
# The tests above check the committed *output*. These check that the build refuses the inputs
# it says it refuses, which is the other half of CLAUDE.md's "failure modes, not just happy
# path" -- and the half that a build tool, whose whole job is to refuse rather than fabricate,
# most needs. None of them needs the API: every raise below happens before or after the wire.
# ---------------------------------------------------------------------------------------


def test_an_unreadable_image_suffix_is_refused_rather_than_guessed() -> None:
    """The multipart content type comes from the file, and an unknown suffix stops the build.

    Labelling an unrecognised container as something else would send the API an image it then
    refuses for a reason naming the wrong thing, and the operator would debug the wrong file.
    """
    assert _media_type(Path("crop.png")) == "image/png"
    assert _media_type(Path("crop.TIF")) == "image/tiff"
    assert _media_type(Path("crop.jpeg")) == "image/jpeg"
    with pytest.raises(GalleryBuildError, match=r"suffix '\.bmp'"):
        _media_type(Path("crop.bmp"))


def test_an_unlabelled_display_block_is_refused_rather_than_stored(tmp_path: Path) -> None:
    """A stored picture without the block that says it is a rendering must not ship.

    Each labelling key is dropped in turn, so the refusal is pinned for every one of them
    rather than for whichever happens to be checked first.
    """
    complete = {key: f"value for {key}" for key in DISPLAY_BLOCK_KEYS}
    for absent in DISPLAY_BLOCK_KEYS:
        block = {key: value for key, value in complete.items() if key != absent}
        with pytest.raises(GalleryBuildError, match=absent):
            _write_card(tmp_path / "card", {}, block, b"")
    assert not (tmp_path / "card").exists(), (
        "a refused card wrote files before raising; a partial card directory is exactly what "
        "the refusal exists to prevent"
    )


def test_the_stored_display_block_drops_only_the_encoded_image(tmp_path: Path) -> None:
    """``display.json`` is the served block minus ``png_base64``, and nothing else moves."""
    block: dict[str, Any] = {key: f"value for {key}" for key in DISPLAY_BLOCK_KEYS}
    block[PNG_BASE64_KEY] = "aGVsbG8="
    directory = tmp_path / "card"
    _write_card(directory, {"result_id": "abc"}, block, b"\x89PNG\r\n\x1a\n")

    stored = json.loads((directory / DISPLAY_METADATA_NAME).read_text(encoding="utf-8"))
    assert stored == {key: value for key, value in block.items() if key != PNG_BASE64_KEY}
    assert (directory / DISPLAY_NAME).read_bytes() == b"\x89PNG\r\n\x1a\n"
    assert json.loads((directory / RESULT_NAME).read_text(encoding="utf-8")) == {
        "result_id": "abc"
    }


def test_a_card_that_is_not_one_lane_fails_the_build() -> None:
    """Ruling 2, 2026-08-25: a card is one caller-supplied ROI, so any other count is a defect."""
    two_lanes: dict[str, Any] = {
        "lanes": [
            {"lane_id": "L0", "roi_source": "caller"},
            {"lane_id": "L1", "roi_source": "caller"},
        ],
        "bands": [],
        "normalization": {"ratios": []},
        "image_qc_flags": [],
    }
    with pytest.raises(GalleryBuildError, match="came back with 2 lanes"):
        _card_verdict("two-lane-card", "a title", two_lanes)


def test_a_document_the_ruled_derivation_cannot_read_fails_the_build() -> None:
    """A document the service produced that the derivation refuses is a defect, not a skip."""
    with pytest.raises(GalleryBuildError, match="cannot be read back"):
        _card_verdict("damaged", "a title", {"lanes": [], "bands": []})


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("not a mapping", "does not parse as a YAML mapping"),
        ("api_url: http://x\n", r"is missing \['entries'\]"),
        ("entries: []\n", r"is missing \['api_url'\]"),
        ("api_url: http://x\nentries: 3\n", "must be a list of card entries"),
        ("api_url: http://x\nentries: []\n", "lists no entries"),
        ("api_url: http://x\nentries:\n  - 3\n", "not a mapping carrying"),
        ("api_url: http://x\nentries:\n  - id: a\n", "is missing"),
    ],
)
def test_a_malformed_manifest_is_refused_with_a_message_naming_the_fault(
    tmp_path: Path, body: str, expected: str
) -> None:
    """Every manifest defect names itself. A build has one input and it must say what is wrong."""
    path = tmp_path / "manifest.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(GalleryBuildError, match=expected):
        _load_manifest(path)


def test_a_repeated_id_is_refused_because_the_second_card_would_overwrite_the_first(
    tmp_path: Path,
) -> None:
    """Ids become directory names, and YAML ``1`` and ``"1"`` name the same directory."""
    path = tmp_path / "manifest.yaml"
    entry = "    image: i.png\n    polarity: p\n    config: c\n    lane_roi: r\n    title: t\n"
    path.write_text(
        f"api_url: http://x\nentries:\n  - id: 1\n{entry}  - id: '1'\n{entry}",
        encoding="utf-8",
    )
    with pytest.raises(GalleryBuildError, match="repeats the id"):
        _load_manifest(path)


def test_a_missing_manifest_says_so_rather_than_building_nothing(tmp_path: Path) -> None:
    """An absent manifest is not an empty gallery; the build refuses instead of shipping one."""
    with pytest.raises(GalleryBuildError, match="no gallery manifest at"):
        _load_manifest(tmp_path / "absent.yaml")


def test_an_unreachable_api_stops_the_build_and_names_the_command_that_starts_one() -> None:
    """The build never fabricates a measurement, and never starts the server itself."""
    with httpx.Client(transport=httpx.MockTransport(_refuse_to_connect)) as client:
        with pytest.raises(GalleryBuildError, match=START_COMMAND):
            _preflight(client, "http://127.0.0.1:9")


def test_something_else_listening_on_the_port_stops_the_build() -> None:
    """A 200 from ``/openapi.json`` is the check; anything else is not this service.

    A gallery built against whatever happened to be listening would be measurements from a
    service nobody chose, and nothing in the output would say so.
    """
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as client:
        with pytest.raises(GalleryBuildError, match="not the blotquant API"):
            _preflight(client, "http://127.0.0.1:8000")


def test_each_committed_card_measures_the_image_its_manifest_entry_names(
    index: list[dict[str, Any]], card_directories: list[Path]
) -> None:
    """The card's attribution and its numbers must come from the same file.

    ``test_the_index_lists_the_manifests_cards_in_the_manifests_order`` checks ids and order, so
    a manifest whose ``image:`` or ``title:`` was edited without a rebuild ships a card
    attributing one figure and reporting measurements of another, and nothing fails. The build
    checks this at build time against the bytes it posted; this checks the *committed* result,
    which is what actually ships.
    """
    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))
    repo_root = Path(__file__).resolve().parent.parent
    rows = {row["id"]: row for row in index}
    for entry in manifest["entries"]:
        entry_id = str(entry["id"])
        document = _document(GALLERY_ROOT / entry_id)
        served = document["source"].get("sha256")
        if served is not None:
            image = repo_root / str(entry["image"])
            assert image.is_file(), f"{entry_id}: {image} does not exist"
            expected = "sha256:" + hashlib.sha256(image.read_bytes()).hexdigest()
            assert served == expected, (
                f"{entry_id} was measured from a file whose digest is {served}, but the "
                f"manifest names {entry['image']}, which hashes to {expected}. The card would "
                f"attribute one figure and report measurements of another -- rebuild the gallery"
            )
        assert rows[entry_id]["title"] == str(entry["title"]), (
            f"{entry_id}: index.json says {rows[entry_id]['title']!r} and the manifest says "
            f"{entry['title']!r}; rebuild the gallery"
        )
        lane_roi = document["lanes"][0]["roi"]
        assert (
            f"{lane_roi['x']},{lane_roi['y']},{lane_roi['width']},{lane_roi['height']}"
            == str(entry["lane_roi"])
        ), f"{entry_id}: the stored lane ROI is not the rectangle the manifest names"


def _manifest(tmp_path: Path, *, ids: tuple[str, ...], image: Path) -> Path:
    """Write a manifest naming ``ids``, all pointing at ``image``."""
    entries = "".join(
        f"  - id: {entry_id}\n    image: {image}\n    polarity: dark_on_bright\n"
        f"    config: default\n    lane_roi: \"0,0,4,4\"\n    title: t {entry_id}\n"
        for entry_id in ids
    )
    path = tmp_path / "manifest.yaml"
    path.write_text(f"api_url: http://api.test\nentries:\n{entries}", encoding="utf-8")
    return path


def _one_lane_envelope(entry_id: str, png: bytes) -> dict[str, Any]:
    """A minimal but *valid-shaped* envelope: one caller lane, no bands, no ratios."""
    return {
        "result": {
            "result_id": entry_id,
            "source": {"width_px": 4, "height_px": 4},
            "lanes": [{"lane_id": "L0", "roi_source": "caller"}],
            "bands": [],
            "normalization": {"ratios": []},
            "image_qc_flags": [],
        },
        "display": {
            **{key: f"value for {key}" for key in DISPLAY_BLOCK_KEYS},
            PNG_BASE64_KEY: base64.b64encode(png).decode("ascii"),
        },
    }


def _transport(handler: Any) -> httpx.Client:
    """A client whose ``/analyze`` responses come from ``handler``; preflight always succeeds."""

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/openapi.json":
            return httpx.Response(200, json={})
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(route))


@pytest.fixture()
def existing_gallery(tmp_path: Path) -> Path:
    """A gallery root holding one card and an index, standing in for the committed one."""
    root = tmp_path / "gallery"
    (root / "old-card").mkdir(parents=True)
    (root / "old-card" / RESULT_NAME).write_text("{}", encoding="utf-8")
    (root / INDEX_NAME).write_text('[{"id": "old-card"}]\n', encoding="utf-8")
    return root


def _assert_untouched(root: Path) -> None:
    """The committed gallery is exactly as it was: the old card and the old index, unchanged."""
    assert (root / INDEX_NAME).read_text(encoding="utf-8") == '[{"id": "old-card"}]\n', (
        f"{root / INDEX_NAME} was modified or removed by a build that failed; the gallery is "
        f"committed, so a failed build must leave the working tree as it found it"
    )
    assert (root / "old-card" / RESULT_NAME).is_file()
    assert not (root.parent / f"{root.name}.building").exists(), "staging directory left behind"
    assert not (root.parent / f"{root.name}.previous").exists(), "swap directory left behind"


def test_an_unreachable_api_leaves_the_committed_gallery_untouched(
    tmp_path: Path, existing_gallery: Path
) -> None:
    """The most likely failure of this tool must not be its most destructive.

    An API that is not running used to delete the committed ``index.json`` and rebuild nothing,
    leaving the tree strictly worse for no gain: ``pytest`` and ``next build`` both fail until
    ``git checkout`` restores it.
    """
    manifest = _manifest(tmp_path, ids=("a",), image=Path("data/real/crops"))
    with httpx.Client(transport=httpx.MockTransport(_refuse_to_connect)) as client:
        with pytest.raises(GalleryBuildError, match=START_COMMAND):
            build(manifest, existing_gallery, client)
    _assert_untouched(existing_gallery)


def test_a_refusal_part_way_through_leaves_the_committed_gallery_untouched(
    tmp_path: Path, existing_gallery: Path, png_bytes: bytes
) -> None:
    """No mixture of two builds. Card 1 succeeds, card 2 is refused, and nothing is written.

    This is the state the previous in-place arrangement produced and could not detect: some
    cards from the new build, some stale from the old, and no index -- all of it commit-able.
    """
    image = tmp_path / "crop.png"
    image.write_bytes(png_bytes)
    manifest = _manifest(tmp_path, ids=("a", "b"), image=image)

    def handler(request: httpx.Request) -> httpx.Response:
        handler.calls += 1  # type: ignore[attr-defined]
        if handler.calls == 1:  # type: ignore[attr-defined]
            return httpx.Response(200, json=_one_lane_envelope("a", png_bytes))
        return httpx.Response(
            415, json={"detail": "pixel type float32", "error": "UnsupportedBitDepthError"}
        )

    handler.calls = 0  # type: ignore[attr-defined]
    with _transport(handler) as client:
        with pytest.raises(GalleryBuildError, match="UnsupportedBitDepthError"):
            build(manifest, existing_gallery, client)
    _assert_untouched(existing_gallery)
    assert not (existing_gallery / "a").exists(), (
        "the first card was written even though the build failed; a card the build did not "
        "finish must not reach the committed directory"
    )


def test_a_transport_failure_is_reported_as_a_build_error_not_a_traceback(
    tmp_path: Path, existing_gallery: Path, png_bytes: bytes
) -> None:
    """A read timeout or dropped connection leaves through the same handler as every failure."""
    image = tmp_path / "crop.png"
    image.write_bytes(png_bytes)
    manifest = _manifest(tmp_path, ids=("a",), image=image)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with _transport(handler) as client:
        with pytest.raises(GalleryBuildError, match="did not complete"):
            build(manifest, existing_gallery, client)
    _assert_untouched(existing_gallery)


def test_a_non_json_body_on_a_200_is_reported_rather_than_raising_a_decode_error(
    tmp_path: Path, existing_gallery: Path, png_bytes: bytes
) -> None:
    """A gateway answering 200 with HTML is a build error naming the body, not a traceback."""
    image = tmp_path / "crop.png"
    image.write_bytes(png_bytes)
    manifest = _manifest(tmp_path, ids=("a",), image=image)

    with _transport(lambda _: httpx.Response(200, text="<html>gateway</html>")) as client:
        with pytest.raises(GalleryBuildError, match="is not JSON"):
            build(manifest, existing_gallery, client)
    _assert_untouched(existing_gallery)


def test_a_garbled_display_payload_is_refused_rather_than_shipped_as_a_picture(
    tmp_path: Path, existing_gallery: Path, png_bytes: bytes
) -> None:
    """``base64.b64decode`` defaults to *discarding* junk; a card's picture must not be guessed.

    The payload matters. ``"not base64 !!!"`` raises under both ``validate=True`` and the default
    ``validate=False``, so it pinned nothing: dropping ``validate=True`` left the whole suite
    green. This is real PNG base64 with two ``!`` inserted mid-string, which the default silently
    strips, decoding to bytes that still begin with the PNG signature -- so neither the decode nor
    the signature check would catch it, and a subtly wrong picture would ship beside correct
    numbers with the build exiting 0.
    """
    image = tmp_path / "crop.png"
    image.write_bytes(png_bytes)
    manifest = _manifest(tmp_path, ids=("a",), image=image)
    envelope = _one_lane_envelope("a", png_bytes)
    encoded = base64.b64encode(png_bytes).decode("ascii")
    corrupt = encoded[:12] + "!!" + encoded[12:]
    # The premise, asserted rather than assumed: the lenient default accepts this and yields
    # something the PNG signature check would wave through.
    assert base64.b64decode(corrupt).startswith(PNG_SIGNATURE), (
        "this payload no longer discriminates validate=True from the default; it must decode "
        "leniently to bytes the signature check accepts"
    )
    envelope["display"][PNG_BASE64_KEY] = corrupt

    with _transport(lambda _: httpx.Response(200, json=envelope)) as client:
        with pytest.raises(GalleryBuildError, match="not valid base64"):
            build(manifest, existing_gallery, client)
    _assert_untouched(existing_gallery)


def test_bytes_that_are_not_a_png_are_refused(
    tmp_path: Path, existing_gallery: Path, png_bytes: bytes
) -> None:
    """Refusing a missing label while accepting any bytes as the image checks only the caption."""
    image = tmp_path / "crop.png"
    image.write_bytes(png_bytes)
    manifest = _manifest(tmp_path, ids=("a",), image=image)
    envelope = _one_lane_envelope("a", b"this is not a png")

    with _transport(lambda _: httpx.Response(200, json=envelope)) as client:
        with pytest.raises(GalleryBuildError, match="PNG signature"):
            build(manifest, existing_gallery, client)
    _assert_untouched(existing_gallery)


def test_a_served_digest_that_is_not_the_posted_images_fails_the_build(
    tmp_path: Path, existing_gallery: Path, png_bytes: bytes
) -> None:
    """The card's attribution comes from the manifest and its numbers from the API.

    Nothing else checks they are the same image, so a manifest edited to name a different crop
    would produce a card attributing one figure and reporting measurements of another.
    """
    image = tmp_path / "crop.png"
    image.write_bytes(png_bytes)
    manifest = _manifest(tmp_path, ids=("a",), image=image)
    envelope = _one_lane_envelope("a", png_bytes)
    envelope["result"]["source"]["sha256"] = "sha256:" + "0" * 64

    with _transport(lambda _: httpx.Response(200, json=envelope)) as client:
        with pytest.raises(GalleryBuildError, match="would attribute one figure"):
            build(manifest, existing_gallery, client)
    _assert_untouched(existing_gallery)


def test_a_matching_digest_passes_and_the_gallery_is_swapped_in(
    tmp_path: Path, existing_gallery: Path, png_bytes: bytes
) -> None:
    """The success path: the new tree replaces the old one whole, de-listed cards included."""
    image = tmp_path / "crop.png"
    image.write_bytes(png_bytes)
    manifest = _manifest(tmp_path, ids=("a",), image=image)
    envelope = _one_lane_envelope("a", png_bytes)
    envelope["result"]["source"]["sha256"] = (
        "sha256:" + hashlib.sha256(png_bytes).hexdigest()
    )

    with _transport(lambda _: httpx.Response(200, json=envelope)) as client:
        rows = build(manifest, existing_gallery, client)

    assert [row["id"] for row in rows] == ["a"]
    assert json.loads((existing_gallery / INDEX_NAME).read_text(encoding="utf-8")) == rows
    # **The invariant the whole gallery rests on**: the committed document is the one the service
    # served, unedited. Asserting the file merely *exists* let a mutation that stripped
    # `image_qc_flags` before `json.dumps` pass the entire suite -- and a card whose stored QC
    # annotations differ from what was measured is the one thing this site must never ship.
    assert (
        json.loads((existing_gallery / "a" / RESULT_NAME).read_text(encoding="utf-8"))
        == envelope["result"]
    ), "the stored document is not the document the API served"
    stored_display = json.loads(
        (existing_gallery / "a" / DISPLAY_METADATA_NAME).read_text(encoding="utf-8")
    )
    assert stored_display == {
        key: value for key, value in envelope["display"].items() if key != PNG_BASE64_KEY
    }, "the stored display block is not what was served, minus the encoded image"
    assert (existing_gallery / "a" / DISPLAY_NAME).read_bytes() == png_bytes
    assert not (existing_gallery / "old-card").exists(), (
        "a card the manifest no longer names survived the swap"
    )
    assert not (tmp_path / "gallery.building").exists()
    assert not (tmp_path / "gallery.previous").exists()


def _succeeding_build(tmp_path: Path, gallery_root: Path, png: bytes) -> tuple[Path, Any]:
    """Return a manifest and a transport that would build one card successfully."""
    image = tmp_path / "crop.png"
    image.write_bytes(png)
    manifest = _manifest(tmp_path, ids=("a",), image=image)
    envelope = _one_lane_envelope("a", png)
    envelope["result"]["source"]["sha256"] = "sha256:" + hashlib.sha256(png).hexdigest()
    return manifest, lambda _: httpx.Response(200, json=envelope)


def test_a_swap_that_cannot_complete_puts_the_previous_gallery_back(
    tmp_path: Path, existing_gallery: Path, png_bytes: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one window in which the gallery is not in place must not be able to lose it.

    The swap renames the live tree aside and the new tree into its place. A failure between those
    two renames used to leave the directory simply *gone*: the built tree deleted by the caller's
    cleanup, the only surviving copy stranded under ``<name>.previous``, and that copy destroyed
    at the start of the next run. ``web/public/gallery/`` is committed, so this was a way to
    delete a committed directory with a keystroke.
    """
    manifest, handler = _succeeding_build(tmp_path, existing_gallery, png_bytes)
    real_rename = Path.rename
    calls = {"n": 0}

    def flaky(self: Path, target: Any) -> Path:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated failure between the two renames")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky)
    with _transport(handler) as client:
        with pytest.raises(GalleryBuildError, match="left in place"):
            build(manifest, existing_gallery, client)

    _assert_untouched(existing_gallery)


def test_a_gallery_stranded_by_an_interrupted_swap_is_recovered_not_deleted(
    tmp_path: Path, png_bytes: bytes
) -> None:
    """A build killed mid-swap leaves the only copy under ``.previous``; the next run restores it.

    Deleting it at startup -- which is what the first version of this staging build did -- would
    destroy the last copy of the committed gallery on the next invocation, with no failure of
    any kind to signal it.
    """
    gallery_root = tmp_path / "gallery"
    stranded = tmp_path / "gallery.previous"
    (stranded / "old-card").mkdir(parents=True)
    (stranded / "old-card" / RESULT_NAME).write_text("{}", encoding="utf-8")
    (stranded / INDEX_NAME).write_text('[{"id": "old-card"}]\n', encoding="utf-8")
    assert not gallery_root.exists()

    manifest, handler = _succeeding_build(tmp_path, gallery_root, png_bytes)
    with _transport(handler) as client:
        build(manifest, gallery_root, client)

    # Recovered first, then replaced by the fresh build -- and never simply deleted.
    assert not stranded.exists()
    assert (gallery_root / "a" / RESULT_NAME).is_file()


def test_an_output_root_holding_anything_else_is_refused_before_any_request(
    tmp_path: Path, png_bytes: bytes
) -> None:
    """``--out`` one directory too high must not take the rest of the tree with it.

    The build replaces the output root wholesale, so the root is inspected before a single
    request is made and anything this tool did not write stops it.
    """
    root = tmp_path / "public"
    (root / "gallery").mkdir(parents=True)
    (root / "gallery" / RESULT_NAME).write_text("{}", encoding="utf-8")
    (root / "favicon.ico").write_bytes(b"not a card")

    manifest, handler = _succeeding_build(tmp_path, root, png_bytes)
    with _transport(handler) as client:
        with pytest.raises(GalleryBuildError, match=r"favicon\.ico"):
            build(manifest, root, client)

    assert (root / "favicon.ico").is_file(), "a refused output root was modified anyway"


def test_a_served_document_the_derivation_cannot_type_check_is_a_build_error(
    tmp_path: Path, existing_gallery: Path, png_bytes: bytes
) -> None:
    """``lane_verdicts`` checks key presence, not type, so a wrong type raises TypeError.

    That is not a ``DisplayError``, and before this it escaped the build's handler as a traceback
    -- contradicting ``main()``'s promise of an actionable message on any failure.
    """
    image = tmp_path / "crop.png"
    image.write_bytes(png_bytes)
    manifest = _manifest(tmp_path, ids=("a",), image=image)
    envelope = _one_lane_envelope("a", png_bytes)
    envelope["result"]["lanes"] = 3

    with _transport(lambda _: httpx.Response(200, json=envelope)) as client:
        with pytest.raises(GalleryBuildError, match="cannot be read back"):
            build(manifest, existing_gallery, client)
    _assert_untouched(existing_gallery)


def test_main_reports_a_filesystem_failure_rather_than_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """"1 with an actionable message on any failure" is a promise about every failure."""

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr("tools.gallery.build.build", boom)
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("api_url: http://x\nentries: []\n", encoding="utf-8")

    assert main(["--manifest", str(manifest), "--out", str(tmp_path / "out")]) == 1
    assert "no space left on device" in capsys.readouterr().err


def _flagged_envelope(png: bytes) -> dict[str, Any]:
    """A one-lane document whose bands carry flags, so `flag_count` is not zero."""
    envelope = _one_lane_envelope("a", png)
    envelope["result"]["bands"] = [
        {"band_id": "L0_B0", "lane_id": "L0", "qc_flags": ["saturated", "overlapping"]},
        {"band_id": "L0_B1", "lane_id": "L0", "qc_flags": ["saturated"]},
    ]
    envelope["result"]["normalization"]["ratios"] = [
        {"lane_id": "L0", "numerator_band_id": "L0_B0", "excluded": False, "qc_flags": []},
    ]
    return envelope


def test_the_index_row_carries_the_flag_count_the_derivation_produced(
    tmp_path: Path, existing_gallery: Path, png_bytes: bytes
) -> None:
    """`flag_count` drives every non-blocked caption on the site, and nothing pinned it.

    Every card the build path exercised elsewhere has an empty flag list, so replacing the count
    with a literal `0` survived the suite. This builds a card that carries flags.
    """
    image = tmp_path / "crop.png"
    image.write_bytes(png_bytes)
    manifest = _manifest(tmp_path, ids=("a",), image=image)
    envelope = _flagged_envelope(png_bytes)

    with _transport(lambda _: httpx.Response(200, json=envelope)) as client:
        rows = build(manifest, existing_gallery, client)

    verdict = lane_verdicts(envelope["result"])[0]
    assert verdict.verdict == "flagged"
    assert list(verdict.qc_flags) == ["saturated", "overlapping"]
    assert rows[0]["flag_count"] == len(verdict.qc_flags) == 2, (
        "the index row's flag_count is not the number of flags the ruled derivation produced"
    )
    assert "blocked_reason" not in rows[0]
    assert json.loads((existing_gallery / INDEX_NAME).read_text(encoding="utf-8")) == rows


def test_a_response_missing_half_the_envelope_is_refused(
    tmp_path: Path, existing_gallery: Path, png_bytes: bytes
) -> None:
    """Both halves are required, and each absence is refused by name.

    The check could be deleted with the suite green: nothing sent a body missing one of them.
    """
    image = tmp_path / "crop.png"
    image.write_bytes(png_bytes)
    manifest = _manifest(tmp_path, ids=("a",), image=image)
    for absent in ("result", "display"):
        envelope = _one_lane_envelope("a", png_bytes)
        del envelope[absent]
        with _transport(lambda _, body=envelope: httpx.Response(200, json=body)) as client:
            with pytest.raises(GalleryBuildError, match=f"carries no '{absent}'"):
                build(manifest, existing_gallery, client)
        _assert_untouched(existing_gallery)


def test_a_manifest_naming_an_image_that_does_not_exist_is_refused(
    tmp_path: Path, existing_gallery: Path, png_bytes: bytes
) -> None:
    """The path is resolved against the repository root, and a miss says so before posting."""
    manifest = _manifest(tmp_path, ids=("a",), image=tmp_path / "no-such-crop.png")
    with _transport(lambda _: httpx.Response(200, json=_one_lane_envelope("a", png_bytes))) as c:
        with pytest.raises(GalleryBuildError, match="which does not exist"):
            build(manifest, existing_gallery, c)
    _assert_untouched(existing_gallery)


def test_a_display_block_with_no_encoded_image_is_refused(
    tmp_path: Path, existing_gallery: Path, png_bytes: bytes
) -> None:
    """A labelled block with no picture in it is not a card; the build says so rather than
    writing a directory with a document and no derivative beside it."""
    image = tmp_path / "crop.png"
    image.write_bytes(png_bytes)
    manifest = _manifest(tmp_path, ids=("a",), image=image)
    envelope = _one_lane_envelope("a", png_bytes)
    del envelope["display"][PNG_BASE64_KEY]

    with _transport(lambda _: httpx.Response(200, json=envelope)) as client:
        with pytest.raises(GalleryBuildError, match="carries no 'png_base64'"):
            build(manifest, existing_gallery, client)
    _assert_untouched(existing_gallery)
