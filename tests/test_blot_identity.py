"""The blot identity table: what it proposes, what it refuses, and the id it must not merge.

Two failure modes, and they pull in opposite directions.

The first is the same quiet-success failure the designation table has: a reader that hands back
a *proposed* blot_id when asked for a confirmed one. The blot count is what the ratified stop
rule turns on -- a minimum of 10 blots -- so a count assembled from proposals would evaluate
the stop rule against a naming convention.

The second is specific to this table and is the reason :func:`validate_blot_ids` exists: a
blot_id that **collapses two parents into one**. ``PMC13135410_Figure3`` panel B and
``PMC13135410_Figure4`` panel B are two figures of one article, and an id keyed on the
accession alone merges them. That is not a weaker proposal, it is a wrong one, and it moves the
blot count in the direction that makes the stop rule easier to fail to notice.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import pytest

from pipeline.errors import PipelineError
from tools.phase3.blot_identity import (
    BLOT_IDENTITY_PATH,
    COLUMNS,
    CONFIRMED_SOURCES,
    CROP_LOG_PATH,
    PROPOSED_PENDING,
    BlotIdentity,
    BlotIdentityError,
    build_blot_identities,
    confirmed_blot_id,
    propose_blot_id,
    read_blot_identities,
    validate_blot_ids,
    write_blot_identities,
)
from tools.phase3.blot_identity import main as blot_identity_main
from tools.phase3.crop_names import CorpusError, CropNameError, CropSetError
from tools.phase3.designations import (
    DESIGNATIONS_PATH,
    DesignationError,
    contributes_ratios,
    read_designations,
)

VINCULIN = "PMC13135388_Figure4__E-Vinculin.png"
TIGAR = "PMC13135388_Figure4__E-TIGAR.png"

pytestmark = pytest.mark.skipif(
    not BLOT_IDENTITY_PATH.is_file(), reason="the real-blot identity table is not present"
)


@pytest.fixture
def table() -> dict[str, BlotIdentity]:
    """Return the committed blot identity table."""
    return read_blot_identities(BLOT_IDENTITY_PATH)


def test_rebuilding_over_the_committed_table_preserves_every_human_ruling(
    table: dict[str, BlotIdentity],
) -> None:
    """The builder is idempotent over a ruled table: re-running it discards no ruling.

    Before the gate this asserted that the file *is* a fresh build. That is now the wrong
    property to want: the rulings came from the images, and a builder that reproduced the file
    from filenames would be one that could silently revert them.
    """
    rebuilt = {row.crop_filename: row for row in build_blot_identities(existing=table)}

    assert rebuilt == table


def test_a_rebuild_without_the_existing_rows_loses_the_rulings(
    table: dict[str, BlotIdentity],
) -> None:
    """Asserted, so the idempotence test above is not passing for a vacuous reason."""
    from_scratch = {row.crop_filename: row for row in build_blot_identities()}

    assert {row.identity_source for row in from_scratch.values()} == {PROPOSED_PENDING}
    assert from_scratch != table


def test_there_is_one_row_per_measurable_crop(table: dict[str, BlotIdentity]) -> None:
    """12 crops load under the ratified collapse rule, so 12 identities are proposed."""
    assert len(table) == 12
    assert COLUMNS[3] == "blot_id", "the column DEBT D5 asks for, by name"


def test_every_row_was_ruled_from_the_images(table: dict[str, BlotIdentity]) -> None:
    """The human rules on blot identity by looking at the images, and did.

    Before the gate this asserted the mirror image -- that nothing was confirmed. The pair is
    the point: the implementer ruled on none of it, the human ruled on all of it.
    """
    sources = {row.identity_source for row in table.values()}

    assert sources <= CONFIRMED_SOURCES
    assert PROPOSED_PENDING not in sources


def test_the_three_figure4_panels_are_ruled_three_separate_blots(
    table: dict[str, BlotIdentity],
) -> None:
    """G2's substantive finding, and the one that decides whether the blot floor is cleared.

    Had these been ruled one physical blot, the count would have fallen from 12 to 10. The
    ruling rests on four things a filename cannot carry, so the ruling's *basis* is pinned here
    too -- a row marked confirmed with no reason recorded is a row nobody can audit.
    """
    panels = {
        crop: row for crop, row in table.items()
        if row.parent_figure == "PMC13135410_Figure4"
    }

    assert len(panels) == 3
    assert len({row.blot_id for row in panels.values()}) == 3
    for row in panels.values():
        assert "THREE" in row.notes and "SEPARATE PHYSICAL BLOTS" in row.notes
        assert "denervation" in row.notes, "the cohort basis is recorded, not just the verdict"


def test_the_ratio_contributing_blot_count_is_one_below_the_row_count(
    table: dict[str, BlotIdentity],
) -> None:
    """12 ids, 11 of which can contribute a ratio -- the reference strip is a blot, not a ratio.

    This is the number the stop rule's blot floor is read against, and it is neither the row
    count of this file nor the row count of the designation table, so it is asserted directly.
    """
    designations = read_designations(DESIGNATIONS_PATH)
    contributing = {
        crop for crop in table if contributes_ratios(designations, crop)
    }

    assert len(table) == 12
    assert len(contributing) == 11
    assert len({table[crop].blot_id for crop in contributing}) == 11


def test_two_figures_of_one_article_do_not_share_a_proposed_id() -> None:
    """The collapsing-id failure, asserted on the id function itself.

    An implementation keyed on the PMC accession rather than on the full figure stem returns
    the same id for both of these, and every count downstream is then one blot short per
    merged pair.
    """
    third = propose_blot_id("PMC13135410_Figure3", "B")
    fourth = propose_blot_id("PMC13135410_Figure4", "B")

    assert third != fourth
    assert "Figure3" in third and "Figure4" in fourth


def test_the_committed_table_has_one_id_per_crop(table: dict[str, BlotIdentity]) -> None:
    """No two of the 12 crops are proposed as the same blot, so 12 crops give 12 ids."""
    assert len({row.blot_id for row in table.values()}) == len(table) == 12


def test_a_collapsing_id_is_refused_by_the_validator() -> None:
    """The invariant, tested by violating it: one id may not span two figure-and-panel pairs.

    Written against ``validate_blot_ids`` rather than only against ``propose_blot_id`` so that
    a build which acquires ids from anywhere else -- a hand-edited file, a future column in the
    crop log -- is checked by the same rule.
    """
    merged = [
        BlotIdentity("a.png", "PMC13135410_Figure3", "B", "PMC13135410_B", PROPOSED_PENDING, ""),
        BlotIdentity("b.png", "PMC13135410_Figure4", "B", "PMC13135410_B", PROPOSED_PENDING, ""),
    ]

    with pytest.raises(BlotIdentityError, match="more than one figure-and-panel"):
        validate_blot_ids(merged)


def test_a_collapsing_id_cannot_be_written_to_disk(tmp_path: Path) -> None:
    """The validator guards the write path too: a merged table never reaches the tree."""
    merged = [
        BlotIdentity("a.png", "PMC13135410_Figure3", "B", "PMC13135410_B", PROPOSED_PENDING, ""),
        BlotIdentity("b.png", "PMC13135410_Figure4", "B", "PMC13135410_B", PROPOSED_PENDING, ""),
    ]
    path = tmp_path / "blot_identity.csv"

    with pytest.raises(BlotIdentityError):
        write_blot_identities(merged, path)
    assert not path.exists()


def test_two_crops_of_one_panel_may_legitimately_share_an_id() -> None:
    """The validator forbids merging *parents*, not the §9 case it exists to make recordable."""
    figure, panel, blot_id = "PMC13135388_Figure4", "E", "PMC13135388_Figure4_E"
    same_panel = [
        BlotIdentity("a.png", figure, panel, blot_id, PROPOSED_PENDING, ""),
        BlotIdentity("b.png", figure, panel, blot_id, PROPOSED_PENDING, ""),
    ]

    validate_blot_ids(same_panel)


def test_the_section_9_case_is_flagged_against_the_whole_crop_log(
    table: dict[str, BlotIdentity],
) -> None:
    """E-Vinculin shares panel E with E-TIGAR, which the collapse rule excludes.

    The note must still name it: computing sibling panels over the *measurable* subset alone
    would erase precisely the case §9 anticipated, because the sibling is one of the seven
    crops that no longer load.
    """
    assert TIGAR in table[VINCULIN].notes
    assert "§9" in table[VINCULIN].notes


def test_asking_for_a_confirmed_id_on_a_proposed_row_raises() -> None:
    """A panel letter is not evidence of physical identity, so the proposal is not returned.

    Synthetic now that every committed row is ruled: the guard protects the *next* crop to
    arrive, so it is tested in the state that crop arrives in.
    """
    table = {"x.png": BlotIdentity("x.png", "FIG", "A", "FIG_A", PROPOSED_PENDING, "")}

    with pytest.raises(BlotIdentityError, match="no human has ruled on"):
        confirmed_blot_id(table, "x.png")


def test_asking_about_a_crop_with_no_row_raises(table: dict[str, BlotIdentity]) -> None:
    """An unlisted crop has no recorded blot, and does not acquire one by default."""
    with pytest.raises(BlotIdentityError, match="no row in the blot identity table"):
        confirmed_blot_id(table, TIGAR)


def test_an_unrecognised_source_is_never_read_as_a_confirmation() -> None:
    """A reader written as "not pending, therefore confirmed" fails here and nowhere else."""
    table = {"x.png": BlotIdentity("x.png", "FIG", "A", "FIG_A", "looks_ruled_to_me", "")}

    with pytest.raises(BlotIdentityError, match="not a value this reader knows"):
        confirmed_blot_id(table, "x.png")


def test_a_confirmation_with_a_blank_id_raises() -> None:
    """A confirmed row with nothing in its blot_id cell confirms nothing."""
    source = sorted(CONFIRMED_SOURCES)[0]
    table = {"x.png": BlotIdentity("x.png", "FIG", "A", "", source, "")}

    with pytest.raises(BlotIdentityError, match="blot_id is blank"):
        confirmed_blot_id(table, "x.png")


def test_a_confirmed_row_returns_its_id() -> None:
    """Once the human has looked at the images and ruled, the reader hands the id over."""
    source = sorted(CONFIRMED_SOURCES)[0]
    table = {"x.png": BlotIdentity("x.png", "FIG", "A", "FIG_A", source, "ruled from the images")}

    assert confirmed_blot_id(table, "x.png") == "FIG_A"


def test_the_header_states_that_a_panel_is_not_a_blot() -> None:
    """The caveat has to be in the file, because a reader of the CSV may never see this test."""
    # Whitespace-normalised, because the note is wrapped to CSV comment lines and every phrase
    # worth asserting spans a line break. Matching the wrapped form would pin the wrapping.
    header = " ".join(
        line.lstrip("#").strip()
        for line in BLOT_IDENTITY_PATH.read_text(encoding="utf-8").splitlines()
        if line.startswith("#")
    )

    assert "TWO PANELS OF ONE FIGURE MAY BE TWO DIFFERENT PHYSICAL BLOTS" in header
    assert "a panel letter is not evidence of physical identity" in header
    assert "the proposals did not decide that; the human did" in header.lower()
    assert "blot_id" in header, "§9's provision for the column is quoted in the file"
    assert "contributes a ratio" in header, "and that not every blot here does"


def test_the_table_round_trips_through_the_file(tmp_path: Path) -> None:
    """What is written is what is read back."""
    rows = build_blot_identities()
    path = write_blot_identities(rows, tmp_path / "blot_identity.csv")

    assert list(read_blot_identities(path).values()) == rows


def test_the_writer_refuses_a_destination_inside_the_gold_set(tmp_path: Path) -> None:
    """The same boundary the designation writer enforces, for the same reason."""
    destination = tmp_path / "ground_truth" / "blot_identity.csv"

    with pytest.raises(PipelineError, match="ground truth is written only by"):
        write_blot_identities(build_blot_identities(), destination)
    assert not destination.parent.exists()


def test_the_module_docstrings_shared_parent_count_is_measured_not_asserted() -> None:
    """"Five of the 13 parent figures contribute more than one crop" -- checked, not quoted.

    The sentence is the premise of DEBT draft D5 and of this module's whole reason to exist, so
    it is re-derived from the frozen crop log on every run rather than left as prose.
    """
    with (CROP_LOG_PATH).open(newline="") as handle:
        parents = [row["parent"] for row in csv.DictReader(handle)]
    counts = Counter(parents)

    assert len(counts) == 13
    assert sum(1 for count in counts.values() if count > 1) == 5


def test_the_corpus_and_table_errors_are_siblings_not_a_chain() -> None:
    """Catching one must never swallow the other, and the catch-all must catch both.

    A wrong corpus and a wrong table send a reader to different files. An earlier arrangement
    made the corpus error the base class of the table errors, which reads sensibly and cannot
    work: with the corpus error as the base, no expression isolates it.
    """
    assert not issubclass(DesignationError, CorpusError)
    assert not issubclass(BlotIdentityError, CorpusError)
    assert not issubclass(CorpusError, (DesignationError, BlotIdentityError))
    for error in (CorpusError, CropNameError, DesignationError, BlotIdentityError):
        assert issubclass(error, CropSetError)


def test_a_crop_whose_bytes_fail_the_frozen_digest_raises_a_corpus_error(
    tmp_path: Path,
) -> None:
    """The corpus check fires before any table is built, and names the corpus, not a table."""
    crops = tmp_path / "crops"
    crops.mkdir()
    log = crops / "crop_log.csv"
    log.write_text(
        "crop,crop_sha256,px,parent,parent_sha256,panel_note\n"
        "x__A-T-ACTIN.png," + "0" * 64 + ",8x8,p.jpg," + "1" * 64 + ",A-T-ACTIN\n",
        encoding="utf-8",
    )
    (crops / "x__A-T-ACTIN.png").write_bytes(b"not the approved bytes")

    with pytest.raises(CorpusError, match="not the bytes Gate 2 approved"):
        build_blot_identities(log, crops)


def test_the_committed_file_is_byte_identical_to_what_the_writer_emits(tmp_path: Path) -> None:
    """Rows *and* header note, for the reason the designation table's twin gives.

    Here the note carries §9's quotation and the statement that a panel is not a blot, so a
    drifting header is a drifting report of the conflict this file exists to raise.
    """
    rebuilt = write_blot_identities(
        build_blot_identities(existing=read_blot_identities(BLOT_IDENTITY_PATH)),
        tmp_path / "blot_identity.csv",
    )

    assert rebuilt.read_bytes() == BLOT_IDENTITY_PATH.read_bytes()


def test_the_cli_entry_point_preserves_every_ruling(tmp_path: Path) -> None:
    """The command a human runs, end to end. Its twin in the designation tests says why."""
    out = tmp_path / "blot_identity.csv"
    out.write_bytes(BLOT_IDENTITY_PATH.read_bytes())

    assert blot_identity_main(["--out", str(out)]) == 0

    assert out.read_bytes() == BLOT_IDENTITY_PATH.read_bytes()
    sources = {row.identity_source for row in read_blot_identities(out).values()}
    assert sources <= CONFIRMED_SOURCES


def test_a_rebuild_refuses_to_drop_a_ruling_for_a_crop_that_left_the_set(
    table: dict[str, BlotIdentity],
) -> None:
    """A ruling from the images must not be deleted by a tool run that nobody asked to delete it."""
    stranded = dict(table)
    stranded[TIGAR] = BlotIdentity(
        TIGAR, "PMC13135388_Figure4", "E", "PMC13135388_Figure4_E", "image_confirmed_human", ""
    )

    with pytest.raises(CorpusError, match="no longer makes measurable"):
        build_blot_identities(existing=stranded)
