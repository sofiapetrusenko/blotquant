# Phase 3b-1 — review cycle 6

> **Provenance.** Verbatim extract from a Claude Code session log.
> Source file: `23162d98-6aec-489b-8bfa-a7f3fbff87a7.jsonl`
> Source sha256: `963a57ea86b8b2d08ae1ae6b0df493f49c74ec65c0255cdc9010b8632e3cb4a0`
> Location: JSONL record on line 1027 of 1203 (~85% into the file), `type: user`, timestamp `2026-08-20T13:18:44.435Z`.
> The reviewer ran as a subagent; its output reached the session inside a
> `<task-notification>` block, and what follows is the exact content of that
> block's `<result>` element — copied byte for byte, including any typos.
> Extracted 2026-08-24. The source log is retained locally and is NOT committed.

---

Tree restored exactly: 28 staged paths as at start, unstaged diff empty, ruff and `check_claims` clean.

## Scope (a) — the two cycle-5 claim fixes: both correct

- `NOTES.md:1371-1376` — the Phase 1 bullet is annotated in place ("**Since Phase 3b-1 this is history in one narrow respect:**") rather than rewritten. Matches the file's convention. Accurate.
- `/Users/sofiapetrusenko/blotquant/pipeline/load.py::_collapse_channels` (line 253) — docstring now says "Two of the three carry the loader's long-standing … wording. The bit-depth arm deliberately does not". Verified against the three raise sites: the shape arm (271) and divergence arm (296) carry "single-channel grayscale gel-doc images only"; the bit-depth arm (287) does not. Claim is true.

The G1/G2/R1-R4 block at `NOTES.md:3369-3396` is **verbatim**, character-for-character against the ruling text once the `&gt; ` prefixes and wrapping are normalised. I diffed it mechanically.

## REQUIRED

**1. `/Users/sofiapetrusenko/blotquant/tools/phase3/designations.py:417` and `/Users/sofiapetrusenko/blotquant/tools/phase3/blot_identity.py:329` — the merge is only ever exercised through a call shape that exists nowhere outside the tests. Removing it from `main()` passes all 808 tests and wipes every human ruling.**
`build_designations`/`build_blot_identities` default `existing=None`; the merge happens *only* because `main()` reads the file first and passes it. Every test calls `build_*(existing=…)` directly, so the argument is always supplied by the test itself. I deleted `, existing=existing` from both `main()` bodies and ran the suite: **808 passed**, identical to baseline. Then ran `python -m tools.phase3.designations`, which printed `wrote …/designations.csv: 12 row(s), 0 ruled by a human, 12 pending human confirmation`, exit 0, and reverted all twelve rows to `parsed_pending_human` — including the corrected `Fibronectin|CCN2` row and the E-Vinculin ruling. That command is the one both CSV headers and `read_designations`'s own error message instruct a human to run. Mutation 4 as specified does bite, but it guards a function nobody calls that way; the write path a human uses is unguarded. The "airtight" claim in the merge-preserving docstrings is not met.

**2. `tools/phase3/designations.py:244` (`build_designations`) and `blot_identity.py:174` (`build_blot_identities`) — a confirmed row whose crop leaves the measurable set is silently deleted, with no error and no note.**
Both builders return rows only for `measurable_crops(...)`. I stubbed `measurable_crops` to drop E-Vinculin and rebuilt over the committed table: 12 rows became 11, the E-Vinculin ruling gone, no exception. Combined with `main()` (read → build → write), any change to loader behaviour plus one tool run permanently removes a human ruling from the tree. The loader's admissible set changed in *this diff*, and the amendment bound is explicitly the kind of thing a future ruling could move. `existing` keys not present in the rebuilt set should raise, not vanish.

**3. `tools/phase3/designations.py:312` (`_validate_targets`) — a `caption_confirmed_human` row with a blank `target_label` passes every guard and yields a ratio with zero targets.**
Measured directly:
```
Designation("x.png", "", "GAPDH", "caption_confirmed_human", "")
  _validate_targets      -&gt; no raise      (target_labels is (), so `any(not t for t in ())` is False)
  target_labels          -&gt; ()
  contributes_ratios     -&gt; True
  confirmed_reference_label -&gt; "GAPDH"
```
The module enforces the exact mirror case — `"is marked {source!r} but its reference_label is blank. A confirmation with nothing confirmed is not a designation"` — and not this one. It matters concretely because E-Vinculin *is* a blank-target row: the only thing keeping it out of N is the string `reference_strip_confirmed_human`, and the two vocabulary values sit adjacent in the header note. Changing that one cell moves N from 11 to 12 with a nameless ratio and no refusal anywhere. The whole point of the new `target_labels` mechanism is "one ratio per target"; a confirmed row with zero targets contributes an undefined number and nothing objects. The mirror is also unguarded: a `reference_strip_confirmed_human` row that *does* carry a target (`target_labels == ("TIGAR",)`) returns `contributes_ratios → False` and is silently excluded. The invariant binding row shape to vocabulary word needs to be enforced in both directions.

