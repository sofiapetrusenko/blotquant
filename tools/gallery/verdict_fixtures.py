"""Generate the fixtures that pin ``web/lib/verdict.ts`` to ``api/display.py``.

``python -m tools.gallery.verdict_fixtures`` writes them; ``--check`` fails if the committed
tree has drifted from what the current ``api/display.py`` produces.

**Why this file exists.** The ruled verdict mapping
(``docs/PRE_REGISTRATION_2026-08-25_verdict_mapping.md``, Ruling 1) is derived in
:func:`api.display.lane_verdicts`. Phase 4b-web computes it a second time, in TypeScript, so the
live ``POST /analyze`` path can show a verdict for a result that was never stored -- the API does
not serve one, and §5 of the pre-registration leaves that to a later ruling. Two implementations
of one ruled mapping diverge silently: the flag order drifts, an edge case is read differently,
and nothing fails. So they are pinned to each other. Every ``expected`` block below is produced
by calling the **Python** function and :meth:`api.display.LaneVerdict.as_dict`; none is written
by hand. ``web/test/verdict.test.ts`` asserts the TypeScript reproduces every one of them, and
``tests/test_gallery_verdict_fixtures.py`` runs ``--check`` so a change to ``api/display.py``
that is not reflected here fails ``pytest``.

**What is covered.** Every stored gallery document under ``web/public/gallery/*/result.json``,
so the mirror is tested on real measurements; and synthetic documents covering the three ruled
classes, both ``blocked_reason`` values, edge cases E1--E8, the ruling's mutation walk, and the
flag-ordering rules. Edge case E6 is *ruled: raise*, so its fixtures live under ``errors/``
beside the other raising cases, each written only after the Python function has been observed
to raise :class:`api.errors.DisplayError` on it.

**Determinism.** Stable key order, sorted file emission, ``indent=2``, trailing newline. The
generator is re-runnable to a byte-identical tree, which is what makes ``--check`` meaningful.

The document builders below deliberately match the shapes in ``tests/test_api_verdict.py``, so
the two agree about what a minimal document looks like. This module does not import from
``tests/``: a generator whose output depended on the test suite would make the suite an input to
the thing it checks.
"""

from __future__ import annotations

import argparse
import copy
import filecmp
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from api.display import BAND_QC_FLAGS, lane_verdicts
from api.errors import DisplayError
from pipeline.qc import OVERLAPPING, SATURATED, UNRESOLVED_SHOULDER

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
"""Repository root; every default path below is resolved against it, not against the cwd."""

FIXTURE_ROOT = REPO_ROOT / "web" / "test" / "fixtures" / "verdict"
"""Where the fixtures are written. ``web/test/verdict.test.ts`` globs this directory."""

ERROR_SUBDIR = "errors"
"""Subdirectory holding the documents the derivation must refuse, not verdicts it produces."""

GALLERY_ROOT = REPO_ROOT / "web" / "public" / "gallery"
"""The committed gallery. Its stored documents become fixtures; see :func:`_gallery_cases`."""

RESULT_NAME = "result.json"

DIVERGENCE_NAME = "divergences.json"
"""Fixture recording, by observation, what the Python does with a schema-invalid document.

**Why this is generated rather than written down.** `NOTES.md`'s list of deliberate divergences
between `api/display.py` and `web/lib/verdict.ts` was hand-authored and was wrong on three
successive reviews -- claiming two divergences when there were three, four when there were more,
and describing the Python as raising on inputs where it in fact returns a confident verdict. A
prose list of what another implementation does is a claim, and this project's own rule is that
claims about behaviour are observations.

So each case below is run through the **Python**, its actual outcome is recorded -- returns with
these verdicts, or raises this exception class -- and `web/test/verdict.test.ts` asserts the
TypeScript refuses every one. The structural difference the table documents is that
`api/display.py` checks key *presence* and then iterates, while the mirror checks the types
`schema/result.schema.json` declares; the table is what that difference amounts to, case by case,
and it cannot go stale because nothing here states it.
"""

