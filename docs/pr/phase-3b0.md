## What was built

**Phase 3b-0** — the first time this project pointed the pipeline at an image `synth/` did not
make. Three workpackages: an operating-characteristics instrument for the pre-registered
thresholds, the real-data run itself, and a clean-install CI job. **Gate 1 ruling 3 governs the
whole phase**: a real blot may falsify a decision, never select a parameter or a code path.
Nothing in `pipeline/`, `configs/` or any shipped parameter changed.

### 1. The first real-data run measured nothing, and that is the result

`tools/phase3/run_real.py` ran the shipped pipeline over all 19 Gate 2 crops with
`configs/default.yaml`, enumerated from `crop_log.csv` rather than by globbing.

**0 of 19 crops produced a result document; 19 failed.** Every crop is a 3-channel PNG, and the
loader quantifies single-channel images only. The refusal is correct — §7 of the pre-registration
forbids choosing an RGB→grey conversion, and the loader says exactly that rather than picking a
channel. So no lane, band or QC flag has ever been produced from a real blot, and the ImageJ
comparison has not begun.

What §11 verified was per-pixel **colour fraction**; what the loader tests is **channel count**.
A mechanical check that does not test the property its consumer tests certifies nothing about the
consumer, which is how Gate 2 closed on a set the instrument cannot read.

**The report deliberately prints no number called N.** Three terms of §2's ratio definition cannot
be evaluated from a result document: "usable lane" is undefined in the pre-registration, lane width
is unquantified, and no reference band is designated anywhere a tool can read. The report gives an
upper bound and names the three blockers instead. Printing a number under those conditions would
publish an implementer's definition wearing a pre-registration's name — a deviation from the
kickoff's wording, stated inside the report itself.

Two consequences carried forward: `crop_log.csv` has no `blot_id` column though §9 provides for
one, and five parent figures contribute more than one crop, so **the set is between 13 and 19 blots
and the record does not say which** — neither form of the stopping rule can be evaluated against
it. And `panel_note` names a reference *protein* while the CLI needs a *band id*; bridging them
means deciding from the image which band is the loading control, which §2 forbids in terms. The
tool detects and reports a designation column but does not pass it to `--reference-band`.

### 2. Two draft amendments to the pre-registration

Both are dated amendment files beside the frozen document, not edits of it.
`DECISION_unit_of_analysis.md` remains byte-identical to what Gate 2 froze, and
`tools/check_claims.py` fails the build if it is not.

| file | sha256 | status |
|---|---|---|
| `data/real/AMENDMENT_2026-08-19_delta_and_power.md` | `a104b9fc6146f76338d074e175922f2f5dd2fac32b5937d9509900b3fc3e2a0e` | DRAFT |
| `data/real/AMENDMENT_2026-08-19_channel_collapse.md` | `7eb626d7825ecdb8d1c0961bcc0a587d1f1cb81dfd9af68d1cba156eab6f6a38` | DRAFT |

**What they supersede, named rather than implied.** The first amendment's header originally read
"supplements, does not supersede"; that was inaccurate and is **withdrawn**. It supersedes **§5**
(the ratio floor for a descriptive-only outcome is raised, and a blot minimum joins the "whichever
comes first" ceiling) and **§3's treatment of clustered CIs in the small-N regime only** (§3 decided
"disclosed, not corrected"; the cluster-bootstrap consequence corrects it at small N — §3's
disclosure requirement is otherwise untouched). The second amends **§7**: it does not change §7's
rule, it decides how that rule applies to the 19 crops now that they have been measured against it.

Each carries a **ratification procedure** in its own status section — flip DRAFT, recompute sha256,
pin the digest, update the path if renamed, in one commit. The figure pins reach the first document
by its exact path and return quietly if that path is absent, which is acceptable in a draft and not
in a ratified one.

### 3. Operating characteristics, pinned on both sides

`tools/stats/rs_power.py` (seed 20260819, ~25 s) answers the Gate 1 deferred discriminating-power
question by simulation; its full output is committed as `rs_power_expected.txt`. Two mechanisms
hold the amendment that quotes it: a CI step re-runs the script and requires byte-identical stdout,
and `check_amendment_figures()` requires every figure the amendment quotes to still sit in the
**row and column** it was read from. Two steps, two halves — "the numbers moved", and "the numbers
moved and the prose did not".

Both halves were arrived at by being wrong first. A file-wide substring search passed a value that
had moved between cells; a row-anchored search passed two values transposed within one row; and
counting only rows that still carried the value could not see a duplicated row hiding a corrupted
one. Row identity and cell value are now separate patterns, and a duplicated row is itself a
failure.

### 4. E1 is closed

`install-path` runs README.md's install-and-run path command for command on a runner that has never
seen this repository, then validates the result against `schema/result.schema.json`. Its first step
reads both README.md's fenced blocks and the job's own step from disk and requires them to be the
same sequence — a subset test would pass an *added* README command and leave the job verifying a
path nobody is told to type.

**It passed on this branch's first push, in 32 s** (run `32351508541`, commit `1e812b3`, conclusion
`success`; the `checks` job passed alongside it). With the clean-clone run on macOS, both halves of
E1 are covered and **the README needed no correction on either**. E1 moves `Open` → `Accepted`.

