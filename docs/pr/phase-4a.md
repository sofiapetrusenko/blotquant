## What was built

**Phase 4a** — the pipeline's caller-ROI input, the FastAPI service, and display rendering. PLAN.md's Phase 4 ("API + UI") is split: 4b is the Next.js UI and the deploy, and their absence here is deliberate.

### 1. Caller-supplied lane ROIs (`pipeline/`)

`python -m pipeline run <image> --config … --out … --lane-roi x,y,w,h` (repeatable). Supplying any rectangle switches lane detection off for that image: the rectangles given are the lanes, in the order given, and band detection inside them is unchanged — the same code with the same parameters. `tests/test_pipeline_lane_rois.py::test_feeding_the_detected_lanes_back_reproduces_the_bands_exactly` pins that by running `detect` without ROIs, feeding its own lane rectangles back, and asserting the `bands` tuple comes back identical with only `roi_source` differing.

**Provenance does not lie about where a lane came from.** Every lane object carries `roi_source: "detected" | "caller"`, always present — on the same argument that puts `excluded_from_normalization` on every band even when false: an absent value is indistinguishable from a writer that does not record it. The vocabulary is closed and enforced three times over: `DetectedLane.__post_init__` in process, the schema enum on the document, and a test pinning the tuple against the enum *in order*.

**`result_id` hashes the supplied ROIs.** It already hashed source digest, config digest and reference band ids, in order, precisely because two runs differing only in caller input would otherwise collide. Supplied ROIs are a fourth such input and a stronger case — they replace the lanes outright, so every ROI, intensity and ratio moves. Hashed in order, JSON-encoded rather than joined so no id is forgeable through a separator, with `null` encoded distinctly from any list.

⚠️ **Ids computed before this change do not reproduce after it.** Nothing is persisted anywhere, so nothing broke — but `GET /results/{id}` makes ids externally visible for the first time, so this is the last phase in which that sentence is free.

**Validation** rejects, each naming the offending rectangle by its 1-based position and coordinates: a malformed `x,y,w,h` string, non-positive extent, a rectangle not wholly inside the image, an overlapping pair (naming both), and a rectangle too small to carry a profile. `LaneRoiError` is its own class, separate from `DetectionError`, because the two say different things: one is a mistake in the request (400), the other is what the pixels do not contain (422).

### 2. Schema — 1.1.0 → 1.2.0

Three edits, all additive, each with its reason in NOTES.md:

1. `lanes[]` gains **required** `roi_source`, enum `["detected", "caller"]`.
2. `schema_version`'s `const` bumped, mirrored by `pipeline.RESULT_SCHEMA_VERSION`; `tests/test_schema.py` keeps the two pinned.
3. The top-level `description` records edit 1.

Adding a *required* field is still additive here because `schema_version` is a `const` — a 1.1.0 document was never going to validate against the 1.2.0 schema whatever else changed, so no existing document population is narrowed. Precedent: Phase 2 added a required `qc` block to `provenance.parameters`. `schema/ground_truth.schema.json` is untouched.

### 3. FastAPI (`api/`)

`POST /analyze` (image upload + config name + optional lane ROIs + optional reference band ids) and `GET /results/{result_id}`. Both return the same envelope:

```
{"result": <document valid against schema/result.schema.json>, "display": {…}}
```

The envelope is forced, not decorative: the result schema is `additionalProperties: false` at top level, so the display derivative cannot live inside the document — and should not, because the PNG is a rendering and the document is the measurement. **Every document is validated against the schema before it is returned**, and a document that fails is a 500, never a 200.

**The two-pass housekeeping flow** works over the API and is documented in the endpoint description (it renders in OpenAPI): analyse once under a mode needing no reference to learn band ids, then re-post the same image naming them. Tested end to end, and the OpenAPI test asserts the flow's *wording*, not just that a `reference_band_id` field exists.

**Errors** keep their actionable message verbatim — the messages name the offending rectangle, the config, the band id, the pixel type, and an HTTP layer that replaced them with "Bad Request" would discard the only half a caller can act on. The class name travels beside the message so a machine consumer can branch without parsing prose; the traceback does not travel. An unmapped `PipelineError` subclass defaults to **500, not 400**: an unclassified failure mode is one this service has not thought about, and blaming the caller for it would be a guess.

### 4. Display rendering — in `api/`, not `pipeline/`

