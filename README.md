# blotquant

QC-first western blot densitometry with a measured evaluation harness.

blotquant quantifies protein bands in single-channel gel-doc images and attaches a quality-control verdict, an explicit parameter set, and full provenance to every number it reports. It is for molecular biologists who need densitometry numbers they can defend in review, and for reviewers who need to see what a number was measured from. It is a command-line tool at present, aimed at people willing to run `python -m pipeline run` on an exported image.

- **Status: Phase 2 of 5** — CLI pipeline, QC and normalization complete; no API, UI, export, or real-blot validation yet.
- **Band detection F1 0.851**; intensity recovery **7.05% mean / 4.60% median** on clean bands — synthetic dev split only.
- The held-out test split has never been scored or tuned on, and **nothing has been measured on a real blot**.
- **529 tests**; CI enforces lint, schema validity, and re-measurement of every recorded figure.
- MIT licensed.

## Why it exists

Densitometry is among the most routinely misused quantification methods in molecular biology. Bands are measured after the detector has clipped, so the integrated intensity is a lower bound rather than a measurement. Exposures sit outside the linear range of the detector. Background is subtracted by whatever method the software defaulted to, with the radius left wherever it was. Signal is normalized to a single housekeeping protein that may itself shift under the treatment being studied.

Existing tools let all of this pass silently: they will integrate a saturated band and return a number with no indication that it is unusable. That characterisation is the project's motivating premise, inherited from [PLAN.md](PLAN.md) — it is a statement about those tools' feature sets, not a measurement, and no comparison against ImageJ has been run here yet.

blotquant is built on the opposite premise: **it refuses to produce an unpublishable number without saying so.** Every band carries its QC flags. Every flagged band is still reported — annotated, never quietly dropped — and its exclusion from a normalization ratio is explicit and recorded. Every result document carries the complete parameter set that produced it, so a number can be traced back to its ROI and its configuration.

## What differentiates it from ImageJ

- **QC flags are first-class output, not a note in a lab book.** Pixel clipping, low dynamic range, band overlap, unresolved band structure, and lossy input format are computed and recorded in the result document. Four of the five are scored against ground truth; `unresolved_shoulder` cannot be, because the gold set has no such label — it is reported as a coincidence instead, and the eval strips it before scoring rather than renaming it as `overlapping`.
- **Total-protein normalization is a first-class mode.** Single-housekeeping normalization is supported but always emits a recorded warning, per current journal guidance.
- **Accuracy is measured, not asserted.** A seeded synthetic generator produces a gold set with per-band ground truth, and the numbers below come from running the pipeline over it. Held-out test data exists and is untouched.
- **Provenance is complete.** Every reported number traces to an ROI, a parameter set, a software version, and a content-addressed result id. The result document validates against [`schema/result.schema.json`](schema/result.schema.json).

## Measured results — synthetic **dev** split only

30 images, 150 truth lanes, 352 truth bands. Produced by `python -m evals.run --config configs/default.yaml`; the sweep surfaces are in [`evals/dev_sweeps.md`](evals/dev_sweeps.md).

**The test split has never been scored or tuned on.** It is 10 further images. (Tests and CI do *read* all 40 — they rehash every image and validate every ground-truth file against the schema — but nothing scores the pipeline against the test split or selects a parameter using it.) `evals/run.py` and `evals/sweep.py` both hard-code the dev split: a test pins the runner's whole option set so it cannot be given another, and the sweep harness imports the same constant and exact-compares the record's `split` field. Per [PLAN.md](PLAN.md) it is run once, at the end of a phase — which has not yet happened.

**No figure below has been measured on a real blot.**

### Detection

| | precision | recall | F1 | tp / fp / fn |
|---|---|---|---|---|
| band detection (IoU ≥ 0.5) | 0.918 | 0.793 | **0.851** | 279 / 25 / 73 |
| lane detection (IoU ≥ 0.5) | 1.000 | 0.993 | **0.997** | 149 / 0 / 1 |

**The lane F1 is structurally flattered and should not be read as a 0.997-quality result.** A lane ROI's vertical extent is not detected at all — it is fixed at the full image height — so the y-axis contributes exactly 1.0 to every lane IoU by construction. Only the horizontal boundaries are measured. This is a deliberate convention, recorded and carried to Phase 3 as an open item.

### Intensity recovery (relative error vs true integrated intensity, post-background)

