#!/usr/bin/env python3
"""Mechanical staleness check for the project's *claims* surface.

Why this exists, in one paragraph. Phase 4a ran eight review cycles. The code converged
after three; the record did not converge at all. Nine of the seventeen REQUIRED items the
capped cycles raised were wrong claims rather than wrong behaviour, and cycles 7 and 8
identified the mechanism precisely: **a claim is fixed where a reviewer reported it, and its
duplicates elsewhere survive.** A fresh-context reviewer cannot close that -- it reports what
it happened to read, and the same sentence lives in four documents. DEBT.md P1 carried two
standing rules against it and both failed, because a standing rule is a person promising to
remember. This is the mechanism instead. `evals.sweep --check` does the same job for measured
figures; nothing did it for prose until now.

Three checks, all of which name file and line and exit non-zero:

1. **Retracted phrases.** Wording that has been withdrawn or hedged, with its replacement.
   Any occurrence is an error.
2. **Numeric consistency.** Each named quantity is extracted everywhere it is asserted; if one
   quantity is asserted with two different values, every site is reported. The correct value is
   never hardcoded -- the check reports *disagreement*, so it stays true when a number
   legitimately changes and both sites are updated together.
3. **Arithmetic.** Sums written out in the prose (`a + b = c`, `n S + n E + n P = n`) are
   evaluated, and the register's stated composition is checked against the entries that
   actually exist.

Stdlib only, by design: this runs in CI beside `ruff` and `pytest`, and a claims checker that
needed its own dependency tree would be the first thing dropped from a slow build.

Exit code 0 when every check passes, 1 on any hit, 2 if a scanned file is unreadable.
"""

from __future__ import annotations

import csv
import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SCANNED = (
    "NOTES.md",
    "DEBT.md",
    "README.md",
    "docs/pr/*.md",
    "docs/review/phase-4a/README.md",
    "data/real/README.md",
    "data/real/AMENDMENT_*.md",
    "tools/gate2/README.md",
)
"""Files whose claims are checked. Entries containing ``*`` are globs.

``docs/pr/*.md`` holds the PR bodies. They are **committed artefacts**, and they are committed
precisely so that their claims are checked: the PR body is where several of Phase 4a's stale
claims lived, and while it sat outside the tree at ``.git/PHASE4A_PR_BODY.md`` it was scanned
locally and invisible in CI. The glob rather than a per-phase literal so a later phase's PR
body is covered by adding the file, not by editing this list.

A missing file is skipped rather than an error, so this list may name a path that does not
exist yet. That is safe only because a quantity matching *no* site anywhere is itself a
failure -- see the blindness guard in :func:`check_numeric` -- which is what caught this
file's own misplacement on its first CI run.
"""

VERBATIM_GLOB = "docs/review/phase-4a/cycle-*.md"
"""Files scanned for numbers but **exempt from the retracted-phrase check**.

These are byte-for-byte extracts of reviewer verdicts from the session logs. They contain
retracted wording *because that is what the reviewers wrote*, and editing them to satisfy a
checker would destroy the only thing they are for. The exemption is narrow and deliberate:
they are still read by the numeric checks, where they are a source of truth rather than a
claim. ``docs/review/phase-4a/README.md`` is a claim about them and is **not** exempt.
"""

ALLOW_MARKER = "claims-check: allow-retracted"
"""Marker permitting a deliberate quotation of retracted wording.

Put ``<!-- claims-check: allow-retracted -->`` on the offending line or the line above it. It
exists so that a document can say "the earlier wording was X and it was too strong" without
the checker treating the quotation as a live claim. Requiring an explicit marker keeps that a
deliberate act rather than a silent exemption.
"""


@dataclass(frozen=True)
class Retracted:
    """A phrase that must no longer appear, and what to say instead."""

    phrase: str
    replacement: str
    why: str


RETRACTED: tuple[Retracted, ...] = (
    Retracted(
        "no behaviour defect at all",
        "raised no new behaviour defect requiring a code change in this phase",
        "cycle 4's item 3 did surface a behavioural issue (the silent result overwrite); it is "
        "recorded as DEBT E10 item 3 and deferred to Phase 4b, so the absolute form is false",
    ),
    Retracted(
        "two consecutive cycles with no behaviour defect",
        "two consecutive cycles raising no item that required a code change",
        "same reason: the property that justifies the cap extension is 'no item required a code "
        "change', not 'nothing behavioural was seen'",
    ),
    Retracted(
        "found only claim defects and no behaviour defects",
        "raised no item requiring a code change",
        "same reason; this form survived in the PR body after the others were hedged",
    ),
    Retracted(
        "found no behaviour defect",
        "raised no new behaviour defect requiring a code change",
        "same reason; catches the shortest form of the retracted premise",
    ),
    Retracted(
        "zero behaviour defects",
        "no new behaviour defect requiring a code change",
        "same reason; catches the 'zero' phrasing",
    ),
    Retracted(
        "there is no `api/` module",
        "there is no HTTP API on `main`; it is built and staged on `phase-4a-api`",
        "the api/ package exists on this branch, so the unqualified form contradicts the same "
        "README's status line",
    ),
    Retracted(
        "no `POST /analyze`",
        "`POST /analyze` is built and staged on `phase-4a-api`, unmerged",
        "same reason",
    ),
    Retracted(
        "none of them was a figure",
        "several were figures; the widening rests on the record being the dominant defect surface",
        "withdrawn in DEBT P1 after cycles 4-6 found figure defects",
    ),
    Retracted(
        "none involving a figure",
        "several were figures; the widening rests on the record being the dominant defect surface",
        "same reason; this is the PR body's form of it",
    ),
    Retracted(
        "recovery of the 8 is untested end to end",
        "verified live on 2026-08-18: 21 of 21 recovered and matched",
        "the live run happened and succeeded; the untested caveat is retired",
    ),
    Retracted(
        "a second machine",
        "this same machine under concurrent load",
        "the 33.28 s reading came from this machine while busy, not a second machine",
    ),
)


