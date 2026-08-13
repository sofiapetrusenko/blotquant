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
  push, so a parameter change that invalidates a recorded surface fails the build. It is a CI
  step rather than part of `pytest` because it is slow: **about nine and a quarter minutes of
  CPU** on an arm64 machine (measured twice, 9m09s and 9m19s of CPU time). That figure is Phase
  2's, and Phase 2 is what made it slow — it was about two minutes when this entry was written,
  before the QC and normalization records added a pipeline pass. `.github/workflows/ci.yml`
  carries the same measurement and what dominates it.
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

**Superseded by Phase 2, and the pointers above are now historical.** Phase 2 produces all
five fields, so it deleted `PHASE2_RESULT_FIELDS`, `PHASE2_CONDITIONAL_BAND_FIELDS` and the two
tests that pinned the gap from both sides. What guards the same ground now is
`tests/test_pipeline_result.py::test_the_result_validates_against_the_full_schema` — the
document against the schema *as written*, with nothing relaxed — and
`::test_every_band_states_its_exclusion_explicitly`, which pins the `allOf` consequence the
next paragraph describes. The reasoning above is kept because it is why the subset was a
subset; the two constants no longer exist.

That test surfaced a consequence worth recording: the band `allOf` requires
`exclusion_reason` whenever `excluded_from_normalization` is true, and JSON Schema's
`properties` constrains only keys that are *present*, so omitting
`excluded_from_normalization` satisfies the `if` and pulls in the `then`. Phase 1
therefore also omits `exclusion_reason` (`PHASE2_CONDITIONAL_BAND_FIELDS`, since removed).
Phase 2 should note that any band object must carry `excluded_from_normalization` explicitly,
even when false, or it inherits a requirement meant for excluded bands — **it does, and a test
pins it.** Two other items belong on the Phase 2 list: `schema_version` should stop declaring a
version the document knowingly fails (**done**), and the one place detection drops a peak
without recording it — a lane
whose columns carry no spread over the peak's rows, documented and tested in
`_detect_bands_in_lane` — should surface a count rather than only a code comment.

The parameter echo follows the same rule in reverse: `configs/*.yaml` carry only the
parameters the pipeline reads, so as Phase 1 shipped there was no `normalization` block to echo
yet, and a config that carries parameters for the background method it did *not* select is
rejected. An echoed parameter that nothing read is a false provenance record. (**Superseded in
Phase 2**, which reads normalization and QC parameters and therefore echoes both: both configs
carry a `normalization` and a `qc` block, and `provenance.parameters` requires them. The rule is
unchanged — it is what put them there.)

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
one band plus a QC flag, and the flag is Phase 2's job.

**Which flag, corrected by Phase 2's measurement.** This entry originally said "the `overlapping`
QC flag", and that turned out to be the wrong flag for this job: `overlapping` is geometric, and
once a doublet is one band with one ROI there is no second ROI to overlap with, so Phase 2
measured it firing on **0** of the 52 truth-`overlapping` matched bands. That measurement is why
the human issued Ruling 1, splitting the question in two: `overlapping` stays geometric, and
`unresolved_shoulder` — a test on the row profile's shape — is the flag that reports an unresolved
doublet. See the Phase 2 section; a reader stopping here would be left with exactly the
misconception that ruling exists to prevent.

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
- **`schema_version` named the contract targeted, not a claim of validity — and Phase 2 ended
  that.** As Phase 1 shipped, the document declared `1.0.0` while omitting five of that
  version's required fields, so a consumer validating on the declared version got `required`
  failures with no in-band explanation; nothing consumed results yet, and inventing an
  out-of-band "partial" marker would have needed a schema change Phase 1 was not authorised to
  make. **Since Phase 2 this is history:** the version is `1.1.0`, the schema pins it as a
  `const`, and the document validates in full. The idea of a `schema_version` that could say
  "subset" was not taken up and should not be revived without a reason this concrete.
- **`result_id` is content-addressed** — as Phase 1 shipped,
  `sha256(source digest | config digest)` — so re-analysing the same image with the same
  parameters reproduces the same id on any machine, and the determinism test can compare whole
  documents with only `created_at` removed. **Phase 2 widened the inputs to three**: the
  caller's `reference_band_ids` are hashed too, in order, because Ruling 2 made them a
  per-image input that changes the document while no config digest can carry them. The property
  is unchanged; the inputs are not, and ids computed before that change do not reproduce. The
  Phase 2 entry gives the reasoning.
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
  The same inconsistency remains in `pipeline/quantify.py`, in `Roi.__post_init__` and — added
  in Phase 2 — in `RowProfile.__post_init__`; all three raise bare `ValueError`/`TypeError`, and
  all three are programming errors that `detect` cannot reach with any legal config, so they are
  left as they are rather than converted into user-facing errors. Recorded here rather than
  silently accepted.
- **Lane width is measured two different ways, and both are stated in `detect_lanes`'
  docstring.** With two or more lanes it is the median spacing of detected centres; with
  exactly one it is that lane's own column-profile extent, under the band extent rule with
  `detection.lane`'s configured pair. Neither is a canvas-derived constant, and the extent
  pair is config rather than a literal.
- **`evals/run.py` has no `--split` flag.** Pointing it at the test split requires a code
  change, which is the point; a test asserts the parser's options are exactly
  `{--config, --data}`.

---

## Phase 2 — QC, normalization and provenance

What this phase adds: `pipeline/qc.py`, `pipeline/normalize.py`, the QC and normalization
parameter blocks in `configs/*.yaml`, and the fields that close the Phase 1 gap in the result
document — which now validates against `schema/result.schema.json` in full rather than
against a documented subset of it.

**Detection, background and quantification are untouched, and that is checked rather than
claimed.** Regenerating `evals/dev_sweeps.json` after this phase changed exactly two values in
it: the two shipped config digests, which moved because the configs gained a `qc` and a
`normalization` block. Every measured figure in the record reproduced bit for bit, including
the headline:

<!-- sweep: shipped_configs -->
| config | lane F1 | band F1 | mean error % | median error % |
|---|---|---|---|---|
| `default.yaml` | 0.9967 | 0.8506 | 17.39 | 6.50 |
<!-- end sweep -->

The test split was not read, scored, or tuned on in this phase either.

### The three human rulings this phase implements

Recorded as ruled, because each one closes a question the phase could otherwise have answered
silently, and Phase 3 inherits them.

**Ruling 1 — the overlap warning is two flags, named and scored separately.** I measured that
Phase 1's "one band per resolved maximum" ruling leaves the pipeline's own ROIs almost never
overlapping, so a purely geometric flag cannot answer the question a reader thinks it answers.
That measurement is the reason this ruling exists, so it is a recorded surface rather than a
number in a sentence — `qc.overlap_iou_threshold`, over the shipped threshold, the generator's
own labelling threshold, the smallest overlap the validator accepts, and one beyond:

<!-- sweep: qc.overlap_iou_threshold -->
| IoU threshold | same-lane pairs | pairs at or above it | detected bands flagged | truth `overlapping` bands | matched bands | tp | fp | fn |
|---|---|---|---|---|---|---|---|---|
| 0.001 | 161 | 3 | 6 | 104 | 279 | 0 | 1 | 52 |
| **0.05** | 161 | 3 | 6 | 104 | 279 | 0 | 1 | 52 |
| 0.15 | 161 | 2 | 4 | 104 | 279 | 0 | 0 | 52 |
| 0.3 | 161 | 1 | 2 | 104 | 279 | 0 | 0 | 52 |
<!-- end sweep -->

Ground truth labels 104 bands `overlapping` across the split; the flag's true positives are
**zero at every threshold**, including the smallest overlap the config validator will accept. The
human's ruling:

> Shoulder detection must be a test on the profile's shape (e.g. asymmetry or residual against
> a single-peak model), with the criterion pinned by a test and recorded in config and
> provenance. It must NOT create a second band, a second centre, or a second ROI — Phase 1's
> ruling stands. Name and score the two flags separately.

So `overlapping` stays strictly geometric — ROI IoU against same-lane neighbours, threshold in
config — and a second flag, `unresolved_shoulder`, fires from a shape test on the lane's row
profile. **Naming note:** the option preview called it `unresolved_structure`; the human's
later note wrote `unresolved_shoulder`, and that is the name shipped.

**Ruling 2 — reference bands are designated by the caller, never inferred.**

> Yes — explicit ids from the caller; the pipeline must never infer which band is the loading
> control, and must raise (not guess, not fall back) if a housekeeping mode is requested
> without `reference_band_ids`.
>
> On evals: designating the reference from ground-truth `role` is an oracle input that does not
> exist on a real blot. It is acceptable for scoring in this phase, but it must be disclosed in
> the eval output, in NOTES.md, and in the PR body — the housekeeping normalization numbers are
> conditional on a correct reference, not a measure of the pipeline finding one. Add it to Open
> items for Phase 3, alongside the clean-band selector, as another criterion that needs a
> real-blot substitute.

