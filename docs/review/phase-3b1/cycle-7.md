# Phase 3b-1 — review cycle 7

> **Provenance.** Verbatim extract from a Claude Code session log.
> Source file: `23162d98-6aec-489b-8bfa-a7f3fbff87a7.jsonl`
> Source sha256: `963a57ea86b8b2d08ae1ae6b0df493f49c74ec65c0255cdc9010b8632e3cb4a0`
> Location: JSONL record on line 1122 of 1203 (~93% into the file), `type: user`, timestamp `2026-08-20T14:11:48.024Z`.
> The reviewer ran as a subagent; its output reached the session inside a
> `<task-notification>` block, and what follows is the exact content of that
> block's `<result>` element — copied byte for byte, including any typos.
> Extracted 2026-08-24. The source log is retained locally and is NOT committed.

---

Review complete. All mutations applied, observed, and restored; tree verified clean (28 paths, empty unstaged diff, `pytest` 817/817 green, `ruff` clean, `check_claims` clean).

## REQUIRED

**1. `NOTES.md:3336` — the R2-superseded sentence is still standing, unannotated, and the annotation 18 lines above says it was removed.**
Section `### Why the blot_id is a sibling file and not a column in crop_log.csv` ends:

```
carries §9's sentence verbatim in its header, and the human rules on where the column belongs.
```

Cycle-6 finding 6 named exactly this sentence. The fix annotated it at line 3318 (`An earlier version of this paragraph ended "the human rules on where the column belongs" — R2 is that ruling, and the sentence was left standing one revision too long.`) — but that annotation is attached to a *different* paragraph, and the sentence it says was corrected is verbatim intact in the section below. So the fix is incomplete *and* the repair text is now itself a false claim: the sentence is still standing, one more revision too long. R2 ruled ("the sibling file is the ruled mechanism"), so a reader of §3336 is told a decision is outstanding that the same file records as made.

**2. `NOTES.md:3356–3361` — the replacement for the "W7 was not run" section asserts a run and a section that do not exist.**

```
### W7 was blocked on the tables, and the block is now lifted
...
**G1 and G2 lifted that block on 2026-08-20**; what W7 then produced, and what it could
not produce, is recorded in its own section rather than here.
```

W7 has not been executed. There is no W7 section in `NOTES.md` (`grep -n "W7" NOTES.md` returns only these three lines and one back-reference), no `runs/3b1/`, and no `runs/3b1/REPORT.md`. This is not "W7 is missing" — it is the record claiming an artefact it does not have, which is precisely the standard this same diff applies in DEBT P2 entry (11), where counts were **withdrawn** because "a figure in this register whose evidence is not in the tree is the stale claim `tools/check_claims.py` exists to catch." Fix 6 removed an accurate statement ("W7 was not run") and replaced it with an inaccurate one.

**3. `NOTES.md:3196` — the phase section heading still states the confirmation as outstanding, unannotated.**

```
## Phase 3b-1 — the ruled collapse implemented, and two tables the human must confirm
```

The sub-heading at 3282 got the file's superseded-decision annotation (`— *superseded at the gate, see below*`); the parent heading, which is the more prominent of the two and carries a live modal obligation ("must confirm"), did not. Same defect class cycle 6 called REQUIRED for the sub-heading.

**4. `tools/phase3/designations.py:10–13` and `tools/phase3/blot_identity.py:52–53` — module-level docstrings still assert that nothing is confirmed, contradicting the file each module writes.**

```
designations.py:10   It writes candidates parsed from the filename, marks every row pending, and
designations.py:11   **confirms nothing**: ... The human confirms each row against the figure caption.

blot_identity.py:52  PROPOSED_PENDING = "proposed_pending_human"
blot_identity.py:53  """Every row this module writes. A proposal from a filename, and nothing more."""
```

Both are false since the carry-through merge: `build_designations`/`build_blot_identities` emit `caption_confirmed_human` and `image_confirmed_human` rows verbatim, and all 12 committed rows in each table are ruled. `designations.py`'s docstring and the `HEADER_NOTE` it writes twelve lines below it ("CONFIRMED AT THE HUMAN GATE, 2026-08-20") now say opposite things about the same table. The mandate for this cycle was to search rather than trust the list of four; these are the further sites.