@dataclass(frozen=True)
class Quantity:
    """One quantity, and every written form that asserts it."""

    name: str
    patterns: tuple[str, ...]
    note: str = ""
    _compiled: tuple[re.Pattern[str], ...] = field(default=(), compare=False, repr=False)

    def compiled(self) -> tuple[re.Pattern[str], ...]:
        """Return the patterns compiled case-insensitively."""
        return tuple(re.compile(p, re.I) for p in self.patterns)


QUANTITIES: tuple[Quantity, ...] = (
    Quantity(
        "test count on this branch",
        (
            r"\*\*(\d{3}) passed\*\*",
            r"\b(\d{3}) passed\b",
            r"\bthe (\d{3}) tests\b",
            r"(\d{3}) on the unmerged",
        ),
        "pytest on phase-4a-api",
    ),
    Quantity(
        "test count on main",
        (
            r"\*\*(\d{3}) tests\*\* on `main`",
            r"up from (\d{3}) on `main`",
            r"`main` measured at \*{0,2}(\d{3})",
            r"(\d{3}) on a `main` worktree",
        ),
        "pytest on main",
    ),
    Quantity(
        "REQUIRED items across cycles 1-5",
        (
            r"(\d+) REQUIRED items (?:the five capped cycles |across cycles 1-5 )?raised",
            r"of the (\d+) REQUIRED items",
            r"\*\*(\d+)\*\* across the five that fell within",
            r"(\d+) across the five that fell within",
            r"Nine of the (\d+) REQUIRED",
        ),
        "the capped cycles only; cycle 6+ are counted separately",
    ),
    Quantity(
        "numbered P2 entries",
        (
            r"\*\*(\w+) numbered entries below record",
            r"six → \*\*(\w+) numbered entries\*\*",
            r"goes six → \*\*(\w+) numbered entries\*\*",
        ),
        "entries, not deviations",
    ),
    Quantity(
        "deviations recorded in P2",
        (
            r"numbered entries below record (\w+) deviations",
            r"recording six → (\w+) deviations",
        ),
        "deviations, not entries",
    ),
    Quantity(
        "committed Gate 2 source figures",
        (
            r"\*\*(\d+) of the 21 source figures are committed",
            r"source figures a crop derives from \| `images/` \| (\d+) \|",
            r"only the (\d+) figures a measured crop derives from",
            r"only the (\d+) that matter for measurement",
        ),
        "a source is committed iff a measured artefact derives from it",
    ),
    Quantity(
        "Gate 2 crops",
        (
            r"cropped blot panels[^|]*\| `crops/` \| (\d+) \|",
            r"per-crop sha256[^|]*\| (\d+) rows \|",
            r"\bThe (\d+) crops are committed\b",
            r"colour fraction measured on all (\d+) crops",
        ),
        "",
    ),
    Quantity(
        "deviations this phase contributed to P2",
        (
            r"this phase contributed (\w+) deviations",
            r"contributed \*\*(\w+) deviations\*\*",
        ),
        "",
    ),
)

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "twentyone": 21,
}


def as_number(token: str) -> int | None:
    """Return ``token`` as an integer, accepting digits or an English number word."""
    token = token.strip().lower().replace(",", "")
    if token.isdigit():
        return int(token)
    return WORD_NUMBERS.get(token)


SUM_PATTERNS = (
    re.compile(r"(\d+)\s*\+\s*(\d+)\s*=\s*(\d+)"),
    re.compile(r"(\d+)\s*S\s*\+\s*(\d+)\s*E\s*\+\s*(\d+)\s*P\s*=\s*(\d+)"),
)
"""Sums written out in prose. The second form is the register's own composition."""


@dataclass
class Hit:
    """One problem, located."""

    path: str
    line: int
    check: str
    message: str


def _read(path: Path) -> list[str] | None:
    """Return ``path``'s lines, or ``None`` if it does not exist."""
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").splitlines()


def _targets() -> list[tuple[str, list[str], bool]]:
    """Return ``(display path, lines, is_verbatim_extract)`` for every scanned file."""
    out: list[tuple[str, list[str], bool]] = []
    for entry in SCANNED:
        paths = sorted(REPO_ROOT.glob(entry)) if "*" in entry else [REPO_ROOT / entry]
        for path in paths:
            lines = _read(path)
            if lines is not None:
                out.append((str(path.relative_to(REPO_ROOT)), lines, False))
    for path in sorted(REPO_ROOT.glob(VERBATIM_GLOB)):
        lines = _read(path)
        if lines is not None:
            out.append((str(path.relative_to(REPO_ROOT)), lines, True))
    return out


def check_retracted(targets: list[tuple[str, list[str], bool]]) -> list[Hit]:
    """Report every occurrence of a withdrawn phrase outside a verbatim extract."""
    hits: list[Hit] = []
    for rel, lines, verbatim in targets:
        if verbatim:
            continue
        for number, line in enumerate(lines, 1):
            lowered = line.lower()
            previous = lines[number - 2] if number >= 2 else ""
            if ALLOW_MARKER in line or ALLOW_MARKER in previous:
                continue
            for item in RETRACTED:
                if item.phrase.lower() in lowered:
                    hits.append(
                        Hit(
                            rel,
                            number,
                            "retracted-phrase",
                            f"{item.phrase!r} was withdrawn -- say {item.replacement!r} instead. "
                            f"Why: {item.why}. If this is a deliberate quotation of the old "
                            f"wording, mark the line with <!-- {ALLOW_MARKER} -->",
                        )
                    )
    return hits