**Ruling 3 — a flagged reference normalizes and warns, with the flag named.**

> Normalize and warn — but the warning must name which flag the reference carries (saturated,
> overlapping, unresolved_shoulder, lossy_format), not just that it is flagged. A saturated
> reference biases every ratio in the lane upward in a known direction; an overlapping one
> biases it in an unknown direction. Those are different caveats and the user needs to tell
> them apart.
>
> Also record, per lane, that the ratio is reference-flagged, so a downstream consumer
> (export, UI, Phase 3 scoring) can filter on it without re-deriving the condition.

### What the flags score against ground truth

Scored on the 30 dev images and on the 279 matched band pairs — a flag decision can only be
scored where truth and prediction describe the same band, so unmatched truth bands and
false-positive detections are detection failures, counted by `detection_scores`, not flag
failures. Confusion counts (the record holds counts only; the rates below them are arithmetic
on these six numbers):

<!-- sweep: qc_flag_accuracy -->
| flag | items | tp | fp | fn | tn |
|---|---|---|---|---|---|
| `image.saturated` | 30 | 10 | 0 | 0 | 20 |
| `image.lossy_format` | 30 | 6 | 0 | 0 | 24 |
| `image.low_dynamic_range` | 30 | 7 | 0 | 3 | 20 |
| `band.saturated` | 279 | 23 | 2 | 0 | 254 |
| `band.overlapping` | 279 | 0 | 1 | 52 | 226 |
<!-- end sweep -->

So: `saturated` and `lossy_format` are exact at image level (P = R = F1 = 1.000);
`low_dynamic_range` is P 1.000, R 0.700, F1 0.824; band `saturated` is P 0.920, R 1.000,
F1 0.958; and band `overlapping` is **0.000 on all three** — 0 true positives, 52 misses. Each
flag has both a firing and a non-firing case in the split (the `tn` column), so none of these
is the degenerate "flags everything" or "flags nothing" pass.

**Band `overlapping` scoring 0.000 is the measured consequence of Phase 1's ruling, not a bug,
and it is the reason Ruling 1 exists.** A doublet is reported as one band with one ROI, so
there is no second ROI to overlap with; the flag can only fire where two *separately resolved*
bands in one lane have ROIs that grow into each other. The recorded surface above says how often
that is, and every count in this sentence is a column of it: at the shipped threshold **3 of the
161 same-lane pairs overlap enough to flag, which puts the flag on 6 detected bands** — and not
one of those is a band whose truth carries `overlapping`, which is why the confusion counts read
0 true positives and 1 false positive. The 1 is the count among *matched* bands, a narrower
denominator than the 6, and an earlier draft of this paragraph quoted it as the frequency of the
event and so understated that frequency six-fold. Which three pairs they are is a one-off lookup,
listed with the other one-off lookups at the end of this section.

The flag is kept because it answers a question that matters for the integral — are these two
numbers double-counting shared pixels — and because on a gel where a doublet *is* resolved it is
the only flag that would fire. Its recall against this gold set is a statement about the gold
set's doublets and about the detector, not about the flag's criterion.

`unresolved_shoulder` is the flag that answers the question a reader of "overlapping" actually
has, and it **cannot be scored as an accuracy at all**, because ground truth has no such label.
Renaming truth's `overlapping` into it would be scoring a shape test against a geometry label
and calling the result precision. So it is reported as a coincidence instead — the firing rate
on bands whose truth carries `overlapping` and on bands whose truth does not — which
`evals/metrics.flag_coincidence` computes and whose docstring says plainly that it is not an
accuracy:

<!-- sweep: qc_flag_accuracy -->
| | matched bands | truth `overlapping` | truth clean | fires with | fires without |
|---|---|---|---|---|---|
| `band.unresolved_shoulder_coincidence` | 279 | 52 | 227 | 31 | 5 |
<!-- end sweep -->

31 of 52 is a rate of 0.596 on the bands truth calls overlapping, against 5 of 227 = 0.022 on
the rest: a factor of 27 between the two populations, where the geometric flag separates them
by nothing at all. `tests/test_sweep_check.py` asserts that separation from the record as a
property, so it cannot quietly disappear.

### Normalization, measured — and the oracle disclosure

<!-- sweep: normalization_modes -->
| mode | ratios | included | excluded | ref-flagged | lanes used | lanes skipped | included mean \|e\|% | included median \|e\|% | all mean \|e\|% | all median \|e\|% |
|---|---|---|---|---|---|---|---|---|---|---|
| `housekeeping_single` | 129 | 77 | 52 | 2 | 129 | 21 | 12.22 | 3.96 | 22.95 | 6.13 |
| `total_protein` | 172 | 150 | 22 | 40 | 86 | 64 | 31.65 | 13.94 | 34.10 | 11.31 |
<!-- end sweep -->

`included` is the default policy — QC-flagged bands excluded from the ratios — and `all` adds
them back, because a flagged ratio is *reported* with its flags and its exclusion rather than
dropped. Both columns are given for the same reason the saturated bands are shown twice in the
Phase 1 recovery table: a number that quietly excluded its hard cases has to say so on the
same screen.

**The two modes' error columns are not directly comparable, and nothing here should be read as
if they were.** 12.22% is over 77 included ratios drawn from 129 lanes; 31.65% is over 150
included ratios drawn from 86 lanes. Different lane subsets, different ratio counts, and the
housekeeping figure additionally uses an oracle reference the other does not. No common-subset
figure is recorded, so the defensible statement is that each mode's error is known *on its own
subset*; the size of the gap between them is not established. The direction is consistent and
`dev_03`'s negative integral is independent evidence, which is why the open item below is worded
as a candidate diagnosis rather than a measurement.

**Why `total_protein` is scored on 86 of 150 lanes — an evaluation-comparability limit, not a
product gap.** This is the phase's most easily misread figure, so the breakdown is stated rather
than left to inference. Measured over the dev split with the shipped config: 59 lanes are
unscored because not every truth band in them was matched, 4 because no truth band matched
inside the matched lane, and 1 because detection never found the lane — 64 in total, against 86
used. The eval requires *every* truth band of a lane to be matched, because the truth-side
denominator is the sum of the true intensities of all that lane's bands; if one is missing the
two sides describe different sets of bands and the ratio is not comparable. Of the 59, the
shortfall is one band in 55 lanes (48 missing 1 of 3, 7 missing 1 of 2) and two bands in 4.
**The missing band is overwhelmingly the doublet partner**: across all detected lanes the
unmatched truth bands are 46 `target_secondary`, 13 `target` and 12 `housekeeping`, and 52 of
the 150 truth lanes are 3-band doublet lanes. So the skip rate is largely Phase 1's
one-band-per-maximum ruling propagating into Phase 2's scoring.

What the *product* does on the same split is different and much less alarming: the pipeline
emits 302 ratios across 148 of 149 detected lanes, and exactly one lane yields no ratio —
`dev_03`'s, via `lane_denominator_not_positive`. `total_protein` is not silent on 60% of lanes;
it is *scored* on 57% of them. Recording this breakdown as sweep fields needs an
`evals/sweep.py` change and a tolerance class, which the pass that added this paragraph was
instructed not to make; it is on Open items, and until then this is the one aggregate figure in
the section the record does not hold (kind 3 below says so).

**ORACLE DISCLOSURE, per Ruling 2.** The `housekeeping_single` row designates its reference
band by reading ground truth's `role`. That input does not exist on a real blot: the pipeline
refuses to infer a reference and requires the caller to name one, which on synthetic data
means the eval is the caller and the eval cheats. **So 12.22% is the error of dividing by the
right band, not evidence that the right band can be found.** `evals/run.py` prints that
disclosure directly under the table, `evals/sweep.py` carries it in the record's own note, and
it is an Open item for Phase 3 below.

Two more things the table does not say on its own:

- **What `skipped lanes` means, per mode.** For housekeeping, a truth lane is skipped when
  either band of its truth ratio went undetected, or when the two were detected into different
  lanes — then the predicted quotient is not the one the truth ratio describes. For
  total_protein, a lane is skipped unless *every* truth band of the lane was detected into the
  matched detected lane, because otherwise the two denominators cover different sets of bands.
  Both counts are printed rather than absorbed.
- **What the total_protein truth is.** Ground truth records no total-protein reference, so the
  truth analogue is the sum of the true integrated intensities of the bands in that lane, and
  the predicted denominator is the lane's whole background-corrected integral. That comparison
  is fair only under the skip rule above, and it is why total_protein's error is several times
  housekeeping's: the numerator's error is the same, and the denominator adds the background's
  residual over ~9000 lane pixels to it. Which brings up the finding below.

