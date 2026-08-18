# Phase 4a — review cycle 5

> **Provenance.** Verbatim extract from a Claude Code session log.
> Source file: `e1cac5de-37df-4dc3-b3c1-ecaa0100139b.jsonl`
> Source sha256: `63af2401073c7616a6f8b9f73169a78830c7f160bfd8c4fb8a86bbf6f292338e`
> Location: JSONL record on line 639 of 906 (~71% into the file), `type: user`, timestamp `2026-08-17T14:00:17.979Z`.
> The reviewer ran as a subagent; its output reached the session inside a
> `<task-notification>` block, and what follows is the exact content of that
> block's `<result>` element — copied byte for byte, including any typos.
> Extracted 2026-08-18. The source log is retained locally and is NOT committed.

---

I have the full diff, the api package, all new tests, PLAN.md, CLAUDE.md, and the NOTES.md/DEBT.md hunks. I verified the numeric and behavioural claims by running code rather than reading it.

## REQUIRED

**1. `/Users/sofiapetrusenko/blotquant/api/app.py`, `ANALYZE_DESCRIPTION` (line 70) — the caller-facing OpenAPI text asserts document identity that does not hold.**

&gt; "The `result_id` in the response is content-addressed over the image bytes, the config digest, the reference band ids and the supplied lane ROIs, so **re-posting the identical request returns the identical document** and the same id."

Ran it. Two POSTs of the same bytes, same filename, same config:

```
identical request -&gt; same id: True
identical document: False
source.path differs: True
   .../blotquant-upload-yaklypog/same.png | .../blotquant-upload-to18bmzb/same.png
created_at differs: True
```

`source.path` is a fresh `TemporaryDirectory` per request and `provenance.created_at` is a clock read; neither is hashed into the id and neither is stable. This is the *same* false claim cycle 4 caught in `api/storage.py::save` and that DEBT.md E10 item 3 now records — corrected in the docstring one file away, left standing in the OpenAPI document, which is the copy a caller actually reads. The description's later "Recorded source path" paragraph discloses the temp path but does not retract the sentence above it. A consumer diffing two responses to confirm reproducibility will find them unequal and have no way to tell that from a genuine defect. Fix: say what is stable (the id, and every measured field) and name `source.path` and `created_at` as the two that are not, the way `storage.save` now does.

**2. `/Users/sofiapetrusenko/blotquant/api/__init__.py` line 16 and `/Users/sofiapetrusenko/blotquant/DEBT.md` E10 item 2 — the gold-set dimensions in the new timing table are transposed.**

Both read "192x256 (the gold-set size)" / "1.19 s at the gold set's 192×256". The gold set is **256 px wide × 192 px tall** — all 40 ground-truth documents carry `"width_px": 256, "height_px": 192`, and `cv2.imread('data/images/test_00.png').shape` is `(192, 256)`.

The repo's convention is width×height, set in this same phase: `validate_lane_rois` emits `f"which is {width_px}x{height_px} px"` and `tests/test_api.py` asserts `"200x120"` for a 200-wide, 120-tall fixture. The table's other two rows follow it (512×384 and 1360×1024 are landscape), so the table is internally inconsistent: one row reads H×W and two read W×H.

The measured seconds are fine — I reproduced 1.03 s / 4.55 s / 33.28 s on this machine, so the table's magnitudes and its order-of-magnitude conclusion stand. But this is a figure written from intent rather than read from the artefact, in the entry cycle 4 required precisely because the previous claim was unmeasured, and in the same diff that widens P1 to cover it. Two characters in two files.

## SUGGESTED

