"""The FastAPI application: ``POST /analyze`` and ``GET /results/{result_id}``.

Both endpoints return the same **envelope**::

    {"result": <document valid against schema/result.schema.json>, "display": {...}}

The envelope is necessary rather than decorative. The result schema is
``additionalProperties: false`` at its top level, so the display derivative cannot be
carried inside the result document without breaking the contract that makes the document
worth anything -- and it should not be, because the PNG is a rendering and the document is
the measurement. Wrapping keeps the document byte-for-byte what the pipeline wrote and what
the schema validates, and gives presentation its own place beside it.

Every document is validated against the schema *before* it is returned, on both endpoints. A
document that fails is raised as :class:`api.errors.ResultSchemaError` and becomes a 500,
because serving it as a 200 would put an invalid document into circulation with this
service's name on it. The two endpoints reach that failure from opposite directions and
:func:`_require_valid` is told which: on ``POST`` the document was just produced here, so
nothing but a defect in this service can explain it, while on ``GET`` it also covers a
document stored under an earlier ``schema_version`` than this build declares -- for which
the actionable fact is "re-analyse the image", not "hunt a bug".

**Two different things return 422.** FastAPI's own request validation -- a missing form
field, an unparseable one -- returns 422 with ``detail`` as a *list* of per-field objects and
no ``error`` key. This service returns 422 with ``detail`` as a *string* and an ``error`` key
naming the class (``DetectionError``, ``BackgroundError``) for a request that was well formed
and still could not be carried out. The ``error`` key is the discriminator: present means the
request reached the pipeline.
"""

from __future__ import annotations

import base64
import json
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator

from api import API_VERSION
from api.configs import ConfigCatalog
from api.display import DISPLAY_BLOCK_KEYS, DisplayDerivative, render_display
from api.errors import CorruptStoredResultError, ResultSchemaError, status_for
from api.storage import ResultStore
from pipeline import PIPELINE_VERSION, RESULT_SCHEMA_VERSION
from pipeline.analyze import analyze_image
from pipeline.detect import parse_lane_rois
from pipeline.errors import PipelineError
from pipeline.load import load_image

RESULT_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "result.schema.json"
"""The contract every returned document is checked against.

A fixed repository asset, unlike the storage root: the schema is the source of truth for
the result contract and there is nothing to configure about which one applies.
"""

UPLOAD_FALLBACK_NAME = "upload"
"""Name the uploaded bytes are written under when the client sent no usable filename."""

ANALYZE_DESCRIPTION = """
Analyse one uploaded image and return the result document with its display derivative.

Processing is synchronous: the pipeline runs inside this request and the finished result
comes back in the response. The `result_id` is content-addressed over the image bytes, the
config digest, the reference band ids and the supplied lane ROIs, so re-posting the
identical request returns the same id.

**What is reproducible under that id, and what is not.** Everything the analysis measured:
every ROI, every intensity, every ratio, every QC flag, and `source.sha256`. Two fields are
*not*, because neither is an input to the id: `provenance.created_at`, which is a clock
read, and `source.path`, which names a per-request temporary file (see "Recorded source
path" below). So two identical requests return the same id and two documents that differ in
those two fields — if you are diffing responses to check reproducibility, compare the
measured fields, not the whole document.

**Lane ROIs.** Supplying any `lane_roi` (repeat the field, `x,y,width,height` in pixels,
in lane order) switches lane detection off for this image: the rectangles given are the
lanes and band detection proceeds inside them unchanged. Each lane in the result records
`roi_source`, which is `caller` for a supplied rectangle and `detected` otherwise, so a
region you chose is never reported back to you as something the detector found.

**The two-pass housekeeping flow.** Band ids only exist after an image has been analysed,
and this pipeline never infers which band is the loading control. So normalizing to a
housekeeping band takes two posts:

1. Post the image under a config whose normalization mode needs no reference --
   `total_protein` -- and read the band ids out of `result.bands[].band_id`.
2. Post the *same* image again under a `housekeeping_single` or `housekeeping_multi`
   config, naming the reference bands with one `reference_band_id` field per band.

The two passes produce two different documents with two different ids, and both remain
retrievable through `GET /results/{result_id}`.

**Recorded source path.** The upload is written to a temporary file under its own filename
for the duration of the request, and `result.source.path` names that file -- the path the
pixels were actually read from. The image is identified by `result.source.sha256`.

**Reading a 422.** Two different things return it. A failure of this endpoint's own request
validation -- a missing or unparseable form field -- carries `detail` as a *list* and no
`error` key. A request that was well formed and still could not be carried out, such as an
image no lane could be located in, carries `detail` as a *string* and an `error` key naming
the class. Branch on `error`, not on the status.
"""

RESULTS_DESCRIPTION = """
Return a stored result by its `result_id`, in the same envelope shape `POST /analyze`
returns: the stored result document and its display derivative.

**Which analysis you get back.** The stored entry is the most recent analysis written under
that id, not necessarily the first. `result_id` is content-addressed over the image bytes,
the config digest, the reference band ids and the supplied lane ROIs -- but not over
`source.path`, which names the per-request temporary file the upload was read from. So two
posts of identical bytes under different filenames share an id, produce documents that
differ in `source.path` and `provenance.created_at`, and the later one replaces the earlier.
Everything the analysis measured is identical across them; see `POST /analyze` for the full
statement of what is and is not reproducible under an id.

An id that names nothing stored is a 404 that quotes the id back.
"""


