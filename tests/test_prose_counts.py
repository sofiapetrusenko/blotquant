"""Tests for the prose-count check (the authorship freeze's mechanical half, 2026-08-25).

Why these exist. The check reports rather than fails, so nothing else in the build will notice
if it stops catching things — a report-only check that has gone blind looks exactly like a clean
tree. Both directions are pinned here: it must fire on a count asserted in prose, and it must
stay silent on a count that is a table row, a quotation, a pinned figure, or human-marked.

The primary half of the freeze is an authorship rule and is not testable; see NOTES.md,
"Authorship freeze".
"""

from __future__ import annotations

import pytest

from tools.check_claims import COUNT_ALLOW_MARKER, COUNT_BLOCKING, check_prose_counts


def _hits(text: str) -> list[str]:
    """Return the messages the check produces for one synthetic document."""
    return [hit.message for hit in check_prose_counts([("t.md", text.split("\n"), False)])]


@pytest.mark.parametrize(
    "text",
    [
        "There are 7 crops in the set.",
        "There are seventeen entries in the register.",
        "Two files hold the record.",
        "The scan finds 6 files.",
    ],
)
def test_a_count_asserted_in_prose_is_reported(text: str) -> None:
    """The whole class: a number and a counted noun, with the evidence somewhere else."""
    assert _hits(text), f"expected a prose-count advisory for {text!r}"


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("a table row", "| total | 7 crops |"),
        ("a blockquote", "> There are 7 crops in the set."),
        ("a code fence", "```\nThere are 7 crops.\n```"),
        ("an allow marker on the line", f"There are 7 crops. <!-- {COUNT_ALLOW_MARKER} -->"),
        ("an allow marker above", f"<!-- {COUNT_ALLOW_MARKER} -->\nThere are 7 crops."),
        ("a noun outside the counted set", "There are 7 reasons for this."),
        ("a count of one", "One file holds the record."),
        ("a version number", "Schema 1.4.0 adds 1 field."),
    ],
)
def test_what_the_freeze_does_not_reach_is_not_reported(label: str, text: str) -> None:
    """Each exemption is deliberate; a check that fires on all of them teaches skimming."""
    assert not _hits(text), f"{label} must not be reported"


def test_a_count_derived_from_a_table_below_it_is_accepted() -> None:
    """The rule's positive form: the rows are the things, so the count cannot drift from them."""
    document = "The three files:\n\n| file | n |\n|---|---|\n| a | 1 |\n| b | 2 |\n| c | 3 |"

    assert not _hits(document)


def test_a_table_with_too_few_rows_does_not_licence_the_count() -> None:
    """Otherwise any table nearby would launder any number — which is the defect, not the fix."""
    document = "The three files:\n\n| file | n |\n|---|---|\n| a | 1 |"

    assert _hits(document)


def test_a_table_further_away_than_the_window_does_not_licence_the_count() -> None:
    """A count whose table is a screen away is not obviously derived from it."""
    document = "The three files:\n" + "\n" * 14 + "| file | n |\n|---|---|\n| a |\n| b |\n| c |"

    assert _hits(document)


def test_a_verbatim_extract_is_exempt() -> None:
    """Same reason the retracted-phrase check exempts them: they record what someone wrote."""
    lines = "There are 7 crops in the set.".split("\n")

    assert check_prose_counts([("docs/review/x/cycle-1.md", lines, True)]) == []


def test_the_message_names_the_freeze_and_the_way_out() -> None:
    """An advisory a reader cannot act on is noise, and this one reports rather than fails."""
    (message,) = _hits("There are 7 crops in the set.")

    assert "authorship freeze" in message
    assert "table whose rows are the things counted" in message
    assert COUNT_ALLOW_MARKER in message


# --- the 2026-08-25 blocking/advisory split ---------------------------------------------------


def _checks(rel: str, text: str) -> list[str]:
    """Return the check name of each hit for one synthetic document at ``rel``."""
    return [hit.check for hit in check_prose_counts([(rel, text.split("\n"), False)])]


def test_a_count_in_a_blocking_file_is_marked_blocking() -> None:
    """Ruling 2: a document created after it, facing an external reader, fails rather than warns."""
    (blocking,) = sorted(COUNT_BLOCKING)[:1]

    assert _checks(blocking, "There are 7 crops in the set.") == ["prose-count-blocking"]


def test_a_count_in_any_other_file_stays_advisory() -> None:
    """Ruling 2: the existing record is not cleaned in v1.0, so it cannot fail the build."""
    assert _checks("NOTES.md", "There are 7 crops in the set.") == ["prose-count"]


def test_the_blocking_set_is_exact_paths_not_a_glob() -> None:
    """A glob would sweep in pre-ruling documents the first time one was renamed."""
    for path in COUNT_BLOCKING:
        assert "*" not in path and "?" not in path
