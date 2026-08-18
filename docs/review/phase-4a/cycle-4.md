# Phase 4a — review cycle 4

> **Provenance.** Verbatim extract from a Claude Code session log.
> Source file: `e1cac5de-37df-4dc3-b3c1-ecaa0100139b.jsonl`
> Source sha256: `63af2401073c7616a6f8b9f73169a78830c7f160bfd8c4fb8a86bbf6f292338e`
> Location: JSONL record on line 507 of 906 (~56% into the file), `type: user`, timestamp `2026-08-17T13:45:53.255Z`.
> The reviewer ran as a subagent; its output reached the session inside a
> `<task-notification>` block, and what follows is the exact content of that
> block's `<result>` element — copied byte for byte, including any typos.
> Extracted 2026-08-18. The source log is retained locally and is NOT committed.

---

I have the full diff, both test files, the api package, and the NOTES/DEBT hunks. I verified several claims by measurement rather than reading.

---

## REQUIRED

**1. `api/__init__.py`, module docstring (line 4–6) — the timing figure that justifies the synchronous design is unmeasured and wrong by roughly an order of magnitude.**

&gt; "PLAN.md Phase 4 allows this for the MVP because the images are small (**a gel-doc export analyses in about two seconds**)"

Measured on this branch with `configs/default.yaml`:

| image | time |
|---|---|
| `data/images/dev_02.png` (192×256, the gold-set size) | 1.26 s |
| 512×384 | 3.86 s |
| 768×576 | 8.34 s |
| 1360×1024 (a small gel-doc export, 1.4 MP) | 15.6–23.5 s |

The parenthetical is not a decoration — it is the entire stated warrant for running the pipeline inside the request instead of behind a queue, and it is repeated as fact in NOTES.md ("Processing is synchronous, and the handlers are `def`…"). Worse, the same diff's `DEBT.md` E10 states **"No measurement: no load test has been run, and no size at which the service degrades is known."** The diff therefore asserts a performance figure in one file and denies having measured anything in another. This is precisely the failure P1 was widened in this diff to cover, committed in the diff that widens it. Either measure it and quote the measurement with its image size, or delete the figure and justify synchronous processing without one.

**2. `DEBT.md` P2, "Why (7) is not a licence to reorder further" — the list of "every open Phase 3 item" is wrong in both directions.**

&gt; "every open Phase 3 item in this register — S1, S3, S6, S9, S10, S15, E5, E6 — stays open, stays Phase 3's"

Checked against the same file's own `**Status.**` lines:

- **S9** — `**Status.** Accepted for now`
- **S10** — `**Status.** Accepted for the threshold`, and `**Closes.** Closed at Phase 3 Gate 1`
- **E5** — `**Status.** Accepted, with the mitigation named`

All three are also counted as `Accepted` by the summary paragraph twelve lines above, which this diff itself edited: "The remaining seven Accepted-or-Permanent entries (S9, S11, S13, E5, E9, P1, P2)…" and "six moved to Accepted at the gate (S2, S5, S7, S8, **S10**, S12)". Calling them "open" contradicts the register's own vocabulary and the 15/16 arithmetic in the same diff.

And it **omits E8** — `evals/history.md` does not exist, `**Status.** Open`, `**Closes.** Phase 3, which owns the iteration log`. E8 is a PLAN.md Phase 3 deliverable and the file CLAUDE.md requires a `synth/` break marker to be written into. A paragraph whose sole job is to prove that deferring Phase 3 discharges nothing must not drop the one Phase 3 item that another invariant depends on.

(For the record, the rest of the arithmetic does check out: I counted 18 S + 10 E + 3 P = 31 entries, 15 Accepted-or-Permanent, 16 Open. That part is correct.)

**3. `api/storage.py::ResultStore.save`, lines 125–127 — the docstring's justification for the destructive overwrite is false for the only caller of this class.**

&gt; "Overwrites an existing entry for the same id, which is safe precisely because the id is content-addressed: the same image, config, reference bands and lane rectangles produce the same document, **byte for byte, apart from `provenance.created_at`**."

`result.source.path` is in the document and is *not* an input to `_result_id`. Over the API it is `&lt;random TemporaryDirectory&gt;/&lt;client-supplied filename&gt;`. Demonstrated against this branch:

```
same id: True
path1: /var/folders/.../blotquant-upload-uk58ssuv/a.png
path2: /var/folders/.../blotquant-upload-eb81t65p/b_renamed.png
```

Two POSTs of identical bytes produce the same `result_id` and *different* documents, and the second silently overwrites the first in the store — including a caller-controlled string (the filename) rewriting the stored provenance under an id someone else already holds. `GET /results/{id}` is documented as returning "a previously analysed result", and it does not reliably return the one that was previously analysed.

`DEBT.md` E10 names the "path cannot resolve" half but not this half, and the docstring states the opposite of what happens. Fix the claim (and E10) to say what is actually stable — `source.sha256` — or stop putting a per-request ephemeral path in a content-addressed document.

**4. `DEBT.md` E10, "Evidence" — "Both are in the code as written and are documented in `ANALYZE_DESCRIPTION`" is false for the load-bearing half.**

