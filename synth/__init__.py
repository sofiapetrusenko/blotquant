"""Synthetic western-blot generator.

This package is the ONLY writer of ``data/ground_truth/``. It is FROZEN after the
Phase 0 merge: any authorised change must bump :data:`SYNTH_VERSION` and add a break
marker to ``evals/history.md``, because scores across the break are not comparable.
"""

from __future__ import annotations

SYNTH_VERSION = "1.0.0"
"""Freeze marker for the generator. Recorded in every ground-truth file."""

GROUND_TRUTH_SCHEMA_VERSION = "1.0.0"
"""Version of ``schema/ground_truth.schema.json`` that emitted files conform to."""

__all__ = ["GROUND_TRUTH_SCHEMA_VERSION", "SYNTH_VERSION"]
