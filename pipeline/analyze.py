"""End-to-end analysis of one image, and the result document it produces.

The document is a **strict subset** of ``schema/result.schema.json``: every field it
emits has the name, type and shape the schema defines, and it emits no field the
schema does not define -- but it omits the fields Phase 2 owns
(:data:`PHASE2_RESULT_FIELDS`). Emitting them as empty structures would assert that QC
ran and found nothing, and that normalization ran and produced no ratios, which is
indistinguishable from a real clean result. A missing key is honest; an empty list is
not. Phase 2 fills them in and the document becomes schema-valid.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline import PIPELINE_VERSION, RESULT_SCHEMA_VERSION
from pipeline.background import correct_background
from pipeline.config import PipelineConfig
from pipeline.detect import detect
from pipeline.errors import PipelineError
from pipeline.load import load_image
from pipeline.quantify import quantify_bands

PHASE2_RESULT_FIELDS: dict[str, tuple[str, ...]] = {
    "<root>": ("normalization", "image_qc_flags"),
    "bands[]": ("qc_flags", "excluded_from_normalization"),
    "provenance.parameters": ("normalization",),
}
"""Schema-required fields Phase 1 deliberately does not emit, by the object holding them.

Each is produced by ``pipeline/qc.py`` or ``pipeline/normalize.py``, which are Phase 2.
``tests/test_pipeline_result.py`` pins this list from both sides: the result validates
against the schema with exactly these requirements relaxed, and fails against the
unmodified schema for exactly these reasons.
"""

PHASE2_CONDITIONAL_BAND_FIELDS: tuple[str, ...] = ("exclusion_reason",)
"""Also absent, but required by the schema only through a conditional.

The schema requires ``exclusion_reason`` when ``excluded_from_normalization`` is true.
JSON Schema's ``properties`` constrains only the keys that are present, so omitting
``excluded_from_normalization`` altogether satisfies that ``if`` and pulls in the
``then``. Listing the field here keeps that consequence explicit instead of letting it
surface as a surprise in Phase 2.
"""

RESULT_ID_HEX_DIGITS = 16
"""Length of the content-addressed result id, in hex characters."""


def _result_id(source_sha256: str, config_digest: str) -> str:
    """Return a deterministic id for the (image bytes, parameter set) pair.

    Derived from content rather than from the file path or the clock, so re-analysing
    the same image with the same config reproduces the same id on any machine.
    """
    payload = f"{source_sha256}|{config_digest}".encode()
    return hashlib.sha256(payload).hexdigest()[:RESULT_ID_HEX_DIGITS]


def _created_at() -> str:
    """Return the current UTC time as an RFC 3339 timestamp."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def analyze_image(
    image_path: Path,
    config: PipelineConfig,
    ground_truth_image_id: str | None = None,
) -> dict[str, Any]:
    """Load, background-correct, detect and quantify one image; return its result document.

    Deterministic: for a fixed image and config every field except
    ``provenance.created_at`` is reproduced exactly on every run.

    ``ground_truth_image_id`` is recorded verbatim when the caller supplies one (the
    eval harness does, to join results to ground truth). Nothing in the analysis reads
    it, and it is never inferred from the file name.
    """
    loaded = load_image(image_path)
    correction = correct_background(loaded.pixels, config.background)
    detection = detect(correction.corrected, config.detection)
    measurements = quantify_bands(
        correction.corrected, correction.background, detection.bands, config.quantification
    )
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "result_id": _result_id(loaded.sha256, config.digest()),
        "source": loaded.as_source(ground_truth_image_id),
        "provenance": {
            "software_version": PIPELINE_VERSION,
            "config_id": config.config_id,
            "config_digest": config.digest(),
            "created_at": _created_at(),
            "parameters": config.as_parameters(),
        },
        "lanes": [
            {
                "lane_id": lane.lane_id,
                "lane_index": lane.lane_index,
                "roi": lane.roi.as_dict(),
            }
            for lane in detection.lanes
        ],
        "bands": [measurement.as_dict() for measurement in measurements],
    }


GROUND_TRUTH_DIRECTORY_NAME = "ground_truth"
"""Directory name nothing in this package may write into, whatever the caller asks for.

PLAN.md's first key invariant is that ``data/ground_truth/`` is written only by
``python -m synth``. Nothing here would write there deliberately, but ``--out`` is a path
from the command line, so the boundary is enforced rather than left to convention.
"""


def require_writable_destination(out_dir: Path) -> None:
    """Raise :class:`PipelineError` if ``out_dir`` is inside a ground-truth directory.

    Three properties, each of which the obvious one-line version of this check lacks:

    * **Resolved.** ``Path.resolve`` expands ``..`` and follows symlinks, so a symlink or
      a relative path pointing into the gold set is caught rather than inspected as text.
    * **Case-folded.** macOS and Windows filesystems are case-insensitive, so
      ``data/GROUND_TRUTH`` *is* ``data/ground_truth`` on the platform this project is
      developed on. An exact-case component match let that through.
    * **Whole components only.** ``ground_truth2`` and ``my_ground_truth_notes`` are
      ordinary directories and stay writable; only a path component that *is* the
      reserved name is refused.
    """
    resolved = out_dir.expanduser().resolve()
    if any(part.casefold() == GROUND_TRUTH_DIRECTORY_NAME for part in resolved.parts):
        raise PipelineError(
            f"refusing to write into {out_dir} (resolves to {resolved}): ground truth is "
            f"written only by 'python -m synth' (PLAN.md key invariant). Choose an output "
            f"directory outside any {GROUND_TRUTH_DIRECTORY_NAME}/ tree"
        )


def write_result(result: dict[str, Any], out_dir: Path, stem: str) -> Path:
    """Write ``result`` as ``<out_dir>/<stem>.json`` and return the path written.

    Refuses any destination inside a ground-truth directory; see
    :func:`require_writable_destination`.
    """
    require_writable_destination(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stem}.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return path
