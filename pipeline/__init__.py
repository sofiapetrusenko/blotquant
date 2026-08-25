"""blotquant processing pipeline.

Anti-circularity invariant (PLAN.md): nothing in this package imports from
:mod:`synth`, reads ``data/ground_truth/``, or special-cases any property of the
synthetic generator. Every processing parameter arrives through
:class:`pipeline.config.PipelineConfig` and is echoed into result provenance.
"""

from __future__ import annotations

PIPELINE_VERSION = "0.1.0"
"""Version recorded as ``provenance.software_version`` in every result."""

RESULT_SCHEMA_VERSION = "1.4.0"
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

1.3.0 adds **two** *optional* fields. ``source.channel_collapse`` records that a multi-channel
input was collapsed to a single channel on the way in, and by which method and measured
divergence; ``normalization.reference_designation_source`` records where the caller's
reference-band designation came from. Optional rather than required, and in both cases the
distinction carries the meaning: an absent ``channel_collapse`` says the file was already
single-channel, and an absent designation source says the designation's origin was not
stated — neither of which is the same as a null, and both of which are true of every document
written before this version, so no existing writer becomes wrong.

``channel_collapse``'s shape is fixed by the ratified 2026-08-19 §7 amendment rather than chosen
here -- ``{method: green, max_divergence_dn}`` -- and ``method`` is a ``const`` and
``max_divergence_dn`` is capped at the ruled bound, so a document asserting a collapse the
amendment does not permit fails validation instead of being read as a variant. The version is
bumped rather than the fields slipped in silently because ``source`` and ``normalization`` are
both ``additionalProperties: false``: a 1.2.0 validator rejects a 1.3.0 document outright, which
is the honest outcome and not one to hide behind an unchanged version number. NOTES.md's Phase
3b-1 section records the bump with this reasoning.

1.4.0 adds one field, and it is *required*: ``source.polarity``, the signal polarity the caller
declared for the file. Required rather than optional, unlike 1.3.0's two, and the difference is
the whole point: an absent ``channel_collapse`` means the file was single-channel, which is
information, whereas an absent polarity would mean nobody said -- and a document measured without
a declared polarity is precisely what the 2026-08-24 amendment refuses to produce. There is no
document it can be absent from, because the loader will not return one. It is an ``enum`` of the
amendment's two values, so a third convention fails validation rather than being read as a
variant. Every 1.3.0 document lacks the field and does not validate here, which is the honest
outcome: those documents were measured under an assumption that is now a declaration.

The schema pins this value as a ``const``, mirroring the ground-truth schema, so the two
cannot drift apart unnoticed.
"""
