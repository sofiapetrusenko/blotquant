"""Normalization: the three modes, and the caveats each ratio carries with it.

``housekeeping_single`` divides by one designated reference band per lane and **always**
emits ``single_housekeeping_reference``, because current journal guidance treats a single
loading control as insufficient evidence on its own. ``housekeeping_multi`` divides by the
*geometric* mean of two or more references per lane -- geometric because the quantity being
combined is a ratio scale, so the mean of several references must be the one whose
reciprocal is the mean of the reciprocals. ``total_protein`` divides by the lane's own
signal integral over the detected lane ROI.

Three rules the whole module exists to keep:

* **A reference is designated by the caller, never inferred.** Which band is the loading
  control is not visible in the pixels, and a pipeline that guessed would put a guess
  inside a provenance record. A housekeeping mode without ``reference_band_ids`` raises;
  so does an id that names no band (human ruling, NOTES.md Phase 2).
* **QC annotates, never drops.** A flagged band's ratio is still computed and still
  reported -- marked ``excluded`` with a reason when ``exclude_qc_flagged`` is set, which
  is the default. Setting that false is an explicit override and is recorded as a warning.
* **A flagged *reference* normalizes and warns, and the warning names the flag.** Excluding
  it would delete every ratio in the lane, and the direction of the bias depends on which
  flag it is: a saturated reference under-reads, so every ratio in that lane is biased
  upward by a known sign; an overlapping or shouldered one is biased in an unknown
  direction. Each ratio also records ``reference_qc_flagged`` and the flags themselves, so
  a consumer can filter on the condition without re-deriving it (human ruling).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pipeline.config import (
    HOUSEKEEPING_MODES,
    HOUSEKEEPING_MULTI,
    HOUSEKEEPING_SINGLE,
    TOTAL_PROTEIN,
    NormalizationConfig,
)
from pipeline.errors import NormalizationError
from pipeline.qc import BAND_QC_FLAGS, LOSSY_FORMAT

SINGLE_HOUSEKEEPING_REFERENCE = "single_housekeeping_reference"
REFERENCE_BAND_QC_FLAGGED = "reference_band_qc_flagged"
QC_FLAGGED_BANDS_INCLUDED_BY_OVERRIDE = "qc_flagged_bands_included_by_override"
LANE_WITHOUT_REFERENCE_BAND = "lane_without_reference_band"
LANE_DENOMINATOR_NOT_POSITIVE = "lane_denominator_not_positive"

REFERENCE_FLAG_WARNINGS: Mapping[str, str] = {
    flag: f"reference_band_{flag}" for flag in (*BAND_QC_FLAGS, LOSSY_FORMAT)
}
"""Flag -> the warning naming it on a reference.

The general ``reference_band_qc_flagged`` is emitted alongside the band flags, so a consumer
written against the earlier vocabulary keeps working; these say *which* flag it was, because
the caveats differ in direction and a reader has to be able to tell them apart.

``lossy_format`` is here too, and is the one entry that is not a band flag: it is a property
of the image, so it warns about every denominator measured from it without entering any
band's or ratio's flag list. Those lists keep the band vocabulary they share with ground
truth, which is what makes them scorable.
"""

WARNING_ORDER: tuple[str, ...] = (
    SINGLE_HOUSEKEEPING_REFERENCE,
    REFERENCE_BAND_QC_FLAGGED,
    *REFERENCE_FLAG_WARNINGS.values(),
    QC_FLAGGED_BANDS_INCLUDED_BY_OVERRIDE,
    LANE_WITHOUT_REFERENCE_BAND,
    LANE_DENOMINATOR_NOT_POSITIVE,
)
"""The order warnings are reported in, so a result document is byte-reproducible."""

QC_EXCLUSION_REASON = "carries QC flags: "
"""Prefix of the reason recorded when a flagged band is excluded from the ratios."""


@dataclass(frozen=True)
class NormalizationBand:
    """One band as normalization sees it: an identity, a lane, a value and its QC flags."""

    band_id: str
    lane_id: str
    intensity: float
    qc_flags: tuple[str, ...]


@dataclass(frozen=True)
class BandExclusion:
    """Whether one band's value entered the ratios, and why not when it did not."""

    band_id: str
    excluded: bool
    reason: str | None