def check_numeric(targets: list[tuple[str, list[str], bool]]) -> list[Hit]:
    """Report any quantity asserted with two different values, naming every site."""
    hits: list[Hit] = []
    for quantity in QUANTITIES:
        patterns = quantity.compiled()
        found: list[tuple[str, int, int, str]] = []
        for rel, lines, _ in targets:
            for number, line in enumerate(lines, 1):
                for pattern in patterns:
                    for match in pattern.finditer(line):
                        value = as_number(match.group(1))
                        if value is not None:
                            found.append((rel, number, value, match.group(0).strip()))
        values = {value for _, _, value, _ in found}
        if not found:
            # A quantity that matches nothing has gone blind: the wording it keys on was
            # reworded and the check now passes vacuously, which is exactly how a stale claim
            # survives. tests/test_recorded_figures.py::MINIMUM_MARKED_BLOCKS guards the same
            # hazard for sweep figures. Fail loudly and make someone re-point the pattern.
            hits.append(
                Hit(
                    "tools/check_claims.py",
                    0,
                    "numeric-consistency",
                    f"the quantity {quantity.name!r} matched no site in any scanned file, so it "
                    f"is no longer checking anything. Either the claim was removed -- delete the "
                    f"quantity -- or it was reworded and the patterns need re-pointing",
                )
            )
            continue
        if len(values) > 1:
            for rel, number, value, text in sorted(found):
                hits.append(
                    Hit(
                        rel,
                        number,
                        "numeric-consistency",
                        f"{quantity.name!r} is asserted as {value} here ({text!r}) but as "
                        f"{sorted(values - {value})} elsewhere. One of these sites is stale; the "
                        f"check reports the disagreement and does not know which is right"
                        + (f". Note: {quantity.note}" if quantity.note else ""),
                    )
                )
    return hits


def check_arithmetic(targets: list[tuple[str, list[str], bool]]) -> list[Hit]:
    """Evaluate sums written out in prose, and the register's stated composition."""
    hits: list[Hit] = []
    for rel, lines, verbatim in targets:
        if verbatim:
            continue
        for number, line in enumerate(lines, 1):
            for match in SUM_PATTERNS[0].finditer(line):
                a, b, total = (int(g) for g in match.groups())
                if a + b != total:
                    hits.append(
                        Hit(rel, number, "arithmetic",
                            f"{match.group(0)!r} is wrong: {a} + {b} = {a + b}")
                    )
            for match in SUM_PATTERNS[1].finditer(line):
                s, e, p, total = (int(g) for g in match.groups())
                if s + e + p != total:
                    hits.append(
                        Hit(rel, number, "arithmetic",
                            f"{match.group(0)!r} is wrong: {s} + {e} + {p} = {s + e + p}")
                    )
    hits.extend(_check_register_composition())
    return hits


ENTRY_HEADING = re.compile(r"^### ([SEP])(\d+) — ", re.M)
STATUS_LINE = re.compile(r"^\*\*Status\.\*\* (\w+)", re.M)
COMPOSITION = re.compile(
    r"\*\*(\w+) of the (\d+) entries are `Accepted` or `Permanent`; (\d+) are\s*\n`Open`\.\*\*"
)


def _check_register_composition() -> list[Hit]:
    """Check DEBT.md's stated entry tally against the entries and statuses it actually has."""
    path = REPO_ROOT / "DEBT.md"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    match = COMPOSITION.search(text)
    if match is None:
        return [Hit("DEBT.md", 0, "arithmetic",
                    "the register's composition sentence ('N of the M entries are Accepted or "
                    "Permanent; K are Open') was not found; if it was reworded, update "
                    "tools/check_claims.py::COMPOSITION so the tally stays checked")]
    stated_settled = as_number(match.group(1))
    stated_total = int(match.group(2))
    stated_open = int(match.group(3))
    entries = ENTRY_HEADING.findall(text)
    statuses = STATUS_LINE.findall(text)
    actual_total = len(entries)
    actual_open = sum(1 for s in statuses if s.lower() == "open")
    actual_settled = actual_total - actual_open
    line = text[: match.start()].count("\n") + 1
    hits: list[Hit] = []
    if actual_total != stated_total:
        hits.append(Hit("DEBT.md", line, "arithmetic",
                        f"the register says {stated_total} entries; {actual_total} '### X<n> — ' "
                        f"headings exist"))
    if actual_open != stated_open:
        hits.append(Hit("DEBT.md", line, "arithmetic",
                        f"the register says {stated_open} Open; {actual_open} '**Status.** Open' "
                        f"lines exist"))
    if stated_settled is not None and actual_settled != stated_settled:
        hits.append(Hit("DEBT.md", line, "arithmetic",
                        f"the register says {stated_settled} Accepted-or-Permanent; "
                        f"{actual_total} entries minus {actual_open} Open is {actual_settled}"))
    return hits


CROP_LOG = Path("data/real/crops/crop_log.csv")
"""The Gate 2 crop manifest: one row per measured artefact, with its parent's digest."""

DECISION_PATH = Path("data/real/DECISION_unit_of_analysis.md")
DECISION_SHA256 = "994acc30daf82011acfe957adff2866f824b05aa3b39bade118e539f4cfd88d0"
"""The frozen Gate 2 pre-registration and the digest cited for it.

Pinned here so that editing the pre-registration fails the build. It was frozen before any
measurement and its hash is quoted in ``data/real/README.md`` and NOTES.md; a pre-registration
that can be revised after the fact is not one.
"""


