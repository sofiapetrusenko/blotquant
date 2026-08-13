"""blotquant processing pipeline.

Anti-circularity invariant (PLAN.md): nothing in this package imports from
:mod:`synth`, reads ``data/ground_truth/``, or special-cases any property of the
synthetic generator. Every processing parameter arrives through
:class:`pipeline.config.PipelineConfig` and is echoed into result provenance.
"""

from __future__ import annotations

PIPELINE_VERSION = "0.1.0"
"""Version recorded as ``provenance.software_version`` in every result."""

RESULT_SCHEMA_VERSION = "1.1.0"
"""Version of ``schema/result.schema.json`` this pipeline targets, and validates against.

1.0.0 was the version Phase 1 *declared* while knowingly failing five of its required
fields, all of them owned by ``pipeline/qc.py`` and ``pipeline/normalize.py``. Phase 2
produces those fields and additionally extends the contract -- a third band flag, warnings
that name which flag a reference carries, a per-ratio reference-flagged record, a
multi-reference denominator list and the ``qc`` parameter block -- so the version is bumped
rather than left declaring a contract that has changed. Every edit is additive; NOTES.md's
Phase 2 section lists them with their reasons.

The schema pins this value as a ``const``, mirroring the ground-truth schema, so the two
cannot drift apart unnoticed.
"""
