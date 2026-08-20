"""Run the shipped pipeline over the 19 Gate 2 real-blot crops and report what happened.

This is the first time in the project's history that `pipeline/` is pointed at an image
`synth/` did not make. It is a *measurement*, not a tuning run: Gate 1 ruling 3 makes real
blots able to falsify, never to select, so nothing this script observes may change a
parameter or a code path. Anomalies become DEBT drafts beside the report.

What it does, and nothing else:

- enumerates the crops from `data/real/crops/crop_log.csv` (the frozen Gate 2 record), never
  by globbing the directory -- a glob would silently measure a file the record does not
  approve, and §10 of the pre-registration defines the approved set as *the rows of that
  file*;
- invokes the pipeline through its documented CLI (`python -m pipeline run`) as a
  subprocess, so what is exercised is the user-facing path rather than an internal call
  this script arranged to succeed;
- refuses to measure a crop whose bytes do not match its recorded `crop_sha256`, because §9
  requires ImageJ to run on the same bytes and a measurement of different bytes could not be
  compared with anything;
- records every crop that errors, verbatim, and continues;
- counts what the pre-registered band-height criterion can decide, and reports the terms of
  §2 it cannot evaluate rather than substituting its own. It does NOT report a number called
  N: two of §2's terms ("usable lane", lane width) are undefined in the pre-registration, and
  a third (the reference band's own height) needs a designation the record may or may not
  carry -- so any N printed here would be an implementer's definition wearing a
  pre-registration's name.

Usage: python -m tools.phase3.run_real
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The pre-registered band criterion, quoted rather than paraphrased. Both sentences that
# state it in DECISION_unit_of_analysis.md are carried here so the report can show the
# reader the wording the count was made against; §8(c) is where the rule is stated and §10
# is where it is applied to the approved set.
CRITERION_QUOTE_8C = (
    "Borderline-resolution rule: no figure is excluded by eye for band size. "
    "All remaining figures are cropped; the crop's measured pixel height decides "
    "against the pre-registered >=15 px band-height minimum. Selection by "
    "measurement, not by impression."
)
CRITERION_QUOTE_10 = (
    "Borderline crops enter measurement; the >=15 px band-height minimum and "
    "lane-width reality decide there, per §8(c)."
)
# §2's ratio definition is two bullets, and they are quoted as two. Joining them into one
# paragraph reads as a single sentence in the source and is not what the frozen file says --
# which verify_quotations() is what caught.
RATIO_DEFINITION_QUOTES = (
    (
        "Per blot, the reference lane/band is designated at image-selection time, from the "
        "figure caption alone, before any measurement is run."
    ),
    (
        "Each remaining usable lane contributes exactly one ratio to that reference: a blot "
        "with L usable lanes yields L\u22121 ratios. No other pairings enter N."
    ),
)

# Column names in crop_log.csv that would carry a machine-resolvable reference designation.
# `panel_note` is deliberately NOT among them. It records a protein name ("...-ACTIN"), and
# turning a protein name into the band id the CLI needs means looking at the image and
# deciding which band is the loading control -- which §2 forbids in terms ("Guessing the
# loading control from the data is forbidden") and Phase 2 Ruling 2 forbids generally.
REFERENCE_COLUMNS: tuple[str, ...] = ("reference_band_id", "reference_band")

# Exit code recorded for a crop the pipeline was never given, because its bytes do not match
# the frozen Gate 2 record. Not a pipeline exit code: no process ran. Negative so it cannot
# collide with one.
NOT_RUN_SHA_MISMATCH = -1

# The band-height minimum DECISION_unit_of_analysis.md §8(c) pre-registered. It is a default
# rather than a hard-coded rule so an exploratory run can vary it -- but the report says
# loudly when it has been varied, because §8(c) is not the authority for any other value.
PREREGISTERED_MIN_BAND_HEIGHT_PX = 15

# Columns this tool reads out of the crop log. Checked up front: a missing one would otherwise
# surface as a KeyError from inside the report writer, after every crop had already been
# measured and with nothing written.
REQUIRED_CROP_LOG_COLUMNS: tuple[str, ...] = ("crop", "crop_sha256", "px", "parent")


@dataclass(frozen=True)
class RunConfig:
    """Every parameter this script uses. Nothing below reads a literal out of a function."""

    crop_log: Path
    crops_dir: Path
    decision_path: Path
    pipeline_config: Path
    out_dir: Path
    min_band_height_px: int
    expected_crop_count: int


@dataclass
class CropResult:
    """One crop's outcome, whether it measured or failed."""

    crop: str
    stem: str
    px: str
    panel_note: str
    sha256_matches_log: bool
    exit_code: int
    wall_clock_s: float
    stdout: str
    stderr: str
    result_path: Path | None = None
    lanes: int | None = None
    bands: int | None = None
    qc_flag_counts: Counter[str] = field(default_factory=Counter)
    image_qc_flags: list[str] = field(default_factory=list)
    emitted_ratios: int | None = None
    emitted_ratios_excluded: int | None = None
    qualifying_bands: int | None = None
    usable_lanes: int | None = None
    excluded_ratio_reasons: Counter[str] = field(default_factory=Counter)

    @property
    def ok(self) -> bool:
        """Return True when the pipeline exited 0 and a result document was parsed."""
        return self.exit_code == 0 and self.lanes is not None