**The lane integral can come out negative, and one dev image does.** `local_median` estimates
the background slightly high (band pixels inside its own window; the Phase 1 entry measures
it), and a lane ROI is the full image height, so on a faint image the residual over ~9000
pixels can exceed the band signal in the lane. On `dev_03` — `exposure: low` — one of its
lanes does: the integral is about -5400 DN. Three options were available: raise (kills the image over
one lane), emit a negative ratio (a published number with no meaning), or record it. The
pipeline records it: that lane produces no ratios, every band in it carries
`excluded_from_normalization` with a reason naming the denominator and its value, and the result
carries the `lane_denominator_not_positive` warning. Not a silent fallback — the outcome is
stated in three places and printed by the CLI — but it *is* a limitation of total-protein
normalization on faint images, and it is an Open item rather than something this phase fixes.

### Each threshold, its independent justification, and its coincidence audit

`synth/MODELS.md`'s closing rule: a parameter equal to a number in that document is
tuning-to-the-generator unless it has an independent justification recorded in the config and
in NOTES.md. That rule bites hard here, because the generator's QC parameters
(`saturated_min_clipped_pixels = 3`, `overlap_iou_threshold = 0.15`,
`low_dynamic_range_peak_fraction = 0.2`) produced the very labels these flags are scored
against: copying them would maximise the score circularly. So each threshold below was chosen
from a criterion stated without reference to the generator, and *then* measured.

**`qc.saturated_min_clipped_pixels = 1`.** Criterion: a single pixel at full scale means the
ROI sum is a lower bound and not a measurement — the pixel's own value is unknown and
everything above full scale was discarded — and QC annotates rather than drops, so a flag costs
a caveat while a missed clipped band costs a published number. The whole surface is recorded,
so the cost of choosing independently is a re-measured figure and not a claim:

<!-- sweep: qc.saturated_min_clipped_pixels -->
| clipped pixels | matched bands | tp | fp | fn | tn |
|---|---|---|---|---|---|
| **1** | 279 | 23 | 2 | 0 | 254 |
| 2 | 279 | 23 | 1 | 0 | 255 |
| 3 | 279 | 23 | 0 | 0 | 256 |
| 5 | 279 | 22 | 0 | 1 | 256 |
| 10 | 279 | 21 | 0 | 2 | 256 |
<!-- end sweep -->

So the shipped 1 is P 0.920, R 1.000, F1 0.958, and **the generator's 3 scores perfectly
(1.000 / 1.000 / 1.000) and is not shipped.** Those two figures are the evidence that the trade
was deliberate, and the human has ruled on it:

> `saturated_min_clipped_pixels` stays at 1. Do not move it to the generator's 3. Any clipped
> pixel means the detector saturated and the integrated intensity is truncated — western blot QC
> treats any saturation as disqualifying, and 3 is the generator's arbitrary labelling threshold,
> not a biological one. Moving the parameter to match it would be a third circularity of the same
> family as the two you just removed from the test suite. Record this in NOTES.md explicitly: the
> two "false positives" hold 1 and 2 clipped pixels and are correct detections against a coarser
> label; the QC flag and the ground-truth label disagree here by design, and the score is the one
> that is wrong, not the flag.

Spelling that out, because it inverts how the table above should be read. The two "false
positives" are `dev_10_L4_target`, which holds one pixel at full scale, and
`dev_19_L4_housekeeping`, which holds two — ground truth records the same counts, so there is no
disagreement about the pixels. **They are correct detections against a coarser label.** The
generator labels a band `saturated` only at three clipped pixels, so that a lone noise excursion
does not become a label; the pipeline flags at one because its criterion is whether the
*measurement* is truncated, and one clipped pixel truncates it. Where the two disagree the flag is
right and the score is wrong: F1 0.958 is the cost of being scored against a coarser rule, not
evidence of a worse flag. The two arguments that were open when this entry was first drafted — that
a lone full-scale pixel might be noise, and that 3 scores better — are both answered by the
ruling: the first is a concern about labelling rather than about measurement, and the second is
exactly the circularity this project forbids. It would have been the third of that family, after
the two the test suite had to have removed from it.

Coincides with `roi_width_fraction_of_pitch = 1.0` and `target_relative_levels[0] = 1.0` in
`MODELS.md`, neither of which is a pixel count.

**`qc.overlap_iou_threshold = 0.05`.** Criterion: two overlapping ROIs count their shared
pixels into both integrals, so the question is how much double-counted signal changes a
reported ratio. A tenth of a band's aperture does — it is larger than the aperture error the
pipeline already carries — and for two ROIs of equal size, sharing a tenth of each box is
IoU = 0.1/(2 - 0.1) = 0.0526, hence 0.05. This is *stricter* than the generator's
`overlap_iou_threshold = 0.15`, which for equal boxes is a 26% shared area, and the strictness
buys nothing measurable on this split — the recorded surface under Ruling 1 above shows one
false positive at 0.05 and none at 0.15, with zero true positives either way. Coincides with
`bbox_relative_threshold = 0.05`, the generator's
band-ROI aperture rule — an unrelated quantity, and note that Phase 1 deliberately refused
0.05 for `band.extent_relative_height` precisely because there it *would* have matched the
generator's aperture. Here it is an overlap fraction, not an aperture.

**`qc.shoulder_half_maximum_fraction = 0.5`.** The level at which the two half-widths are
measured, as a fraction of the peak's height above its baseline. Criterion: it is the
conventional half-maximum level — "half-width at half maximum" is what the statistic is called,
and FWHM is how every instrument specification states a peak's width — so 0.5 is a convention
rather than a fitted value, and it is the one point on the flank where the width has a name a
reader already knows.

**It is nonetheless a declared parameter, in `qc` and in provenance, on the human's ruling**
(Ruling 1: "with the criterion pinned by a test and recorded in config and provenance"). Two
reasons that is right rather than bureaucratic. First, the level sets the test's sensitivity as
much as the threshold does: measured on a hand-computed profile in
`tests/test_pipeline_qc.py::test_the_level_is_a_parameter_that_moves_the_measured_asymmetry`, the
same peak measures a ratio of 1.0 at a level of 0.6 and 1.3788 at 0.2, because a shoulder low on
one flank is invisible high up. Second, a level in a function body is exactly the magic number
CLAUDE.md forbids, and the audit below only sees parameters that are config keys — so leaving it
as a module constant would have kept it out of the coincidence audit as well. Coincides with
`roi_tilt_excursion_fraction_of_height = 0.5` in `MODELS.md` §4a, a lane-geometry fraction with
nothing to do with a profile's flank; it is also the only one of the five whose value would have
been the same under any generator, being a naming convention.

**`qc.shoulder_half_width_ratio = 1.5`.** Criterion: a single band's profile is symmetric about
its own centre, because diffusion about a migration position has no preferred direction. The
statistic is therefore the ratio of the wider half-width to the narrower one at the level above,
which is 1.0 for one band and grows as a shoulder appears; it is scale-free, so it does not
depend on the band's size, height or units. 1.5 says "one side is at least half again as wide
as the other", which no single diffusive band produces. **This is the weakest of the five
justifications and is disclosed as such**: "half again as wide" restates 1.5 rather than
deriving it the way the dynamic-range fraction is derived, and the recorded surface below shows
1.5 is also where the separation between the two populations is widest — so the choice is
consistent with the measurement but not independent of it in the way the others are.

<!-- sweep: qc.shoulder_half_width_ratio -->
| ratio | matched bands | truth `overlapping` | truth clean | fires with | fires without |
|---|---|---|---|---|---|
| 1.2 | 279 | 52 | 227 | 46 | 100 |
| 1.3 | 279 | 52 | 227 | 39 | 39 |
| **1.5** | 279 | 52 | 227 | 31 | 5 |
| 1.75 | 279 | 52 | 227 | 8 | 2 |
| 2.0 | 279 | 52 | 227 | 3 | 1 |
| 2.5 | 279 | 52 | 227 | 0 | 0 |
<!-- end sweep -->

The rates those counts give are 1.2: 0.885 / 0.441, 1.3: 0.750 / 0.172, 1.5: 0.596 / 0.022,
1.75: 0.154 / 0.009, 2.0: 0.058 / 0.004 — separation ratios of 2.0, 4.4, 27.1, 17.5 and 13.1,
computed from the counts rather than from those rounded rates. The shipped value is where that
separation happens to be widest.

