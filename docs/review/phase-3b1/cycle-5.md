# Phase 3b-1 — review cycle 5

> **Provenance.** Verbatim extract from a Claude Code session log.
> Source file: `23162d98-6aec-489b-8bfa-a7f3fbff87a7.jsonl`
> Source sha256: `963a57ea86b8b2d08ae1ae6b0df493f49c74ec65c0255cdc9010b8632e3cb4a0`
> Location: JSONL record on line 863 of 1203 (~72% into the file), `type: user`, timestamp `2026-08-20T12:34:55.322Z`.
> The reviewer ran as a subagent; its output reached the session inside a
> `<task-notification>` block, and what follows is the exact content of that
> block's `<result>` element — copied byte for byte, including any typos.
> Extracted 2026-08-24. The source log is retained locally and is NOT committed.

---

Review complete. Baseline verified green (796 collected / 796 passed, `ruff` clean, `check_claims` clean); all four mutations applied, observed, and restored; tree confirmed byte-identical to how I found it (`git diff` empty, `git status --short` = the same 28 staged entries).

---

## Cycle-4 item: verified fixed, nothing new introduced

All three named surfaces now make true statements, and I checked each against the code rather than against the claim:

- `/Users/sofiapetrusenko/blotquant/api/app.py:323` — I rendered the OpenAPI document (`app.openapi()`) and confirmed the new text appears verbatim as `components.schemas.Body_analyze_analyze_post.properties.image.description`. It is what a Swagger reader sees. Every clause is true: 16-bit 3-channel and RGBA both reach `UnsupportedImageError` → 415.
- `/Users/sofiapetrusenko/blotquant/api/errors.py:87-99` — accurate.
- `/Users/sofiapetrusenko/blotquant/pipeline/errors.py:27-36` — accurate; each of the four `UnsupportedImageError` raise sites does name which case it is.
- `/Users/sofiapetrusenko/blotquant/api/display.py:147-150` — accurate.

I swept the whole tree for the old claim (`multi-channel|single-channel|3-channel|colour image`, all extensions, excluding `.venv`). One live site still makes it — REQUIRED 1 below. The rest are correctly-tensed history (`NOTES.md:2993` "were refused", `docs/pr/phase-3b0.md`) or frozen artefacts that must not be edited (`data/real/DECISION_unit_of_analysis.md`, `tools/gate2/fetch_v2.py`).

The cycle-4 SUGGESTED items were all genuinely taken, and mutations 4a/4b prove the new pins bite rather than pass vacuously.

---

## REQUIRED

**1. `/Users/sofiapetrusenko/blotquant/NOTES.md:1372` — a live design-decisions bullet still asserts the pre-amendment rule.**

Under `## Phase 1 — core pipeline` → `### Small decisions that would otherwise have to be reverse-engineered`:

&gt; - **A multi-channel image raises** rather than having a channel picked for it. PLAN.md's MVP scope is single-channel grayscale; choosing a channel silently would change every intensity downstream.

After W4 this is false for 8-bit 3-channel input at or below the bound. It is the fourth copy of exactly the claim cycle 4 raised as REQUIRED, surviving one file away — the failure mode the delta/power amendment's own note describes ("Review cycles repeatedly repaired figure-staleness at the sites a reviewer happened to name, and each time the same defect survived one copy away"). It matters because NOTES.md is the record the README's Design Decisions section draws from, and because this bullet list is *not* treated as frozen history: its immediate sibling, the `schema_version` bullet at `NOTES.md:1381`, was annotated in place with "**Since Phase 2 this is history:** …". The file's own convention is to annotate a superseded decision, and this one was not.

*Blocks the PR or carryable?* One-line annotation. I would fix it rather than carry it — carrying a DEBT line about a stale sentence costs more than editing the sentence — but it is not behavioural, so a human ruling to close at the cap and record it would not endanger anything measured.

**2. `/Users/sofiapetrusenko/blotquant/pipeline/load.py:251-253` (`_collapse_channels` docstring) — overclaims uniform refusal wording.**

&gt; Raises :class:`UnsupportedImageError` -- the same class, and the same "single-channel" wording, that this loader has always refused multi-channel input with -- for a shape the ruling does not cover, a bit depth its bound is not defined for, or a measured divergence above the bound.

