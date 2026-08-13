"""Normalization: the three modes against hand-computed fixtures, and every failure mode.

PLAN.md requires the modes to be "verified against hand-computed fixtures", so every expected
ratio below is computed in the test from numbers chosen to make the arithmetic exact -- the
geometric mean of 400 and 900 is 600 because 400 * 900 = 360000 = 600**2 -- rather than
compared against whatever the implementation happens to return.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import pytest

from pipeline.config import (
    HOUSEKEEPING_MULTI,
    HOUSEKEEPING_SINGLE,
    TOTAL_PROTEIN,
    NormalizationConfig,
)
from pipeline.errors import NormalizationError
from pipeline.normalize import (
    LANE_DENOMINATOR_NOT_POSITIVE,
    LANE_WITHOUT_REFERENCE_BAND,
    QC_FLAGGED_BANDS_INCLUDED_BY_OVERRIDE,
    REFERENCE_BAND_QC_FLAGGED,
    SINGLE_HOUSEKEEPING_REFERENCE,
    WARNING_ORDER,
    NormalizationBand,
    NormalizationResult,
    normalize,
)
from pipeline.qc import LOSSY_FORMAT, OVERLAPPING, SATURATED, UNRESOLVED_SHOULDER

TOLERANCE = 1e-12
"""Ratios are two divisions of float64 values; nothing here needs a looser bound."""


def _config(mode: str, exclude: bool = True) -> NormalizationConfig:
    """Return a normalization config for ``mode``."""
    return NormalizationConfig(mode=mode, exclude_qc_flagged=exclude)


def _band(band_id: str, lane_id: str, intensity: float, *flags: str) -> NormalizationBand:
    """Return one band with the given flags."""
    return NormalizationBand(
        band_id=band_id, lane_id=lane_id, intensity=intensity, qc_flags=tuple(flags)
    )


def _normalize(
    bands: Sequence[NormalizationBand],
    lane_ids: Sequence[str],
    config: NormalizationConfig,
    **keywords: Any,
) -> NormalizationResult:
    """Call :func:`normalize` with ``lossy_format`` defaulted, which it does not do itself.

    ``normalize`` requires it, so that a caller cannot silently lose the lossy-container
    warning; a test that is not about that warning says so once, here.
    """
    keywords.setdefault("lossy_format", False)
    return normalize(bands, lane_ids, config, **keywords)


def test_housekeeping_single_divides_by_the_designated_band() -> None:
    """1200 / 400 = 3.0, and the single-reference warning is always recorded."""
    bands = [_band("t", "L0", 1200.0), _band("hk", "L0", 400.0)]

    result = _normalize(bands, ["L0"], _config(HOUSEKEEPING_SINGLE), reference_band_ids=["hk"])

    assert len(result.ratios) == 1
    ratio = result.ratios[0]
    assert ratio.numerator_band_id == "t"
    assert ratio.denominator_band_ids == ("hk",)
    assert ratio.ratio == pytest.approx(1200.0 / 400.0, abs=TOLERANCE)
    assert ratio.ratio == pytest.approx(3.0, abs=TOLERANCE)
    assert SINGLE_HOUSEKEEPING_REFERENCE in result.warnings
    assert result.reference_band_ids == ("hk",)
    assert ratio.as_dict()["denominator_band_id"] == "hk"


def test_housekeeping_multi_divides_by_the_geometric_mean() -> None:
    """sqrt(400 * 900) = 600 exactly, so 1500 / 600 = 2.5 -- and it is not the mean, 650."""
    bands = [
        _band("t", "L0", 1500.0),
        _band("hk1", "L0", 400.0),
        _band("hk2", "L0", 900.0),
    ]

    result = _normalize(
        bands, ["L0"], _config(HOUSEKEEPING_MULTI), reference_band_ids=["hk1", "hk2"]
    )

    geometric_mean = math.sqrt(400.0 * 900.0)
    assert geometric_mean == pytest.approx(600.0, abs=TOLERANCE)
    assert len(result.ratios) == 1
    assert result.ratios[0].ratio == pytest.approx(1500.0 / 600.0, abs=TOLERANCE)
    assert result.ratios[0].ratio == pytest.approx(2.5, abs=TOLERANCE)
    arithmetic_mean = 0.5 * (400.0 + 900.0)
    assert result.ratios[0].ratio != pytest.approx(1500.0 / arithmetic_mean, abs=1e-6)
    assert result.ratios[0].denominator_band_ids == ("hk1", "hk2")
    assert SINGLE_HOUSEKEEPING_REFERENCE not in result.warnings


def test_housekeeping_multi_geometric_mean_of_three_and_its_order_independence() -> None:
    """cbrt(200 * 400 * 800) = 400, so 1000 / 400 = 2.5, whatever order the ids arrive in."""
    bands = [
        _band("t", "L0", 1000.0),
        _band("a", "L0", 200.0),
        _band("b", "L0", 400.0),
        _band("c", "L0", 800.0),
    ]

    forward = _normalize(
        bands, ["L0"], _config(HOUSEKEEPING_MULTI), reference_band_ids=["a", "b", "c"]
    )
    reversed_order = _normalize(
        bands, ["L0"], _config(HOUSEKEEPING_MULTI), reference_band_ids=["c", "b", "a"]
    )

    assert (200.0 * 400.0 * 800.0) ** (1.0 / 3.0) == pytest.approx(400.0, abs=1e-9)
    assert forward.ratios[0].ratio == pytest.approx(1000.0 / 400.0, abs=TOLERANCE)
    assert forward.ratios[0].ratio == pytest.approx(2.5, abs=TOLERANCE)
    assert reversed_order.ratios[0].ratio == pytest.approx(forward.ratios[0].ratio, abs=TOLERANCE)


def test_total_protein_divides_by_the_lane_integral() -> None:
    """1250 / 5000 = 0.25, and each lane uses its own integral."""
    bands = [_band("a", "L0", 1250.0), _band("b", "L1", 600.0)]

    result = _normalize(
        bands,
        ["L0", "L1"],
        _config(TOTAL_PROTEIN),
        lane_total_protein={"L0": 5000.0, "L1": 2400.0},
    )

    assert [ratio.ratio for ratio in result.ratios] == [
        pytest.approx(0.25, abs=TOLERANCE),
        pytest.approx(0.25, abs=TOLERANCE),
    ]
    assert all(ratio.denominator_band_ids == () for ratio in result.ratios)
    assert "denominator_band_id" not in result.ratios[0].as_dict()
    assert result.lane_total_protein == {"L0": 5000.0, "L1": 2400.0}
    assert result.reference_band_ids is None


def test_total_protein_ratios_of_two_bands_recover_their_intensity_ratio() -> None:
    """The lane integral cancels: (a/T) / (b/T) is a / b, to float64 precision."""
    bands = [_band("a", "L0", 1200.0), _band("b", "L0", 400.0)]

    result = _normalize(
        bands, ["L0"], _config(TOTAL_PROTEIN), lane_total_protein={"L0": 7777.0}
    )

    first, second = result.ratios
    assert first.ratio / second.ratio == pytest.approx(1200.0 / 400.0, abs=TOLERANCE)


def test_a_flagged_band_is_reported_with_its_ratio_and_marked_excluded() -> None:
    """QC annotates, never drops: the ratio is computed, reported, and marked with a reason."""
    bands = [_band("t", "L0", 1200.0, SATURATED), _band("hk", "L0", 400.0)]

    result = _normalize(bands, ["L0"], _config(HOUSEKEEPING_SINGLE), reference_band_ids=["hk"])

    ratio = result.ratios[0]
    assert ratio.ratio == pytest.approx(3.0, abs=TOLERANCE)
    assert ratio.excluded is True
    assert ratio.exclusion_reason is not None
    assert SATURATED in ratio.exclusion_reason
    exclusion = result.exclusion_by_band_id()["t"]
    assert exclusion.excluded is True
    assert exclusion.reason == ratio.exclusion_reason
    assert QC_FLAGGED_BANDS_INCLUDED_BY_OVERRIDE not in result.warnings


def test_the_override_includes_flagged_bands_and_records_that_it_did() -> None:
    """``exclude_qc_flagged: false`` is explicit, and the result says the override was used."""
    bands = [_band("t", "L0", 1200.0, SATURATED), _band("hk", "L0", 400.0)]

    result = _normalize(
        bands,
        ["L0"],
        _config(HOUSEKEEPING_SINGLE, exclude=False),
        reference_band_ids=["hk"],
    )

    assert result.ratios[0].excluded is False
    assert result.ratios[0].exclusion_reason is None
    assert result.exclusion_by_band_id()["t"].excluded is False
    assert QC_FLAGGED_BANDS_INCLUDED_BY_OVERRIDE in result.warnings


def test_the_override_warning_is_absent_when_no_band_is_flagged() -> None:
    """The warning claims flagged bands were included; with none, that claim would be false."""
    bands = [_band("t", "L0", 1200.0), _band("hk", "L0", 400.0)]

    result = _normalize(
        bands, ["L0"], _config(HOUSEKEEPING_SINGLE, exclude=False), reference_band_ids=["hk"]
    )

    assert QC_FLAGGED_BANDS_INCLUDED_BY_OVERRIDE not in result.warnings


@pytest.mark.parametrize("flag", [SATURATED, OVERLAPPING, UNRESOLVED_SHOULDER])
def test_a_flagged_reference_normalizes_and_the_warning_names_the_flag(flag: str) -> None:
    """Human ruling: normalize and warn, with the flag named -- the caveats differ by flag."""
    bands = [_band("t", "L0", 1200.0), _band("hk", "L0", 400.0, flag)]

    result = _normalize(bands, ["L0"], _config(HOUSEKEEPING_SINGLE), reference_band_ids=["hk"])

    ratio = result.ratios[0]
    assert ratio.ratio == pytest.approx(3.0, abs=TOLERANCE), "the ratio is still produced"
    assert REFERENCE_BAND_QC_FLAGGED in result.warnings
    assert f"reference_band_{flag}" in result.warnings
    assert ratio.reference_qc_flagged is True
    assert ratio.reference_qc_flags == (flag,)
    assert ratio.qc_flags == (flag,)
    assert result.exclusion_by_band_id()["hk"].excluded is False, (
        "excluding the reference would delete every ratio in its lane"
    )


def test_a_lossy_image_warns_about_its_denominator_without_flagging_any_band() -> None:
    """``lossy_format`` is an image property: it warns, and stays out of the band vocabulary."""
    bands = [_band("t", "L0", 1200.0), _band("hk", "L0", 400.0)]

    result = _normalize(
        bands,
        ["L0"],
        _config(HOUSEKEEPING_SINGLE),
        reference_band_ids=["hk"],
        lossy_format=True,
    )

    assert f"reference_band_{LOSSY_FORMAT}" in result.warnings
    assert REFERENCE_BAND_QC_FLAGGED not in result.warnings, "no band flag was carried"
    assert result.ratios[0].reference_qc_flags == ()
    assert result.ratios[0].reference_qc_flagged is False


def test_total_protein_reports_the_lanes_own_flags_as_the_denominators_caveat() -> None:
    """The lane integral contains every band in the lane, so their flags qualify it."""
    bands = [_band("a", "L0", 1200.0, SATURATED), _band("b", "L0", 400.0)]

    result = _normalize(
        bands, ["L0"], _config(TOTAL_PROTEIN), lane_total_protein={"L0": 5000.0}
    )

    assert all(ratio.reference_qc_flagged for ratio in result.ratios)
    assert all(ratio.reference_qc_flags == (SATURATED,) for ratio in result.ratios)
    assert f"reference_band_{SATURATED}" in result.warnings


def test_a_lane_without_a_reference_produces_no_ratio_and_records_why() -> None:
    """Not a silent drop: a warning, and every band in the lane carries the reason."""
    bands = [
        _band("t0", "L0", 1200.0),
        _band("hk0", "L0", 400.0),
        _band("t1", "L1", 900.0),
    ]

    result = _normalize(
        bands, ["L0", "L1"], _config(HOUSEKEEPING_SINGLE), reference_band_ids=["hk0"]
    )

    assert [ratio.lane_id for ratio in result.ratios] == ["L0"]
    assert LANE_WITHOUT_REFERENCE_BAND in result.warnings
    exclusion = result.exclusion_by_band_id()["t1"]
    assert exclusion.excluded is True
    assert "no designated reference band" in str(exclusion.reason)


@pytest.mark.parametrize("denominator", [0.0, -1500.0])
def test_a_non_positive_denominator_is_recorded_rather_than_reported(denominator: float) -> None:
    """A ratio against a non-positive denominator is not a measurement, so none is emitted."""
    bands = [_band("a", "L0", 1200.0)]

    result = _normalize(
        bands, ["L0"], _config(TOTAL_PROTEIN), lane_total_protein={"L0": denominator}
    )

    assert result.ratios == ()
    assert LANE_DENOMINATOR_NOT_POSITIVE in result.warnings
    exclusion = result.exclusion_by_band_id()["a"]
    assert exclusion.excluded is True
    assert "not positive" in str(exclusion.reason)


def test_a_non_positive_reference_band_is_recorded_the_same_way() -> None:
    """Same treatment for a housekeeping reference that measured at or below zero."""
    bands = [_band("t", "L0", 1200.0), _band("hk", "L0", -20.0)]

    result = _normalize(bands, ["L0"], _config(HOUSEKEEPING_SINGLE), reference_band_ids=["hk"])

    assert result.ratios == ()
    assert LANE_DENOMINATOR_NOT_POSITIVE in result.warnings
    assert result.exclusion_by_band_id()["t"].excluded is True


def test_every_band_gets_an_exclusion_record_even_when_nothing_is_excluded() -> None:
    """The result must let its caller state ``excluded_from_normalization`` for every band."""
    bands = [_band("t", "L0", 1200.0), _band("hk", "L0", 400.0)]

    result = _normalize(bands, ["L0"], _config(HOUSEKEEPING_SINGLE), reference_band_ids=["hk"])

    exclusions = result.exclusion_by_band_id()
    assert set(exclusions) == {"t", "hk"}
    assert all(item.excluded is False for item in exclusions.values())
    assert all(item.reason is None for item in exclusions.values())


def test_warnings_are_reported_in_a_fixed_order() -> None:
    """A result document has to be byte-reproducible, so the warning list is ordered."""
    bands = [
        _band("t", "L0", 1200.0, SATURATED),
        _band("hk", "L0", 400.0, OVERLAPPING),
        _band("t1", "L1", 900.0),
    ]

    result = _normalize(
        bands,
        ["L0", "L1"],
        _config(HOUSEKEEPING_SINGLE, exclude=False),
        reference_band_ids=["hk"],
        lossy_format=True,
    )

    positions = [WARNING_ORDER.index(warning) for warning in result.warnings]
    assert positions == sorted(positions)
    assert len(set(result.warnings)) == len(result.warnings)


@pytest.mark.parametrize("mode", [HOUSEKEEPING_SINGLE, HOUSEKEEPING_MULTI])
def test_a_housekeeping_mode_without_reference_ids_raises(mode: str) -> None:
    """The pipeline never infers which band is the loading control (human ruling)."""
    bands = [_band("t", "L0", 1200.0), _band("hk", "L0", 400.0)]

    with pytest.raises(NormalizationError, match="needs reference_band_ids"):
        _normalize(bands, ["L0"], _config(mode))


def test_total_protein_refuses_reference_ids() -> None:
    """An input no mode of the run reads would be an unused quantity in the provenance."""
    bands = [_band("t", "L0", 1200.0)]

    with pytest.raises(NormalizationError, match="has no reference band"):
        _normalize(
            bands,
            ["L0"],
            _config(TOTAL_PROTEIN),
            lane_total_protein={"L0": 5000.0},
            reference_band_ids=["t"],
        )


def test_total_protein_without_lane_totals_raises() -> None:
    """There is no fallback denominator; the lane integral is the mode."""
    with pytest.raises(NormalizationError, match="needs lane_total_protein"):
        _normalize([_band("t", "L0", 1200.0)], ["L0"], _config(TOTAL_PROTEIN))


def test_a_housekeeping_mode_refuses_lane_totals() -> None:
    """Symmetrically: the housekeeping modes never read the lane integral."""
    bands = [_band("t", "L0", 1200.0), _band("hk", "L0", 400.0)]

    with pytest.raises(NormalizationError, match="never reads it"):
        _normalize(
            bands,
            ["L0"],
            _config(HOUSEKEEPING_SINGLE),
            lane_total_protein={"L0": 5000.0},
            reference_band_ids=["hk"],
        )


def test_an_unknown_reference_band_id_raises_and_names_the_bands_there_are() -> None:
    """A reference that does not exist cannot be normalized against."""
    bands = [_band("t", "L0", 1200.0), _band("hk", "L0", 400.0)]

    with pytest.raises(NormalizationError, match="name no measured band"):
        _normalize(bands, ["L0"], _config(HOUSEKEEPING_SINGLE), reference_band_ids=["absent"])


def test_a_duplicated_reference_band_id_raises() -> None:
    """A band counted twice would weight itself twice in the geometric mean."""
    bands = [_band("t", "L0", 1200.0), _band("hk", "L0", 400.0)]

    with pytest.raises(NormalizationError, match="more than once"):
        _normalize(
            bands,
            ["L0"],
            _config(HOUSEKEEPING_MULTI),
            reference_band_ids=["hk", "hk"],
        )


def test_single_mode_with_two_references_in_one_lane_raises() -> None:
    """The mode divides by exactly one band; two is a caller mistake, not a fallback."""
    bands = [_band("t", "L0", 1200.0), _band("a", "L0", 400.0), _band("b", "L0", 900.0)]

    with pytest.raises(NormalizationError, match="divides by exactly one"):
        _normalize(bands, ["L0"], _config(HOUSEKEEPING_SINGLE), reference_band_ids=["a", "b"])


def test_multi_mode_with_one_reference_in_a_lane_raises() -> None:
    """A geometric mean of one value is the value: it would be single mode without its warning."""
    bands = [_band("t", "L0", 1200.0), _band("a", "L0", 400.0)]

    with pytest.raises(NormalizationError, match="at least two"):
        _normalize(bands, ["L0"], _config(HOUSEKEEPING_MULTI), reference_band_ids=["a"])


def test_a_band_naming_an_undetected_lane_raises() -> None:
    """Lane ids come from detection; a band outside them cannot be assigned a denominator."""
    bands = [_band("t", "L9", 1200.0)]

    with pytest.raises(NormalizationError, match="not among the detected lanes"):
        _normalize(bands, ["L0"], _config(TOTAL_PROTEIN), lane_total_protein={"L0": 1.0})


def test_lossy_format_must_be_stated_by_the_caller() -> None:
    """It has no default: a forgotten argument would silently drop a caveat on every JPEG."""
    bands = [_band("t", "L0", 1200.0), _band("hk", "L0", 400.0)]

    with pytest.raises(TypeError, match="lossy_format"):
        normalize(bands, ["L0"], _config(HOUSEKEEPING_SINGLE), reference_band_ids=["hk"])


def test_a_repeated_band_id_raises() -> None:
    """Ids key the exclusion records, so a repeat would attribute a value to the wrong band."""
    bands = [_band("t", "L0", 1200.0), _band("t", "L0", 900.0), _band("hk", "L0", 400.0)]

    with pytest.raises(NormalizationError, match="occur more than once"):
        _normalize(bands, ["L0"], _config(HOUSEKEEPING_SINGLE), reference_band_ids=["hk"])


def test_a_repeated_lane_id_raises() -> None:
    """The lanes are walked in the order given, so a repeat would emit every ratio twice."""
    bands = [_band("t", "L0", 1200.0), _band("hk", "L0", 400.0)]

    with pytest.raises(NormalizationError, match="lane id"):
        _normalize(
            bands, ["L0", "L0"], _config(HOUSEKEEPING_SINGLE), reference_band_ids=["hk"]
        )


def test_a_lossy_image_that_produced_no_ratio_does_not_warn_about_a_denominator() -> None:
    """The warning qualifies a denominator, so it needs one to exist first."""
    bands = [_band("t", "L0", 1200.0)]

    result = _normalize(
        bands,
        ["L0"],
        _config(TOTAL_PROTEIN),
        lane_total_protein={"L0": -5.0},
        lossy_format=True,
    )

    assert result.ratios == ()
    assert LANE_DENOMINATOR_NOT_POSITIVE in result.warnings
    assert f"reference_band_{LOSSY_FORMAT}" not in result.warnings


def test_a_band_flag_outside_the_vocabulary_raises() -> None:
    """An unscorable flag name must not reach a result document through the ratios."""
    bands = [_band("t", "L0", 1200.0, "probably_fine"), _band("hk", "L0", 400.0)]

    with pytest.raises(NormalizationError, match="outside the band vocabulary"):
        _normalize(bands, ["L0"], _config(HOUSEKEEPING_SINGLE), reference_band_ids=["hk"])
