# Phase 4a — review cycle 2

> **Provenance.** Verbatim extract from a Claude Code session log.
> Source file: `e1cac5de-37df-4dc3-b3c1-ecaa0100139b.jsonl`
> Source sha256: `63af2401073c7616a6f8b9f73169a78830c7f160bfd8c4fb8a86bbf6f292338e`
> Location: JSONL record on line 317 of 906 (~35% into the file), `type: user`, timestamp `2026-08-17T11:46:01.259Z`.
> The reviewer ran as a subagent; its output reached the session inside a
> `<task-notification>` block, and what follows is the exact content of that
> block's `<result>` element — copied byte for byte, including any typos.
> Extracted 2026-08-18. The source log is retained locally and is NOT committed.

---

I read the full diff (23 files), the whole `api/` package, all three new test files, PLAN.md, CLAUDE.md, and the NOTES.md/DEBT.md hunks. I verified two suspicions with targeted snippets (the FastAPI absent-form-field shape, and a degenerate caller ROI).

## REQUIRED

**1. `/Users/sofiapetrusenko/blotquant/NOTES.md`, lines 2691–2697 (Phase 4a → "Small decisions that would otherwise have to be reverse-engineered") — the design record states the opposite of what ships, on exactly the point cycle 1 changed.**

The bullet reads:

&gt; **An empty ROI list means "none supplied", not "supplied nothing".** `analyze_image` treats an empty sequence as `None` and runs detection. It is the HTTP shape that forces this: a form with no `lane_roi` field arrives as an empty list, not as an absent one…

Both halves are false against this diff:

- `/Users/sofiapetrusenko/blotquant/pipeline/analyze.py:164` is `supplied_lane_rois = None if lane_rois is None else tuple(lane_rois)` — `[]` becomes `()`, reaches `detect` → `caller_lanes` → `validate_lane_rois`, and raises `LaneRoiError`. `analyze_image`'s own docstring (lines 143–152) says so in as many words, and `tests/test_pipeline_lane_rois.py::test_an_empty_lane_roi_list_is_refused_rather_than_re_detecting` asserts it.
- The HTTP premise is wrong too. I ran a minimal FastAPI app with `lane_roi: Annotated[list[str] | None, Form()] = None`: an absent field arrives as `None`, not `[]`. `api/app.py` never sees an empty list, and `_analyse_upload` passes `None` through unchanged (line 205).

Why it matters: NOTES.md is the project's design record and CLAUDE.md/PLAN.md make it the source for README's Design Decisions. As written it documents a silent re-detection fallback — the exact class of behaviour this codebase forbids — as a deliberate decision, while the code correctly refuses. A reader reconstructing the contract from the record gets the wrong answer, and the repo now contradicts itself between `NOTES.md` and `pipeline/analyze.py`. Rewrite the bullet to record what shipped and why (one behaviour, one layer: `None` means "not supplying lanes", `[]` raises), and drop the false framework claim.

**2. `/Users/sofiapetrusenko/blotquant/pipeline/detect.py` — a caller-supplied lane rectangle under 2 px tall produces a 422 whose message names neither the rectangle nor a remedy the caller has.**

`validate_lane_rois` (line 271) checks emptiness, bounds and overlap, but not that a rectangle is large enough for a row profile to exist. A 1-row rectangle passes validation, reaches `_detect_bands_in_lane` → `profile_noise_sigma` (line 368), and raises `DetectionError` → 422 via `api/errors.py:101`. Verified:

```
Roi(x=0, y=0, width=60, height=1) -&gt; DetectionError : noise cannot be estimated from 1 sample(s):
... A one-column lane reaches this; raise detection.lane.min_separation_px
```

The supplied rectangle is a one-*row* lane, not a one-column one; `detection.lane.min_separation_px` has no effect on a supplied ROI; and over HTTP the caller cannot change any config value at all, because configs are selected by name (`api/configs.py`). Every other caller-rectangle failure in this diff names its 1-based position and coordinates — this one, uniquely, names neither. That message was written for a detector-internal condition and is inherited unchanged by a new caller-facing input surface, which is what makes it a defect of this diff rather than a pre-existing one. Add the minimum-extent condition to `validate_lane_rois` alongside the other three, reported in the same `lane ROI {position} ({_describe(roi)})` form.

## What I checked and found clean

