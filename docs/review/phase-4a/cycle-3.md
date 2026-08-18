# Phase 4a — review cycle 3

> **Provenance.** Verbatim extract from a Claude Code session log.
> Source file: `e1cac5de-37df-4dc3-b3c1-ecaa0100139b.jsonl`
> Source sha256: `63af2401073c7616a6f8b9f73169a78830c7f160bfd8c4fb8a86bbf6f292338e`
> Location: JSONL record on line 396 of 906 (~44% into the file), `type: user`, timestamp `2026-08-17T12:10:00.603Z`.
> The reviewer ran as a subagent; its output reached the session inside a
> `<task-notification>` block, and what follows is the exact content of that
> block's `<result>` element — copied byte for byte, including any typos.
> Extracted 2026-08-18. The source log is retained locally and is NOT committed.

---

I read the full diff (23 files), the whole `api/` package, all three new/modified test files, PLAN.md Phase 4, CLAUDE.md, and the NOTES.md/DEBT.md hunks. I also re-derived the new bound against `smooth_profile`/`profile_noise_sigma` as written, and ran one targeted experiment.

**First, what checks out.** The minimum-extent derivation is *correct*, not merely plausible. `smooth_profile` pads with `np.pad(..., mode="edge")` and convolves `mode="valid"`, so output sample `i` averages padded positions `i … i+W-1`; at `L = W` the sample at index `pad` covers padded positions `pad … pad+L-1`, which are exactly the `L` original values — so the boundary claim ("at exactly `profile_smoothing_px` the centre sample does average that many distinct values, one shorter and no sample does") holds as stated, and `profile_noise_sigma`'s unconditional `/ sqrt(smoothing_px)` does understate sigma below it. Both dimensions genuinely become profiles (`raw_row_profile` = lane height, `raw_column_profile` = lane width), so applying the bound to both is right. `NOISE_ESTIMATOR_MIN_SAMPLES` is read by both the estimator's guard and the bound, so they cannot drift. Threading `DetectionConfig` into `validate_lane_rois`/`caller_lanes` broke no caller — the only production call sites are `detect` (`pipeline/detect.py:806`) and the four `evals/sweep.py` calls, which pass no `lane_rois`. `detect`'s stated guarantees still hold on the caller path (band ROIs are offset by `lane.roi.x/y` and bounded by the profile sizes; ids are `L{index}`-derived and unique). Requirements 1–7 are all met in substance. The detected-lane path is behaviourally untouched: `_image_shape` is a pure extraction, and shipped `profile_smoothing_px=5` vs `lane.min_separation_px=12` means no detected multi-lane rectangle can fall under the new bound anyway.

## REQUIRED

**1. `pipeline/detect.py:281` `minimum_lane_extent_px` + `tests/test_pipeline_lane_rois.py:121-220` — the half of the bound that this review cycle exists for is not pinned by any test.**

Every min-extent test takes its number *from the function under test* (`minimum_px = minimum_lane_extent_px(config)`), and the only hard-coded sizes in the suite are 1 px. So the `profile_smoothing_px` term — the half whose failure mode is silent rather than a crash — has zero coverage. I verified this rather than asserting it: with `minimum_lane_extent_px` replaced by `lambda cfg: NOISE_ESTIMATOR_MIN_SAMPLES` (i.e. the bound reverted to 2, exactly the pre-cycle-3 state for everything except a 1 px side), the **entire suite still passes, 605/605**. `test_a_lane_roi_at_and_just_above_the_minimum_extent_is_accepted_and_analyses` cannot catch it either, because it recomputes its rectangles from the weakened bound.

This is the tautology CLAUDE.md's "image-processing code without numeric assertions is untested code" is aimed at. What is missing is a test that pins the *reason*: e.g. assert that `smooth_profile(v, 5)` on a length-3 profile produces no sample averaging 5 distinct values while a length-5 profile does at index 2, and/or that `profile_noise_sigma(raw, 5)` on a sub-window profile returns a value materially below the noise the smoothed profile actually carries. Without it, a future edit to `minimum_lane_extent_px` that drops the smoothing term is invisible.

**2. `DEBT.md:32` — "Only P2 has been updated for it" is false in this same diff.**

The new Phase 4a header states: *"**Only P2 has been updated for it.** Every other entry still describes commit `52200a5`…"*. But this diff also rewrites **P1**: its title changes ("Numeric claims restated from memory" → "Claims restated from memory"), it gains a ~14-line "Widened in Phase 4a" block, and it gains a whole new "**Second standing rule, added in Phase 4a**". A reader auditing the register against the snapshot is told P1 is at `52200a5` when it is not.