def verify_quotations(config: RunConfig) -> None:
    """Fail unless every quoted passage is actually in the file the report attributes it to.

    The report prints these three passages under "quoted from `<decision_path>`". Without this
    they are constants that happen to have been copied correctly once, and `--decision` could
    name any file -- including one that does not exist -- while the report still attributed
    the text to it. The kickoff required the criterion to be read from the pre-registration
    rather than paraphrased; this is what makes that checkable on every run.

    Whitespace is normalised on both sides because the source wraps its lines and the
    constants wrap differently; nothing else is normalised.
    """
    if not config.decision_path.exists():
        raise FileNotFoundError(
            f"{config.decision_path} is missing, but the report quotes three passages from it "
            f"(§2, §8(c), §10). Refusing to attribute quotations to a file that is not there."
        )
    source = " ".join(config.decision_path.read_text().split())
    for name, quote in (
        ("§8(c)", CRITERION_QUOTE_8C),
        ("§10", CRITERION_QUOTE_10),
        *(("§2", quote) for quote in RATIO_DEFINITION_QUOTES),
    ):
        if " ".join(quote.split()) not in source:
            raise ValueError(
                f"the {name} passage this tool quotes is not in {config.decision_path}. "
                f"Either the pre-registration changed -- in which case the change is the "
                f"finding, and these constants must be re-read from it -- or the wrong file "
                f"was named. The quotation is:\n\n{quote}"
            )


