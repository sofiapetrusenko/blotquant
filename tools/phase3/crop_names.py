"""Parsing a Gate 2 crop filename into its parts, and refusing to when it cannot be done.

The 19 approved crops are named ``<parent figure stem>__<panel note>.png``, where the panel
note is a hyphen-separated run such as ``A-p16p21-ACTIN``: a panel token, then the labels the
cropper wrote down. **That is a naming convention, not data.** Nothing in the project ruled
that the last label is the loading control or that the first token is a panel letter; the
convention is simply what the crop log happens to contain, and §2 of the pre-registration is
explicit that the reference "is designated at image-selection time, from the figure caption
alone" and that "Guessing the loading control from the data is forbidden".

So this module parses, and stops. It produces *candidates* for a human to confirm against the
caption, it labels how confident the parse was, and where the filename does not carry the
information it says so instead of supplying a value. The one behaviour it must never have is
the quiet one: returning a plausible reference for a name that names no reference. A caller
that wanted a designation and got a default would put a guess into a measurement.

Read-only with respect to ``data/real/``: this module parses strings and writes nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PARSED_PENDING = "parsed_pending_human"
"""Panel, target and reference all read cleanly off the filename. Still not confirmed."""

PARSED_AMBIGUOUS_PENDING = "parsed_ambiguous_pending_human"
"""The name carries more labels than target-plus-reference, so the split is a guess."""

UNPARSED_NEEDS_HUMAN = "unparsed_needs_human"
"""The name carries no second label, so it names no reference. Nothing is supplied."""

PENDING_SOURCES: frozenset[str] = frozenset(
    {PARSED_PENDING, PARSED_AMBIGUOUS_PENDING, UNPARSED_NEEDS_HUMAN}
)
"""Every value this module can write. None of them is a confirmation, by construction.

A reader that treats an unrecognised value as confirmed would make this set the wrong side of
the check, so readers test *membership of the confirmed vocabulary they define*, never
non-membership of this one.
"""

TARGET_SEPARATOR = "|"
"""Separates the targets of a crop that carries more than one, in ``target_label``.

A crop can carry two targets sharing one reference -- ``PMC13135410_Figure3__B`` carries
Fibronectin at 250 kDa and CCN2 at 35 kDa, both against GAPDH, as Figure 3C's axis labels state.
The human ruled (G1, 2026-08-20) that the two must not be folded into one label, and offered two
mechanisms: two rows, or a target list. **A list in one cell was chosen.** The reason first given
for it was that two rows would mean
*relaxing* ``read_designations``'s duplicate-crop guard, and that was an overstatement: the guard
could instead be re-keyed on ``(crop, target)`` with a consistency check on ``reference_label``,
which is exactly as strict. The honest reason is narrower. One row per crop is what the task's
column list fixes and what every consumer of this table already assumes -- ``measurable_crops``
returns crops, the identity table is keyed by crop, and ``contributes_ratios`` answers per crop --
so a list keeps the change inside one cell, while two rows would move the table's unit of record
from the crop to the (crop, target) pair and would have to be followed through every one of those
places. Both mechanisms are sound; this one is smaller, and the human offered either.

