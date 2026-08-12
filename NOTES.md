# NOTES — design decisions

Running log. One entry per decision that a reader of the code would otherwise have to
reverse-engineer. Feeds the README's "Design Decisions" section.

---

## Phase 0 — synthetic ground truth + metrics

### Determinism is enforced by the legacy RNG, not by luck

`numpy.random.Generator` explicitly does **not** guarantee that a given seed yields
the same stream across numpy releases; `numpy.random.RandomState` does. The committed
gold set is a frozen artifact that every future eval score is measured against, so
`synth/` uses `RandomState` everywhere. numpy is pinned exactly in `requirements.txt`
for the same reason — upgrading it is a deliberate act that must be paired with
regeneration, a `SYNTH_VERSION` bump and a break marker in `evals/history.md`.

Seeds are derived as `sha256(f"{master_seed}:{label}")[:4]`, not by arithmetic on the
master seed, so the derivation does not depend on numpy or on iteration order. Each
image draws from four independent labelled streams (`background`, `bands`,
`defects`, `noise`); adding a draw in one component therefore cannot shift another
component's numbers, which keeps future changes auditable. All four derived seeds are
recorded in the ground truth.

### The canonical artifact is the pixel array, not the file

PNG and JPEG bytes depend on the local zlib/libjpeg build; the quantized array does
not. So `pixel_sha256` digests the array (dtype and shape folded in, bytes taken
little-endian), and the determinism test compares digests rather than file hashes.
Lossless containers are checked to round-trip to the exact recorded digest; JPEG is
checked against the canonical array within an explicit tolerance. The tolerance is
derived from the **dev split only** (worst case over its six lossy images: mean
3.48 DN, max 26 DN; the test allows 6.0 / 48) and then applied to every lossy image.
This is a generator self-check rather than pipeline parameter iteration, but the
split rule is worth keeping unambiguous, and deriving the bound from dev costs
nothing. The bounds are not re-fitted when the dev worst case moves: they only have to
sit above it with headroom, and re-tightening them on every regeneration would trade
CI robustness against libjpeg builds for nothing.

Ground-truth floats are rounded to 6 decimals on serialisation, which absorbs
last-bit differences in `exp`/`tan` between CPU architectures without hiding anything
that matters at DN scale.

### Bit depth and container are one axis, not two

JPEG cannot carry 16-bit data. Rather than generate `(16, jpeg)` and silently repair
it, the matrix enumerates only the five legal pairs (`tiff8`, `tiff16`, `png8`,
`png16`, `jpeg8`). Requesting 16-bit JPEG anywhere else raises
`UnsupportedFormatError`.

### The axis levels are config, not module constants

Which levels each difficulty axis has is a generation parameter like any threshold, so
they live in `MatrixConfig` inside `synth/config.py`; `synth/matrix.py` only schedules
them and resolves each cell's container and bit depth from the configured pairs. As
module-level constants they sat outside `GeneratorConfig.digest()`, so editing an axis
would have changed the gold set without the digest — the freeze guard — noticing.
`GeneratorConfig.__post_init__` additionally refuses a level that has no parameter
entry in the matching mapping, so the mismatch surfaces at config construction rather
than part-way through a render.

Consequently the digest hashes the config in declaration order rather than sorted
order. Level *order* is a parameter too — the balanced round-robin assigns from it, so
reordering `format_depths` changes which image gets which container — and a sorted
payload hashed identically before and after that edit. Both orders are fixed
properties of the source, so the digest stays stable run to run.

The same argument applies to the *written* record. `data/generation_config.json` is
the only artifact that states the axis definitions at all (per-image ground truth
records `config_digest`, not the axes), so serialising it with sorted keys re-sorted
`format_depths` into `['jpeg8', 'png16', 'png8', 'tiff16', 'tiff8']` — an order that
was never generated from, that could not be hashed back to the recorded digest, and
that would have made an axis reorder invisible when two echoes are compared across a
regeneration. The echo is now written in declaration order, and a test re-hashes the
echoed `config` block and asserts it reproduces the recorded `config_digest`. Ground
truth stays sorted: it contains no order-bearing mapping, and sorted keys give
readable diffs.

### Matrix cells are balanced-then-permuted, per split

A modulo schedule (`levels[i % n]`) correlates every axis with the same period, so
"tiff" would always co-occur with "tilted". Instead each axis gets a balanced
sequence (levels round-robin to the split size) permuted by its own derived seed.
This guarantees per-level counts differ by at most one **and** that every level of
every axis appears in *both* splits — asserted in `tests/test_generator.py`, not
assumed. Consequence: the 10-image test split covers all 8 axes, at the cost of only
one or two images per level, so per-cell test numbers will be noisy. Test scores are
reported once per phase as an aggregate, which is what PLAN.md asks for anyway.

### Exposure absorbs the "saturation" axis

PLAN.md lists saturation as a difficulty axis. It is implemented as one level of an
`exposure` axis (`normal` / `low` / `saturating`) because under- and over-exposure
are the same physical knob, and modelling them separately would have created illegal
combinations (`low` + `saturating`). `low` doubles as the source of truth for the
`low_dynamic_range` QC label.

### The truth intensity is per-band, inside the band's own ROI

`true_integrated_intensity_dn` sums *that band's own* noise-free layer inside its
ROI, after dust attenuation, before clipping (full statement in `synth/MODELS.md`
§8). Three deliberate consequences:

- A perfect pipeline that finds the same ROI scores ~0% recovery error — the truth is
  not defined over an infinite integration area, so there is no built-in error floor.
  Verified numerically: on a noiseless, flat-background fixture a plain box sum
  recovers the recorded truth to within 0.5% (quantization only).
- In a doublet cell, a box sum over one partner's ROI over-reads: measured across the
  committed set, by 82.4–113.1% on the weaker partner and 37.3–51.7% on the primary
  (`synth/MODELS.md` §8 states the measurement). That is the cell's purpose; both
  bands carry the `overlapping` flag.
- On a saturated band the truth is unrecoverable by construction. Recovery error
  there is *supposed* to be large; the honest answer is the QC flag, not a number.

### Lane ROIs are declared, not measured — so their rule is config and is documented

A band ROI is derived from rendered pixels (the `bbox_relative_threshold` contour). A
lane ROI is not: it is a rectangle the generator declares. Its two free numbers —
width in lane pitches, and how much tilt excursion to allow — were literals in
`_lane_roi`, which made them exactly the kind of hidden layout constant that
`sigma_x_fraction_of_pitch` is not. They now live in `LaneLayoutConfig`
(`roi_width_fraction_of_pitch = 1.0`, `roi_tilt_excursion_fraction_of_height = 0.5`),
are covered by the config digest, and are echoed per image under
`generation.parameters.lane_layout`.

That matters beyond tidiness: `lanes[].roi` is a ground-truth output Phase 1's lane
detection is scored against, and `synth/MODELS.md` is the document the Phase 3
reviewer uses to judge `pipeline/` for special-casing. With the rule undocumented, a
Phase 1 implementer could have reverse-engineered "one pitch wide, full height"
straight out of the committed JSON and no document would have caught it. MODELS.md
§4a now defines the rule, and the special-casing list has a bullet for it.

The chosen values reproduce the previous geometry bit-for-bit: regenerating with them
left every image byte and every recorded ROI unchanged, and only `config_digest` and
the new echoed block moved.

### Band aspect ratio is a configured, validated quantity — bands are never circular

A western blot band is the cross-section of a sample lane. The well and the lane bound
how wide it can be; nothing bounds it to be equally tall, so a real band is always
substantially wider than it is tall. A near-circular blob is a **dot-blot** artifact —
dot blots pipette sample straight onto the membrane with no electrophoretic lane — and
dot blots are out of scope per PLAN.md.

The first cut of the generator did not control this quantity anywhere. `sigma_x` was
derived from lane pitch (`0.24 * pitch`, so 13.68 / 10.94 / 9.12 px at 4 / 5 / 6
lanes) while `sigma_y` was an absolute per-shape constant, which left the aspect ratio
as an unowned by-product of two unrelated decisions. It came out between 1.14 and 4.56
across the matrix; 13 of the 40 committed images — every `smeared` cell — held bands
rounder than 1.75, and the 6-lane `smeared` cells were essentially circular at 1.14.
Nothing in the config, the schema or the tests would have noticed.

Two changes, together:

- `BandShapeParams.aspect_ratio` replaces `sigma_y_px`. `sigma_y` is now derived as
  `sigma_x / aspect_ratio`, so a level's *shape* is fixed while its *size* still
  scales with lane pitch. This is the load-bearing half: with an absolute `sigma_y`
  the ratio varied along the `lane_count` axis, so any floor could only ever hold at
  the loosest lane count. It now holds exactly at 4, 5 and 6 lanes.
- `BandLayoutConfig.min_aspect_ratio = 2.5` is the floor, checked against **every**
  `band_shape` level in `GeneratorConfig.__post_init__`, raising `ConfigError` that
  names the level and its ratio. Deliberately not a `min()` clamp: clamping is the
  silent fallback this project forbids, and it would leave the config describing a
  band the generator never rendered.

Levels are `sharp = 5.0`, `doublet = 4.0`, `smeared = 2.6` — chosen to keep the
qualitative spread the axis exists for (`smeared` stays ~1.9x more vertically diffuse
than `sharp`, and sits nearest the floor because it is the hardest cell) while all
three clear 2.5. `sigma_x_fraction_of_pitch` was left at 0.24: inflating it would have
raised the ratio the easy way, but band ROIs already span 0.75 of a lane pitch, so
widening bands would push adjacent lanes toward bleeding into one another.

**The `band_shape` axis did get narrower, and that is the price paid.** The old
absolute sigmas separated `smeared` from `sharp` by 2.67x (8.0 px against 3.0 px); the
new ratios separate them by 1.923x (5.0 against 2.6), and `smeared`'s absolute extent
fell from 8.0 px at every lane count to 5.26 / 4.21 / 3.51 px at 4 / 5 / 6 lanes. The
axis is not collapsed — measured on the committed set, 6-lane target ROIs are still
cleanly separated at 9 px (`sharp`), 11–12 px (`doublet`) and 17–18 px (`smeared`) —
but the hardest cell is less diffuse than it was, and a Phase 1 score on `smeared` is
therefore not comparable to one taken before this change. `tests/test_generator.py`
now asserts a hard floor (`smeared` ROI at least 1.5x the `sharp` ROI height) rather
than only a config-derived expectation, so a future retune cannot narrow the axis
further without failing.

Cost: `data/` was regenerated from the same seed, so every pixel moved. Pre-freeze, so
`SYNTH_VERSION` stays `1.0.0` and no break marker is due (human ruling). The QC axis
kept its coverage — all four flags still fire in both splits — with one band losing its
`saturated` label (31 → 30 in dev) because smaller ROIs contain fewer clipped pixels.
The `saturated` label now sits closer to its threshold than it did: measured over the
committed set, the smallest `clipped_pixel_count` among bands labelled `saturated` is
**4**, against `qc.saturated_min_clipped_pixels = 3`, and the largest among unflagged
bands is **2**. One clipped pixel either way would move a label, so a future change
that shrinks ROIs again should re-check this margin rather than assume it.

### QC labels in ground truth describe what is observable

A pixel is "clipped" when its final value equals full scale — not when the underlying
float exceeded it. The two differ only where noise pushes an unclipped pixel to the
maximum, and the observable definition is the one a pipeline could ever reproduce, so
scoring against it is fair. A band needs 3 clipped pixels (not 1) to be labelled
`saturated`, so a lone noise excursion is not called saturation.

### Metrics raise instead of returning a defensible-looking zero