**4. `tools/phase3/designations.py:377-385` — `contributes_ratios`'s missing-row guard has zero test coverage; replacing it with `return False` passes all 808 tests.**
I ran that mutation against the full suite: no failures. This is precisely the failure the function's own docstring says it exists to prevent ("would let an unconfirmed crop drop silently out of N"), and it is the one branch of the new API with no test. `confirmed_blot_id`'s equivalent *is* covered (`test_asking_about_a_crop_with_no_row_raises`), so the omission is asymmetric within the same diff.

**5. `tools/phase3/designations.py:250` (`write_designations`) does not validate what it writes, while `blot_identity.py:220` (`write_blot_identities`) calls `validate_blot_ids` first.**
Demonstrated: `write_designations([Designation("z.png","A||B","GAPDH","caption_confirmed_human","")], …)` writes `z.png,A||B,GAPDH,caption_confirmed_human,` to disk without complaint. Malformed lists are refused only on read. The designation table is the file a human hand-edits, so the writer is where the asymmetry costs most.

**6. `NOTES.md:3282`, `3286`, `3350-3352` — the record still asserts, unannotated, that nothing is confirmed.**
Heading "### Two tables were built and neither was confirmed"; body "with every row marked pending. **Nothing in either file is confirmed, and the implementer may not confirm it.**"; and "### W7 was not run / The run and N are blocked on the human confirming both tables." All three are now false of the tree. The R4 paragraph annotates only the *vocabulary* deviation, not the blanket claim. This is the identical defect cycle 5 raised against the Phase 1 bullet, and the fix applied there at line 1371 is the file's own convention — in-place annotation, not silent survival behind a later section. (Line 3358, "the human rules on where the column belongs", is the same class: R2 ruled it.)

**7. `README.md:110` — "the reference-band designations and the blot identities are recorded as *candidates awaiting human confirmation*, so no ratio, no N and no agreement statistic has been produced from a real blot."**
The first clause is false after G1/G2. The second is still true and should survive; the first must not. This is the most public surface in the diff.

**8. `DEBT.md:132` — "so the settled count is unchanged and the open count is back to where Phase 3b-0 left it" contradicts the sentence it annotates.**
I counted the register mechanically: 33 entries, 17 Accepted/Permanent, 16 Open — the headline numbers are right. But the settled count moved 16 → 17 (S19 to Accepted) and the *open* count is the one that stayed at 16 (S19 out, S20 in). The parenthetical states both backwards, in the same breath as the "Seventeen" that replaced "Sixteen" two lines above. P2's markers do read 1..11 in first-occurrence order, and 11 entries + 2 (entry 7) = 13 deviations checks out.

**9. `NOTES.md` Phase 3b-1 section — the two-target mechanism decision is not recorded there at all.**
G1: "Propose the table mechanism for a two-target crop (two rows or a target list) and report it before wiring". The choice and its reasoning exist only in `tools/phase3/crop_names.py:45-60` (`TARGET_SEPARATOR`) and the CSV header. NOTES.md's gate section describes the *correction* to the row but never says which mechanism was chosen or why; "What each ruling changed, in one line each" omits it. CLAUDE.md requires NOTES.md for mid-phase design decisions, and this section records every other one in the diff (schema bump, module constants, 16-bit refusal, free-text flag, sibling file). On the merits the choice is defensible — the human offered it explicitly — but the stated reason is overstated: two rows would not require *relaxing* the duplicate guard, only re-keying it on (crop, target) with a consistency check on `reference_label`, which is equally strict. Record the decision and state the reason accurately.

**10. `DEBT.md:1227`, P2 entry (11) — four numeric claims about the Phase 3b-1 review cycles with no artifact in the tree.**
"cycle 5 raised **no behavioural defect**", "both of its items were wrong sentences", "mutation-tested in every cycle", "failed the build from four independent directions in cycle 4 and six in cycle 5". There is no `docs/review/phase-3b1/`; only `docs/review/phase-4a/` exists. Entry (8), the precedent (11) explicitly invokes, was upgraded in a prior phase precisely to close this hole — "**Evidence for the premise is now in the tree**: `docs/review/phase-4a/` holds all six cycles' verdicts verbatim". Entry (11) reproduces the premise with equal specificity and none of the evidence, and `check_claims.py` does not pin those numbers. R1 authorises the cycle; it does not supply the counts.