- **Requirement 1.** `detect_lanes`, `_detect_bands_in_lane`, `_extent_threshold`, background estimation and every shipped config value are untouched; the only edit inside `detect_lanes` is the extraction of the identical 2D guard into `_image_shape`. `evals/sweep.py` calls `detect(image, config.detection)` positionally and is unaffected.
- **Requirement 2.** `roi_source` required on lanes, closed enum, mirrored by `ROI_SOURCES` and enforced in `DetectedLane.__post_init__` before serialisation. No `roi_source` on bands, correctly.
- **Requirement 3.** `_result_id` hashes four inputs, JSON-encoded, `null` distinct from any list, order-sensitive; tested for different-ROI, different-order and repeat-determinism.
- **Requirement 5.** Additive edit, 1.1.0 → 1.2.0, `tests/test_schema.py:144` pins the `const` to `pipeline.RESULT_SCHEMA_VERSION`. `schema/ground_truth.schema.json` untouched; `synth/` untouched; nothing outside `synth/` writes `data/ground_truth/` (`ResultStore.__init__` runs `require_writable_destination`, tested).
- **Requirement 7.** Rendering lives in `api/display.py`; `tests/test_api.py::test_the_pipeline_never_imports_the_api` enforces the direction by AST. The renderer *checks* its no-clip claim rather than asserting it, and `source_dn_per_output_level` closes the "255 in the PNG ⇒ saturated" inference. No dtype conversion destroys clipping evidence — QC runs on `loaded.pixels` independently.
- **The `ReferenceBandError` split, raise site by raise site.** All four raises in `_resolve_references` and both count checks in `_lane_denominator` are genuine request mistakes → 400. Everything left on the parent (`_resolve_lane_totals` both branches, the missing-lane total, repeated band/lane id, band naming an undetected lane, unknown flag) is built by `analyze_image` and unreachable from the wire → 500. I found no caller mistake now landing on 500 and no invariant landing on 400. The count checks are only reached when the lane holds ≥1 reference (the zero case is a `_LaneProblem` warning, not an exception), so they are always the caller's designation. `STATUS_BY_ERROR` is specific-first, so the subclass wins. `NormalizationError`'s "what does not raise" half is verbatim, so Phase 2's NOTES entry at line 1953 still points at something true. Nothing in `evals/` catches `NormalizationError` — I grepped — so the subclass direction costs nothing, and `test_an_invariant_break_is_not_reported_as_a_reference_band_mistake` pins both directions.
- **The `reference_band_ids` `[] → None` asymmetry you asked me to judge: defensible, not the same defect.** For lane ROIs the pipeline defines `None` and `[]` as different requests. For reference ids it does not — `_resolve_references` tests `if not reference_band_ids` and `_result_id` does `list(reference_band_ids or ())`, so `[]` and `None` are already the same value everywhere downstream. The collapse in `api/app.py:214` is a no-op over a distinction that does not exist, not a fallback masking one that does.
- **Test honesty.** The new tests would fail on regression, not merely execute. The round-trip test compares `fed_back.bands == detected.bands` on frozen dataclasses including the full `RowProfile` sample tuple, so any coupling of band detection to `roi_source` breaks it. The schema-gate test monkeypatches `analyze_image` to emit a forbidden top-level key and asserts `"result" not in body` — it forces the gate rather than pinning it. Display assertions are hand-computed numerics against decoded PNG pixels. `test_a_stored_result_comes_back_in_the_same_envelope` compares whole envelopes.
- No `except: pass`, no bit-depth squashing, no defaulted config keys, no test-split contact, no `pipeline/ → synth/` import, no generator special-casing, no Phase 4b code.

## SUGGESTED

1. `/Users/sofiapetrusenko/blotquant/pipeline/normalize.py:315-319` — the "no total-protein signal was supplied for lane" message still ends "so a missing lane is a **caller error** rather than an unnormalizable lane", while `_lane_denominator`'s docstring in this same diff reclassifies that raise as internal (500). Unreachable in practice, but the wording now contradicts the classification a reader is being taught.
2. `/Users/sofiapetrusenko/blotquant/api/app.py:214` — `list(reference_band_ids) or None` is dead (`reference_band_ids` is `()` or a non-empty list). Dropping `or None` removes the apparent asymmetry with `lane_rois` that any future reader will stop on.
3. No test asserts that a schema-invalid document is *not* written to the store. `POST` currently validates before `store.save`, which is right; a reorder would pass the suite.
4. `image.file.read()` reads the whole upload into memory with no cap. Worth a decision before 4b's deploy.
5. `test_the_openapi_document_describes_the_two_pass_flow` asserts two field names. Asserting the two-pass wording itself ("two posts") would pin the requirement rather than the vocabulary.

**VERDICT: CHANGES REQUIRED**
