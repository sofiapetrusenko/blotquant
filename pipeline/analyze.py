"""End-to-end analysis of one image, and the result document it produces.

The document validates against ``schema/result.schema.json`` in full: every field the
schema requires is emitted, every field emitted is one the schema defines, and
``schema_version`` names the version it actually satisfies. Phase 1 emitted a documented
strict subset because ``qc_flags``, ``excluded_from_normalization`` and the whole
``normalization`` block had no producer yet, and emitting them empty would have asserted
that QC ran and found nothing. Both producers exist now, so nothing is omitted.

One consequence of the schema is load-bearing and easy to lose: ``excluded_from_normalization``
is emitted on **every** band, including the ones that are not excluded. The band object's
``allOf`` requires ``exclusion_reason`` whenever that key is *absent or* true -- JSON
Schema's ``properties`` constrains only the keys a document actually has -- so a band that
quietly omitted it would inherit a requirement written for excluded bands.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline import PIPELINE_VERSION, RESULT_SCHEMA_VERSION
from pipeline.background import correct_background
from pipeline.config import TOTAL_PROTEIN, PipelineConfig
from pipeline.detect import DetectedLane, Roi, detect
from pipeline.errors import PipelineError
from pipeline.load import load_image
from pipeline.normalize import NormalizationBand, normalize
from pipeline.qc import BandQc, assess
from pipeline.quantify import BandMeasurement, integrate_roi, quantify_bands

RESULT_ID_HEX_DIGITS = 16
"""Length of the content-addressed result id, in hex characters."""


def _result_id(
    source_sha256: str,
    config_digest: str,
    reference_band_ids: Sequence[str] | None,
    lane_rois: Sequence[Roi] | None,
) -> str:
    """Return a deterministic id for every input that can change the document.

    Derived from content rather than from the file path or the clock, so re-analysing the
    same image with the same inputs reproduces the same id on any machine.

    All **four** inputs are hashed, not two. The reference band ids are a per-image input no
    parameter set can carry -- ``NormalizationConfig`` deliberately does not -- and they change
    the denominators, the ratios and the exclusions, so hashing only the image and the config
    would give two genuinely different documents the same id, and PLAN.md's Phase 4
    ``GET /results/{id}`` would serve one for the other. The list is hashed *in order*, because
    the order is recorded on the result and reappears in each ratio's ``denominator_band_ids``,
    and it is JSON-encoded rather than joined so that no id can be forged through a separator
    inside a band id.

    The caller-supplied lane ROIs are the fourth input, for the same reason: they replace the
    detected lanes outright, so they change every ROI, every intensity and every ratio in the
    document while the image bytes and the config digest stay identical. They too are hashed
    *in order* -- the order is the lane order -- and JSON-encoded, and ``null`` is encoded
    distinctly from any list, so "detection ran" and "the caller supplied lanes" can never
    hash alike.
    """
    references = json.dumps(list(reference_band_ids or ()), separators=(",", ":"))
    rois = json.dumps(
        None if lane_rois is None else [roi.as_dict() for roi in lane_rois],
        separators=(",", ":"),
    )
    payload = f"{source_sha256}|{config_digest}|{references}|{rois}".encode()
    return hashlib.sha256(payload).hexdigest()[:RESULT_ID_HEX_DIGITS]


def _created_at() -> str:
    """Return the current UTC time as an RFC 3339 timestamp."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _band_document(
    measurement: BandMeasurement, qc: BandQc, excluded: bool, reason: str | None
) -> dict[str, Any]:
    """Return one ``bands[]`` entry: the measurement, its QC verdict and its exclusion.

    ``excluded_from_normalization`` is always present, whatever its value; see this
    module's docstring for why an omitted false is not equivalent.
    """
    if measurement.band_id != qc.band_id:
        raise PipelineError(
            f"measurement {measurement.band_id!r} was paired with the QC verdict for "
            f"{qc.band_id!r}; the two are produced in detection order and must stay aligned"
        )
    document = measurement.as_dict()
    document["peak_value"] = qc.peak_value
    document["clipped_pixel_count"] = qc.clipped_pixel_count
    # Explicitly null rather than absent when the shape statistic was censored: absence would
    # be indistinguishable from a writer that does not measure it, and null says "measured,
    # and the answer is that it could not be measured here".
    document["row_half_width_ratio"] = qc.row_half_width_ratio
    document["qc_flags"] = list(qc.flags)
    document["excluded_from_normalization"] = excluded
    if reason is not None:
        document["exclusion_reason"] = reason
    return document


def _lane_document(lane: DetectedLane, total_protein: float | None) -> dict[str, Any]:
    """Return one ``lanes[]`` entry, carrying its total-protein signal only if it was used.

    ``roi_source`` is always present, whatever its value, for the same reason
    ``excluded_from_normalization`` is always present on a band: an absent value would be
    indistinguishable from a writer that does not record it, and a document that reported a
    caller-chosen region as a detector output would be a false record.
    """
    document: dict[str, Any] = {
        "lane_id": lane.lane_id,
        "lane_index": lane.lane_index,
        "roi": lane.roi.as_dict(),
        "roi_source": lane.roi_source,
    }
    if total_protein is not None:
        document["total_protein_signal"] = total_protein
    return document


