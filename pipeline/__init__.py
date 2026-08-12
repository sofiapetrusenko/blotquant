"""blotquant processing pipeline.

Anti-circularity invariant (PLAN.md): nothing in this package imports from
:mod:`synth`, reads ``data/ground_truth/``, or special-cases any property of the
synthetic generator. Every processing parameter arrives through
:class:`pipeline.config.PipelineConfig` and is echoed into result provenance.
"""

from __future__ import annotations

PIPELINE_VERSION = "0.1.0"
"""Version recorded as ``provenance.software_version`` in every result."""

RESULT_SCHEMA_VERSION = "1.0.0"
"""Version of ``schema/result.schema.json`` this pipeline targets.

Phase 1 emits a documented strict subset of that schema -- see NOTES.md, "The Phase 1
result is a strict subset of the result schema, and says so".
"""