def check_gate2_integrity() -> list[Hit]:
    """Verify the Gate 2 artefacts byte-for-byte against their recorded digests.

    This is the half of the claims surface that is not prose. Three things are checked, and the
    reason is recorded in ``tools/gate2/README.md``: cropping was done by hand in Preview, whose
    Cmd+S writes back to the *open* file, and twice during Gate 2 that silently re-encoded a
    source figure. The pixels look identical; only the digest chain notices. So:

    * every crop in ``crop_log.csv`` exists and hashes to its recorded ``crop_sha256`` -- a
      corrupted or re-saved crop must fail CI rather than be measured;
    * every parent named by a crop, when it is committed, hashes to the recorded
      ``parent_sha256``, which is the link that caught the overwrite both times;
    * the pre-registration still hashes to the value cited for it.

    A missing crop file is an error, not a skip. A parent that is *not* committed is skipped
    with no complaint -- only the 13 parents a crop derives from are in the tree, by the rule in
    ``data/real/README.md``, and ``sources.csv`` records the rest.
    """
    hits: list[Hit] = []
    decision = REPO_ROOT / DECISION_PATH
    if not decision.is_file():
        hits.append(Hit(str(DECISION_PATH), 0, "gate2-integrity",
                        "the frozen Gate 2 pre-registration is missing"))
    else:
        digest = hashlib.sha256(decision.read_bytes()).hexdigest()
        if digest != DECISION_SHA256:
            hits.append(Hit(str(DECISION_PATH), 0, "gate2-integrity",
                            f"the pre-registration hashes {digest} but is cited as "
                            f"{DECISION_SHA256}. It was frozen before any measurement; if it "
                            f"genuinely must change, that is an amendment with its own section "
                            f"and a new digest recorded everywhere the old one is quoted"))
    log = REPO_ROOT / CROP_LOG
    if not log.is_file():
        hits.append(Hit(str(CROP_LOG), 0, "gate2-integrity", "the crop manifest is missing"))
        return hits
    rows = list(csv.DictReader(log.open(encoding="utf-8")))
    if not rows:
        hits.append(Hit(str(CROP_LOG), 0, "gate2-integrity",
                        "the crop manifest has no rows, so this check would pass vacuously"))
        return hits
    for number, row in enumerate(rows, start=2):
        crop = REPO_ROOT / CROP_LOG.parent / row["crop"]
        if not crop.is_file():
            hits.append(Hit(str(CROP_LOG), number, "gate2-integrity",
                            f"{row['crop']} is recorded but not on disk; the measured artefact "
                            f"is missing"))
            continue
        digest = hashlib.sha256(crop.read_bytes()).hexdigest()
        if digest != row["crop_sha256"]:
            hits.append(Hit(str(CROP_LOG), number, "gate2-integrity",
                            f"{row['crop']} hashes {digest[:16]}… but is recorded as "
                            f"{row['crop_sha256'][:16]}…. It has been re-saved or corrupted "
                            f"since Gate 2 closed; measuring it would measure a different "
                            f"artefact from the one the record approves"))
    parents = {(r["parent"], r["parent_sha256"]) for r in rows}
    for name, recorded in sorted(parents):
        parent = REPO_ROOT / "data" / "real" / "images" / name
        if not parent.is_file():
            continue
        digest = hashlib.sha256(parent.read_bytes()).hexdigest()
        if digest != recorded:
            hits.append(Hit(f"data/real/images/{name}", 0, "gate2-integrity",
                            f"hashes {digest[:16]}… but crop_log.csv records {recorded[:16]}…. "
                            f"This is the exact failure the parent-sha256 column exists to "
                            f"catch: a source figure re-encoded after its crops were taken"))
    return hits


AMENDMENT_PATH = "data/real/AMENDMENT_2026-08-19_delta_and_power.md"
"""The one amendment :data:`QUOTED_FIGURES` describes.

Named exactly rather than globbed. Every entry in ``QUOTED_FIGURES`` is a figure *this*
amendment quotes, so applying them to any file matching ``AMENDMENT_*.md`` would demand every
one of them from an unrelated later amendment and turn CI red the day one is written. A second
amendment brings its own pins; it does not inherit these. No count of the entries is restated
here: a number in this docstring would be one more thing to keep in step with the tuple below,
which is the failure this whole module exists to catch.
"""

RS_POWER_EXPECTED = "tools/stats/rs_power_expected.txt"
"""The committed output of ``python -m tools.stats.rs_power``, pinned by diff in CI."""


@dataclass(frozen=True)
class QuotedFigure:
    """One figure the amendment quotes, located in the reference by section and regex.

    ``pattern`` must pin the value to its **column**, not merely to its row. Matching a value
    anywhere in the located line is not enough: table E's ``  30 |`` row carries three quoted
    figures, so a row-level substring test passes when two of them are transposed -- and
    transposing the true-0.70 and true-0.80 cells inverts the amendment's central safety
    claim. Each pattern below therefore counts the fields before its value, or anchors on the
    label that names it.
    """

    in_amendment: str
    section: str
    row: str
    pattern: str
    what: str
    region: str = "rulings"


def _e_row(column: int, value: str) -> str:
    """Return a pattern pinning ``value`` to the ``column``-th value of table E's N = 30 row.

    ``column`` is 1-based over the true-rho columns: 1 = true 0.60 … 6 = true 0.95.
    """
    skipped = r"(?:\s+\S+)" * (column - 1)
    return r"^\s*30 \|" + skipped + r"\s+" + re.escape(value) + r"(?:\s|$)"


