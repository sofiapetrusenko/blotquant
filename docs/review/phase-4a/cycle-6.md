# Phase 4a — review cycle 6

> **Provenance.** Verbatim extract from a Claude Code session log.
> Source file: `e1cac5de-37df-4dc3-b3c1-ecaa0100139b.jsonl`
> Source sha256: `63af2401073c7616a6f8b9f73169a78830c7f160bfd8c4fb8a86bbf6f292338e`
> Location: JSONL record on line 835 of 906 (~92% into the file), `type: user`, timestamp `2026-08-18T08:58:52.883Z`.
> The reviewer ran as a subagent; its output reached the session inside a
> `<task-notification>` block, and what follows is the exact content of that
> block's `<result>` element — copied byte for byte, including any typos.
> Extracted 2026-08-18. The source log is retained locally and is NOT committed.

---

Working tree restored to its original state (no files added or modified by this review).

## REQUIRED

**1. `/Users/sofiapetrusenko/blotquant/DEBT.md`, S18, line 576 — CONTRADICTED**

&gt; "`_lane_edges` clamps the outer boundary to the image, so an edge lane whose centre sits within half a pitch of the border gets roughly `min_separation_px / 2`: measured, centres at columns 1 and 13 with a pitch of 12 yield a first lane **8 px** wide, not 12."

The diff yields **7 px**, not 8. `_lane_edges([1,13], 12.0, 256)` returns `[0.0, 7.0, 19.0]`; `detect_lanes` then computes `left = int(round(edges[0])) = 0`, `right = int(round(edges[1])) - 1 = 6`, `width = right - left + 1 = 7`. (I ran both: `_lane_edges` directly, and the ROI arithmetic at `pipeline/detect.py:558-573`.) 8 px is what centres at **2 and 14** give — an off-by-one on the `- 1` in `right`. The knock-on sentence two lines later, "the true margin against the shipped `profile_smoothing_px = 5` is about **8 against 5**, not 12 against 5", is wrong for the same reason: it is 7 against 5, and 6 against 5 for a centre at column 0 (`_lane_edges([0,12],12,256) → [0,6,18]`, width 6). This matters because the figure is explicitly labelled "measured", it is the entire quantitative content of the entry's multi-lane bullet, and the entry uses it to size how much headroom the unguarded detected path has. The direction of the error is the unsafe one — it overstates the margin.

**2. `/Users/sofiapetrusenko/blotquant/.git/PHASE4A_PR_BODY.md`, "## Register", first bullet — CONTRADICTED**

&gt; "**DEBT.md P2** goes six → seven deviations, entry (7) covering the 2026-08-17 ruling."

`DEBT.md` P2 in this diff reads "**What.** **Eight** deviations from PLAN.md have been made", and the same diff adds both entry (7) and entry (8). The PR body's own "Deviations from PLAN.md" section says so ("A fourth, ruled separately on 2026-08-18 and carried as DEBT.md P2 entry (8)"), and `DEBT.md`'s snapshot paragraph says "P2 gains entries (7) and (8)". The Register bullet is a stale mid-phase claim that survived the cycle-5 change. This is precisely the failure P1's *second standing rule* was added to prevent, in the section of the PR body that announces that rule.

**3. `/Users/sofiapetrusenko/blotquant/DEBT.md`, P1, lines 831-835 — CONTRADICTED**

&gt; "Phase 4a produced three instances and **none of them was a figure**, which is why the entry is no longer scoped to numbers."

The PR body, in the same diff, says: "applying that rule caught **three further stale claims** before this PR was opened — including **a quantization threshold stated as 65279 when the measured value is 65407**." That is (a) a fourth-through-sixth Phase 4a instance, and (b) a *figure*. The PR body's own review table also records 4 claim-text defects in cycle 4 and 2 in cycle 5, i.e. nine claim defects in the phase, not three. So P1's count is wrong and its stated rationale for the widening ("none of them was a figure") is false as written. Either P1 should say "three instances *caught by review cycles 1-3*, plus three more caught by the new rule, one of them a figure", or the PR body overstates — both cannot stand. I verified 65407 is right (`render_display` on `uint16` 65200-65535: first source value rendering 255 is 65407), so the correction itself is sound; it is the claim of "no figures" that fails.

**4. `/Users/sofiapetrusenko/blotquant/tests/test_api_display.py:118` (docstring of `test_the_record_states_how_many_source_dn_one_output_level_spans`) — CONTRADICTED**

&gt; "At 16 bits the top output level covers 257 source DN, so 65407 and 65535 render alike"

