"""The blot identity table: propose ids, and refuse to decide identity (DEBT D5).

§9 of the frozen pre-registration provides for a ``blot_id`` -- a reference cropped as a
second rectangle is "recorded against the same `blot_id`" -- and ``crop_log.csv`` has no such
column. Five of the 13 parent figures contribute more than one crop, so for those the record
cannot say whether two crops are two blots or one blot with its reference strip, and **neither
form of the ratified stop rule can be evaluated**: §5's "30 ratios or 10 blots, whichever comes
first", nor the 2026-08-19 amendment's minimum of 10 blots.

This module writes a **sibling** file rather than a column in ``crop_log.csv``. That is a
deviation from what draft D5 asks for and it is deliberate: the crop log is the frozen Gate 2
record, byte-identical to what the gate approved, and §9's provision for the column is a
conflict for the human to rule on rather than for this module to resolve by editing the
approved artefact. The conflict is reported, not acted on.

What is proposed here is a *grouping key*, not an identity. It is built from the parent figure
and the panel token and from nothing else, and it can be wrong in both directions: two panels
of one figure may be two different physical blots, and one physical blot may appear as two
panels. **The human rules on blot identity by looking at the images.** A caption or a panel
letter selects candidates; only the image decides -- the same class as the Gate 2 "figure ->
panel" rule.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from pipeline.analyze import require_writable_destination
from tools.phase3.crop_names import CropSetError, parse_crop_name
from tools.phase3.designations import (
    CROP_LOG_PATH,
    REPO_ROOT,
    measurable_crops,
    refuse_to_drop_rulings,
)

BLOT_IDENTITY_PATH = REPO_ROOT / "data/real/blot_identity.csv"

COLUMNS: tuple[str, ...] = (
    "crop_filename",
    "parent_figure",
    "panel",
    "blot_id",
    "identity_source",
    "notes",
)

PROPOSED_PENDING = "proposed_pending_human"
"""Every row this module *proposes*. A proposal from a filename, and nothing more.

A row a human has ruled on keeps its own value and is carried through unchanged.
"""

CONFIRMED_SOURCES: frozenset[str] = frozenset({"image_confirmed_human"})
"""The only value meaning a human looked at the images and ruled.