`ANALYZE_DESCRIPTION` (`api/app.py`, lines 65–100) has four labelled sections: Lane ROIs, the two-pass housekeeping flow, Recorded source path, Reading a 422. The temp-path behaviour is documented there. **There is no mention anywhere in it of upload size, memory, or the absence of a cap.** E10 itself calls the size cap "the load-bearing half", so the claim that it is documented in the OpenAPI description is both wrong and wrong about the more consequential of the two.

---

## What I checked and found sound

Stating these plainly because three cycles preceded me and converging is a real outcome.

**The new property tests are honest.** This was the item cycle 3 was convened for, and the fix is not shallow. `_smoothing_weights` recovers the operator by pushing each unit impulse through `smooth_profile` itself — it is a black-box impulse-response recovery, not a re-derivation of the padding/convolution logic — and `test_the_recovered_smoothing_weights_are_the_operation_itself` validates the linearity premise against `smooth_profile` on an arbitrary profile before anything relies on it. `_distinct_samples_averaged` and `_noise_variance_factor` are then functions of that recovered matrix only. `_measured_noise_reduction` is an independent Monte-Carlo measurement, and the tests cross-check the two against each other (`rtol=0.03`). The hard-coded ratios are right: I recomputed the window-5 weights by hand and the four output samples give variance-factor ratios 1.4832, 1.1832, 1.1832, 1.4832 against the claimed `1/√5`, so `&gt;= 1.15` and `&gt;= 1.4` are true with margin and the docstring's "18% at the best sample and 48% at the worst" is exact. `test_the_minimum_lane_extent_is_the_shortest_profile_both_of_its_reasons_hold_at` pins the `profile_smoothing_px` term in both directions and `test_with_no_smoothing_the_bound_falls_back_to_the_noise_estimators_own_minimum` pins the other; together they pin the function rather than restate it. The revert falsification the orchestrator ran is the correct test and it fails as it should.

**No change to detection, background, or any shipped parameter.** `detect_lanes` differs only by the extraction of `_image_shape`; `profile_noise_sigma` differs only by `2` → `NOISE_ESTIMATOR_MIN_SAMPLES` (same value) and a message; `smooth_profile` is untouched; `configs/*.yaml` are not in the diff (`profile_smoothing_px: 5` in both, matching NOTES).

**Boundaries.** `synth/` untouched; `schema/ground_truth.schema.json` untouched; `pipeline/` imports nothing from `synth/` or `api/` and `tests/test_api.py` pins both directions by AST; `ResultStore` and `create_app` both go through `require_writable_destination` at construction; no test-split work.

**Requirements 1–7.** All present and numerically tested. `test_feeding_the_detected_lanes_back_reproduces_the_bands_exactly` is the right pin for requirement 1. The `roi_source` vocabulary is enforced three times over (dataclass `__post_init__`, schema enum, and `tests/test_schema.py` pinning the tuple against the enum *in order*). `result_id` hashes the ROIs in order with `null` encoded distinctly, tested. The empty-list-vs-`None` distinction is real and reachable, and I confirmed FastAPI delivers an absent field as `None`.

**Other NOTES/DEBT claims I verified against the repo:** the six `ReferenceBandError` raise sites and the seven remaining `NormalizationError` sites match the prose exactly; nothing in `evals/` catches `NormalizationError`; gold-set lane counts are 4/5/6 and never 1, so S18's unreachability claim holds; the "only four entries updated" claim matches the hunks; the Phase 4b open-question entry that cycle 1 found missing is now actually in `## Open items`.

---

## SUGGESTED

- **`api/app.py::_require_labelled_display`** checks key *presence* only, but its docstring says "unless `block` still labels its PNG". A record damaged to `{"is_derivative": false, "note": "", "mapping": {}, …}` passes and is served as a 200 — including a `note` that points at `mapping.source_dn_per_output_level`, the field a consumer needs to know that 255 is not saturation, now absent. Comparing against `DisplayDerivative.as_block()`'s invariant sub-keys would close it; at minimum, narrow the docstring to what the check does.
- **`api/__main__.py::main`** does not catch `PipelineError`, so `python -m api --config-dir typo` now emits a raw traceback where `python -m pipeline` prints the message to stderr and exits 1. Cycle 3's `ConfigCatalog` fix moved this failure to construction; the CLI wrapper did not follow. Loud, but inconsistent with the shipped precedent, and untested.
- **`tests/test_api.py`**: `test_an_image_with_no_lanes_is_a_422` is fully subsumed by `test_a_422_from_request_validation_is_told_apart_by_the_error_key`, which builds the same flat image and asserts the same status and `error` key plus more. One can go.
- **`DEBT.md` S18** attributes the detected-lane exposure to `detect_lanes`' single-lane branch only. The multi-lane branch derives widths from `_lane_edges`, which partitions on the median pitch and is bounded by `lane.min_separation_px: 12` rather than by `profile_smoothing_px`; the entry would be stronger if it said why 12 &gt; 5 makes that branch safe rather than leaving it unmentioned.

---

**VERDICT: CHANGES REQUIRED**

All four REQUIRED items are in the design record or in docstrings that justify behaviour, not in the measurement path — no number this project reports is wrong because of them. But two of them (1 and 4) are self-contradictions *within this diff*, and item 2 is a contradiction with the same file two paragraphs up. The diff's own P1 entry argues that a wrong design record is more dangerous than a wrong figure and that nothing mechanical covers prose; that argument applies to these four.