### 5. Rulings R1–R9

Recorded verbatim in NOTES.md, "Phase 3b-0 rulings".

- **R1** — W0's continuation past the §1 stop condition is ratified retroactively; §1's testability
  claim is confirmed by the computation. **The breach of the stop gate stays recorded, not erased.**
- **R2** — the amendment header is corrected to name what it supersedes.
- **R3** — pre-run revision on measured power: the descriptive-only cutoff moves from N < 15 to
  N < 18, closing the verdict gap by moving the boundary rather than disclosing it. The three
  agreement thresholds are unmoved; what Gate 1 ruling 8 forbids is revision *after* a result.
- **R4** — the corrected figures are authoritative from the committed script output.
- **R5** — §7 on the 19-crop set: collapse to green permitted where channels are byte-identical
  (10 crops) or diverge by at most 2 DN (2 crops); six crops between 3 and 43 DN excluded with
  disclosure; one at 255 DN rejected as real colour. **The 2 DN bound was named before the
  divergence table was measured and was not revised once the values were visible** — the
  distinction is not in the number but in the order of events. **The measurable set is 12 of 19.**
  Implementation is deferred to the next phase.
- **R6** — figures in the implementer's note live in tables only; prose references them by tag, and
  the rule is mechanical rather than editorial.
- **R7** — the four blocking items from the sixth cycle, and both disclosable ones.
- **R8** — the ratification-rename blind spot becomes a documented procedure, not a disclosure.
- **R9** — the cycles past PLAN.md's cap are recorded in DEBT.md on the Phase 4a precedent.

### 6. Register: S19 added, D1–D7 drafted

**S19** — the ruled channel collapse is not implemented, so the 12 admissible crops still do not
load. Deferred deliberately: the measurement and the ruling both happened in this phase, so
implementing the loader here is what Gate 1 ruling 3 forbids however mechanical it looks. **This
branch ships the ruling; the next ships the code.**

Seven drafts sit in `runs/3b0-real/DEBT_DRAFTS.md` (untracked — `runs/` is ignored) for promotion:
all 19 crops refused; nine crops not losslessly single-channel; §11's colour screen passing a
saturated pixel; no machine-readable reference designation; the missing `blot_id`; E10's timing
anchor still absent; and §6 expecting `lossy_format` to fire where §9's PNG export means it cannot.
The first three carry the R5 ruling on their Status lines and are marked superseded rather than
deleted — a ruling is easier to audit beside the finding that prompted it.

## Review record

**Seven cycles**, against PLAN.md's cap of five. Cycles one to five were full reviews of the diff
(13, 11, 12, 15 and 3 REQUIRED findings). Cycles six and seven ran on explicit human instruction,
each **scoped to verifying named fixes rather than re-reviewing** (7 and 8 findings), on the Phase
4a precedent recorded in DEBT.md.

**The loop did not close on "zero REQUIRED".** The seventh cycle's eight findings were all
mechanical and were fixed **without an eighth cycle**, so they carry their own tests and mutation
testing but no fresh-reviewer pass. The loop closed on a different criterion, ruled by the human:
that cycles six and seven had degraded the escaping-defect class — a figure fixed where a reviewer
named it while an unpinned copy survived elsewhere — from invisible to small and self-catching.
That is a judgement about the class of remaining defect rather than about their count, and it is
recorded in DEBT.md so a reader checking this phase against the loop protocol finds the criterion
rather than an unexplained cap.

The recurrence itself is the phase's most useful finding about process: **three consecutive cycles
fixed figure-staleness at the sites a reviewer happened to name, and each time the same defect
survived one paragraph, one function or one document away** — inside the mechanism built to stop
exactly that. Named-site fixing scales with a reviewer's attention. The structural rule replaced
it.

## Deviations and disclosures

- **A second file was written to `data/real/`.** The kickoff's standing constraint allowed one.
  R5 directs a second; the human's ruling is later and more specific, and the supersession is
  recorded in NOTES.md rather than passed over.
- **Seven review cycles against a cap of five**, as above.
- **W1 reports an upper bound instead of N**, as above.
- **No new dependency.** `requirements.txt` changes one comment only.
- **Nothing rode into either changeset after the loop closed.** `.review-sources/` is sometimes
  read as a late local addition; it is not. It has been in `.gitignore` since before this branch
  (line 8 at `dbe1ee8`) and appears in the diff only as context. This branch added exactly two
  ignore patterns, `runs/` and `kickoff_3b0.md`, both with their reasons in the file.

## Open questions for the human

1. **The cluster-bootstrap consequence's content.** R2 fixed the header contradiction. Two findings
   stand: the evidence cited for the bootstrap comes from the independent-observations table, and
   `rs_power.py` simulates no bootstrap anywhere.
2. **Both amendments are DRAFT.** Ratifying either means running its own procedure, including
   pinning its digest.
3. **The `42 s` figure.** The task text for this phase referred to "the 42 s/1360×1024 figure".
   No such figure exists here — E10 records 23.49 s. It was transcribed into two documents before
   being caught, which is the failure mode E10's own entry already documents.
