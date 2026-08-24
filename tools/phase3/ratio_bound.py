"""The ratio bound: how many lanes *could* contribute a ratio, before any label is mapped.

Read-only, and deliberately not a measurement of N. It answers one question -- **is the
band-mapping gate worth holding?** -- by counting, from a detection run's own result documents,
how many lanes are left with enough unflagged bands for a ratio to be possible at all. If that
count is small, the gate is expensive and the phase's outcome is knowable before it is held.

**Its output was WITHDRAWN as evidence about N by human ruling R2 of 2026-08-24**, and the
withdrawal is emitted at the head of every report this module writes. The counts filter on QC
flags, and the QC diagnostic of the same day established that those flags measure polarity rather
than clipping on this corpus (DEBT S14). The arithmetic is sound and its input is not, so the
result says nothing about N -- N is *unknown*, not small. The module is kept, and kept runnable,
because it becomes meaningful again once polarity is a declared input under R4 and the crops are
re-run through it.

**Nothing here selects, tunes, or rules.** It reads the shipped exclusion rule out of
:mod:`pipeline.normalize` rather than restating it, groups by the human-ruled ``blot_id``
through :func:`tools.phase3.blot_identity.confirmed_blot_id`, and applies the pre-registered
band-height criterion as a *second column* rather than as a decision. It computes no ratio, no
N, no agreement statistic, and selects no stop-rule branch.

**Why "could" and not "will".** A lane contributes a ratio only once a human has named which
detected band carries the reference label -- the gate this file exists to price. Every count
here is an **upper bound**: it assumes the mapping gate goes as well as it possibly could.

Usage: python -m tools.phase3.ratio_bound --run-dir runs/3b1
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from pipeline.qc import BAND_QC_FLAGS, IMAGE_QC_FLAGS
from tools.phase3.blot_identity import BLOT_IDENTITY_PATH, confirmed_blot_id, read_blot_identities
from tools.phase3.designations import DESIGNATIONS_PATH, contributes_ratios, read_designations

PREREGISTERED_MIN_BAND_HEIGHT_PX = 15
"""§8(c)'s band-height minimum. Applied as a tabulated column here, never as an exclusion.

Duplicated from :mod:`tools.phase3.run_real` rather than imported, because importing it would
make this module depend on the run script for a constant that belongs to the pre-registration.
:func:`_check_criterion_agrees` asserts the two have not drifted apart.
"""


def _check_criterion_agrees() -> None:
    """Fail loudly if this module and the runner disagree about the pre-registered minimum."""
    from tools.phase3.run_real import PREREGISTERED_MIN_BAND_HEIGHT_PX as runner_value

    if runner_value != PREREGISTERED_MIN_BAND_HEIGHT_PX:
        raise ValueError(
            f"the band-height minimum is {PREREGISTERED_MIN_BAND_HEIGHT_PX} here and "
            f"{runner_value} in tools.phase3.run_real. One of them is wrong, and a bound "
            f"computed against the wrong criterion would understate or overstate every count"
        )


# The blocking set, derived from the shipped rule rather than declared here. See
# blocking_flags() for the quotation and the reasoning; this module never hard-codes a flag
# name into the decision.
ADVISORY_IMAGE_FLAGS: tuple[str, ...] = IMAGE_QC_FLAGS


def blocking_flags() -> tuple[str, ...]:
    """Return the band flags that block a band from a ratio under the ratified rules.

    **Every band flag blocks, and the rules draw no blocking/advisory distinction at all.**
    That is not a simplification made here; it is what the shipped rule says. `configs/default.yaml`
    sets ``normalization.exclude_qc_flagged: true`` with the comment *"Exclude QC-flagged bands
    from the ratios"*, and :mod:`pipeline.normalize` implements it as
    ``excluded = bool(band.qc_flags) and config.exclude_qc_flagged`` -- a test of whether the
    list is empty, not of which flag is in it. `DECISION_unit_of_analysis.md` §4 is the ruling
    behind it: *"DECISION: excluded from the correlation; reported as a separate count ('n
    excluded by QC', with the flags that excluded them)"*, and *"QC thresholds are frozen as
    shipped (Phase 3 Gate 1); nothing here reopens them."*

    So the blocking set is exactly :data:`pipeline.qc.BAND_QC_FLAGS`. Returning it from the
    pipeline's own vocabulary rather than listing three strings means a flag added to the
    vocabulary later is blocking here too, without anyone remembering to update this file.
    """
    return BAND_QC_FLAGS


@dataclass(frozen=True)
class LaneCount:
    """One lane's band counts, before and after the pre-registered height criterion."""

    crop_filename: str
    blot_id: str
    lane_id: str
    bands: int
    unflagged: int
    unflagged_tall: int
    any_band_tall: int

    @property
    def could_pair(self) -> bool:
        """Two unflagged bands: a numerator and a denominator both surviving QC."""
        return self.unflagged >= 2

    @property
    def could_pair_tall(self) -> bool:
        """The same, with §8(c)'s height minimum applied on top."""
        return self.unflagged_tall >= 2

    @property
    def could_pair_flagged_reference(self) -> bool:
        """One unflagged band, plus any other band at all to serve as the reference.

        The looser bound, and the one the shipped rule actually permits: `pipeline/normalize.py`
        exempts the reference band from exclusion -- *"A flagged reference is used, not excluded:
        dropping it would delete every ratio in the lane"* -- and carries the caveat as a named
        warning instead. So a lane whose only clean band is the target can still yield a ratio
        against a flagged reference.
        """
        return self.unflagged >= 1 and self.bands >= 2

    @property
    def could_pair_flagged_reference_tall(self) -> bool:
        """The looser bound with §8(c) applied to both bands."""
        return self.unflagged_tall >= 1 and self.any_band_tall >= 2