@dataclass(frozen=True)
class Ratio:
    """One normalized value, with everything needed to decide whether to trust it.

    ``denominator_band_ids`` names the reference band(s) this lane was divided by: one in
    ``housekeeping_single``, several in ``housekeeping_multi``, and none in
    ``total_protein``, whose denominator is the lane integral rather than a band. The
    singular ``denominator_band_id`` is emitted as well when there is exactly one.
    """

    lane_id: str
    numerator_band_id: str
    denominator_band_ids: tuple[str, ...]
    ratio: float
    excluded: bool
    exclusion_reason: str | None
    qc_flags: tuple[str, ...]
    reference_qc_flagged: bool
    reference_qc_flags: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        """Return the ``normalization.ratios[]`` entry of a result document."""
        document: dict[str, Any] = {
            "lane_id": self.lane_id,
            "numerator_band_id": self.numerator_band_id,
        }
        if len(self.denominator_band_ids) == 1:
            document["denominator_band_id"] = self.denominator_band_ids[0]
        if self.denominator_band_ids:
            document["denominator_band_ids"] = list(self.denominator_band_ids)
        document["ratio"] = self.ratio
        document["excluded"] = self.excluded
        if self.exclusion_reason is not None:
            document["exclusion_reason"] = self.exclusion_reason
        document["qc_flags"] = list(self.qc_flags)
        document["reference_qc_flagged"] = self.reference_qc_flagged
        document["reference_qc_flags"] = list(self.reference_qc_flags)
        return document


@dataclass(frozen=True)
class NormalizationResult:
    """Everything normalization produces for one image."""

    mode: str
    exclude_qc_flagged: bool
    reference_band_ids: tuple[str, ...] | None
    warnings: tuple[str, ...]
    ratios: tuple[Ratio, ...]
    band_exclusions: tuple[BandExclusion, ...]
    lane_total_protein: Mapping[str, float] | None

    def as_dict(self) -> dict[str, Any]:
        """Return the ``normalization`` block of a result document."""
        document: dict[str, Any] = {
            "mode": self.mode,
            "exclude_qc_flagged": self.exclude_qc_flagged,
        }
        if self.reference_band_ids is not None:
            document["reference_band_ids"] = list(self.reference_band_ids)
        document["warnings"] = list(self.warnings)
        document["ratios"] = [ratio.as_dict() for ratio in self.ratios]
        return document

    def exclusion_by_band_id(self) -> Mapping[str, BandExclusion]:
        """Return each band's exclusion record keyed by band id."""
        return {exclusion.band_id: exclusion for exclusion in self.band_exclusions}


def _ordered_flags(flags: Sequence[str]) -> tuple[str, ...]:
    """Return ``flags`` deduplicated and ordered by the band vocabulary.

    Raises :class:`NormalizationError` for a name outside that vocabulary, so a flag the
    schema's ``band_qc_flags`` enum does not define cannot reach a result document.
    """
    unique = set(flags)
    unknown = sorted(unique - set(BAND_QC_FLAGS))
    if unknown:
        raise NormalizationError(
            f"band QC flag(s) {unknown} are outside the band vocabulary "
            f"{list(BAND_QC_FLAGS)}; normalization records band flags on its ratios, and an "
            f"unknown name there would be an unscorable flag in the result document"
        )
    return tuple(flag for flag in BAND_QC_FLAGS if flag in unique)


def _qc_reason(flags: Sequence[str]) -> str:
    """Return the recorded reason for excluding a band that carries ``flags``."""
    return QC_EXCLUSION_REASON + ", ".join(_ordered_flags(flags))


def _geometric_mean(values: Sequence[float]) -> float:
    """Return the geometric mean of strictly positive ``values``.

    The caller has already established that every value is positive; the logarithm is
    undefined otherwise. Summed with :func:`math.fsum` so the result does not depend on the
    order the references were designated in.
    """
    return math.exp(math.fsum(math.log(value) for value in values) / len(values))


@dataclass(frozen=True)
class _Denominator:
    """A lane's usable denominator: its value, the band ids it names, and their flags."""

    value: float
    band_ids: tuple[str, ...]
    flags: tuple[str, ...]


@dataclass(frozen=True)
class _LaneProblem:
    """Why one lane produced no ratio, as a warning code and a per-band reason.

    Not an exception: a lane whose denominator is unusable is an outcome of the *data*, and
    failing the whole image over it would discard the lanes that measured fine. It is
    recorded three times over instead -- the warning, the absence of ratios, and every band
    in the lane carrying ``excluded_from_normalization`` with this reason -- so nothing is
    dropped silently. Caller mistakes, by contrast, do raise: see :func:`_resolve_references`.
    """

    warning: str
    reason: str