**5. `tools/phase3/designations.py:302` and `tools/phase3/blot_identity.py:204` — `refuse_to_drop_rulings` is handed the module-default path, not the file being rebuilt, so the refusal names the wrong file and instructs the human to edit a table that is correct.**
Reproduced against a copy of the committed table with one orphan row appended, run as `main(["--out", &lt;copy&gt;])`:

```
CorpusError : /Users/sofiapetrusenko/blotquant/data/real/designations.csv holds row(s) for
['PMC13135388_Figure4__E-TIGAR.png'] ... Decide what should happen to those rows and remove
them deliberately, or restore the crops
```

`data/real/designations.csv` holds no such row; the copy does. The `path` parameter exists solely so the message can name the file, and both call sites feed it a constant. A human following this actionable message edits the committed, human-ruled table. New defect introduced by fix 2. Fix: thread the destination through `build_designations`/`build_blot_identities`, or drop the path from the message.

**6. `tools/phase3/crop_names.py:105–114` — `CorpusError`'s docstring enumerates the conditions it is raised for, and `refuse_to_drop_rulings` is now a third one it does not cover.**

```
"""The approved crop set itself is wrong, before any table is built from it.

A crop the frozen log lists but the tree does not hold, or bytes whose sha256 does not
match the digest the log records for them. Raised while *reading* the crop set, ...
```