def lane_counts(
    run_dir: Path,
    identity_table: dict[str, object],
    blocking: tuple[str, ...],
    min_height_px: int,
) -> list[LaneCount]:
    """Return one :class:`LaneCount` per detected lane across every result document in ``run_dir``.

    Lanes are enumerated from ``document["lanes"]``, not from the bands: a lane in which nothing
    was detected has zero unflagged bands and is part of the distribution. Reading the lane list
    only through the bands would silently drop it and inflate every proportion below it.
    """
    blocking_set = set(blocking)
    counts: list[LaneCount] = []
    for path in sorted(run_dir.glob("*/*.json")):
        document = json.loads(path.read_text())
        # ``source.path`` is what the document records; the tables are keyed on the
        # filename, so the basename is taken rather than a second field being invented.
        crop = Path(str(document["source"]["path"])).name
        blot_id = confirmed_blot_id(identity_table, crop)  # type: ignore[arg-type]
        by_lane: dict[str, list[dict[str, object]]] = {
            str(lane["lane_id"]): [] for lane in document["lanes"]
        }
        for band in document["bands"]:
            by_lane[str(band["lane_id"])].append(band)
        for lane_id, bands in by_lane.items():
            clean = [b for b in bands if not (set(b["qc_flags"]) & blocking_set)]  # type: ignore[arg-type]
            tall = [b for b in bands if int(b["roi"]["height"]) >= min_height_px]  # type: ignore[index]
            counts.append(
                LaneCount(
                    crop_filename=crop,
                    blot_id=blot_id,
                    lane_id=lane_id,
                    bands=len(bands),
                    unflagged=len(clean),
                    unflagged_tall=sum(
                        1
                        for b in clean
                        if int(b["roi"]["height"]) >= min_height_px  # type: ignore[index]
                    ),
                    any_band_tall=len(tall),
                )
            )
    return counts


def distribution(values: list[int]) -> dict[str, int]:
    """Return the 0 / 1 / 2 / 3+ bucketing the bound is read from."""
    counter = Counter(min(value, 3) for value in values)
    return {
        "0": counter[0],
        "1": counter[1],
        "2": counter[2],
        "3+": counter[3],
    }