**No test asserts that it is the widest, and that is deliberate.** An earlier draft of this
section did assert the argmax, and it was wrong to: `evals/metrics.FlagCoincidence` and this
section both say the coincidence with truth's `overlapping` label *cannot* be an accuracy, so
making its maximum a property CI enforces would turn the diagnostic into the selection criterion
in everything but name — and would freeze 1.5 at the dev-split optimum, since any later
detection change that moved the surface could be made to pass again only by re-tuning the
threshold. What the tests assert is what this section claims: that the shipped value is on the
recorded surface, and that its separation is large (`with_reference > 10 * without_reference`).
The ordering is recorded for a reader to check, not for CI to enforce. Coincides with the dust speck radius `U(1.5, 3.5)` in
`MODELS.md`, which is a length in pixels.

**`qc.dynamic_range_min_peak_fraction = 0.25`.** Criterion: derived from what the *weakest*
band in the same image then measures. A blot's weakest band of interest is commonly around a
seventh of its strongest, so at a quarter of full scale the weakest peaks near 0.25/7 = 3.6% of
full scale, which at 8-bit is 9 DN — only a few times a typical read noise. Below a quarter,
the dim end of the same image is not quantifiable at all.

**A better score was available on this gold set and was declined.** That is the load-bearing
claim of this whole entry, so it is a recorded surface, not a sentence:

<!-- sweep: qc.dynamic_range_min_peak_fraction -->
| peak fraction | images | tp | fp | fn | truth `low_dynamic_range` images | brightest peak among them |
|---|---|---|---|---|---|---|
| 0.15 | 30 | 5 | 0 | 5 | 10 | 0.3226 |
| 0.2 | 30 | 7 | 0 | 3 | 10 | 0.3226 |
| **0.25** | 30 | 7 | 0 | 3 | 10 | 0.3226 |
| 0.3 | 30 | 8 | 0 | 2 | 10 | 0.3226 |
| 0.4 | 30 | 10 | 0 | 0 | 10 | 0.3226 |
<!-- end sweep -->

So the shipped fraction is P 1.000, R 0.700, and **0.40 reaches R 1.000 with no false positive
and is refused.** Refused because a scratch adds `0.25 * M` (`MODELS.md` §5), so reaching that
row means matching the generator's scratch amplitude — the circularity this entire entry is
about. The last column says why the shipped value misses what it misses: the brightest peak
measured on any image ground truth calls `low_dynamic_range` is 0.3226 of full scale, well above
0.25, because a scratch crossing a lane is additive contamination that a peak measurement cannot
distinguish from signal. **The three misses are `dev_01`, `dev_03` and `dev_07`** — exactly the
three `exposure: low` images that also carry `defect: scratch` (an identity, so a one-off
lookup). The honest fix is to measure dynamic range on something a scratch cannot inflate, which
is a Phase 3 question about the measure, not a threshold to move now. Coincides with the scratch
amplitude `0.25 * M` itself, which is the coincidence that causes the misses.

**Re-audited coincidence tally.** Phase 1's entry found **7 of 15** numeric processing
parameters equal to a number in `MODELS.md`. Phase 2 adds **five** — four thresholds plus
`shoulder_half_maximum_fraction`, which entered the config on the human's ruling and is inside
the audit precisely because it did — and, as the entries above show, **all five coincide**. The
count is now **12 of 20**; the eight that coincide with nothing are unchanged from Phase 1 and
are listed in "`min_prominence_sigma` and where the shipped values coincide with generator
constants", along with the seven Phase 1 coincidences.

This is not a sign that five thresholds were copied: it is the second observation of that entry,
arriving harder. `MODELS.md` declares roughly forty parameter values spread over the same numeric
ranges QC thresholds live in — small counts, fractions of full scale, ratios near one — so
coincidence is close to unavoidable and avoiding it is not the goal. Two of the five are
*deliberately different* from the generator parameter that governs the same question (1 against
3 clipped pixels, 0.05 against 0.15 IoU), one differs from it (0.25 against 0.20 of full scale),
one answers a question the generator has no parameter for, and the fifth is a naming convention
that would read 0.5 whatever generator it met.

### `unresolved_shoulder` is a shape test that changes nothing about detection

`pipeline/detect.py` owns the profiles, so it is where the profile *arrives* from: each band
carries the `RowProfile` its ROI was walked from — the samples, the peak index, the baseline and
the two bounds. `pipeline/qc.py` owns the whole criterion, and applies both of its parameters
(the level and the ratio threshold) to that profile. Detection therefore holds no threshold, no
level and no decision about shape, which is what makes the parameters' home unambiguous:
`half_width_ratio` takes the level as a required argument and would raise rather than default it.

Detection's behaviour is unchanged: the band count, the centre and the ROI are what Phase 1
produced — the sweep record reproducing bit for bit is the evidence — and `DetectedBand` merely
carries the profile it already had in hand.

Three properties of the statistic that make it a QC flag rather than a deconvolution:

- It is measured on the same profile, peak and bounds the ROI was walked from, so it describes
  that band's own peak and cannot stray into a neighbour's.