An evaluation with no truth boxes, a non-positive reference intensity, a lane present
in the truth but missing from the prediction, a QC flag outside the declared
vocabulary — all raise. A metric that returns 0.0 for "undefined" prints in an eval
table exactly like a real score, which is how bad numbers get published. The IoU
threshold is a required argument everywhere; `PLAN_IOU_THRESHOLD = 0.5` exists as a
named constant that callers must pass explicitly.

The one place a zero is *not* an undefined value: an empty prediction set against
non-empty truth. The pipeline genuinely found nothing, so precision, recall and F1
are all reported as 0.0, and the docstring says so.

Per-flag QC scores use `None`, not 0.0, where they are genuinely undefined — a flag
absent from both truth and prediction has no precision or recall. Phase 3's
per-difficulty-cell table will routinely contain cells with no saturated image, and a
0.0 there would read as total failure on a flag the pipeline handled perfectly. A
runner must render `None` as "n/a".

Per-flag **F1** follows the standard convention rather than propagating that `None`:
`f1 = 2*tp / (2*tp + fp + fn)`, which is 0.0 whenever the flag appears in the truth or
in the predictions but yields no true positive, and `None` if and only if
`tp == fp == fn == 0`. An earlier version returned `None` as soon as precision *or*
recall was undefined, which meant the single most important failure — a flag that
occurs in the gold set and that the pipeline never once fires — printed as "n/a". That
is the mirror image of the hazard the paragraph above is guarding against: the first
hazard publishes a fake zero, this one hides a real one, and hiding a real zero is how
a total failure gets *not* published. Precision and recall keep their `None`-when-
undefined semantics, so a row can legitimately read `precision = n/a`,
`recall = 0.00`, `f1 = 0.00`.

### CI's pre-Phase-0 escape hatches were removed

`.github/workflows/ci.yml` skipped schema validation when the schema or the gold set
was absent, and tolerated pytest's "no tests collected" exit code — both annotated in
the file as acceptable only until Phase 0 merged. With them in place, deleting
`data/` or `tests/` would leave CI green, so PLAN.md's "all ground truth validates
against schema in CI" was not actually enforced. Both now fail loudly. This is the
only edit to an existing file outside the Phase 0 checklist; the new files the phase
adds (`pyproject.toml`, `requirements.txt`, `conftest.py`, `.gitignore`) are packaging
and test scaffolding for it.

### `data/` is committed, images included

The gold set is checked in (2.5 MB, 256×192 px) so that CI can validate ground truth
against the schema and every later phase measures against a byte-identical set.
Images are kept small deliberately; band geometry scales with lane pitch, so the
difficulty does not depend on the canvas size.

### The generator refuses to half-overwrite a gold set

`python -m synth --seed 42 --out data/` overwrites in place (PLAN.md's done-when
requires that exact command to work). But if the output already holds a set from a
different seed, a different `SYNTH_VERSION`, or a different config digest, or holds
any file this run would not rewrite, the run aborts with `GoldSetMismatchError` and
points at `--force`. Silently mixing two generations would be indistinguishable from
a corrupted gold set.

The check covers `images/` as well as `ground_truth/`, and that is the case that
actually bites: the container axis is permuted per seed, so a reseed changes image
*extensions* while ground-truth filenames stay the same — a guard that only looked at
`ground_truth/` would leave orphaned images from the previous generation with no
matching truth. Subdirectories count as entries too, and `--force` refuses to delete
them.

The guard reads `generation_config.json` as well as every ground-truth file, because
deleting the ground truth "to regenerate it" is exactly how a user ends up pointing a
new seed at another generation's images — and because a config-only edit changes no
filename at all, leaving the echo as the only witness. Corrupt or truncated
provenance (unparseable JSON, a JSON array, a `generation` key that is not an object)
raises `GoldSetMismatchError` naming the file, never a raw traceback.

Deletion happens only under `--force`, and the deleted names are returned in
`DatasetSummary.removed_files` and printed by the CLI. In a project whose central
claim is provenance, a destructive act on the gold set may not be silent.

The config-digest half of the guard is the freeze protocol in code: editing a
parameter without bumping `SYNTH_VERSION` now fails loudly at generation time.

### The models are asserted numerically, not just pinned

`tests/test_synth_determinism.py` pins current behaviour byte-for-byte, which says
nothing about whether that behaviour is what `synth/MODELS.md` describes.
`tests/test_render_models.py` asserts the documented formulas directly: the rigid
rotation's opposite-sign row shift, the quadratic smile amplitude, the flat-top and
Gaussian band profiles, the background ramp and blob reconstruction, dust
attenuation as `1 - (1 - transmittance)·exp(...)` (not `1 - transmittance·exp(...)`),
the scratch cross-section reconstructed from its recorded endpoints, half-up
rounding at quantization, and the noise variance
`gain*level + (read_noise_fraction*M)^2` with `gain = M/photon_full_well`.
Where a model writes a record into the ground truth, the test reconstructs the
pixels *from that record* — so the recorded geometry and the rendered pixels cannot
drift apart either.
This matters more than ordinary coverage, because MODELS.md is the document a
reviewer uses in Phase 3 to judge `pipeline/` for special-casing — a doc that has
drifted from the code makes that check worthless.

Similarly, `box_iou` in the generator (which decides the `overlapping` ground-truth
label) and `iou` in `evals/metrics.py` (which decides detection F1) are separate
implementations, because `evals/` must not import `synth/`. A test pins them to the
same numbers on a shared fixture set.

### Result schema is a contract, deliberately open in one place

`schema/result.schema.json` is written now and produces nothing yet. It pins what
PLAN.md commits to — ROI coordinates, software version, QC flags, normalization mode
and warnings, explicit exclusion reasons, an explicit background method — and it
leaves the *contents* of each parameter sub-object open, because Phase 1 owns those
keys. Band and image QC flag vocabularies are shared with the ground-truth schema so
`qc_flag_accuracy` can score them directly.

---

## Phase 1 — core pipeline

Dev-split scores this phase closes on, with `configs/default.yaml`
(`python -m evals.run --config configs/default.yaml`, IoU ≥ 0.5 = `PLAN_IOU_THRESHOLD`,
30 images / 150 lanes / 352 bands):

<!-- sweep: shipped_configs -->
| quantity | value |
|---|---|
| lane detection | **F1 0.9967** (149 tp, 0 fp, 1 fn) |
| band detection | **F1 0.8506** (279 tp, 25 fp, 73 fn) |
| intensity recovery, all 279 matched bands | mean \|error\| **17.39%**, median 6.50% |
<!-- end sweep -->

<!-- sweep: matched_band_subsets -->
| matched-band subset | bands | mean error % | median error % |
|---|---|---|---|
| `no_saturated` — no truth `saturated` flag | 256 | 17.06 | 6.25 |
| `unflagged` — no truth QC flag | 211 | 7.05 | 4.60 |
<!-- end sweep -->

**Read the lane number with its caveat, here rather than 200 lines down.** A lane ROI's
vertical extent is not detected: it is fixed at the full image height (the entry below
argues why). So the y-axis contributes exactly 1.0 to every lane IoU by construction, and
lane F1 measures horizontal lane-finding only. It is a real score against the declared
convention, and it is structurally easier than the band number beside it.

The last two rows are not in the runner's output. The final one is the honest figure for
*aperture and background* accuracy: it drops the saturated bands, whose truth is
unrecoverable, and the unresolved doublets, whose merged ROI over-reads by construction —
`synth/MODELS.md` §8 puts the noise-free best case at 37–52%, and measured through this
pipeline it is larger:

<!-- sweep: matched_band_subsets -->
| matched bands of 279 | share | signed error |
|---|---|---|
| carrying `overlapping` | 52 (18.64%) | +62.68% mean |
| `overlapping` primaries only | 46 | +54.05% mean, +56.39% median |
| no truth flag at all | 211 | — |
<!-- end sweep -->

The first three rows of the headline are what the phase reports, because a number that
quietly excluded its hard cases is the failure this project is built against — but a
reader asking "how well does this measure a band it can actually see" should read the
last row.

The test split was not read, scored, or tuned on in this phase.

### How the figures in this section are kept true

Three review cycles found stale dev-split figures here and in `configs/*.yaml`. Every
time the cause was the same: a parameter moved, and the recorded surfaces were patched
by hand instead of re-measured. Two mechanisms now close that, and the claim below is
exactly as strong as they are — no stronger.

- **`python -m evals.sweep`** re-runs every sweep quoted in this section and in both
  configs, and writes `evals/dev_sweeps.json` plus a readable `evals/dev_sweeps.md`.
  `python -m evals.sweep --check` re-runs them and exits non-zero naming any figure that
  has moved: exactly, for the record's structure, its header and the two shipped config
  digests; within a per-class tolerance for the measured figures, for the reason the next
  entry sets out and no wider than the drift measured there. CI runs `--check` on every
  push, so a parameter change that invalidates a recorded surface fails the build. It takes
  about two minutes, which is why it is a CI step rather than part of `pytest`.
- **`tests/test_recorded_figures.py`** checks the transcription, which is the half a
  re-measuring tool cannot: every figure quoted in this section or in a config comment
  sits inside a block marked with the sweep it came from, and the test fails if a number
  in such a block is not a value or a recorded figure of that sweep. This is what makes
  hand-transcription errors — the actual failure mode of the last three cycles —
  impossible to commit silently.

So, precisely: **every figure inside a marked block in this section and in
`configs/*.yaml` is mechanically tied to `evals/dev_sweeps.json`, and that record is
mechanically re-measured from the committed gold set in CI.** The tie to the record is
exact; the re-measurement is exact for the parameters the record was measured under and
within a stated tolerance for the figures, as the next entry sets out. No weaker and no
stronger.

Numbers *outside* a marked block are not covered by the checker. Cycle 4 found that the
previous version of this paragraph claimed a three-way exhaustive taxonomy that several
figures fell outside of, so it is enumerated again, and this time the gold-set measurements
it used to omit have been moved *into* blocks (`doublet_cost`, `lane_roi_geometry`,
`band_roi_sizes`, `presmooth_variance`, `matched_band_subsets`). What is left outside is:

- **Arithmetic on block figures**, where every operand is in a block on the same page: a
  difference between two F1s, a ratio between two error figures, `352 - 52 = 300`, and the
  `0.5/(1-f)` background quantile implied by a recorded coverage.
- **Closed-form computations that touch no gold set**: the Gaussian and flat-top mass
  fractions, and the box-ratio IoU table, each stated with the formula it comes from.
- **Two named one-off measurements** the record does not carry: the matched-set diff between
  `h = 0.06` and `0.08` (7 bands gained, 1 lost, unflagged 211 → 217), and the lane count
  `dev_01` yields with a non-robust lane scale (1 of 6). Each says in place what it measured.
- **One figure asserted by a test rather than by the record**: the +1.9%-of-amplitude
  background bias at the largest band, which `tests/test_pipeline_background.py` pins.
- **Gold-set figures quoted from `synth/MODELS.md` rather than measured here**: §8's
  +37.3% to +51.7% best-case doublet over-read, and `roi_mass_fraction` 0.9732–0.9899. The
  second is load-bearing for the "not chosen to match the generator's own aperture"
  argument, so it is worth naming: both are the generator's own recorded numbers, checked
  against `data/ground_truth/` rather than against the sweep record, and both reproduce.
- **The evidence behind the `--check` tolerance policy**, in the entry below. This kind was
  added along with the policy itself, and it is four things, none of them a figure the
  record holds: the pinned dependency versions; what the x86-64 CI runner measured, which
  is a reading from a CI log on another machine; the one-off diagnostics that identified
  the cause and sized the leverage, each stated in place with what it measured; and the
  tolerance constants themselves, which live in `evals/sweep.py`. Two derived kinds come
  with them. A difference between a CI reading and a record figure — "154.73 → 161.38, 4.3%
  relative" — has one operand outside any block, so the first bullet does not cover it and
  this one does. The *committed* side of every such pair is a record figure, and each one
  is quoted inside a marked block, so those are covered normally; the three band-F1 effect
  sizes quoted for calibration are differences between two block figures, so they fall
  under the first bullet.