def report(
    counts: list[LaneCount],
    designation_table: dict[str, object],
    blocking: tuple[str, ...],
    min_height_px: int,
    run_dir: Path,
) -> list[str]:
    """Return RATIO_BOUND.md, as lines. Every figure is computed from ``counts``."""
    lanes = len(counts)
    crops = len({c.crop_filename for c in counts})
    blots = len({c.blot_id for c in counts})
    ratio_blots = {
        c.blot_id
        for c in counts
        if contributes_ratios(designation_table, c.crop_filename)  # type: ignore[arg-type]
    }

    strict = distribution([c.unflagged for c in counts])
    strict_tall = distribution([c.unflagged_tall for c in counts])

    lines = [
        "# Phase 3b-1 — the ratio bound, before the band-mapping gate",
        "",
        # Emitted by the generator, not pasted into the output, so that re-running this tool
        # reproduces the withdrawal instead of silently erasing it. R2 requires the notice
        # wherever the bound is referenced, and the place a reader meets it is this file.
        "> **WITHDRAWN AS EVIDENCE ABOUT N — human ruling R2, 2026-08-24.**",
        ">",
        "> Every count below filters on bands carrying no QC flag. The QC diagnostic of the "
        "same day (`QC_DIAGNOSTIC.md`) established that those flags are measuring "
        "**polarity, not clipping**: these are white-ground figures with dark bands, and "
        "`saturated` fires on white background inside a band rectangle. The arithmetic here "
        "is correct and its input is not.",
        ">",
        "> **The consequence is that N is UNKNOWN, not small.** A small N would select the "
        "pre-registered descriptive-only branch; an unknown N selects nothing, and **no "
        "stop-rule branch is selected by Phase 3b-1**. Nothing in this file may be cited as "
        "a bound on N, on the surviving ratio count, or on the blot count against the "
        "10-blot floor.",
        ">",
        "> Kept rather than deleted: a measurement that was correctly computed and wrongly "
        "premised is the evidence for why the premise was wrong. Re-running it is only "
        "meaningful after polarity is handled under R4.",
        "",
        "**Read-only. No ruling was made, no ratio computed, no N counted, no agreement "
        "statistic produced, and no stop-rule branch selected.** This file answers one "
        "question — whether the band-mapping gate is worth the hours — by bounding above the "
        "number of lanes that could contribute a ratio *even if that gate went perfectly*.",
        "",
        f"Source: the {crops} result documents under `{run_dir}/`, written by "
        f"`python -m tools.phase3.run_real --detection-only` in this session. `blot_id` comes "
        f"from `confirmed_blot_id`, the human-ruled table; nothing here re-derives it. "
        f"{lanes} detected lanes, {blots} blots.",
        "",
        "## 1. What blocks a band, quoted rather than paraphrased",
        "",
        "**The ratified rules draw no blocking-versus-advisory distinction among band flags. "
        "Every band flag blocks.** That is the finding, not a simplification made here.",
        "",
        "`DECISION_unit_of_analysis.md` §4, the ruling:",
        "",
        "> DECISION: excluded from the correlation; reported as a separate count",
        "> (\"n excluded by QC\", with the flags that excluded them).",
        "",
        "> Failure mode I accept: QC may leave fewer than 15 surviving ratios. I will",
        "> report that count as a finding in its own right (\"QC excluded X of Y ratios",
        "> from N real blots\") and will not relax any QC threshold to save N. QC",
        "> thresholds are frozen as shipped (Phase 3 Gate 1); nothing here reopens them.",
        "",
        "`configs/default.yaml`, the shipped setting, verbatim:",
        "",
        "> Exclude QC-flagged bands from the ratios. The band still reports its value and its",
        "> flags, and its ratio is still reported - marked excluded, with the reason recorded -",
        "> so nothing is dropped silently.",
        "",
        "> `exclude_qc_flagged: true`",
        "",
        "`pipeline/normalize.py`, the implementation, verbatim:",
        "",
        "> `excluded = bool(band.qc_flags) and config.exclude_qc_flagged`",
        "",
        "That is a test of whether the flag list is **empty**, not of which flag is in it. So:",
        "",
        f"- **Blocking (band-level): {', '.join(f'`{flag}`' for flag in blocking)}** — the whole "
        f"of `pipeline.qc.BAND_QC_FLAGS`. This module reads that tuple rather than naming the "
        f"flags, so a flag added to the vocabulary later blocks here without anyone remembering "
        f"to edit this file.",
        f"- **Advisory (image-level): "
        f"{', '.join(f'`{flag}`' for flag in ADVISORY_IMAGE_FLAGS)}** — `image_qc_flags` are "
        f"never read by the exclusion path. They qualify the image, and `lossy_format` adds a "
        f"named warning to the denominator, but no image flag removes a band from a ratio.",
        "",
        "**One exemption in the rules, and it moves the bound.** `pipeline/normalize.py` does "
        "not exclude the reference band:",
        "",
        "> A flagged reference is used, not excluded: dropping it would delete every",
        "> ratio in the lane. The caveat travels as a named warning and as this",
        "> lane's `reference_qc_flags` instead.",
        "",
        "So a lane needs **one** unflagged band plus **any** second band to serve as reference — "
        "not two unflagged bands. Both figures are reported below. The two-unflagged count is "
        "the conservative one; the reference-exempt count is the true upper bound.",
        "",
        f"## 2. Unflagged bands per lane — distribution over all {lanes} lanes",
        "",
        "Computed against the blocking set only, which is every band flag. The second column "
        f"applies §8(c)'s **≥ {min_height_px} px** band-height minimum on top, tabulated and not "
        "applied as a decision.",
        "",
        "| unflagged bands in the lane | lanes | lanes, also ≥ "
        f"{min_height_px} px |",
        "|---|---|---|",
        f"| 0 | {strict['0']} | {strict_tall['0']} |",
        f"| 1 | {strict['1']} | {strict_tall['1']} |",
        f"| 2 | {strict['2']} | {strict_tall['2']} |",
        f"| 3 or more | {strict['3+']} | {strict_tall['3+']} |",
        f"| **total** | **{lanes}** | **{lanes}** |",
        "",
        "## 3. The bound: lanes that could contribute a ratio at all",
        "",
        f"| bound | lanes | lanes, also ≥ {min_height_px} px |",
        "|---|---|---|",
        f"| **≥ 2 unflagged bands** (conservative) | "
        f"{sum(1 for c in counts if c.could_pair)} | "
        f"{sum(1 for c in counts if c.could_pair_tall)} |",
        f"| **≥ 1 unflagged band + any second band** (true upper bound, reference exempt) | "
        f"{sum(1 for c in counts if c.could_pair_flagged_reference)} | "
        f"{sum(1 for c in counts if c.could_pair_flagged_reference_tall)} |",
        f"| lanes with no unflagged band at all | "
        f"{sum(1 for c in counts if c.unflagged == 0)} | "
        f"{sum(1 for c in counts if c.unflagged_tall == 0)} |",
        "",
        "These are ceilings on *lanes*, not on ratios, and they assume the mapping gate goes as "
        "well as it possibly could: that a human names a reference band in every one of these "
        "lanes, and that the band they name is one of the ones counted here.",
        "",
        "## 4. Per blot, against the 10-blot floor",
        "",
        f"`{len(ratio_blots)}` of the {blots} blots are ruled to contribute ratios at all "
        f"(`contributes_ratios`); the remainder is the §9 reference strip G1 excluded. The "
        f"ratified stop rule's floor is **a minimum of 10 blots**.",
        "",
        f"| blot_id | lanes | lanes ≥2 unflagged | ≥2 unflagged, ≥ {min_height_px} px | "
        f"lanes ≥1 unflagged + a second band | same, ≥ {min_height_px} px | "
        f"contributes ratios |",
        "|---|---|---|---|---|---|---|",
    ]
    for blot_id in sorted({c.blot_id for c in counts}):
        blot_lanes = [c for c in counts if c.blot_id == blot_id]
        lines.append(
            f"| `{blot_id}` | {len(blot_lanes)} | "
            f"{sum(1 for c in blot_lanes if c.could_pair)} | "
            f"{sum(1 for c in blot_lanes if c.could_pair_tall)} | "
            f"{sum(1 for c in blot_lanes if c.could_pair_flagged_reference)} | "
            f"{sum(1 for c in blot_lanes if c.could_pair_flagged_reference_tall)} | "
            f"{'yes' if blot_id in ratio_blots else 'no'} |"
        )

    strict_blots = sorted(
        {
            c.blot_id
            for c in counts
            if c.could_pair and c.blot_id in ratio_blots
        }
    )
    strict_blots_tall = sorted(
        {
            c.blot_id
            for c in counts
            if c.could_pair_tall and c.blot_id in ratio_blots
        }
    )
    loose_blots = sorted(
        {
            c.blot_id
            for c in counts
            if c.could_pair_flagged_reference and c.blot_id in ratio_blots
        }
    )
    loose_blots_tall = sorted(
        {
            c.blot_id
            for c in counts
            if c.could_pair_flagged_reference_tall and c.blot_id in ratio_blots
        }
    )
    lines += [
        "",
        "**Ratio-contributing blots retaining at least one lane that could pair**, which is the "
        "figure the 10-blot floor is read against:",
        "",
        f"| bound | blots | blots, also ≥ {min_height_px} px |",
        "|---|---|---|",
        f"| ≥ 2 unflagged bands in some lane | {len(strict_blots)} | {len(strict_blots_tall)} |",
        f"| ≥ 1 unflagged + a second band in some lane | {len(loose_blots)} | "
        f"{len(loose_blots_tall)} |",
        "",
        "**This is not a stop-rule evaluation.** The floor is a property of the blots that "
        "actually yield ratios after the mapping gate and the ratio count, neither of which has "
        "happened. The figures above bound those from above and nothing more.",
        "",
        "## 5. Per crop",
        "",
        f"| crop | blot_id | lanes | bands | lanes ≥2 unflagged | ≥2 unflagged, ≥ "
        f"{min_height_px} px | lanes ≥1 unflagged + a second band |",
        "|---|---|---|---|---|---|---|",
    ]
    for crop in sorted({c.crop_filename for c in counts}):
        crop_lanes = [c for c in counts if c.crop_filename == crop]
        lines.append(
            f"| `{crop}` | `{crop_lanes[0].blot_id}` | {len(crop_lanes)} | "
            f"{sum(c.bands for c in crop_lanes)} | "
            f"{sum(1 for c in crop_lanes if c.could_pair)} | "
            f"{sum(1 for c in crop_lanes if c.could_pair_tall)} | "
            f"{sum(1 for c in crop_lanes if c.could_pair_flagged_reference)} |"
        )
    lines.append("")
    return lines