A single target is written bare, with no separator, so the ten single-target rows are unchanged by
the mechanism. Parsing is exact: the cell is split on this character and nothing is stripped except
surrounding whitespace, so a target containing it is impossible rather than silently split wrong.
"""

PANEL_SEPARATOR = "__"
"""What divides the parent figure stem from the panel note in a crop filename."""

_PANEL_TOKEN = re.compile(r"^[A-Z][0-9]*$")
"""A panel token as the crop log writes them: a capital letter, optionally numbered (``A1``)."""


@dataclass(frozen=True)
class CropName:
    """One crop filename, taken apart. Every field is a candidate, none is a decision."""

    crop_filename: str
    parent_stem: str
    panel: str
    target_label: str
    reference_label: str | None
    source: str
    notes: str


class CropSetError(ValueError):
    """Base class for every failure of the crop set or of a table built from it.

    A deliberate catch-all and nothing more: catching it catches corpus failures, name-parsing
    failures, designation-table failures and identity-table failures alike. It exists so that a
    caller who wants all of them can say so in one clause, not so that any of them can be told
    apart -- for that, catch the specific class.

    The three kinds are **siblings** under this base rather than a chain, because they send
    whoever reads the traceback to different files: :class:`CorpusError` to the crops and the
    frozen log, :class:`CropNameError` to a filename, and
    :class:`tools.phase3.designations.DesignationError` /
    :class:`tools.phase3.blot_identity.BlotIdentityError` to a table a human maintains. An
    earlier arrangement made this class *itself* the corpus error and the table errors its
    subclasses, which reads sensibly and cannot work: with the corpus error as the base, no
    expression isolates it, because catching it catches every table error too.
    """


class CorpusError(CropSetError):
    """The approved crop set itself is wrong, before any table is built from it.

    A crop the frozen log lists but the tree does not hold, bytes whose sha256 does not match
    the digest the log records for them, or an existing table row naming a crop the current set
    no longer makes measurable. The first two are raised while *reading* the crop set; the third
    while reconciling a table against it, where the crop set may be entirely correct and what
    changed is which crops it admits. All three are reported by whichever builder happened to be
    running and belong to neither -- which is why this is a sibling of the table errors and not
    one of them, and why the reconciliation case raises it rather than a table error it would
    have to pick arbitrarily between. A caller that wants corpus failures
    specifically catches this class; ``except CorpusError`` is that expression, and it exists
    because this class is not the base.
    """


class CropNameError(CropSetError):
    """A crop filename does not have the shape the Gate 2 naming convention produces.

    Raised rather than worked around. A filename this module cannot take apart is one whose
    convention has changed, and continuing under the old assumptions would mis-assign a
    target, a reference or a panel across the whole table at once.
    """


def parse_crop_name(crop_filename: str) -> CropName:
    """Return the parts of ``crop_filename``, or raise :class:`CropNameError`.

    Raises for a name with no ``__`` separator, an empty side of it, or a first token that is
    not a panel token. Does **not** raise for a name that carries only one label: that is a
    real and expected case in this set -- ``PMC13135388_Figure4__E-Vinculin`` is the
    loading-control strip §9 anticipated -- and the honest answer there is a row saying no
    reference was named, not an exception that would keep the whole table from being written.
    """
    stem = crop_filename[: -len(".png")] if crop_filename.endswith(".png") else crop_filename
    if stem.count(PANEL_SEPARATOR) != 1:
        raise CropNameError(
            f"{crop_filename!r} does not carry exactly one {PANEL_SEPARATOR!r} separator, so "
            f"its parent figure and its panel note cannot be told apart. The Gate 2 naming "
            f"convention is <parent stem>{PANEL_SEPARATOR}<panel note>.png"
        )
    parent_stem, panel_note = stem.split(PANEL_SEPARATOR)
    if not parent_stem or not panel_note:
        raise CropNameError(
            f"{crop_filename!r} has an empty parent stem or panel note either side of "
            f"{PANEL_SEPARATOR!r}"
        )
    tokens = panel_note.split("-")
    panel, labels = tokens[0], tokens[1:]
    if not _PANEL_TOKEN.match(panel):
        raise CropNameError(
            f"{crop_filename!r} starts its panel note with {panel!r}, which is not a panel "
            f"token (a capital letter, optionally followed by digits, as in 'A' or 'A1'). "
            f"Without a panel token neither the designation table nor the identity table can "
            f"be built from this name"
        )
    if not labels:
        raise CropNameError(
            f"{crop_filename!r} carries a panel token and no labels at all, so it names "
            f"neither a target nor a reference"
        )
    if len(labels) == 1:
        return CropName(
            crop_filename=crop_filename,
            parent_stem=parent_stem,
            panel=panel,
            target_label=labels[0],
            reference_label=None,
            source=UNPARSED_NEEDS_HUMAN,
            notes=(
                "the filename names one label and therefore no reference; "
                "the reference is not inferred. Pre-registration §2: a blot whose caption "
                "states neither a loading-control band nor stain-based loading is REJECTED at "
                "selection, not decided at measurement time"
            ),
        )
    if len(labels) == 2:
        return CropName(
            crop_filename=crop_filename,
            parent_stem=parent_stem,
            panel=panel,
            target_label=labels[0],
            reference_label=labels[1],
            source=PARSED_PENDING,
            notes="",
        )
    return CropName(
        crop_filename=crop_filename,
        parent_stem=parent_stem,
        panel=panel,
        target_label="-".join(labels[:-1]),
        reference_label=labels[-1],
        source=PARSED_AMBIGUOUS_PENDING,
        notes=(
            f"the filename carries {len(labels)} labels ({', '.join(labels)}); taking the "
            f"last as the reference and the rest as the target is a guess about where the "
            f"boundary falls, not a reading of the caption"
        ),
    )
