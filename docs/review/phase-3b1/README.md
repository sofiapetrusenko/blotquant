# Phase 3b-1 — review record

**These files are verbatim extracts from a Claude Code session log, made on 2026-08-24.**
Each `cycle-N.md` contains the exact text of one reviewer's verdict — its REQUIRED items,
their wording, and its mutation table — copied byte for byte from the session log, including
any typos. Nothing here is paraphrased, summarised, or corrected. The extraction was
re-compared against the source after writing and all seven bodies are byte-identical to it.

Committed because `DEBT.md` P2 entry (11) needed it. That entry records this phase's cap
extension on the Phase 4a precedent, and cycle 6 found that it reproduced entry (8)'s premise
"with equal specificity and none of the evidence": four numeric claims about these cycles with
no artefact in the tree. The figures were withdrawn rather than left standing. This directory
is the artefact they were withdrawn for want of, committed by human ruling of 2026-08-24, and
entry (11) now cites it.

**The raw log is retained locally and is not committed**, because it contains private
conversation content well beyond the review record. It is held under `.review-sources/`,
which is gitignored.

| file | sha256 | covers |
|---|---|---|
| `23162d98-6aec-489b-8bfa-a7f3fbff87a7.jsonl` | `963a57ea86b8b2d08ae1ae6b0df493f49c74ec65c0255cdc9010b8632e3cb4a0` | **all seven Phase 3b-1 cycles**; branch `phase-3b1-collapse`, 2026-08-20 |

**Six session logs were searched, not one.** Every `*.jsonl` under this project's Claude Code
log directory was scanned with the same filter. Exactly one holds Phase 3b-1 review cycles.
Of the others: `1850b996-…` holds seven verdicts, all dated 2026-08-19 — those are Phase 3b-0's
cycles, and its single occurrence of the string `phase-3b1` is a `type: system` record with no
result content; `e1cac5de-…` holds the Phase 4a cycles and has grown since Phase 4a pinned it,
so its live sha256 no longer matches the digest in `../phase-4a/README.md` — that digest pins
the copy under `.review-sources/`, which is unchanged; `179c84b1-…` and `f18d5865-…` hold no
verdict of this shape at all; `16769fad-…` is the session that performed this extraction and
contains no review cycle.

**Any cycle that could not be found in the logs would be listed below as "not recovered",
never reconstructed from memory.** All seven were found.

## How the extraction was located

Each reviewer ran as a subagent. Its output reached the session inside a
`<task-notification>` block, and what each `cycle-N.md` reproduces is the exact content of
that block's `<result>` element. Records were found by scanning for entries whose message
content is a string containing `<task-notification>`, `VERDICT` and `REQUIRED`; seven matched,
and every one is reproduced. Each file's header names its source line number and timestamp.

## Index