VOCABULARY_NAME = "vocabulary.json"
"""Fixture holding the band QC vocabulary, in order, as ``api/display.py`` imports it.

**The one thing the case fixtures cannot pin.** Every other fixture is a document plus the
verdict the Python produced for it, which pins the *mapping*. The vocabulary is an input to the
mapping rather than an output of it: ``_ordered_flags`` reports the flags a lane carries in
``BAND_QC_FLAGS`` order, so a flag added to ``pipeline/qc.py`` takes a vocabulary position in the
Python immediately, while ``web/lib/verdict.ts`` -- which retypes the tuple rather than importing
it -- keeps sorting it as an unknown, last. No fixture would contain the new flag, so every check
in this project would stay green while the two implementations ordered flags differently in
production. This fixture closes that: it is written from the Python's own tuple, and
``web/test/verdict.test.ts`` deep-equals the TypeScript constant against it, order included.
"""

UNKNOWN_FLAG = "zzz_future_flag"
"""A flag outside the current vocabulary, used to pin that unknowns sort after the known ones.

Named ``zzz_`` so that a lexicographic sort would place it last *anyway*: the fixture that
matters is the one below pairing it with a known flag, where vocabulary order and alphabetical
order disagree.
"""


@dataclass(frozen=True)
class Case:
    """One fixture: a name, what it pins, and the document the derivation is run over."""

    name: str
    why: str
    document: dict[str, Any]


def _band(band_id: str, lane_id: str, flags: tuple[str, ...] = ()) -> dict[str, Any]:
    """Return a ``bands[]`` entry carrying only what the verdict reads."""
    return {"band_id": band_id, "lane_id": lane_id, "qc_flags": list(flags)}


