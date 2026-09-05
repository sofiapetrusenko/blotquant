"""The anti-divergence mechanism, checked from the Python side.

``web/lib/verdict.ts`` is a second implementation of the ruled verdict mapping
(``docs/PRE_REGISTRATION_2026-08-25_verdict_mapping.md``, Ruling 1), and the only thing holding
it to :func:`api.display.lane_verdicts` is the fixture tree under ``web/test/fixtures/verdict/``.
That tree is generated from the Python function, so it can go stale the moment the Python
changes -- and a stale fixture set does not look stale: the TypeScript test stays green while
the two implementations disagree.

These tests close that. The first asserts the committed fixtures are exactly what the current
``api/display.py`` produces, so a change to the mapping that is not regenerated fails ``pytest``
rather than being discovered in a browser. The rest assert the generator's own properties: that
it is deterministic, that its ``expected`` blocks come from the Python function rather than from
a hand-written table, and that it refuses to write a refusal fixture for a document the
derivation does not actually refuse.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.display import BAND_QC_FLAGS, lane_verdicts
from api.errors import DisplayError
from tools.gallery.verdict_fixtures import (
    DIVERGENCE_NAME,
    ERROR_SUBDIR,
    FIXTURE_ROOT,
    GALLERY_ROOT,
    VOCABULARY_NAME,
    Case,
    _divergence_cases,
    _error_cases,
    _synthetic_cases,
    _write_error_fixture,
    check,
    generate,
)


def _fixtures(directory: Path) -> list[dict[str, object]]:
    """Return every *case* fixture in ``directory``, sorted by file name.

    ``vocabulary.json`` and ``divergences.json`` are excluded: neither carries a document with
    an expected verdict. The first pins the flag vocabulary, the second records what this module
    does with documents the schema forbids. Both are asserted on their own below.
    """
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"))
        if path.name not in {VOCABULARY_NAME, DIVERGENCE_NAME}
    ]


MINIMUM_DIVERGENCE_CASES = 28
"""Floor on the divergence table. **A floor, and never a census.**

It exists in **pytest** rather than only in ``web/test/verdict.test.ts`` because the web suite
runs in no CI job (NOTES.md, "The web suite runs in no CI job"). Emptying
:func:`_divergence_cases` would otherwise leave the drift check green with nothing recorded and
nothing for the TypeScript to be asserted against.