def _validator() -> Draft202012Validator:
    """Return a validator for the result schema as written, with nothing relaxed."""
    schema = json.loads(RESULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _require_valid(
    result: Mapping[str, Any], validator: Draft202012Validator, cause: str
) -> None:
    """Raise :class:`ResultSchemaError` if ``result`` fails the result schema.

    ``cause`` is the calling endpoint's account of what a failure means there, and is
    required rather than defaulted because a single sentence would be wrong on one of the two
    paths: a document that has just been analysed can only fail through a defect here, while
    a document read back off disk can equally well predate the contract this build declares.
    An operator told to hunt a code defect over a stale stored document is being sent the
    wrong way.
    """
    errors = sorted(validator.iter_errors(result), key=lambda error: list(error.path))
    if errors:
        detail = "; ".join(
            f"{'/'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
            for error in errors
        )
        raise ResultSchemaError(
            f"a result document fails schema/result.schema.json {RESULT_SCHEMA_VERSION} and "
            f"was not returned: {detail}. {cause}"
        )


def _stored_document_cause(result: Mapping[str, Any], result_id: str) -> str:
    """Return why a *stored* document might fail the schema, given what it declares.

    ``schema_version`` is a ``const`` in the schema, so every document written by an earlier
    build of this service fails validation the moment the constant moves. That is the first
    thing to check and the only one with a remedy the operator can apply, so the message
    states both versions and says what to do about them.
    """
    stored_version = result.get("schema_version", "<absent>")
    if stored_version != RESULT_SCHEMA_VERSION:
        return (
            f"Stored result {result_id!r} declares schema_version {stored_version!r} and this "
            f"service serves {RESULT_SCHEMA_VERSION!r}: the stored document predates the "
            f"current contract. Re-analyse the image to get one written against "
            f"{RESULT_SCHEMA_VERSION}"
        )
    return (
        f"Stored result {result_id!r} declares schema_version {stored_version!r}, which is "
        f"the version this service serves, so this is not a stale document: it is a defect "
        f"in this service or a store that has been written to by something else"
    )


def _require_labelled_display(block: Mapping[str, Any], result_id: str) -> None:
    """Raise :class:`CorruptStoredResultError` unless every labelling key is *present*.

    The result document is re-validated against the schema on the way out; the display record
    has no schema, and it is the half of the envelope that carries the derivative's own
    warning label. A ``display.json`` damaged into ``{}`` parses, so without this check a
    ``GET`` would answer with a PNG carrying no ``is_derivative``, no ``note`` and no
    ``mapping`` -- an unlabelled rendering of measured data, which is precisely what this
    service's display boundary exists to prevent. Only ``GET`` needs it: on ``POST`` the block
    was built by :meth:`api.display.DisplayDerivative.as_block` in the same request.

    **Presence only, and the limit is worth stating rather than implying.** This does not check
    the values, so a record damaged into ``{"is_derivative": false, "note": "", "mapping": {}}``
    passes -- and that record has lost ``mapping.source_dn_per_output_level``, which is the
    field a consumer needs in order not to read a 255 in the PNG as saturation. It catches a
    record that has lost its shape, not one that has kept its shape and lost its meaning.
    Checking the values against :meth:`~api.display.DisplayDerivative.as_block`'s invariants is
    the stronger form and is not implemented here.
    """
    missing = [key for key in DISPLAY_BLOCK_KEYS if key not in block]
    if missing:
        raise CorruptStoredResultError(
            f"the stored result {result_id!r} exists but its display record is missing "
            f"{missing}, so the PNG beside it cannot be labelled as the derivative it is; "
            f"a display record carries {list(DISPLAY_BLOCK_KEYS)}. It was written by this "
            f"service complete, so it has been damaged since; re-analysing the image "
            f"reproduces this result, id and all"
        )


def _envelope(
    result: Mapping[str, Any], display_block: Mapping[str, Any], png: bytes
) -> dict[str, Any]:
    """Return the response envelope, assembled the same way on both endpoints."""
    return {
        "result": dict(result),
        "display": {**display_block, "png_base64": base64.b64encode(png).decode("ascii")},
    }


def _upload_filename(filename: str | None) -> str:
    """Return a safe file name for the uploaded bytes.

    ``Path(...).name`` strips any directory the client put in the filename, so a client
    cannot choose where its upload lands inside the temporary directory.
    """
    candidate = Path(filename or "").name.strip()
    return candidate or UPLOAD_FALLBACK_NAME


def _analyse_upload(
    payload: bytes,
    filename: str | None,
    config_name: str,
    lane_roi_specs: Sequence[str] | None,
    reference_band_ids: Sequence[str],
    catalog: ConfigCatalog,
) -> tuple[dict[str, Any], DisplayDerivative]:
    """Run the pipeline over uploaded bytes and render their display derivative.

    ``lane_roi_specs`` is ``None`` when the request carried no ``lane_roi`` field at all, and
    a sequence when it carried any -- passed through as it arrives rather than collapsed,
    because the pipeline distinguishes "not supplying lanes" from "supplying none".

    The bytes are written to a temporary file because the pipeline reads images from paths,
    and are loaded twice on purpose: once by :func:`pipeline.analyze.analyze_image`, which
    owns the measurement, and once here for the derivative. The alternative -- returning the
    pixel array out of the analysis so it can be re-used -- would make the measurement path
    responsible for feeding the presentation path, which is the coupling this package's
    display boundary exists to avoid.
    """
    config = catalog.load(config_name)
    lane_rois = None if lane_roi_specs is None else parse_lane_rois(lane_roi_specs)
    with tempfile.TemporaryDirectory(prefix="blotquant-upload-") as directory:
        image_path = Path(directory) / _upload_filename(filename)
        image_path.write_bytes(payload)
        loaded = load_image(image_path)
        display = render_display(loaded.pixels, loaded.max_value)
        result = analyze_image(
            image_path,
            config,
            reference_band_ids=list(reference_band_ids),
            lane_rois=lane_rois,
        )
    return result, display


def create_app(*, storage_root: Path, config_dir: Path) -> FastAPI:
    """Return the application, storing results under ``storage_root``.

    Both directories are constructor arguments rather than module constants: a test points
    them at ``tmp_path``, and nothing about where results live is baked into an import.
    ``storage_root`` is checked against the ground-truth boundary immediately, so a server
    configured to write into the gold set fails here rather than on its first upload.
    """
    store = ResultStore(storage_root)
    catalog = ConfigCatalog(config_dir)
    validator = _validator()

    app = FastAPI(
        title="blotquant",
        version=API_VERSION,
        description=(
            f"QC-first western blot densitometry. Pipeline {PIPELINE_VERSION}, result "
            f"schema {RESULT_SCHEMA_VERSION}. Every response carries the result document "
            f"exactly as the pipeline wrote it, validated against "
            f"schema/result.schema.json, plus a display derivative that is labelled as one."
        ),
    )

    def pipeline_error_handler(request: Request, exc: Exception) -> JSONResponse:
        """Turn a :class:`PipelineError` into its mapped status, message intact.

        The message is passed through verbatim because it is the actionable part -- it names
        the lane rectangle, the config, the band id or the pixel type at fault. The class
        name travels with it so a machine consumer can branch without parsing prose. The
        traceback does not travel at all.

        Starlette routes exceptions to this handler by class, so ``exc`` is always a
        :class:`PipelineError`. Anything else is re-raised rather than wrapped: reporting an
        unrelated exception under this mapping would give it a status this module never
        chose for it.
        """
        if not isinstance(exc, PipelineError):
            raise exc
        return JSONResponse(
            status_code=status_for(exc),
            content={"detail": str(exc), "error": type(exc).__name__},
        )

    app.add_exception_handler(PipelineError, pipeline_error_handler)

    @app.post("/analyze", description=ANALYZE_DESCRIPTION, summary="Analyse one image")
    def analyze(
        image: Annotated[UploadFile, File(description="8/16-bit grayscale TIFF, PNG or JPEG")],
        config: Annotated[
            str, Form(description="name of a parameter set in configs/, e.g. 'default'")
        ],
        lane_roi: Annotated[
            list[str] | None,
            Form(description="lane rectangle 'x,y,width,height' in px; repeat, in lane order"),
        ] = None,
        reference_band_id: Annotated[
            list[str] | None,
            Form(description="housekeeping reference band id; repeat once per reference"),
        ] = None,
    ) -> dict[str, Any]:
        """Analyse the uploaded image and return its envelope."""
        result, display = _analyse_upload(
            payload=image.file.read(),
            filename=image.filename,
            config_name=config,
            lane_roi_specs=lane_roi,
            reference_band_ids=reference_band_id or (),
            catalog=catalog,
        )
        _require_valid(
            result,
            validator,
            "The document was written by the analysis this request just ran, against the "
            "contract this build declares, so no caller input can cause this: it is a defect "
            "in this service",
        )
        block = display.as_block()
        store.save(result["result_id"], result, block, display.png)
        return _envelope(result, block, display.png)

    @app.get(
        "/results/{result_id}",
        description=RESULTS_DESCRIPTION,
        summary="Fetch a stored result",
    )
    def get_result(result_id: str) -> dict[str, Any]:
        """Return the stored envelope for ``result_id``.

        Both halves of the stored envelope are re-checked rather than trusted. The document
        is re-validated, so one written under an earlier contract is refused loudly instead of
        being served as though it satisfied the version this service declares -- the likely
        cause here rather than a defect, so the message names the stored id and both versions.
        The display record is checked for the labelling it exists to carry
        (:func:`_require_labelled_display`), which no schema covers.
        """
        result, block, png = store.load(result_id)
        _require_valid(result, validator, _stored_document_cause(result, result_id))
        _require_labelled_display(block, result_id)
        return _envelope(result, block, png)

    return app