This is precisely the failure class P1 is being widened to cover ("a claim about what the code **does** … re-read against the final `git diff`, not against the intent it was written from"), applied to DEBT.md itself. Two earlier cycles already produced REQUIRED items against the design record; this is a third instance and it is inside the entry that documents the first two.

Related, in the same entry: `DEBT.md:717` still opens *"Across all four phases, **figures** written from memory … have been wrong"* while the paragraph immediately below it states the Phase 4a instances involved **no figure**. Widen the opening sentence to "claims" (matching the new title) or scope it to three phases.

**3. `api/storage.py:62` `_read_json` — `errors="replace"` silently repairs a damaged store, contradicting the guarantee two docstrings above it.**

```python
text = _read_bytes(path, result_id).decode("utf-8", errors="replace")
```

`ResultStore.load`'s docstring promises to raise `CorruptStoredResultError` "when the files are all there and one of them cannot be read back as what was written", and `_read_json`'s promises that "an unreadable or truncated file … leaves as a PipelineError with an actionable message". `errors="replace"` does the opposite for one class of damage: bytes corrupted inside a JSON string become U+FFFD, the document parses, and it is **served as a 200** with silently altered content. For `result.json` the schema gate does not catch it (a mangled `band_id` or `note` still validates); for `display.json` nothing checks it at all. This is the "loud failure over silent fallback" rule inverted in the one module whose stated job is detecting corruption. Decode strictly and raise `CorruptStoredResultError` on `UnicodeDecodeError`, the same way `JSONDecodeError` and `OSError` are already handled.

## SUGGESTED

- **The detected-lane path carries the same hazard and is recorded nowhere.** This diff articulates, for the first time, that a profile shorter than `profile_smoothing_px` yields an understated sigma. `detect_lanes`' single-lane branch derives `pitch_px` from a measured column extent, so it can emit a lane 3–4 px wide, which then hits exactly that silent understatement — and `configs/default.yaml` already notes that `lane.extent_relative_height`/`extent_min_sigma` are "not selected at all, because no sweep can exercise them". I am **not** asking for a change to detection (PLAN.md forbids it here). I am suggesting a DEBT.md entry, since the diff's own reasoning establishes the weakness and the register is where known weaknesses live.
- **`api/configs.py:32` `ConfigCatalog.__init__` does not check the directory exists.** `create_app` validates `storage_root` at construction "so a misconfigured server fails here rather than on its first upload"; the same argument applies to `config_dir`. As written, `--config-dir /typo` yields a **400 `UnknownConfigError`** on every request — blaming the caller for a deployment defect, which is the exact inversion `api/errors.py` reasons against for `ConfigError` (500, "a deployment defect").
- **`api/app.py:313` `get_result` re-validates the result document but not the display block.** A `display.json` damaged into `{}` still parses, and `GET` would serve a PNG with no `is_derivative`, no `note` and no `mapping` — an unlabelled derivative, which requirement (7) exists to prevent. At minimum assert the required keys before returning.
- **`pipeline/detect.py:49` `ROI_SOURCES` claims to be "mirrored by the result schema's enum" and nothing tests it.** A one-line test comparing the tuple to `schema["…lanes…roi_source"]["enum"]` would pin the claim, in the same spirit as `test_result_schema_file_and_pipeline_constant_declare_the_same_version`.
- **Unused public surface:** `ResultStore.root` (`api/storage.py:89`) and `ConfigCatalog.directory` (`api/configs.py:41`) are read by nothing, including tests.
- **`result.source.path` records a path inside a `TemporaryDirectory` that is deleted before the response is sent.** It is documented honestly in `ANALYZE_DESCRIPTION`, and `sha256` is the real identifier, so this is not a defect — but a stored provenance field that can never resolve is worth a line in DEBT rather than only in an OpenAPI description.
- **`image.file.read()` (`api/app.py:282`) reads the upload unbounded into memory.** Not in scope for 4a, but worth naming before 4b deploys it.
- **`pipeline/__main__.py:90-93`'s new `[detected]` / `[caller]` stdout annotation is untested.** `test_the_cli_accepts_repeated_lane_rois` checks the written document, not the line printed.

## VERDICT: CHANGES REQUIRED

Items 1 and 2 are the load-bearing ones. Item 1 means the fix this cycle was convened to make is asserted only by its own plumbing; item 2 means the design record misstates its own scope inside the entry about misstating the record.