**No prose anywhere may quote this number as the size of the table**, and that prohibition is
written here because it has already been violated: an earlier revision said "25 schema-invalid
documents" in ``NOTES.md`` and ``web/README.md`` after three cases had been added and this floor
had not been raised, so a floor was being reported as a count in two files at once. That is the
same hand-copied-figure defect the divergence table was built to eliminate, committed in the
passage describing it. The size of the table is ``len(_divergence_cases())`` and is read from
``web/test/fixtures/verdict/divergences.json``; the guarantee this constant carries is only that
the table has not been hollowed out.
"""


def test_the_divergence_table_records_every_case_and_its_observed_outcome(
    tmp_path: Path,
) -> None:
    """The table is the record of how the two implementations differ, so it must not empty out.

    Each case's ``python`` block is an observation of :func:`api.display.lane_verdicts` -- what it
    returned, or which exception class it raised -- and nothing in the fixture states what the
    TypeScript does. Asserted here: the table is populated, every case carries an outcome, and at
    least one case is a document the Python answers rather than refuses, which is what makes it a
    divergence table rather than a second list of refusals.
    """
    assert len(_divergence_cases()) >= MINIMUM_DIVERGENCE_CASES

    generate(tmp_path, GALLERY_ROOT)
    emitted = json.loads((tmp_path / DIVERGENCE_NAME).read_text(encoding="utf-8"))
    cases = emitted["cases"]
    assert len(cases) >= MINIMUM_DIVERGENCE_CASES
    answered = 0
    for case in cases:
        outcome = case["python"]
        assert outcome["outcome"] in {"returns", "raises"}, case["name"]
        if outcome["outcome"] == "returns":
            assert isinstance(outcome["verdicts"], list), case["name"]
            answered += 1
        else:
            assert isinstance(outcome["error_type"], str) and outcome["error_type"], case["name"]
    assert answered > 0, (
        "no case in the divergence table is one the Python answers; the table would be recording "
        "agreement rather than divergence"
    )
    committed = json.loads((FIXTURE_ROOT / DIVERGENCE_NAME).read_text(encoding="utf-8"))
    assert committed == emitted, (
        "the committed divergence table is not what this api/display.py produces; regenerate "
        "with 'python -m tools.gallery.verdict_fixtures'"
    )


def test_the_vocabulary_fixture_is_the_modules_own_tuple(tmp_path: Path) -> None:
    """The one input to the mapping that no case fixture can pin.

    ``_ordered_flags`` reports a lane's flags in ``BAND_QC_FLAGS`` order and sorts anything
    outside the vocabulary last, and ``web/lib/verdict.ts`` retypes that tuple instead of
    importing it. A flag added to ``pipeline/qc.py`` would therefore take a vocabulary position
    in the Python and sort as an unknown in the TypeScript, and **no document-plus-verdict
    fixture could contain it**, so every check in this project would stay green while the two
    implementations ordered flags differently in production.

    This asserts the emitted fixture is the module's own tuple, in order; ``web/test/verdict
    .test.ts`` asserts the TypeScript constant equals the fixture. Between the two, the
    vocabulary cannot drift silently.
    """
    generate(tmp_path, GALLERY_ROOT)
    emitted = json.loads((tmp_path / VOCABULARY_NAME).read_text(encoding="utf-8"))
    assert emitted["band_qc_flags"] == list(BAND_QC_FLAGS), (
        "the vocabulary fixture is not api.display.BAND_QC_FLAGS; it is written from that tuple "
        "and must never be authored by hand"
    )
    committed = json.loads((FIXTURE_ROOT / VOCABULARY_NAME).read_text(encoding="utf-8"))
    assert committed["band_qc_flags"] == list(BAND_QC_FLAGS), (
        "the committed vocabulary fixture has drifted from pipeline.qc.BAND_QC_FLAGS; "
        "regenerate with 'python -m tools.gallery.verdict_fixtures'"
    )


def test_the_committed_fixtures_match_the_current_display_module() -> None:
    """The whole point: a change to ``api/display.py`` must be reflected in the fixtures.

    If this fails, the TypeScript mirror is being asserted against a mapping the Python no
    longer implements. Regenerate with ``python -m tools.gallery.verdict_fixtures`` and read the
    diff -- it is a diff of a ruled mapping, not of a test artefact.
    """
    assert check(FIXTURE_ROOT, GALLERY_ROOT) == []


def test_the_generator_is_deterministic(tmp_path: Path) -> None:
    """Two generations produce a byte-identical tree, which is what makes ``--check`` mean anything.

    A generator whose output moved between runs would make every ``--check`` a coin toss, and
    the mechanism above would report drift that was not there -- teaching its reader to
    regenerate on red rather than to read the diff.
    """
    first = tmp_path / "first"
    second = tmp_path / "second"
    generate(first, GALLERY_ROOT)
    generate(second, GALLERY_ROOT)

    names = sorted(str(path.relative_to(first)) for path in first.rglob("*.json"))
    assert names == sorted(str(path.relative_to(second)) for path in second.rglob("*.json"))
    assert names, "the generator emitted no fixtures at all"
    for name in names:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


def test_every_expected_block_is_the_python_functions_own_output(tmp_path: Path) -> None:
    """The fixtures' ``expected`` is re-derived here and must match, field by field.

    This is the assertion that the fixture tree is a *record* of ``api/display.py`` rather than
    a second hand-written statement of the mapping. Flag order is included: the lists are
    compared as lists, so a fixture whose flags were sorted differently fails.
    """
    generate(tmp_path, GALLERY_ROOT)
    fixtures = _fixtures(tmp_path)
    assert fixtures, "the generator emitted no verdict fixtures"
    for fixture in fixtures:
        document = fixture["document"]
        assert isinstance(document, dict)
        expected = [verdict.as_dict() for verdict in lane_verdicts(document)]
        assert fixture["expected"] == expected, fixture["name"]


def test_every_refusal_fixture_really_is_refused(tmp_path: Path) -> None:
    """Each ``errors/`` fixture names a document ``lane_verdicts`` raises on -- checked, not
    claimed."""
    generate(tmp_path, GALLERY_ROOT)
    fixtures = _fixtures(tmp_path / ERROR_SUBDIR)
    assert fixtures, "the generator emitted no refusal fixtures"
    for fixture in fixtures:
        assert fixture["expected_error"] is True, fixture["name"]
        document = fixture["document"]
        assert isinstance(document, dict)
        with pytest.raises(DisplayError):
            lane_verdicts(document)


def test_a_refusal_fixture_that_does_not_refuse_fails_the_generator(tmp_path: Path) -> None:
    """The generator must not write a fixture claiming a refusal that does not happen.

    Handed a perfectly good document under a refusal case's name, it raises and writes nothing.
    Without this the ``errors/`` directory could fill with documents that pin the TypeScript to
    throw where the Python returns -- a divergence introduced by the anti-divergence tool.
    """
    good = Case(
        name="not_actually_a_refusal",
        why="a well-formed document, listed as one the derivation must refuse",
        document={
            "lanes": [{"lane_id": "L0", "roi_source": "caller"}],
            "bands": [],
            "normalization": {"ratios": []},
            "image_qc_flags": [],
        },
    )
    with pytest.raises(AssertionError, match="does not raise"):
        _write_error_fixture(tmp_path, good)
    assert not list(tmp_path.glob("*.json"))


def test_the_case_set_covers_every_ruled_class_reason_and_edge_case() -> None:
    """The fixtures cover what §5 of the pre-registration requires a test to cover.

    Asserted over the case set rather than over the emitted files so that the obligation is
    stated in one place: a case silently dropped from the generator fails here, where the
    reason it existed is written down, rather than only in the TypeScript count.
    """
    derived = [
        verdict for case in _synthetic_cases() for verdict in lane_verdicts(case.document)
    ]
    assert {verdict.verdict for verdict in derived} == {"pass", "flagged", "blocked"}
    assert {"all_ratios_excluded", "no_ratio_emitted"} <= {
        verdict.blocked_reason for verdict in derived
    }

    names = {case.name for case in _synthetic_cases()} | {case.name for case in _error_cases()}
    for edge_case in ("e1", "e2", "e3", "e4", "e5", "e6", "e7", "e8"):
        assert any(name.startswith(edge_case) for name in names), edge_case