def sha256_of(path: Path) -> str:
    """Return the hex sha256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_crop_rows(config: RunConfig) -> list[dict[str, str]]:
    """Return the approved crop rows, failing loudly if the record is not as expected."""
    if not config.crop_log.exists():
        raise FileNotFoundError(
            f"{config.crop_log} is missing; the approved real-image set is defined as the "
            f"rows of that file (DECISION_unit_of_analysis.md §10) and cannot be inferred "
            f"from the contents of {config.crops_dir}"
        )
    with config.crop_log.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    missing = [c for c in REQUIRED_CROP_LOG_COLUMNS if not rows or c not in rows[0]]
    if missing:
        raise ValueError(
            f"{config.crop_log} is missing required column(s) {', '.join(missing)}; it has "
            f"{', '.join(rows[0]) if rows else 'no columns'}. Every one of "
            f"{', '.join(REQUIRED_CROP_LOG_COLUMNS)} is read while measuring or reporting, so "
            f"this is refused here rather than raising a KeyError after the whole set has run."
        )
    if len(rows) != config.expected_crop_count:
        raise ValueError(
            f"{config.crop_log} has {len(rows)} rows; expected {config.expected_crop_count}. "
            f"The Gate 2 set changed, or the wrong file was read. Refusing to report a "
            f"count against a set this script cannot recognise."
        )
    return rows


def reference_column(rows: list[dict[str, str]]) -> str | None:
    """Return the crop-log column carrying a resolvable reference band id, or None.

    Detection only. Nothing in this module passes the value to ``--reference-band``; the
    report states what remains to be built before it could. See ``_reference_section``.
    """
    present = set(rows[0]) if rows else set()
    for name in REFERENCE_COLUMNS:
        if name in present:
            return name
    return None


def analyse_result(document: dict[str, Any], config: RunConfig) -> dict[str, Any]:
    """Summarise one result document against the pre-registered band criterion."""
    lanes = document["lanes"]
    bands = document["bands"]
    ratios = document["normalization"]["ratios"]

    qc_counts: Counter[str] = Counter()
    for band in bands:
        qc_counts.update(band["qc_flags"])

    qualifying = [b for b in bands if int(b["roi"]["height"]) >= config.min_band_height_px]
    usable_lane_ids = {b["lane_id"] for b in qualifying}

    # §4: QC-excluded ratios are reported "with the flags that excluded them", so the reason
    # and the flags are carried through rather than collapsed into a count.
    excluded_reasons: Counter[str] = Counter()
    for ratio in ratios:
        if not ratio["excluded"]:
            continue
        # schema/result.schema.json requires only lane_id, numerator_band_id, ratio and
        # excluded on a ratio; exclusion_reason and qc_flags are optional. Reading them with
        # .get is following the schema, not defaulting around a missing key -- and their
        # absence is reported as absence rather than as "no flags".
        reason = ratio.get("exclusion_reason") or "exclusion_reason not recorded"
        flags = ",".join(sorted(ratio.get("qc_flags", []))) or "no band flags recorded"
        # The pipeline's reason often already names the flags ("carries QC flags: saturated"),
        # and printing them twice reads as two different facts.
        label = reason if flags in reason else f"{reason} ({flags})"
        excluded_reasons[label] += 1

    return {
        "lanes": len(lanes),
        "bands": len(bands),
        "qc_flag_counts": qc_counts,
        "image_qc_flags": list(document["image_qc_flags"]),
        "emitted_ratios": len(ratios),
        "emitted_ratios_excluded": sum(1 for r in ratios if r["excluded"]),
        "qualifying_bands": len(qualifying),
        "usable_lanes": len(usable_lane_ids),
        "excluded_ratio_reasons": excluded_reasons,
    }


def _explained(explanation: str, process_stderr: str) -> str:
    """Return this tool's explanation, with the process's own stderr kept beneath it."""
    if not process_stderr:
        return f"{explanation} The process wrote nothing to stderr."
    return f"{explanation}\n\nThe process's own stderr follows:\n{process_stderr}"


def run_one(row: dict[str, str], config: RunConfig) -> CropResult:
    """Run the pipeline CLI on one crop and collect everything the report needs."""
    crop_name = row["crop"]
    crop_path = config.crops_dir / crop_name
    stem = Path(crop_name).stem
    if not crop_path.exists():
        raise FileNotFoundError(
            f"{crop_path} is listed in {config.crop_log} but is not in the tree; the "
            f"approved set is incomplete and a report over the remainder would understate N"
        )

    sha_matches = sha256_of(crop_path) == row["crop_sha256"]
    crop_out = config.out_dir / stem
    crop_out.mkdir(parents=True, exist_ok=True)

    if not sha_matches:
        # The gate, not a note. §9 requires ImageJ to run on the SAME crop file,
        # byte-identical; a measurement of different bytes cannot be compared to anything and
        # would still be counted into N if it were taken. So it is not taken. This is not a
        # pipeline failure -- the pipeline was never invoked -- and it is recorded as its own
        # outcome rather than raising, because one re-saved crop should not suppress the
        # report on the other eighteen.
        return CropResult(
            crop=crop_name,
            stem=stem,
            px=row["px"],
            panel_note=(row.get("panel_note") or ""),
            sha256_matches_log=False,
            exit_code=NOT_RUN_SHA_MISMATCH,
            wall_clock_s=0.0,
            stdout="",
            stderr=(
                f"NOT RUN: {crop_path} does not hash to the crop_sha256 recorded in "
                f"{config.crop_log}. DECISION_unit_of_analysis.md §9 requires the measured "
                f"file to be byte-identical to the approved crop, so this crop was not "
                f"measured. Restore the approved bytes, or amend Gate 2's record."
            ),
        )

    command = [
        sys.executable,
        "-m",
        "pipeline",
        "run",
        str(crop_path),
        "--config",
        str(config.pipeline_config),
        "--out",
        str(crop_out),
    ]
    started = time.perf_counter()
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    elapsed = time.perf_counter() - started

    result = CropResult(
        crop=crop_name,
        stem=stem,
        px=row["px"],
        panel_note=(row.get("panel_note") or ""),
        sha256_matches_log=sha_matches,
        exit_code=completed.returncode,
        wall_clock_s=elapsed,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
    )

    result_path = crop_out / f"{stem}.json"
    if completed.returncode == 0 and not result_path.exists():
        # Exit 0 and no document is the one outcome that would otherwise be recorded as an
        # unexplained failure: the table would show "exit 0" beside "the run failed", and the
        # verbatim section would print an empty stderr. Say what actually happened instead.
        # The process's own stderr is kept below this tool's explanation rather than replaced
        # by it: a run that exits 0, writes to stderr and writes no document would otherwise
        # have its only diagnostic discarded by the code reporting the problem.
        result.stderr = _explained(
            f"NO RESULT DOCUMENT: the pipeline exited 0 but {result_path} was not written. "
            f"This is not a refusal by the pipeline and not a crash; it is a successful run "
            f"that produced no result, and it is reported here because nothing else in this "
            f"report would show it.",
            completed.stderr.strip(),
        )
        return result
    if completed.returncode == 0:
        try:
            document = json.loads(result_path.read_text())
        except json.JSONDecodeError as error:
            result.stderr = _explained(
                f"UNREADABLE RESULT DOCUMENT: {result_path} is not valid JSON ({error}). "
                f"The pipeline exited 0, so this crop is reported as unmeasured rather than "
                f"counted from a document that cannot be parsed.",
                completed.stderr.strip(),
            )
            return result
        summary = analyse_result(document, config)
        result.result_path = result_path
        result.lanes = summary["lanes"]
        result.bands = summary["bands"]
        result.qc_flag_counts = summary["qc_flag_counts"]
        result.image_qc_flags = summary["image_qc_flags"]
        result.emitted_ratios = summary["emitted_ratios"]
        result.emitted_ratios_excluded = summary["emitted_ratios_excluded"]
        result.qualifying_bands = summary["qualifying_bands"]
        result.usable_lanes = summary["usable_lanes"]
        result.excluded_ratio_reasons = summary["excluded_ratio_reasons"]
    return result


def _failure_timings(failed: list[CropResult]) -> str:
    """Return the one-line timing summary for crops the pipeline actually ran and refused."""
    if not failed:
        return (
            "No crop reached the pipeline at all, so there are no timings here -- not even "
            "the cost of a refusal."
        )
    slowest = max(failed, key=lambda r: r.wall_clock_s)
    return (
        f"Slowest crop on the failure path: `{slowest.crop}` ({slowest.px}) at "
        f"{slowest.wall_clock_s:.2f} s. Fastest "
        f"{min(r.wall_clock_s for r in failed):.2f} s; total wall clock "
        f"{sum(r.wall_clock_s for r in failed):.2f} s over {len(failed)} crops. These are "
        f"dominated by interpreter start-up and move between runs; they are not a measure of "
        f"anything about the images."
    )


def _crop_table(results: list[CropResult]) -> list[str]:
    """Return the per-crop table rows required by the kickoff: lanes, bands, QC, seconds."""
    lines = [
        "| crop | px | exit | lanes | bands | QC flags fired (by flag) | wall-clock s |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.ok:
            flags = ", ".join(f"`{k}` x{v}" for k, v in sorted(r.qc_flag_counts.items()))
            image_flags = ", ".join(f"`{f}` (image)" for f in sorted(r.image_qc_flags))
            both = "; ".join(part for part in (flags, image_flags) if part) or "none"
            lanes, bands = str(r.lanes), str(r.bands)
        elif r.exit_code == NOT_RUN_SHA_MISMATCH:
            both = "not reached — crop bytes do not match the frozen log, so it was not run"
            lanes = bands = "—"
        else:
            both = "not reached — the run failed before QC"
            lanes = bands = "—"
        exit_shown = "not run" if r.exit_code == NOT_RUN_SHA_MISMATCH else str(r.exit_code)
        lines.append(
            f"| `{r.crop}` | {r.px} | {exit_shown} | {lanes} | {bands} | {both} "
            f"| {r.wall_clock_s:.2f} |"
        )
    return lines


def _n_section(
    results: list[CropResult],
    rows: list[dict[str, str]],
    config: RunConfig,
    ref_column: str | None,
) -> list[str]:
    """Return the N section: what the criterion counts, what it cannot, and why."""
    ok = [r for r in results if r.ok]
    designated = _designations(rows, ref_column)
    # Whether the blot count is ambiguous is a property of THIS set's parent column, not a
    # standing fact. Asserting it over a set where every crop has its own parent would state
    # something false about that set and point at an explanation the report does not contain.
    blots_ambiguous = any(len(crops) > 1 for crops in _blot_groups(rows).values())
    preregistered_height = config.min_band_height_px == PREREGISTERED_MIN_BAND_HEIGHT_PX
    lines = [
        "## Candidate ratio counts under the band-height criterion",
        "",
        "**This section deliberately does not report a number called N.** N is defined by §2 "
        "of the pre-registration, and terms of it cannot be evaluated here -- two are "
        "undefined in the pre-registration itself, and the third depends on a designation "
        "the record supplies for "
        + (
            "none of these crops"
            if not designated
            else f"{len(designated)} of {len(rows)} of these crops"
        )
        + ". What is below is the part that can be counted, plus the exact list of what "
        "blocks the rest. Inventing the missing terms would be selecting a criterion "
        "on real data, which Gate 1 ruling 3 forbids.",
        "",
        f"The band criterion, quoted from `{config.decision_path}` §8(c):",
        "",
        f"> {CRITERION_QUOTE_8C}",
        "",
        "and §10, applying it to the approved set:",
        "",
        f"> {CRITERION_QUOTE_10}",
        "",
        "The ratio definition, §2 -- two bullets, quoted as two:",
        "",
        *[f"> - {quote}" for quote in RATIO_DEFINITION_QUOTES],
        "",
        "### What this run implements, and what it does not",
        "",
        f"**Implemented.** A band qualifies when its result ROI height is "
        f">= {config.min_band_height_px} px. That is the whole of the height half of the "
        f"criterion, applied to the measured ROI and to nothing else.",
        "",
        "**Not implemented, term 1 — \"usable lane\".** §2 counts ratios per *usable lane* and "
        "never defines the word; §8(c) and §10 define only the band-height minimum. This "
        "report uses `lanes carrying >= 1 qualifying band` as a **stand-in, chosen by the "
        "implementer, not by the pre-registration**, and labels the column accordingly. It is "
        "not a quotation and must not be read as one.",
        "",
        "**Not implemented, term 2 — lane width.** §10 says the minimum *\"and lane-width "
        "reality\"* decide together. No lane-width threshold is stated anywhere in the "
        "pre-registration, in `PLAN.md` or in `configs/`, so there is nothing to apply. "
        "Choosing one here would be inventing a selection criterion against real images.",
        "",
        "**Not implemented, term 3 — the reference band's own height.** Every §2 ratio is "
        "taken *against that blot's designated reference*, so a reference band that misses "
        "the criterion takes the blot's whole ratio count to 0, not to L\u22121. The counts below "
        "are therefore an **upper bound** on any eventual N: they can only fall when the "
        "term is applied.",
        "",
    ]
    if not designated:
        lines += [
            "This run cannot apply term 3 at all, because no crop has a designated reference "
            "-- see the next section.",
            "",
        ]
    else:
        lines += [
            f"A designation is recorded in `{ref_column}` for {len(designated)} of "
            f"{len(rows)} crops, but this run still does not apply term 3: the designation is "
            f"reported (next section) and is **not** passed to the pipeline. See the note "
            f"under *What this run does not do with the designation*.",
            "",
        ]
    if not preregistered_height:
        lines += [
            f"**The band criterion was overridden on the command line to "
            f"{config.min_band_height_px} px.** The pre-registered value is "
            f"{PREREGISTERED_MIN_BAND_HEIGHT_PX} px, so the §8(c) and §10 quotations above "
            f"are NOT the authority for the counts below. This run is exploratory and its "
            f"counts are not pre-registered counts.",
            "",
        ]

    lines += ["### Counts", ""]
    if not ok:
        lines += [
            f"**No crop produced a result document ({len(results)} of {len(results)} did "
            f"not), so there is nothing to count.** No band has a measured pixel height, so "
            f"the criterion has nothing to decide against. This is not a count of zero "
            f"surviving ratios -- it is the absence of any measurement to count. See *Errors "
            f"and empty detections, verbatim* below for why.",
            "",
            f"Counts for the >=10-blots stop rule: **no crop contributed any ratio**, 0 of "
            f"{len(results)} measured."
            + (
                " The blot count is in any case a range rather than a number -- see the "
                "reference section below."
                if blots_ambiguous
                else ""
            ),
            "",
        ]
    else:
        lines += [
            "| crop | lanes with >= 1 qualifying band (stand-in, see above) "
            "| upper-bound ratios (L\u22121) | bands >= threshold | ratios the pipeline emitted "
            "| of which QC-excluded |",
            "|---|---|---|---|---|---|",
        ]
        upper_bound = 0
        for r in ok:
            usable = r.usable_lanes or 0
            ratios = max(usable - 1, 0)
            upper_bound += ratios
            lines.append(
                f"| `{r.stem}` | {usable} | {ratios} | {r.qualifying_bands} "
                f"| {r.emitted_ratios} | {r.emitted_ratios_excluded} |"
            )
        lines += [
            "",
            f"**Upper bound on N = {upper_bound}** over {len(ok)} measured **crops**. Not "
            f"N, and not per blot: see the three unimplemented terms above."
            + (
                " The number of *blots* in this set is a range rather than a number -- see "
                "the reference section below -- so the >=10-blots stop rule cannot be read "
                "off this table."
                if blots_ambiguous
                else " Every crop in this set has its own parent figure, so crop count and "
                "blot count coincide here; that is a property of this set, not a general one."
            ),
            "",
        ]
        excluded_flags: Counter[str] = Counter()
        for r in ok:
            excluded_flags.update(r.excluded_ratio_reasons)
        if excluded_flags:
            lines += [
                "§4 requires QC-excluded ratios to be reported *\"with the flags that "
                "excluded them\"*. Across the measured crops:",
                "",
            ]
            lines += [f"- `{k}`: {v}" for k, v in sorted(excluded_flags.items())]
            lines.append("")
        lines += [
            "The last two columns are what the pipeline emitted under the mode this run's "
            "config ships. They are **not** the pre-registered ratio set: under §8(a) the "
            "pre-registered real-blot set is housekeeping-only, and a total-protein ratio has "
            "no reference lane in it at all. They are reported so the human can see what the "
            "run produced.",
            "",
        ]

    if len(designated) < len(rows):
        lines += [
            f"Until a reference designation exists for every crop, {len(rows) - len(designated)}"
            f" of {len(rows)} are `no reference assigned -- excluded, needs human ruling`, and "
            f"the pre-registered N is **undefined rather than zero**.",
            "",
        ]
    return lines


def _blot_groups(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    """Group crops by the parent figure they were cut from, per crop_log.csv.

    This is the only grouping the frozen record supports: `parent` is a recorded column.
    It is NOT a blot grouping -- one figure can hold several unrelated blots -- which is
    exactly the gap §9's `blot_id` was meant to fill and `crop_log.csv` does not have.
    """
    groups: dict[str, list[str]] = {}
    for row in rows:
        groups.setdefault(row["parent"], []).append(row["crop"])
    return groups


def _shared_parent_block(rows: list[dict[str, str]]) -> list[str]:
    """Return the blot-count ambiguity finding, or nothing when the set has none.

    Emitted in **both** designation branches. It is a property of the crop log's `parent`
    column and has nothing to do with whether a reference is designated -- and the human doing
    what draft D4 asks (adding a reference column) must not thereby erase draft D5's evidence
    from the next report.
    """
    shared = {parent: crops for parent, crops in _blot_groups(rows).items() if len(crops) > 1}
    if not shared:
        return []
    lines = [
        f"**{len(shared)} parent figure(s) contribute more than one crop, and the record "
        f"cannot say which of those crops are one blot.** §9 provides for a reference cropped "
        f"as a second rectangle *\"recorded against the same blot_id\"*, but the crop log has "
        f"no `blot_id` column, so any target/reference pairing among these would have to be "
        f"inferred from filenames:",
        "",
    ]
    for parent, crops in sorted(shared.items()):
        lines.append(f"- `{parent}` -> {', '.join(f'`{c}`' for c in sorted(crops))}")
    lines += [
        "",
        f"This also makes the blot count ambiguous. If every crop is its own blot the set is "
        f"{len(rows)} blots; if crops sharing a parent are one blot it is "
        f"{len(_blot_groups(rows))}. The record does not say which, so the >=10-blots stop "
        f"rule cannot be evaluated against it without a human ruling.",
        "",
    ]
    return lines


def _designations(rows: list[dict[str, str]], ref_column: str | None) -> dict[str, str]:
    """Return crop -> reference band id for every row that actually records one.

    A header is not a designation. A row whose cell is blank has no assignment, and the
    kickoff is explicit that such a crop is listed as "no reference assigned -- excluded,
    needs human ruling" rather than reported as designated with an empty id.
    """
    if ref_column is None:
        return {}
    return {r["crop"]: r[ref_column].strip() for r in rows if (r.get(ref_column) or "").strip()}


def _reference_section(rows: list[dict[str, str]], ref_column: str | None) -> list[str]:
    """Return the reference-designation section (DEBT S6 is a human input)."""
    lines = ["## Reference-band designation (DEBT S6 -- human input)", ""]
    designated = _designations(rows, ref_column)
    unassigned = [r for r in rows if r["crop"] not in designated]

    if designated:
        lines += [
            f"{len(designated)} of {len(rows)} crops carry a designation in the "
            f"`{ref_column}` column of the crop log, as recorded at selection time.",
            "",
            "| crop | reference band id |",
            "|---|---|",
        ]
        lines += [f"| `{crop}` | `{band}` |" for crop, band in sorted(designated.items())]
        lines += [
            "",
            "### What this run does not do with the designation",
            "",
            "**It reports the designation; it does not measure against it.** The pipeline was "
            "invoked without `--reference-band`, under the mode this run's config ships, so "
            "the ratios reported above are that mode's ratios and not the §2 ratios. "
            "Producing those needs three things beyond the column: `--reference-band` plumbed "
            "through this tool, a housekeeping config (README.md: *\"no housekeeping config "
            "ships\"*, and `configs/` is frozen for this phase), and a two-pass flow, because "
            "band ids come from a previous run's `bands[]`. None of that is built here -- "
            "building it against a designation nothing has yet supplied would be writing "
            "speculative measurement code, which is what Gate 1 ruling 3 rules out.",
            "",
        ]

    if unassigned:
        headline = (
            f"**No crop has a resolvable reference designation. All {len(rows)} are "
            f"`no reference assigned -- excluded, needs human ruling`.**"
            if not designated
            else f"**{len(unassigned)} of {len(rows)} crops are "
            f"`no reference assigned -- excluded, needs human ruling`.** The `{ref_column}` "
            f"column exists but their cell is empty; a header is not a designation."
        )
        columns = list(rows[0]) if rows else []
        lines += [headline, ""]
        if ref_column is None:
            lines += [
                "The pre-registration fixes the designation at selection time (§2: *\"the "
                "reference lane/band is designated at image-selection time, from the figure "
                "caption alone\"*), and under §8(a) the pre-registered real-blot set is "
                "housekeeping-only. But nothing in the crop log records the designation in a "
                "form the pipeline can consume. Its columns are "
                f"{', '.join(f'`{c}`' for c in columns)} -- none of "
                f"{', '.join(f'`{c}`' for c in REFERENCE_COLUMNS)}.",
                "",
                "`panel_note` names the reference *protein* where it names one at all, and "
                "that is as far as the record goes. The CLI needs a **band id** "
                "(`--reference-band L0_B2`, ids taken from a previous run's `bands[]`). "
                "Turning a protein name into a band id means looking at the image and "
                "deciding which band is the loading control -- which §2 forbids in terms "
                "(*\"Guessing the loading control from the data is forbidden\"*) and Phase 2 "
                "Ruling 2 forbids generally. So this run does not guess, and lists them here "
                "instead.",
                "",
            ]
        lines += ["| crop | panel_note (as recorded) |", "|---|---|"]
        lines += [
        f"| `{r['crop']}` | " + (f"`{note}` |" if (note := (r.get("panel_note") or "").strip())
                                  else "not recorded |")
        for r in unassigned
    ]
        lines.append("")

    lines += _shared_parent_block(rows)
    return lines


def write_report(
    results: list[CropResult],
    rows: list[dict[str, str]],
    config: RunConfig,
    ref_column: str | None,
) -> Path:
    """Write REPORT.md from the run's artifacts and return its path."""
    # Imported here rather than at module scope: this tool shells out to the pipeline CLI and
    # does not otherwise depend on the package, and a top-level import would make an unrelated
    # import error in pipeline/ look like a failure of this reporting tool.
    import pipeline

    if not results:
        raise ValueError(
            "write_report was given no crop results. A report over an empty set would state "
            "a count of nothing as though it were a measurement; the caller must pass the "
            "results of the approved set."
        )
    ok = [r for r in results if r.ok]
    # Three outcomes, not two. A crop whose bytes do not match the frozen log was never given
    # to the pipeline, so calling it a failure would attribute to the pipeline something it
    # never saw -- and would put a fabricated exit code in the table.
    not_run = [r for r in results if r.exit_code == NOT_RUN_SHA_MISMATCH]
    failed = [r for r in results if not r.ok and r not in not_run]

    lines = [
        f"# blotquant over the {len(rows)} crops of `{config.crop_log}`",
        "",
        "**Gate 1 ruling 3 is in force: real blots may falsify, never select.** This run "
        "changed no parameter and no code path. Every statement below is derived from this "
        "run's own artifacts and from the crop log named above; the report makes no claim "
        "about any other image set.",
        "",
        "## Provenance",
        "",
        f"- pipeline version: `{pipeline.PIPELINE_VERSION}`",
        f"- python: `{sys.version.split()[0]}`",
        f"- config: `{config.pipeline_config}` (sha256 "
        f"`{sha256_of(config.pipeline_config)[:16]}…`)",
        f"- crop set: the {len(rows)} rows of `{config.crop_log}`, enumerated from the file",
        f"- band criterion: >= {config.min_band_height_px} px ROI height",
        f"- invocation, per crop: `python -m pipeline run <crop> --config "
        f"{config.pipeline_config} --out {config.out_dir}/<crop>/`",
        "",
        "## Outcome in one line",
        "",
        f"**{len(ok)} of {len(results)} crops produced a result document; {len(failed)} "
        f"failed"
        + (f"; {len(not_run)} were not run.**" if not_run else ".**"),
        "",
        "## Per crop",
        "",
    ]
    lines += _crop_table(results)
    lines += ["", "## Timing (E10 anchor)", ""]

    if ok:
        slowest_ok = max(ok, key=lambda r: r.wall_clock_s)
        lines += [
            f"Slowest crop that measured: `{slowest_ok.crop}` ({slowest_ok.px}) at "
            f"**{slowest_ok.wall_clock_s:.2f} s**.",
            "",
        ]
    else:
        lines += [
            "**No real-data timing anchor was obtained.** DEBT E10's recorded timings (read "
            "them from `DEBT.md`; none is quoted here, because this run measured none of "
            "them) still have no real-image counterpart: every crop failed before any "
            "measurement work began, so the times below are the cost of loading a file and "
            "raising, not the cost of analysing a blot.",
            "",
            _failure_timings(failed),
            "",
        ]

    lines += _n_section(results, rows, config, ref_column)
    lines += _reference_section(rows, ref_column)

    lines += ["## Crop byte-identity against the frozen log", ""]
    mismatched = [r for r in results if not r.sha256_matches_log]
    if mismatched:
        lines += [
            f"**{len(mismatched)} crop(s) do not hash to their recorded `crop_sha256`.** §9 "
            "requires ImageJ to run on byte-identical files; these are not the approved bytes:",
            "",
        ]
        lines += [f"- `{r.crop}`" for r in mismatched]
    else:
        lines.append(
            f"All {len(results)} crops hash to the `crop_sha256` recorded for them in "
            f"`{config.crop_log}`, so the bytes measured here are the bytes that record "
            f"approves."
        )
    lines.append("")

    if not_run:
        lines += [
            "## Crops that were not run",
            "",
            f"{len(not_run)} of {len(results)} crops were never given to the pipeline, so "
            f"they have no exit code and no output. The text below is this tool's, not a "
            f"process's:",
            "",
        ]
        for r in not_run:
            lines += [f"### `{r.crop}` — not run", "", "```", r.stderr, "```", ""]

    lines += ["## Errors and empty detections, verbatim", ""]
    if not failed:
        lines += [
            f"None: every crop that was run produced a result document"
            f"{' (see the section above for those that were not run)' if not_run else ''}.",
            "",
        ]
    else:
        lines += [
            f"{len(failed)} of {len(results)} crops, labelled by stream. `[stdout]` is "
            "always the process's own. `[stderr]` is the process's own where the pipeline "
            "refused the crop; where it exited 0 but wrote no result document, or wrote one "
            "that will not parse, `[stderr]` opens with this tool's explanation of that and "
            "then carries the process's own stderr beneath it -- those two outcomes say so in "
            "their first words:",
            "",
        ]
        for r in failed:
            streams = (("stderr", r.stderr), ("stdout", r.stdout))
            body = "\n\n".join(
                f"[{stream}]\n{text}" for stream, text in streams if text
            ) or "(no output on either stream)"
            lines += [f"### `{r.crop}` — exit {r.exit_code}", "", "```", body, "```", ""]
    empty = [r for r in ok if r.bands == 0 or r.lanes == 0]
    if empty:
        lines += ["### Empty detections", ""]
        lines += [f"- `{r.crop}`: {r.lanes} lane(s), {r.bands} band(s)" for r in empty]
        lines.append("")

    report_path = config.out_dir / "REPORT.md"
    report_path.write_text("\n".join(lines))
    return report_path