def _ratio(
    lane_id: str,
    numerator: str,
    *,
    excluded: bool = False,
    flags: tuple[str, ...] = (),
    reference_flags: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return a ``normalization.ratios[]`` entry carrying only what the verdict reads."""
    return {
        "lane_id": lane_id,
        "numerator_band_id": numerator,
        "excluded": excluded,
        "qc_flags": list(flags),
        "reference_qc_flags": list(reference_flags),
    }


def _lane(lane_id: str, roi_source: str = "caller") -> dict[str, Any]:
    """Return a ``lanes[]`` entry carrying only what the verdict reads."""
    return {"lane_id": lane_id, "roi_source": roi_source}


def _document(
    lanes: list[dict[str, Any]],
    bands: list[dict[str, Any]],
    ratios: list[dict[str, Any]],
    image_flags: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return the fragment of a result document the verdict derivation reads."""
    return {
        "lanes": lanes,
        "bands": bands,
        "normalization": {"ratios": ratios},
        "image_qc_flags": list(image_flags),
    }


def _clean_document() -> dict[str, Any]:
    """Return a one-lane document whose lane passes: two bands, two ratios, no flag anywhere."""
    return _document(
        [_lane("L0")],
        [_band("L0_B0", "L0"), _band("L0_B1", "L0")],
        [_ratio("L0", "L0_B0"), _ratio("L0", "L0_B1")],
    )


def _mutation_walk() -> tuple[Case, Case, Case]:
    """Return the ruling's mutation walk as three fixtures over one progressively edited document.

    §5 of the pre-registration requires a test in which a passing lane, mutated by adding one QC
    flag, becomes flagged, and mutated again so no ratio survives, becomes blocked. Pinning all
    three stages as fixtures makes the TypeScript mirror carry the same obligation: a mirror that
    ignored the flags could reproduce any one stage and not the walk.
    """
    document = _clean_document()
    first = Case(
        name="mutation_walk_1_pass",
        why="the walk's starting point: no flag anywhere, every ratio usable, so the lane passes",
        document=copy.deepcopy(document),
    )
    document["bands"][0]["qc_flags"] = [SATURATED]
    document["normalization"]["ratios"][0]["excluded"] = True
    document["normalization"]["ratios"][0]["qc_flags"] = [SATURATED]
    second = Case(
        name="mutation_walk_2_flagged",
        why=(
            "one flag on one band and the ratio it excludes; the other number still stands, so "
            "the lane is flagged rather than blocked"
        ),
        document=copy.deepcopy(document),
    )
    document["bands"][1]["qc_flags"] = [OVERLAPPING]
    document["normalization"]["ratios"][1]["excluded"] = True
    document["normalization"]["ratios"][1]["qc_flags"] = [OVERLAPPING]
    third = Case(
        name="mutation_walk_3_blocked",
        why=(
            "the second number goes too and nothing survives; blocked with both flags, in "
            "vocabulary order"
        ),
        document=copy.deepcopy(document),
    )
    return first, second, third


def _synthetic_cases() -> tuple[Case, ...]:
    """Return every synthetic fixture, in a fixed order.

    Covers the three ruled classes, both ``blocked_reason`` values, edge cases E1--E5, E7 and E8
    (E6 is ruled *raise* and lives in :func:`_error_cases`), the mutation walk, the flag-ordering
    rules, and the document's lane order.
    """
    walk = _mutation_walk()
    return (
        Case(
            name="class_pass",
            why=(
                'Ruling 1 clause 1: "pass = no QC flags and every ratio computed". Rule 2 of '
                "the mapping"
            ),
            document=_clean_document(),
        ),
        Case(
            name="class_flagged",
            why=(
                'Ruling 1 clause 2: "flagged = QC flags present, number reported and '
                'annotated". One ratio is excluded, one survives'
            ),
            document=_document(
                [_lane("L0")],
                [_band("L0_B0", "L0", (SATURATED,)), _band("L0_B1", "L0")],
                [
                    _ratio("L0", "L0_B0", excluded=True, flags=(SATURATED,)),
                    _ratio("L0", "L0_B1"),
                ],
            ),
        ),
        Case(
            name="class_blocked_all_ratios_excluded",
            why=(
                'Ruling 1 clause 3: "blocked = no number, because the input to it was '
                'excluded". Ratios exist and every one is excluded'
            ),
            document=_document(
                [_lane("L0")],
                [_band("L0_B0", "L0", (SATURATED,)), _band("L0_B1", "L0", (SATURATED,))],
                [
                    _ratio("L0", "L0_B0", excluded=True, flags=(SATURATED,)),
                    _ratio("L0", "L0_B1", excluded=True, flags=(SATURATED,)),
                ],
            ),
        ),
        Case(
            name="class_blocked_no_ratio_emitted",
            why=(
                "the other blocked reason: the lane emitted no ratio at all, so the ruled "
                '"because the input was excluded" clause does not hold and the reason says so'
            ),
            document=_document([_lane("L0")], [_band("L0_B0", "L0")], []),
        ),
        Case(
            name="e1_a_lane_with_no_bands",
            why=(
                "E1: a lane with no detected bands emits no ratio; blocked with "
                "no_ratio_emitted, and the band and ratio counts say why"
            ),
            document=_document([_lane("L0")], [], []),
        ),
        Case(
            name="e2_lane_level_problem_blocks_without_a_flag",
            why=(
                "E2: a lane-level cause (no designated reference, or a non-positive "
                "denominator) excludes every ratio while no QC flag is involved. Rule 1 fires "
                "before the flag test, so the lane is blocked with an empty flag list"
            ),
            document=_document(
                [_lane("L0")],
                [_band("L0_B0", "L0"), _band("L0_B1", "L0")],
                [
                    _ratio("L0", "L0_B0", excluded=True),
                    _ratio("L0", "L0_B1", excluded=True),
                ],
            ),
        ),
        Case(
            name="e3_exclude_qc_flagged_false_keeps_the_number",
            why=(
                "E3: with exclude_qc_flagged off a flagged band keeps its number, so the lane "
                "has a flag and a usable ratio and reads flagged"
            ),
            document=_document(
                [_lane("L0")],
                [_band("L0_B0", "L0", (SATURATED,))],
                [_ratio("L0", "L0_B0", excluded=False, flags=(SATURATED,))],
            ),
        ),
        Case(
            name="e4_flagged_reference_denominator",
            why=(
                "E4, the worst failure this mapping could have: every band is clean and the "
                "denominator is not. A mirror reading only bands[].qc_flags would call this "
                "pass while every number in it was divided by a flagged reference"
            ),
            document=_document(
                [_lane("L0")],
                [_band("L0_B0", "L0")],
                [_ratio("L0", "L0_B0", reference_flags=(SATURATED,))],
            ),
        ),
        Case(
            name="e5_a_lane_of_references_only",
            why=(
                "E5: references are skipped as numerators under a housekeeping mode, so a lane "
                "holding only references emits no ratio and falls to E1's reason"
            ),
            document=_document([_lane("L0")], [_band("L0_B0", "L0")], []),
        ),
        Case(
            name="e7_image_saturation_does_not_touch_a_clean_lane",
            why=(
                "E7 and Ruling 3: image_qc_flags is not an input. A clean lane inside a "
                "saturated image is a pass card, and a mirror that read the image block would "
                "produce a different verdict here"
            ),
            document=_document(
                [_lane("L0")],
                [_band("L0_B0", "L0"), _band("L0_B1", "L0")],
                [_ratio("L0", "L0_B0"), _ratio("L0", "L0_B1")],
                image_flags=(SATURATED,),
            ),
        ),
        Case(
            name="e8_low_dynamic_range_has_no_effect",
            why=(
                "E8: the same for the other image flags. Identical to the pass fixture except "
                "for image_qc_flags, so the expected verdicts must be identical too"
            ),
            document=_document(
                [_lane("L0")],
                [_band("L0_B0", "L0"), _band("L0_B1", "L0")],
                [_ratio("L0", "L0_B0"), _ratio("L0", "L0_B1")],
                image_flags=("low_dynamic_range", "lossy_format"),
            ),
        ),
        walk[0],
        walk[1],
        walk[2],
        Case(
            name="ordering_flags_follow_the_vocabulary",
            why=(
                "flags are reported in BAND_QC_FLAGS order (saturated, overlapping, "
                "unresolved_shoulder) whatever order they arrive in, and duplicates collapse. "
                "Alphabetical order would give a different list, which is what makes this a "
                "test rather than a coincidence"
            ),
            document=_document(
                [_lane("L0")],
                [
                    _band("L0_B0", "L0", (UNRESOLVED_SHOULDER, OVERLAPPING)),
                    _band("L0_B1", "L0", (SATURATED, OVERLAPPING)),
                ],
                [_ratio("L0", "L0_B0"), _ratio("L0", "L0_B1", excluded=True)],
            ),
        ),
        Case(
            name="ordering_unknown_flag_sorts_last",
            why=(
                "a document written by a later vocabulary stays legible: an unrecognised flag "
                "is sorted after every known one rather than refused, and it still flags"
            ),
            document=_document(
                [_lane("L0")],
                [_band("L0_B0", "L0", (UNKNOWN_FLAG, SATURATED))],
                [_ratio("L0", "L0_B0")],
            ),
        ),
        Case(
            name="ordering_verdicts_follow_the_documents_lane_order",
            why=(
                "one verdict per lane, in the document's lanes order and not sorted by id or "
                "grouped by verdict, so a card list can be built by zipping. All three classes "
                "appear, out of alphabetical order"
            ),
            document=_document(
                [_lane("L2"), _lane("L0"), _lane("L1")],
                [_band("L0_B0", "L0"), _band("L1_B0", "L1", (SATURATED,))],
                [_ratio("L0", "L0_B0"), _ratio("L1", "L1_B0", flags=(SATURATED,))],
            ),
        ),
        Case(
            name="optional_ratio_flag_keys_are_absent",
            why=(
                "qc_flags and reference_qc_flags are optional on a ratio under "
                "schema/result.schema.json, unlike a band's, so an absent list is a legal "
                "document that reads as no flags. The mirror must read them the same way -- "
                "and must not extend that leniency to a band"
            ),
            document=_document(
                [_lane("L0")],
                [_band("L0_B0", "L0")],
                [{"lane_id": "L0", "numerator_band_id": "L0_B0", "excluded": False}],
            ),
        ),
    )


def _error_cases() -> tuple[Case, ...]:
    """Return every document the derivation must refuse, in a fixed order.

    E6 of the pre-registration -- *"a lane id in ratios or bands that is not in lanes ... not an
    edge case in the data; a corrupt document. Ruled: raise"* -- plus the missing-key cases the
    same ruling covers: *"Likewise a missing required key: no placeholder defaults."*
    """
    band_unlisted = _document([_lane("L0")], [_band("L1_B0", "L1")], [])
    ratio_unlisted = _document([_lane("L0")], [], [_ratio("L9", "L9_B0")])

    missing: list[Case] = []
    for key in ("lanes", "bands", "normalization"):
        document = _clean_document()
        del document[key]
        missing.append(
            Case(
                name=f"e6_missing_{key}",
                why=(
                    f"E6: {key!r} is required of a result document, and a silent default would "
                    f"turn a damaged document into a confident verdict"
                ),
                document=document,
            )
        )
    without_ratios = _clean_document()
    del without_ratios["normalization"]["ratios"]

    without_band_flags = _clean_document()
    del without_band_flags["bands"][0]["qc_flags"]

    without_roi_source = _clean_document()
    del without_roi_source["lanes"][0]["roi_source"]

    without_excluded = _clean_document()
    del without_excluded["normalization"]["ratios"][0]["excluded"]

    return (
        Case(
            name="e6_band_names_an_unlisted_lane",
            why=(
                "E6: a band naming a lane the document does not list is a corrupt document, "
                "not a lane to invent. Ruled: raise"
            ),
            document=band_unlisted,
        ),
        Case(
            name="e6_ratio_names_an_unlisted_lane",
            why="E6, the other half: the same check on the ratios",
            document=ratio_unlisted,
        ),
        *missing,
        Case(
            name="e6_missing_normalization_ratios",
            why=(
                "E6: the ratios list is required of the normalization block; reading it as "
                "empty would report every lane blocked with no_ratio_emitted"
            ),
            document=without_ratios,
        ),
        Case(
            name="e6_band_without_qc_flags",
            why=(
                "E6: qc_flags is required of a band, and a band whose flags quietly defaulted "
                "to empty would read pass -- the single most dangerous silent fallback here"
            ),
            document=without_band_flags,
        ),
        Case(
            name="e6_lane_without_roi_source",
            why=(
                "E6: roi_source is required of a lane and the verdict carries it, so a card "
                "cannot report a caller-chosen region as a detector output by omission"
            ),
            document=without_roi_source,
        ),
        Case(
            name="e6_ratio_without_excluded",
            why=(
                "E6: excluded is required of a ratio; it is the field rule 1 of the mapping is "
                "decided on, so a default would decide the verdict rather than read it"
            ),
            document=without_excluded,
        ),
    )


def _gallery_cases(gallery_root: Path) -> tuple[Case, ...]:
    """Return one fixture per stored gallery document, sorted by card id.

    The synthetic fixtures pin the mapping's rules; these pin it on documents the shipped
    service actually produced, so a mirror that agrees on hand-built fragments and disagrees on
    a real document is caught. An absent gallery directory is not an error -- the fixtures can
    be generated before the gallery is built -- but the count is always printed, because a
    silently empty source is the failure this whole mechanism exists to prevent.
    """
    if not gallery_root.is_dir():
        print(f"gallery: {gallery_root} does not exist; 0 stored document(s) used")
        return ()
    cases: list[Case] = []
    for directory in sorted(child for child in gallery_root.iterdir() if child.is_dir()):
        document_path = directory / RESULT_NAME
        if not document_path.is_file():
            raise FileNotFoundError(
                f"{directory} is a gallery card directory with no {RESULT_NAME}; rebuild the "
                f"gallery with 'python -m tools.gallery.build' or remove the directory"
            )
        cases.append(
            Case(
                name=f"gallery__{directory.name}",
                why=(
                    f"the stored gallery card {directory.name!r}, exactly as the service served "
                    f"it: the mirror is pinned on a real measurement and not only on hand-built "
                    f"document fragments"
                ),
                document=json.loads(document_path.read_text(encoding="utf-8")),
            )
        )
    print(f"gallery: {len(cases)} stored document(s) used from {gallery_root}")
    return tuple(cases)


def _write(path: Path, payload: dict[str, Any]) -> None:
    """Write one fixture as JSON at the payload's own key order, with a trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _divergence_cases() -> tuple[Case, ...]:
    """Documents the schema forbids, on which the two implementations do not agree.

    Every one is otherwise a clean single-lane `pass` document with exactly one field replaced,
    so the recorded outcome isolates that field. The shapes are grouped by the field they damage.

    **Each ``why`` describes the Python and nothing else.** Three of them originally described
    what the *TypeScript* does with the same document, and all three were wrong — the mirror
    refuses every case here. A note about another implementation inside a fixture that exists to
    record this one is the stale claim this file was built to stop writing.
    """

    def document(**patch: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "lanes": [{"lane_id": "L0", "roi_source": "caller"}],
            "bands": [{"band_id": "L0_B0", "lane_id": "L0", "qc_flags": []}],
            "normalization": {
                "ratios": [
                    {
                        "lane_id": "L0",
                        "numerator_band_id": "L0_B0",
                        "excluded": False,
                        "qc_flags": [],
                    }
                ]
            },
            "image_qc_flags": [],
        }
        base.update(patch)
        return base

    def ratio(**patch: Any) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "lane_id": "L0",
            "numerator_band_id": "L0_B0",
            "excluded": False,
            "qc_flags": [],
        }
        entry.update(patch)
        return document(normalization={"ratios": [entry]})

    def band(**patch: Any) -> dict[str, Any]:
        entry: dict[str, Any] = {"band_id": "L0_B0", "lane_id": "L0", "qc_flags": []}
        entry.update(patch)
        return document(bands=[entry])

    cases: list[Case] = []
    for name, why, doc in [
        # -- the three collections the derivation iterates ---------------------------------
        ("lanes_is_a_mapping", "an empty mapping iterates as empty", document(lanes={})),
        (
            "lanes_is_a_mapping_and_nothing_names_a_lane",
            "iterates as empty with no band or ratio to catch it, so a lane-less result "
            "surface is returned rather than refused",
            {"lanes": {}, "bands": [], "normalization": {"ratios": []}, "image_qc_flags": []},
        ),
        ("lanes_is_a_number", "not iterable at all", document(lanes=3)),
        ("lanes_is_a_string", "iterates as characters", document(lanes="L0")),
        ("lanes_holds_strings", "entries that are not mappings", document(lanes=["L0"])),
        ("bands_is_a_mapping", "iterates as empty, so the lane reads as having no bands",
         document(bands={})),
        ("bands_is_an_empty_string", "iterates as empty, so the lane reads as having no bands",
         document(bands="")),
        ("bands_is_a_nonempty_string", "iterates as characters, each of which is then indexed",
         document(bands="saturated")),
        ("ratios_is_a_mapping", "iterates as empty, so every lane looks like it emitted none",
         document(normalization={"ratios": {}})),
        ("ratios_is_a_nonempty_string", "iterates as characters, each of which is then indexed",
         document(normalization={"ratios": "abc"})),
        # -- the field rule 1 turns on -----------------------------------------------------
        ("excluded_is_an_empty_list", "falsy, so Python counts the ratio as a survivor",
         ratio(excluded=[])),
        ("excluded_is_an_empty_mapping", "falsy, so Python counts it as a survivor",
         ratio(excluded={})),
        ("excluded_is_an_empty_string", "falsy, so Python counts it as a survivor",
         ratio(excluded="")),
        ("excluded_is_zero", "falsy, so Python reads a number as a boolean", ratio(excluded=0)),
        ("excluded_is_null", "falsy, so Python reads an absent value as not-excluded",
         ratio(excluded=None)),
        ("excluded_is_the_string_false", "truthy, so Python excludes the ratio on the strength of "
         "a string that says otherwise", ratio(excluded="false")),
        # -- the ids the derivation groups by ----------------------------------------------
        ("lane_id_is_a_number", "groups under a different key type", document(
            lanes=[{"lane_id": 1, "roi_source": "caller"}],
            bands=[{"band_id": "B", "lane_id": 1, "qc_flags": []}],
            normalization={"ratios": []},
        )),
        ("lane_id_is_a_boolean", "groups under a key of a type the schema does not admit",
         document(
            lanes=[{"lane_id": True, "roi_source": "caller"}],
            bands=[{"band_id": "B", "lane_id": True, "qc_flags": []}],
            normalization={"ratios": []},
        )),
        (
            "lane_ids_one_and_true_merge",
            "Python hashes 1 and True to one key, so the single band attributed to lane 1 is "
            "counted for BOTH lanes -- each reports band_count 1 and carries its flags",
            {
                "lanes": [
                    {"lane_id": 1, "roi_source": "caller"},
                    {"lane_id": True, "roi_source": "caller"},
                ],
                "bands": [{"band_id": "B", "lane_id": 1, "qc_flags": ["saturated"]}],
                "normalization": {"ratios": []},
                "image_qc_flags": [],
            },
        ),
        ("roi_source_is_null", "passed through verbatim by the Python, into the verdict record",
         document(lanes=[{"lane_id": "L0", "roi_source": None}], bands=[],
                  normalization={"ratios": []})),
        ("roi_source_is_a_number", "the same", document(
            lanes=[{"lane_id": "L0", "roi_source": 3}], bands=[],
            normalization={"ratios": []})),
        # -- the flag lists ----------------------------------------------------------------
        ("band_flags_is_a_string", "extend() spreads it into characters",
         band(qc_flags="saturated")),
        ("band_flags_is_a_mapping", "extend() spreads it into keys",
         band(qc_flags={"saturated": 1})),
        ("band_flags_holds_numbers", "numbers enter the flag list", band(qc_flags=[1, 2])),
        ("band_flags_holds_null", "None enters the flag list", band(qc_flags=[None])),
        ("ratio_flags_is_a_string", "the same, on the ratio's own list",
         ratio(qc_flags="saturated")),
        ("ratio_reference_flags_is_a_string", "the same, on the reference list",
         ratio(reference_qc_flags="saturated")),
        ("ratio_flags_is_a_mapping", "the same", ratio(qc_flags={"saturated": 1})),
    ]:
        cases.append(Case(name=name, why=why, document=doc))
    return tuple(cases)


def _write_divergence_fixture(directory: Path) -> None:
    """Record what the Python actually does with each divergence case. Nothing is asserted here.

    A case whose recorded outcome is a refusal is not a divergence at all -- it is agreement --
    and it is written anyway, because the value of the table is that it is complete and observed
    rather than curated. ``web/test/verdict.test.ts`` reads the outcomes and asserts the
    TypeScript's behaviour against them.
    """
    recorded: list[dict[str, Any]] = []
    for case in _divergence_cases():
        try:
            verdicts = lane_verdicts(case.document)
        except Exception as error:  # noqa: BLE001 -- the class is the observation
            outcome: dict[str, Any] = {
                "outcome": "raises",
                "error_type": type(error).__name__,
                "refuses": isinstance(error, DisplayError),
            }
        else:
            outcome = {
                "outcome": "returns",
                "refuses": False,
                "verdicts": [verdict.as_dict() for verdict in verdicts],
            }
        recorded.append(
            {"name": case.name, "why": case.why, "document": case.document, "python": outcome}
        )
    _write(
        directory / DIVERGENCE_NAME,
        {
            "name": "divergences",
            "why": (
                "What api/display.py actually does with each schema-invalid document, observed "
                "by running it. web/lib/verdict.ts refuses all of them; this table is the "
                "record of what it is refusing that the Python does not, and it exists because "
                "a hand-written list of the same thing was wrong three times"
            ),
            "cases": recorded,
        },
    )


def _write_vocabulary_fixture(directory: Path) -> None:
    """Write the band QC vocabulary as the Python holds it, in its own order.

    Observed, not authored: the list is ``api.display.BAND_QC_FLAGS``, which is
    ``pipeline.qc.BAND_QC_FLAGS``. Nothing here restates the flag names.
    """
    _write(
        directory / VOCABULARY_NAME,
        {
            "name": "vocabulary",
            "why": (
                "The band QC vocabulary in the order api/display.py reports flags in, taken from "
                "pipeline.qc. web/lib/verdict.ts retypes this tuple instead of importing it, so "
                "without this fixture a flag added to the pipeline would take a vocabulary "
                "position in the Python and sort as an unknown in the TypeScript, with no test "
                "in the project able to see the difference"
            ),
            "band_qc_flags": list(BAND_QC_FLAGS),
        },
    )


def _write_verdict_fixture(directory: Path, case: Case) -> None:
    """Write one fixture whose ``expected`` is the Python derivation's own output.

    ``expected`` is never authored: it is :func:`api.display.lane_verdicts` over ``case.document``,
    rendered through :meth:`api.display.LaneVerdict.as_dict`.
    """
    expected = [verdict.as_dict() for verdict in lane_verdicts(case.document)]
    _write(
        directory / f"{case.name}.json",
        {"name": case.name, "why": case.why, "document": case.document, "expected": expected},
    )


def _write_error_fixture(directory: Path, case: Case) -> None:
    """Write one refusal fixture, but only after observing the Python function refuse.

    A fixture asserting "this must raise" that was never checked against the implementation
    would pin the TypeScript to a claim rather than to the Python, which is the one thing this
    generator exists not to do. If the derivation returns verdicts for a document listed here,
    the generator fails and writes nothing for it.
    """
    try:
        verdicts = lane_verdicts(case.document)
    except DisplayError:
        _write(
            directory / f"{case.name}.json",
            {
                "name": case.name,
                "why": case.why,
                "document": case.document,
                "expected_error": True,
            },
        )
        return
    raise AssertionError(
        f"error fixture {case.name!r} does not raise: api.display.lane_verdicts returned "
        f"{[verdict.as_dict() for verdict in verdicts]} for a document this generator lists as "
        f"one the derivation must refuse. Either the document no longer expresses the case, or "
        f"api/display.py has stopped refusing it -- both are findings, and neither is fixed by "
        f"writing the fixture anyway"
    )


def generate(fixture_root: Path, gallery_root: Path) -> tuple[int, int]:
    """Write every fixture under ``fixture_root``; return ``(verdict count, error count)``.

    Guarantees the emitted tree is a function of ``api/display.py``, the synthetic cases here
    and the stored gallery documents alone: no state carries over between runs, so the tree is
    byte-identical for identical inputs. Files under ``fixture_root`` that this run did not
    write are removed, so a renamed case cannot leave a stale fixture behind that the
    TypeScript test would still assert against.
    """
    error_root = fixture_root / ERROR_SUBDIR
    # Recursive, to match ``_tree``'s ``rglob``: a fixture in an unexpected subdirectory that
    # this sweep missed would survive every regeneration and then report as drift forever.
    if fixture_root.is_dir():
        for path in sorted(fixture_root.rglob("*.json")):
            path.unlink()
    fixture_root.mkdir(parents=True, exist_ok=True)
    error_root.mkdir(parents=True, exist_ok=True)

    _write_vocabulary_fixture(fixture_root)
    _write_divergence_fixture(fixture_root)
    cases = (*_synthetic_cases(), *_gallery_cases(gallery_root))
    for case in sorted(cases, key=lambda item: item.name):
        _write_verdict_fixture(fixture_root, case)
    errors = _error_cases()
    for case in sorted(errors, key=lambda item: item.name):
        _write_error_fixture(error_root, case)
    return len(cases), len(errors)


def _tree(root: Path) -> list[str]:
    """Return every fixture path under ``root``, relative and sorted, for comparison."""
    return sorted(str(path.relative_to(root)) for path in root.rglob("*.json"))


def check(fixture_root: Path, gallery_root: Path) -> list[str]:
    """Return a list of differences between the committed fixtures and a fresh generation.

    An empty list means the committed tree is exactly what the current ``api/display.py``
    produces. Anything else names the files that were added, removed or changed.
    """
    with tempfile.TemporaryDirectory(prefix="blotquant-verdict-fixtures-") as directory:
        fresh = Path(directory) / "verdict"
        generate(fresh, gallery_root)
        if not fixture_root.is_dir():
            return [
                f"no committed fixtures at {fixture_root}; run "
                f"'python -m tools.gallery.verdict_fixtures'"
            ]
        committed_paths = _tree(fixture_root)
        fresh_paths = _tree(fresh)
        differences = [
            f"stale, not produced by this api/display.py: {name}"
            for name in committed_paths
            if name not in fresh_paths
        ]
        differences.extend(
            f"missing from the committed tree: {name}"
            for name in fresh_paths
            if name not in committed_paths
        )
        differences.extend(
            f"content differs from what api/display.py produces: {name}"
            for name in fresh_paths
            if name in committed_paths
            and not filecmp.cmp(fixture_root / name, fresh / name, shallow=False)
        )
        return differences


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the fixture generator CLI."""
    parser = argparse.ArgumentParser(
        prog="python -m tools.gallery.verdict_fixtures",
        description=(
            "Generate the fixtures pinning web/lib/verdict.ts to api/display.py, or check the "
            "committed ones against it."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=FIXTURE_ROOT,
        help=f"fixture root (default: {FIXTURE_ROOT.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--gallery",
        type=Path,
        default=GALLERY_ROOT,
        help=f"gallery root to read stored documents from (default: "
        f"{GALLERY_ROOT.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit non-zero if the committed fixtures have drifted",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Generate or check the fixtures. Returns 0 on success, 1 on drift or a refusal failure."""
    args = build_parser().parse_args(argv)
    if args.check:
        differences = check(args.out, args.gallery)
        if differences:
            print(
                "error: the committed verdict fixtures no longer match api/display.py.\n"
                "Regenerate them with 'python -m tools.gallery.verdict_fixtures' and read the "
                "diff: a change here is a change to a ruled mapping.",
                file=sys.stderr,
            )
            for difference in differences:
                print(f"  {difference}", file=sys.stderr)
            return 1
        print(f"OK: the committed verdict fixtures match api/display.py ({args.out})")
        return 0
    verdicts, errors = generate(args.out, args.gallery)
    print(f"wrote {verdicts} verdict fixture(s) and {errors} refusal fixture(s) to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