def _resolve_references(
    bands: Sequence[NormalizationBand], reference_band_ids: Sequence[str] | None, mode: str
) -> tuple[str, ...]:
    """Return the validated reference ids for ``mode``, raising on anything ambiguous."""
    if mode not in HOUSEKEEPING_MODES:
        if reference_band_ids:
            raise NormalizationError(
                f"reference_band_ids was given for mode {mode!r}, which has no reference "
                f"band: its denominator is the lane's own signal integral. Passing ids that "
                f"nothing reads would put an unused input into the provenance record"
            )
        return ()
    if not reference_band_ids:
        raise NormalizationError(
            f"mode {mode!r} needs reference_band_ids: which band is the loading control is "
            f"not visible in the pixels, so the pipeline will not infer it and will not fall "
            f"back to another mode. Pass the reference band ids explicitly, or use "
            f"{TOTAL_PROTEIN!r}"
        )
    known = {band.band_id for band in bands}
    unknown = [band_id for band_id in reference_band_ids if band_id not in known]
    if unknown:
        raise NormalizationError(
            f"reference band id(s) {unknown} name no measured band; the bands in this image "
            f"are {sorted(known)}. A reference that does not exist cannot be normalized "
            f"against"
        )
    duplicates = sorted({i for i in reference_band_ids if list(reference_band_ids).count(i) > 1})
    if duplicates:
        raise NormalizationError(
            f"reference band id(s) {duplicates} appear more than once; a band counted twice "
            f"would weight itself twice in the geometric mean"
        )
    return tuple(reference_band_ids)


def _resolve_lane_totals(
    lane_total_protein: Mapping[str, float] | None, mode: str
) -> Mapping[str, float]:
    """Return the lane totals ``mode`` reads, raising when they are missing or unread."""
    if mode == TOTAL_PROTEIN:
        if lane_total_protein is None:
            raise NormalizationError(
                f"mode {TOTAL_PROTEIN!r} needs lane_total_protein: its denominator is the "
                f"signal integral of each detected lane ROI, and there is no fallback "
                f"denominator to normalize against"
            )
        return lane_total_protein
    if lane_total_protein is not None:
        raise NormalizationError(
            f"lane_total_protein was given for mode {mode!r}, which divides by reference "
            f"bands and never reads it; passing an input that nothing reads would put an "
            f"unused quantity into the provenance record"
        )
    return {}


def _not_positive(what: str, value: float) -> _LaneProblem:
    """Return the recorded outcome for a lane whose denominator is not strictly positive."""
    return _LaneProblem(
        warning=LANE_DENOMINATOR_NOT_POSITIVE,
        reason=(
            f"{what} is {value:.6g}, which is not positive, so no ratio in this lane would "
            f"be a measurement"
        ),
    )


def _lane_denominator(
    mode: str,
    lane_id: str,
    references: Sequence[NormalizationBand],
    lane_total_protein: Mapping[str, float],
) -> _Denominator | _LaneProblem:
    """Return one lane's denominator, or why the lane has none that could be divided by.

    Raises :class:`NormalizationError` only for a caller mistake -- a lane holding the wrong
    *number* of designated references for the mode. A denominator that measured non-positive
    is data, and comes back as a :class:`_LaneProblem`.
    """
    if mode == TOTAL_PROTEIN:
        if lane_id not in lane_total_protein:
            raise NormalizationError(
                f"no total-protein signal was supplied for lane {lane_id!r}; mode "
                f"{TOTAL_PROTEIN!r} integrates every detected lane ROI, so a missing lane is "
                f"a caller error rather than an unnormalizable lane"
            )
        total = float(lane_total_protein[lane_id])
        if total <= 0.0:
            return _not_positive(f"the total-protein signal of lane {lane_id!r}", total)
        return _Denominator(value=total, band_ids=(), flags=())
    if mode == HOUSEKEEPING_SINGLE:
        if len(references) != 1:
            raise NormalizationError(
                f"lane {lane_id!r} has {len(references)} designated reference bands, but mode "
                f"{HOUSEKEEPING_SINGLE!r} divides by exactly one. Use "
                f"{HOUSEKEEPING_MULTI!r} to combine several references, or designate one band "
                f"per lane"
            )
        reference = references[0]
        if reference.intensity <= 0.0:
            return _not_positive(
                f"the integrated intensity of reference band {reference.band_id!r}",
                reference.intensity,
            )
        return _Denominator(
            value=reference.intensity,
            band_ids=(reference.band_id,),
            flags=_ordered_flags(reference.qc_flags),
        )
    if len(references) < 2:
        raise NormalizationError(
            f"lane {lane_id!r} has {len(references)} designated reference band(s), but mode "
            f"{HOUSEKEEPING_MULTI!r} takes the geometric mean of at least two. Designate "
            f"more references in that lane, or use {HOUSEKEEPING_SINGLE!r}"
        )
    for reference in references:
        if reference.intensity <= 0.0:
            return _not_positive(
                f"the integrated intensity of reference band {reference.band_id!r}, one of "
                f"the references whose geometric mean lane {lane_id!r} is normalized by",
                reference.intensity,
            )
    return _Denominator(
        value=_geometric_mean([reference.intensity for reference in references]),
        band_ids=tuple(reference.band_id for reference in references),
        flags=_ordered_flags([flag for reference in references for flag in reference.qc_flags]),
    )


