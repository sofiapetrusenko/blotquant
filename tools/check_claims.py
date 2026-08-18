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
