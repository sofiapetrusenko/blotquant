# Amendment 2026-08-24 — signal polarity is a declared caller input

**Status: RATIFIED 2026-08-24 by the human gate. In force.**

**Ratification, executed 2026-08-24.** Status flipped, sha256 recomputed, and the digest pinned
in `tools/check_claims.py` (`RATIFIED_AMENDMENTS`) so that an edit to this ratified amendment
fails the build the way an edit to the pre-registration does. The path is unchanged. Ratified
with **Option A** of §(c) ruled, and with the addition to §(e) recorded *before* the flip so that
the real-crop prediction is dated ahead of the run it predicts.

Amends `data/real/DECISION_unit_of_analysis.md` by adding a scope condition it does not
currently state, on the same discipline as the two 2026-08-19 amendments: a dated file beside
the frozen document, which stays byte-identical to what Gate 2 froze, with
`tools/check_claims.py` failing the build if it does not.

**Ratification procedure, executed 2026-08-24** — recorded in the past tense because it has been
carried out, not as a plan: the status line above was flipped to RATIFIED with the date, this
file's sha256 was recomputed, and the digest was pinned in `tools/check_claims.py`
(`RATIFIED_AMENDMENTS`). The path is unchanged, so nothing there needed re-pointing. While this
file was a draft the digest was deliberately unpinned, because a draft that changes is not a
broken build; it is pinned now, so a change to it fails the build the way a change to the
pre-registration does.

**Corrected 2026-08-24, after the digest was first pinned.** The flip left three sentences behind
that still described a draft — this paragraph in the future tense, a closing line reading
"Awaiting ratification", and a claim that the digest was unpinned. All three contradicted the
status line eleven lines above them. They are corrected here rather than in a further amendment
because they are not a revision of any ruling: they are step one of this procedure, executed
incompletely. The digest was recomputed and re-pinned as part of the correction, which is why
this note exists rather than a silent edit.

**What a reader of `main` will and will not see.** This whole sequence — the flip, the
incomplete flip, the correction and the re-pin — happened in one uncommitted working tree, so
`git log` on this path shows a single commit adding a consistent file and its pin, and no moved
digest. The note is here because the *session* moved one and that should be visible somewhere,
not because history records it. It is a record of how the ratification went, not a precedent for
editing a ratified file: a change to any **ruling** in this document is a further amendment with
its own date and digest, and after this file is merged even a correction of this kind is.

## Why this is needed

Phase 3b-1 ran detection over the 12 crops the channel-collapse amendment admits and then, before
recording the result, measured whether the QC flags were describing the corpus or the measurement.
They were describing the measurement. From `runs/3b1/QC_DIAGNOSTIC.md`:

- Every one of the 12 crops has a median pixel value of **249 or higher against a full scale of
  255**, and between **37.7% and 54.5%** of each image sits at exactly full scale. These are
  white-ground published figures whose bands are **darker** than their background.
- `pipeline/` detects **maxima** and defines clipping as pixels at **full scale**
  (`pipeline/qc.py::_clipped_pixel_count`: `pixels[rows, columns] >= max_value`). On a white
  ground, the extreme it tests for is the paper.
- **286 of 430 bands carry `saturated`**, and exactly 286 contain a full-scale pixel inside their
  own ROI; the median `saturated` band is **39.3%** full-scale by area. The extreme a genuinely
  saturated *dark* band would produce — a pixel at 0 — occurs in **8 of 430** bands.

Human ruling **R1 of 2026-08-24** records that as a measurement artefact, not a corpus property.
Human ruling **R4 of the same day** rules the remedy, and this amendment is that ruling written
as a pre-registration change. `DEBT.md` S14 carries both.

## (a) Ruling — polarity is declared, never inferred

**The pipeline refuses an image whose polarity is not declared.** There is no default and no
inference. An auto-detection heuristic — "if the median is above half of full scale, treat it as
dark-on-light" — would be a **threshold chosen against real data**, which Gate 1 ruling 3 forbids
outright, and it would be chosen against the twelve images whose behaviour prompted it. That is
the one move this project refuses everywhere else.