def normalize(
    bands: Sequence[NormalizationBand],
    lane_ids: Sequence[str],
    config: NormalizationConfig,
    *,
    lossy_format: bool,
    lane_total_protein: Mapping[str, float] | None = None,
    reference_band_ids: Sequence[str] | None = None,
) -> NormalizationResult:
    """Normalize every band of one image and return the ratios plus their caveats.

    ``lane_ids`` fixes the lane order of the output. Each mode takes exactly the input its
    denominator needs and refuses the other, so no unread input reaches a provenance record:
    ``total_protein`` requires ``lane_total_protein``, the signal integral of every detected
    lane ROI, and refuses ``reference_band_ids``; the housekeeping modes require
    ``reference_band_ids`` and refuse ``lane_total_protein``. ``lossy_format`` says whether
    the image came out of a lossy container, which is a caveat on every denominator measured
    from it; it is required rather than defaulted, because a caller who forgot it would
    silently lose the ``reference_band_lossy_format`` warning on every JPEG.

    Guarantees:

    * exactly one :class:`BandExclusion` per input band, so a caller can always state
      ``excluded_from_normalization`` explicitly -- even when it is false;
    * one :class:`Ratio` per (lane, non-reference band) pair whose lane has a usable
      denominator, whether or not it is excluded: a flagged value is reported with its flags
      and its exclusion, never omitted;
    * a lane with no usable denominator -- no designated reference, or a denominator that
      measured non-positive -- produces no ratios and instead records the reason on every one
      of its bands and a warning on the result;
    * deterministic ordering and a warning list in :data:`WARNING_ORDER`.

    Raises :class:`pipeline.errors.NormalizationError` for a caller mistake, never for an
    outcome of the data: a housekeeping mode without reference ids, reference ids under
    ``total_protein``, lane integrals under a housekeeping mode or missing under
    ``total_protein``, an unknown or duplicated reference id, a repeated band or lane id, a band
    naming an undetected lane, a lane holding the wrong number of designated references for the
    mode, or a band flag outside the band vocabulary.
    """
    band_ids = [band.band_id for band in bands]
    repeated = sorted({band_id for band_id in band_ids if band_ids.count(band_id) > 1})
    if repeated:
        raise NormalizationError(
            f"band id(s) {repeated} occur more than once; ids identify a band's exclusion "
            f"record and its ratios, so a repeated one would attribute a measurement to the "
            f"wrong band"
        )
    repeated_lanes = sorted({lane for lane in lane_ids if list(lane_ids).count(lane) > 1})
    if repeated_lanes:
        raise NormalizationError(
            f"lane id(s) {repeated_lanes} occur more than once; the lanes are walked in the "
            f"order given, so a repeated lane would emit every ratio in it twice"
        )
    references = _resolve_references(bands, reference_band_ids, config.mode)
    totals = _resolve_lane_totals(lane_total_protein, config.mode)
    reference_ids = set(references)
    by_lane: dict[str, list[NormalizationBand]] = {lane_id: [] for lane_id in lane_ids}
    for band in bands:
        if band.lane_id not in by_lane:
            raise NormalizationError(
                f"band {band.band_id!r} names lane {band.lane_id!r}, which is not among the "
                f"detected lanes {list(lane_ids)}"
            )
        by_lane[band.lane_id].append(band)

    warnings: set[str] = set()
    if config.mode == HOUSEKEEPING_SINGLE:
        warnings.add(SINGLE_HOUSEKEEPING_REFERENCE)
    flagged_bands = [band for band in bands if band.qc_flags]
    if flagged_bands and not config.exclude_qc_flagged:
        warnings.add(QC_FLAGGED_BANDS_INCLUDED_BY_OVERRIDE)
    # The lossy caveat is added after the lanes are walked, not here: it qualifies a
    # denominator, so an image that produced no ratio at all must not claim one was measured on
    # rewritten pixels. See the end of this function.

    ratios: list[Ratio] = []
    exclusions: dict[str, BandExclusion] = {}
    for lane_id in lane_ids:
        lane_bands = by_lane[lane_id]
        lane_references = [band for band in lane_bands if band.band_id in reference_ids]
        resolved: _Denominator | _LaneProblem
        if config.mode in HOUSEKEEPING_MODES and not lane_references:
            resolved = _LaneProblem(
                warning=LANE_WITHOUT_REFERENCE_BAND,
                reason=(
                    f"lane {lane_id!r} has no designated reference band, so this value could "
                    f"not be normalized"
                ),
            )
        else:
            resolved = _lane_denominator(
                config.mode, lane_id, lane_references, totals
            )
        if isinstance(resolved, _LaneProblem):
            warnings.add(resolved.warning)
            for band in lane_bands:
                exclusions[band.band_id] = BandExclusion(
                    band_id=band.band_id, excluded=True, reason=resolved.reason
                )
            continue
        reference_flags = resolved.flags
        if config.mode == TOTAL_PROTEIN:
            # The lane integral contains every band in the lane, so any flag in the lane is a
            # caveat on the denominator -- the same statement Ruling 3 makes about a flagged
            # housekeeping band, applied to the quantity that plays its part here.
            reference_flags = _ordered_flags(
                [flag for band in lane_bands for flag in band.qc_flags]
            )
        for flag in reference_flags:
            warnings.add(REFERENCE_BAND_QC_FLAGGED)
            warnings.add(REFERENCE_FLAG_WARNINGS[flag])

        for band in lane_bands:
            if band.band_id in reference_ids:
                # A flagged reference is used, not excluded: dropping it would delete every
                # ratio in the lane. The caveat travels as a named warning and as this
                # lane's reference_qc_flags instead.
                exclusions[band.band_id] = BandExclusion(
                    band_id=band.band_id, excluded=False, reason=None
                )
                continue
            excluded = bool(band.qc_flags) and config.exclude_qc_flagged
            reason = _qc_reason(band.qc_flags) if excluded else None
            exclusions[band.band_id] = BandExclusion(
                band_id=band.band_id, excluded=excluded, reason=reason
            )
            ratios.append(
                Ratio(
                    lane_id=lane_id,
                    numerator_band_id=band.band_id,
                    denominator_band_ids=resolved.band_ids,
                    ratio=band.intensity / resolved.value,
                    excluded=excluded,
                    exclusion_reason=reason,
                    qc_flags=_ordered_flags([*band.qc_flags, *reference_flags]),
                    reference_qc_flagged=bool(reference_flags),
                    reference_qc_flags=reference_flags,
                )
            )

    if lossy_format and ratios:
        # An image-wide caveat on every denominator, named as the human ruling requires but
        # kept out of the band-flag lists, which carry the ground-truth band vocabulary. Only
        # emitted once a denominator exists: with no ratios there is nothing it qualifies.
        warnings.add(REFERENCE_FLAG_WARNINGS[LOSSY_FORMAT])
    return NormalizationResult(
        mode=config.mode,
        exclude_qc_flagged=config.exclude_qc_flagged,
        reference_band_ids=references if config.mode in HOUSEKEEPING_MODES else None,
        warnings=tuple(warning for warning in WARNING_ORDER if warning in warnings),
        ratios=tuple(ratios),
        band_exclusions=tuple(exclusions[band.band_id] for band in bands),
        lane_total_protein=(
            {lane_id: float(totals[lane_id]) for lane_id in lane_ids}
            if config.mode == TOTAL_PROTEIN
            else None
        ),
    )
