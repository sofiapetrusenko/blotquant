"""The display-layer verdict mapping, pinned class by class and edge case by edge case.

Ruled by Sofia 2026-08-25 and pre-registered in
``docs/PRE_REGISTRATION_2026-08-25_verdict_mapping.md``. Every test here names the clause or
the edge case it pins, so that a change to the mapping fails against the ruling rather than
against a number somebody once observed.

The documents are built by hand rather than produced by a run, because the ruling is that a
verdict is recoverable from a stored document **alone**: a test that needed the pipeline to
produce its input would not be testing that.
"""

from __future__ import annotations

from typing import Any

import pytest

from api.display import (
    ALL_RATIOS_EXCLUDED,
    BLOCKED,
    BLOCKED_REASONS,
    FLAGGED,
    NO_RATIO_EMITTED,
    PASS,
    VERDICTS,
    lane_verdicts,
)
from api.errors import DisplayError
from pipeline.qc import BAND_QC_FLAGS, OVERLAPPING, SATURATED, UNRESOLVED_SHOULDER


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


def _lane(lane_id: str, roi_source: str = "caller") -> dict[str, Any]:
    """Return a ``lanes[]`` entry carrying only what the verdict reads."""
    return {"lane_id": lane_id, "roi_source": roi_source}


def _clean_document() -> dict[str, Any]:
    """Return a one-lane document whose lane passes: two bands, two ratios, no flag anywhere."""
    return _document(
        [_lane("L0")],
        [_band("L0_B0", "L0"), _band("L0_B1", "L0")],
        [_ratio("L0", "L0_B0"), _ratio("L0", "L0_B1")],
    )


# --------------------------------------------------------------------------------------
# The three ruled classes.
# --------------------------------------------------------------------------------------


def test_pass_is_no_qc_flags_and_every_ratio_computed() -> None:
    """Ruling 1, clause 1: *"pass = no QC flags and every ratio computed"*."""
    (verdict,) = lane_verdicts(_clean_document())
    assert verdict.verdict == PASS
    assert verdict.qc_flags == ()
    assert verdict.blocked_reason is None
    assert (verdict.ratio_count, verdict.usable_ratio_count) == (2, 2)


def test_flagged_is_flags_present_with_the_number_still_reported() -> None:
    """Ruling 1, clause 2: *"flagged = QC flags present, number reported and annotated"*."""
    document = _document(
        [_lane("L0")],
        [_band("L0_B0", "L0", (SATURATED,)), _band("L0_B1", "L0")],
        [_ratio("L0", "L0_B0", excluded=True, flags=(SATURATED,)), _ratio("L0", "L0_B1")],
    )
    (verdict,) = lane_verdicts(document)
    assert verdict.verdict == FLAGGED
    assert verdict.qc_flags == (SATURATED,)
    assert verdict.blocked_reason is None
    # The surviving number is what separates this from blocked, so it is asserted, not implied.
    assert (verdict.ratio_count, verdict.usable_ratio_count) == (2, 1)


def test_blocked_is_no_number_because_its_input_was_excluded() -> None:
    """Ruling 1, clause 3: *"blocked = no number, because the input to it was excluded"*."""
    document = _document(
        [_lane("L0")],
        [_band("L0_B0", "L0", (SATURATED,)), _band("L0_B1", "L0", (SATURATED,))],
        [
            _ratio("L0", "L0_B0", excluded=True, flags=(SATURATED,)),
            _ratio("L0", "L0_B1", excluded=True, flags=(SATURATED,)),
        ],
    )
    (verdict,) = lane_verdicts(document)
    assert verdict.verdict == BLOCKED
    assert verdict.blocked_reason == ALL_RATIOS_EXCLUDED
    assert verdict.usable_ratio_count == 0


def test_the_vocabularies_are_closed() -> None:
    """Three verdicts and two blocked reasons, because that is what was ruled."""
    assert VERDICTS == (PASS, FLAGGED, BLOCKED)
    assert BLOCKED_REASONS == (ALL_RATIOS_EXCLUDED, NO_RATIO_EMITTED)


# --------------------------------------------------------------------------------------
# The mutation test the ruling requires: a changed flag changes the verdict.
# --------------------------------------------------------------------------------------


def test_mutating_one_band_flag_walks_the_verdict_pass_to_flagged_to_blocked() -> None:
    """One document, mutated twice, must produce all three verdicts.

    This is the test that a verdict cannot be reached by a code path that ignores the flags:
    nothing changes between the three assertions except the flags and the exclusions they
    justify, and the verdict changes every time.
    """
    document = _clean_document()
    (before,) = lane_verdicts(document)
    assert before.verdict == PASS

    # One flag on one band, and the ratio it excludes. The other number still stands.
    document["bands"][0]["qc_flags"] = [SATURATED]
    document["normalization"]["ratios"][0]["excluded"] = True
    document["normalization"]["ratios"][0]["qc_flags"] = [SATURATED]
    (mutated,) = lane_verdicts(document)
    assert mutated.verdict == FLAGGED
    assert mutated.qc_flags == (SATURATED,)

    # Now the second number goes too, and nothing survives.
    document["bands"][1]["qc_flags"] = [OVERLAPPING]
    document["normalization"]["ratios"][1]["excluded"] = True
    document["normalization"]["ratios"][1]["qc_flags"] = [OVERLAPPING]
    (blocked,) = lane_verdicts(document)
    assert blocked.verdict == BLOCKED
    assert blocked.blocked_reason == ALL_RATIOS_EXCLUDED
    assert blocked.qc_flags == (SATURATED, OVERLAPPING)