| subset | bands | mean \|error\| | median \|error\| |
|---|---|---|---|
| all matched bands | 279 | 17.39% | 6.50% |
| excluding bands whose truth carries `saturated` | 256 | 17.06% | 6.25% |
| bands with **no** truth QC flag at all | 211 | **7.05%** | **4.60%** |

The last row is the honest figure for aperture-and-background accuracy on clean bands. The headline 17.39% is inflated by two populations that are supposed to be hard: saturated bands, whose true intensity is what the band would have had without clipping and which no pipeline can recover from a clipped image; and unresolved doublets, where one detected ROI spans two true bands and over-reads. Both are included in the headline and shown separately — never dropped. Max \|error\| over all 279 is 154.99%.

### QC flag accuracy vs ground truth

Image rows are scored over all 30 images. Band rows are scored over the **279 matched band pairs**, not the 352 truth bands: a flag verdict needs a detected band paired to a truth band, so the 73 truth bands detection missed are counted as detection false negatives instead.

| scope | flag | tp | fp | fn | tn | P | R | F1 |
|---|---|---|---|---|---|---|---|---|
| image | `saturated` | 10 | 0 | 0 | 20 | 1.000 | 1.000 | **1.000** |
| image | `lossy_format` | 6 | 0 | 0 | 24 | 1.000 | 1.000 | **1.000** |
| image | `low_dynamic_range` | 7 | 0 | 3 | 20 | 1.000 | 0.700 | **0.824** |
| band | `saturated` | 23 | 2 | 0 | 254 | 0.920 | 1.000 | **0.958** |
| band | `overlapping` | 0 | 1 | 52 | 226 | 0.000 | 0.000 | **0.000** |

Exact flag-set match: 0.900 of images, 0.803 of matched bands.

Band `saturated` recall of 1.000 is over the 23 truth-saturated bands that were matched. The dev split carries **30**; the other 7 were never detected, so they received no flag verdict at all and are counted as detection false negatives instead. The flag missed none of the bands it was given the chance to judge.

`unresolved_shoulder` is deliberately **not** in this table. Ground truth has no such label, and mapping it onto `overlapping` would be renaming one flag as another. It is reported as a coincidence on the same 279 bands: it fires on **31 of 52** bands whose truth carries `overlapping` (0.596) and **5 of 227** that do not (0.022).

### Normalization

| mode | ratios | included | ref-flagged | mean \|e\| | median \|e\| |
|---|---|---|---|---|---|
| `total_protein` (shipped) | 172 | 150 | 40 | 31.65% | 13.94% |
| `housekeeping_single` | 129 | 77 | 2 | 12.22% | 3.96% |

**The `housekeeping_single` row rests on an oracle** and is not a measure of the tool working end to end. Its reference band is designated from ground truth's `housekeeping` role — an input that does not exist on a real blot. The pipeline refuses to infer a reference and requires the caller to name one, so these figures are conditional on a *correct* reference being supplied.

**The two mean errors are not directly comparable**: 31.65% is over 150 ratios drawn from 86 lanes, 12.22% over the 77 included of the 129 ratios its 129 scored lanes produced, and only one uses an oracle. Each is known on its own subset; the size of the gap between them is not established.

The `included` column is the default policy — QC-flagged bands excluded from ratios. Adding them back gives: over all 172 ratios `total_protein` is 34.10% mean / 11.31% median; over all 129 `housekeeping_single` is 22.95% / 6.13%. **Three of those four comparisons are worse than the published rows — but not all four: `total_protein`'s median absolute error improves, 13.94% to 11.31%.** So the record does not support a blanket claim that excluding flagged ratios always measures better, and the default exclusion is a QC policy rather than something these four numbers establish. A flagged ratio is reported with its flags and its exclusion either way; nothing is dropped, so neither figure is hidden.

`housekeeping_multi` cannot be scored here — every lane in the gold set has one housekeeping band and the mode requires at least two, so it raises rather than degrading. It is verified against hand-computed fixtures in `tests/test_pipeline_normalize.py`.

## Status: Phase 2 of 5 complete

See [PLAN.md](PLAN.md) for the full plan. **This README is interim.** PLAN.md schedules it as a
Phase 5 deliverable; it was written early, as a ratified deviation, because a repository with
three merged phases and no README misrepresents itself to anyone who opens it. The Phase 5
version supersedes it, and will add the screenshots and the real-blot numbers this one records
as absent.

