"""The reference designation table: what it proposes, and what it refuses to supply.

The failure this file exists to catch is not a crash. It is a designation table that answers
every question -- one that hands back the parsed candidate when asked for a confirmation, or
reads an unrecognised ``source_of_designation`` as good enough. Either would put a reference
band nobody read a caption for into a measurement, and §2 of the pre-registration forbids
exactly that: "Guessing the loading control from the data is forbidden."

So the read-path tests here are all shaped the same way: ask for a confirmed value in a state
where none exists, and require an exception naming the state.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.errors import PipelineError
from tools.phase3.crop_names import (
    PARSED_AMBIGUOUS_PENDING,
    PARSED_PENDING,
    PENDING_SOURCES,
    UNPARSED_NEEDS_HUMAN,
    CorpusError,
)
from tools.phase3.designations import (
    COLUMNS,
    CONFIRMED_SOURCES,
    DESIGNATIONS_PATH,
    NO_RATIO_SOURCES,
    RULED_SOURCES,
    Designation,
    DesignationError,
    build_designations,
    confirmed_reference_label,
    contributes_ratios,
    read_designations,
    write_designations,
)
from tools.phase3.designations import main as designations_main

VINCULIN = "PMC13135388_Figure4__E-Vinculin.png"
"""The crop whose filename names one label and therefore no reference at all."""

FIB_CCN2 = "PMC13135410_Figure3__B-Fib-CCN2-GAPDH.png"
"""The crop whose filename carries three labels, so the target/reference split is a guess."""

pytestmark = pytest.mark.skipif(
    not DESIGNATIONS_PATH.is_file(), reason="the real-blot designation table is not present"
)


@pytest.fixture
def table() -> dict[str, Designation]:
    """Return the committed designation table."""
    return read_designations(DESIGNATIONS_PATH)


def test_rebuilding_over_the_committed_table_preserves_every_human_ruling(
    table: dict[str, Designation],
) -> None:
    """The builder is idempotent over a ruled table: re-running it discards nothing.

    Before the human gate this test asserted the stronger property that the file *is* what a
    fresh build produces. That property is now wrong to want: one row was corrected at the gate
    against what the parser proposes, so a builder that reproduced the file from filenames would
    be a builder that silently reverted a human ruling. Idempotence is the invariant that
    survives, and it is the one that protects the rulings.
    """
    rebuilt = {row.crop_filename: row for row in build_designations(existing=table)}

    assert rebuilt == table


def test_a_rebuild_would_otherwise_revert_the_corrected_row(
    table: dict[str, Designation],
) -> None:
    """The test above matters because a build *without* the existing rows disagrees.

    Asserted rather than assumed: if the parser happened to reproduce the confirmed table, the
    idempotence test would pass for a builder that ignored ``existing`` entirely.
    """
    from_scratch = {row.crop_filename: row for row in build_designations()}

    assert from_scratch[FIB_CCN2].target_label == "Fib-CCN2", "what the parser proposes"
    assert table[FIB_CCN2].target_label == "Fibronectin|CCN2", "what the human ruled"
    assert from_scratch != table


def test_there_is_one_row_per_measurable_crop(table: dict[str, Designation]) -> None:
    """12 of the 19 approved crops load under the ratified collapse rule; 12 get a row."""
    assert len(table) == 12


def test_the_columns_are_exactly_the_specified_ones() -> None:
    """A renamed or reordered column changes what every row means, so it is pinned."""
    assert COLUMNS == (
        "crop_filename",
        "target_label",
        "reference_label",
        "source_of_designation",
        "notes",
    )


def test_every_row_is_ruled_by_a_human_and_none_by_a_parser(
    table: dict[str, Designation],
) -> None:
    """Every row carries a human ruling, and no row is still carrying a parser's proposal.

    Designating a reference band is a measurement input, not a parameter choice (DEBT S6), so
    Gate 1 ruling 3 does not reach it and only the human may make it. Before the gate this test
    asserted the mirror image -- that *nothing* was confirmed -- and the pair is the point: the
    implementer confirmed none of it and the human confirmed all of it.
    """
    sources = {designation.source_of_designation for designation in table.values()}

    assert sources <= RULED_SOURCES
    assert not sources & PENDING_SOURCES


def test_the_reference_strip_is_ruled_to_contribute_no_ratio(
    table: dict[str, Designation],
) -> None:
    """E-Vinculin is the §9 separate reference strip, and its target is refused under §7.

    The parser could say only that the filename named no reference. The human said what it *is*:
    the vinculin strip serving E-TIGAR, which the collapse rule refuses at 255 DN. So it carries
    no target, and it contributes nothing to N.
    """
    row = table[VINCULIN]

    assert row.target_labels == (), "a reference strip carries no target"
    assert row.reference_label == "Vinculin", "it names what the strip itself carries"
    assert row.source_of_designation in NO_RATIO_SOURCES
    assert contributes_ratios(table, VINCULIN) is False


def test_the_two_target_crop_carries_two_targets_and_one_reference(
    table: dict[str, Designation],
) -> None:
    """The corrected row: two targets sharing one reference, not one hyphenated target.

    The parser proposed ``Fib-CCN2`` and flagged the split as a guess. The guess was wrong in a
    way only the figure could settle: these are Fibronectin and CCN2, two proteins at different
    molecular weights, both normalized to the same GAPDH. Folding them into one label would have
    produced one ratio where the figure supports two.
    """
    row = table[FIB_CCN2]

    assert row.target_labels == ("Fibronectin", "CCN2")
    assert row.reference_label == "GAPDH"
    assert row.source_of_designation in CONFIRMED_SOURCES
    assert contributes_ratios(table, FIB_CCN2) is True


def test_a_single_target_row_parses_to_one_target(table: dict[str, Designation]) -> None:
    """The list mechanism leaves single-target rows alone -- no separator, one element."""
    row = table["PMC12686555_FIGURE1__A-p16p21-ACTIN.png"]

    assert row.target_labels == ("p16p21",)
    assert "|" not in row.target_label


def test_eleven_of_twelve_rows_contribute_ratios(table: dict[str, Designation]) -> None:
    """The count W7 reads: every crop but the reference strip contributes.

    Not the same as the row count and not the same as the blot count, so it is asserted on its
    own rather than inferred from either.
    """
    contributing = [crop for crop in table if contributes_ratios(table, crop)]

    assert len(table) == 12
    assert len(contributing) == 11
    assert VINCULIN not in contributing


@pytest.mark.parametrize(
    "source", [PARSED_PENDING, PARSED_AMBIGUOUS_PENDING, UNPARSED_NEEDS_HUMAN]
)
def test_asking_for_a_confirmation_on_a_pending_row_raises(source: str) -> None:
    """The read path never falls back to the parsed candidate. It raises and says why.

    Built synthetically now that every committed row is ruled. The guard is what stands between
    a future crop's proposal and a measurement, so it is tested on a table in the state a future
    crop arrives in, not on the state the committed file happens to be in today.
    """
    table = {"x.png": Designation("x.png", "TARGET", "ACTIN", source, "")}

    with pytest.raises(DesignationError, match="no human has confirmed"):
        confirmed_reference_label(table, "x.png")


def test_asking_whether_a_pending_row_contributes_raises_rather_than_returning_false(
) -> None:
    """"Nobody has looked" and "a human looked and said no" are different answers.

    Returning False for a pending row would let an unconfirmed crop drop out of N silently,
    which is the failure this whole table exists to prevent, arriving through the back door.
    """
    table = {"x.png": Designation("x.png", "TARGET", "ACTIN", PARSED_PENDING, "")}

    with pytest.raises(DesignationError, match="no human has ruled"):
        contributes_ratios(table, "x.png")


def test_a_reference_strip_row_refuses_to_yield_a_reference_label() -> None:
    """A ruled no-ratio row is not a confirmation to measure against, even though it is ruled."""
    source = sorted(NO_RATIO_SOURCES)[0]
    table = {"x.png": Designation("x.png", "", "Vinculin", source, "")}

    with pytest.raises(DesignationError, match="contributes no ratio"):
        confirmed_reference_label(table, "x.png")


@pytest.mark.parametrize("cell", ["A||B", "A|", "|B", "A|A"])
def test_a_malformed_target_list_is_refused_rather_than_tidied(
    tmp_path: Path, cell: str
) -> None:
    """A blank target would contribute a nameless ratio; a repeated one would count twice."""
    path = tmp_path / "designations.csv"
    path.write_text(
        ",".join(COLUMNS) + "\n" + f"x.png,{cell},ACTIN,caption_confirmed_human,\n",
        encoding="utf-8",
    )

    with pytest.raises(DesignationError, match="blank target|more than once"):
        read_designations(path)


def test_asking_about_a_crop_with_no_row_raises(table: dict[str, Designation]) -> None:
    """An unlisted crop is excluded and needs a ruling; it is not given a default."""
    with pytest.raises(DesignationError, match="no row in the designation table"):
        confirmed_reference_label(table, "PMC13135388_Figure4__E-TIGAR.png")


def test_an_unrecognised_source_is_never_read_as_a_confirmation() -> None:
    """The allow-list, tested from the outside: a plausible-looking word is still refused.

    This is the direction that matters. A reader written as "not pending, therefore confirmed"
    passes every other test in this file and fails this one.
    """
    table = {
        "x.png": Designation("x.png", "TARGET", "ACTIN", "looks_confirmed_to_me", "")
    }

    with pytest.raises(DesignationError, match="not a value this reader knows"):
        confirmed_reference_label(table, "x.png")


def test_a_confirmation_with_a_blank_reference_cannot_even_be_constructed() -> None:
    """Confirming a row whose reference cell is empty confirms nothing, and it now cannot exist.

    This invariant used to live in the read path. It moved onto the row itself, because the
    reader is not the only way a row reaches the file -- the writer writes what it is handed and
    a human hand-edits the table -- and a check that runs only on read lets a malformed row be
    written now and rejected later, by someone who did not write it.
    """
    source = sorted(CONFIRMED_SOURCES)[0]

    with pytest.raises(DesignationError, match="reference_label is blank"):
        Designation("x.png", "TARGET", "", source, "")


def test_a_confirmed_row_naming_no_target_cannot_be_constructed() -> None:
    """The gap that let a blank-target row yield a ratio with zero targets.

    E-Vinculin *is* a blank-target row; the only thing keeping it out of N was one vocabulary
    word sitting next to its neighbour in the header note. Changing that one cell moved N from
    11 to 12 with a nameless ratio and nothing objecting. The row shape and the word now have to
    agree.
    """
    source = sorted(CONFIRMED_SOURCES)[0]

    with pytest.raises(DesignationError, match="names no target"):
        Designation("x.png", "", "GAPDH", source, "")


def test_a_no_ratio_row_carrying_a_target_cannot_be_constructed() -> None:
    """The mirror, which is the direction that silently *removes* a target from N."""
    source = sorted(NO_RATIO_SOURCES)[0]

    with pytest.raises(DesignationError, match="contributes no ratio, but it names target"):
        Designation("x.png", "TIGAR", "Vinculin", source, "")


def test_an_unrecognised_source_imposes_no_vocabulary_versus_shape_constraint() -> None:
    """The row refuses shapes it knows to be wrong; the reader refuses words it does not know.

    Keeping the two apart is what lets the allow-list test below construct a row with a
    plausible-looking source and check that the *reader* rejects it. The target-list checks are
    not part of that split and still bind: asserted below, because the earlier name for this test
    claimed an unrecognised source constrained the shape "not at all", which is false.
    """
    Designation("x.png", "", "", "looks_confirmed_to_me", "")

    with pytest.raises(DesignationError, match="blank target"):
        Designation("x.png", "A|", "", "looks_confirmed_to_me", "")
    with pytest.raises(DesignationError, match="more than once"):
        Designation("x.png", "A|A", "", "looks_confirmed_to_me", "")


def test_asking_whether_an_unlisted_crop_contributes_raises_rather_than_returning_false(
    table: dict[str, Designation],
) -> None:
    """A crop with no row is unrecorded, not excluded, and the two must not read alike.

    Returning False here would drop an unlisted crop out of N with no one deciding to, which is
    the same silent-exclusion failure the pending-row guard above exists to prevent.
    """
    with pytest.raises(DesignationError, match="unrecorded rather than false"):
        contributes_ratios(table, "PMC13135388_Figure4__E-TIGAR.png")


def test_the_cli_entry_point_preserves_every_ruling(tmp_path: Path) -> None:
    """The write path a human actually runs, end to end -- not the call shape tests use.

    The merge lived only in ``main()``, and every other test supplied ``existing=`` itself. So
    deleting the merge from ``main()`` passed the whole suite while
    ``python -m tools.phase3.designations`` -- the command this file's own header note tells a
    human to run -- reverted all twelve rulings and exited 0. This test is that command.
    """
    out = tmp_path / "designations.csv"
    out.write_bytes(DESIGNATIONS_PATH.read_bytes())

    assert designations_main(["--out", str(out)]) == 0

    assert out.read_bytes() == DESIGNATIONS_PATH.read_bytes()
    assert {row.source_of_designation for row in read_designations(out).values()} <= (
        RULED_SOURCES
    )


def test_a_rebuild_refuses_to_drop_a_ruling_for_a_crop_that_left_the_set(
    table: dict[str, Designation],
) -> None:
    """Carrying rulings through is half the protection; refusing to drop one is the other half.

    Both builders return a row per *measurable* crop, so a narrowing of the loader's admissible
    set would return fewer rows than it was given and write the shorter table. Deleting a ruling
    may well be right when a crop stops being measurable — but not at exit code 0 with nobody
    deciding it.
    """
    stranded = dict(table)
    stranded["PMC13135388_Figure4__E-TIGAR.png"] = Designation(
        "PMC13135388_Figure4__E-TIGAR.png", "TIGAR", "Vinculin", "caption_confirmed_human", ""
    )

    with pytest.raises(CorpusError, match="no longer makes measurable"):
        build_designations(existing=stranded)


def test_the_writer_refuses_two_rows_for_one_crop(tmp_path: Path) -> None:
    """The writer validates what it writes, as the identity writer does.

    Otherwise a malformed table is written now and refused later, on read, by someone else.
    """
    row = Designation("x.png", "T", "ACTIN", "caption_confirmed_human", "")

    with pytest.raises(DesignationError, match="appears more than once"):
        write_designations([row, row], tmp_path / "designations.csv")


def test_a_confirmed_row_returns_its_reference() -> None:
    """The other half: once a human has confirmed a row, the reader hands the value over."""
    source = sorted(CONFIRMED_SOURCES)[0]
    table = {"x.png": Designation("x.png", "TARGET", "ACTIN", source, "read from the caption")}

    assert confirmed_reference_label(table, "x.png") == "ACTIN"


def test_the_table_round_trips_through_the_file(tmp_path: Path) -> None:
    """What is written is what is read back, header note and quoted notes included."""
    designations = build_designations()
    path = write_designations(designations, tmp_path / "designations.csv")

    reread = read_designations(path)

    assert list(reread.values()) == designations


def test_a_reordered_column_is_refused_rather_than_read(tmp_path: Path) -> None:
    """Column order is meaning here, so a reordered file fails instead of being reinterpreted."""
    path = tmp_path / "designations.csv"
    path.write_text(
        "reference_label,crop_filename,target_label,source_of_designation,notes\n"
        "ACTIN,x.png,TARGET,caption_confirmed_human,\n",
        encoding="utf-8",
    )

    with pytest.raises(DesignationError, match="expected exactly"):
        read_designations(path)


def test_a_duplicated_crop_is_refused(tmp_path: Path) -> None:
    """Two designations for one crop cannot both be the caption's."""
    path = tmp_path / "designations.csv"
    path.write_text(
        ",".join(COLUMNS) + "\n"
        "x.png,TARGET,ACTIN,caption_confirmed_human,\n"
        "x.png,TARGET,GAPDH,caption_confirmed_human,\n",
        encoding="utf-8",
    )

    with pytest.raises(DesignationError, match="more than once"):
        read_designations(path)