Two of the three branches do carry the literal phrase (`load.py:260`, `load.py:287`). The bit-depth branch (`load.py:275-282`) does not — its message is `"… is a 3-channel image of pixel type {dtype}; the ruled channel collapse is defined only for 8-bit input …"`. The test author already noticed: `tests/test_pipeline_load.py::test_a_16_bit_three_channel_image_raises` matches on `"8-bit"`, not on `"single-channel"`, while the docstring above still says all three are worded alike. This is the same defect class cycle 4 was entirely about — a claim about the code directly beneath it that a reader would verify by reading three strings — and DEBT P2 entry (8) records that this project counts wrong claims as REQUIRED-grade (nine of Phase 4a's seventeen). Fix is one word in the docstring ("the same class, and — for the shape and divergence cases — the same wording") or adding the phrase to the third message.

*Blocks the PR or carryable?* The weaker of the two. Private-helper docstring, trivial fix, no behaviour or measurement depends on it. Reasonable to carry as a DEBT line if the human closes at the cap.

---

## SUGGESTED

1. **The README's Phase 3 status block is now visibly self-contradictory.** `README.md:101` says `3 — full evals + real-blot cross-validation | **not started**` and `README.md:110` says "`data/real/` does not exist". Both were already false on `HEAD` (I checked `git show HEAD:README.md` — pre-existing since the 3b-0 merge, not introduced here). But the new Limitations bullet at `README.md:161` links to `data/real/AMENDMENT_2026-08-19_channel_collapse.md`, so one document now both denies the directory exists and links into it. Worth correcting in this PR or opening a DEBT line; it is the surface PLAN.md's Phase 5 DoD is written about.
2. **`README.md:10`** still reads "**529 tests** on `main`, 627 on the unmerged `phase-4a-api` branch" and does not mention this branch's 796. `check_claims` pins the two figures for internal consistency but does not measure pytest, so nothing catches the omission.
3. **The committed CSV header notes are unpinned.** Mutation 4b showed this: changing `SECTION_9_QUOTE` failed only the verbatim-quote test; `test_the_committed_table_is_what_a_fresh_build_produces` compares *rows*, so the committed `blot_identity.csv`'s `#` header can drift from what `write_blot_identities` would now emit and nothing fails. Comparing the whole rendered file, not just the rows, would close it.
4. **`channel_divergence_dn` accepts `uint16`** (`pipeline/load.py:234`) and returns a number the docstring calls "DN", but for 16-bit input that is 16-bit DN, not the amendment's 8-bit-full-scale unit. No caller does this — `_collapse_channels` checks depth first — but the function is deliberately public, and the amendment's unit is the thing the whole ruling hangs on. Either restrict it to `uint8` or say in the return description which scale the number is in.
5. **`pytestmark = skipif(...)` on the three real-data test files** (`tests/test_real_channel_collapse.py:82`, `tests/test_designations.py:44`, `tests/test_blot_identity.py:47`) makes the entire file vanish silently if its artefact is missing. The artefacts are committed, so this never fires today — but a deleted `designations.csv` would take 19 tests with it and CI would stay green. One asserting test on presence, with the rest unconditional, would be stronger.
6. **NOTES.md does not record why `channel_collapse` went under `source` rather than the literal `provenance` block.** The amendment says "Record the operation in result provenance, in the style `roi_source` already uses for lane origin". I read that as endorsing `source` (`roi_source` lives on `lanes[]`, not in `provenance`), and the field name and shape are verbatim — so I am **not** calling this an amendment disagreement. But it is a placement judgement the record does not currently defend, and it is the sort a later reader will re-litigate.
7. **`tools/phase3/run_real.py` still reads its designation from a `crop_log.csv` column** and knows nothing about the two new tables. Correct as it stands — nothing in either is confirmed — but W7 needs the wiring, and it deserves a line in the handoff so it is not discovered mid-run.
8. **`data/real/provenance.md:4`** still claims "Every image listed is CC BY and single-channel", which the divergence measurement falsified for nine crops. Pre-existing and already recorded in `docs/pr/phase-3b0.md:120`; noting it only because it is the last surviving copy of the claim in a non-archival file.

---

## Mutation outcomes

All four produced failures. None passed silently.

| # | mutation | result |
|---|---|---|
| 1 | `CHANNEL_COLLAPSE_MAX_DIVERGENCE_DN = 2 → 3` | **13 failures**, spanning every layer that should have noticed. Synthetic boundary: `test_divergence_above_the_bound_raises_and_names_the_measurement[3]`, `test_the_bound_is_not_moved_by_a_single_dn`. Real-set regression: both `test_a_crop_at_the_bound_collapses_and_records_the_bound[…]`, `test_an_excluded_crop_is_still_refused…[PMC13102869_Fig1__B-FXN-VCL.png]` (the 3 DN crop crossing into the admitted band), `test_exactly_twelve_of_nineteen_crops_load`, `test_lossy_format_fires_on_no_crop_the_collapse_admits`. Plus the schema pin, the README pin, the API 415 test, and **both committed CSVs** (13 crops now measurable). This is the strongest result of the four — widening the bound cannot be made to pass by editing one test. |
| 2a | `confirmed_reference_label` returns the parsed candidate for a pending row | 3 failures: `test_asking_for_a_confirmation_on_a_pending_row_raises[…]` × 3 (Vinculin, Fib-CCN2, EMT-GAPDH). |
| 2b | any non-pending source accepted as confirmed (allow-list check removed) | 1 failure: `test_an_unrecognised_source_is_never_read_as_a_confirmation`. This is the "not pending, therefore confirmed" direction, and it is the only test that catches it — which is exactly what the test's own docstring claims. |
| 3 | `propose_blot_id` keyed on the PMC accession, not the figure stem | 4 failures: `test_two_figures_of_one_article_do_not_share_a_proposed_id` (the direct assertion), plus `test_the_committed_table_is_what_a_fresh_build_produces`, `test_the_table_round_trips_through_the_file`, `test_the_writer_refuses_a_destination_inside_the_gold_set` — the last three via `validate_blot_ids`, because `PMC13135410_Figure3` B and `PMC13135410_Figure4` B collapse to one id. The invariant is enforced at build *and* write, not only asserted. |
| 4a | README bullet's bound `2 DN → 3 DN` | 1 failure: `test_the_readme_states_the_bound_the_loader_enforces`. |
| 4b | `SECTION_9_QUOTE`: "second rectangle" → "separate rectangle" | 1 failure: `test_the_section_9_quote_is_verbatim_against_the_frozen_pre_registration`. (Notably the *committed CSV header* did not fail — see SUGGESTED 3.) |

Restoration confirmed after each: `git diff --stat` empty, `git status --short` unchanged at 28 entries, final full run exit 0.

---

## Other checks asked for

- **The 415 path genuinely reaches `UnsupportedImageError`.** I posted the colour fixture directly and printed the detail: it is the divergence branch of `_collapse_channels`, naming `43 DN` and `2 DN` and citing the amendment. Not passing for an unrelated reason. The collapsible-upload test also exercises the one input class that previously had no API coverage — a 200 with a rendered display, which is what makes `api/display.py`'s 2D assertion an assertion rather than an assumption.
- **Nothing in `api/` or `evals/` breaks.** Full suite green. No stale `1.2.0` pin survives anywhere (`pipeline/__init__.py`'s two mentions are historical prose). No literal `result_id` is pinned in `tests/`, `evals/`, `docs/` or `README.md`, so NOTES.md's claim that the id change is harmless is true.
- **Nothing was confirmed that may not be.** Both CSVs: every `source_of_designation` ∈ {`parsed_pending_human`, `parsed_ambiguous_pending_human`, `unparsed_needs_human`}, every `identity_source` = `proposed_pending_human`. Both readers use confirmation allow-lists, not pending deny-lists. The two departures from `parsed_pending_human` are disclosed in NOTES.md as a deviation and counted by a test.
- **Every number is traceable.** I spot-checked each: "12 crops, `lossy_format` on 0, all 19 parents `.jpg`" (two tests re-measure), "two of the twelve rows" (`test_exactly_two_rows_depart_from_the_plain_parsed_state`), "five of the 13 parent figures" (`test_the_module_docstrings_shared_parent_count_is_measured_not_asserted`), "at most 2 DN" in the README (pinned to the loader constant), "0.003% vs 0.8%" (2/65535 and 2/255 — both correct), DEBT's "Seventeen … 15 are Open" over 32 entries (`_check_register_composition` counts real `**Status.**` lines).
- **`tools/check_claims.py` still bites.** I mutated both arms of the new P2 check: `twelve → thirteen` deviations gave "a difference of 3 … should be 2"; `Ten → Eleven` entries gave "the markers … appear first in the order [1..10], which is not 1..11 in sequence". The split of the conflated PR-body/running-total quantity is sound, and counting markers from the register is genuinely stronger than the sentence-vs-sentence pin it replaced.
- **Boundaries.** No staged path under `data/ground_truth/`, `.claude/`, `configs/` or `synth/`. `DECISION_unit_of_analysis.md`, both amendments, and `crop_log.csv` are untouched (empty staged diff), and their digest pins still pass. `pipeline/` imports neither `synth/` nor `tools/`. No parameter moved; the bound is a module constant with the amendment as its authority and is echoed into provenance as `max_divergence_dn`. `§7`'s quote in `blot_identity.py` is verbatim against the frozen file (I diffed it by hand against `DECISION_unit_of_analysis.md:139-142`). Nothing touches the test split.
- **Coherence.** No leftover scaffolding. The read paths (`confirmed_reference_label`, `confirmed_blot_id`) are currently exercised only by tests, which I considered flagging as an unused abstraction and decided against: they are the guard W7 must pass through, and shipping the tables without them is what would let the next phase default silently. W7 correctly not done.

## VERDICT: CHANGES REQUIRED

Two items, both claim-accuracy, neither behavioural. The code, the schema, the tests and the tables are in good shape — the collapse rule is enforced on both sides of the bound at four independent layers, the tables confirm nothing, and mutation 1 in particular shows the bound cannot be moved without the build noticing in six places at once.

For the human's ruling at the cap: **REQUIRED 1** I would fix (one annotated sentence in `NOTES.md`, the exact defect cycle 4 raised, recurring one file away). **REQUIRED 2** is legitimately carryable as a DEBT line. Neither endangers any measurement, and neither is a reason to hold the PR if the alternative is a sixth cycle.