def parse_args(argv: list[str] | None = None) -> RunConfig:
    """Return the run configuration; every value is explicit and echoed into the report."""
    parser = argparse.ArgumentParser(
        prog="python -m tools.phase3.run_real",
        description="Run the shipped pipeline over the Gate 2 real crops and report.",
    )
    parser.add_argument("--crop-log", type=Path, default=Path("data/real/crops/crop_log.csv"))
    parser.add_argument("--crops-dir", type=Path, default=Path("data/real/crops"))
    parser.add_argument(
        "--decision", type=Path, default=Path("data/real/DECISION_unit_of_analysis.md")
    )
    parser.add_argument("--config", type=Path, default=Path("configs/default.yaml"))
    parser.add_argument("--out", type=Path, default=Path("runs/3b0-real"))
    parser.add_argument(
        "--min-band-height-px",
        type=int,
        default=PREREGISTERED_MIN_BAND_HEIGHT_PX,
        help="the pre-registered band-height minimum (DECISION_unit_of_analysis.md §8(c))",
    )
    parser.add_argument("--expected-crops", type=int, default=19)
    args = parser.parse_args(argv)
    return RunConfig(
        crop_log=args.crop_log,
        crops_dir=args.crops_dir,
        decision_path=args.decision,
        pipeline_config=args.config,
        out_dir=args.out,
        min_band_height_px=args.min_band_height_px,
        expected_crop_count=args.expected_crops,
    )


def main(argv: list[str] | None = None) -> int:
    """Run every approved crop, write the report, and return 0 even when crops failed.

    A non-zero exit would mean *this script* failed. A crop that the pipeline refuses is
    the finding the run exists to produce, and is reported, not raised.
    """
    config = parse_args(argv)
    if not config.pipeline_config.exists():
        raise FileNotFoundError(f"{config.pipeline_config} is missing; no parameters to run with")
    config.out_dir.mkdir(parents=True, exist_ok=True)

    verify_quotations(config)
    rows = read_crop_rows(config)
    ref_column = reference_column(rows)

    results: list[CropResult] = []
    for index, row in enumerate(rows, start=1):
        result = run_one(row, config)
        results.append(result)
        state = "ok" if result.ok else f"FAILED (exit {result.exit_code})"
        print(f"[{index:>2}/{len(rows)}] {row['crop']}: {state} in {result.wall_clock_s:.2f}s")

    report_path = write_report(results, rows, config, ref_column)
    ok = sum(1 for r in results if r.ok)
    print(f"\n{ok} of {len(results)} crops measured; wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