- Its level is a declared parameter and not a constant, so the sensitivity of the test is in the
  provenance record of every result rather than in a source file (see the threshold entry above
  and the human's Ruling 1).
- It is `None`, not a number, when the profile does not fall to half maximum on both sides
  within those bounds. A censored width is not a symmetric one, and reading `None` as either
  would be the silent fallback this project forbids; `assess` does not flag on `None`.
- `tests/test_pipeline_qc.py` pins it on the same doublet fixture
  `tests/test_pipeline_detect.py` uses for the Phase 1 ruling: the row profile really has a
  single maximum, the detector really reports one band, and the flag fires on that one band.
  The symmetric single-band case is pinned beside it and measures 1.0 within 0.2.

### The image `saturated` flag is a statement about bands

An image is flagged `saturated` when at least one *band* is, not when any pixel in it is
clipped. Ground truth uses the same definition (`MODELS.md` §7), so adopting it keeps the score
like for like — but it also has an independent reason, which is why it was adopted rather than
inherited: clipping outside every ROI is a bright artifact or a hot background, and this
project's flags exist to qualify *measurements*. A saturated dust speck between two lanes
invalidates nothing. `tests/test_pipeline_qc.py` pins both halves.

Clipping itself is defined as a pixel at full scale, derived from the bit depth the loader
read, and it is **not** a configured level. That is a definition, not a magic number: a pixel
reading `max_value` is indistinguishable from one truncated there, so it is the only definition
a pipeline can observe, and a "clipping level" parameter would invite a fraction of full scale
that no detector actually reports.

### Schema edits, and the honest `schema_version`

`RESULT_SCHEMA_VERSION` goes **1.0.0 → 1.1.0**, and the schema now pins that value as a
`const`, mirroring `ground_truth.schema.json`, with `tests/test_schema.py` asserting the two
agree. Phase 1 left the field dishonest — it declared 1.0.0 on a document that failed five of
1.0.0's required fields — and the fix has two halves: the document now validates
(`tests/test_pipeline_result.py` checks the produced document against the schema as written,
with no requirements relaxed), and the version is bumped because the contract itself changed.
Minor rather than major: every edit is additive, and a 1.0.0 consumer keeps working.

Every edit, with its reason:

1. **`band_qc_flags` gains `unresolved_shoulder`.** Ruling 1 needs a second flag name; the
   enum was closed. The `$def`'s description now states that the two flags ask different
   questions and are scored separately, because that vocabulary is what `qc_flag_accuracy`
   reads and the next reader has to know that one is not a proxy for the other.
2. **`normalization_warning` gains four named-flag codes** —
   `reference_band_saturated`, `reference_band_overlapping`,
   `reference_band_unresolved_shoulder`, `reference_band_lossy_format` — because Ruling 3
   requires the warning to name which flag the reference carries, and the enum was closed. The
   general `reference_band_qc_flagged` is kept and is emitted *alongside* the specific code, so
   a consumer written against 1.0.0 still sees the condition.
3. **`normalization_warning` gains `lane_without_reference_band` and
   `lane_denominator_not_positive`**, the two ways a lane can have no usable denominator. Both
   are recorded rather than raised, per the entries above, and a warning is how a consumer
   learns that some lane produced no ratio without diffing the lane and ratio lists.
4. **The ratio object gains `reference_qc_flagged` and `reference_qc_flags`.** The per-lane
   half of Ruling 3: a consumer filters on the condition instead of re-deriving it. Both are
   emitted on every ratio, so absence is never ambiguous.
5. **The ratio object gains `denominator_band_ids`.** `housekeeping_multi` divides by several
   bands, and the singular `denominator_band_id` cannot say which. It is still emitted when
   there is exactly one, so nothing that reads it breaks.
6. **`provenance.parameters` gains a required `qc` block.** Every parameter the pipeline reads
   is echoed; five new QC parameters are read. Left open like `detection`, because the pipeline
   owns the key names.
7. **The band object gains `row_half_width_ratio`** (`number` or `null`) — the observation the
   `unresolved_shoulder` flag is decided on, recorded beside the flag for exactly the reason
   `clipped_pixel_count` sits beside `saturated`: a reader can re-apply any threshold to the
   number the decision was made on, and the flag becomes auditable from the document alone.
   Explicitly `null` when the statistic was censored, because absence would be
   indistinguishable from a writer that does not measure it. It is also what lets the eval's
   threshold surfaces re-apply `pipeline.qc`'s own predicates instead of restating them.
8. **Descriptions added** to `peak_value`, `clipped_pixel_count`, `schema_version`, the
   `warnings` array and the ratio's `qc_flags` and `reference_qc_flags` — the fields existed but
   did not say what they mean, and `peak_value` in particular is ambiguous without saying it is
   background-corrected.

`schema/ground_truth.schema.json` was **not touched**. It is the frozen gold-set contract, its
`schema_version` is a `const` with a test pinning it, and the gold set cannot be regenerated
while `synth/` is frozen.

### Small decisions that would otherwise have to be reverse-engineered

- **A flagged reference is not "excluded".** `excluded_from_normalization` on a reference band
  is always false, even when the band carries flags, because its value *was* used — as the
  denominator of its lane. The caveat travels as the named warning and as the ratio's
  `reference_qc_flags`. Excluding it instead would delete every ratio in the lane, which is the
  outcome Ruling 3 rejects.
- **The override warning is only emitted when it is true.** `exclude_qc_flagged: false` with no
  flagged band in the image emits no `qc_flagged_bands_included_by_override`, because the
  warning asserts that flagged bands were included and there were none.
- **The total-protein denominator is an ROI pixel sum, not a mean-column-profile integral.**
  PLAN.md calls the mode's denominator a "lane-profile integral", and the two differ by the lane's
  width — so on lanes of unequal width they are different quantities. The pixel sum is what ships,
  because a band's `integrated_intensity` is a pixel sum over the same corrected image and the
  ratio of the two is only meaningful if they are the same kind of quantity. The schema's
  `total_protein_signal` description says which one it is.
- **`total_protein` treats the lane's own flags as the denominator's caveat.** The lane integral
  contains every band in the lane, so a saturated band in it under-reads the denominator and
  biases every ratio in that lane upward — the same statement Ruling 3 makes about a flagged
  housekeeping band, applied to the quantity that plays its part. That is why the recorded table
  above shows a far larger share of `total_protein`'s ratios reference-flagged than of
  `housekeeping_single`'s.
- **`lossy_format` warns but never becomes a band flag.** It is a property of the image, and
  the band and ratio flag lists carry the vocabulary they share with ground truth — which is
  what makes them scorable. So `reference_band_lossy_format` is emitted on a JPEG while
  `reference_qc_flags` stays empty, and `normalize` raises if a flag outside the band
  vocabulary ever reaches it.
- **Each mode takes exactly the input its denominator needs and refuses the other.**
  `total_protein` requires the lane integrals and refuses `reference_band_ids`; the
  housekeeping modes require the ids and refuse the integrals. Same rule as Phase 1's config
  loader refusing parameters for the background method it did not select: an input nothing read
  is a false provenance record. `analyze_image` therefore only *measures* the lane integrals in
  `total_protein` mode, and `lanes[].total_protein_signal` is emitted only there.
- **`housekeeping_multi` uses the geometric mean because the quantity is a ratio scale.** The
  mean of several references has to be the one whose reciprocal is the mean of the reciprocals;
  an arithmetic mean would let one bright reference dominate. `math.fsum` over the logarithms,
  so the answer does not depend on the order the ids were given in — pinned by a test.
- **A wrong *number* of references in a lane raises; a non-positive denominator does not.** The
  first is a caller mistake (the mode divides by one band, or by at least two), the second is
  an outcome of the data. The split is deliberate: raising on data would throw away the lanes
  that measured fine. `NormalizationError`'s own docstring enumerates both sides, because it is
  the class a consumer catches: a docstring promising an exception for a condition that is only
  ever *recorded* would invite exactly the silent fallback this project forbids.
- **Warning uniqueness is a property of the code, not a schema constraint.** `normalize` filters
  a set through `WARNING_ORDER`, so a warning cannot repeat, and a test asserts it. A
  `uniqueItems` on the schema's `warnings` array was written and then removed: it constrains
  documents that were valid at 1.0.0, so it is a narrowing rather than an additive edit, and the
  authorisation for this phase's schema edits was drawn narrowly on purpose.
- **The CLI gained `--reference-band`, repeatable.** Band ids are only known after a run, so
  the housekeeping workflow is two runs: one to see the ids, one to name the references. That
  is the honest shape of "the caller designates the reference" for a CLI.
- **`evals/run.py` runs the pipeline once per image, and so does the whole sweep record.** The
  housekeeping ratios come from re-running `normalize` over the same measured intensities and QC
  flags with the mode replaced, not from analysing the image twice. The two QC threshold surfaces
  work the same way: they re-apply `pipeline.qc.is_saturated` and
  `pipeline.qc.is_unresolved_shoulder` — the shipped criteria, exported for this — to the
  observations the result documents already carry, with one config field replaced per row. A
  first draft recomputed the clipping count and both comparisons inside `evals/sweep.py`, which
  was a second copy of the criteria *presented as the shipped flag's behaviour*, and cost a
  second full pipeline pass; `tests/test_sweep_check.py` now also pins each surface's shipped row
  against the corresponding `qc_flag_accuracy` row, exactly, so the two records cannot describe
  different rules. Switching from the copy to the shipped predicates left every figure in both
  surfaces identical, which is the evidence that the copy had been faithful *so far* — and not a
  reason to have kept it.
- **One pipeline pass in the sweep is still not shared, and is paid deliberately.** The QC record
  runs `evals.run.evaluate_image`, which re-estimates the background that `evals/sweep.py`'s cache
  already holds — 1.4 s per image, 43 s of a `--check` that costs about 9m15s of CPU. The alternative is to
  assemble a result document inside `evals/sweep.py` from the cached surfaces, which duplicates
  `pipeline.analyze.analyze_image`; the QC row is worth more measured through the same path the
  runner and the CLI use than 8% of a CI step is worth saving. `.github/workflows/ci.yml` states
  the measured runtime rather than an estimate.
- **`result_id` hashes three inputs, not two.** Phase 1 content-addressed a result by
  `sha256(source digest | config digest)`. Ruling 2 introduced a third input that changes the
  document and that no parameter set can carry: the caller's reference band ids. Two runs of the
  same image and config with different references produce different denominators, different
  ratios and different exclusions, so on the Phase 1 id they would have collided — and PLAN.md's
  Phase 4 serves results by id. The ids are hashed **in order**, because the order is recorded
  on the result and reappears in each ratio's `denominator_band_ids`, and JSON-encoded rather
  than joined so no id can be forged through a separator inside a band id. Consequence worth
  stating: an id computed before this change does not reproduce after it. Nothing had been
  persisted, so nothing broke.
- **`normalize` requires `lossy_format`; it has no default.** It is the one input that is not
  refused by some mode, so a default would be the one way a caller could silently lose a caveat
  — every JPEG's `reference_band_lossy_format` warning — rather than being told. The tests route
  through a wrapper that states `False` once, so the requirement is not diluted per call.
- **`evals.metrics.flag_coincidence` takes truth first, like `qc_flag_accuracy`, and declares
  both vocabularies.** The two mappings have the same type, so a swapped pair would type-check
  and run; and the two sides genuinely have different vocabularies — `unresolved_shoulder` is
  legal as a prediction and illegal as a truth label — so each is checked against its own,
  including the two flag names. A mistyped flag name would otherwise report an honest-looking
  0.0 rate. `qc_flag_accuracy`'s own signature is untouched: other phases depend on it.
- **Under `total_protein`, a ratio's `reference_qc_flags` includes the numerator's own flags**,
  because the numerator band is part of the lane integral it is divided by. That is the honest
  reading of "what qualifies this denominator", but it means the field alone cannot distinguish
  "the denominator is independently compromised" from "this band is flagged" — a consumer
  compares it with the band's own `qc_flags`. The schema's description says so.

### Deviations from PLAN.md in this phase, and the disclosure the PR body must carry

Three, all recorded above and gathered here so a PR body can cite them:

1. **`total_protein`'s denominator is an ROI pixel sum, not literally the "lane-profile integral"
   PLAN.md names.** The two differ by the lane's width, so on lanes of unequal width they are
   different quantities. The pixel sum ships because a band's `integrated_intensity` is a pixel sum
   over the same corrected image, and their ratio only means something if both are the same kind of
   quantity. The schema's `total_protein_signal` description says which one it is.
2. **`evals/run.py` and `evals/sweep.py` grew QC and normalization reporting** — four threshold
   surfaces, a flag-accuracy record and a normalization record — beyond the file list PLAN.md gives
   Phase 1. This continues the deviation the human ratified in Phase 1 for the same reason: the
   phase makes measured claims, and a claim that is not mechanically re-measured goes stale.
3. **The CLI gained `--reference-band`** (repeatable), because Ruling 2 requires the caller to name
   the reference bands and PLAN.md's Phase 1 CLI has no way to.

**The disclosure that must appear in the PR body, per Ruling 2:** the `housekeeping_single`
normalization figures are conditional on an **oracle** reference. The eval designates the reference
band by reading ground truth's `role`, an input a real blot does not supply, and the pipeline
itself refuses to infer one. 12.22% is the error of dividing by the right band, not evidence that
the right band can be found. It is printed with the eval table, carried in the record's own note,
and carried as an Open item for Phase 3 beside the clean-band selector.

### One item Phase 1 put on this phase's list and this phase did not do

The Phase 1 entry "The Phase 1 result is a strict subset of the result schema" left three items
for Phase 2. Two are done: `schema_version` no longer declares a version the document fails,
and every band states `excluded_from_normalization` explicitly. The third is **not** done —
"the one place detection drops a peak without recording it … should surface a count rather than
only a code comment", the guard in `_detect_bands_in_lane` for a lane whose columns carry no
spread over a peak's rows.

Why not: **it is unreachable on any committed image** — it needs a lane exactly constant across
its whole width at a peak's rows — so no figure this phase reports depends on it, and a counter
that is always zero is a field a reader learns to ignore. Reporting it also means another
`schema/result.schema.json` field, and while this phase did add fields beyond the four the
rulings required (`denominator_band_ids`, the `qc` block, `row_half_width_ratio` — each argued
where it is listed), every one of them carries a number some flag or ratio *rests* on. A
detection diagnostic that never fires is a different kind of addition. It stays a documented,
tested guard and moves to whichever phase next touches the detection contract.

### What is checked mechanically in this section, and what is not

The Phase 1 entry "How the figures in this section are kept true" applies here unchanged: every
figure inside a `<!-- sweep: ... -->` block in this section, and in the Open items below it, is
tied to `evals/dev_sweeps.json` by `tests/test_recorded_figures.py`, and that record is
re-measured from the committed gold set by `python -m evals.sweep --check` in CI. (The checker's
span used to stop at `## Open items`, which left a marked block quoting a recorded trade-off curve
*in* that section unchecked. The span was widened rather than the block moved.)

**This is the fourth attempt at the paragraph below, and the three failures are the reason it is
written as a closed list.** Cycle 4 of Phase 1 rejected a "three-way exhaustive taxonomy that
several figures fell outside of". Phase 2's first draft reintroduced the same defect, claiming
"two one-off diagnostics" while four unrecorded dev-split measurements sat outside a block —
including the evidence for Ruling 1 itself. The correction then missed three more. Each round the
fix was to *record the measurement*, not to widen the words: the saturation surface, the overlap
surface (which carries Ruling 1's evidence, the IoU 0.15 claim and the flagged-band count as a
column) and the dynamic-range surface are all in the record because of this paragraph. What
follows was written by extracting every standalone number in this section and in the Open items
that is not inside a block — 150 lines carry one — and classifying each. Six kinds account for all
of them, and the extraction is a three-line script over this file, so the next reader can repeat it
rather than trust it: strip phase, ruling, cycle and section references and list numbering, strip
version numbers, and every number that remains is one of the six.

1. **Figures a block on this page holds, restated in prose or turned into a rate.** Every
   precision, recall, F1, firing rate and separation ratio quoted above, and the counts they are
   computed from where a sentence names them. The QC records hold counts only, deliberately — a
   rate is `None` rather than zero in cases the record cannot express, and recording one would
   turn an honestly undefined score into a comparison failure — so
   `tests/test_sweep_check.py` asserts the *properties* the rates are quoted for (no missed
   clipped band, no false image flag, the shoulder separation, the declined perfect
   dynamic-range row) from the record instead, which is stronger than pinning a transcription.
   **The hazard in this kind is worth naming rather than hiding: a restated figure can go stale
   while its block updates**, which is exactly what happened once to a lane count in the
   normalization table. Restatements are therefore kept adjacent to their block — usually the
   next sentence — so a reader sees both at once, and none is more than a page away.
2. **Figures a recorded surface forces, with the derivation stated.** Two: that
   `dev_10_L4_target` and `dev_19_L4_housekeeping` hold 1 and 2 clipped pixels (the *counts* are
   forced by the saturation surface — false positives run 2, 1, 0 at thresholds 1, 2, 3 — and only
   the two band ids are a lookup), and that the shoulder bounds admit 1.5, 1.75 and 2.0, which the
   meta-test recomputes from the surface.
3. **Named one-off dev-split measurements the record does not hold.** Five, and this is the whole
   list. Four are identities or single-image readings, not aggregate scores: the identity of the
   three `low_dynamic_range` misses (`dev_01`, `dev_03`, `dev_07`, all `exposure: low` with
   `defect: scratch`); the two band ids in kind 2; `dev_03`'s total-protein integral of about
   -5400 DN; and the three same-lane ROI overlaps the geometric flag fires on — `dev_03`
   L0_B1/L0_B2 at IoU 0.1705, `dev_03` L3_B0/L3_B1 at 0.3736 and `dev_12` L4_B0/L4_B1 at 0.0918,
   whose *count* is recorded but whose identities and exact overlaps are not.

   **The fifth is an aggregate, and it is the only one, so it is named as the exception rather
   than hidden inside the kind:** the `total_protein` lane-skip breakdown in the normalization
   entry above — 59 / 4 / 1 by reason, the 48 / 7 / 4 shortfall split, the 46 / 13 / 12 roles of
   the unmatched bands, the 52 doublet lanes, and the 302 ratios over 148 of 149 detected lanes.
   It was added because a human gate asked why 64 lanes go unscored and the answer was not
   written down anywhere. Recording it properly means new `evals/sweep.py` fields with a
   tolerance class, and the pass that measured it was instructed to change no code — so this
   entry is a promise to record it in the next code-touching pass, not a claim that widening the
   words was the right fix. The precedent this paragraph sets is *record the measurement*; this
   is the one figure currently in debt to that rule, and Open items tracks it.
4. **Closed-form and hand-computed values that touch no gold set**, each stated with the
   arithmetic that produces it: the IoU 0.1/(2 - 0.1) = 0.0526 behind the overlap threshold and
   the 26% shared area the generator's 0.15 implies; the 0.25/7 = 3.6% and 9 DN behind the
   dynamic-range fraction; the 1.0 and 1.3788 asymmetries hand-computed in the
   half-maximum-level test, and the 1.0-within-0.2 the symmetric-band fixture asserts; and the
   ~9000 pixels of a full-height lane, which is the canvas height times a lane's width.
5. **Generator parameter values quoted from `synth/MODELS.md`** — `saturated_min_clipped_pixels`
   3, `overlap_iou_threshold` 0.15, `low_dynamic_range_peak_fraction` 0.2, the scratch's
   `0.25 * M`, `bbox_relative_threshold` 0.05, `roi_tilt_excursion_fraction_of_height` 0.5, the
   dust radius 1.5–3.5, and the rest of the coincidence audit's subject matter. These are the
   frozen generator's own constants, not measurements of anything, and `synth/` cannot change
   without a `SYNTH_VERSION` bump and a break marker.
6. **Facts about this repository rather than about the gold set**: the coincidence tally's
   parameter counts (7 of 15, now 12 of 20 — counts of config keys, checkable by reading the two
   config files), schema and version numbers, the numbering of the schema-edit list, the tolerance
   constants in `evals/sweep.py`, and the measured runtimes (1.4 s per background estimate, 43 s
   for the QC pass, about 9m15s of CPU for `--check`), which `.github/workflows/ci.yml` carries
   as well.

The new record fields are compared within four tolerance classes, each derived in
`evals/sweep.py`: `QC_FLAG_COUNT` (+/-4 counts, inherited from the matched band set's own
permitted movement), `IMAGE_FLAG_COUNT` (+/-2, tighter and derived rather than inherited: an
image flag is one decision per image over a 30-image split, and the CI drift the count class
comes from moved 2 of 303 band ROIs, so at most two images can flip on drift that has actually
been observed), `NORMALIZATION_ERROR` (+/-35% of the recorded value, from the leverage of four
ratios on a subset mean — a derivation the record now carries the inputs for, since both
subsets' largest |error| is recorded so the leverage can be recomputed for both means rather
than asserted; the two *medians* inherit that bound without a derivation of their own, which is
recorded as a weakness in the class's docstring) and `EXTREME_RELATIVE`, which the largest ratio
error inherits from the largest recovery error. **The count and error classes are wide, and
neither is a regression alarm** — a one-band change in a QC decision passes `--check`. What
guards the QC behaviour tightly is `tests/test_pipeline_qc.py` against the gold set and the
property assertions in `tests/test_sweep_check.py`; `--check` catches staleness and gross
regression. That is recorded as an Open item rather than presented as a guarantee.

**What the suite asserts about the shoulder threshold, in full**, since an earlier draft of this
section described it incompletely and the omission mattered. Three assertions touch that
surface: the shipped value is present on it; the shipped row fires on at least one band; and its
firing rate on truth-`overlapping` bands exceeds ten times its rate on the rest. Nothing asserts
how *often* it fires — an earlier version added a `> 0.5` recall floor, and that floor together
with the 10× separation admitted exactly one of the six recorded thresholds, so the argmax this
section says is unpinned was still pinned, in a conjunction rather than in an assertion. The
bounds as they now stand admit **1.5, 1.75 and 2.0**, and
`tests/test_sweep_check.py::test_the_shoulder_assertions_do_not_pin_a_single_threshold` re-applies
them to every recorded row and fails if only one row could ever pass — a guard on the guard,
because that is the failure that has already happened once.

---

## After Phase 2 — repository-level decisions

Decisions that belong to the repository rather than to a phase, taken between the Phase 2 merge
and the start of Phase 3. Both are human rulings, recorded here in the same form as the
sweep-harness deviation above because PLAN.md is the contract and a deviation folded in silently
is the thing that form exists to prevent.

### Licence: MIT, chosen by the human — and it does not reach third-party figures

**Human ruling: the repository is MIT licensed.** Nothing in PLAN.md or CLAUDE.md specified a
licence before this. PLAN.md mentions licensing exactly twice and both times about *other
people's* images — the `data/real/` provenance file and Gate 2's "CC-BY figures only, DOI +
licence recorded per image" — so there was no project licence to inherit or contradict, and the
choice was made rather than derived. `LICENSE` at the repository root carries it.

What it covers: the code, and the generated contents of `data/`. The gold set under
`data/images/` and `data/ground_truth/` is output of this repository's own generator from a seed
committed here, so it is this project's work and carries the project's licence.

What it does **not** cover: `data/real/`, when Phase 3 adds it. Those are third-party published
figures, and each will carry its own CC-BY terms with source, DOI and licence recorded per image
in `data/real/provenance.md`, per PLAN.md's Gate 2. **The repository licence does not relicense
them** — MIT on this repository says nothing about a figure someone else published, and a reader
who takes the root `LICENSE` as covering the whole tree would be wrong about exactly the files
where being wrong matters. The distinction is stated in the README's License section for the same
reason.

### The README arrived in the interim, not in Phase 5, as a ratified deviation

**Human ruling: an interim README stays, as a ratified deviation from PLAN.md's phase plan and
from CLAUDE.md's branch convention.** PLAN.md schedules the README as a **Phase 5** deliverable
("README: problem, architecture, eval numbers, screenshots, Design Decisions…, honest
Limitations"), and CLAUDE.md requires work to happen on a branch named `phase-N-<short-name>`.
`README.md` and `LICENSE` were written between Phase 2 and Phase 3 on a branch named
`docs/readme`, which satisfies neither.

The stated reason: **a public repository with three merged phases and no README misrepresents the
project to anyone who opens it.** A reader arriving at that point would have found working code,
a committed gold set and measured numbers, with nothing telling them what the tool is, that no
figure has been measured on a real blot, or that the API, UI and export do not exist. The absence
was itself a misleading claim, and waiting three phases to correct it was the worse option.

It is scoped as interim rather than complete, and says so in its own Status section: **the Phase 5
README supersedes it.** Phase 5 inherits two obligations from this — the screenshots PLAN.md asks
for, which cannot exist until Phase 4 builds a UI, and the real-blot and ImageJ-agreement numbers
that Phase 3 produces. The interim version states in its Limitations that neither exists yet, so
what Phase 5 has to do is replace absence statements with measurements, not walk back a claim.

---

## Open items

Unresolved questions carried out of a phase. Not decisions — each one names the phase
that has to settle it.

### The housekeeping reference is an oracle and needs a real-blot substitute — Phase 3 to settle

**Human ruling, recorded so Phase 3 inherits it explicitly:** designating the reference band
from ground truth's `role` is an oracle input that does not exist on a real blot; it is
acceptable for scoring in Phase 2 provided it is disclosed in the eval output, in this file and
in the PR body, and it goes on this list "alongside the clean-band selector, as another
criterion that needs a real-blot substitute".

So there are now **two** criteria in the project that exist only on synthetic data. The
clean-band selector (Phase 1) chose thirteen parameters by a statistic defined on ground-truth
QC flags. The housekeeping reference (Phase 2) is a ground-truth *role*. Neither can be
computed from a real image, and the housekeeping normalization figures — the whole
`housekeeping_single` row of the Phase 2 table — are conditional on the reference being right.

What Phase 3 has to decide is not the same thing in both cases. The selector needs a
substitute *criterion* (ImageJ agreement is the candidate). The reference needs a substitute
*input*: on a real blot the reference is whatever the scientist says it is, so the honest
answer may be that there is nothing to substitute and the number simply cannot be measured
without a human in the loop — in which case Phase 3 should say so in the README rather than
report a housekeeping accuracy at all. A third possibility, worth naming because it is
tempting and wrong: inferring the reference from the data (the band row that varies least
across lanes, say) would make the pipeline guess the loading control, which is exactly what
the Ruling 2 forbids.

### `low_dynamic_range` is inflated by additive contamination — Phase 3 to settle

The flag reads the brightest detected band's background-corrected peak. A scratch crossing a
lane is additive contamination that a peak measurement cannot distinguish from signal, so on the
three dev images that are both `exposure: low` and `defect: scratch` the measured peak rises well
above the shipped fraction and the flag misses them. The Phase 2 threshold entry carries the
recorded surface: precision 1.000 and recall 0.700 at the shipped value, a perfect row at 0.40,
and 0.40 refused because it is the generator's scratch amplitude.

What would actually fix it is measuring dynamic range on a quantity contamination cannot
inflate — a robust upper quantile of the band peaks rather than the maximum, or the peak of the
lane profile rather than of any single pixel neighbourhood. Both are new measures rather than
new thresholds, both need their own justification, and neither can be chosen honestly on a set
whose only contamination model is this generator's. Phase 3, where real blots arrive with real
artifacts, is the place.

### Band `overlapping` has zero recall against this gold set — needs a ruling

Measured: 0 true positives, 52 misses, one false positive (Phase 2 table). The flag's criterion
is sound — overlapping ROIs double-count shared pixels — but Phase 1's "one band per resolved
maximum" means a doublet yields one ROI, so there is nothing for it to overlap with, and the
flag can only fire where two separately resolved bands' ROIs grow into each other.
`unresolved_shoulder` answers the question a reader of "overlapping" actually has, and does
separate the populations (0.596 against 0.022).

Three ways to resolve it, none of which this phase may take: keep both flags and state the
recall in the README as measured (the current position); raise the generator's
`doublet_offset_sigma` past 2.55 so doublets resolve into two bands and the geometric flag has
something to fire on, which needs the ruling the Phase 0 open item below already asks for and a
regeneration; or retire the geometric flag, which would lose the only flag that fires when two
bands genuinely do share pixels. The measurement above is what a decision should be made on.

### Total-protein normalization is limited by the background residual over a full-height lane

The denominator is the lane's whole background-corrected integral, ~9000 pixels on the committed
canvas, so it accumulates the background estimator's residual bias over an area far larger than
any band. Consequences measured in Phase 2: total_protein's ratio error is larger than
housekeeping's, and on `dev_03` the integral is negative outright, so that lane produces no ratios
and says so.

**The error comparison is suggestive, not established, and must not be quoted as a ratio.** 31.65%
is over 150 included ratios drawn from 86 lanes; 12.22% is over 77 included ratios drawn from 129
lanes, and the housekeeping figure uses an oracle reference this mode does not have. Three
differences at once, so "several times housekeeping's" — which an earlier draft of this entry said
— is not a claim the numbers support. What *is* established: each mode's error on its own subset,
and one lane whose integral is negative. A common-subset comparison would settle the size of the
gap and is worth producing before Gate 1 rules on the measure.

Three candidate directions, all of them changes to the *measure* rather than to a parameter, and
all needing Phase 3's Gate 1: integrate the lane profile above its own robust baseline (what
ImageJ's gel analyzer does), integrate only the rows the lane's bands occupy, or keep the full
integral and report it with an explicit uncertainty from the background estimate. The first two
change what "total protein" means, which is why none of them is a Phase 2 decision.

### The QC and normalization figures are only loosely re-measurement-checked

`python -m evals.sweep --check` compares the new QC and normalization figures within
`QC_FLAG_COUNT` (+/-4 counts) and `NORMALIZATION_ERROR` (+/-35% of the recorded value). Both
derive from the matched band set's own permitted movement, which the detection-count class
already allows to be four bands, so neither can be tightened without failing on drift the
record accepts two sweeps over. The consequence is that a one-band change in a QC decision, or
a few points of ratio error, passes the check.

This is the same shape of gap as the aperture-ordering item below, and it is mitigated the same
way rather than closed: `tests/test_pipeline_qc.py` pins the flag behaviour against gold-set
images case by case, and `tests/test_sweep_check.py` asserts the properties the figures are
quoted for — no missed clipped band, no false image flag, the shoulder separation — from the
record. What would close it is a QC item set that does not move with detection, e.g. scoring
band flags over *truth* ROIs rather than over matched detections. That is a change to the eval's
join, which Phase 3 owns.

### `saturated_min_clipped_pixels` stays at 1 — settled by the human, not an open question

**No longer open.** It shipped at 1, from the criterion that any pixel at full scale makes the ROI
sum a lower bound rather than a measurement, and the human has ruled that it stays there. The
ruling and its reasoning are recorded in full in the Phase 2 threshold entry: any clipped pixel
truncates the integrated intensity and western blot QC treats any saturation as disqualifying; 3
is the generator's labelling threshold, not a biological one; matching it would be a third
circularity of the family the test suite has already had two removed from; and the two "false
positives" are **correct detections against a coarser label**, so the flag and the ground-truth
label disagree by design and the score is what is wrong there, not the flag.

The measurement stays on the page because it is the evidence the trade was taken deliberately:
F1 **0.958** at the shipped 1 against **1.000** at the generator's 3, from a recorded surface.

**The mechanics of a change are kept here anyway, because a future phase may still want them.**
Moving the value is a config line **plus two test edits plus a record regeneration.**
`tests/test_pipeline_qc.py` deliberately pins the shipped value — it asserts
`saturated_min_clipped_pixels == 1` and parametrises `(1, True)`, `(2, True)` — so `pytest` fails
loudly rather than passing on a stale claim. The record needs regenerating too, and here is the
part that does not announce itself: at 3 the `band.saturated` row moves fp 2 → 0 and tn 254 → 256,
both **inside** `QC_FLAG_COUNT`'s ±4, so `python -m evals.sweep --check` would *pass* on the stale
record. What catches it is the digest comparison — a changed config parameter moves both shipped
digests — not the figure guard. Worth knowing that the figure guard would not have caught it alone.

### The `low_dynamic_range` threshold trades recall against circularity — Phase 3 to settle

**Human's ruling, verbatim:**

> image `low_dynamic_range` ships at recall 0.700 / precision 1.000 — three genuinely low-range
> images are not flagged. For a QC-first tool, silence is the more dangerous error direction, and
> precision 1.000 leaves room to trade. Do NOT change the threshold now. Record it in NOTES.md
> under Open items as a Phase 3 decision, with the measured precision/recall trade-off curve if a
> sweep already covers it, and state the asymmetry argument: under-warning is worse than
> over-warning for this flag.

A sweep does cover it. The curve, transcribed from `evals/dev_sweeps.md`:

<!-- sweep: qc.dynamic_range_min_peak_fraction -->
| peak fraction | images | tp | fp | fn | truth low-range images |
|---|---|---|---|---|---|
| 0.15 | 30 | 5 | 0 | 5 | 10 |
| 0.2 | 30 | 7 | 0 | 3 | 10 |
| **0.25** | 30 | 7 | 0 | 3 | 10 |
| 0.3 | 30 | 8 | 0 | 2 | 10 |
| 0.4 | 30 | 10 | 0 | 0 | 10 |
<!-- end sweep -->

**The fact that sharpens the ruling: precision is 1.000 at every swept threshold, including 0.40.**
The false-positive column is zero all the way up. So on this gold set raising the threshold costs
*nothing measurable* — not a single image that ground truth calls fine would be flagged — and the
only objection to 0.40 is that it sits above the amplitude the generator gives a scratch
(`0.25 * M`, `MODELS.md` §5), i.e. the tuning-to-the-generator objection rather than a measured
cost.

That is what makes this a genuine Phase 3 decision rather than a settled trade: **the
anti-circularity argument and the QC-asymmetry argument point in opposite directions here.**
Anti-circularity says do not move a threshold onto a generator constant to buy a score.
QC asymmetry says under-warning is worse than over-warning for this flag, because a silently
unflagged low-dynamic-range image yields numbers a reader trusts, while an over-warned one yields
a caveat a reader can dismiss. Both are right, and neither can break the tie on synthetic data,
because the whole disagreement is about a generator constant. A real-blot criterion is what would
settle it — which is Phase 3, where real blots arrive with real artifacts and where a scratch
amplitude is whatever a scratch happens to be.

What Phase 3 should weigh, beyond the threshold: the deeper fix is a *measure* a scratch cannot
inflate, since the misses are all `exposure: low` images that also carry `defect: scratch` and the
brightest peak among truth's low-range images is 0.3226 of full scale. A robust upper quantile of
the band peaks rather than the maximum, or the lane profile's peak rather than a single pixel
neighbourhood, would make the threshold question smaller. That direction is recorded in its own
open item below.


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

### The `total_protein` lane-skip breakdown is not in the record — record it next code pass

A human gate asked why the shipped mode is scored on 86 of 150 lanes, and the answer was not
written down anywhere: 59 lanes because not every truth band in them was matched, 4 because none
matched inside the matched lane, 1 because detection missed the lane, with the missing band
overwhelmingly the doublet partner (46 `target_secondary` of 71 unmatched). Those figures are now
in the normalization entry as prose, which makes them the one aggregate measurement in the
section the sweep record does not hold — the debt the outside-a-block taxonomy names in kind 3.

Recording it means new fields on the `normalization_modes` rows (unscored lanes by reason, and
the roles of the unmatched bands) plus a tolerance class, since `evals/sweep.py` raises on an
unclassified field. The pass that measured this was instructed to change no code, so it is
deferred rather than declined. Whoever next touches `evals/sweep.py` should close it; the numbers
above are the expected values.

Worth carrying into that change: the same accounting shows the *product* leaves only one lane
without a ratio (302 ratios over 148 of 149 detected lanes), so the eval's 64 and the product's 1
are answers to different questions, and a record that reports only one of them will be misread
the way the first draft of the Phase 2 PR body was.

### Phases 1 and 2 have already selected parameters on dev, which pre-empts Phase 3's Gate 1

PLAN.md puts "Gate 1 (human): eval design sign-off **before parameter iteration begins**"
ahead of "iterate detection/background parameters on the dev split to plateau". Phase 1
selected thirteen of its fifteen shipped numeric parameters from thirteen dev-split sweeps, so
that gate has been pre-empted in substance even though the artifact it produced —
`evals/sweep.py` plus `evals/dev_sweeps.json` — exists to make Phase 1's recorded claims
checkable rather than to iterate to a plateau.

**Phase 2 adds five parameters and two more dev-split surfaces to that list.** The count is
now twenty numeric parameters, not fifteen. The five QC parameters were each chosen from a
criterion stated without reference to the generator and *then* measured, which is a different
process from Phase 1's selection — but two of them need the human's eye at Gate 1 all the same:

- **`qc.shoulder_half_width_ratio = 1.5` is the one whose justification leans on a dev
  measurement.** Its criterion ("one side at least half again as wide as the other") restates
  the value rather than deriving it, and the recorded surface shows 1.5 is also where the
  separation between the two truth populations is widest. Phase 2 says so in its own entry and
  deliberately does *not* pin the argmax in CI, but the honest summary is that this value's
  position on a dev-split surface is part of why it is 1.5. It is the Phase 2 parameter most
  likely to need redoing under a real-blot criterion.
- **`qc.saturated_min_clipped_pixels = 1` is a disclosed trade against a better score**, and
  there is a separate open item below asking whether the human prefers 3. Gate 1 is the natural
  place to settle it, since the choice is a policy question (how conservative a QC flag should
  be) rather than a measurement.

The other three — the overlap IoU threshold, the dynamic-range fraction and the half-maximum
level — are derived from stated criteria and are not any surface's argmax; the dynamic-range
entry explicitly refuses the value that would have scored best. Gate 1 should still see them,
because the two recorded QC surfaces are dev-split surfaces like any other.

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

1. **Gate 1 must ratify or redo the thirteen parameters Phase 1 selected on the dev split, and
   take a position on the two Phase 2 thresholds named above.** They are not provisional
   defaults that Phase 3 may quietly keep; they are a selection that pre-empted the gate, and
   the gate has to take a position on it either way.
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