- **DEBT.md S18, the multi-lane safety argument.** "two centres cannot be nearer than `detection.lane.min_separation_px = 12`. Since 12 &gt; the shipped `profile_smoothing_px = 5`, a multi-lane rectangle cannot fall under the bound." The conclusion is right; the inequality is not the binding one. `_lane_edges` clamps the outer boundary to the image, so an edge lane whose centre sits within half a pitch of the border gets a rectangle of about `min_separation_px / 2`, not `min_separation_px` — with `c0 = 1, c1 = 13` the first lane is 7 px wide, not 12. The real margin is 7 vs 5, not 12 vs 5, and the recorded trigger condition ("a config that narrowed the separation ... would silently reopen it") therefore misclassifies `min_separation_px` of 6 or 7 as safe when the edge lane would be 4 px. Since the entry's purpose is to tell a future maintainer when the guard breaks, state the bound as roughly `min_separation_px / 2`.
- **`README.md` lines 185–197** document the CLI including the repeatable `--reference-band`, and do not mention the new repeatable `--lane-roi`. PLAN.md gives the README to Phase 5, so this is not a Phase 4a obligation, but the interim README is now one flag behind the shipped CLI surface.
- **`api/__init__.py` line 28**, "a 23-second request is inside no default proxy or browser timeout by much", is a double negative that takes a second read. DEBT E10's phrasing ("sits close to or beyond common proxy and browser timeouts") is clearer; reuse it.
- **`api/app.py::get_result`** passes `_stored_document_cause(result, result_id)` as an eager argument, so the message is formatted on every successful GET. Harmless, but a lambda or a deferred call would keep the happy path free of it.

## What I checked and found clean

Contract vs PLAN.md and the phase brief: all seven requirements are met, including the derived-not-picked minimum lane extent, the `roi_source` enum mirrored between `pipeline.detect.ROI_SOURCES` and the schema (pinned by `tests/test_schema.py`), and the 1.1.0→1.2.0 bump with the schema `const` and `pipeline.RESULT_SCHEMA_VERSION` locked together. No path outside `synth/` writes `data/ground_truth/` — `ResultStore.__init__` runs `require_writable_destination` at construction and there is a test. `synth/`, `configs/`, `evals/` are untouched; no shipped parameter value moved; `detect_lanes` changed only by an extracted `_image_shape` and the `roi_source=` argument. `pipeline/` does not import `synth/` or `api/`, `api/` imports neither `synth/` nor `evals/`, and both directions are asserted by AST-walking tests. No generator special-casing; no test-split contact.

No silent fallbacks: `parse_lane_roi` refuses floats, short lists and blanks rather than coercing; `[]` and `None` are genuinely different requests all the way down and the empty case raises; `_read_json` decodes strictly and the U+FFFD-repair hazard has a test that asserts the band id was not rewritten; `render_display` checks its own "clips: false" claim instead of asserting it. Every raise carries an actionable message naming the offending rectangle by 1-based position and coordinates. The `ReferenceBandError` split is correct — 6 caller-fault raise sites moved, 7 invariant sites stayed, and `test_an_invariant_break_is_not_reported_as_a_reference_band_mistake` pins the direction.

Cycle 4's four fixes are complete and introduced nothing. The `PipelineError` catch in `api/__main__.py::main` is scoped to `create_app` only, so a uvicorn failure still surfaces normally, and it has a test asserting exit 1, the `error:` prefix and no traceback. The removed `test_an_image_with_no_lanes_is_a_422` is fully subsumed by `test_a_422_from_request_validation_is_told_apart_by_the_error_key`, which asserts the same 422 plus `error == "DetectionError"`. `storage.save`'s corrected docstring is accurate — I reproduced the same-id-different-filename overwrite. DEBT's arithmetic is right: 31 entries, 15 Accepted/Permanent and 16 Open by its own status lines, and the corrected Phase-3-owned open list (S1, S3, S6, S15, E6, E8) is exactly the intersection of Open status with a Phase 3 `Closes` line — S9/S10/E5 are indeed Accepted and E8 was indeed missing. Test honesty is unusually good: the minimum-extent bound is justified by measuring the smoothing operator's weight matrix and 20 000 noise trials rather than by restating the constant, and determinism is tested with supplied lanes.

**VERDICT: CHANGES REQUIRED** — two items, both in claim-text rather than behaviour, both small. If the human prefers to ship, item 2 is cosmetic and item 1 is a one-sentence edit to an OpenAPI string; neither touches a measured number.