QUOTED_FIGURES: tuple[QuotedFigure, ...] = (
    QuotedFigure(
        "N ≥ 18", "=== B.", r"observed r_s = 0\.90", r"observed r_s = 0\.90\s+->\s+N >= 18$",
        "0.90 vs 0.70",
    ),
    QuotedFigure(
        "N ≥ 38", "=== B.", r"observed r_s = 0\.85", r"observed r_s = 0\.85\s+->\s+N >= 38$",
        "0.85 vs 0.70",
    ),
    QuotedFigure(
        "≈ 27 of nominal 30", "=== D.", r"10 blots x 3, ICC 0\.5",
        r"10 blots x 3, ICC 0\.5\s+30\s+\S+\s+26\.8\s", "N_eff, ICC 0.5",
    ),
    QuotedFigure(
        "at ICC 0.7, ≈ 20", "=== D.", r"10 blots x 3, ICC 0\.7",
        r"10 blots x 3, ICC 0\.7\s+30\s+\S+\s+19\.8\s", "N_eff, ICC 0.7",
    ),
    QuotedFigure(
        "~0.3% at true 0.70", "=== E.", r"^\s*30 \|", _e_row(2, "0.003"), "declares, true 0.70"
    ),
    QuotedFigure(
        "~3.5% at true 0.80", "=== E.", r"^\s*30 \|", _e_row(3, "0.035"), "declares, true 0.80"
    ),
    QuotedFigure(
        "~42% of the time (0.417)", "=== E.", r"^\s*30 \|", _e_row(5, "0.417"),
        "declares, true 0.90",
    ),
    QuotedFigure(
        "mean observed r_s 0.884", "=== C.", r"^\s*N= 30\s", r"N= 30\s+mean r_s 0\.884\s",
        "mean r_s at N=30",
    ),
    QuotedFigure(
        "0.59 vs analytic 0.45", "=== C.", r"^\s*N= 10\s", r"N= 10.*sd\(z\) 0\.592\s",
        "observed sd(z), N=10",
    ),
    QuotedFigure(
        "sd(z) observed 0.59 vs analytic 0.45 at N = 10", "=== C.", r"^\s*N= 10\s",
        r"N= 10.*\(analytic 0\.448\)", "analytic sd(z), N=10",
    ),
    QuotedFigure(
        "effective N ≈ 16", "=== D.", r"6 blots x 3, ICC 0\.5",
        r"6 blots x 3, ICC 0\.5\s+18\s+\S+\s+15\.9\s", "headline 2, N_eff 6 blots",
    ),
    # --- figures the implementer's note records, one table row each ---
    QuotedFigure(
        "`0.417`, so ~42%", "=== E.", r"^\s*30 \|", _e_row(5, "0.417"),
        "note (A), corrected declaration rate", region="note",
    ),
    QuotedFigure(
        "`0.884`, and it is a **mean**", "=== C.", r"^\s*N= 30\s",
        r"N= 30\s+mean r_s 0\.884\s", "note (A), corrected central tendency", region="note",
    ),
    QuotedFigure(
        "`0.035`, so ~3.5%", "=== E.", r"^\s*30 \|", _e_row(3, "0.035"),
        "note (A), corrected false-claim rate", region="note",
    ),
    QuotedFigure(
        "row `15`, column `lower` | `0.665`", "=== A.", r"^\s*15\s", r"^\s*15\s+0\.665\s",
        "note table, CI lower bound at the floor", region="note",
    ),
    QuotedFigure(
        "row `observed r_s = 0.90` | `N >= 18`", "=== B.", r"observed r_s = 0\.90",
        r"observed r_s = 0\.90\s+->\s+N >= 18$", "note table, N needed at agreement",
        region="note",
    ),
    QuotedFigure(
        "row `N= 10`, column `sd(z)` | `0.592`", "=== C.", r"^\s*N= 10\s",
        r"N= 10.*sd\(z\) 0\.592\s", "note table, observed sd(z) at smallest N", region="note",
    ),
    QuotedFigure(
        "row `N= 10`, column `(analytic)` | `0.448`", "=== C.", r"^\s*N= 10\s",
        r"N= 10.*\(analytic 0\.448\)", "note table, analytic sd(z) at smallest N",
        region="note",
    ),
    QuotedFigure(
        "row `N= 15`, column `sd(z)` | `0.321`", "=== C.", r"^\s*N= 15\s",
        r"N= 15.*sd\(z\) 0\.321\s", "note table, observed sd(z) at next N", region="note",
    ),
    QuotedFigure(
        "row `N= 15`, column `(analytic)` | `0.342`", "=== C.", r"^\s*N= 15\s",
        r"N= 15.*\(analytic 0\.342\)", "note table, analytic sd(z) at next N", region="note",
    ),
    QuotedFigure(
        "ICC 0.0`, column `N_eff` | `34.0`", "=== D.", r"10 blots x 3, ICC 0\.0",
        r"10 blots x 3, ICC 0\.0\s+30\s+\S+\s+34\.0\s", "note table, ICC-zero control",
        region="note",
    ),
    QuotedFigure(
        "ICC 0.5`, column `N_eff` | `26.8`", "=== D.", r"10 blots x 3, ICC 0\.5",
        r"10 blots x 3, ICC 0\.5\s+30\s+\S+\s+26\.8\s", "note table, N_eff at middle ICC",
        region="note",
    ),
    QuotedFigure(
        "`6 blots x 3, ICC 0.5`, column `N_eff` | `15.9`", "=== D.",
        r"6 blots x 3, ICC 0\.5", r"6 blots x 3, ICC 0\.5\s+18\s+\S+\s+15\.9\s",
        "note table, N_eff few blots", region="note",
    ),
    QuotedFigure(
        "`8 blots x 4, ICC 0.5`, column `N_eff` | `26.0`", "=== D.",
        r"8 blots x 4, ICC 0\.5", r"8 blots x 4, ICC 0\.5\s+32\s+\S+\s+26\.0\s",
        "note table, N_eff more ratios fewer blots", region="note",
    ),
    QuotedFigure(
        "`12 blots x 3, ICC 0.5`, column `N_eff` | `31.5`", "=== D.",
        r"12 blots x 3, ICC 0\.5", r"12 blots x 3, ICC 0\.5\s+36\s+\S+\s+31\.5\s",
        "note table, N_eff more blots", region="note",
    ),
)
"""Every figure the amendment reads out of a cell of the reference output.

**What this check does.** Each entry names the half of the amendment it belongs to --
``region="rulings"`` or ``region="note"``, split at :data:`AMENDMENT_RULINGS_MARKER` -- and the
check requires (a) that half to contain the figure as written, **exactly once**, and (b) the
reference to carry the value in the row and column it was read from. Both halves are covered:
the note quotes figures of its own, and leaving them unpinned was itself a review finding.

The exactly-once rules, on both sides, are load-bearing rather than tidiness. A second copy of
a figure in the same half lets an edited one hide behind an unedited one. A duplicated row in
the reference does the same, which is why row identity (:attr:`QuotedFigure.row`) and cell
value (:attr:`QuotedFigure.pattern`) are separate patterns: counting only the rows that still
carry the value cannot see a duplicate, because corrupting one copy leaves the other matching
exactly once.

**What it does not do.** It does not verify the rounding of a pinned value into the prose that
reports it -- whether an effective N was fairly reported as an approximation was a human
judgement made once against the output. It does not cover the arithmetic the amendment *derives*
rather than reads: the retention percentages, the ratio of clustered to unclustered effective N,
and the comparison between the N an observed correlation needs and the stop rule's floor are
review findings, not build failures. No figure is restated in this docstring, for the reason the
module docstring gives. It does not cover figures quoted from anywhere other than this reference,
and it cannot tell whether a figure is paired with the *right* row -- a crossed pairing passes,
and one was found by test rather than by this check. Claims about this check elsewhere in the
repository must not say more than this docstring does.
"""