Phase-0 sections of this file are outside all of this; the claim covers the Phase 1
section and `configs/*.yaml` only. It does not extend to `evals/sweep.py`'s own docstrings,
which are deliberately written to need no coverage: they quote drift magnitudes, leverage
measurements and their own constants, and where a derivation depends on a record figure —
that the band-F1 tolerance still separates real parameter effects, that the Monte-Carlo
tolerance still separates border handling — the docstring states the property and
`tests/test_sweep_check.py` asserts it from the record instead of transcribing a number.

**Human ruling: this tooling stays, as a ratified deviation from PLAN.md's Phase 1 file
list.** PLAN.md's Phase 1 deliverables name `pipeline/*`, the CLI and `evals/run.py` v0;
they do not name `evals/sweep.py`, `evals/dev_sweeps.{json,md}` or
`tests/test_recorded_figures.py`. Those three exist anyway, and the human ratified them
after cycle 4 rather than have them removed, for a stated reason: **four consecutive review
cycles found recorded dev-split figures in this file and in `configs/*.yaml` that did not
reproduce**, and hand-transcription was the cause every time. The tooling makes the claims
layer mechanically checkable — `--check` re-measures the record from the gold set in CI, and
the transcription test fails on a figure that is not the record's. It is recorded here as a
deviation rather than folded in silently, because PLAN.md is the contract.

**Why a sweep harness is a Phase 1 artifact and not Phase 3 work.** PLAN.md puts
"iterate detection/background parameters on the dev split to plateau" and
`evals/history.md` in Phase 3, and this is deliberately neither. `evals/sweep.py` exists
to make Phase 1's *recorded claims* checkable — the review process demanded a mechanical
guarantee that the figures in this file reproduce — not to search parameter space or to
log iterations. It has no per-difficulty-cell breakdown and no history log, both of which
are explicitly Phase 3, and it reads `evals.run.EVALUATED_SPLIT` with no flag that could
point it at the test split. If Phase 3 wants an iteration harness it can build on this,
but nothing here anticipates it.

### Why `--check` compares some figures within a tolerance

`python -m evals.sweep --check` passed on the arm64 development machine and failed on the
x86-64 CI runner, on the same commit with identical pinned dependency versions (numpy
2.4.6, scipy 1.17.1, scikit-image 0.26.0, opencv 5.0.0). Sixteen figures moved, all of them
inside the `background.local_median.window_px` sweep at 61 and 81. The chain was measured
link by link before anything was loosened, and each link below is a measurement:

1. **Not the dependencies.** The versions match, so the difference is architectural.
2. **Not the median filter.** The `local_median` background is exactly integer-valued on
   this data (`bg == np.round(bg)` everywhere, and so is the corrected image), and a median
   is a selection among exact integers, so it is bit-identical on both machines.
3. **Not a band sitting on its threshold.** The smallest relative margin between any band
   peak's prominence and its threshold is 2.1e-4 — twelve orders of magnitude above
   float64 last-bit noise.
4. **The float non-determinism enters at the profile means**, `corrected[:, a:b].mean(axis=1)`,
   where summation order and SIMD blocking differ between NEON and AVX.
5. **Ties are common**, because the corrected image is integer-valued: about 2% of adjacent
   profile samples are exactly equal (598 of 28459 at window 51), so plateaus abound.
6. **A last bit becomes a whole pixel.** `find_peaks` resolves plateaus and ties by
   position, and ROI edges are integers. Perturbing one lane slice by 1 ULP moved 2 of 303
   band ROIs at window 61.
7. **A whole pixel becomes a changed matched set.** A one-pixel shift can cross the IoU ≥
   0.5 matching boundary, so tp/fp/fn flip and the *matched set* the error statistics are
   aggregated over changes with them.

Link 7 is the binding constraint, and it is why the drifting quantities are not all small.
Five of the figures the 81 row records:

<!-- sweep: background.local_median.window_px -->
| window | band F1 | precision | false positives | mean \|e\|% | max \|e\|% |
|---|---|---|---|---|---|
| 81 | 0.7938 | 0.8658 | 40 | 18.31 | 154.73 |
<!-- end sweep -->

On x86-64 the same code, on the same committed pixels, measured 0.7994, 0.875, 37, 18.87
and 161.38 for those five. The last is a 6.65-point move, 4.3% of the recorded value: one
band with a large error entered the matched set, and an extreme carries that band wholesale.
So a single blanket epsilon is not available either — the same 1-ULP cause moves a count by
3 and an extreme by 4.3%.

**Integer detection counts can legitimately differ by one or a few between platforms.**
That reads wrong — a count of detections is the last thing that looks like it should be
architecture-dependent — so it is worth stating plainly rather than leaving a reader to
rediscover it: the count is downstream of a whole-pixel ROI shift, which is downstream of a
tie, which is downstream of the last bit of a mean. The false-positive count in the table
above measured 37 on the other machine.

So `--check` splits the record in two. Compared **exactly, and in both directions** (a
sweep, value or field that only the committed record has fails as loudly as one only the
measurement has):

- the structure: the set of sweep names, of value labels within a sweep, and of fields
  within a value;
- each sweep's `parameter` and `note` — see the prose rule below;
- the header: `split`, `iou_threshold`, `images`, `truth_lanes`, `truth_bands`;
- **`shipped`, the two config digests** — see the guarantee below;
- `evals/dev_sweeps.md`, against what the **committed** JSON renders. It used to be
  compared against what the fresh measurement renders, which quietly made every figure in
  the report exact again and is why the report appeared in the CI failure at all. Rendering
  the committed record makes it a transcription check between two committed files: exact,
  and the same on every machine.

Compared **within a class tolerance**, each constant named in `evals/sweep.py` with its
derivation beside it:

| class | figures | bound | evidence |
|---|---|---|---|
| platform-independent | `samples_in_window`, `target_secondary_bands_in_split`, the `lane_roi_geometry` figures, and the whole `truth` row of `band_roi_sizes` | exact | no measurement reaches them |
| detection count | every lane and band tp, fp, fn, plus `matched`, `clean_count`, `bands` and the `doublet_cost` and `flag_counts` tallies | ±4 counts | CI moved one by 3 |
| detection rate | `lane_f1`, `band_f1`, `band_precision`, `band_recall` | ±0.02 | CI moved one by 0.0092 |
| percentage-point error | `mean`/`median_absolute_percent` and the sweeps' `clean_*` columns | ±1.0 pp | CI moved one by 0.56; one matched band is worth 0.54 pp of the aggregate mean |
| flagged-subset error | the `overlapping_*` signed mean and median errors | ±4.0 pp | no drift; one band of that fifty-band subset is worth 1.81 pp |
| count share | `overlapping_share_percent` | ±2.0 pp | no drift; four matched bands are 1.4 pp of it |
| paired bootstrap | the aperture bootstrap's standard error, difference and interval | ±0.3 pp | no drift; one band is worth 0.058 pp of a paired difference |
| median ROI pixel size | the detected median ROI width and height | ±2 px | no drift; a tie flip moves one ROI edge by one pixel |
| extreme value | `max_absolute_percent` and the detected extreme ROI dimensions | ±10% of the figure | CI moved one by 4.3% |
| ROI area share | the detected window-coverage percentages | ±25% of the figure | no drift; an area whose sides are already allowed ±10%, or a median ROI's area under a 2 px step per side |
| Monte-Carlo variance ratio | the two `presmooth_variance` ratios | ±0.01% of the figure | no drift; only float64 accumulation noise, ~1e-12 relative, can reach them |

Two kinds of evidence set those bounds, and each constant says which it used. Where a class
**drifted in CI**, the bound is that drift rounded up to a round figure — 1.3× it for the
integer counts, which move in whole units, and 1.8× to 2.3× for the continuous statistics.
The multipliers are stated rather than averaged because each is checkable against the drift
beside it. Where a class **did not** drift, the bound is the measured *leverage* — how far
one band entering or leaving a subset moves
the statistic, measured over the dev split as a one-off diagnostic (the largest change from
dropping any single band; not committed) — times the number of bands a tie flip is observed
to move, or times the count bound where the figure is computed from those counts.

Neither is a worst case, and the policy does not pretend to be one. A class seen to drift
beyond its bound is a bound to re-derive from that observation, not a bound to widen
pre-emptively. The one thing a bound may never be is **tighter than a figure it is computed
from**: that guarantees a false failure on a movement the record already permits, which is
why the coverage percentages are not held tighter than the ROI dimensions they multiply, and
why a share of two counts is not held tighter than those counts.

The "do not widen pre-emptively" half was tested on this table rather than merely written
into it. The percentage-point bound stood at 1.5 pp for a draft, taken from leverage alone
while nothing had ever been observed to move one of those figures past 0.56. At 1.5 pp the
record's own measurement of a background window one notch from the shipped one — four
matched bands lost, band F1 down by more than the rate bound, clean mean |error| moved by
over a point — passes as a measurement of the shipped row. It is 1.0 pp, and
`tests/test_sweep_check.py` pins that case so the bound cannot drift back up quietly.

A tighter bound for the `clean_*` columns points the other way and is refused for the
mirror-image reason: it is justified on the aperture sweep, whose shared subset is large and
whose errors are small, and not on the smallest sweep in the record, so a bound that held on
one sweep would fail on another.

**A figure's class is a property of the quantity, not of its name.** `band_roi_sizes`
records the same nine figures twice — once over the detected ROIs, which move with the
detection, and once over the truth ROIs, which are read out of `data/ground_truth/` and
divided by a config parameter. Keyed by name alone, the truth row would inherit the
detected row's tolerances: the truth band count tolerated ±4 while the identical number in
the record header is compared exactly, and gold-set drift passing a check whose whole claim
is that it re-measures from the committed gold set. So tolerances resolve by
`(sweep, value label, field)` and fall back to the name, an override may only tighten, and
`tests/test_sweep_check.py` asserts that against the committed record.

**Nothing measured is interpolated into an exactly-compared note.** The aperture bootstrap's
note used to state how many bands its shared subset held. That is a matched-set count —
precisely the quantity a flipped tie moves — compared bit-exactly as prose while the
identical count is tolerated ±4 as `clean_count` two sweeps over. It would have failed CI on
the same mechanism this entry is about, in a field nobody would think to look at. The note
now points at the field that records it, and `tests/test_sweep_check.py` holds the line
generally: every number in a `note` or a `parameter` must be a value label of its own sweep
or one of a listed set of constants.

**The tolerances do not carry the "a parameter actually moved" guarantee, and are not
justified as if they did.** That guarantee is the exact comparison of `shipped`, the two
config digests: any change to any parameter in `configs/*.yaml` changes a digest, and a
digest mismatch fails `--check` outright no matter how well the figures reproduce. Verified
by editing the band `min_prominence_fraction` 0.30 → 0.31 and re-running: `--check` fails
with exactly one message, the digest — at that step not one figure moved beyond its class,
which is precisely why the guarantee cannot be asked of the figures.
`tests/test_recorded_figures.py::test_the_record_matches_the_shipped_config_digests` holds
the same line inside `pytest`, and `tests/test_sweep_check.py` pins the case that matters
most: every figure within tolerance, digest changed, still a failure.

**What `--check` still catches**, then:

- any shipped parameter edit, through the digest, regardless of figures;
- any algorithmic regression that moves band F1 by more than 0.02. For calibration, from
  the committed record: `extent_min_sigma` 2.0 → 0.0 moves band F1 by 0.0305, `window_px`
  51 → 81 by 0.0568, and `profile_smoothing_px` 5 → 9 by 0.0269 — all comfortably caught,
  and `tests/test_sweep_check.py` asserts that from the record so this paragraph cannot go
  quietly stale;