The pipeline measures pixels and refuses to rescale them; that refusal is shipped behaviour with an error message. Rendering for a browser is presentation and sits on the other side of that line. `tests/test_api.py` enforces the direction by AST — `pipeline/` imports nothing from `api/`.

One mapping ships: linear full-scale, `out = round(px * 255 / max_value)`. It never clips, and a saturated source pixel maps to 255. **A percentile/window mode was deliberately refused**: windowing maps the brightest pixel *present* to 255, so an image peaking at 40% of full scale renders with pure white bands — and a viewer comparing that white against the *absence* of a `saturated` flag would conclude the flag had missed something. For a tool whose premise is that saturation must be visible, a display mode that manufactures apparent saturation is a defect, not a convenience. The accepted cost: a faint blot renders faint.

The response marks the PNG a derivative and records the mapping — name, formula, source and output maxima, that it scales, that it does not clip, and `source_dn_per_output_level` so a consumer cannot infer "255 in the PNG" ⇒ "saturated". The renderer *checks* the no-clipping claim rather than asserting it.

## Decisions taken in-phase (PLAN.md asks for these to be decided and noted)

- **Synchronous processing.** The pipeline runs inside the request. The alternative — a job queue plus a polling endpoint — adds a second source of truth for "what has this image been analysed as" without changing a single number. Handlers are `def`, not `async def`, so Starlette runs them in its threadpool.

  **But the premise PLAN.md allows this on does not hold at real image sizes**, and the measurement is new in this PR (`configs/default.yaml`, one arm64 machine, wall clock, width×height): **1.19 s** at the gold set's 256×192, **4.05 s** at 512×384, **23.49 s** at 1360×1024 (1.4 MP). Three back-to-back runs of the 1.4 MP case on an idle machine give 23.12-23.31 s; the same machine under concurrent load gave 33.28 s. So the magnitudes are the finding, not the digits. PLAN.md permits synchronous "because images are small"; a real gel-doc export is megapixels. Synchronous ships for 4a, where the caller is a test client or a developer. What it means for 4b's deploy is an open question below, and DEBT E10 carries it.
- **Config is identified by name from `configs/`**, not posted. A posted config widens the input surface from "one of a handful of reviewed parameter sets" to "any mapping the loader accepts", and `provenance.config_digest` is only worth something if the digest traces back to a file someone can read.
- **`GET /results/{id}` implies storage**: a filesystem store rooted at a directory the app is constructed with, never a module constant. The root passes `pipeline.analyze.require_writable_destination` — the same guard the CLI's `--out` passes — at construction, so a server configured to write into the gold set fails at start-up rather than on its first upload.

## Deviations from PLAN.md

**Five in total.** **Three from one human ruling on 2026-08-17**, recorded in NOTES.md's Phase 4a section and as DEBT.md P2 entry (7):

1. **Phase 4 runs before the remainder of Phase 3.** Phase 4 has no technical dependency on it — the pipeline it consumes has existed since Phase 2, and the ImageJ agreement numbers land in the README, not in the interface — while the remainder of Phase 3 is blocked on Gate 2, a manual CC-BY image search of unknown duration. **This discharges no Phase 3 obligation:** Gate 2 is untouched, no real blot has been read, and S1, S3, S6, S15, E6 and E8 all stay open and stay Phase 3's.
2. **Draggable ROI edges are dropped**, numeric fields becoming the correction mechanism in 4b. PLAN.md names the keyboard-accessible alternative in the same sentence, so the capability it specifies is delivered — but the cost is real and named: dragging is the faster gesture for a coarse correction, and PLAN.md's done-when is a 2-minute time bound that numeric entry now has to meet alone.
3. **Deploy moves from Phase 5 into Phase 4b**, because a phase that is "done" with nothing to open is not done.

**A fourth, ruled separately on 2026-08-18** and carried as DEBT.md P2 entry (8): **the review ran a sixth cycle, one past PLAN.md's hard cap of five**, narrowed to claim surfaces with behaviour out of scope. The reason is measured, not general — cycles 4 and 5 raised no new behaviour defect requiring a code change in this phase, so the code had converged and the record had not. (Hedged deliberately: cycle 4's item 3 did surface a behavioural issue, the silent result overwrite described under open question 3, but reported it as a false docstring claim and it is deferred to Phase 4b as DEBT E10 item 3.) See the Review outcome section for what the narrowing does and does not buy.