AMENDMENT_NOTE_START = "<!-- implementer-note-start -->"
AMENDMENT_RULINGS_MARKER = "<!-- implementer-note-end -->"
"""The implementer's note is delimited by these two markers.

Two reasons for delimiting it rather than splitting the file at one point. First, the note
records every figure it verified, and searching the whole file for a rulings figure would let
the note's copy satisfy the presence check while the ruling itself had been edited -- the
figure would be "present" in a sentence that only says it was once checked. Second, the
digits rule below applies to the note and to nothing else: the author's preamble above it is
not the implementer's to reformat.
"""

DIGIT = re.compile(r"\d")
"""Any digit. The note may not contain one outside the exemptions below."""

NOTE_DIGIT_EXEMPT_LINES = (
    re.compile(r"^\s*\|"),
    re.compile(r"^\s*>"),
)
"""Whole lines the digits rule does not apply to: table rows, and quoted blocks.

A table row is where figures are *required* to live. A quoted block is another document's
words -- §5's own text, NOTES.md's own text -- and paraphrasing a frozen document to avoid a
digit would damage the thing being quoted to satisfy a rule about the thing quoting it. Both
are structurally obvious to a reader, which is what makes them safe: a figure hidden in prose
looks like an assertion, and a figure in a table row or a blockquote does not.
"""

NOTE_DIGIT_EXEMPT_PATTERNS = (
    re.compile(r"§\d+[a-z]?(\([a-z]\))?"),
    re.compile(r"\d{4}-\d{2}-\d{2}"),
    re.compile(r"\b[0-9a-f]{64}\b"),
    re.compile(r"\bsha256\b"),
    re.compile(r"\b(Gate|Phase|Ruling|ruling)\s\d[a-z]?(-\d)?\b"),
    re.compile(r"\bW\d\b"),
    re.compile(r"\bR\d\b"),
)
"""Digit-bearing spans the note may contain, each declared and matched by pattern.

In order: a section mark (§5, §8(c), §11a); an ISO date; a sha256 digest and the word itself; a
gate, phase or ruling reference (Gate 1, Phase 3b-0, Ruling 2); a workpackage name (W0); a
human-gate ruling id (R3, as numbered in NOTES.md's "Phase 3b-0 rulings"). Nothing else. These
are removed from a line before it is tested, so a digit that survives is a figure sitting in
prose.

Every pattern here must be exercised by the note as written. Two earlier entries -- a
debt-register id (``S6``, ``E10``) and a JSON Schema draft name -- were removed on review
because the note contains neither, while ``\b[SEP]\d{1,2}\b`` would have exempted any small
figure written ``E34``. An exemption that exempts nothing real and something unreal is a
loophole, not a narrow exception; add one only with a line in the note that needs it.
"""


def _section_rows(reference: list[str], figure: QuotedFigure) -> list[str]:
    """Return the rows of the figure's section that identify its row.

    Row identity and value are separate patterns on purpose. Counting only the rows that
    still carry the *value* cannot see a duplicated row: corrupt one copy and the intact one
    matches exactly once, which reads as healthy. Identifying the row independently makes the
    duplicate itself the failure, whatever the corrupted copy now says.
    """
    rows = []
    in_section = False
    for line in reference:
        if line.startswith("==="):
            in_section = line.startswith(figure.section)
            continue
        if in_section and re.search(figure.row, line):
            rows.append(line)
    return rows