def main(argv: list[str] | None = None) -> int:
    """Compute the bound and write RATIO_BOUND.md beside the run it was computed from."""
    parser = argparse.ArgumentParser(
        prog="python -m tools.phase3.ratio_bound",
        description="Bound the lanes that could contribute a ratio, before the mapping gate.",
    )
    parser.add_argument("--run-dir", type=Path, default=Path("runs/3b1"))
    parser.add_argument("--designations", type=Path, default=DESIGNATIONS_PATH)
    parser.add_argument("--blot-identity", type=Path, default=BLOT_IDENTITY_PATH)
    parser.add_argument(
        "--min-band-height-px", type=int, default=PREREGISTERED_MIN_BAND_HEIGHT_PX
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    _check_criterion_agrees()
    identity_table = read_blot_identities(args.blot_identity)
    designation_table = read_designations(args.designations)
    blocking = blocking_flags()
    counts = lane_counts(args.run_dir, identity_table, blocking, args.min_band_height_px)
    if not counts:
        raise ValueError(
            f"no result documents under {args.run_dir}; run "
            f"'python -m tools.phase3.run_real --detection-only' first. A bound over an empty "
            f"run would report zero lanes and read as a finding about the blots"
        )
    out = args.out or args.run_dir / "RATIO_BOUND.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "\n".join(
            report(counts, designation_table, blocking, args.min_band_height_px, args.run_dir)
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out} over {len(counts)} lanes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
