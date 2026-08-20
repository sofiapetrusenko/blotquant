"""Tests for the amendment-figure check in tools/check_claims.py.

Why these exist. Two review cycles found that this check said more than it did: first it
matched a value anywhere in the reference file, then anywhere in the located row, and both
times the overclaim was in a docstring rather than in the behaviour. A check nothing tests is
a claim, and this file is about a mechanism whose whole purpose is to stop claims drifting.

The tests below mutate copies, never the committed files.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tools.check_claims import (
    AMENDMENT_NOTE_START,
    AMENDMENT_RULINGS_MARKER,
    QUOTED_FIGURES,
    check_amendment_figures,
)

AMENDMENT = Path("data/real/AMENDMENT_2026-08-19_delta_and_power.md")
REFERENCE = Path("tools/stats/rs_power_expected.txt")
QUOTED_SOURCES = (Path("data/real/DECISION_unit_of_analysis.md"), Path("NOTES.md"))


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the two pinned files, with the checker pointed at the copy."""
    (tmp_path / "data" / "real").mkdir(parents=True)
    (tmp_path / "tools" / "stats").mkdir(parents=True)
    shutil.copyfile(AMENDMENT, tmp_path / AMENDMENT)
    shutil.copyfile(REFERENCE, tmp_path / REFERENCE)
    # The note's blockquotes are verified against these, so a sandbox without them would test
    # a checker that cannot run rather than one that passes.
    for source in QUOTED_SOURCES:
        shutil.copyfile(source, tmp_path / source)
    # REPO_ROOT, not the CWD. The check resolves through REPO_ROOT precisely so it cannot
    # no-op when run from elsewhere, and a fixture that worked by chdir would be testing the
    # bug rather than the fix.
    monkeypatch.setattr("tools.check_claims.REPO_ROOT", tmp_path)
    return tmp_path


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} is not in {path}; the test's premise is stale"
    path.write_text(text.replace(old, new, 1))


def test_the_committed_pair_passes(sandbox: Path) -> None:
    """The baseline: unmutated, the check is silent."""
    assert check_amendment_figures() == []


def test_a_corrupted_reference_value_is_caught(sandbox: Path) -> None:
    """The ordinary case: a digit changes in the cell a figure was read from."""
    _edit(sandbox / REFERENCE, "26.8", "26.9")
    hits = check_amendment_figures()
    assert hits, "a changed N_eff must fail"
    assert any("N_eff, ICC 0.5" in hit.message for hit in hits)


def test_values_transposed_within_one_row_are_caught(sandbox: Path) -> None:
    """The defect two cycles missed: the value is still on its row, in the wrong column.

    Table E's N = 30 row carries three quoted figures. Swapping the true-0.70 and true-0.80
    cells inverts the amendment's safety claim while leaving every value present in the file
    and on its own row, so only a column-anchored check can see it.
    """
    path = sandbox / REFERENCE
    lines = path.read_text().splitlines()
    for index, line in enumerate(lines):
        if line.startswith("  30 |"):
            values = line.split("|")[1].split()
            values[1], values[2] = values[2], values[1]
            lines[index] = "  30 |" + "".join(f"{v:>11}" for v in values)
            break
    else:  # pragma: no cover - the row is in the committed file
        pytest.fail("table E has no N = 30 row; the test's premise is stale")
    path.write_text("\n".join(lines) + "\n")

    hits = check_amendment_figures()
    assert hits, "transposing two cells of one row must fail"
    assert any("true 0.70" in hit.message or "true 0.80" in hit.message for hit in hits)


def test_analytic_and_observed_transposed_are_caught(sandbox: Path) -> None:
    """The same, on table C, where the two values are labelled rather than positional."""
    _edit(
        sandbox / REFERENCE,
        "sd(z) 0.592 (analytic 0.448)",
        "sd(z) 0.448 (analytic 0.592)",
    )
    assert check_amendment_figures(), "swapping observed and analytic sd(z) must fail"


def test_an_edited_ruling_figure_is_caught_despite_the_note_recording_it(sandbox: Path) -> None:
    """The note records the same figure in its table; a ruling edit must not hide behind it."""
    path = sandbox / AMENDMENT
    note, rulings = path.read_text().split(AMENDMENT_RULINGS_MARKER, 1)
    assert "`N >= 18`" in note, "the note is expected to record this figure in its table"
    assert "N ≥ 18" in rulings, "the ruling is expected to state it in prose"
    path.write_text(note + AMENDMENT_RULINGS_MARKER + rulings.replace("N ≥ 18", "N ≥ 25", 1))

    hits = check_amendment_figures()
    assert hits, "an edited headline figure must fail even though the note records it too"
    assert any("0.90 vs 0.70" in hit.message for hit in hits)