- any stale or hand-edited figure that is out by more than its class allows;
- any structural drift, and any change to a sweep's prose, exactly and in both directions;
- any `evals/dev_sweeps.md` that is not the transcription of the committed JSON.

What it no longer catches is a change smaller than a bound — including, honestly, a one-step
move in the flattest sweeps (`extent_min_sigma` 2.0 → 2.5 is 0.0031 of band F1) and a
sub-point move in an aggregate error. `--check` is a staleness and regression alarm, not a
bit-exactness proof, and the transcription checker, not `--check`, is what ties the figures
quoted in this file to the record.

**One consequence is worth naming rather than leaving to be discovered: `--check` does not
police the aperture ordering.** The paired bootstrap exists to resolve a difference several
times smaller than the ±0.3 pp its fields carry, and reports intervals narrower still, so a
re-measurement in which a looser aperture beat the shipped one — interval excluding zero, in
the opposite direction — passes silently. The bound cannot be tightened out of that: it
follows from the shared subset's own permitted movement, and a tighter one would fail on
drift the record already allows; recording the *resolved sign* as a field instead would only
move the fragility, since the narrowest interval sits closer to zero than four bands of
leverage. So the transcription of that table is checked and its re-measurement is not, and
that is an open item below rather than a claim retired quietly.

**One thing not to read into this.** The shipped `window_px: 51` row did not drift while 61
and 81 did. That is luck — whether any band in a particular configuration sits close enough
to a tie for the last bit to matter — and not a property of the shipped value. The shipped
row is one flipped tie away from drifting like its neighbours, which is the argument for a
tolerance policy rather than for pinning the rows that happen to reproduce.

### The Phase 1 result is a strict subset of the result schema, and says so

`schema/result.schema.json` requires `normalization`, `image_qc_flags`, per-band
`qc_flags` and `excluded_from_normalization`, and a `normalization` block in the
parameter echo. All five are produced by `pipeline/qc.py` and `pipeline/normalize.py`,
which are Phase 2. Two ways to handle that: emit empty structures now, or emit nothing
and not claim validity.

Empty structures were rejected. `"qc_flags": []` is indistinguishable from *"QC ran and
this band is clean"*, and `"ratios": []` from *"normalization ran and produced no usable
ratio"* — a silent fallback presented as a result, which is the specific failure this
project exists to avoid. An absent key cannot be misread. So Phase 1 emits every field
it produces in exactly the shape the schema defines, emits no field the schema does not
define, and omits the Phase-2 fields; `pipeline.analyze.PHASE2_RESULT_FIELDS` names
them and `tests/test_pipeline_result.py` pins the decision from both sides — the
document validates against the schema with exactly those requirements relaxed, and
fails the unmodified schema for exactly those reasons and no others. Phase 2 deletes
the gap; nothing about the schema was changed to accommodate Phase 1.

That test surfaced a consequence worth recording: the band `allOf` requires
`exclusion_reason` whenever `excluded_from_normalization` is true, and JSON Schema's
`properties` constrains only keys that are *present*, so omitting
`excluded_from_normalization` satisfies the `if` and pulls in the `then`. Phase 1
therefore also omits `exclusion_reason` (`PHASE2_CONDITIONAL_BAND_FIELDS`). Phase 2
should note that any band object must carry `excluded_from_normalization` explicitly,
even when false, or it inherits a requirement meant for excluded bands. Two other items
belong on the Phase 2 list: `schema_version` should stop declaring a version the document
knowingly fails, and the one place detection drops a peak without recording it — a lane
whose columns carry no spread over the peak's rows, documented and tested in
`_detect_bands_in_lane` — should surface a count rather than only a code comment.

The parameter echo follows the same rule in reverse: `configs/*.yaml` carry only the
parameters the pipeline reads, so there is no `normalization` block to echo yet, and a
config that carries parameters for the background method it did *not* select is
rejected. An echoed parameter that nothing read is a false provenance record.

### Lane ROI width — resolved: a detected Voronoi partition, one pitch wide

Ground truth declares each lane ROI one full lane pitch wide and full canvas height
(`synth/MODELS.md` §4a). Phase 0 left open whether that is the right target, since a
detector that finds a lane's actual signal extent (~0.75 pitch) would be penalised
against a box that includes the inter-lane gutter.

Resolution, entirely inside `pipeline/`: **lane boundaries are the midpoints between
neighbouring detected lane centres**, with the outer two placed half a median pitch
beyond the first and last centre and clamped to the image; height is the full image.
Three reasons, in order of weight:

1. It is what the *next* consumer needs. Phase 2's `total_protein` normalization
   integrates a whole lane profile, and a tight box around the bands would exclude the
   inter-band signal that mode is defined on. One lane rectangle serves both, rather
   than two rectangles that have to be kept consistent.
2. **It reproduces three of the five properties `MODELS.md` §4a lists, and that has to
   be said rather than denied.** The emitted lane ROI *is* one pitch wide, *does* start
   at `y = 0`, *does* span the full canvas height, and adjacent ROIs *do* tile: the
   helper that computes the boundaries is called `_lane_edges` and is documented as
   partitioning the image, `detect_lanes` guarantees non-overlap, and
   `tests/test_pipeline_detect.py` pins exact abutment. An earlier draft of this entry
   claimed "nothing assumes that ROIs tile the canvas", which the code contradicts on
   the same page it is written on.

   What is *not* reproduced is the fourth and fifth properties: nothing emits
   `margin + k*pitch` (centres are peaks of the measured column profile and the pitch is
   the median spacing of those centres, so a non-uniform gel gives non-uniform lanes),
   and nothing reproduces §4a's tilt widening by `|s| * H/2`. The tilt term is the
   discriminating one, and its absence is visible in the score — a detected lane ROI is one
   pitch wide, so where the truth box is wider than a pitch the quotient is the best IoU a
   correct detection can reach:

<!-- sweep: lane_roi_geometry -->
| lane geometry | median truth ROI width | median pitch | IoU ceiling |
|---|---|---|---|
| `straight` | 48 px | 45 px | 0.95 |
| `tilted` | 61 px | 45 px | 0.7475 |
| `smile` | 47 px | 45 px | 0.9702 |
<!-- end sweep -->

   A pipeline that had reverse-engineered §4a would reach ≈ 1.0 on the tilted cells instead
   of ≈ 0.75.

   The two properties that *are* shared are shared for reasons that hold without the
   generator. Full height and tiling are what a lane rectangle means in densitometry —
   ImageJ's gel analyzer takes exactly one full-height rectangle per lane — and the
   vertical extent is genuinely **not detected**: it is fixed at the whole image, which
   is an assumption about gel-doc framing, not a measurement. That is the weakest part
   of this design, and it is carried as an open question rather than defended here — see
   "The lane ROI's vertical extent is fixed, not detected" under Open items, which names
   Phase 3 as the phase that settles it.
3. It scores fairly against the declared convention without matching it by
   construction. On a straight cell the two coincide to a pixel or two. On a tilted
   cell the truth box is widened by the tilt excursion well beyond one pitch, so a correct
   detection scores IoU ≈ 0.75 (tabulated below) — comfortably above threshold, and *below*
   1.0, which is the honest outcome: the pipeline does not know the generator's widening rule
   and is not rewarded for guessing it.

**One-lane gels take a different rectangle, and that is a deliberate trade.** With two or
more lanes the width is the median spacing of detected centres. With exactly one there is no
spacing to measure, so the width comes from that lane's own column-profile extent under the
same threshold rule the band extent uses (`detection.lane.extent_relative_height` and
`extent_min_sigma`). An earlier revision used the canvas width instead and claimed the ROI
was "the full canvas" — which was false whenever the lone lane sat off centre, since the
rectangle was then clamped asymmetrically: a lane at x = 40 of 200 px produced x = 0,
width 140. The human chose the measured convention over the declared one, and accepted this
consequence:

> The measured-extent convention makes the band ROI — and therefore per-band integrated
> intensity — dependent on the lane slice, which differs between single- and multi-lane
> images (~15% tighter ROI in the lone-lane case, excluding ~6 px of true band signal, not
> only background). Absolute integrated intensities are therefore convention-dependent and
> must not be compared across images or conventions. Within one image the convention is
> uniform, so normalization ratios are unaffected.

The two figures are measured, not asserted: on the unit fixture one identical band
(`sigma_x` 8 px, amplitude 4000) measures **39 px** wide inside a 60 px multi-lane ROI —
matching the closed form `2·sigma_x·sqrt(2 ln(1/h)) + 1` = 38.95 px — and **33 px** inside
the 40 px single-lane ROI. 33/39 is the ~15%; the 6 px difference is band signal the tighter
slice puts outside the ROI, not gutter. The mechanism is that a band's horizontal extent is
walked on a column profile taken over the lane slice, so a tighter slice raises that
profile's `baseline_percentile` and lifts the edge threshold with it.
`tests/test_pipeline_detect.py` pins both widths, and pins centred, left-of-centre and
right-of-centre single lanes so a refactor cannot restore the canvas rule.

The scoring convention is therefore unchanged (`lanes[].roi` vs detected ROI at IoU ≥
0.5, same metric as bands). Measured cost of the mismatch on tilted cells: none visible.
Lane F1 is 0.9967 with no false positives, and the single false negative is `dev_03`,
whose cell is `background: speckle`, `noise: low`, `exposure: low`, `defect: scratch`,
`format_depth: jpeg8`, `band_shape: sharp`, `lane_count: 5` and — the point here —
`lane_geometry: straight`. So the one lane miss is not a tilted cell; it is a faint,
speckled, JPEG-compressed one. (An earlier draft described it as high-noise, which is
backwards: `dev_03` is on the `low` noise level.)

### Doublet resolution — resolved: one band per resolved maximum, and the cost is 46 bands

Phase 0 measured that the `doublet` cell sums to a **single local maximum with a
shoulder**, and that in 18 of 69 committed doublet lanes the partner's own peak is
under 3× the local per-pixel noise. The ruling for Phase 1:

**The band detector reports one band per resolved local maximum. It does not attempt to
deconvolve shoulders.** A shoulder-splitter would have to invent a second centre and a
second boundary from an inflection, and in exactly the cells where the answer matters —
weak lanes under `high` noise — it would be fitting noise. Reporting two numbers where
the data shows one peak is worse than reporting one, because both numbers would carry
provenance implying they were measured. The honest report for an unresolved doublet is
one band plus the `overlapping` QC flag, and the flag is Phase 2's job.

`tests/test_pipeline_detect.py` pins both halves on a fixture the pipeline has never
been tuned against: two peaks 4σ apart are reported as two bands, and a partner at
2.2σ / 0.65 amplitude is reported as one — with the test first asserting directly that
the profile really does have a single maximum, so the expectation is a property of the
data and not of the detector.

**Cost, measured on the dev split, not estimated.**

<!-- sweep: doublet_cost -->
| `missed_by_role` | bands |
|---|---|
| missed in total | 73 |
| `target_secondary` partners | 46 |
| `target` | 14 |
| `housekeeping` | 13 |
| partners present in the split | 52 |
<!-- end sweep -->

<!-- sweep: doublet_cost -->
| `missed_by_band_shape` | bands |
|---|---|
| `doublet` cells | 56 |
| `sharp` | 16 |
| `smeared` | 1 |
<!-- end sweep -->

With 52 partners in the split, recall is capped at 300/352 = 0.852 by this decision alone.
The measured 0.793 is 279 of 352, so **21** bands beyond the partners are missed — not the
27 an earlier draft inferred by subtracting 46 from 73. That subtraction double-counted:
6 of those 27 are primaries missed *because* their own partner matched instead, which is the
same ruling rather than a separate cause. The 21 are overwhelmingly in low-exposure or
high-noise cells. Those 6 partners are "matched" only because the merged ROI scored a
higher IoU against the partner's box than against the primary's, which makes the primary
the miss instead — one detection per doublet lane either way. Intensity pays too: the surviving band's ROI
spans the merged peak, so it integrates both partners and over-reads. `MODELS.md` §8 puts
the noise-free best case at +37.3% to +51.7%; measured through this pipeline it is larger,
and it is most of the gap between the headline aggregate error and the unflagged-band
figure beside it:

<!-- sweep: matched_band_subsets -->
| `flag_counts`, matched bands | value |
|---|---|
| matched | 279 |
| carrying `overlapping` | 52, i.e. 18.64% |
| signed error over those | +62.68% mean |
| primaries among them | 46 |
| signed error over the primaries | +54.05% mean, +56.39% median |
<!-- end sweep -->

If a future phase wants "resolves two closely spaced bands" as a measured capability,
that is a change to the *generator* (raise `doublet_offset_sigma` past the 2.55 σ where
a second maximum appears on the pixel grid), not to this detector, and it needs the
human ruling that Phase 0's open item asked for.

### `local_median` is the default background because a rolling ball reads the noise floor

Both methods are implemented, and `background.method` is required with no default.
`configs/default.yaml` selects `local_median`; `configs/rolling_ball.yaml` is identical
except for the background block. Measured on dev:

`default.yaml` is `local_median` with a 51 px window; `rolling_ball.yaml` is a 25 px
radius with a 9 px pre-smooth. Measured on dev:

<!-- sweep: shipped_configs -->
| config | lane F1 | band F1 | mean error % | median error % |
|---|---|---|---|---|
| `default.yaml` | 0.9967 | **0.8506** | **17.39** | 6.50 |
| `rolling_ball.yaml` | 0.9763 | 0.7847 | 23.65 | 6.43 |
<!-- end sweep -->