This puts polarity in the same class as two inputs the project already treats this way: the
reference-band designation (`DEBT.md` S6; §2 of the pre-registration: *"Guessing the loading
control from the data is forbidden"*) and blot identity (ruling G2, ruled from the images). The
pattern is now three deep and is stated here as a rule: **where the pipeline cannot know
something about the sample, it refuses and says so, rather than inferring it from the data it is
about to measure.**

**Vocabulary — exactly two values, and no third.**

| value | meaning |
|---|---|
| `bright_on_dark` | Signal is positive: bands are brighter than their background. Chemiluminescence, gel-doc, and the synthetic gold set. |
| `dark_on_bright` | Signal is negative: bands are darker than their background. Transmissive film scans, and every published-figure crop in `data/real/crops/`. |

No `auto`, no `unknown`, no empty string. `auto` would be the forbidden heuristic wearing a
vocabulary word; `unknown` would be a declaration that declares nothing, and the refusal already
covers the case where nobody has said.

**Where the declaration lives — both surfaces, because both are entry points.**

- **CLI:** a required `--polarity {bright_on_dark,dark_on_bright}` on `python -m pipeline run`.
  Required rather than defaulted: an omitted flag must fail, and `argparse`'s `required=True`
  makes the failure the parser's rather than a downstream `None` check's.
- **API:** a required field on the analyse request, refused with the same status class as the
  other unquantifiable-input refusals, so a caller gets one consistent story.
- **Provenance:** recorded on the result document as `source.polarity`, beside `bit_depth` and
  `channel_collapse` — a property of the input, on the object it describes, which is where the
  channel-collapse amendment put its own record and for the same reason.

`pipeline.load.load_image` is the single choke point: every caller in the tree reaches pixels
through it (`pipeline/analyze.py`, `api/app.py`, `evals/sweep.py`, `tools/phase3/designations.py`,
`tools/phase3/qc_diagnostic.py`). The refusal belongs there so that no path can acquire pixels
without a declaration.

## (b) The synthetic gold set and its generator declare `bright_on_dark`

**Established by reading the generator, not assumed.** Three independent statements in the
committed source, quoted rather than paraphrased:

- `synth/render.py`, module docstring: *"Intensities are in DN of the target bit depth,
  signal-positive (bright bands on a dark background, i.e. chemiluminescence)."*
- `synth/MODELS.md`: *"Signal is positive on a dark background (chemiluminescence convention)."*
- `synth/generator.py:326`, the composition itself: `signal = background + sum(band_layers)`,
  with `render_band` returning `amplitude_dn * exp(...)` — a **positive addition** to the
  background. A dark-band generator would subtract.

**Corroborated by measuring the committed gold set**, read in this session through
`pipeline.load.load_image`: **16 images; the median pixel value is 8.1%–14.1% of full scale**
(8 images at full scale 255, 8 at 65535); **0.1142%** of all pixels sit at full scale and
**0.0000%** sit at zero. A dark ground with a small bright tail — the generator's declared
convention, confirmed on its output.

So the gold set declares **`bright_on_dark`**, and the twelve real crops declare
**`dark_on_bright`**. `synth/` is frozen; declaring the value it already implements requires no
change to it, and none is proposed here. Where the declaration is written down for the gold set —
a per-image field in the ground-truth record, or a constant the eval harness passes — is an
implementation question for (c), not a further ruling.

## (c) Implementation choice — proposed with reasoning, for the human to rule

### Option A — invert at load, so all downstream code sees one convention

`load_image` takes the declared polarity; for `dark_on_bright` it returns `max_value - pixels`
and records the declaration in provenance. Everything downstream — background estimation,
detection, quantification, QC, normalization — is unchanged and sees `bright_on_dark` always.

- **For.** One convention below the loader means one code path, one set of tests, and no
  branch that can be got wrong in one place and right in another. It fixes **detection** as well
  as QC: on a `dark_on_bright` image the maxima become the bands, which is the only version of
  this fix that could change the 430-band count. Clipping tests at full scale become correct
  because a dark band's clipped pixels land at full scale after inversion. `max_value - pixels`
  is exact integer arithmetic on `uint8`/`uint16` — no float round-trip, no rounding.
- **Against.** It is the larger change and the more invasive one: the array the pipeline measures
  is no longer the array the file holds, and `source.sha256` keeps identifying the delivered
  bytes while the measurement is of something else. That has to be visible in provenance rather
  than implied. It also changes what "the image" means in any future debugging session.

### Option B — make QC polarity-aware without inverting

`load_image` records the declaration; `pipeline/qc.py` branches on it, testing clipping at 0
instead of at full scale for `dark_on_bright`, and dynamic range against the inverted excursion.
Detection, background and quantification are untouched.

- **For.** Smaller, and it leaves the delivered pixels as the measured pixels.
- **Against, and this is decisive.** It fixes the flags and **not the measurement**. Detection
  still finds maxima, so on the twelve real crops it still finds the gaps between bands rather
  than the bands; background estimation still treats the paper as signal; integrated intensity is
  still computed over rectangles drawn around the wrong features. The QC vocabulary would become
  accurate about a measurement that remains wrong — arguably worse than the current state, in
  which the flags at least fire loudly on every band.

### Recommendation

**Option A**, and the reason is (b): the gold set declares `bright_on_dark`, which is exactly the
convention the pipeline already assumes, so **choosing `bright_on_dark` as the canonical internal
convention makes Option A a literal no-op on every image the project has measured to date** —
the array is passed through untouched, not inverted twice. The cost of the larger change is
therefore paid only by `dark_on_bright` images, of which there are currently twelve and all of
them are unmeasured. Option B's smaller diff buys nothing here and leaves the measurement wrong.

**Noted as the ruling asks:** Option A also exposes **detection** to the corrected polarity. That
is diagnostically the point — it is what makes the 3b-2 re-run able to say whether over-detection
was a polarity artefact or a separate defect — and it is also the larger blast radius, because
band counts, band ids and every downstream count on the real crops will change. On the gold set
it changes nothing; see (d).

## (d) Predicted effect on the recorded dev-split figures — written before any run

**This section is the pre-registration. A figure that moves in a way this section did not
anticipate is a finding, not a re-record.**

**Prediction: under Option A with `bright_on_dark` as the canonical convention, no recorded
dev-split figure can move. Under Option B, likewise none.** The reason is the same for both and
it is structural, not empirical:

1. Every recorded figure in `evals/dev_sweeps.md` — the detection sweeps, `shipped_configs`,
   `matched_band_subsets`, `band_roi_sizes`, `doublet_cost`, `lane_roi_geometry`,
   `presmooth_variance`, `qc_flag_accuracy`, `normalization_modes`, the four `qc.*` sweeps and
   `aperture_selector_uncertainty` — is computed from the gold-set pixels.
2. The gold set declares `bright_on_dark` (b).
3. Under Option A the `bright_on_dark` path returns the array **untouched**. Not inverted twice —
   untouched, and this is a requirement on the implementation rather than an observation about
   it. Under Option B the QC branch taken for `bright_on_dark` is the existing code.
4. Therefore the pixel arrays fed to background estimation, detection, quantification, QC and
   normalization are bit-identical to today's, and every figure derived from them is unchanged.

**If any dev-split figure moves, the implementation is wrong, not the record.** The most likely
mechanism for a wrong implementation, named in advance so it is checked: an inversion applied
unconditionally and then undone, which is bit-exact for `uint8`/`uint16` and therefore silent —
except at the clipping boundary, where `max_value - (max_value - x)` is exact but any
intermediate float or signed cast is not. A second: reading the declaration from a place that
supplies a different value for some images than for others.

**Three things that will change and are not recorded dev-split figures**, listed so that their
movement is not mistaken for a violation of the paragraph above:

- **The result schema version and `source.polarity`.** Adding a field to `source`, which is
  `additionalProperties: false`, requires a version bump exactly as `channel_collapse` did.
- **`result_id`.** It is a content address over provenance inputs, so a new declared input
  changes it for every document. Nothing in the tree pins a literal `result_id`; this was
  established when the 1.3.0 bump made the same change, and it must be re-established rather
  than assumed.
- **Every count on the twelve real crops**, under Option A. That is the point of the change and
  is measured in W11 against the 3b-1 baseline (430 bands, 404 flagged, 286 saturated, 106
  lanes), not predicted here — predicting it would be pre-registering the answer to the question
  W11 exists to ask.

**Explicitly out of scope, and not a prediction of this section:** whether over-detection
survives the polarity fix. Detection parameters do not change in Phase 3b-2. If over-detection
survives, it is a separate defect requiring its own pre-registration, and it is not addressed by
ratifying this amendment.

## (e) The re-record rule

Recorded figures are re-measured **only** when all four of these hold, in this order:

1. **This amendment is ratified.** No figure is re-recorded on the strength of a draft.
2. **The implementation is complete and the movement is reported against (d).** Every figure that
   moved is listed with its before and after value and the mechanism, and checked against the
   prediction above. A movement (d) did not anticipate is written up as a **finding** — what the
   prediction got wrong and why — before any figure is rewritten.
3. **The before/after pair is committed**, both values, in the same commit as the new record. A
   record that shows only the new value cannot be audited by a later reader.
4. **`python -m evals.sweep --check` passes on the new record**, so the committed figures are
   the ones the committed gold set reproduces.

**Absorbing a movement silently is the failure this section exists to prevent** — a figure
quietly rewritten to match a changed measurement is indistinguishable, one commit later, from a
figure that never moved. `tools/check_claims.py` catches a figure that disagrees with its record;
it cannot catch a record and a measurement that were changed together.

If (d) is right and nothing moves, steps 2 and 3 are satisfied by recording that nothing moved,
with the `--check` run as the evidence. **That is still a report, not a silence.**

### Addition, ruled 2026-08-24 with Option A — what the real-crop over-detection is expected to do

Option A exposes **detection** to the corrected polarity for the first time. Whatever happens to
the over-detection recorded in Phase 3b-1 drafts **D8** and **D10** is therefore a **measurement
of how much of it was polarity and how much is parameters** — not a fix, and not a scope breach,
because no detection parameter moves in Phase 3b-2. This section states the expectation **before
W11 runs**, so the outcome is predicted rather than explained afterwards.

The mechanism both predictions rest on: `pipeline/detect.py::detect_lanes` takes
`corrected.mean(axis=0)` and finds **maxima** of that column profile, and band detection finds
maxima of a lane's row profile. On a `dark_on_bright` image the maxima are not the lanes and not
the bands — they are the **bright gaps between them**, and the margins.

**D8 — 13 lanes detected where G2 counted 12. Expected direction: down, to 12.**
A panel of 12 lanes has 11 interior gaps, plus up to 2 bright margins at the left and right
edges: 11 + 2 = **13**, which is exactly the count observed on all four `PMC13135410` panels.
That arithmetic is the leading hypothesis, and Option A tests it directly — after inversion the
maxima are the lanes themselves, so the count should become **12**. Two other outcomes are
possible and each means something different, so both are named now rather than after the fact:
if it stays at 13, the excess is a real spurious lane and is **parameter-driven**, belonging to
the separate pre-registration this amendment defers; if it moves to some third number, neither
the gap arithmetic nor the spurious-lane reading is right and the finding is that the mechanism
is not understood.

**D10 — up to 8 bands in one lane, where the confirmed designations allow 2 (3 on the two-target
crop). Expected direction: down, substantially, and not necessarily to 2.**
The same argument one axis over: within a strip of background, the row-profile maxima on a
light-ground image are the bright gaps between dark bands, so a lane holding *k* real bands
presents roughly *k* + 1 bright regions before any texture is counted. Inversion should therefore
cut the per-lane count, and the aggregate 430 bands with it. **It is not expected to reach 2.**
Published figure panels also carry MW annotations, arrowheads, panel borders and JPEG texture,
none of which polarity touches, and D10's own closing paragraph says the honest instrument for
the remainder is a human count of true bands per lane. A drop that stops well above 2 is the
expected result, and the size of the residue is the number this measurement exists to produce.

**What would falsify the polarity explanation altogether:** band and lane counts that are
unchanged, or that rise. Either would say the over-detection was never about polarity, and the
whole of it would belong to the deferred detection pre-registration.

**Neither prediction licenses a parameter change.** If the residue after inversion is still far
above what the designations allow, that is recorded as a measurement and carried as debt. Gate 1
ruling 3 is not suspended by a prediction coming true.

## What this amendment does not do

It selects no parameter and changes no threshold. It does not touch detection parameters:
Phase 3b-2 is polarity only, and whether over-detection survives the fix is a separate question
with its own pre-registration if the answer is that it does. It does not re-crop, re-log or
re-export anything, and `crop_log.csv` and `DECISION_unit_of_analysis.md` stay byte-identical to
what Gate 2 froze. It does not itself declare the polarity of any image; it rules that the
declaration must exist and be supplied by a caller.

Proposed: implementer, 2026-08-24.

Agreed: Sofia, 2026-08-24 — Option A of §(c) ruled, with the §(e) addition required before the
status flip so that the real-crop prediction is dated ahead of the run it predicts.