| cycle | date (UTC) | REQUIRED | what was contradicted | source |
|---|---|---|---|---|
| [1](cycle-1.md) | 2026-08-20 | **8** | DEBT S19 false in the present tense and still `Status. Open.` after the diff closed it; two unrecorded PLAN.md deviations missing from P2; the three-value `source_of_designation` vocabulary shipped without being stated as a departure; the schema's `const: green` and `maximum: 2` restating the loader constants with nothing pinning them together; no test validating a produced document carrying `channel_collapse`; the CLI collapse print block unreachable under test; `except PipelineError` reclassifying a corrupt file as an amendment exclusion; two new `--out` writers with no gold-set guard | log line 424 |
| [2](cycle-2.md) | 2026-08-20 | **4** | the API's `ANALYZE_DESCRIPTION` sentence a caller acts on being false; the 1.3.0 version history naming one of the two fields the bump adds; `lossy_format` structurally unreachable across the whole real corpus with nothing recording it; three new refusal branches with no test | log line 638 |
| [3](cycle-3.md) | 2026-08-20 | **3** | three of the four `lossy_format` figures asserted with nothing re-measuring them, against the standard the same diff sets one section earlier; `CropSetError`'s docstring promising a catch its own hierarchy cannot express; the user-facing single-channel scope claim left standing in `README.md`, `--help` and `api/display.py` after the loader widened | log line 742 |
| [4](cycle-4.md) | 2026-08-20 | **1** | the OpenAPI input description still promising grayscale-only, with `api/errors.py`'s 415 rationale and `UnsupportedImageError`'s docstring carrying the same now-over-broad claim | log line 799 |
| [5](cycle-5.md) | 2026-08-20 | **2** | a live design-decisions bullet in `NOTES.md` still asserting the pre-amendment rule; `_collapse_channels`'s docstring overclaiming uniform refusal wording | log line 863 |
| [6](cycle-6.md) | 2026-08-20 | **12** | the ruling-preserving merge exercised only through a call shape no caller uses; a confirmed row silently deleted when its crop leaves the measurable set; a confirmed row with a blank `target_label` yielding a ratio with zero targets; `contributes_ratios`'s missing-row guard passing all 808 tests when replaced by `return False`; `write_designations` not validating what it writes; and six record claims — including P2 entry (11)'s four unevidenced figures — still asserting the pre-gate state | log line 1027 |
| [7](cycle-7.md) | 2026-08-20 | **7** | a superseded sentence still standing under an annotation saying it had been removed; the replacement for "W7 was not run" asserting a run and a section that do not exist; the phase heading still stating the confirmation as outstanding; two module docstrings still asserting nothing is confirmed; `refuse_to_drop_rulings` naming the wrong file in an actionable message; `CorpusError`'s docstring not covering its new raise site; a `__post_init__` docstring claim false in its own paragraph | log line 1122 |

**Totals: 37 REQUIRED across the seven Phase 3b-1 cycles;
18 in the five Phase 3b-1 cycles inside PLAN.md's cap.** No cycle returned zero. Cycles 6 and 7 were extensions past that cap,
each under its own human ruling; an eighth was not run.

## What the record shows about the cap extensions

**Every cycle was mutation-tested.** All seven files carry a mutation table, and the
bound-widening mutation — admitting 3 DN where the amendment bounds the collapse at 2 — appears
in every one of them, and bit every time. The number of tests it brings down, cycle by cycle:
**6**, 10, 8, 9, 13, 14, **15**. The sequence is not monotone and is not a measurement of the
suite — cycle 1 mutates the comparison rather than the constant, and each figure is what that
cycle's own table enumerates. What it does show is the direction: by cycle 7 the ruled bound
could not be widened without fifteen tests objecting, spanning — in cycle 7's own listing —
`test_pipeline_load.py`, `test_real_channel_collapse.py`, `test_schema.py`, and both
committed tables' byte-identity and CLI tests. No mutation survived
in any cycle. This is the behavioural half of the review, and it converged.

**The claim half did not.** Cycle 5's verdict, in its own words, is *"Two items, both
claim-accuracy, neither behavioural."* Cycle 6, narrowed by ruling R1 to the two cycle-5 claim
fixes and their surfaces, returned twelve. Cycle 7, narrowed to confirming those twelve,
returned seven — and **four of the seven are defects the cycle-6 fixes introduced**: items 2, 5,
6 and 7. That tally is this file's reading of the cycle-7 text rather than a count the reviewer
states. What the reviewer states is narrower and is quoted here so the reading can be checked
against it: item 2, *"Fix 6 removed an accurate statement … and replaced it with an inaccurate
one"*; item 5, *"New defect introduced by fix 2"*; item 6, `refuse_to_drop_rulings` is *"now a
third one it does not cover"*; and the verdict's *"Fixes 2 and 3 each left one defect on the
surface they touched"*, whose fix-3 half is item 7.

**One withdrawn figure is not restored, because the artefact contradicts it.** Of P2 entry
(11)'s four withdrawn claims, three are borne out above. The fourth — that a mutation "failed
the build from four independent directions in cycle 4 and six in cycle 5" — is not. Both
numbers belong to cycle 5: its verdict says the rule is *"enforced on both sides of the bound
at four independent layers"* and that mutation 1 *"shows the bound cannot be moved without the
build noticing in six places at once"*. Cycle 4's own table gives its bound-widening mutation
as **9 failed** and characterises no directions. The claim was misattributed as well as
unevidenced, so it stays withdrawn and the failure counts above stand in its place.