The reason is structural, not a tuning accident. A rolling ball is a grayscale opening,
so its estimate is the *minimum* of the surface under the ball — on a noisy image it
settles onto the noise floor and reads low by roughly the deficit of the minimum over
the ball's footprint, which grows with the per-pixel noise and is multiplied by the ROI
area when it reaches an integrated intensity. `presmooth_px` (a mean filter before the
ball, which is what ImageJ's implementation does) reduces that but cannot remove it.
The radius sweep shows both ends of that. ImageJ's default 50 px is worse on everything
and misses 19 of 150 lanes outright; 15 px scores higher aggregate band F1 than the
shipped 25 px and is still not shipped, because it is worse on the clean-band selector and
its 31 px footprint is *narrower* than the widest truth band ROI, which the record puts
at 43 px:

<!-- sweep: background.rolling_ball.radius_px -->
| radius | band F1 | aggregate mean error % | clean mean error % | lane tp/fp/fn |
|---|---|---|---|---|
| 15.0 | 0.8303 | 22.72 | 11.14 | 146/2/4 |
| **25.0** | **0.7847** | **23.65** | **10.14** | 144/1/6 |
| 35.0 | 0.7278 | 25.52 | 10.46 | 137/4/13 |
| 50.0 | 0.6941 | 28.21 | 13.25 | 131/5/19 |
<!-- end sweep -->

The `presmooth_px` sweep is the most direct evidence that the bias is what this entry says
it is. `clean signed` is the mean *signed* error on unflagged bands, so its sign is the
sign of the background error:

<!-- sweep: background.rolling_ball.presmooth_px -->
| presmooth | band F1 | clean signed error % | clean mean \|e\| |
|---|---|---|---|
| 1 | 0.5854 | +31.29% | 31.29% |
| 3 | 0.6740 | +7.40% | 8.72% |
| **9** | **0.7847** | **-0.74%** | **5.62%** |
| 21 | 0.8267 | -7.19% | 9.73% |
<!-- end sweep -->

With no pre-smoothing the ball rides the noise minimum and every intensity reads about a
third high — the predicted direction, at a magnitude that would make the method unusable.
Pre-smoothing walks that to well under a percent at the shipped 9 px, and by 21 px the ball
starts eating real signal instead. The widest window scores higher band F1 and loses on the
selector, so 9 px stands.

`local_median` has the opposite bias and a smaller one: band pixels inside its own window
push the median up, so a window in which a fraction `f` of pixels is band signal reports the
`0.5/(1-f)` quantile of the background rather than its median.

**How large `f` actually is, measured — because an earlier draft of this entry asserted a
bound it did not have.** It claimed band ROIs run "up to ~30 × 15 px" covering "~17% of the
window", and 17% is the *median* coverage, not a maximum:

<!-- sweep: band_roi_sizes -->
| ROI source | bands | median | widest | tallest | median coverage % | max coverage % | largest ROI |
|---|---|---|---|---|---|---|---|
| `truth` | 352 | 34 × 12 px | 43 px | 28 px | 16.34 | 45.21 | 42 × 28 px |
| `detected` | 304 | 34 × 18 px | 56 px | 89 px | 21.68 | 180.85 | 56 × 84 px |
<!-- end sweep -->

So the guarantee holds for a typical band and **degrades at the largest**: at 16% coverage
the window reports about the 0.60 quantile of the background, and at 45% about the 0.91
quantile. `tests/test_pipeline_background.py` now asserts that consequence at the measured
worst case rather than asserting it away — on a noiseless flat background a median-sized band
leaves the estimate exact, and a 42 × 28 px band biases it **+1.9% of the band's amplitude**,
high, which under-reads the band by the same proportion. Detected ROIs can
exceed the window outright, but those are the run-away ROIs on weak peaks that
`extent_min_sigma` limits, not bands the aperture is meant to fit.

**And `window_px` had no recorded surface at all until cycle 4.** It has one now, and it is
the one parameter where the two criteria disagree monotonically over the whole swept range:
a larger window is less contaminated by the band, so it *measures* better, while it follows
the background less well, so it *detects* worse.

<!-- sweep: background.local_median.window_px -->
| window | lane F1 | band F1 | aggregate mean error % | aggregate median error % | clean mean error % | clean signed error % |
|---|---|---|---|---|---|---|
| 31 | 0.9801 | 0.8782 | 19.11 | 13.81 | 15.31 | -15.23 |
| 41 | 0.9967 | 0.8693 | 17.64 | 8.66 | 9.23 | -8.95 |
| **51** | **0.9967** | **0.8506** | **17.39** | **6.50** | **6.90** | **-6.52** |
| 61 | 0.9967 | 0.8397 | 17.72 | 5.17 | 5.74 | -5.18 |
| 71 | 0.9764 | 0.8228 | 17.89 | 4.72 | 5.22 | -4.52 |
| 81 | 0.9730 | 0.7938 | 18.31 | 4.19 | 4.90 | -4.12 |
<!-- end sweep -->

There is therefore **no argmin to appeal to**, and this is the one shipped value the
project's usual selector does not choose: on clean mean error alone the largest window swept
wins, monotonically. 51 px is shipped because it is the argmin of *aggregate* mean
|recovery error| — the figure PLAN.md commits the README to reporting — and one of the three
windows at maximal lane F1. The cost is stated rather than hidden: 61 px would measure
1.16 points better on the clean subset for 0.011 of band F1, and 81 px would measure 2
points better for 0.057 of band F1 and two lane false positives. A reader who weights
measurement over detection more heavily than this file does should prefer 61 px, and Phase
3's Gate 1 is the right place to settle it.

`tests/test_pipeline_background.py` asserts both methods' biases directly, in both
directions, rather than leaving them as claims here.

**A slope-preserving border extension was written, measured, and removed.** The concern
was that a background gradient would be mis-estimated at the image edge and leave a
bright rim that lane detection reads as a lane. It changes nothing: for a median with an
odd window, edge replication is already exact on a ramp (the replicated half fills
exactly half the window, so the median still lands on the centre sample), and the dev
scores were identical to three decimals with and without it. The test that would have
justified it is kept as an assertion that the border is no worse than the interior.

### Detection thresholds: two criteria for peaks, two for ROI edges

A band peak must clear **both** `min_prominence_fraction` (of the lane's own signal
range) and `min_prominence_sigma` (of the lane profile's own measured noise), because
the two reject different things: the sigma threshold rejects noise excursions, and the
fraction rejects gel *structure* — speckle blobs, dust, a scratch crossing the lane —
which is real signal and clears any noise threshold.

**What each is worth, measured.** "Removing" a criterion needs defining. The sigma
criterion can simply be set to `0.0`, which the validator accepts. The fraction cannot:
the validator requires it to be positive, so the nearest legal stand-in for absent is
`0.001`, and that is what the sweep records.

<!-- sweep: band.min_prominence_sigma -->
| `min_prominence_sigma` | 0.0 | 2.0 | **5.0** | 8.0 | 12.0 |
|---|---|---|---|---|---|
| band F1 | 0.8467 | 0.8467 | **0.8506** | 0.8484 | 0.8462 |
| false positives | 28 | 28 | **25** | 24 | 23 |
<!-- end sweep -->

<!-- sweep: band.min_prominence_fraction -->
| `min_prominence_fraction` | 0.001 | 0.05 | 0.1 | 0.2 | 0.25 | **0.3** | 0.35 | 0.5 |
|---|---|---|---|---|---|---|---|---|
| band F1 | 0.6984 | 0.7707 | 0.8122 | 0.8353 | 0.8442 | **0.8506** | 0.8444 | 0.8326 |
| false positives | 168 | 93 | 56 | 37 | 30 | **25** | 23 | 21 |
| true positives | 279 | 279 | 279 | 279 | 279 | **279** | 274 | 266 |
<!-- end sweep -->

The two are highly redundant and the split is very uneven: removing the fraction takes band
F1 from 0.8506 to 0.6984, while removing the sigma criterion takes it to 0.8467 and adds 3
false positives. Neither touches what is *measured*: the clean mean |error| column is
constant across both sweeps in `evals/dev_sweeps.md`, because these two thresholds decide
which peaks become bands and not where a band's edges fall. They are therefore pure
detection parameters, band F1 is the right selector for them, and 0.30 and 5.0 are its
argmax.

Two earlier figures in this paragraph were wrong and are worth recording as a pattern.
The first draft claimed "either alone costs 3–7 points", which does not reproduce at all.
The correction then said "7 points is what `min_prominence_fraction: 0.05` costs" — also
stale, carried over from a superseded parameter set: against the shipped config that
variant scores 0.7707, so the cost is 8.0 points. A sentence whose job is to correct a non-reproducing number is the last place a
stale number should survive, which is why the figures above are now transcribed inside
checked blocks instead of retyped.

The sigma criterion is kept on that 0.4-point margin for one reason, stated plainly as a
judgement and not as a measurement: it is the only one of the two whose scale does not
depend on the brightest feature in the lane. A lane containing one saturating band and
one faint band has a large signal range, so the fraction threshold rises with the bright
band while the sigma threshold does not. Nothing in the dev split makes that case
decisive; if a later phase finds it never matters, the parameter should go.

Noise is measured, per profile, from the median absolute deviation of the profile's
first differences: differencing removes anything smooth on the scale of a sample, and
the MAD ignores the few large differences at a band's own edges. Both correction
factors in `profile_noise_sigma` are properties of the estimator (MAD→σ for Gaussian
data, and the variance doubling from differencing), not tunable numbers, and the
function is tested against a known noise level and against noiseless structure.

The lane threshold is taken against a **robust** profile range — the spread between the
10th and 90th percentiles — rather than max-minus-min. A near-vertical scratch is
brighter than any lane and otherwise sets the scale for the whole image. With
`robust_range_percentile: 0.0`, which is exactly max-minus-min, `dev_01` yields **1 lane
of 6** — five lost — at both 0.25 and 0.40 lane prominence, so the loss is the scale and
not the threshold. Across the whole split the same change costs 20 of 150 lanes:

<!-- sweep: lane.robust_range_percentile -->
| percentile | 0.0 | 2.0 | 5.0 | **10.0** | 20.0 | 30.0 |
|---|---|---|---|---|---|---|
| lane F1 | 0.9123 | 0.9338 | 0.9695 | **0.9967** | 0.9933 | 0.9868 |
| lane tp/fp/fn | 130/5/20 | 134/3/16 | 143/2/7 | **149/0/1** | 149/1/1 | 149/3/1 |
<!-- end sweep -->

(An earlier draft said four of six lanes on `dev_01`; re-measured, it is five.)

ROI edges use the same pairing: the peak has to fall to `extent_relative_height` of its
height above the lane baseline, **or** to within `extent_min_sigma` of the baseline,
whichever comes first. Without the noise floor a weak peak never reaches the relative
threshold before the noise does, and its ROI grows until it hits its neighbour:

<!-- sweep: band.extent_min_sigma -->
| `extent_min_sigma` | 0.0 | 1.0 | 1.5 | **2.0** | 2.5 | 3.0 |
|---|---|---|---|---|---|---|
| band F1 | 0.8201 | 0.8293 | 0.8384 | **0.8506** | 0.8537 | 0.8476 |
| clean mean error % | 7.09% | 7.08% | 6.98% | **6.89%** | 6.91% | 7.01% |
| aggregate mean error % | 17.98% | 18.07% | 17.47% | **17.39%** | 17.45% | 18.03% |
<!-- end sweep -->

So the floor takes band F1 from 0.8201 to 0.8506, and most of what it buys is oversized
ROIs that were scoring as a false positive and a false negative at once. 2.5 reaches 0.8537
and is worse on the clean selector and on aggregate error alike, so the round 2σ boundary
stands.

### `extent_relative_height`: why band F1 cannot select an aperture

This parameter has now been wrong twice, for two different reasons, and both are recorded
because the second one is the interesting one.

**First error: the criterion was stated in the wrong dimensionality.** The extent rule is
applied once per axis — to the row profile and to the column profile — so the fraction of
a band the resulting *rectangle* encloses is the **product** of the two 1D marginals, not
one of them. Earlier drafts quoted `erf(sqrt(ln(1/h)))` (one marginal) as if it were a
property of the ROI:

| `extent_relative_height` | 1D marginal | 2D, ideal Gaussian | 2D, Gaussian × flat-top |
|---|---|---|---|
| 0.05 | 98.56% | 97.15% | 98.07% |
| **0.06** | **98.23%** | **96.49%** | **97.61%** |
| 0.08 | 97.54% | 95.14% | 96.67% |
| 0.10 | 96.81% | 93.73% | 95.67% |
| 0.20 | 92.72% | 85.97% | 90.05% |

The fourth column is the honest one for this project, since a band here is Gaussian along
the migration axis and flat-topped across the lane. On that column a criterion demanding
97.5% of the band caps `h` at **0.0625**; on the ideal-Gaussian column it would cap it at
0.0444. (An earlier draft quoted the 0.0444 figure one sentence after calling the
flat-top column the honest one — the two are not interchangeable.)

**Second error, and the one worth reading: the reason given for refusing band F1 as the
selector was false.** The previous draft said the F1 advantage of a tighter aperture was
cancellation of the doublet over-read. That is true of the *error* metric and not of F1. I
diffed the matched sets: going from `h = 0.06` to `0.08` gains 7 bands and loses 1, and
**6 of the 7 gained carry no truth flag at all** — the unflagged matched count rises from
211 to 217. So the F1 gain is additional clean-band detections crossing IoU ≥ 0.5, not
cancellation.

The real reason F1 cannot select this parameter is that **F1 at IoU ≥ 0.5 is nearly blind
to aperture size over the entire range being swept.** Truth ROIs are the bbox of the
region where a band exceeds `bbox_relative_threshold`, i.e. ±1.5645 σx (flat-top) and
±2.4477 σy (Gaussian). A concentric box scaled by `kx`,`ky` scores IoU `kx·ky`, so:

| `h` | box vs truth box | best-case IoU |
|---|---|---|
| 0.06 | 0.9844 × 0.9691 | 0.9540 |
| 0.08 | 0.9582 × 0.9182 | 0.8799 |
| 0.12 | 0.9172 × 0.8413 | 0.7716 |
| 0.20 | 0.8561 × 0.7330 | 0.6275 |
| 0.30 | 0.7962 × 0.6340 | 0.5048 |

A well-centred band passes the 0.5 threshold at every one of those apertures — the
criterion does not start rejecting correct detections until `h ≈ 0.30`, where the box
holds under 80% of the band. What F1 *does* respond to in this range is the tail of
over-extended ROIs on weak, noisy peaks: shrinking every aperture rescues those. So
maximising F1 buys a handful of sporadic detections by paying a systematic aperture error
on all 279 measurements, and it keeps paying — aggregate band F1 rises monotonically all
the way to the *tightest* aperture swept. (Direction, since it is easy to invert: a larger
`extent_relative_height` is a higher edge threshold, so it stops the walk sooner and gives a
**smaller** ROI. The record shows it — `clean signed error` runs from -6.80% to -10.07% as
`h` rises, an under-read growing as the box shrinks.)

<!-- sweep: band.extent_relative_height -->
| `h` | 0.04 | 0.05 | **0.06** | 0.07 | 0.08 | 0.1 | 0.12 | 0.2 |
|---|---|---|---|---|---|---|---|---|
| band F1 | 0.8293 | 0.8415 | **0.8506** | 0.8628 | 0.8689 | 0.8750 | 0.8811 | 0.8841 |
| clean mean error % | 7.20% | 7.12% | **7.12%** | 7.19% | 7.27% | 7.53% | 7.83% | 10.22% |
| clean median error % | 4.89% | 4.62% | **4.61%** | 4.74% | 4.95% | 5.59% | 5.69% | 8.67% |
| clean signed error % | -6.82% | -6.78% | **-6.80%** | -6.88% | -6.98% | -7.26% | -7.61% | -10.07% |
| clean n | 206 | 206 | **206** | 206 | 206 | 206 | 206 | 206 |
<!-- end sweep -->

An F1 selector runs away to `h = 0.20`, an aperture holding ~90% of the band whose clean
mean |error| is 10.22% against 7.12% — 43.6% larger. There is a second reason to distrust it, which the reviewer
raised and which I think is real but secondary: the truth ROI is built at the generator's
own `bbox_relative_threshold`, so tuning an aperture to maximise IoU against it is
aperture-matching to `MODELS.md` §4 — but note that maximising F1 at a 0.5 threshold does
not even push toward the truth box, it pushes *away* from it. The blindness is the
first-order problem.

**So the selector is the clean subset**: matched bands carrying no truth QC flag, over one
fixed 206-band subset matched at every candidate so the comparison is not confounded by
detection changing underneath it. There the aperture's own signature appears — signed
error is negative everywhere, as a mass loss must be — and |error| has a flat minimum at
0.05–0.06, rising on both sides. `0.06` is shipped; `0.05` measures the same and is
refused only because it equals the generator's `bbox_relative_threshold`, so `0.06` is one
step away at no measurable cost. It also sits just inside the 0.0625 cap the flat-top mass
column gives, which is a consistency check rather than the reason.

**What it costs, stated plainly.** Aggregate band F1 is 0.8506 at `h = 0.06` against
0.8689 at 0.08 and 0.8841 at 0.20, and aggregate mean |recovery error| is 17.39% against
17.09% at 0.08. Those are the numbers this phase reports, and they are worse than an
F1-selected pipeline would report. That is the trade, made deliberately: 6 more clean
detections at 0.08 against a systematically larger aperture error on every band, on a metric
that cannot see the difference until the aperture is far *tighter* than any of these — the
geometry table above puts the first correct detection at risk only around `h = 0.30`.

**Is that 0.15-point difference even resolved?** It is worth asking, since 0.0335 of band F1
is being traded on it. A paired bootstrap over the 206 shared bands answers it. The
interval column is a 95% percentile interval on the paired difference; where it excludes
zero, the ordering is resolved.

<!-- sweep: aperture_selector_uncertainty -->
| `h` | clean mean error % | bootstrap SE | difference from shipped | interval |
|---|---|---|---|---|
| 0.04 | 7.20 | 0.47 | 0.07 | -0.01 to 0.16 |
| 0.05 | 7.12 | 0.46 | -0.01 | -0.04 to 0.03 |
| **0.06** | **7.12** | **0.46** | **0.00** | — |
| 0.07 | 7.19 | 0.45 | 0.06 | 0.03 to 0.1 |
| 0.08 | 7.27 | 0.44 | 0.15 | 0.09 to 0.21 |
| 0.1 | 7.53 | 0.43 | 0.41 | 0.29 to 0.53 |
| 0.12 | 7.83 | 0.41 | 0.70 | 0.53 to 0.87 |
| 0.2 | 10.22 | 0.39 | 3.09 | 2.77 to 3.4 |
<!-- end sweep -->

So 0.06 beats 0.07, 0.08 and everything looser at 95% confidence — the selector does carry
the weight. It does **not** separate 0.06 from 0.05 (interval spans zero), which is exactly
what the entry already says: those two measure the same, and the coincidence rule picks
between them. The bootstrap resamples bands, so it captures sampling across bands and not
the 30-image sample; it is a floor on the uncertainty, not a full account of it.

**The selector cannot be computed on a real blot.** It is defined by *ground-truth* QC
flags, so it exists only on synthetic data. That is a real limitation for Phase 3: the
parameters shipped here were chosen by a criterion the real-blot half of the evaluation
cannot reproduce, and Phase 3's ImageJ comparison is the external check on whether that
choice generalises. It is not circular with respect to the generator's ROI rule, though —
re-scoring the aperture sweep against `true_total_intensity_dn` (the band's whole signal,
not the part inside the truth ROI) leaves the ranking and the 0.05–0.06 argmin unchanged.

**Not chosen to match the generator's own aperture.** The truth ROI encloses 0.9732–0.9899
of each band (`roi_mass_fraction`), and 0.06 lands at 97.61% for this band shape, inside
that range. That agreement is a *consequence*: picking `h` to reproduce
`roi_mass_fraction` would be tuning to the generator's ROI construction, which
`MODELS.md` forbids. The clean-band error would read the same way against any ground
truth.

### `min_prominence_sigma` and where the shipped values coincide with generator constants

`synth/MODELS.md`'s closing rule is that a parameter equal to a number in that document
counts as tuning-to-the-generator *unless* it has "an independent justification recorded
in the config and in NOTES.md". An earlier draft of this file instead claimed that **none**
of the shipped thresholds equals a number in `MODELS.md`. That is false, and a false
denial is worse than the coincidence it denies, because it is what the next reviewer
checks instead of checking the values. **Seven of the fifteen** numeric processing
parameters do coincide with a generator parameter. (Fifteen, not thirteen: the single-lane
rule added `detection.lane.extent_relative_height` and `detection.lane.extent_min_sigma`.
The tally below is a fresh audit of all fifteen against `MODELS.md`, not the old one with
two added.)

| shipped parameter | value | generator parameter with the same value | independent justification |
|---|---|---|---|
| `detection.profile_smoothing_px` | 5 | `aspect_ratio` (`sharp`) = 5.0 | minimum of clean-band recovery error and smallest window reaching lane F1 0.997; surface in the config |
| `detection.band.min_separation_px` | 1 | `roi_width_fraction_of_pitch` = 1.0, `target_relative_levels[0]` = 1.0 | the smallest value the peak finder accepts, chosen *because* it is no floor at all (see the separation entry) |
| `detection.band.min_prominence_fraction` | 0.30 | `target_relative_levels[3]` = 0.30 | largest value that costs no real band on dev; surface in the config |
| `detection.band.min_prominence_sigma` | 5.0 | `aspect_ratio` (`sharp`) = 5.0 | the conventional 5σ detection threshold, from signal detection rather than from this gold set |
| `detection.band.extent_relative_height` | 0.06 | `base_fraction` (`gradient`) = 0.06, and `housekeeping_jitter_fraction`, which §4 writes as ±6% | minimum of clean-band recovery error (entry above) |
| `detection.lane.extent_relative_height` | 0.06 | the same two | inherits the band value, which was selected on the dev split for the band profile; the dev split has no single-lane image, so this one could not be fitted to anything, and it was copied from a sibling parameter rather than read off `MODELS.md` |
| `background.rolling_ball.presmooth_px` | 9 | `smile_amplitude_px` = 9 | ImageJ pre-smooths before rolling, and 9 px is the smallest odd window whose variance cut approaches the 81-sample ideal (recorded under `presmooth_variance`, quoted in `configs/rolling_ball.yaml`) |

The other eight — `lane.min_separation_px` 12, `lane.min_prominence_fraction` 0.40,
`lane.robust_range_percentile` 10.0, `lane.extent_min_sigma` 2.0,
`band.baseline_percentile` 10.0, `band.extent_min_sigma` 2.0, `local_median.window_px` 51,
`rolling_ball.radius_px` 25.0 — equal no parameter declared in `MODELS.md`. (2.0 in
particular does not: the generator's nearby values are `min_aspect_ratio` 2.5,
`doublet_offset_sigma` 2.2 and the `smeared` aspect ratio 2.6.) Two caveats on that scan, because "equals
nothing" is a stronger claim than it can support. It compares against `MODELS.md`, which
is the document the rule names, and not against every number in
`data/generation_config.json`: there, `split.test_count` is 10, which the two percentile
parameters equal, and `matrix.lane_counts[1]` is 5, which `profile_smoothing_px` equals.
And several shipped values equal digits appearing *somewhere* in `MODELS.md` prose — a
section number, a percentage inside a measurement — which is not what the rule is about.
Neither caveat changes a justification: the percentiles are chosen on the surfaces recorded
above, and both 5-valued parameters are already listed in the table.

Two observations worth carrying forward. First, the generator declares roughly forty
parameter values spread over the same numeric ranges a detector's thresholds live in, so
coincidence is close to unavoidable and avoiding it is not the goal — which is exactly
why the rule is phrased about justification. Second, moving `extent_relative_height` from
0.08 to 0.06 traded one coincidence (`base_fraction` `flat` = 0.08) for another
(`base_fraction` `gradient` = 0.06); that is not evidence either way, and the entry above
is the justification the rule asks for. `bbox_relative_threshold` = 0.05 is the one
coincidence deliberately *not* taken, because it is the generator's own aperture rule and
matching it would make the ROI comparison circular.

### The two `min_prominence_fraction` values, and where they actually sit on dev

Both are dev-chosen, and the shapes of the two surfaces are different, so they are
recorded separately. Neither is a plateau in the sense an earlier draft of this file
claimed.

**Lane, 0.40.** Lane F1 climbs monotonically to 0.35, is flat from 0.35 to 0.45, and
falls off a cliff at 0.50 as real lanes start being rejected:

<!-- sweep: lane.min_prominence_fraction -->
| lane `min_prominence_fraction` | 0.1 | 0.15 | 0.2 | 0.25 | 0.3 | 0.35 | **0.4** | 0.45 | 0.5 |
|---|---|---|---|---|---|---|---|---|---|
| lane F1 | 0.9236 | 0.9577 | 0.9737 | 0.9900 | 0.9933 | 0.9967 | **0.9967** | 0.9967 | 0.9589 |
| band F1 | 0.8112 | 0.8373 | 0.8424 | 0.8506 | 0.8506 | 0.8506 | **0.8506** | 0.8506 | 0.8362 |
<!-- end sweep -->

0.40 is the middle of the flat region, so neither the climb below nor the cliff above is
one step away. An earlier draft shipped 0.25 and described 0.20–0.30 as a plateau; it is a
monotone climb, and 0.25 is *weakly* dominated by 0.35–0.45 — strictly better lane F1,
equal band F1 — which is the honest way to put it.

**Band, 0.30.** Recall is flat and precision improves up to 0.30; from 0.35 on, real
bands start being rejected:

The surface is in the criteria entry above. So 0.30 is the largest value that costs no real band — which is the criterion the config
comment states, and the turn is between 0.30 and 0.35 rather than at 0.25 as an earlier
draft said. The limit is worth stating plainly: **a band weaker than 30% of the strongest
feature in its own lane is not reported.**

### `profile_smoothing_px`: the parameter with the largest effect, and a disclosed cost

Smoothing the two 1D profiles moves the scores more than the sigma criterion, the extent
floor and both separation floors put together, so its surface belongs here rather than
only in the config:

<!-- sweep: profile_smoothing_px -->
| px | lane F1 | lane fp | band F1 | aggregate mean error % | clean mean error % |
|---|---|---|---|---|---|
| 1 | 0.8805 | 28 | 0.7471 | 17.54% | 7.51% |
| 3 | 0.9868 | 3 | 0.8571 | 16.70% | 6.56% |
| **5** | **0.9967** | **0** | **0.8506** | **17.39%** | **6.49%** |
| 7 | 0.9967 | 0 | 0.8445 | 19.60% | 6.67% |
| 9 | 0.9967 | 0 | 0.8237 | 21.26% | 7.07% |
| 11 | 0.9967 | 0 | 0.7823 | 20.57% | 7.53% |
<!-- end sweep -->

**3 px is better on both aggregate numbers, not just F1**, and it is still not shipped.
An earlier draft disclosed only the F1 loss, which understated the cost of this choice;
the full comparison is above. Three reasons for 5 px, in order of weight:

1. On the clean subset — matched bands with no truth QC flag, the same selector used for
   the aperture — 5 px is the minimum, at 6.49% against 3 px's 6.56%. That is the
   statistic that answers "does the profile shape let us measure a band".
2. 3 px costs 3 lane false positives against zero, so it is worse at the *other*
   detection task in the same run.
3. Its aggregate advantage runs the same way as the aperture's did: a narrower smoother
   gives tighter ROIs, and on a set where 18.64% of matched bands are unresolved doublets
   reading +62.68% high, tighter ROIs are rewarded for clipping a neighbour's signal
   rather than for measuring their own band. The clean subset removes exactly that.

The value is a dev choice and is disclosed as one. Note that 1 px — no smoothing at all —
collapses both scores, so this parameter is doing real work and its inertness is not the
question; which side of the optimum to sit on is.

### `min_separation_px`: what it is for, and the fact that it does nothing here

Two peaks closer than `min_separation_px` are treated as one. Both values are floors
intended for real gels, and **both are inert on the dev split**:

<!-- sweep: lane.min_separation_px -->
| lane `min_separation_px` | 1 | 4 | 8 | **12** | 16 | 20 | 24 |
|---|---|---|---|---|---|---|---|
| lane F1 | 0.9967 | 0.9967 | 0.9967 | **0.9967** | 0.9967 | 0.9967 | 0.9967 |
| band F1 | 0.8506 | 0.8506 | 0.8506 | **0.8506** | 0.8506 | 0.8506 | 0.8506 |
<!-- end sweep -->

<!-- sweep: band.min_separation_px -->
| band `min_separation_px` | **1** | 4 | 8 | 12 | 16 | 17 | 20 | 24 |
|---|---|---|---|---|---|---|---|---|
| band F1 | **0.8506** | 0.8506 | 0.8506 | 0.8506 | 0.8506 | 0.8519 | 0.8519 | 0.8519 |
| false positives | **25** | 25 | 25 | 25 | 25 | 24 | 24 | 24 |
<!-- end sweep -->

17 px is the first band value that changes anything, and what it changes is one merged
false positive — 0.0013 of F1, which is not a reason to compromise the paragraph below.

`detection.band.min_separation_px` is deliberately **1**, the smallest the peak finder
accepts, i.e. no floor at all. A larger value would be a second, silent answer to the
question the "Doublet resolution" ruling above answers explicitly: per `MODELS.md` §4 the
doublet partner sits at `2.2 * sigma_y`, which is 5.02 px at 6 lanes, 6.03 px at 5 lanes
and 7.52 px at 4 lanes, so any floor in that range would suppress the partner *by
arithmetic* rather than because the profile has one maximum. An earlier draft shipped 6
px with no recorded rationale, which is exactly the coincidence `MODELS.md`'s closing
rule says to treat as tuning-to-the-generator. At 1 the ruling stands on its own
evidence, and the shoulder test in `tests/test_pipeline_detect.py` proves the profile is
unimodal rather than proving the floor fired.

`detection.lane.min_separation_px` stays at 12 px, which is a statement about the gels
this tool targets — below the pitch of the most crowded gel, above the width of a scratch
or dust speck — and not a fitted value; the dev split cannot distinguish it from 1.

### Saturated bands are counted in the headline recovery error, and shown separately

For a `saturating` cell the recorded truth is the intensity the band *would* have had
without clipping, which no pipeline can recover from a clipped image
(`synth/MODELS.md` §8). Dropping those bands from the recovery table would flatter every
number in it, so `evals/run.py` reports the headline figure over **all** matched bands
and prints a second line over the subset with no truth `saturated` flag, plus a
paragraph saying why the two differ. On dev the effect is small — 17.39% over all 279
matched bands against 17.06% over the 256 with no `saturated` flag, since only 23 matched
bands are saturated — but the reporting
rule is what matters: a number that excludes its hard cases has to say so on the same
screen.

### Small decisions that would otherwise have to be reverse-engineered

- **`background_estimate` is a level, not an integral.** It is the mean background in DN
  over the band's ROI, so the raw ROI sum is recoverable as
  `integrated_intensity + background_estimate * width * height`. The schema's band
  object is closed, so only one of the two could be reported.
- **Negative corrected pixels are kept** (`clamp_negative_pixels: false`, recorded).
  Noise is symmetric about the background, so the ROI sum is unbiased only if both signs
  survive; clamping adds ~0.4σ per pixel, which on a 600-pixel ROI at 80 DN noise
  invents ~19000 DN of "signal". The parameter exists so the choice is recorded rather
  than conventional, and a test measures both.
- **The container format is read from the file's signature bytes, not its extension.**
  A PNG named `.tiff` is loaded, and reported, as a PNG.
- **A multi-channel image raises** rather than having a channel picked for it. PLAN.md's
  MVP scope is single-channel grayscale; choosing a channel silently would change every
  intensity downstream.
- **`schema_version` names the contract targeted, not a claim of validity.** The
  document declares `1.0.0` while omitting five of that version's required fields, so a
  consumer that validates on the declared version gets `required` failures with no
  in-band explanation. Nothing consumes results yet, and inventing an out-of-band
  "partial" marker would need a schema change this phase may not make. Phase 2 closes
  the gap and is the right place to decide whether the field should ever be able to say
  "subset"; until then this file is the explanation.
- **`result_id` is content-addressed** — `sha256(source digest | config digest)` — so
  re-analysing the same image with the same parameters reproduces the same id on any
  machine, and the determinism test can compare whole documents with only
  `created_at` removed.
- **`source.ground_truth_image_id` is supplied by the caller**, never inferred from the
  filename. `evals/run.py` passes it because it is iterating ground truth already;
  nothing in the analysis path reads it. A test parses every `pipeline/*.py` and fails on
  an import of `synth` or `evals`, or on a non-docstring string literal naming
  `ground_truth/`, `pixel_sha256` or the `dev_`/`test_` id scheme.
- **The micro-average lives in `evals/metrics.py`, not in the runner.** Pooling per-image
  confusion counts and recomputing precision/recall/F1 from the totals is scoring
  arithmetic, and `evals/metrics.py` is the contract other phases depend on for that, so
  `micro_average_detection_scores` was added there (a new function; nothing existing
  changed) and the runner now does no scoring arithmetic of its own. It requires every
  input to carry the same IoU threshold, and it documents why a pooled 0.0 is allowed
  where `detection_scores` raises: each input has already passed that check.
- **`write_result` refuses to write into any `ground_truth/` directory.** PLAN.md's first
  key invariant is that the gold set has one writer; `--out` is a path from the command
  line, so the boundary is enforced in code rather than left to convention.
- **A one-column lane is a `DetectionError`, not a bare `ValueError`.**
  `profile_noise_sigma` needs two samples, and a lane one column wide is reachable
  through a legal config (`lane.min_separation_px: 1` with two adjacent peaks). It now
  raises a `PipelineError` subclass so the CLI prints `error: …` instead of a traceback.
  The same inconsistency remains in `pipeline/quantify.py` and in `Roi.__post_init__`,
  which raise bare `ValueError`/`TypeError`; those paths are programming errors that
  `detect` cannot reach with any legal config, so they are left as they are rather than
  converted into user-facing errors. Recorded here rather than silently accepted.
- **Lane width is measured two different ways, and both are stated in `detect_lanes`'
  docstring.** With two or more lanes it is the median spacing of detected centres; with
  exactly one it is that lane's own column-profile extent, under the band extent rule with
  `detection.lane`'s configured pair. Neither is a canvas-derived constant, and the extent
  pair is config rather than a literal.
- **`evals/run.py` has no `--split` flag.** Pointing it at the test split requires a code
  change, which is the point; a test asserts the parser's options are exactly
  `{--config, --data}`.

---

## Open items

Unresolved questions carried out of a phase. Not decisions — each one names the phase
that has to settle it.

### The aperture ordering is transcription-checked but not re-measurement-checked — Phase 3 to settle

`python -m evals.sweep --check` re-measures every recorded figure, but the tolerance the
aperture bootstrap's paired differences carry is several times the difference the aperture
selection turns on, and an order of magnitude above the intervals the bootstrap reports. So
a code or platform change that *inverted* the ordering — a looser aperture measuring better
than the shipped one, with an interval excluding zero — would not fail the check. The
figures themselves cannot go stale unnoticed, because `tests/test_recorded_figures.py` ties
every one quoted in the Phase 1 section to the record; what is unguarded is the record
being re-measured into a different conclusion.

Tightening the bound is not the fix: it is derived from how far the shared band subset is
itself allowed to move between CPU architectures, so a tighter one would fail on drift that
is already accepted everywhere else. Recording the resolved sign of each difference as its
own exactly-compared field only relocates the problem, since the narrowest interval in the
table sits closer to zero than a few bands of leverage. What would close it is a selection
statistic whose resolution is not of the same order as the platform noise — more dev images,
or a comparison that is not a difference of two means over one shared subset. That is a
Phase 3 question about the selector, not a Phase 1 question about the checker, which is why
it is recorded here rather than patched.

### The lane ROI's vertical extent is fixed, not detected — Phase 3 to settle

A detected lane ROI spans the full image height, always. Nothing measures where a lane
starts or ends vertically, so the y-axis contributes exactly 1.0 to every lane IoU by
construction and the reported lane F1 measures horizontal lane-finding only. The Phase 1
entry above argues why the convention is defensible — it is what a lane rectangle means in
densitometry, and Phase 2's total-protein integral wants the whole lane — but "defensible"
is not "measured", and this is the one property of the emitted lane ROI that no measurement
supports.

Phase 3 settles it, because that is where real blots arrive: a gel-doc image with margins,
a tilted crop, or two stacked gels in one frame is where a fixed full-height rectangle
stops being right. The decision needed is whether to detect the vertical extent from the
row profile or to keep the convention and state it as a documented limitation.

### The single-lane recovery-error impact is unquantified — Phase 3 to settle

The single-lane rule changes the band ROI a lane slice yields, and on the unit fixture that
is a 15% narrower ROI excluding about 6 px of true band signal (recorded under the lane-ROI
convention above). What that does to *recovery error* is unmeasured, because the committed
dev split has no single-lane image and so no sweep can reach the path. Phase 3 should
quantify the single-lane recovery-error impact on the synthetic single-lane cases — which
means the gold set needs some, so it is also an input to any regeneration decision.

### Phase 1 has already selected every parameter on dev, which pre-empts Phase 3's Gate 1

PLAN.md puts "Gate 1 (human): eval design sign-off **before parameter iteration begins**"
ahead of "iterate detection/background parameters on the dev split to plateau". Phase 1 has
now selected thirteen of its fifteen shipped numeric parameters from thirteen dev-split
sweeps, so that gate has been pre-empted in substance even though the artifact it produced —
`evals/sweep.py` plus `evals/dev_sweeps.json` — exists to make Phase 1's recorded claims
checkable rather than to iterate to a plateau.

What the human is being asked to ratify or redo:

- **The selector.** Parameters that change what is *measured* were chosen on the mean
  |recovery error| over bands whose ground truth carries no QC flag, on a fixed subset
  matched by every value of the sweep. Parameters that only change what is *found* were
  chosen on the relevant detection F1. The reasoning is in the aperture entry above; the
  headline consequence is that this phase reports a *worse* band F1 than an F1-selected
  pipeline would.
- **Two values the selector does not choose**, each disclosed where it is set:
  `band.min_separation_px` (an a-priori floor, set to impose none) and
  `background.local_median.window_px` (the two criteria are monotone in opposite directions,
  so no value optimises both; a reader weighting measurement more heavily should prefer
  61 px).
- **Two values the selector cannot exercise at all**, which is a stronger caveat than not
  being its argmax: `detection.lane.extent_relative_height` and
  `detection.lane.extent_min_sigma` are read only when exactly one lane is detected, and the
  committed dev split has 4, 5 and 6 lanes and never one. No sweep can reach them. They
  inherit the band extent rule's values, and the single-lane behaviour is pinned by unit
  tests rather than by any dev figure — so Gate 1 is looking at two parameters with **no
  evidence behind their values**, only a consistency argument.
- **That the selector cannot be computed on a real blot**, being defined by ground-truth
  flags. Phase 3's ImageJ comparison is the only external check on it.
- **Whether the test split may still be considered untouched by tuning.** It is untouched in
  the literal sense — no code in this phase can read it — but the parameters it will score
  were chosen on dev, which is exactly the situation Gate 1 exists to authorise.

**The human's ruling on this, recorded so Phase 3 inherits it explicitly, has two halves:**

1. **Gate 1 must ratify or redo the thirteen parameters Phase 1 selected on the dev split.**
   They are not provisional defaults that Phase 3 may quietly keep; they are a selection that
   pre-empted the gate, and the gate has to take a position on it either way.
2. **The clean-band selector needs a real-blot substitute in Phase 3.** It is defined by
   ground-truth QC flags, so it exists only on synthetic data, and Phase 3 is where real
   blots arrive. Something has to play its role there — ImageJ agreement on the CC-BY set is
   the obvious candidate — or the parameters shipped here rest on a criterion the real half
   of the evaluation cannot even compute.

If Gate 1 rejects the selector, the sweeps are already recorded and re-selecting from them
costs one regeneration of the record and a rewrite of the parameter entries; no pipeline code
depends on the choice.

### The `doublet` cell renders one peak with a shoulder, not two peaks — needs a ruling

**Partly settled in Phase 1** — see "Doublet resolution" above: the Phase 1 detector
reports one band per resolved maximum, which costs 46 dev bands. What is still open is
the generator-side half stated at the end of that entry: whether the cell is *meant* to
be resolvable, which would need `doublet_offset_sigma` raised past 2.55 and a
regeneration. That is a human call and `synth/` is frozen. Original entry follows.

Measured, not assumed: at the committed `doublet_offset_sigma = 2.2` and
`doublet_amplitude_ratio = 0.65`, the two partners sum to a **single local maximum**
with a strong shoulder. For that amplitude ratio the continuous sum of the two
Gaussians first becomes bimodal at 2.4605 sigma of separation; on the pixel grid a
second maximum appears at all three lane counts from 2.55 sigma; and 2.8 sigma gives
a 12% dip between the peaks. All three are properties of the profile and the sampling
grid, so they hold for every cell rather than for one measured image.

This predates the aspect-ratio fix and is untouched by it: the offset is expressed in
units of `sigma_y`, so the doublet profile is scale-free and its modality depends on
`doublet_offset_sigma` alone. `tests/test_generator.py` pins both halves — one peak at
2.2 sigma, two at 2.8 — so the cause is isolated in the suite rather than only here.

Whether that is the intended cell is a human call, so nothing was changed. Measured
over the committed set, the cell still does its job for the `overlapping` QC flag
(partner ROI IoU 0.3529–0.4286, both bands flagged) and for the truth contract (a
noise-free, background-free box sum over the weaker partner's ROI over-reads by
82.4–113.1%).

What it does **not** do uniformly is show the partner. It is tempting to settle that
on the noiseless, straight, defect-free fixture the tests use at the brightest lane,
where the shoulder is unambiguous; on the delivered images it does not hold
everywhere, so the figures below are taken from the committed set instead. Measured
that way, the partner's own peak runs from 52.5x the local
per-pixel noise sigma down to 0.6x, and is under 3x in 18 of the 69 committed doublet
target lanes — in the weak lanes (`target_relative_levels` down to 0.18) under `high`
noise, no single pixel establishes it. Integrated over its own ROI the partner clears
the noise in every committed cell, but by as little as 3.8 sigma (`test_07` lane 5;
the best cell is 601.8). So the shoulder is unmistakable in the bright, low-noise
doublet cells and marginal-to-invisible in the weak, high-noise ones.

That widens the ruling rather than settling it. "Resolves two closely spaced bands" is
a distinct pipeline capability from "notices a shoulder", and if Phase 1 is meant to
be scored on the former, the offset has to go up; separately, if the doublet cell is
meant to be a *detectable* partner in every cell, the weakest lanes need a floor on
partner contrast too, which the amplitude pattern does not currently provide. Cost of
raising the offset to 2.8: one regeneration, and the partner ROI IoU drops to
0.269–0.333 across the same 40-cell matrix (measured in memory, not written), still
well above `overlap_iou_threshold = 0.15`.

### Lane ROI width — deferred to Phase 1

**Settled in Phase 1** — see "Lane ROI width — resolved" above. The full-pitch
convention stands, matched by a Voronoi partition of detected lane centres rather than
by a tighter box, because Phase 2's total-protein integral needs the whole lane and two
lane rectangles would have to be kept consistent. `data/` was not regenerated and
`roi_width_fraction_of_pitch` stays `1.0`. Original entry follows.

`LaneLayoutConfig.roi_width_fraction_of_pitch` is `1.0`, so each declared lane ROI is
exactly one lane pitch wide. At that width the ROIs tile the space between the
margins: on straight cells adjacent lane ROIs abut, and on tilted cells they overlap,
because the tilt excursion widens each rectangle's effective footprint without
narrowing its neighbour's.

Whether that is the right target for Phase 1 is open. `lanes[].roi` is what Phase 1's
lane detection gets *scored against*, and a detector that correctly finds a lane's
actual signal extent would be penalised against a box that includes the inter-lane
gutter — so a tighter box (e.g. 0.85 pitch) may be the fairer reference. The counter-
argument is that a full-pitch ROI is what a total-protein lane integral wants in
Phase 2, and two different lane rectangles would then have to coexist.

Cost of changing it: the field already exists and is covered by the config digest, so
the change is one regeneration of `data/` plus a `SYNTH_VERSION` decision (and, if
`synth/` is frozen by then, an explicit human instruction and a break marker in
`evals/history.md`). Nothing is being changed in Phase 0; the value stays `1.0` and
the committed gold set stands.