def _note_region(text: str) -> tuple[str, str] | None:
    """Return (note, rulings) for an amendment, or None if the markers are not both present."""
    if AMENDMENT_NOTE_START not in text or AMENDMENT_RULINGS_MARKER not in text:
        return None
    after_start = text.split(AMENDMENT_NOTE_START, 1)[1]
    if AMENDMENT_RULINGS_MARKER not in after_start:
        return None
    note, rulings = after_start.split(AMENDMENT_RULINGS_MARKER, 1)
    return note, rulings


def check_note_has_no_figures_in_prose(path: str, note: str, first_line: int) -> list[Hit]:
    """Fail on any digit in the implementer's note outside a table, a quote or an exemption.

    The rule the human ratified after three review cycles. Each cycle repaired figure-staleness
    at the sites a reviewer named, and each time an unpinned copy of the same figure survived
    somewhere else in the same note -- because prose is where a figure can sit without anyone
    thinking to pin it. Confining figures to table rows **narrows the search space; it does not
    pin anything** -- pinning is :func:`check_amendment_figures`' job, and a table row with no
    ``QuotedFigure`` entry is as unchecked as prose ever was.

    The exemptions are deliberately not "numbers that look like references": they are patterns,
    declared above, and anything they do not match fails. A new kind of reference is a
    deliberate addition here, not a judgement call at the keyboard.

    **Known ways past this check**, found by adversarial review and recorded rather than
    silently tolerated. A figure spelled out in words (``short by three``); a prose line that
    begins with ``|`` or ``>``, since both exemptions are whole-line; text trailing a genuine
    table row on the same line; a figure written with Unicode superscript digits, which ``\d``
    does not match; a section mark of any length, since ``§\d+`` accepts ``§18``; and prose
    placed after the end marker, which leaves the note region altogether. Each is a hole in a
    floor, not in a ceiling: the rule makes the common accident loud, and none of these is an
    accident.
    """
    hits: list[Hit] = []
    for number, line in enumerate(note.splitlines(), start=first_line):
        if any(pattern.search(line) for pattern in NOTE_DIGIT_EXEMPT_LINES):
            continue
        residue = line
        for pattern in NOTE_DIGIT_EXEMPT_PATTERNS:
            residue = pattern.sub("", residue)
        if DIGIT.search(residue):
            hits.append(
                Hit(
                    path,
                    number,
                    "amendment-note-digits",
                    f"a digit appears in the implementer's note outside a table row, a quoted "
                    f"block, or a declared exemption: {residue.strip()!r}. Figures in this "
                    f"note live in tables; prose references them by tag. Move the figure into "
                    f"a table and reference its tag, or -- if this is a new kind of reference "
                    f"rather than a figure -- add a pattern to NOTE_DIGIT_EXEMPT_PATTERNS.",
                )
            )
    return hits


NOTE_QUOTATION_SOURCES = ("data/real/DECISION_unit_of_analysis.md", "NOTES.md")
"""Documents the implementer's note quotes, besides the amendment's own rulings.

The note's blockquotes are exempt from the digits rule, because paraphrasing a frozen document
to avoid a digit would damage the quotation to satisfy a rule about the text quoting it. That
exemption is only safe if a blockquote really is a quotation, which is what the check below
establishes: an unverified exemption is a hole shaped like a rule.
"""

ELISION = "…"
"""Marks omitted words inside a quotation. Each side is required to appear, in order."""


def _blockquotes(note: str) -> list[tuple[int, str]]:
    """Return (line number within the note, joined text) for each blockquote block."""
    blocks: list[tuple[int, str]] = []
    current: list[str] = []
    start = 0
    for number, line in enumerate(note.splitlines(), start=1):
        if line.lstrip().startswith(">"):
            if not current:
                start = number
            current.append(line.lstrip()[1:].strip())
        elif current:
            blocks.append((start, " ".join(current)))
            current = []
    if current:
        blocks.append((start, " ".join(current)))
    return blocks


def _contains_in_order(source: str, fragments: list[str]) -> bool:
    """Return True when every fragment appears in ``source``, in the order given."""
    cursor = 0
    for fragment in fragments:
        found = source.find(fragment, cursor)
        if found < 0:
            return False
        cursor = found + len(fragment)
    return True


def check_note_quotations_are_verbatim(
    path: str, note: str, rulings: str, first_line: int
) -> list[Hit]:
    """Fail unless every blockquote in the implementer's note is verbatim in a cited source.

    The same discipline ``tools/phase3/run_real.py`` applies to the three pre-registration
    passages it prints. A quotation that has drifted from its source is worse than a paraphrase,
    because it still looks like evidence -- and these particular quotations carry the note's
    conflict findings, which are the part the human gate acts on.

    Whitespace is normalised on both sides, because both wrap; an elision marker splits the
    quotation into fragments that must appear in one source in order. Nothing else is
    normalised, so a changed word, digit or emphasis marker fails.
    """
    sources = {name: (REPO_ROOT / name).read_text() for name in NOTE_QUOTATION_SOURCES}
    sources["the amendment's own rulings"] = rulings
    normalised = {name: " ".join(text.split()) for name, text in sources.items()}

    hits: list[Hit] = []
    for number, quote in _blockquotes(note):
        fragments = [" ".join(part.split()) for part in quote.split(ELISION)]
        fragments = [fragment for fragment in fragments if fragment]
        if any(_contains_in_order(text, fragments) for text in normalised.values()):
            continue
        hits.append(
            Hit(
                path,
                first_line + number - 1,
                "amendment-note-quotations",
                f"this blockquote is not verbatim in any of "
                f"{', '.join(normalised)}: {quote[:80]!r}... A blockquote is exempt from the "
                f"no-figures-in-prose rule because it is another document's words; if it is "
                f"not, it is prose wearing a quotation's exemption.",
            )
        )
    return hits