def analyze_image(
    image_path: Path,
    config: PipelineConfig,
    ground_truth_image_id: str | None = None,
    reference_band_ids: Sequence[str] | None = None,
    *,
    lane_rois: Sequence[Roi] | None = None,
) -> dict[str, Any]:
    """Analyse one image end to end and return its result document.

    Load, background-correct, detect, quantify, QC, normalize -- in that order, because QC
    needs the measured ROIs and normalization needs the QC flags.

    Deterministic: for a fixed image, config, reference list and lane ROIs, every field except
    ``provenance.created_at`` is reproduced exactly on every run.

    ``lane_rois`` replaces lane detection. When rectangles are supplied,
    :func:`pipeline.detect.detect_lanes` does not run and they are the lanes, in the order
    given, each recorded as ``roi_source: "caller"``; band detection inside them is
    unchanged. ``None`` and an *empty* sequence are different requests: ``None`` is "I am not
    supplying lanes", and detection runs; an empty sequence is a caller who switched detection
    off and then named nothing to replace it with, and
    :func:`pipeline.detect.validate_lane_rois` raises
    :class:`pipeline.errors.LaneRoiError` for it. Quietly re-detecting there would answer a
    UI that has just had its last rectangle deleted with a different set of numbers instead
    of an error. The same function raises :class:`pipeline.errors.LaneRoiError` for a
    rectangle outside the image, one overlapping another, or one whose sides are shorter
    than ``config`` can run a profile over
    (:func:`pipeline.detect.minimum_lane_extent_px`).

    ``ground_truth_image_id`` is recorded verbatim when the caller supplies one (the
    eval harness does, to join results to ground truth). Nothing in the analysis reads
    it, and it is never inferred from the file name.

    ``reference_band_ids`` designates the housekeeping reference bands. It is required by
    the housekeeping normalization modes and refused by ``total_protein``: which band is the
    loading control is not visible in the pixels, so it is never inferred and never guessed
    (human ruling, NOTES.md Phase 2). Band ids come from a previous run of this function on
    the same image, or from a user correcting the detection.
    """
    supplied_lane_rois = None if lane_rois is None else tuple(lane_rois)
    loaded = load_image(image_path)
    correction = correct_background(loaded.pixels, config.background)
    detection = detect(correction.corrected, config.detection, supplied_lane_rois)
    measurements = quantify_bands(
        correction.corrected, correction.background, detection.bands, config.quantification
    )
    report = assess(
        loaded.pixels,
        correction.corrected,
        detection.bands,
        loaded.max_value,
        loaded.lossy_format,
        config.qc,
    )
    flags = report.flags_by_band_id()
    # Measured only for the mode that reads it: the lane integral is the total_protein
    # denominator, and normalize refuses an input no mode of it would read. It is an ROI pixel
    # sum, in the same units as a band's integrated_intensity, so the two are comparable -- not a
    # mean-column-profile integral, which would differ from it by the lane's width.
    lane_total_protein: Mapping[str, float] | None = (
        {
            lane.lane_id: integrate_roi(
                correction.corrected, lane.roi, config.quantification.clamp_negative_pixels
            )
            for lane in detection.lanes
        }
        if config.normalization.mode == TOTAL_PROTEIN
        else None
    )
    normalization = normalize(
        [
            NormalizationBand(
                band_id=measurement.band_id,
                lane_id=measurement.lane_id,
                intensity=measurement.integrated_intensity,
                qc_flags=flags[measurement.band_id],
            )
            for measurement in measurements
        ],
        [lane.lane_id for lane in detection.lanes],
        config.normalization,
        lane_total_protein=lane_total_protein,
        reference_band_ids=reference_band_ids,
        lossy_format=loaded.lossy_format,
    )
    exclusions = normalization.exclusion_by_band_id()
    totals = normalization.lane_total_protein
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "result_id": _result_id(
            loaded.sha256, config.digest(), reference_band_ids, supplied_lane_rois
        ),
        "source": loaded.as_source(ground_truth_image_id),
        "provenance": {
            "software_version": PIPELINE_VERSION,
            "config_id": config.config_id,
            "config_digest": config.digest(),
            "created_at": _created_at(),
            "parameters": config.as_parameters(),
        },
        "lanes": [
            _lane_document(lane, None if totals is None else totals[lane.lane_id])
            for lane in detection.lanes
        ],
        "bands": [
            _band_document(
                measurement,
                qc,
                exclusions[measurement.band_id].excluded,
                exclusions[measurement.band_id].reason,
            )
            for measurement, qc in zip(measurements, report.bands, strict=True)
        ],
        "normalization": normalization.as_dict(),
        "image_qc_flags": list(report.image_flags),
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
