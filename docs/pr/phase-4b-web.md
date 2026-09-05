## What was built

[Sofia to write]

The frontend, `web/`: a Next.js 15 static export with two paths. A **gallery** of six
precomputed lane cards, each one caller-supplied ROI measured through the shipped API and
committed as data. And **`/analyze`**, an upload → lane-ROI → result flow against a live API.
The one thing the UI derives is the per-lane verdict, and it derives it by mirroring
`api/display.py` in TypeScript — which is this phase's one deviation from a ruling, taken
knowingly and pinned against divergence rather than trusted.

## Scope and what governed it

Nothing outside `web/`, `tools/gallery/`, two new test modules and `NOTES.md` was touched.

| boundary | state on this branch |
|---|---|
| `api/` | untouched |
| `pipeline/` | untouched |
| `synth/` | untouched |
| `data/` | untouched |
| `data/ground_truth/` | untouched; no write path added anywhere |
| `schema/` | untouched |
| `configs/` | untouched |
| `evals/` | untouched |
| `docs/PRE_REGISTRATION_*` | untouched (digest still pinned; `check_claims` exits 0) |
| `tools/check_claims.py` | untouched |
| `requirements.txt` | untouched — no new Python dependency |
| `.github/` | untouched |
| `pipeline/` importing `synth/` | none |
| test split | not read, not tuned on |

## The verdict, and the one deviation

The verdict is per lane, ruled by Ruling 1 of 2026-08-25, pre-registered in
`docs/PRE_REGISTRATION_2026-08-25_verdict_mapping.md` §2 and implemented in
`api/display.py::lane_verdicts`. The UI derives that and nothing else.

**The deviation, stated plainly and not softened.** Ruling 1 says the derivation is *"computed
in api/display.py"*. `web/lib/verdict.ts` computes it a second time, in the browser. It is
adopted because the API does not serve the verdict — §5 of the pre-registration leaves that to
a later ruling — and the live `/analyze` path must show a verdict for a result that was never
stored. **The mirror is debt, not a design.** It closes when the human rules on serving the
verdict from the API, which is open question 1.

**Two implementations of one ruled mapping can diverge silently, so they are pinned.**
`tools/gallery/verdict_fixtures.py` runs the **Python** function and records what it actually
did; nothing in the fixture set is hand-written. It emits:

| fixture group | count *at this commit* | what it pins |
|---|---:|---|
| synthetic verdict documents | 18 | the three ruled classes, both `blocked_reason` values, E1–E5, E7, E8, the ruling's mutation walk, both flag-ordering rules |
| committed gallery documents | 6 | every card the site ships |
| refusals (`errors/`) | 9 | E6 and every documented raise path; the generator refuses to write one for a document the Python does not actually refuse |
| `vocabulary.json` | 1 | `BAND_QC_FLAGS` itself, emitted from the Python's own tuple |
| `divergences.json` | 28 cases | what the Python observably does with schema-invalid documents |

Those are **counts measured at this commit**, not the floors of the same name. Three of them
happen to equal `MINIMUM_SYNTHETIC_FIXTURES`, `MINIMUM_ERROR_FIXTURES` and
`MINIMUM_DIVERGENCE_CASES` today, and that coincidence is exactly how a floor got quoted as a
census twice in this phase already. The floors guarantee only that the sets have not been hollowed
out; the sizes are whatever the generator emits.

`web/test/verdict.test.ts` asserts the TypeScript reproduces every one, flag order included, and
`tests/test_gallery_verdict_fixtures.py` fails `pytest` if the fixtures drift from
`api/display.py`.

**Which leg enforces what, since it decides how much open question 2 costs.** The `--check` leg is
a regeneration and a tree diff: it catches the fixtures drifting from `api/display.py`, and that
runs under `pytest`. The guarantees that the fixture set has not been *hollowed out* — the count
floors, the required name prefixes, the non-empty gallery set and the `vocabulary.json` equality —
live in `web/test/verdict.test.ts`, which is the leg that **runs in no CI job**. So the half most
in need of automation is the half without it.