def check_amendment_figures() -> list[Hit]:
    """Check every figure the amendment quotes against the committed reference output."""
    # Resolved through REPO_ROOT, like every other check in this file. A CWD-relative glob
    # returns nothing from any other directory, and "nothing to check" would then print a
    # green OK it has not earned -- the vacuous pass _targets() and the crop manifest check
    # both refuse by name.
    expected = REPO_ROOT / RS_POWER_EXPECTED
    amendment = REPO_ROOT / AMENDMENT_PATH
    if not amendment.exists():
        return []
    amendments = [amendment]  # a list of one, so the loop below reads the same as before
    if not expected.exists():
        return [
            Hit(
                RS_POWER_EXPECTED,
                0,
                "amendment-figures",
                f"{RS_POWER_EXPECTED} is missing, but {AMENDMENT_PATH} quotes figures from "
                f"it. Regenerate it with "
                f"'python -m tools.stats.rs_power > {RS_POWER_EXPECTED}'.",
            )
        ]

    reference = expected.read_text().splitlines()
    hits: list[Hit] = []
    for amendment in amendments:
        whole = amendment.read_text()
        regions = _note_region(whole)
        if regions is None:
            hits.append(
                Hit(
                    str(amendment.relative_to(REPO_ROOT)),
                    0,
                    "amendment-figures",
                    f"'{AMENDMENT_NOTE_START}' and '{AMENDMENT_RULINGS_MARKER}' must both be "
                    f"present, in that order: without them the implementer's note cannot be "
                    f"separated from the rulings, a figure could hide behind the note's own "
                    f"copy of itself, and the note's no-figures-in-prose rule has no region "
                    f"to apply to. Restore the markers.",
                )
            )
            continue
        note, rulings = regions
        # The note's own line numbers are useless to a reader with the file open, so they are
        # offset by where the note starts. The start marker's own line is line one of `note`.
        first_line = whole[: whole.index(AMENDMENT_NOTE_START)].count("\n") + 1
        relative = str(amendment.relative_to(REPO_ROOT))
        hits += check_note_has_no_figures_in_prose(relative, note, first_line)
        hits += check_note_quotations_are_verbatim(relative, note, rulings, first_line)
        for figure in QUOTED_FIGURES:
            text = note if figure.region == "note" else rulings
            occurrences = text.count(figure.in_amendment)
            if occurrences > 1:
                hits.append(
                    Hit(
                        str(amendment.relative_to(REPO_ROOT)),
                        0,
                        "amendment-figures",
                        f"the {figure.what} figure '{figure.in_amendment}' appears "
                        f"{occurrences} times in the {figure.region}; this check can only "
                        f"pin a "
                        f"figure that appears once. Make the quoted string specific to one "
                        f"site, or drop the duplicate.",
                    )
                )
            if occurrences == 0:
                hits.append(
                    Hit(
                        str(amendment.relative_to(REPO_ROOT)),
                        0,
                        "amendment-figures",
                        f"the {figure.what} figure '{figure.in_amendment}' is no longer in "
                        f"the {figure.region} half of this file. Either it was edited "
                        f"without updating tools/check_claims.py, or it was dropped.",
                    )
                )
            rows = _section_rows(reference, figure)
            if len(rows) > 1:
                hits.append(
                    Hit(
                        RS_POWER_EXPECTED,
                        0,
                        "amendment-figures",
                        f"{len(rows)} rows under section '{figure.section}' match "
                        f"/{figure.row}/, the row the {figure.what} figure is read from. A "
                        f"duplicated row would let a corrupted value hide behind an intact "
                        f"copy of itself, so this is refused.",
                    )
                )
            elif not rows:
                hits.append(
                    Hit(
                        RS_POWER_EXPECTED,
                        0,
                        "amendment-figures",
                        f"no row matching /{figure.row}/ under section '{figure.section}', "
                        f"so the {figure.what} cell the amendment quotes "
                        f"'{figure.in_amendment}' from no longer exists.",
                    )
                )
            elif not re.search(figure.pattern, rows[0]):
                hits.append(
                    Hit(
                        RS_POWER_EXPECTED,
                        0,
                        "amendment-figures",
                        f"the {figure.what} row now reads '{rows[0].strip()}', which does not "
                        f"match /{figure.pattern}/ -- but the amendment still quotes "
                        f"'{figure.in_amendment}' from it. Either the value changed, or it "
                        f"moved to a different column. The amendment is now stale.",
                    )
                )
    return hits


def main() -> int:
    """Run every check and report. Returns 0 when clean, 1 on any hit."""
    targets = _targets()
    if not targets:
        print(
            "check_claims: no scanned file was found; refusing to pass vacuously",
            file=sys.stderr,
        )
        return 2
    hits = (
        check_retracted(targets)
        + check_numeric(targets)
        + check_arithmetic(targets)
        + check_gate2_integrity()
        + check_amendment_figures()
    )
    scanned = ", ".join(rel for rel, _, _ in targets)
    if not hits:
        print(f"OK: claims check passed over {len(targets)} file(s): {scanned}")
        return 0
    by_check: dict[str, list[Hit]] = {}
    for hit in hits:
        by_check.setdefault(hit.check, []).append(hit)
    for check, group in sorted(by_check.items()):
        print(f"\n{check} -- {len(group)} hit(s):", file=sys.stderr)
        for hit in group:
            print(f"  {hit.path}:{hit.line}: {hit.message}", file=sys.stderr)
    print(f"\ncheck_claims: {len(hits)} hit(s) across {len(by_check)} check(s)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