def test_removing_the_only_flag_returns_the_lane_to_pass() -> None:
    """The mutation runs both ways: the verdict follows the flags rather than remembering."""
    document = _document(
        [_lane("L0")],
        [_band("L0_B0", "L0", (UNRESOLVED_SHOULDER,))],
        [_ratio("L0", "L0_B0", flags=(UNRESOLVED_SHOULDER,))],
    )
    assert lane_verdicts(document)[0].verdict == FLAGGED
    document["bands"][0]["qc_flags"] = []
    document["normalization"]["ratios"][0]["qc_flags"] = []
    assert lane_verdicts(document)[0].verdict == PASS


# --------------------------------------------------------------------------------------
# Edge cases E1-E8 of the pre-registration.
# --------------------------------------------------------------------------------------


def test_e1_a_lane_with_no_bands_is_blocked_with_no_ratio_emitted() -> None:
    """E1: no number, but nothing was excluded, so the reason distinguishes it."""
    (verdict,) = lane_verdicts(_document([_lane("L0")], [], []))
    assert verdict.verdict == BLOCKED
    assert verdict.blocked_reason == NO_RATIO_EMITTED
    assert (verdict.band_count, verdict.ratio_count) == (0, 0)


def test_e2_a_lane_level_problem_blocks_without_any_qc_flag() -> None:
    """E2: a non-positive denominator excludes every band, and no QC flag is involved."""
    document = _document(
        [_lane("L0")],
        [_band("L0_B0", "L0"), _band("L0_B1", "L0")],
        [_ratio("L0", "L0_B0", excluded=True), _ratio("L0", "L0_B1", excluded=True)],
    )
    (verdict,) = lane_verdicts(document)
    assert verdict.verdict == BLOCKED
    assert verdict.blocked_reason == ALL_RATIOS_EXCLUDED
    # Blocked with an empty flag list: the cause is the lane, not QC. Rule 1 fires before the
    # flag test, which is what makes this lane blocked rather than pass.
    assert verdict.qc_flags == ()


def test_e3_the_exclude_override_leaves_a_flagged_lane_flagged() -> None:
    """E3: with ``exclude_qc_flagged`` off, flagged bands keep their numbers and read flagged."""
    document = _document(
        [_lane("L0")],
        [_band("L0_B0", "L0", (SATURATED,))],
        [_ratio("L0", "L0_B0", excluded=False, flags=(SATURATED,))],
    )
    (verdict,) = lane_verdicts(document)
    assert verdict.verdict == FLAGGED
    assert verdict.usable_ratio_count == 1


def test_e4_a_flagged_reference_denominator_flags_the_lane_through_unflagged_numerators() -> None:
    """E4: the worst failure this mapping could have, pinned so it cannot return.

    Every band in the lane is clean; the denominator is not. Reading only ``bands[].qc_flags``
    would call this ``pass`` while every number in it was divided by a flagged reference.
    """
    document = _document(
        [_lane("L0")],
        [_band("L0_B0", "L0")],
        [_ratio("L0", "L0_B0", reference_flags=(SATURATED,))],
    )
    (verdict,) = lane_verdicts(document)
    assert verdict.verdict == FLAGGED
    assert verdict.qc_flags == (SATURATED,)


def test_e5_a_lane_of_references_only_emits_no_ratio_and_is_blocked() -> None:
    """E5: references are skipped as numerators, so the lane produces no number at all."""
    document = _document([_lane("L0")], [_band("L0_B0", "L0")], [])
    (verdict,) = lane_verdicts(document)
    assert verdict.verdict == BLOCKED
    assert verdict.blocked_reason == NO_RATIO_EMITTED
    assert verdict.band_count == 1


def test_e6_a_band_naming_an_unlisted_lane_raises() -> None:
    """E6: a document that cannot be read back as one this service wrote fails loudly."""
    document = _document([_lane("L0")], [_band("L1_B0", "L1")], [])
    with pytest.raises(DisplayError, match="not among the document's lanes"):
        lane_verdicts(document)


def test_e6_a_ratio_naming_an_unlisted_lane_raises() -> None:
    """E6, the other half: the same check on the ratios."""
    document = _document([_lane("L0")], [], [_ratio("L9", "L9_B0")])
    with pytest.raises(DisplayError, match="not among the document's lanes"):
        lane_verdicts(document)


@pytest.mark.parametrize("missing", ["lanes", "bands", "normalization"])
def test_e6_a_missing_top_level_key_raises_rather_than_defaulting(missing: str) -> None:
    """E6: no placeholder defaults. A missing key is a damaged document, not an empty one."""
    document = _clean_document()
    del document[missing]
    with pytest.raises(DisplayError, match=missing):
        lane_verdicts(document)