**What the pinning does not reach**, because it should be said rather than implied: a document
shape no fixture contains. The two implementations could still disagree on an input neither has
been shown. That is the standing residual of duplicating a mapping instead of serving it.

**Two holes were found in this mechanism during review and closed.** The flag *vocabulary* was
retyped in TypeScript with nothing asserting the two agreed — it is an input to the mapping, not
an output, so no document-plus-verdict fixture could ever have caught a difference. And the prose
list of Python/TypeScript divergences was wrong on three separate occasions before it was replaced
by a generated table, which then immediately contradicted one of the claims it replaced.

## The gallery is the ruled composition

`docs/pr/phase-4b0.md` ("Gallery composition, ruled and confirmed by image", recorded at `77e6597`
— a commit on `phase-4b0`, which is this branch's base; it is not a merge commit and is not yet on
`main`, whose head is `1d1f742`) fixes the cards and their order. The manifest first shipped diverging from it in four ways, three
recorded nowhere. **All four are resolved by implementing the ruling.**

| # | id | verdict | ruled group |
|---:|---|---|---|
| 1 | `pmc12895598-fig3a-l3` | ■ blocked · `all_ratios_excluded` · `saturated` | blocked |
| 2 | `pmc12895598-fig3a-l2` | ■ blocked · `all_ratios_excluded` · `saturated` | blocked |
| 3 | `pmc12895598-fig3a-l4` | ■ blocked · `all_ratios_excluded` · `saturated` | blocked |
| 4 | `pmc13135410-fig3b-l11` | ▲ flagged · `saturated, overlapping, unresolved_shoulder` | flagged |
| 5 | `pmc12895598-fig3a-l5` | ● pass · 0 flags | detection beta |
| 6 | `pmc13135410-fig4c-l8` | ● pass · 0 flags | pass |

Read from `web/public/gallery/index.json`, not from memory.

- **Two rejected ROIs had been shipped as the flagged cards.** The ruling rejects
  `PMC13025488_Figure5__C` `L7` and `PMC12686555_FIGURE1__A` `L3` because each spans two sample
  lanes. Both were live, presenting to every visitor — as examples of the tool working — two ROIs
  known to be measuring two lanes at once. Removed. That is enforcement of a ruling, not a
  selection.
- **An addition the ruling never names**, `pmc12956003-fig2a-l2`, was shipped. Removed.
- **The ruled pass card is `L8`**, and it was unshippable while no caller-ROI rectangle for it
  existed; a substitute (`L3` of the same crop) had been shipped instead, which also left the
  ruled order with no detection-beta group to order. The rectangle has since been measured.

### Where the `L8` rectangle came from

`runs/4b0/CALLER_ROI_MEASUREMENT.md` carries no `L8` **rectangle**, so it was measured on
2026-09-04 at this tree by that document's own §1 method — re-run the detected path and read the
lane's rectangle. `L8` does appear in that file's §5 and §6 tables; what it is absent from is §1,
the ROI table, the only place a caller rectangle is recorded. Section numbers here are that
gitignored measurement artefact's — **the ruling itself has no numbered sections**, and calling
one of these "the ruling's §6" would conflate a measurement with a ruling.

```
python -m pipeline run data/real/crops/PMC13135410_Figure4__C-PDGFRa-GAPDH.png \
    --config configs/default.yaml --polarity dark_on_bright --out <tmp>/
```

giving **`L8 = 272,0,36,122`**. The same run returned **`L3 = 123,0,33,122`**, reproducing the §1
table exactly — that control is the evidence the method recovers the rectangles §1 recorded rather
than some other set. Submitted through the live HTTP API on the caller path, `272,0,36,122`
returns:

```
verdict pass · 3 bands · 3 ratios · 3 usable · qc_flags [] · image_qc_flags []
L0_B0  {281,35,1,11}   0.011145096539858537
L0_B1  {283,48,25,27}  0.2839418849168419
L0_B2  {275,84,29,13}  0.3036417510992162
```

That matches §6 wherever §6 says anything — its band and ratio counts, and its three ratios at the
6 decimal places it quotes. It is deliberately **not** described as matching "digit for digit".
§6's four columns are *card*, *rows the panel has*, *bands detected in the ROI* and *ratios the
card would show*, so of the six figures above exactly **two** are checkable against it. The other
**four** — the verdict, the usable-ratio count, `qc_flags` and `image_qc_flags` — are this
measurement's alone and have nothing in §6 to be compared with.

A measured rectangle, not an invented one.

### The detection-beta card is not softened

`PMC12895598_Fig3__A` `L5` is the printed molecular-weight label column: its five "bands" are the
five printed MW labels. Detection locates printed type and quantifies it cleanly, and QC has
nothing to say about it — every flag in the vocabulary qualifies a *measurement*, and printed type
measured cleanly is a clean measurement of printed type. So the derivation returns `pass`,
correctly about the flags and silent about what was measured.

The card ships that `pass` with no field added to `index.json` and no softening, and discloses in
its title that the region is printed labels rather than a sample lane. A gallery that omitted its
own known detection failure would be applying the tool's central claim to everything except
itself.

The pass card shows **all three** of its ratios including `0.0111451`, and names the one-pixel
detection behind it — derived from `bands[i].roi`, with its JSON path, not from the ruling's
prose. No parameter moved to suppress it, and none may (Gate 1 ruling 3).

## Deviations from the task text

1. **The verdict is mirrored client-side.** Above. Open question 1.
2. **`no_ratio_emitted` is not representable from this corpus.** It is one of the two ruled
   `blocked_reason` values and occurs **zero times across all 116 corpus lanes**. No ROI was
   invented to manufacture one; it is pinned in the synthetic fixtures only, because there is
   nothing real to pin it against.
3. **Config names are hardcoded** as `default` and `rolling_ball` in the upload step, as the task
   permits. The API exposes no endpoint listing `configs/`, so the alternative was inventing one
   in `api/`, which was out of scope.
4. **`/analyze` gained a lane auto-detect step**, posting with no `lane_roi` and dropping the
   result into the same editable rows. It is beyond the task text and inside PLAN.md Phase 4,
   which asks for "auto-detected lanes/bands overlaid on the image → correction via ... numeric
   nudge fields → recompute".
5. **The live browser proof did not run.** See Gates.
6. **ROI correction ships as numeric fields only, not draggable edges.** PLAN.md Phase 4 asks for
   correction "via draggable ROI edges **AND** numeric nudge fields". Rectangles are drawn by
   dragging on the surface, and every existing rectangle is corrected through the keyboard-
   accessible numeric inputs; dragging an *edge* of a placed rectangle is not implemented. The
   numeric route is the accessible one and is the one under test — the drag route is not covered
   by any test, because no browser could be driven (see Gates). Declared here because CLAUDE.md
   requires deviations in the PR body; also recorded in `NOTES.md`.

## New dependencies

**Nothing added to `requirements.txt`.** `tools/gallery/build.py` imports `httpx` and `PyYAML`;
`tests/test_gallery_build.py` imports `jsonschema` to validate the committed documents. All three
were already present. `httpx`'s requirements.txt comment marks it TEST-ONLY; `build.py` adds a
developer-tool use, still not a runtime one.

JavaScript, all in `web/package.json`:

| package | why |
|---|---|
| `next`, `react`, `react-dom` | the framework the phase specifies |
| `typescript`, `@types/{node,react,react-dom}` | types; `tsc --noEmit` is a gate |
| `tailwindcss`, `@tailwindcss/postcss`, `postcss` | Tailwind v4, as specified |
| `eslint`, `eslint-config-next`, `@eslint/eslintrc` | `pnpm lint` at `--max-warnings 0` |
| `vitest`, `@vitejs/plugin-react`, `jsdom` | the test runner the gates require |
| `@testing-library/{react,user-event,jest-dom}` | the ROI-binding and refusal tests drive real components |

