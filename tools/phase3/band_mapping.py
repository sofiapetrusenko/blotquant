"""The label-to-band mapping table: propose candidates, and refuse to resolve them (W7 pass 1).

§2 of the pre-registration designates the reference lane/band "at image-selection time, from
the figure caption alone", and says in terms that *"Guessing the loading control from the data
is forbidden"*. ``data/real/designations.csv`` closes half of that: a human has ruled, per crop,
which **label** is the reference -- ACTIN, GAPDH, tub. What normalization needs is a **band id**
(``--reference-band L0_B2``), and nothing connects the two. Picking the band by vertical
position, by rank, or by "the bottom one is usually the loading control" is exactly the
inference §2 forbids, dressed as a heuristic.

So this module does what the other two tables did before their gate: it writes the
**candidates** and confirms nothing. One row per detected band, carrying the facts a human
needs to read a molecular weight off the published figure and say which band is which -- lane,
vertical position, height, and where the band ranks by integrated intensity within its lane --
and a ``mapping_source`` that says no human has looked yet.

**This is a third human gate of the same class as G1 and G2.** G1 ruled which label is the
reference by reading the caption; G2 ruled blot identity by looking at the images; this one is
ruled by molecular weight read off the figures. The read path is the enforcement: nothing here
returns a band id a human did not write down.

The vocabulary word for a confirmation, :data:`MOLECULAR_WEIGHT_CONFIRMED`, is **an implementer
proposal, not a ruling** -- the human has not yet named one. It is written down here rather than
left blank so the allow-list has something to be an allow-list of, and it is flagged in the
handoff. That is the same course the ``reference_strip_confirmed_human`` invention took, which
the human ratified on 2026-08-24 as a dated correction to G1; this one is offered on the same
terms, to be ratified or renamed.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from pipeline.analyze import require_writable_destination
from tools.phase3.crop_names import CropSetError

COLUMNS: tuple[str, ...] = (
    "crop_filename",
    "blot_id",
    "detected_band_id",
    "lane_id",
    "y_position",
    "height_px",
    "integrated_intensity_rank",
    "mapping_source",
)
"""The columns, in order. Fixed by the phase task text; a reader checks them rather than trusts.

**One thing the column list does not carry, reported rather than added.** There is no column for
the label a human will name -- no ``reference_label``, no ``carries_label``. The task fixes these
eight columns and adding a ninth would be the implementer deciding the table's shape at the
moment the human is about to rule on its contents, which is the move G1 required be reported
before it was wired. Two shapes would work and the choice is the human's: a ninth column, or the
existing ``mapping_source`` carrying the confirmation while a separate confirmed table names the
label. Until that is ruled, this table is a candidate list and nothing reads a label out of it.
"""

DETECTED_PENDING = "detected_pending_human"
"""The one value every row this module writes carries: a detection, not a designation."""

PENDING_SOURCES: frozenset[str] = frozenset({DETECTED_PENDING})
"""Values meaning the pipeline found a band here and no human has ruled on what it is."""

MOLECULAR_WEIGHT_CONFIRMED = "molecular_weight_confirmed_human"
"""Proposed word for "a human named this band by its molecular weight, read off the figure".

Proposed, not ruled -- see this module's docstring. It is named for the evidence the human said
they would use, so that a later reader can tell what a confirmation on this table *rests on*,
which is a different kind of evidence from G1's captions and G2's images.
"""

CONFIRMED_SOURCES: frozenset[str] = frozenset({MOLECULAR_WEIGHT_CONFIRMED})
"""An allow-list, not a deny-list.