**11. `data/real/blot_identity.csv:45` — the E-Vinculin row's note is self-contradictory.**
It reads "…§9 anticipates exactly this … but whether that is what these are **is for the human to rule on**; ruled distinct from the images (G2, 2026-08-20)". A row marked `image_confirmed_human` cannot also record that the question is open. This is a hand-merge artifact of the generated proposal note (`build_blot_identities` computes `notes` and then discards it for existing rows, so the two texts were concatenated by hand). The basis a reader audits should be the ruling, not the ruling appended to the proposal it replaced.

**12. `tests/test_designations.py:339` — docstring says "The header note is where this file says it is **not confirmed** and how a human confirms it."**
The header note now says the opposite. This is exactly the cycle-5 defect class inside the cycle-6 diff.

## SUGGESTED

- `tools/phase3/blot_identity.py:181-193` — `notes` is computed for every crop and then thrown away when `crop in existing`. Move the `if crop in existing: continue` above the computation; it currently reads as if the note applied to the carried-through row.
- `tools/phase3/designations.py:121` — consider validating in `Designation.__post_init__` rather than in a free function called from one call site; that closes finding 5 and finding 3 in one place and makes an invalid `Designation` unconstructible.
- Judgement on scope item 3: inventing `reference_strip_confirmed_human` / `NO_RATIO_SOURCES` is the **right call**, not over-reach. G1 confirmed E-Vinculin *and* said it contributes no ratio; neither leaving it pending nor marking it `caption_confirmed_human` can express that (the latter would return `contributes_ratios → True`, as finding 3 shows). `contributes_ratios` raising for a pending row is likewise correct and well argued. The problem is not the vocabulary, it is that the vocabulary word is the only thing carrying the state.
- *Out of scope (settled in earlier cycles, reported only so the human can rule):* `tools/phase3/` has no `__init__.py` and relies on namespace packages for `from tools.phase3.… import`; it works on 3.11 and matches `tools/gate2/`, but it is implicit.
- *Out of scope:* `pipeline/normalize.py:562-565` sets `reference_designation_source` unconditionally while `reference_band_ids` is gated on `config.mode in HOUSEKEEPING_MODES`. Currently unreachable divergence (an empty `references` raises first), but the two fields describe each other and are guarded differently.

## Mutation outcomes

| # | Mutation | Result |
|---|---|---|
| 1 | `CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN = 3` | **FAILS** — 14 tests, both sides: synthetic (`test_divergence_above_the_bound_raises…[3]`, `test_the_bound_is_not_moved_by_a_single_dn`) and real (`test_exactly_twelve_of_nineteen_crops_load`, the at-bound and excluded-crop cases), plus schema and both CSV byte-identity tests as a 13th crop becomes measurable |
| 2a | `confirmed_reference_label` returns the reference for a **pending** row | **FAILS** — `test_asking_for_a_confirmation_on_a_pending_row_raises`, all 3 params |
| 2b | …returns one for a `reference_strip_confirmed_human` row | **FAILS** — `test_a_reference_strip_row_refuses_to_yield_a_reference_label` |
| 3 | `contributes_ratios` returns False for a pending row | **FAILS** — `test_asking_whether_a_pending_row_contributes_raises_rather_than_returning_false` |
| 4 | `build_designations` ignores `existing` | **FAILS** — `test_rebuilding_over_the_committed_table_preserves_every_human_ruling`, `test_the_committed_file_is_byte_identical_to_what_the_writer_emits` |
| 5 | `target_labels` returns the raw cell (folds the two targets) | **FAILS** — `test_the_two_target_crop_carries_two_targets_and_one_reference` + all 4 malformed-list params |
| 6 | `propose_blot_id` keyed on the PMC accession | **FAILS** — `test_two_figures_of_one_article_do_not_share_a_proposed_id` + 3 more via `validate_blot_ids` |
| **extra A** | `main()` stops passing `existing` (both modules) | **SURVIVES — 808 passed.** Running the tool then wipes all 12 rulings, exit 0. Finding 1 |
| **extra B** | `contributes_ratios` returns False for a crop with no row | **SURVIVES — 808 passed.** Finding 4 |

All six specified mutations bite. The two I added do not. Restoration verified after each: MD5s match the backups, `git diff` empty, `git status --short` unchanged at 28 paths.

Baseline: `808 passed in 128.93s`; `ruff check .` clean; `tools/check_claims.py` OK over 16 files — and it still bites (it caught the P2 historical-vs-running-total conflation, which is why `_check_p2_composition` was added; that new check is a genuine strengthening, since it now measures the tally from the register rather than comparing one sentence to another). W7 correctly not attempted, and nothing in the tree pre-empts N or the verdict-eligible branch beyond quoting G2.

**VERDICT: CHANGES REQUIRED**