A fifth, smaller one, recorded in NOTES.md rather than as a P2 entry because it is a consequence of this phase's own work: **`NormalizationError` was split**, adding `ReferenceBandError` for the caller-caused raise sites. Phase 2's single class was right for a library and a CLI, where the caller and the service are the same person; over HTTP they are not, and a duplicate band id — an analysis invariant — would have been reported as a `400`, telling a caller to fix a request they got right. A subclass, so anything already catching `NormalizationError` keeps working. No ratio, denominator, exclusion or warning changed.

## New dependencies

| dependency | reason |
|---|---|
| `fastapi>=0.110` | the HTTP layer: `POST /analyze` and `GET /results/{id}` |
| `uvicorn>=0.27` | ASGI server `python -m api` runs the app on; 4b deploys it |
| `python-multipart>=0.0.9` | multipart/form-data parsing, which FastAPI requires for `UploadFile` |
| `httpx>=0.27` | **test-only** — the transport Starlette's `TestClient` runs on. Marked as such in `requirements.txt`; it belongs there because CI installs from that file and then runs pytest |

No imaging dependency was added: the display derivative is encoded with `opencv-python-headless`, already present.

## Gates

- `ruff check .` — clean.
- `pytest` — **627 passed**, up from 529 on `main` (measured against a `main` worktree, not estimated).
- `python -m evals.sweep --check` — **exit 0**: *"structure, header and config digests exactly, every figure within its tolerance class."* No recorded figure moved, and the config digests matching confirms no shipped parameter was touched. Run on five successive states of this branch.
- **Review: 6 cycles**, each in a fresh context. Cycles 1-5 on the full `git diff main`; **cycle 6 is one past PLAN.md's hard cap**, ratified by the human on 2026-08-18 and deliberately narrowed to claim surfaces only — NOTES.md, DEBT.md, this PR body, docstrings that justify behaviour, and the OpenAPI descriptions — with behaviour explicitly out of scope. Recorded in NOTES.md ("The review cap was extended to a sixth cycle, narrowed to the record") and as DEBT.md P2 entry (8).

### Review outcome, stated precisely

| cycle | REQUIRED | behaviour | claim-text |
|---|---|---|---|
| 1 | 6 | 5 | 1 |
| 2 | 2 | 1 | 1 |
| 3 | 3 | 2 | 1 |
| 4 | 4 | 0 | 4 |
| 5 | 2 | 0 | 2 |
| 6 (narrowed) | see below | not in scope | claim surfaces only |
| **total, cycles 1-5** | **17** | **8** | **9** |

**Cycle 5's two items were fixed after the cap.** They were re-reviewed by cycle 6, which is why cycle 6 was authorised. Both were claim-text: an OpenAPI sentence promising that "re-posting the identical request returns the identical document" (false — `source.path` and `created_at` are not hashed into the id), and the gold-set dimensions transposed as 192×256 in the new timing table when the gold set is 256 wide × 192 tall. Both were verified by running the code before fixing, and `ruff`/`pytest`/`sweep --check` were re-run afterwards.

**Why the cap was extended rather than the phase closed on unresolved items.** The cap exists to stop grinding on converged code. That was not the failure mode here: cycles 4 and 5 raised **no new behaviour defect requiring a code change**, so a full sixth cycle would have spent most of its effort re-reading code two consecutive reviewers had already passed. Cycle 6 is therefore a different review, aimed at the surface where defects were still being found. **What it cannot do is re-assure behaviour** — the behaviour assurance for this phase remains what cycles 1-5 produced.

Two findings worth surfacing on their own:

- **Cycle 3 proved a test-honesty failure rather than asserting one.** The new minimum-extent bound has two halves, and the load-bearing half — the one whose failure mode is silent — had no coverage: reverting `minimum_lane_extent_px` to the noise estimator's minimum left all 605 tests passing, because every test took its number from the function under test. Fixed with tests that pin the *reason* (the smoothing operator's weight matrix recovered by impulse response; 20 000-trial noise measurement) rather than the constant. Falsification re-run afterwards: the same revert now fails 5 tests.
- **Nine of the 17 REQUIRED items were wrong *claims* rather than wrong behaviour** — six in NOTES.md/DEBT.md, three in docstrings that justify behaviour. Examples: a carry-forward asserted but not performed; a bullet describing behaviour a cycle had already removed; a snapshot misstating its own scope; a list of "open Phase 3 items" wrong in both directions; a performance figure asserted in one file while another file in the same diff said nothing had been measured. **Neither of the last two capped cycles raised an item requiring a code change** — the code converged well before the record did. (Cycle 4's item 3 did surface a behavioural issue, the silent result overwrite, but reported it as a false docstring claim; it is deferred to Phase 4b as DEBT E10 item 3.) None was caught by the author re-reading; all were caught by a fresh-context reviewer comparing record against diff. That is why DEBT P1 was widened this phase and now carries a second standing rule, and applying that rule caught four further stale claims no reviewer had reached — including a quantization threshold stated as 65279 when the measured value is 65407. A sixth, narrowed cycle then found four more on top of those, so the ordering is: a fresh-context reviewer beats the author re-reading, and the author re-reading beats nothing.

## Open questions for the human

1. **Band ROIs — in scope for a later phase?** 4a made only lane rectangles supplyable, so the band object gains no `roi_source`. PLAN.md's Phase 4 done-when names correcting "one lane boundary", so lane-only matches the contract. If 4b finds users need band-level correction, three things follow: a band `roi_source` and another schema bump, a fifth `result_id` input, and — the one worth deciding explicitly — a caller-chosen band aperture turns DEBT S11's convention-dependence from a per-image property into a per-band one. Carried in NOTES.md's Open items, owned by 4b.
2. **Does synchronous processing survive 4b's deploy?** 23.5 s for a 1.4 MP image sits close to or beyond common proxy and browser timeouts. The options are a queue, a size limit low enough that synchronous stays honest, or accepting it and documenting it.
3. **What should `source.path` record for an upload?** It currently names a `TemporaryDirectory` joined to the client's filename, and that directory is gone by the time the response is sent. Because the path is not hashed into `result_id`, **two POSTs of identical bytes under different filenames share an id and produce different documents, and the second silently overwrites the first** — including a caller-supplied string rewriting stored provenance under an id another caller holds. Nothing measured is affected (`source.sha256` and every ROI, intensity, ratio and flag are stable), but `GET` is documented as returning "a previously analysed result" and does not reliably return the one previously analysed. Recorded as DEBT E10; not fixed here because it is a decision about what provenance should record, not a bug in the store.
4. **Is Phase 4a the trigger for DEBT S17?** S17 (detection can drop a peak with nothing recorded) closes at "whichever phase next touches the detection contract". This phase edited `pipeline/detect.py` — caller lanes, a provenance field, a minimum-extent bound — but did not surface the drop count, reading the trigger as meaning a phase that revisits *how peaks are found*. That reading is arguable and is recorded in DEBT's snapshot rather than left to be noticed.
5. **P3 (branch naming) is unaffected but adjacent.** This branch is `phase-4a-api`, which is not literally CLAUDE.md's `phase-N-<short-name>` since the phase is split. Flagging in case the convention should be amended to allow sub-phases.

## Register

- **DEBT.md P2** goes six → **eight numbered entries**, recording six → ten deviations. Entries (1)-(6) carry one deviation each; entry (7) is one ruling covering three, entry (8) the 2026-08-18 review-cap extension. **This phase contributed four deviations to P2, across those two entries.** The fifth deviation this PR reports — the `NormalizationError` split — is recorded in NOTES.md and deliberately not as a P2 entry, which is why the PR's "five" and P2's "ten" count different things.
- **DEBT.md P1 widened** from numeric claims to claims generally. The widening does **not** rest on the new failures being non-numeric — several were figures — but on the record, rather than the code, being this phase's dominant defect surface, with figures and prose failing there the same way. Of the claim defects, none of the first three was caught by the author re-reading their own work. A second standing rule was added: a behavioural claim written mid-phase must be re-read against the final diff, because "it was true when I wrote it" is not a defence when review cycles change the code underneath.
- **S18 (new)** — a lane narrower than the smoothing window yields an understated noise sigma. Closed for caller-supplied lanes by the new bound; **open for detected lanes**, where the single-lane branch can emit a rectangle a few pixels wide. Unreachable on committed data.
- **E10 (new)** — the API is not deployment-ready: no upload cap, the timings above, and the unstable `source.path`. Owned by 4b.