The top output level covers **129** source DN (65407…65535 inclusive), not 257. Interior levels cover 257 (e.g. 254 covers 65150…65406). `NOTES.md` in the same diff states this correctly and explicitly — "the top bin is half-width because the mapping rounds rather than floors, so 65406 renders as 254 and 65407 as 255" — so this is a direct file-against-file contradiction inside one diff. The docstring is justifying the assertion beneath it, and the reason it gives for "65407 and 65535 render alike" is the wrong one. (This file is outside the scope list's named files, but the scope says "especially" those and names cross-file self-consistency; downgrade if you read the list as exhaustive.)

## SUGGESTED

1. **`api/__init__.py`, module docstring** — "it never writes into `data/ground_truth/` — **every write goes through** `api.storage.ResultStore`". Not every write does: `api/app.py::_analyse_upload` does `image_path.write_bytes(payload)` into a `TemporaryDirectory`, bypassing `ResultStore` and `require_writable_destination`. The boundary claim itself holds (a `TemporaryDirectory` is never in the gold set), but the justification clause as written is false. "every write to a caller-visible location" or "every stored result" would be accurate.

2. **`api/display.py`, module docstring** — "the top output level **is one of those bins** rather than a single source value: at 16 bits every source value from 65407 up renders as 255." "One of those bins" points at the 257-DN bins just named, but the top level spans 129, which the reader can derive from "65407 up" in the same sentence. Internally inconsistent; `NOTES.md`'s "half-width" phrasing is the correct one and should be mirrored here.

3. **`NOTES.md`, "A supplied lane has a minimum size…"** — "`minimum_lane_extent_px(config)` is public so the tests take the number from it instead of restating it." True, but it presents as the design virtue exactly the property the PR body records cycle 3 as having falsified ("every test took its number from the function under test", so reverting the bound left all tests green). The NOTES section never records the fix — the impulse-response weight-matrix and 20 000-trial noise tests that pin the *reason*. A reader reconstructing the design from NOTES.md alone would take the falsified approach as current practice.

4. **`DEBT.md`, snapshot paragraph** — "**Four entries have been updated for it, and only four**". True at entry granularity (P1, P2, S18, E10). But the same diff also rewrote the "Where the weight actually sits" arithmetic (29→31, 14→16). Since this is the very paragraph corrected for misstating its own scope, "four entries, plus the summary arithmetic in 'Where the weight actually sits'" would close the residual gap rather than leave a reader to notice it.

5. **Claims about history that cannot be checked against the diff, and one of them is load-bearing.** The per-cycle review table (6/2/3/4/2), the 17/8/9 tally, "cycles 4 and 5 found **zero** behaviour defects", "all 605 tests passing" at cycle 3, "an earlier version of this docstring/paragraph said X", "the first cut …", "run on five successive states of this branch", "re-run green after every one of them", the human rulings of 2026-08-17 and 2026-08-18, and E10's "(3) was demonstrated by running two analyses" all rest on records that do not exist in the tree. **The load-bearing one is "cycles 4 and 5 found zero behaviour defects"**: it is the sole stated justification for the cap deviation recorded in `NOTES.md` ("The review cap was extended to a sixth cycle…"), `DEBT.md` P2 (8), and the PR body's Gates section — and the deviation is a deviation from PLAN.md's `Hard cap: 5 review cycles`. Nothing in the repository records any cycle's output, so a future reader has no way to audit the premise. Worth a line saying the cycle record is not in the tree, in the same spirit as P1's "Reported in review but not checkable from the tree" list.

## Claims I checked and found supported (so they are not re-litigated)

- **627 passed** on this branch and **529** on a `main` worktree — both run, both exact.
- **Falsification**: reverting `minimum_lane_extent_px` to `NOISE_ESTIMATOR_MIN_SAMPLES` fails exactly **5** tests (4 parametrisations of `test_the_minimum_lane_extent_is_the_shortest_profile_both_of_its_reasons_hold_at` plus `test_the_shipped_parameter_sets_bound_is_set_by_their_smoothing_window`).
- **Timings**: I re-measured on this machine — 1.16 s / 3.89 s / 22.28 s at 256×192 / 512×384 / 1360×1024, against the recorded 1.19 / 4.05 / 23.49. Reproduces.
- **Gold set is 40 images at 256 wide × 192 tall** — the cycle-5 transposition fix is right, and every timing table now uses width×height consistently.
- **Quantization**: 65407, 129 distinct values, 257 DN/level — all confirmed by running `render_display`.
- **"six caller sites, seven invariant sites"** — 6 `raise ReferenceBandError` and 7 `raise NormalizationError` in `pipeline/normalize.py`. Exact.
- **Register arithmetic**: 31 entries (18 S + 10 E + 3 P), 15 Accepted-or-Permanent, 16 Open, 29 through Phase 3, "remaining seven settled before Gate 1" (S9, S11, S13, E5, E9, P1, P2) — all check out entry by entry.
- **P2's Phase-3-owned open list** (S1, S3, S6, S15, E6, E8) — exactly the entries that are both `Open` and name Phase 3 in **Closes**. Correct in both directions.
- **PLAN.md quotes** are verbatim: "Hard cap: 5 review cycles; if not converged, stop and report the unresolved items instead of grinding"; "correction via draggable ROI edges AND numeric nudge fields (keyboard-accessible)"; "in under 2 minutes"; "Deploy: API on DigitalOcean, web on Vercel" in Phase 5. (`"because the images are small"` is a paraphrase in quote marks — PLAN.md says `(images are small)`; trivial.)
- **Cross-references all resolve and assert what is claimed**: `tests/test_pipeline_lane_rois.py::test_feeding_the_detected_lanes_back_reproduces_the_bands_exactly` (asserts `fed_back.bands == detected.bands` and that only `roi_source` differs); `test_the_openapi_document_describes_the_two_pass_flow` (asserts the flow's wording, not just the field name); `test_the_pipeline_never_imports_the_api` (AST); `tests/test_schema.py::test_result_schema_and_detection_declare_the_same_roi_source_vocabulary` (pins the tuple against the enum **in order**); `DISPLAY_BLOCK_KEYS` pinned against `as_block()`.
- **E10's precision holds**: `ANALYZE_DESCRIPTION` does document the temp path and does **not** document a size cap or timings. `RESULTS_DESCRIPTION` does say "a previously analysed result".
- **Cycle-5 fix landed**: `ANALYZE_DESCRIPTION` now says "returns the same **id**", not "the identical document", and separately names `created_at` and `source.path` as the two unstable fields.
- **`ruff check .`** clean; **`python -m evals.sweep --check`** exit 0 with the exact quoted string "structure, header and config digests exactly, every figure within its tolerance class".
- `profile_smoothing_px = 5` and `lane.min_separation_px = 12` in **both** shipped configs; the bound is 5 px under both.

## VERDICT: CHANGES REQUIRED
