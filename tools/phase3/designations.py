"""The reference-band designation table: build it, read it, and refuse to default it (DEBT D4).

§2 of the pre-registration fixes the reference lane/band per blot "at image-selection time,
from the figure caption alone", and §8(a) makes the real set housekeeping-only. Nothing in
``data/real/`` records which band that is: ``crop_log.csv`` carries ``crop, crop_sha256, px,
parent, parent_sha256, panel_note`` and no reference column, so the pre-registered ratio set
cannot be constructed at all -- N is undefined rather than small.

This module builds the record that closes the *machine-readability* half of that gap, and
nothing else. It writes candidates parsed from the filename, marks every row it proposes
pending, and **confirms nothing**: designating a reference band is a measurement input, not a
parameter choice (DEBT S6), so Gate 1 ruling 3 does not reach it and the implementer may not
make it. The human confirms each row against the figure caption, which happened at the
2026-08-20 gate; a row already ruled on is carried through verbatim.

The read path is where the discipline has to live, because that is where a default would be
invisible. :func:`confirmed_reference_label` raises for a pending row, for a blank reference,
for a source value it does not recognise, and for a crop that is not in the table. It never
returns a fallback. A caller that gets a value from it has a value a human wrote down.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pipeline.analyze import require_writable_destination
from pipeline.errors import UnsupportedImageError
from pipeline.load import load_image
from tools.phase3.crop_names import (
    PENDING_SOURCES,
    REAL_CROP_POLARITY,
    TARGET_SEPARATOR,
    CorpusError,
    CropName,
    CropSetError,
    parse_crop_name,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
"""Anchored to this file, not to the working directory. These tools are run from anywhere."""

DESIGNATIONS_PATH = REPO_ROOT / "data/real/designations.csv"
CROP_LOG_PATH = REPO_ROOT / "data/real/crops/crop_log.csv"

COLUMNS: tuple[str, ...] = (
    "crop_filename",
    "target_label",
    "reference_label",
    "source_of_designation",
    "notes",
)
"""The columns, in order. Fixed by the phase kickoff; a reader checks them rather than trusts."""

CONFIRMED_SOURCES: frozenset[str] = frozenset({"caption_confirmed_human"})
"""Values meaning a human confirmed the row against the caption **and it yields ratios**.

An allow-list, not a deny-list. If the human invents a new vocabulary word, the reader must
fail on it and be taught the word deliberately -- the alternative is a reader that treats
every string it does not recognise as a confirmation, which turns a typo into a measurement.
"""

NO_RATIO_SOURCES: frozenset[str] = frozenset({"reference_strip_confirmed_human"})
"""Values meaning a human ruled on the row and the answer is that it contributes no ratio.

The §9 case: a crop that is a *separate reference strip* rather than a panel with a target in it.
``PMC13135388_Figure4__E-Vinculin`` is one -- the human confirmed (G1, 2026-08-20) that it is the
vinculin strip serving the E-TIGAR target, and E-TIGAR is refused under §7 at 255 DN, so the strip
has nothing to be a reference *for*.