An allow-list for the same reason the designation table uses one: a reader that treated any
unrecognised string as a confirmation would promote a typo into a blot count, and the blot
count is what the stop rule turns on.
"""

SECTION_9_QUOTE = (
    "if the reference is a separate strip (e.g. loading-control row below), it is cropped "
    "as part of the same rectangle whenever they are vertically contiguous in the figure, "
    "else as a second rectangle recorded against the same blot_id"
)
"""§9 verbatim, so the conflict this file records travels with the file that records it."""

HEADER_NOTE: tuple[str, ...] = (
    "blotquant real-blot identities. One row per measurable crop.",
    "",
    "RULED FROM THE IMAGES AT THE HUMAN GATE, 2026-08-20 (ruling G2). Each blot_id was",
    "PROPOSED from the parent figure stem and the panel token and from nothing else, and a",
    "panel letter is not evidence of physical identity -- TWO PANELS OF ONE FIGURE MAY BE TWO",
    "DIFFERENT PHYSICAL BLOTS, and one physical blot may be split across two panels. The",
    "proposals did not decide that; the human did, by looking at the images.",
    "",
    "What the images decided, where it mattered: PMC13135410_Figure4 panels A/B/C are THREE",
    "SEPARATE PHYSICAL BLOTS -- three distinct experiments with distinct animal cohorts",
    "(4-day vs 2-week denervation; drug vs genotype), 12 lanes per panel each with its own MW",
    "annotation, and categorically different background texture and exposure per panel with",
    "no continuity across panel boundaries. None of that is visible in a filename. All other",
    "ids were ruled distinct.",
    "",
    "identity_source 'image_confirmed_human' means a human ruled from the images.",
    "'proposed_pending_human' means a filename proposed it and nobody has looked. Tools that",
    "read this file refuse every value they do not know and never read one as a confirmation.",
    "",
    "NOT every blot here contributes a ratio: PMC13135388_Figure4__E-Vinculin is the §9",
    "separate reference strip for the E-TIGAR target, which §7 refuses at 255 DN. See",
    "designations.csv. The ratio-contributing blot count is therefore one lower than the",
    "number of rows in this file.",
    "",
    "Why this is a sibling file and not a column in crop_log.csv: §9 of",
    "DECISION_unit_of_analysis.md provides for the column in the crop record --",
    f'"{SECTION_9_QUOTE}"',
    "-- but crop_log.csv is the frozen Gate 2 record and is byte-identical to what the gate",
    "approved. That conflict is reported for the human to rule on, not resolved by editing",
    "the approved artefact. See DEBT draft D5.",
    "",
    "Lines beginning with # are this note. The CSV header is the first line that is not one.",
)


@dataclass(frozen=True)
class BlotIdentity:
    """One row: a crop, the figure and panel it came from, and the id proposed for it."""

    crop_filename: str
    parent_figure: str
    panel: str
    blot_id: str
    identity_source: str
    notes: str

    def as_row(self) -> dict[str, str]:
        """Return this row keyed by :data:`COLUMNS`."""
        return {
            "crop_filename": self.crop_filename,
            "parent_figure": self.parent_figure,
            "panel": self.panel,
            "blot_id": self.blot_id,
            "identity_source": self.identity_source,
            "notes": self.notes,
        }


class BlotIdentityError(CropSetError):
    """The identity table cannot answer the question asked of it, and will not guess.

    A **sibling** of :class:`tools.phase3.crop_names.CorpusError` for the reason
    :class:`tools.phase3.designations.DesignationError` gives: this builder reads the crop set
    through ``measurable_crops``, and a corpus failure raised from there is not an identity
    failure and must neither be reported as one nor be caught by ``except BlotIdentityError``.
    """


def propose_blot_id(parent_stem: str, panel: str) -> str:
    """Return the proposed grouping key for a crop from ``parent_stem``, panel ``panel``.

    The **full parent stem**, not the accession. ``PMC13135410_Figure3`` panel B and
    ``PMC13135410_Figure4`` panel B are two different figures of one article, and keying on
    ``PMC13135410`` alone would merge them into a single proposed blot -- collapsing two
    parents into one, which lowers the blot count and moves the stop rule.
    """
    return f"{parent_stem}_{panel}"


def _all_crops(crop_log: Path) -> list[str]:
    """Return every crop in the frozen log, measurable or not."""
    with crop_log.open(newline="") as handle:
        return [row["crop"] for row in csv.DictReader(handle)]


def validate_blot_ids(rows: list[BlotIdentity]) -> None:
    """Raise if any proposed ``blot_id`` covers more than one (figure, panel) pair.

    The invariant the proposal rests on: a blot_id is a *key* for exactly one figure-and-panel,
    so two crops sharing an id must be two crops of the same panel of the same figure. An id
    spanning two parent figures is not a weaker proposal, it is a wrong one -- it merges blots
    that no evidence says are the same and shrinks the blot count the stop rule is read against.
    """
    spread: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in rows:
        spread[row.blot_id].add((row.parent_figure, row.panel))
    collisions = {
        blot_id: sorted(pairs) for blot_id, pairs in spread.items() if len(pairs) > 1
    }
    if collisions:
        raise BlotIdentityError(
            f"proposed blot_id(s) cover more than one figure-and-panel: {collisions}. A "
            f"blot_id that spans two parent figures merges blots nothing has shown to be the "
            f"same, and lowers the blot count the ratified stop rule is evaluated against"
        )


def build_blot_identities(
    crop_log: Path = CROP_LOG_PATH,
    crops_dir: Path | None = None,
    existing: dict[str, BlotIdentity] | None = None,
) -> list[BlotIdentity]:
    """Return one proposed identity per measurable crop, in crop-log order.

    A crop whose (figure, panel) is shared with another crop in the **full** log -- including
    crops the channel-collapse rule excludes -- carries a note saying so. That is the case §9
    anticipated and the one the missing column was meant to record, and it would vanish if the
    note were computed over the measurable subset alone.

    **An existing row is carried through verbatim**, for the reason
    :func:`tools.phase3.designations.build_designations` gives: once a human has ruled from the
    images, regenerating from filenames would discard the ruling and silently replace it with the
    proposal it was meant to adjudicate.
    """
    crops_dir = crops_dir or crop_log.parent
    existing = existing or {}
    everything = _all_crops(crop_log)
    by_panel: dict[tuple[str, str], list[str]] = defaultdict(list)
    for crop in everything:
        parsed = parse_crop_name(crop)
        by_panel[(parsed.parent_stem, parsed.panel)].append(crop)

    crops = measurable_crops(crop_log, crops_dir)
    refuse_to_drop_rulings(existing, crops)
    rows = []
    for crop in crops:
        if crop in existing:
            rows.append(existing[crop])
            continue
        parsed = parse_crop_name(crop)
        panel_mates = by_panel[(parsed.parent_stem, parsed.panel)]
        siblings = [other for other in panel_mates if other != crop]
        notes = (
            f"shares its figure and panel with {', '.join(sorted(siblings))} in the crop log; "
            f"§9 anticipates exactly this as a target and its reference strip recorded against "
            f"one blot_id, but whether that is what these are is for the human to rule on"
            if siblings
            else ""
        )
        rows.append(
            BlotIdentity(
                crop_filename=crop,
                parent_figure=parsed.parent_stem,
                panel=parsed.panel,
                blot_id=propose_blot_id(parsed.parent_stem, parsed.panel),
                identity_source=PROPOSED_PENDING,
                notes=notes,
            )
        )
    validate_blot_ids(rows)
    return rows


def write_blot_identities(rows: list[BlotIdentity], path: Path) -> Path:
    """Write ``rows`` to ``path`` with the header note above the CSV header row.

    Refuses any destination inside a ground-truth directory, for the reason
    :func:`tools.phase3.designations.write_designations` gives: ``--out`` comes from the command
    line, so PLAN.md's key invariant is enforced rather than assumed.
    """
    validate_blot_ids(rows)
    require_writable_destination(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        handle.write("\n".join(f"# {line}".rstrip() for line in HEADER_NOTE) + "\n")
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_row())
    return path


def read_blot_identities(path: Path = BLOT_IDENTITY_PATH) -> dict[str, BlotIdentity]:
    """Return the table keyed by crop filename, raising on anything malformed."""
    if not path.is_file():
        raise BlotIdentityError(
            f"{path} is missing; blot identity is a human ruling and is not generated on "
            f"demand. Build the proposals with 'python -m tools.phase3.blot_identity'"
        )
    with path.open(newline="", encoding="utf-8") as handle:
        body = [line for line in handle if not line.startswith("#")]
    reader = csv.DictReader(body)
    if tuple(reader.fieldnames or ()) != COLUMNS:
        raise BlotIdentityError(
            f"{path} has columns {reader.fieldnames}; expected exactly {list(COLUMNS)}, in "
            f"that order"
        )
    table: dict[str, BlotIdentity] = {}
    for row in reader:
        crop = (row["crop_filename"] or "").strip()
        if not crop:
            raise BlotIdentityError(f"{path} has a row with a blank crop_filename: {row}")
        if crop in table:
            raise BlotIdentityError(f"{path} lists {crop!r} more than once")
        table[crop] = BlotIdentity(
            crop_filename=crop,
            parent_figure=(row["parent_figure"] or "").strip(),
            panel=(row["panel"] or "").strip(),
            blot_id=(row["blot_id"] or "").strip(),
            identity_source=(row["identity_source"] or "").strip(),
            notes=row["notes"] or "",
        )
    validate_blot_ids(list(table.values()))
    return table


def confirmed_blot_id(table: dict[str, BlotIdentity], crop_filename: str) -> str:
    """Return the human-confirmed blot id for ``crop_filename``, or raise.

    No default and no fallback to the proposal. A blot count assembled from proposals would be
    a count of filenames, and the stop rule would then be evaluated against a naming convention.
    """
    identity = table.get(crop_filename)
    if identity is None:
        raise BlotIdentityError(
            f"{crop_filename!r} has no row in the blot identity table, so which blot it "
            f"belongs to is unrecorded. It does not get a proposed id by default"
        )
    source = identity.identity_source
    if source == PROPOSED_PENDING:
        raise BlotIdentityError(
            f"{crop_filename!r} carries identity_source {source!r}: a grouping key proposed "
            f"from its filename, which no human has ruled on. A panel letter is not evidence "
            f"of physical identity -- the human rules by looking at the images. Confirm the "
            f"row before counting blots with it"
        )
    if source not in CONFIRMED_SOURCES:
        raise BlotIdentityError(
            f"{crop_filename!r} carries identity_source {source!r}, which is not a value this "
            f"reader knows. Known confirmations are {sorted(CONFIRMED_SOURCES)}. An "
            f"unrecognised source is never read as a confirmation"
        )
    if not identity.blot_id:
        raise BlotIdentityError(
            f"{crop_filename!r} is marked {source!r} but its blot_id is blank. A confirmation "
            f"with nothing confirmed is not an identity"
        )
    return identity.blot_id


def main(argv: list[str] | None = None) -> int:
    """Build the blot identity proposals and write them. Returns 0 on success."""
    parser = argparse.ArgumentParser(
        prog="python -m tools.phase3.blot_identity",
        description=(
            "Build data/real/blot_identity.csv from the frozen crop log. A crop with no row "
            "gets a proposal from its parent figure and panel token; a row that already exists "
            "is carried through verbatim, so a human ruling is never overwritten."
        ),
    )
    parser.add_argument("--crop-log", type=Path, default=CROP_LOG_PATH)
    parser.add_argument("--out", type=Path, default=BLOT_IDENTITY_PATH)
    args = parser.parse_args(argv)

    existing = read_blot_identities(args.out) if args.out.is_file() else {}
    rows = build_blot_identities(args.crop_log, existing=existing)
    path = write_blot_identities(rows, args.out)
    distinct = len({row.blot_id for row in rows})
    ruled = sum(1 for row in rows if row.identity_source in CONFIRMED_SOURCES)
    print(
        f"wrote {path}: {len(rows)} crop(s), {distinct} distinct blot_id(s), "
        f"{ruled} ruled by a human, {len(rows) - ruled} pending"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
