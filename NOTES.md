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

## Open items

Unresolved questions carried out of a phase. Not decisions — each one names the phase
that has to settle it.

### The `doublet` cell renders one peak with a shoulder, not two peaks — needs a ruling

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
