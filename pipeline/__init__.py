"""blotquant processing pipeline.

Anti-circularity invariant (PLAN.md): nothing in this package imports from
:mod:`synth`, reads ``data/ground_truth/``, or special-cases any property of the
synthetic generator. Every processing parameter arrives through
:class:`pipeline.config.PipelineConfig` and is echoed into result provenance.
"""

from __future__ import annotations

PIPELINE_VERSION = "0.1.0"
"""Version recorded as ``provenance.software_version`` in every result."""

RESULT_SCHEMA_VERSION = "1.2.0"
"""Version of ``schema/result.schema.json`` this pipeline targets, and validates against.

1.0.0 was the version Phase 1 *declared* while knowingly failing five of its required
fields, all of them owned by ``pipeline/qc.py`` and ``pipeline/normalize.py``. Phase 2
produces those fields and additionally extends the contract -- a third band flag, warnings
that name which flag a reference carries, a per-ratio reference-flagged record, a
multi-reference denominator list and the ``qc`` parameter block -- so the version is bumped
rather than left declaring a contract that has changed. Every edit is additive; NOTES.md's
Phase 2 section lists them with their reasons.

1.2.0 adds one field, and it is *required*: ``lanes[].roi_source``, saying whether a lane
rectangle was detected from the image or supplied by the caller. Required rather than
optional because an absent value would be indistinguishable from a writer that does not
record it, and a document reporting a caller-chosen region as a detector output is the
exact class of false record this project exists to prevent. Adding a required field has
precedent -- Phase 2 added a required ``qc`` block to ``provenance.parameters`` -- and the
``const`` on ``schema_version`` means a 1.1.0 document was never going to validate against
this schema anyway. NOTES.md's Phase 4a section lists the edit with its reason.

The schema pins this value as a ``const``, mirroring the ground-truth schema, so the two
cannot drift apart unnoticed.
"""