| phase | state |
|---|---|
| 0 — synthetic gold set + metrics | complete |
| 1 — core pipeline (CLI) + eval loop | complete |
| 2 — QC + normalization + provenance | complete |
| 3 — full evals + real-blot cross-validation | **not started** |
| 4 — API + UI | **not started** |
| 5 — export + polish + deploy | **not started** |

**What does not exist yet:**

- **No HTTP API.** There is no `api/` module and no `POST /analyze`.
- **No user interface.** There is no `web/` module, no upload page, no ROI editing, no overlay. The tool is CLI-only, so this README has no screenshots.
- **No export.** No CSV, no XLSX, no charts, no generated methods paragraph.
- **No real-blot validation.** No ImageJ cross-comparison has been run. `data/real/` does not exist. **Every number in this README comes from synthetic images.**
- **No published accuracy claim.** The figures above are dev-split figures from one generator; they are not a general accuracy claim and are not a claim about real blots.
- **No eval history log.** `evals/history.md`, which [CLAUDE.md](CLAUDE.md) and [PLAN.md](PLAN.md) both reference as the per-iteration record and the place a generator break marker would go, does not exist yet. It arrives with Phase 3.

## Architecture

```
synth/               seeded synthetic blot generator — FROZEN after Phase 0
  MODELS.md          documents every noise and background model it uses
data/ground_truth/   generator-written ground truth JSON — never hand-edited
data/images/         the committed gold set (40 images: 30 dev, 10 test)
pipeline/            load, detect, background, quantify, qc, normalize, analyze
evals/               metrics, dev-split runner, sweep harness + recorded surfaces
schema/              ground_truth + result JSON Schemas (the source of truth)
configs/             explicit processing parameter sets (YAML)
```

Python 3.11, NumPy (pinned exactly), SciPy, scikit-image, OpenCV, tifffile, PyYAML, jsonschema. `api/` and `web/` do not exist yet.

### Anti-circularity invariants

The tool is measured against a generator in the same repository, so the discipline that keeps that honest matters. Of the six below, the first four are enforced by tests or by a refusal in code; the last two rest partly on a reviewer, and say so:

- **`synth/` is frozen** after Phase 0. Changing it requires an explicit instruction, a `SYNTH_VERSION` bump, and a break marker in the eval history, because scores across the break are not comparable.
- **`pipeline/` never imports `synth/`** and never reads `data/ground_truth/`. A test enforces the import ban by walking the AST.
- **`data/ground_truth/` is written only by `python -m synth`.** The pipeline refuses to write into any `ground_truth/` directory, case-insensitively and after resolving symlinks.
- **Split discipline**: parameter iteration happens on dev only; the test split is run once per phase, at the end. Neither the runner nor the sweep harness can be pointed at another split.
- **Generator-constant coincidences are audited — by review, not by a test.** Nothing fails if a future parameter lands on a `MODELS.md` number without justification; this one rests on the reviewer checking. 12 of the 20 shipped numeric parameters happen to equal some number in [`synth/MODELS.md`](synth/MODELS.md); each carries an independent justification recorded in both the config and NOTES.md, because `MODELS.md`'s own rule says an unjustified coincidence should be treated as tuning to the generator.
- Recorded dev-split figures are re-measured in CI by `python -m evals.sweep --check`. (That harness — `evals/sweep.py`, its recorded surfaces and the transcription checker — is not in PLAN.md's Phase 1 file list; it exists because several review cycles were lost to recorded figures that had gone stale, and it was ratified as a deliberate deviation.) A test additionally checks transcription — but only within its stated scope: figures inside explicit `<!-- sweep: … -->` / `# sweep: …` blocks, in `configs/*.yaml` and in NOTES.md from the Phase 1 heading onward. **Numbers outside a marked block are not covered**, which NOTES.md says in those words and accounts for with an enumerated list of what is left unchecked and why.

## Design decisions

[NOTES.md](NOTES.md) is the running design log — every decision a reader would otherwise have to reverse-engineer, with the measurement behind it. Rather than duplicate it, some entry points:

- **Why `local_median` and not a rolling ball** — a rolling ball rides the noise floor and under-estimates the background by an amount that grows with the noise. *"`local_median` is the default background because a rolling ball reads the noise floor"*.
- **Why band F1 cannot select the ROI aperture** — F1 at IoU ≥ 0.5 is nearly blind to aperture size across the swept range, so maximising it buys sporadic detections at the cost of a systematic error on every measurement. *"`extent_relative_height`: why band F1 cannot select an aperture"*.
- **Unresolved doublets report one band, not two** — a shoulder-splitter would have to invent a second centre from an inflection, and in the cells where it matters it would be fitting noise. *"Doublet resolution — resolved: one band per resolved maximum, and the cost is 46 bands"*.
- **Lane ROIs are a detected Voronoi partition** — and the entry states plainly which properties of the generator's own rule this reproduces rather than denying the overlap. *"Lane ROI width — resolved: a detected Voronoi partition, one pitch wide"*.
- **How the recorded figures are kept true** — several review cycles were lost to stale numbers; the fix was a sweep harness plus a transcription checker. *"How the figures in this section are kept true"*.

### Two thresholds deliberately take a worse score than the generator's value

This is the anti-circularity discipline costing something measurable, and both are recorded surfaces so the trade is checkable rather than asserted:

- **`saturated_min_clipped_pixels = 1`**, where the generator labels at 3. Shipping 1 scores band-`saturated` **F1 0.958**; the generator's 3 scores a perfect **1.000/1.000/1.000**. Any clipped pixel truncates the integrated intensity, so any clipping disqualifies the measurement; 3 is a labelling threshold, not a biological one. The two "false positives" genuinely contain 1 and 2 clipped pixels — they are correct detections against a coarser label, so where flag and label disagree the flag is right and the *score* is what is wrong.
- **`dynamic_range_min_peak_fraction = 0.25`**, where 0.40 would score perfectly. Shipped gives recall **0.700**; 0.40 gives recall **1.000** with precision 1.000 at both. 0.40 was refused because it sits above the generator's scratch amplitude, so clearing it would be tuning to an artifact only this generator produces. Whether that is the right trade for a QC tool — where under-warning is the more dangerous direction — is an open Phase 3 decision.

## Limitations

Honest ones, not a formality. [DEBT.md](DEBT.md) is the full register — every known weakness with its evidence, a phase or trigger that should close it, and its status; the list below draws from it — the part a user needs before trusting a number.

- **Single-channel grayscale chemiluminescence only.** 8/16-bit TIFF, PNG, JPEG. Multichannel fluorescence, dot blots and 2D gels are out of scope. Unsupported bit depths raise rather than being squashed to 8-bit.
- **Chemiluminescence is not linear in protein amount** over an arbitrary range. blotquant measures the image it is given; it cannot correct for an exposure taken outside the detector's linear range, only warn that the dynamic range looks wrong.
- **Unresolved doublets report one band.** Where two bands sit too close to resolve, one ROI spans both and over-reads. The tool warns via `unresolved_shoulder` rather than inventing a second band.
- **The geometric `overlapping` flag scores 0.000 by construction, and what little it does fire on is mostly spurious.** Because unresolved doublets yield one ROI, there is usually nothing to overlap: at the shipped threshold of 0.05, only 3 detected same-lane band pairs across the whole dev split overlap enough to flag, putting the flag on 6 detected bands — against 104 truth `overlapping` labels, none of which it matches. Reconciling that 6 with the QC table's single false positive: **only 1 of the 6 is a matched detection; the other 5 are detections that match no truth band at all.** So the flag fires predominantly on detection false positives — two spurious peaks in the same lane overlapping each other — rather than on real bands. That is worth knowing: as it stands the flag is closer to a weak detector-artifact signal than a band-overlap warning. (The generator labels overlap at a different threshold, 0.15, where the pipeline finds 2 such pairs; the shipped 0.05 is chosen from an independent criterion, not copied.) Whether to keep, fix or retire the flag is an open question.
- **The shipped `total_protein` mode can fail outright on a faint image.** Its denominator is the lane's own signal integral over a full-height ROI (~9000 px), so it accumulates the background estimator's residual bias over an area far larger than any band. On one dev image (`dev_03`) that integral comes out **negative**, about -5400 DN, and that lane then yields *no ratios at all* — recorded as `lane_denominator_not_positive` rather than returning a nonsense number. Changing the measure is an open Phase 3 decision.
- **`low_dynamic_range` misses 3 of the 10 genuinely low-range dev images** (recall 0.700, precision 1.000). Under-warning is the more dangerous direction for a QC flag, and the threshold that would catch all ten was refused as tuning to the generator — see the two-thresholds note above. This is an open Phase 3 decision, not a settled trade.
- **Housekeeping normalization figures rest on an oracle reference.** See above. There is no measurement yet of the tool finding a reference band, because it deliberately does not try.
- **Absolute integrated intensities are convention-dependent.** The band ROI depends on the lane slice it was measured in, which differs between single- and multi-lane images (~15% tighter in the lone-lane case, excluding real band signal, not only background). **Absolute intensities must not be compared across images or conventions.** Within one image the convention is uniform, so normalization ratios are unaffected.
- **JPEG input is flagged `lossy_format` but still processed.** A lossy codec rewrites the pixels the measurement is made from.
- **No real-blot validation.** Nothing here has been checked against a real western blot or against ImageJ. That is Phase 3, and it is gated on human sign-off of the eval design and of the CC-BY image list.
- **Most parameters were selected on the dev split.** 13 of the 20 shipped numeric parameters come from dev-split sweeps. PLAN.md puts a human eval-design gate *before* parameter iteration; that gate has been pre-empted in substance and must ratify or redo the selection.

## Running it locally

Python 3.11.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

Analyse one image:

```bash
python -m pipeline run path/to/blot.tif --config configs/default.yaml --out results/
```

This writes `results/blot.json` — a result document carrying the bands, their ROIs, their QC flags, the normalization ratios, and the complete parameter set that produced them. `configs/default.yaml` ships `total_protein` normalization; `configs/rolling_ball.yaml` is the same parameter set with the rolling-ball background, kept because it is what ImageJ users compare against.

The housekeeping modes are implemented but **no housekeeping config ships** — only the two above. To use one, copy `configs/default.yaml`, set `normalization.mode` to `housekeeping_single` or `housekeeping_multi`, and name the reference band on the command line, because the pipeline will not guess which band is a loading control:

```bash
python -m pipeline run blot.tif --config my-housekeeping.yaml --out results/ \
    --reference-band <band_id>
```

The flag is repeatable, and `housekeeping_multi` requires at least two reference bands per lane — one raises. Band ids come from the `bands[]` of a previous run on the same image, so this is a two-pass flow: analyse once to see the bands, then re-run naming the reference. Omitting `--reference-band` under a housekeeping mode raises rather than falling back, and passing it under `total_protein` is refused.

### What a result document looks like

Produced by the command above on `data/images/dev_02.png` with `configs/default.yaml` — an
excerpt, verbatim, of one flagged band and its normalization ratio:

```json
{
  "bands": [
    { "band_id": "L0_B0", "lane_id": "L0",
      "roi": { "x": 21, "y": 58, "width": 43, "height": 16 },
      "integrated_intensity": 15794644.0,
      "background_estimate": 5653.979651162791,
      "peak_value": 59900.0, "clipped_pixel_count": 111,
      "row_half_width_ratio": 1.2757543842794359,
      "qc_flags": ["saturated"],
      "excluded_from_normalization": true,
      "exclusion_reason": "carries QC flags: saturated" }
  ],
  "normalization": {
    "mode": "total_protein", "exclude_qc_flagged": true,
    "ratios": [
      { "lane_id": "L0", "numerator_band_id": "L0_B0", "ratio": 0.6638414975559407,
        "excluded": true, "exclusion_reason": "carries QC flags: saturated",
        "qc_flags": ["saturated"],
        "reference_qc_flagged": true, "reference_qc_flags": ["saturated"] }
    ]
  },
  "provenance": {
    "software_version": "0.1.0", "config_id": "default",
    "config_digest": "sha256:f9db0bd6...",
    "parameters": {
      "background": { "method": "local_median", ... },
      "detection": { "method": "profile_projection", ... },
      "quantification": { "method": "roi_sum", ... },
      "qc": { "saturated_min_clipped_pixels": 1, ... },
      "normalization": { "mode": "total_protein", "exclude_qc_flagged": true }
    }
  }
}
```

That band has 111 pixels at full scale, so it is flagged `saturated` — and note what happens
next: the value is **still reported**, the ratio is **still computed**, and the exclusion is
recorded with its reason on both. `reference_qc_flagged` says the denominator is compromised
too, since on this image the lane's own integral contains the clipped band. The `parameters`
block is the complete set that produced these numbers; `...` marks fields elided here, not
absent from the document. The block is fenced as JSON for highlighting but is **not parseable** —
besides the `...` markers, `normalization.warnings` (which on this run carried
`reference_band_qc_flagged` and `reference_band_saturated`) and `provenance.created_at` are
dropped without a marker. Outside the objects shown, most of the document is omitted too —
`schema_version`, `result_id`, `source`, `lanes`, `image_qc_flags`, and 7 of the 8 bands and 7 of
the 8 ratios. Read the real file if you need the exact shape.

### Reproducing the dev-split figures

The detection, recovery, QC and normalization tables above — everything the runner prints:

```bash
python -m evals.run --config configs/default.yaml
```

Several figures here do **not** come from that command, and they are named rather than left for a reader to hunt for. From the recorded surfaces in [`evals/dev_sweeps.md`](evals/dev_sweeps.md): the 211-band unflagged recovery row (7.05% / 4.60%), the overlap pair counts, and both declined-better-score comparisons (`saturated_min_clipped_pixels = 3`, `dynamic_range_min_peak_fraction = 0.40`). Counted directly from `data/ground_truth/`: the 30 truth-`saturated` bands that qualify the band-`saturated` recall, the 10-image test split, and the 40-image gold set. From the test suite: the ~15% single-lane ROI narrowing. From [NOTES.md](NOTES.md): `dev_03`'s
≈ -5400 DN lane integral and the ~9000-pixel full-height lane. The recorded surfaces are
re-measured by:

```bash
python -m evals.sweep --check
```

which fails if a recorded figure has moved **beyond its per-class tolerance** — the structure, the header and the two config digests are compared exactly, but every measured figure gets a bound from one of fourteen tolerance classes — counts ±4, detection rates ±0.02, most percentage-point errors ±1.0 pp, flagged-subset errors ±4.0 pp, normalized-ratio errors ±35% of the figure, and others tighter or looser (`evals/sweep.py` defines them all, each with its derivation). It is a staleness and regression alarm, not a bit-exactness proof: a small drift passes by design, and a shipped parameter change is caught by the digest rather than by the figures. It is a CI step and takes roughly nine minutes of CPU. The parameter counts (12 of 20 coinciding, 13 of 20 dev-selected) are audits recorded in [NOTES.md](NOTES.md); no command produces them.

Regenerate the gold set from its seed — reproduces byte-identical ground truth and pixel hashes:

```bash
python -m synth --seed 42 --out data/
```

Tests and lint:

```bash
pytest
ruff check .
```

## How this was built

The repository is built under a phase-gated workflow, recorded in [CLAUDE.md](CLAUDE.md). An
implementing agent works autonomously within one phase of [PLAN.md](PLAN.md); a reviewer agent
then audits the full diff in a fresh context, and the phase does not close until it returns zero
required changes. A human ratifies the PR at the end of every phase — commit messages are written
by the human, not the agent — and [PLAN.md](PLAN.md) specifies two further named gates, both in
Phase 3: sign-off on the eval design before parameter iteration, and approval of the CC-BY
real-image list.

What that has caught: the reviewer independently re-measured the reported figures against the
artefacts, and twice removed circularity from the test suite where a shipped threshold had been
pinned to a generator-derived argmax — once as an explicit assertion, and once as two separate
bounds that together admitted only one value.

What it did not catch: automated review passed a gold set in which 13 of the 40 images held
near-circular bands — every `smeared` cell, down to an aspect ratio of 1.14 at six lanes. That is
physically impossible for a western blot, because a band is the cross-section of an
electrophoretic lane and is always substantially wider than it is tall. A human domain review
caught it before the gold set was frozen; the fix was a configured, validated aspect-ratio floor.

## Repository conventions

[CLAUDE.md](CLAUDE.md) records the working agreements this repository is built under — scope discipline, the frozen-generator boundary, the loud-failure-over-silent-fallback rule, and the requirement that image-processing code be asserted numerically rather than eyeballed. [PLAN.md](PLAN.md) is the phase plan. [NOTES.md](NOTES.md) is the design log.

## License

MIT — see [LICENSE](LICENSE).

Note that `data/images/` and `data/ground_truth/` are generated by this repository's own
generator and carry the same licence. The real-blot images Phase 3 will add are third-party
figures under their own terms; PLAN.md restricts them to CC-BY, with DOI and licence recorded
per image.