`refuse_to_drop_rulings` raises `CorpusError` while *reconciling a table against* an already-read crop set, in a case where the crop set may be entirely correct (a ratified amendment narrowing the bound is the scenario the function's own docstring names). Every other raise site in this diff justifies its class choice in prose (`_verify_crop_digest`: "what is wrong here is the corpus"); `refuse_to_drop_rulings` does not, and the class it picked now contradicts its own contract. The choice is defensible — `DesignationError` would be wrong in `blot_identity.py` and vice versa, and `CorpusError` is the only shared concrete sibling — but that reasoning has to be written down and the class docstring widened.

**7. `tools/phase3/designations.py:138–140` — `__post_init__`'s docstring claim "An unrecognised source constrains nothing here" is false, and `tests/test_designations.py::test_an_unrecognised_source_constrains_the_row_shape_not_at_all` asserts less than its name.**
Measured:

```
'A|'   unrecognised-source: RAISED -&gt; 'x.png' has target_label 'A|', which parses to a blank target
'|B'   unrecognised-source: RAISED
'A||B' unrecognised-source: RAISED
'A|A'  unrecognised-source: RAISED -&gt; names the same target more than once
```

The blank-target and duplicate-target checks run before the source is examined, so they bind every source including unrecognised ones. The docstring's sentence is the stated rationale for the class/reader validation split, and it is wrong in the same paragraph that says "this class refuses shapes it knows to be wrong." The test named for the proposition only constructs `Designation("x.png", "", "", "looks_confirmed_to_me", "")` — the one shape that happens to be unconstrained — so the overclaim is unguarded. Either narrow the sentence ("an unrecognised source imposes no *vocabulary-vs-shape* constraint; the target-list well-formedness checks still apply") or rename the test to what it asserts.

## SUGGESTED

- **In scope.** `tests/test_designations.py::test_the_writer_refuses_two_rows_for_one_crop` does not assert `not path.exists()` after the refusal, unlike its twin `test_a_collapsing_id_cannot_be_written_to_disk`. The check is correctly placed before `mkdir`/open, so the property holds — it is just unpinned.
- **In scope.** `refuse_to_drop_rulings` is fully generic (`Mapping[str, object]`, `Sequence[str]`, `Path`) and raises `CorpusError`, which is defined in `crop_names.py`; living in `designations.py` means `blot_identity.py` imports from `designations` for something with no designation content. Not a defect — no circularity, and `measurable_crops` already establishes that direction — but `crop_names.py` is where it belongs alongside the error it raises.
- **Out of scope (record follow-up, not this diff's defect).** DEBT P2 entry (11) records the Phase 3b-1 review as having run "a sixth cycle." This is cycle 7, also under a human ruling. The entry cannot record an outcome it does not yet have, but the register will be stale the moment this cycle closes, and entry (11) already flags "Whether to commit this phase's cycle verdicts, as Phase 4a did, is an open decision for the human." Worth resolving before the PR.
- **Out of scope.** `data/real/designations.csv`'s `HEADER_NOTE` line `"  reference_strip_confirmed_human a human ruled on the row..."` is the one vocabulary entry whose value overruns the column the other two align to. Cosmetic.

## VERDICT: CHANGES REQUIRED

Five of the twelve cycle-6 fixes were behavioural; all five are real, complete, and now bite. Findings 7, 8, 9, 10, 11, 12 are accurate and, where checkable, machine-verified (DEBT register: 15 Accepted + 2 Permanent + 16 Open = 33, matching "Seventeen of the 33 … 16 are Open"; `_check_p2_composition` reads markers `[1..11]`, 11 entries / 13 deviations / difference 2, non-vacuously). Finding 6 is the one that did not land: two sites it targeted are unrepaired, one repair introduced a fresh false claim, and the search it called for did not reach the two module docstrings. Fixes 2 and 3 each left one defect on the surface they touched.

## Mutation outcomes

| # | Mutation | Result |
|---|---|---|
| 1a | `build_designations(args.crop_log)` — drop `existing=` in `designations.main()` | **FAILS** — `test_designations.py::test_the_cli_entry_point_preserves_every_ruling` (only that test). The cycle-6 survivor now bites. |
| 1b | `build_blot_identities(args.crop_log)` — drop `existing=` in `blot_identity.main()` | **FAILS** — `test_blot_identity.py::test_the_cli_entry_point_preserves_every_ruling` (only that test). Bites. |
| 2 | `contributes_ratios` → `return False` for a crop with no row | **FAILS** — `test_asking_whether_an_unlisted_crop_contributes_raises_rather_than_returning_false`. The second cycle-6 survivor bites. |
| 3 | `refuse_to_drop_rulings` no-op (`if False and orphaned`) | **FAILS in both** — `test_designations.py` and `test_blot_identity.py`, both `::test_a_rebuild_refuses_to_drop_a_ruling_for_a_crop_that_left_the_set`. |
| 4a | Remove blank-target-element check from `__post_init__` | **FAILS** — `test_a_malformed_target_list_is_refused_rather_than_tidied[A\|\|B]`, `[A\|]`, `[\|B]`. |
| 4a′ | Remove `if not targets` (confirmed row naming no target) | **FAILS** — `test_a_confirmed_row_naming_no_target_cannot_be_constructed`. |
| 4b | Remove no-ratio-row-carrying-a-target check | **FAILS** — `test_a_no_ratio_row_carrying_a_target_cannot_be_constructed`. |
| 4c *(extra)* | Remove confirmed-blank-reference check from `__post_init__` | **FAILS** — `test_a_confirmation_with_a_blank_reference_cannot_even_be_constructed`. Confirms the check did not go missing in the move out of `_validate_targets`. |
| 5 | Remove duplicate-crop check from `write_designations` | **FAILS** — `test_the_writer_refuses_two_rows_for_one_crop`. |
| 6a | `CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN = 3` | **FAILS (15 tests)** across `test_pipeline_load.py`, `test_real_channel_collapse.py`, `test_schema.py`, and both table files' byte-identity/CLI tests. |
| 6b | `propose_blot_id` keyed on the PMC accession | **FAILS (4 tests)** — `test_two_figures_of_one_article_do_not_share_a_proposed_id`, `test_a_rebuild_without_the_existing_rows_loses_the_rulings`, `test_the_table_round_trips_through_the_file`, `test_the_writer_refuses_a_destination_inside_the_gold_set`. |

Every mutation produced a failure; no survivors. Restore verified after each (`git diff` empty against index, `git status --short` = 28 paths), and the final state re-confirms `pytest` exit 0 / 817 collected, `ruff` exit 0, `tools/check_claims.py` exit 0.

Relevant paths: `/Users/sofiapetrusenko/blotquant/NOTES.md`, `/Users/sofiapetrusenko/blotquant/tools/phase3/designations.py`, `/Users/sofiapetrusenko/blotquant/tools/phase3/blot_identity.py`, `/Users/sofiapetrusenko/blotquant/tools/phase3/crop_names.py`, `/Users/sofiapetrusenko/blotquant/tests/test_designations.py`.