def test_a_figure_duplicated_inside_the_rulings_is_refused(sandbox: Path) -> None:
    """Two copies of a figure would let a change hide behind the unchanged one."""
    path = sandbox / AMENDMENT
    note, rulings = path.read_text().split(AMENDMENT_RULINGS_MARKER, 1)
    path.write_text(note + AMENDMENT_RULINGS_MARKER + rulings + "\n\nN ≥ 18 again.\n")

    hits = check_amendment_figures()
    assert any("appears 2 times" in hit.message for hit in hits)


@pytest.mark.parametrize("marker", [AMENDMENT_NOTE_START, AMENDMENT_RULINGS_MARKER])
def test_a_missing_marker_fails_rather_than_searching_the_whole_file(
    sandbox: Path, marker: str
) -> None:
    """Without both markers the note cannot be delimited, so the check refuses to pass."""
    _edit(sandbox / AMENDMENT, marker, "")
    hits = check_amendment_figures()
    assert any("must both be" in hit.message for hit in hits)


def test_a_second_amendment_does_not_inherit_this_one_s_pins(sandbox: Path) -> None:
    """The pins belong to one named file. A later amendment brings its own.

    This is the regression test for the glob that used to select them: under
    ``AMENDMENT_*.md`` every figure of this amendment was demanded from any other, so writing a
    second amendment turned CI red on its first commit.
    """
    other = sandbox / "data" / "real" / "AMENDMENT_2026-09-01_unrelated.md"
    other.write_text("# A later amendment\n\nIt quotes no figures and has no note.\n")
    assert check_amendment_figures() == []


def test_a_figure_in_the_note_s_prose_fails_the_build(sandbox: Path) -> None:
    """The rule the human ratified: figures live in tables, prose references them by tag."""
    path = sandbox / AMENDMENT
    note, rulings = path.read_text().split(AMENDMENT_RULINGS_MARKER, 1)
    path.write_text(note + "\nThe control row returned 34.0 for a nominal 30.\n"
                    + AMENDMENT_RULINGS_MARKER + rulings)

    hits = check_amendment_figures()
    assert any(hit.check == "amendment-note-digits" for hit in hits)
    assert any("live in tables" in hit.message for hit in hits)


@pytest.mark.parametrize(
    "line",
    [
        "It amends §5 and §8(c), recorded 2026-08-19.",
        "Gate 1 ruling 8 and Phase 3b-0 and Ruling 2 are references, not figures.",
        "W0 produced it, under Gate 1 ruling 3.",
        "Its sha256 is e3df643080ee2add4acef10662bc0df61ff7974dfe3a36147e378645294c3697.",
        "| a table row | may hold 26.8 | freely |",
        "> a quoted block may hold 0.9 / 0.7, because it is another document's words",
    ],
)
def test_declared_exemptions_are_not_figures(sandbox: Path, line: str) -> None:
    """Each exemption is a pattern, and each must actually be exempt."""
    path = sandbox / AMENDMENT
    note, rulings = path.read_text().split(AMENDMENT_RULINGS_MARKER, 1)
    path.write_text(note + f"\n{line}\n" + AMENDMENT_RULINGS_MARKER + rulings)

    assert not [h for h in check_amendment_figures() if h.check == "amendment-note-digits"]


@pytest.mark.parametrize(
    "line",
    [
        "Effective N lands at E34 against a nominal S30.",
        "The control row returned 34.0 for a nominal 30.",
        "Retention was 0.79 across the middle ICC.",
    ],
)
def test_figures_dressed_as_references_are_not_exempt(sandbox: Path, line: str) -> None:
    """An exemption that would let a figure through is a loophole, not a narrow exception.

    The first case is the one that removed a pattern: ``\\b[SEP]\\d{1,2}\\b`` was declared for
    debt-register ids, matched nothing in the note, and exempted any small figure written
    ``E34``.
    """
    path = sandbox / AMENDMENT
    note, rulings = path.read_text().split(AMENDMENT_RULINGS_MARKER, 1)
    path.write_text(note + f"\n{line}\n" + AMENDMENT_RULINGS_MARKER + rulings)

    assert [h for h in check_amendment_figures() if h.check == "amendment-note-digits"]


def test_the_reported_line_is_the_line_in_the_file(sandbox: Path) -> None:
    """A line number counted from the note's start is useless with the file open."""
    path = sandbox / AMENDMENT
    note, rulings = path.read_text().split(AMENDMENT_RULINGS_MARKER, 1)
    offender = "The control row returned 34.0."
    path.write_text(f"{note}\n{offender}\n{AMENDMENT_RULINGS_MARKER}{rulings}")

    hits = [h for h in check_amendment_figures() if h.check == "amendment-note-digits"]
    assert len(hits) == 1
    lines = path.read_text().splitlines()
    assert lines[hits[0].line - 1] == offender