The same discipline as :data:`tools.phase3.designations.CONFIRMED_SOURCES` and
:data:`tools.phase3.blot_identity.CONFIRMED_SOURCES`, for the same reason: a reader written as
"not pending, therefore confirmed" turns a typo into a measurement. Every value outside this set
is refused, including values this module itself does not write.
"""

HEADER_NOTE: tuple[str, ...] = (
    "blotquant real-blot label-to-band mapping. One row per DETECTED band, on the crops the",
    "ratified channel-collapse rule admits.",
    "",
    "NOTHING IN THIS FILE IS CONFIRMED. Every row is a candidate the pipeline detected, and",
    "every mapping_source is 'detected_pending_human'. A band's vertical position, its height",
    "and its rank by integrated intensity are recorded because they are what a human needs in",
    "order to read a molecular weight off the published figure -- they are NOT a basis on which",
    "this file, or anything that reads it, picks the reference band. DECISION_unit_of_analysis.md",
    "§2: 'Guessing the loading control from the data is forbidden'. Choosing a band by where it",
    "sits in the lane is that guess.",
    "",
    "The human names which detected_band_id carries each confirmed reference label, by molecular",
    "weight read off the figures. That is a third gate of the same class as G1 (labels, from the",
    "captions) and G2 (blot identity, from the images).",
    "",
    "blot_id and the reference label for each crop are NOT re-derived here. They are read",
    "through the guarded read paths of blot_identity.csv and designations.csv, which refuse a",
    "pending row, a blank cell, an absent crop, and any vocabulary word they do not know.",
    "",
    "Lines beginning with # are this note. The CSV header is the first line that is not one.",
)


class BandMappingError(CropSetError):
    """The band-mapping table cannot answer the question that was asked of it.

    A sibling of :class:`tools.phase3.designations.DesignationError` and
    :class:`tools.phase3.blot_identity.BlotIdentityError` under
    :class:`tools.phase3.crop_names.CropSetError`, for the reason that base class documents:
    the three send whoever reads the traceback to three different files a human maintains.
    """


@dataclass(frozen=True)
class BandMapping:
    """One row of the mapping table: one detected band, and what is known about it."""

    crop_filename: str
    blot_id: str
    detected_band_id: str
    lane_id: str
    y_position: int
    height_px: int
    integrated_intensity_rank: int
    mapping_source: str

    def __post_init__(self) -> None:
        """Refuse to exist in a shape that would misrepresent a detection.

        Shape only, in the same split the designation table uses: this class refuses rows it
        knows to be malformed, and the reader refuses vocabulary it does not know. The checks
        are not decoration -- a blank ``detected_band_id`` is a row a human could confirm that
        names no band, and a rank of 0 or a negative height would be a measurement that did not
        come from a result document.
        """
        for name in ("crop_filename", "blot_id", "detected_band_id", "lane_id", "mapping_source"):
            if not str(getattr(self, name)).strip():
                raise BandMappingError(
                    f"a band-mapping row has a blank {name}; every row names a crop, a blot, a "
                    f"band, a lane and the state of its mapping"
                )
        if self.y_position < 0:
            raise BandMappingError(
                f"{self.detected_band_id!r} on {self.crop_filename!r} has y_position "
                f"{self.y_position}; a band ROI's y is a pixel row and cannot be negative"
            )
        if self.height_px < 1:
            raise BandMappingError(
                f"{self.detected_band_id!r} on {self.crop_filename!r} has height_px "
                f"{self.height_px}; a detected band occupies at least one pixel row"
            )
        if self.integrated_intensity_rank < 1:
            raise BandMappingError(
                f"{self.detected_band_id!r} on {self.crop_filename!r} has "
                f"integrated_intensity_rank {self.integrated_intensity_rank}; ranks start at 1, "
                f"and 0 would read as 'unranked' on a column where every row is ranked"
            )

    def as_row(self) -> dict[str, str]:
        """Return this mapping as a CSV row keyed by :data:`COLUMNS`."""
        return {
            "crop_filename": self.crop_filename,
            "blot_id": self.blot_id,
            "detected_band_id": self.detected_band_id,
            "lane_id": self.lane_id,
            "y_position": str(self.y_position),
            "height_px": str(self.height_px),
            "integrated_intensity_rank": str(self.integrated_intensity_rank),
            "mapping_source": self.mapping_source,
        }


def rank_by_integrated_intensity(bands: list[dict[str, object]]) -> dict[str, int]:
    """Return ``band_id -> rank within its lane``, 1 being the most intense.

    Descriptive, not selective. The rank is recorded so a human comparing a crop against its
    published figure has the same ordering the figure shows; nothing in this repository reads it
    to choose a band. Ties are broken by ``band_id`` so that two bands of identical integrated
    intensity get a stable, reproducible pair of ranks rather than an arbitrary one -- a report
    that changes between two runs of the same bytes is not a record.
    """
    ranks: dict[str, int] = {}
    lanes: dict[str, list[dict[str, object]]] = {}
    for band in bands:
        lanes.setdefault(str(band["lane_id"]), []).append(band)
    for lane_bands in lanes.values():
        ordered = sorted(
            lane_bands,
            key=lambda b: (-float(b["integrated_intensity"]), str(b["band_id"])),
        )
        for position, band in enumerate(ordered, start=1):
            ranks[str(band["band_id"])] = position
    return ranks


def mappings_for_document(
    crop_filename: str, blot_id: str, document: dict[str, object]
) -> list[BandMapping]:
    """Return one pending :class:`BandMapping` per band in ``document``, in band order.

    ``blot_id`` is passed in rather than derived here, because the only correct source for it is
    :func:`tools.phase3.blot_identity.confirmed_blot_id` and a second derivation path is a second
    thing to keep in step with a human ruling.
    """
    bands = list(document["bands"])  # type: ignore[arg-type]
    ranks = rank_by_integrated_intensity(bands)  # type: ignore[arg-type]
    rows: list[BandMapping] = []
    for band in bands:
        roi = band["roi"]  # type: ignore[index]
        rows.append(
            BandMapping(
                crop_filename=crop_filename,
                blot_id=blot_id,
                detected_band_id=str(band["band_id"]),  # type: ignore[index]
                lane_id=str(band["lane_id"]),  # type: ignore[index]
                y_position=int(roi["y"]),  # type: ignore[index]
                height_px=int(roi["height"]),  # type: ignore[index]
                integrated_intensity_rank=ranks[str(band["band_id"])],  # type: ignore[index]
                mapping_source=DETECTED_PENDING,
            )
        )
    return rows


def write_band_mappings(rows: list[BandMapping], path: Path) -> Path:
    """Write ``rows`` to ``path`` with the header note above the CSV header row.

    Refuses any destination inside a ground-truth directory, and refuses to write two rows for
    one ``(crop_filename, detected_band_id)`` pair -- which would give a human two places to
    confirm one band, and a later reader no way to know which confirmation counted.
    """
    require_writable_destination(path.parent)
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row.crop_filename, row.detected_band_id)
        if key in seen:
            raise BandMappingError(
                f"refusing to write {path}: {row.detected_band_id!r} appears more than once for "
                f"{row.crop_filename!r}, and one band cannot be mapped twice"
            )
        seen.add(key)
    lines = [f"# {line}".rstrip() for line in HEADER_NOTE]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_row())
    return path


def read_band_mappings(path: Path) -> dict[str, list[BandMapping]]:
    """Return the table grouped by crop filename, raising on anything malformed.

    Skips the ``#`` header note. Raises :class:`BandMappingError` for a missing file, missing or
    reordered columns, a blank crop or band cell, a non-integer measurement, and a repeated
    ``(crop, band)`` pair. Every one of those would otherwise become a silently short table, and
    a short table at this gate is a band nobody ruled on quietly leaving the candidate list.
    """
    if not path.is_file():
        raise BandMappingError(
            f"{path} is missing; the band-mapping table is produced by a detection run and "
            f"ruled on by a human. It is not generated on demand"
        )
    with path.open(newline="", encoding="utf-8") as handle:
        body = [line for line in handle if not line.startswith("#")]
    reader = csv.DictReader(body)
    if tuple(reader.fieldnames or ()) != COLUMNS:
        raise BandMappingError(
            f"{path} has columns {reader.fieldnames}; expected exactly {list(COLUMNS)}, in that "
            f"order. A renamed or reordered column changes what every row means"
        )
    table: dict[str, list[BandMapping]] = {}
    seen: set[tuple[str, str]] = set()
    for row in reader:
        crop = (row["crop_filename"] or "").strip()
        band = (row["detected_band_id"] or "").strip()
        if not crop:
            raise BandMappingError(f"{path} has a row with a blank crop_filename: {row}")
        if (crop, band) in seen:
            raise BandMappingError(
                f"{path} lists {band!r} more than once for {crop!r}; one band cannot carry two "
                f"mappings"
            )
        seen.add((crop, band))
        numbers: dict[str, int] = {}
        for name in ("y_position", "height_px", "integrated_intensity_rank"):
            raw = (row[name] or "").strip()
            try:
                numbers[name] = int(raw)
            except ValueError as error:
                raise BandMappingError(
                    f"{path}: {crop!r} band {band!r} has {name}={raw!r}, which is not an "
                    f"integer. These columns are measurements copied from a result document"
                ) from error
        try:
            mapping = BandMapping(
                crop_filename=crop,
                blot_id=(row["blot_id"] or "").strip(),
                detected_band_id=band,
                lane_id=(row["lane_id"] or "").strip(),
                y_position=numbers["y_position"],
                height_px=numbers["height_px"],
                integrated_intensity_rank=numbers["integrated_intensity_rank"],
                mapping_source=(row["mapping_source"] or "").strip(),
            )
        except BandMappingError as error:
            raise BandMappingError(f"{path}: {error}") from error
        table.setdefault(crop, []).append(mapping)
    return table


def confirmed_band_ids(table: dict[str, list[BandMapping]], crop_filename: str) -> tuple[str, ...]:
    """Return the band ids a human confirmed for ``crop_filename``, or raise.

    The whole point of this module, and it currently raises for every crop in the tree -- which
    is the correct answer, because the gate has not been held. There is no default, no "the
    lowest band", and no "the one ranked first": each of those is the inference §2 forbids.

    **Raises for a pending row rather than skipping it.** A crop whose bands are all pending is
    not a crop with no confirmed bands; it is a crop nobody has ruled on, and returning an empty
    tuple would let it drop silently out of whatever is being counted.
    """
    rows = table.get(crop_filename)
    if rows is None:
        raise BandMappingError(
            f"{crop_filename!r} has no rows in the band-mapping table, so which band carries its "
            f"reference label is unrecorded. It does not get a band by default"
        )
    pending = [row for row in rows if row.mapping_source in PENDING_SOURCES]
    if pending:
        raise BandMappingError(
            f"{crop_filename!r} has {len(pending)} of {len(rows)} bands still carrying a pending "
            f"mapping_source ({sorted({row.mapping_source for row in pending})}). A band the "
            f"pipeline detected is not a band a human named. DECISION_unit_of_analysis.md §2 "
            f"forbids guessing the loading control from the data, and vertical position is that "
            f"guess. Rule on the rows before measuring against them"
        )
    unknown = sorted(
        {row.mapping_source for row in rows if row.mapping_source not in CONFIRMED_SOURCES}
    )
    if unknown:
        raise BandMappingError(
            f"{crop_filename!r} carries mapping_source value(s) {unknown}, which this reader does "
            f"not know. Known confirmations are {sorted(CONFIRMED_SOURCES)}. An unrecognised "
            f"source is never read as a confirmation"
        )
    return tuple(row.detected_band_id for row in rows)