Kept apart from :data:`CONFIRMED_SOURCES` rather than folded into it, because the two answer
different questions. Both mean "a human has ruled here"; only one means "measure this". A single
confirmed vocabulary would make a ruled-and-excluded row indistinguishable from an unruled one at
exactly the moment N is being counted, which is when the difference matters most.
"""

RULED_SOURCES: frozenset[str] = CONFIRMED_SOURCES | NO_RATIO_SOURCES
"""Every value meaning a human has looked at this row. Disjoint from :data:`PENDING_SOURCES`."""

HEADER_NOTE: tuple[str, ...] = (
    "blotquant real-blot reference-band designations. One row per measurable crop.",
    "",
    "CONFIRMED AT THE HUMAN GATE, 2026-08-20 (ruling G1). Each row was checked against its",
    "figure caption and axis labels. Pre-registration DECISION_unit_of_analysis.md §2: the",
    "reference lane/band is designated from the figure caption alone, and 'Guessing the",
    "loading control from the data is forbidden'. These rows are that designation; they were",
    "PROPOSED from the crop filename, which is a naming convention and not data, and the",
    "proposal was adjudicated rather than accepted. One row was CORRECTED in the process --",
    "see PMC13135410_Figure3__B, which the parser read as one hyphenated target and which in",
    "fact carries two.",
    "",
    "source_of_designation vocabulary, and what each value licenses:",
    "  caption_confirmed_human        a human confirmed the row; it contributes ratios.",
    "  reference_strip_confirmed_human a human ruled on the row and it contributes NO ratio:",
    "                                 a §9 separate reference strip, not a target panel.",
    "  parsed_*_pending_human         a parser's proposal. NOT confirmed, NOT measurable.",
    "Tools that read this file refuse every value they do not know, and refuse to treat an",
    "unrecognised value as a confirmation. Nothing is measured against a pending row.",
    "",
    "target_label may name MORE THAN ONE target, separated by '|'. A crop carrying two",
    "targets against one shared reference contributes one ratio PER TARGET; the two are not",
    "folded into a single label (ruled at the gate).",
    "",
    "Lines beginning with # are this note. The CSV header is the first line that is not one.",
)
"""Stated in the file itself, because a reader who opens the CSV must not have to be told."""


@dataclass(frozen=True)
class Designation:
    """One row of the designation table, as written and as read back."""

    crop_filename: str
    target_label: str
    reference_label: str
    source_of_designation: str
    notes: str

    def __post_init__(self) -> None:
        """Refuse to exist in a shape its ``source_of_designation`` contradicts.

        Validation lives here rather than in the reader because the reader is not the only way a
        row reaches the file: ``write_designations`` writes whatever it is handed, and this table
        is hand-edited by a human. A check that only runs on read lets a malformed row be written
        now and rejected later, by someone who did not write it.

        The invariant is that the vocabulary word and the row's *shape* must agree, in **both**
        directions, because the word is otherwise the only thing keeping a row out of N:

        * a row confirmed as measurable must name at least one target and a reference -- a
          confirmed row with no target would contribute a nameless ratio, and one with no
          reference has nothing to divide by;
        * a row ruled to contribute nothing must name **no** target -- a no-ratio row carrying a
          target is a target silently excluded from N.

        An unrecognised source imposes no *vocabulary-versus-shape* constraint here -- the two
        checks above it still bind every row, whatever its source, because a blank or repeated
        target is malformed under any vocabulary. :func:`confirmed_reference_label` refuses the
        unknown word on read. That split is deliberate: this class refuses shapes it knows to be
        wrong, and the reader refuses vocabulary it does not know.
        """
        targets = self.target_labels
        if any(not target for target in targets):
            raise DesignationError(
                f"{self.crop_filename!r} has target_label {self.target_label!r}, which parses "
                f"to a blank target. Targets are separated by {TARGET_SEPARATOR!r} and every "
                f"one must be named"
            )
        if len(set(targets)) != len(targets):
            raise DesignationError(
                f"{self.crop_filename!r} names the same target more than once in "
                f"{self.target_label!r}; a target counted twice would contribute two ratios "
                f"from one measurement"
            )
        if self.source_of_designation in CONFIRMED_SOURCES:
            if not targets:
                raise DesignationError(
                    f"{self.crop_filename!r} is marked {self.source_of_designation!r} but names "
                    f"no target. A row confirmed as measurable contributes one ratio per "
                    f"target, so a row with no target contributes an undefined number of them. "
                    f"If it is a reference strip rather than a target panel, say so with one of "
                    f"{sorted(NO_RATIO_SOURCES)}"
                )
            if not self.reference_label:
                raise DesignationError(
                    f"{self.crop_filename!r} is marked {self.source_of_designation!r} but its "
                    f"reference_label is blank. A confirmation with nothing confirmed is not a "
                    f"designation"
                )
        if self.source_of_designation in NO_RATIO_SOURCES and targets:
            raise DesignationError(
                f"{self.crop_filename!r} is marked {self.source_of_designation!r}, which means "
                f"it contributes no ratio, but it names target(s) {list(targets)}. A target on "
                f"a no-ratio row is a target silently excluded from N. Either it is a reference "
                f"strip and carries no target, or it is a target panel and the source is wrong"
            )

    @property
    def target_labels(self) -> tuple[str, ...]:
        """Return the targets this crop carries, in the order written.

        One cell, one or more targets. A crop with two targets sharing a reference contributes a
        ratio per target rather than one ratio for a merged label -- which is the whole reason the
        human ruled against folding them (G1).
        """
        if not self.target_label:
            return ()
        return tuple(part.strip() for part in self.target_label.split(TARGET_SEPARATOR))

    def as_row(self) -> dict[str, str]:
        """Return this designation as a CSV row keyed by :data:`COLUMNS`."""
        return {
            "crop_filename": self.crop_filename,
            "target_label": self.target_label,
            "reference_label": self.reference_label,
            "source_of_designation": self.source_of_designation,
            "notes": self.notes,
        }


class DesignationError(CropSetError):
    """The designation table cannot answer the question that was asked of it.

    Every raise is a refusal to guess: a crop with no row, a row still pending, a row whose
    reference cell is blank, a source value outside the known vocabulary, or a duplicate row.

    A **sibling** of :class:`tools.phase3.crop_names.CorpusError` under
    :class:`tools.phase3.crop_names.CropSetError`, not a parent or a child of it: a wrong table
    and a wrong corpus send a reader to different files, so ``except DesignationError`` must not
    swallow a corpus failure and ``except CorpusError`` must not swallow this.
    """


def _from_crop_name(parsed: CropName) -> Designation:
    """Return the pending designation a parsed filename supports."""
    return Designation(
        crop_filename=parsed.crop_filename,
        target_label=parsed.target_label,
        reference_label=parsed.reference_label or "",
        source_of_designation=parsed.source,
        notes=parsed.notes,
    )


def _verify_crop_digest(path: Path, expected: str, crop_log: Path) -> None:
    """Raise unless ``path``'s bytes match the digest the frozen crop log records for it.

    Raises :class:`tools.phase3.crop_names.CorpusError`, which is a sibling of the table errors
    rather than one of them: what is wrong here is the corpus, and this function is reached from
    both builders, so reporting it as either builder's error would name the wrong thing.

    ``tools/phase3/run_real.py`` refuses to measure a crop whose bytes do not match its recorded
    ``crop_sha256``, and the tables built here are derived artefacts of the same crops -- both
    the designation rows and the identity rows are keyed to bytes this function has checked.
    Without the check, a table would silently describe a file the Gate 2 record never approved.
    """
    if not path.is_file():
        raise CorpusError(
            f"{path} is listed in {crop_log} but is not in the tree; the approved crop set is "
            f"defined as the rows of that log and every row must resolve to a file"
        )
    measured = hashlib.sha256(path.read_bytes()).hexdigest()
    if measured != expected:
        raise CorpusError(
            f"{path} has sha256 {measured}, but {crop_log} records {expected}. These are not "
            f"the bytes Gate 2 approved, and a table derived from them would describe a file "
            f"nobody reviewed. Restore the crop rather than re-recording the digest"
        )


def measurable_crops(crop_log: Path, crops_dir: Path) -> list[str]:
    """Return the approved crops the loader can actually read, in crop-log order.

    Measurability is *measured*, not listed: each crop is put through
    :func:`pipeline.load.load_image`, and the ones the ratified channel-collapse rule admits
    are the ones that get a row. A hard-coded list here would drift from the loader the first
    time either changed, and this table would then designate references for crops that no
    longer load, or omit crops that now do.

    Only :class:`pipeline.errors.UnsupportedImageError` means "not measurable" -- that is the
    class the §7 refusal is raised as. Every other pipeline error is a *broken file*, not an
    excluded one: a truncated PNG raises ``UnsupportedFormatError`` and an unreadable pixel type
    raises ``UnsupportedBitDepthError``, and catching those here would silently reclassify a
    corrupt crop as one the amendment excludes, drop it out of both committed tables, and take
    the row counts down with it. They propagate.
    """
    with crop_log.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    measurable = []
    for row in rows:
        crop, path = row["crop"], crops_dir / row["crop"]
        _verify_crop_digest(path, row["crop_sha256"], crop_log)
        try:
            load_image(path, REAL_CROP_POLARITY)
        except UnsupportedImageError:
            continue
        measurable.append(crop)
    return measurable


def build_designations(
    crop_log: Path = CROP_LOG_PATH,
    crops_dir: Path | None = None,
    existing: dict[str, Designation] | None = None,
) -> list[Designation]:
    """Return one designation per measurable crop, in crop-log order.

    **A row that already exists is carried through verbatim; only a crop with no row gets a
    freshly parsed proposal.** Once the human has confirmed or corrected a row, this file stops
    being purely generated, and a builder that regenerated it from filenames would silently
    discard the human's rulings -- including the one row (``PMC13135410_Figure3__B``) whose
    confirmation *contradicts* what the parser proposes, which is precisely the row a regeneration
    would be most damaging to lose.

    So this is idempotent over the committed table by construction, and the idempotence is
    asserted rather than assumed: ``tests/test_designations.py`` rebuilds over the committed file
    and requires the bytes to be unchanged.
    """
    crops_dir = crops_dir or crop_log.parent
    existing = existing or {}
    crops = measurable_crops(crop_log, crops_dir)
    refuse_to_drop_rulings(existing, crops)
    return [
        existing[crop] if crop in existing else _from_crop_name(parse_crop_name(crop))
        for crop in crops
    ]


def refuse_to_drop_rulings(existing: Mapping[str, object], crops: Sequence[str]) -> None:
    """Raise if an existing row names a crop the rebuilt set no longer contains.

    Carrying rulings through is only half of protecting them. The other half is refusing to
    *silently drop* one: both builders return a row per measurable crop, so if the loader's
    admissible set narrows -- and the ruled divergence bound is exactly the kind of thing a
    later amendment could move -- a rebuild would return fewer rows than it was given, write
    the shorter table, and delete a human ruling with an exit code of 0.

    Deleting a ruling may well be the right outcome when a crop stops being measurable. It is
    not an outcome a tool run should reach without anyone deciding it, which is why this raises
    rather than warns.
    """
    orphaned = sorted(set(existing) - set(crops))
    if orphaned:
        raise CorpusError(
            f"the table being rebuilt holds row(s) for {orphaned}, which the current crop set "
            f"no longer makes measurable. Rebuilding would drop them, and a row may carry a "
            f"human ruling that no tool run may delete. Decide what should happen to those rows "
            f"and remove them deliberately, or restore the crops"
        )


def write_designations(designations: list[Designation], path: Path) -> Path:
    """Write ``designations`` to ``path`` with the header note above the CSV header row.

    Refuses any destination inside a ground-truth directory. ``--out`` is a path from the
    command line, so PLAN.md's first key invariant -- ``data/ground_truth/`` is written only by
    ``python -m synth`` -- is enforced here rather than left to convention, the same way
    :func:`pipeline.analyze.write_result` enforces it.
    """
    require_writable_destination(path.parent)
    # Validate before writing, as write_blot_identities does. Constructing a Designation already
    # enforces the row invariants, but a caller can mutate nothing and still hand over a list
    # with two rows for one crop, which only the reader would otherwise catch.
    seen: set[str] = set()
    for designation in designations:
        if designation.crop_filename in seen:
            raise DesignationError(
                f"refusing to write {path}: {designation.crop_filename!r} appears more than "
                f"once, and two designations for one crop cannot both be the caption's"
            )
        seen.add(designation.crop_filename)
    lines = [f"# {line}".rstrip() for line in HEADER_NOTE]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        for designation in designations:
            writer.writerow(designation.as_row())
    return path


def read_designations(path: Path = DESIGNATIONS_PATH) -> dict[str, Designation]:
    """Return the table keyed by crop filename, raising on anything malformed.

    Skips the ``#`` header note. Raises :class:`DesignationError` for missing or reordered
    columns, a duplicate crop, or a blank crop cell -- each of which would otherwise turn into
    a silently short or silently overwritten table.
    """
    if not path.is_file():
        raise DesignationError(
            f"{path} is missing; the reference designation table is a human input and is not "
            f"generated on demand. Build it with 'python -m tools.phase3.designations'"
        )
    with path.open(newline="", encoding="utf-8") as handle:
        body = [line for line in handle if not line.startswith("#")]
    reader = csv.DictReader(body)
    if tuple(reader.fieldnames or ()) != COLUMNS:
        raise DesignationError(
            f"{path} has columns {reader.fieldnames}; expected exactly {list(COLUMNS)}, in "
            f"that order. A renamed or reordered column changes what every row means"
        )
    table: dict[str, Designation] = {}
    for row in reader:
        crop = (row["crop_filename"] or "").strip()
        if not crop:
            raise DesignationError(f"{path} has a row with a blank crop_filename: {row}")
        if crop in table:
            raise DesignationError(
                f"{path} lists {crop!r} more than once; two designations for one crop cannot "
                f"both be the caption's"
            )
        try:
            designation = Designation(
                crop_filename=crop,
                target_label=(row["target_label"] or "").strip(),
                reference_label=(row["reference_label"] or "").strip(),
                source_of_designation=(row["source_of_designation"] or "").strip(),
                notes=row["notes"] or "",
            )
        except DesignationError as error:
            # Re-raised with the file named. The invariant belongs to the row, but whoever has
            # to fix it needs to know which file the row is in.
            raise DesignationError(f"{path}: {error}") from error
        table[crop] = designation
    return table


def confirmed_reference_label(table: dict[str, Designation], crop_filename: str) -> str:
    """Return the human-confirmed reference label for ``crop_filename``, or raise.

    The whole point of this module. There is no default, no fallback to the parsed candidate,
    and no "if in doubt, use the last label": every one of those would put a designation
    nobody read a caption for into a measurement, which §2 forbids in terms.
    """
    designation = table.get(crop_filename)
    if designation is None:
        raise DesignationError(
            f"{crop_filename!r} has no row in the designation table, so no reference band is "
            f"designated for it. It is excluded and needs a human ruling; it is not given a "
            f"default"
        )
    source = designation.source_of_designation
    if source in PENDING_SOURCES:
        raise DesignationError(
            f"{crop_filename!r} carries source_of_designation {source!r}: a candidate parsed "
            f"from the filename, which no human has confirmed against the figure caption. "
            f"Pre-registration §2 forbids guessing the loading control from the data, and a "
            f"filename is not a caption. Confirm the row before measuring against it"
        )
    if source in NO_RATIO_SOURCES:
        raise DesignationError(
            f"{crop_filename!r} carries source_of_designation {source!r}: a human ruled on it "
            f"and the ruling is that it contributes no ratio. It is a §9 separate reference "
            f"strip, not a panel with a target in it, so it has no reference of its own to "
            f"normalize against. Ask contributes_ratios() before asking for a reference"
        )
    if source not in CONFIRMED_SOURCES:
        raise DesignationError(
            f"{crop_filename!r} carries source_of_designation {source!r}, which is not a "
            f"value this reader knows. Known confirmations are {sorted(CONFIRMED_SOURCES)}. "
            f"An unrecognised source is never read as a confirmation"
        )
    if not designation.reference_label:
        raise DesignationError(
            f"{crop_filename!r} is marked {source!r} but its reference_label is blank. A "
            f"confirmation with nothing confirmed is not a designation"
        )
    return designation.reference_label


def contributes_ratios(table: dict[str, Designation], crop_filename: str) -> bool:
    """Return whether ``crop_filename`` is a crop the pre-registered ratio set draws from.

    True only for a row a human confirmed as a target panel with a reference. False for a row
    ruled to contribute nothing. **Raises for a pending row**, rather than returning False: "no
    human has looked at this yet" and "a human looked and said no" are different answers, and
    collapsing them into False would let an unconfirmed crop drop silently out of N.
    """
    designation = table.get(crop_filename)
    if designation is None:
        raise DesignationError(
            f"{crop_filename!r} has no row in the designation table, so whether it contributes "
            f"a ratio is unrecorded rather than false"
        )
    source = designation.source_of_designation
    if source in NO_RATIO_SOURCES:
        return False
    if source in CONFIRMED_SOURCES:
        return True
    raise DesignationError(
        f"{crop_filename!r} carries source_of_designation {source!r}, on which no human has "
        f"ruled. Whether it contributes a ratio is undecided, which is not the same as no"
    )


def main(argv: list[str] | None = None) -> int:
    """Build the designation table and write it. Returns 0 on success."""
    parser = argparse.ArgumentParser(
        prog="python -m tools.phase3.designations",
        description=(
            "Build data/real/designations.csv from the frozen crop log. A crop with no row "
            "gets a candidate parsed from its filename, written pending; a row that already "
            "exists is carried through verbatim, so a human ruling is never overwritten."
        ),
    )
    parser.add_argument("--crop-log", type=Path, default=CROP_LOG_PATH)
    parser.add_argument("--out", type=Path, default=DESIGNATIONS_PATH)
    args = parser.parse_args(argv)

    existing = read_designations(args.out) if args.out.is_file() else {}
    designations = build_designations(args.crop_log, existing=existing)
    path = write_designations(designations, args.out)
    pending = sum(1 for d in designations if d.source_of_designation in PENDING_SOURCES)
    ruled = sum(1 for d in designations if d.source_of_designation in RULED_SOURCES)
    print(
        f"wrote {path}: {len(designations)} row(s), {ruled} ruled by a human, "
        f"{pending} pending human confirmation"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