def test_e6_a_band_without_qc_flags_raises_rather_than_reading_as_clean() -> None:
    """E6: ``qc_flags`` is required of a band, and a silent default would read ``pass``."""
    document = _clean_document()
    del document["bands"][0]["qc_flags"]
    with pytest.raises(DisplayError, match="qc_flags"):
        lane_verdicts(document)


def test_e7_image_saturation_does_not_touch_a_clean_lane() -> None:
    """E7, and Ruling 3: a clean lane inside a saturated image is a pass card.

    This is the corpus's situation -- every crop carries the flag -- and the verdict must not
    read it, because reading it would give image saturation a blocking role Gate 1 ruling 3
    forbids building after the fact.
    """
    document = _clean_document()
    document["image_qc_flags"] = [SATURATED]
    (verdict,) = lane_verdicts(document)
    assert verdict.verdict == PASS
    assert verdict.qc_flags == ()


@pytest.mark.parametrize(
    "image_flags",
    [(SATURATED,), ("lossy_format",), ("low_dynamic_range",), (SATURATED, "lossy_format")],
)
def test_e8_no_image_flag_can_change_any_verdict(image_flags: tuple[str, ...]) -> None:
    """E7/E8 together: the verdict is invariant under every image flag, present or absent."""
    baseline = lane_verdicts(_clean_document())
    document = _clean_document()
    document["image_qc_flags"] = list(image_flags)
    assert lane_verdicts(document) == baseline


# --------------------------------------------------------------------------------------
# Shape, ordering and the record the verdict carries.
# --------------------------------------------------------------------------------------


def test_verdicts_come_back_in_the_documents_lane_order() -> None:
    """One verdict per lane, in document order, so a card list can be built by zipping."""
    document = _document(
        [_lane("L2"), _lane("L0"), _lane("L1")],
        [_band("L0_B0", "L0"), _band("L1_B0", "L1", (SATURATED,))],
        [_ratio("L0", "L0_B0"), _ratio("L1", "L1_B0", flags=(SATURATED,))],
    )
    verdicts = lane_verdicts(document)
    assert [verdict.lane_id for verdict in verdicts] == ["L2", "L0", "L1"]
    assert [verdict.verdict for verdict in verdicts] == [BLOCKED, PASS, FLAGGED]


def test_flags_are_reported_in_the_band_vocabulary_order() -> None:
    """The same order as every other flag list in the project, whatever order they arrive in."""
    document = _document(
        [_lane("L0")],
        [
            _band("L0_B0", "L0", (UNRESOLVED_SHOULDER, OVERLAPPING)),
            _band("L0_B1", "L0", (SATURATED, OVERLAPPING)),
        ],
        [_ratio("L0", "L0_B0"), _ratio("L0", "L0_B1", excluded=True)],
    )
    (verdict,) = lane_verdicts(document)
    assert verdict.qc_flags == BAND_QC_FLAGS


def test_an_unknown_flag_sorts_after_the_known_ones_rather_than_raising() -> None:
    """A document written by a later vocabulary stays legible to this reader."""
    document = _document(
        [_lane("L0")],
        [_band("L0_B0", "L0", ("zzz_future_flag", SATURATED))],
        [_ratio("L0", "L0_B0")],
    )
    (verdict,) = lane_verdicts(document)
    assert verdict.qc_flags == (SATURATED, "zzz_future_flag")
    assert verdict.verdict == FLAGGED


def test_as_dict_omits_blocked_reason_unless_the_lane_is_blocked() -> None:
    """A ``blocked_reason: null`` would invite a reader to look for a cause that is not there."""
    document = _document(
        [_lane("L0"), _lane("L1")],
        [_band("L0_B0", "L0")],
        [_ratio("L0", "L0_B0")],
    )
    passing, blocked = (verdict.as_dict() for verdict in lane_verdicts(document))
    assert "blocked_reason" not in passing
    assert passing["verdict"] == PASS
    assert blocked["blocked_reason"] == NO_RATIO_EMITTED


def test_the_verdict_carries_the_counts_it_was_decided_on() -> None:
    """The observations travel beside the decision, as they do on ``pipeline.qc.BandQc``."""
    document = _document(
        [_lane("L0", roi_source="detected")],
        [_band("L0_B0", "L0", (SATURATED,)), _band("L0_B1", "L0"), _band("L0_B2", "L0")],
        [
            _ratio("L0", "L0_B0", excluded=True, flags=(SATURATED,)),
            _ratio("L0", "L0_B1"),
            _ratio("L0", "L0_B2"),
        ],
    )
    (verdict,) = lane_verdicts(document)
    assert verdict.as_dict() == {
        "lane_id": "L0",
        "roi_source": "detected",
        "verdict": FLAGGED,
        "qc_flags": [SATURATED],
        "band_count": 3,
        "ratio_count": 3,
        "usable_ratio_count": 2,
    }


def test_the_derivation_does_not_mutate_the_document_it_reads() -> None:
    """A display derivation that edited the measurement record would be the ruling's opposite."""
    document = _clean_document()
    before = repr(document)
    lane_verdicts(document)
    assert repr(document) == before