def test_the_writer_refuses_a_destination_inside_the_gold_set(tmp_path: Path) -> None:
    """``--out`` is a path from the command line, so PLAN.md's key invariant is enforced here.

    ``data/ground_truth/`` is written only by ``python -m synth``. Nothing here would write
    there deliberately, which is exactly why the boundary is a check rather than a convention.
    """
    destination = tmp_path / "ground_truth" / "designations.csv"

    with pytest.raises(PipelineError, match="ground truth is written only by"):
        write_designations(build_designations(), destination)
    assert not destination.parent.exists(), "and nothing is created on the way to refusing"


def test_exactly_the_two_flagged_rows_were_not_confirmed_as_parsed(
    table: dict[str, Designation],
) -> None:
    """The gate's outcome, counted: ten rows confirmed as parsed, two not.

    The two the human did not simply confirm are exactly the two the parser had flagged as
    unsafe -- the ambiguous split and the one that named no reference. That correspondence is
    the case for having flagged them at all, so it is asserted rather than described: a parser
    whose flags landed on different rows than the human's corrections would have been noise.
    """
    confirmed_as_parsed = {
        crop for crop, row in table.items()
        if row.notes.startswith("confirmed as parsed")
    }

    assert len(confirmed_as_parsed) == 10
    assert set(table) - confirmed_as_parsed == {VINCULIN, FIB_CCN2}


def test_the_committed_file_is_byte_identical_to_what_the_writer_emits(tmp_path: Path) -> None:
    """Rows *and* header note. Comparing rows alone lets the committed note drift unnoticed.

    The header note is where this file states what its vocabulary licenses and what the human
    ruled. A reader who opens the CSV sees the note before any row, so it is the load-bearing
    half for them, and it is the half a row-wise comparison does not cover.
    """
    rebuilt = write_designations(
        build_designations(existing=read_designations(DESIGNATIONS_PATH)),
        tmp_path / "designations.csv",
    )

    assert rebuilt.read_bytes() == DESIGNATIONS_PATH.read_bytes()