## Gates

- `ruff check .` — clean.
- `pytest` — **1034 passed**, 1 pre-existing warning (Starlette's `httpx` deprecation).
- `pnpm lint`, `pnpm typecheck` — clean.
- `pnpm test` — **170 tests**.
- `pnpm build` — static export, **zero warnings**, six card routes.
- `tools/check_claims.py` — exit 0; the ratified pre-registration's digest still matches.
- Gallery rebuilt from the manifest against a live `python -m api --storage-root results/
  --config-dir configs/`; all six documents schema-validated against `schema/result.schema.json`
  unrelaxed.

### What was proven, and what was not

**Proven.** A live `POST /analyze` over HTTP returning the ruled measurement for `272,0,36,122`
(quoted above), run against the API directly. **Stated precisely, because the distinction is the
whole of open question 3:** that request carried an `Origin: http://localhost:3000` header and the
service answered `200` — but it answered with **no `Access-Control-Allow-Origin` header**, because
there is no CORS middleware. `curl` does not enforce the same-origin policy and a browser does, so
what this proves is that the *service* answers correctly, **not** that a browser could reach it.
It could not. The full `/analyze` flow driven
through the real page components across all three steps, with the ROI entered through the numeric
inputs alone — which is also the keyboard route — asserting the `FormData` the client built and
the rendered verdict and ratio values.

**Not proven.** No browser was ever driven. No Chrome extension is connected to this machine
(`list_connected_browsers` returns `[]`, verified directly, not inferred from a failed call).
**Layout, focus order and the pointer-drawing path have been seen by nobody.** The keyboard path
through `/analyze` is covered by tests; the mouse path is not.

### Review outcome

Fresh-context reviewer on the full staged diff each cycle.

| cycle | REQUIRED | behaviour / test-honesty | claim-text |
|---|---:|---:|---:|
| 1 | 3 | 0 | 3 |
| 2 | 12 | 7 | 5 |
| 3 | 5 | 1 | 4 |
| 4 | 4 | 2 | 2 |
| 5 | 1 | 0 | 1 |

**Cycle 1** found three wrong claims and no behavioural defect: a test docstring naming a gallery
card that no longer exists and calling a by-hand run "the same measurement"; two operator-facing
messages in `build.py` pointing at a `PROPOSED` marker the manifest does not carry and at
`runs/4b0/` for rectangles it does not all hold; and `NOTES.md` contradicting itself twenty lines
apart about whether the gallery is a proposal and whether the pass card's rectangle exists.

**Cycle 2 is the one that earned its keep**, and it did so by mutating source and showing the
suite stayed green. Two findings were serious:

- **No test asserted that a `blocked` or `flagged` verdict is ever rendered.** Hard-coding
  `verdict="pass"` into either result surface passed **145/145**. `VerdictBadge` was imported by
  no test and `web/app/page.tsx` — the front page, which renders three blocked cards and one
  flagged — had no test at all. A build showing a green ● Pass on three saturated, correctly
  refused lanes would have passed every check in this repository. The verdict is the product.
- **Nothing pinned that the committed gallery document is the API's document unedited.** Stripping
  `image_qc_flags` before writing left all **1030** tests green. That invariant is the entire basis
  for the claim that the numbers on the site are the ones the service produced.

Five more were unpinned guards — `validate=True` on the base64 decode, `flag_count`, and three
loud-failure refusals that could each be deleted with the suite green — and five were wrong
claims, including two invented numbers presented as "taken from the committed cards" and
"merged at `77e6597`", which is neither a merge commit nor on `main`.

Every mutation was re-run to failure after the fix. **I re-ran the badge mutation independently:
it now fails 4 tests, and the tree restores byte-identical to 164/164 green.** The suite grew from
145 to 164 tests as a result of this cycle, and `pytest` from 1030 to 1034.

Cycle 2 also confirmed the anti-divergence mechanism is not vacuous: dropping `reference_qc_flags`
from the flag union, reversing the vocabulary, swapping the blocked reasons and sorting unknown
flags first are each killed by a named test.

**Cycle 3** found one behavioural defect and four wrong claims. The behavioural one was caused by
a fix from cycle 2: the frame-mismatch check added to the lane-detect path computed a warning and
displayed it nowhere — the banner lived only inside the step-3 branch and `submit()` cleared it on
the way there, so the warning was unobservable for exactly as long as it was actionable, under a
comment asserting it was what prevented a person correcting a lane against the wrong picture.
Replacing the call with `void mismatch;` left the whole suite green. It now renders on step 2
beside the lane table, and that mutation fails one named test — verified by running it.

Of cycle 3's four claim errors, three were in **this PR body**: the `--check` leg credited with
guarantees that live on the leg with no CI; `build.py` named as importing `jsonschema`, which
`tests/test_gallery_build.py` imports; and a divergence count contradicting itself twelve screens
apart. A fourth was a floor transcribed as a census in three files — `MINIMUM_DIVERGENCE_CASES`
was 25 while the table held 28, and its docstring claimed it was "raised deliberately when a case
is added", which it had not been. The counts are now gone from the prose entirely.

**Cycle 4** found two more unpinned encodings, both mutation-proven, and both in the half of the
UI that carries a caveat rather than a number:

- **The band highlight was colour-alone in every channel that mattered.** `RoiOverlay`'s docstring
  promises a heavier stroke, a translucent fill and a corner marker "distinguishable in greyscale,
  at a glance, and under every colour deficiency", and nothing asserted any of it. Flattening all
  four channels left the highlight differing from a plain band by **hue only**, and passed
  165/165. This is the trust feature — the one signal that says which region a number came from.
- **`display.note` was rendered and asserted by nothing.** It is `api/display.py`'s
  `DERIVATIVE_NOTE`, and it is the *only* place on screen saying the picture is a derivative
  (`is_derivative` reaches the screen nowhere). Emptying the paragraph passed 165/165. It is the
  stated reason `display.json` is committed at all: without it, a reader judges saturation off the
  brightest colour in a PNG — on a gallery whose blocked cards are saturated.

Its two claim errors were both citations in this PR body, `NOTES.md` and the manifest at once:
"the ruling's §6" (the ruling has **no numbered sections** — the §6 is in the gitignored
measurement artefact, a different kind of document), and "carries no `L8` row" (false; `L8` is in
that file's §5 and §6, what is missing is its **rectangle** in §1). "Digit for digit" also
overstated a four-column table of counts. All three are corrected above.

**Cycle 5** — the final cycle — found one item, and it was an off-by-one in the very sentence
written to state precisely how much of the `L8` measurement is *unverified*. That sentence said
three of the six reported figures have nothing in §6 to be checked against; the true number is
**four** (the verdict, the usable-ratio count, `qc_flags` and `image_qc_flags` — only the band and
ratio counts appear in §6's four columns). It had counted the column *categories* it named rather
than the figures those cover, understating the unchecked portion by a quarter, in the passage that
cycle 4 had already corrected twice. Corrected in both `NOTES.md` and this document; the manifest
header, which enumerates five figures rather than six, was already right.

Three SUGGESTED were taken, one of which was a real gap: `submit()` cleared the frame-mismatch
warning before every request and only the *success* path recomputed it, so a mismatch found at
detect time vanished whenever an Analyse was **refused** — the one outcome that leaves the person
still holding the lane table. Fixed, with a test; reintroducing the clear fails it.

**Two process notes, both the same class of defect as the findings themselves.** Fixing cycle 2's
citation item revealed that an earlier fix had *silently deleted three `NOTES.md` sections* while
rewriting a neighbouring one; all three were restored, and every cycle since has confirmed the
`NOTES.md` diff carries **0 deletions** against `phase-4b0` — the invariant, stated without an
insertion count, because a count written here was itself wrong in cycle 4. And a mutation that
appeared to survive turned
out to be a mis-applied `sed` — a mutation that does not apply looks identical to one the tests
miss, so every mutation reported here was confirmed applied before its result was believed.

## Open questions for the human

1. **Serve the verdict from the API (response-only key) — human ruling.** §5 of the
   pre-registration leaves this open, and it is the reason `web/lib/verdict.ts` exists. Serving it
   would add keys to the `display` block, which is stored on disk and checked against
   `DISPLAY_BLOCK_KEYS`, so every record written before the change would fail the labelling check
   — a migration decision with a compatibility cost. **Until it is ruled, one ruled mapping has
   two implementations.**
2. **The web suite runs in no CI job.** There is no Node step in `.github/workflows/`, so the
   fixture↔TypeScript leg of the anti-divergence pin — the vocabulary equality, the divergence
   divergence table, every fixture floor — has **no automated enforcement**. The Python leg is
   enforced by `pytest`. Closing this means editing a tracked workflow file, which is outside this
   phase's scope and is your call.
3. **CORS.** `api/app.py` installs no CORS middleware, so a browser on any other origin cannot
   reach the API and the live `/analyze` screen cannot work. No proxy was committed and no
   middleware was added to `api/`. It is a decision about who may call the deployed service — which
   origins, or whether the site is served same-origin with the API and the question disappears —
   and it belongs with your API-deployment task.
4. **`api/display.py` is looser than the pre-registration on wrong-typed input.** The generated
   `divergences.json` records it, by observation: `lane_verdicts` reads `excluded` by falsiness, so
   `"false"` reads as *excluded*, where §2.1 defines the survivor set by `excluded` **is `false`**,
   an identity. Likewise `bands` given as a mapping iterates as empty, yielding a confident `pass`
   with no bands. **Not reachable through the shipped path** — both endpoints schema-validate — so
   this is hardening, not a live bug, and `api/` was out of scope. The TypeScript enforces the
   declared types. Worth a decision on whether the Python should too.
5. **The gallery republishes CC BY figures whose licence gate is still open.** `runs/4b0/PROBE.md`
   §4 records every crop as `cc by` *as recorded in* `data/real/sources.csv`, with the
   "licence (Sofia)" column empty pending your hand ruling. That ruling had no consumer before;
   the gallery is now a public artefact built from those crops, so it has one.
6. **Intra-group card order is unruled and it picks the site's largest element.** The blocked group
   ships `L3, L2, L4` (the measurement table's order) while the ruling's table lists `L2, L3, L4`.
   Order within a group is not ruled, but `/` renders the first blocked card at double width, so
   this choice decides the largest thing on the page. Recorded in the manifest header as a
   presentation ground, not a measurement one.
7. **Three of six cards come from one crop.** The blocked group is three lanes of
   `PMC12895598_Fig3__A`, as ruled. Worth confirming that is still what you want now that the
   gallery is six cards rather than four groups.
8. **The committed documents record `source.path` as a local temporary upload path.** Every
   `web/public/gallery/*/result.json` carries a path like
   `/var/folders/…/blotquant-upload-…/….png`, because that is what the service served — the
   per-request temp file the pixels were read from (DEBT E10). It is left exactly as served, since
   editing it would break the "the document is the API's document unedited" invariant the build
   and its tests exist to hold. The consequence is that a public static site carries a developer's
   local filesystem layout. Fixing it belongs with E10 in `api/`, not here.
9. **`web/gallery.manifest.yaml` still needs your approval as a matter of record.** It now
   implements the ruled composition rather than proposing one, so there is nothing left for it to
   propose — but the `L8` rectangle entered it from a measurement taken this session rather than
   from `runs/4b0/`, and that is the one input to the gallery you have not previously signed off.