def test_the_digit_rule_does_not_reach_the_rulings(sandbox: Path) -> None:
    """The rule is the implementer's discipline for the implementer's note, and stops there."""
    path = sandbox / AMENDMENT
    note, rulings = path.read_text().split(AMENDMENT_RULINGS_MARKER, 1)
    path.write_text(note + AMENDMENT_RULINGS_MARKER + rulings + "\n\nA ruling may say 0.90.\n")

    assert not [h for h in check_amendment_figures() if h.check == "amendment-note-digits"]


def test_no_amendment_present_is_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The check is about an amendment that exists; absence is not a failure."""
    monkeypatch.setattr("tools.check_claims.REPO_ROOT", tmp_path)
    assert check_amendment_figures() == []


def test_the_check_does_not_depend_on_the_working_directory(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run from anywhere, it must still find both files and still fire."""
    _edit(sandbox / AMENDMENT, "N ≥ 18", "N ≥ 25")
    monkeypatch.chdir(Path(__file__).parent)
    assert check_amendment_figures(), "a CWD-relative check would silently pass here"


def test_a_duplicated_reference_row_is_refused(sandbox: Path) -> None:
    """Corrupt one copy of a row and leave an intact one: the duplicate is the failure."""
    path = sandbox / REFERENCE
    out = []
    for line in path.read_text().splitlines():
        if "10 blots x 3, ICC 0.5" in line:
            out.append(line.replace("26.8", "99.9"))
        out.append(line)
    path.write_text("\n".join(out) + "\n")

    hits = check_amendment_figures()
    assert any("rows under section" in hit.message for hit in hits)


def test_a_missing_reference_is_a_failure_when_an_amendment_quotes_it(sandbox: Path) -> None:
    """Deleting the reference must not silently retire the pin."""
    (sandbox / REFERENCE).unlink()
    hits = check_amendment_figures()
    assert any("is missing" in hit.message for hit in hits)


def test_every_quoted_figure_names_a_distinct_check(sandbox: Path) -> None:
    """A duplicated entry would double-report one figure and cover none of another."""
    keys = [(f.in_amendment, f.pattern) for f in QUOTED_FIGURES]
    assert len(keys) == len(set(keys))
    assert all(f.region in {"rulings", "note"} for f in QUOTED_FIGURES)


def test_a_corrupted_blockquote_fails(sandbox: Path) -> None:
    """The blockquote exemption is only safe if a blockquote is really a quotation."""
    _edit(
        sandbox / AMENDMENT,
        "> DECISION: collection stops at 30 ratios or 10 blots, whichever comes first.",
        "> DECISION: collection stops at 40 ratios or 10 blots, whichever comes first.",
    )
    hits = [h for h in check_amendment_figures() if h.check == "amendment-note-quotations"]
    assert hits, "a quotation that has drifted from its source must fail"
    assert "not verbatim" in hits[0].message


def test_every_note_blockquote_is_verbatim_today(sandbox: Path) -> None:
    """The committed note's quotations all resolve, against three named sources."""
    assert not [
        h for h in check_amendment_figures() if h.check == "amendment-note-quotations"
    ]


def test_an_elided_quotation_still_has_to_be_verbatim_on_both_sides(sandbox: Path) -> None:
    """An elision marker omits words; it does not license rewriting what surrounds it."""
    _edit(
        sandbox / AMENDMENT,
        "> checked once N is known. If it cannot discriminate,",
        "> checked once N is unknown. If it cannot discriminate,",
    )
    assert [h for h in check_amendment_figures() if h.check == "amendment-note-quotations"]


def test_the_rulings_are_a_valid_quotation_source(sandbox: Path) -> None:
    """The note may quote the amendment's own rulings; editing the ruling must break it.

    Written against an injected quotation rather than whichever passage the note happens to
    quote today, so the test survives the rulings being rewritten -- which is exactly what
    retired its predecessor when the human's ruling R3 rewrote the headline it quoted.
    """
    path = sandbox / AMENDMENT
    note, rulings = path.read_text().split(AMENDMENT_RULINGS_MARKER, 1)
    quotable = "The verdict remains on r_s alone (Gate 1 ruling 8)."
    assert quotable in rulings, "the test's premise is stale"

    path.write_text(f"{note}\n> {quotable}\n{AMENDMENT_RULINGS_MARKER}{rulings}")
    assert not [h for h in check_amendment_figures() if h.check == "amendment-note-quotations"]

    path.write_text(
        f"{note}\n> {quotable}\n{AMENDMENT_RULINGS_MARKER}"
        + rulings.replace(quotable, "The verdict remains on nothing at all.")
    )
    assert [h for h in check_amendment_figures() if h.check == "amendment-note-quotations"]
