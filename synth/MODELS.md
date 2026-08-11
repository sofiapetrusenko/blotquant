# Generator models (SYNTH_VERSION 1.0.0)

This document is the complete, precise description of how a synthetic blot is built.
It exists for two reasons:

1. So a reviewer can check `pipeline/` for **special-casing of generator artifacts** —
   see [What counts as special-casing](#what-counts-as-special-casing).
2. So the ground-truth intensity contract is unambiguous when eval numbers are
   disputed.

Everything below is driven by `synth/config.py`. There are no literals in the
rendering functions; the parameter names used here are the field names of the config
dataclasses, and every value in effect for an image is written into that image's
ground truth under `generation.parameters`. The difficulty-matrix axes — which levels
exist on each axis, and in which order — are declared there too, in `MatrixConfig`;
`synth/matrix.py` only schedules them. Every level named on an axis must have a
parameter entry in the matching mapping, which `GeneratorConfig` checks at
construction. Axis levels are therefore covered by `config_digest`, the freeze guard.

Notation: `M` = `max_value` = `2**bit_depth - 1`; `W`, `H` = canvas width/height in
pixels — the committed set uses `width_px = 256`, `height_px = 192` for every image;
arrays are indexed `image[y, x]` with `x` rightward and `y` downward (the
migration direction). Intensities are in DN of the image's own bit depth. Signal is
positive on a dark background (chemiluminescence convention).

---

## 1. Rendering pipeline

The image is assembled in float64 in exactly this order:

```
signal      = background + Σ_b band_b                      (§2, §4)
composite   = signal * dust_mask + scratch_layer           (§5)
noisy       = gaussian(poisson(composite))                 (§6)
pixels      = clip(floor(noisy + 0.5), 0, M) → uint8/uint16 (§7)
```

Nothing is re-normalised, re-scaled or auto-levelled after quantization.

## 2. Background models (axis `background`)

Common form: `background(x, y) = M * (base_fraction + gx * x/(W-1) + gy * y/(H-1)) + Σ blobs`.

| level | parameters in effect |
|---|---|
| `flat` | `base_fraction = 0.08`, no gradient, no blobs |
| `gradient` | `base_fraction = 0.06`, `gradient_x_fraction = 0.10`, `gradient_y_fraction = 0.04` |
| `speckle` | `base_fraction = 0.09`, `blob_count = 14`, `blob_sigma_px_min/max = 4.0 / 18.0`, `blob_amplitude_fraction_min/max = 0.010 / 0.045`, `blob_bright_probability = 0.6` |

Speckle blobs are isotropic Gaussians:
`amplitude * exp(-0.5 * ((x-cx)² + (y-cy)²) / sigma²)` with, per blob and drawn from
the `background` stream in this order: `cx ~ U(0, W-1)`, `cy ~ U(0, H-1)`,
`sigma ~ U(4.0, 18.0)` px, `|amplitude|/M ~ U(0.010, 0.045)`, and a
sign that is positive with probability `blob_bright_probability` (0.6), negative
otherwise. The blob field is **low-frequency and additive**, not multiplicative.

A background layer that would go negative raises `RenderError` rather than being
clamped. Every blob's centre, sigma and signed amplitude is recorded in
`background_truth.speckle_blobs`.

## 3. Gel geometry (axis `lane_geometry`)

Let `cx0 = (W-1)/2`, `cy0 = (H-1)/2`, `s = tan(tilt_deg)`.

- Lane centre at row `y`: `x_lane(y) = x_nominal + s * (y - cy0)`.
- Band row centre at column `x`:
  `y_row(x) = y_nominal - s * (x - cx0) + smile_amplitude_px * ((x - cx0)/cx0)²`.

The `-s` on the row and `+s` on the lane together are a rigid rotation of the whole
gel — lanes and band rows stay perpendicular. The quadratic term is the gel smile:
band rows sag toward both canvas edges by `smile_amplitude_px` (9 px), symmetric
about the centre column.

| level | `tilt_deg` | `smile_amplitude_px` |
|---|---|---|
| `straight` | 0 | 0 |
| `tilted` | 4 | 0 |
| `smile` | 0 | 9 |

## 4. Bands (axes `band_shape`, `exposure`, `lane_count`)

**Layout.** `lane_pitch = (W - 2*margin_x_px) / lane_count` with `margin_x_px = 14`;
lane `k` is centred at `margin_x_px + (k + 0.5) * pitch`. Two band rows are placed at
`row_y_fractions = (0.32, 0.70) * H`, with roles `("target", "housekeeping")`.

**Profile.** For a band of peak `A` at nominal `(x_n, y_n)`:

```
u = (x - x_lane(y)) / sigma_x
v = (y - y_row(x))  / sigma_y
band(x, y) = A * exp(-0.5 * |u|^p) * exp(-0.5 * v²)
```

`p = flat_top_exponent = 4` gives a flat-topped, steep-shouldered profile across the
lane (a plain Gaussian would be `p = 2`); the migration direction is a true Gaussian.

**Both sigmas are tied to the lane pitch.**

```
sigma_x = sigma_x_fraction_of_pitch * pitch = 0.24 * pitch
sigma_y = sigma_x / aspect_ratio                     (aspect_ratio from the band shape)
```

so a band's *shape* is a property of its `band_shape` level alone, while its *size*
scales with lane count. `aspect_ratio` — not `sigma_y` — is the configured quantity.

**Why the aspect ratio is constrained.** A western blot band is the cross-section of a
sample lane: the well width and the lane it migrated in bound how wide it can get,
and nothing bounds it to be equally tall, so a real band is always substantially
wider than it is tall. A near-circular blob (`sigma_x ≈ sigma_y`) is a *dot-blot*
artifact — dot blots apply sample directly to the membrane with no electrophoretic
lane — and dot blots are explicitly out of scope (PLAN.md). Generating one would put
a shape in the gold set that the pipeline should never meet on a real western blot,
and would score a lane/band detector against geometry no gel produces.

`band_layout.min_aspect_ratio = 2.5` is therefore a floor on `sigma_x / sigma_y` that
**every** `band_shape` level must clear. `GeneratorConfig.__post_init__` checks every
level at construction and raises `ConfigError` naming the offending level and its
ratio; it is never clamped, because a clamp would leave the config describing a shape
the generator did not render.

Deriving `sigma_y` from `sigma_x` rather than fixing it in pixels is what makes that
floor a guarantee rather than a coincidence. `sigma_x` varies with lane count
(13.68 px at 4 lanes, 10.94 at 5, 9.12 at 6), so an absolute `sigma_y_px` produced a
different — and uncontrolled — aspect ratio on every lane-count level, roundest at 6
lanes. With the ratio configured directly it is exact and identical at 4, 5 and 6
lanes.

| level | `aspect_ratio` | `sigma_y` at 4 / 5 / 6 lanes (px) | doublet |
|---|---|---|---|
| `sharp` | 5.0 | 2.74 / 2.19 / 1.82 | no |
| `smeared` | 2.6 | 5.26 / 4.21 / 3.51 | no |
| `doublet` | 4.0 | 3.42 / 2.74 / 2.28 | yes: partner at `+2.2 * sigma_y`, amplitude `0.65 * A` |

`smeared` is 1.923x more diffuse along the migration axis than `sharp` at every lane
count, and sits closest to the floor by design: it is the hardest cell. That spread
survives into the delivered ROIs: across the committed set a 6-lane target ROI is
9 px tall for `sharp`, 11–12 px for `doublet` and 17–18 px for `smeared`.

The doublet partner is added to the **target row only** (role `target_secondary`);
the housekeeping row stays a single band in every cell. Its offset is expressed in
units of `sigma_y`, so the doublet's shape is scale-free like every other level. At
`doublet_offset_sigma = 2.2` with `doublet_amplitude_ratio = 0.65` the two Gaussians
sum to a **single maximum with a pronounced shoulder**, not two resolved peaks. Both
thresholds for that are properties of the profile, not of any image: the continuous
sum for amplitude ratio 0.65 first becomes bimodal at **2.4605** sigma of separation,
and on the pixel grid a second maximum appears at all three lane counts from **2.55**
sigma. Both bands are labelled `overlapping`; across the committed set the partner's
ROI has IoU **0.3529 to 0.4286** with the primary's.

**How visible the partner is depends on the cell, and the committed set spans the
range.** In the noise-free layer the shoulder is unambiguous: over the committed
doublet images, the lane-centre column carries **5.0x to 13.3x** the band signal one
`doublet_offset_sigma` below the primary's centre that it carries the same distance
above (8.31x for the undistorted continuous profile; tilt, smile and dust spread it).
In the delivered pixels it is noise-limited. Over the 69 doublet lanes of the
committed set, the partner's own peak runs from **52.5x down to 0.6x** the per-pixel
noise standard deviation there (`sqrt(gain * composite + (read_noise_fraction * M)²)`,
§6), and is under 3x in 18 of them — in the weak lanes (`target_relative_levels` down
to 0.18) under `high` noise, no single pixel establishes the partner. Integrated over
its own ROI the partner's signal clears that noise in every committed cell, but by as
little as **3.8 sigma**. A pipeline is therefore expected to find the shoulder in the
bright, low-noise doublet cells and to miss it in the weak, high-noise ones.

**Amplitudes.** `peak = reference_peak_fraction * M` from the exposure level. Target
band in lane `k` has `A = peak * target_relative_levels[k mod 6]` with levels
`(1.0, 0.72, 0.48, 0.30, 0.62, 0.18)` — a fixed, non-monotonic "biological" pattern.
Housekeeping bands have `A = peak * 0.55 * (1 + j)` with
`j ~ U(-1, 1) * housekeeping_jitter_fraction` (±6%) drawn from the `bands` stream.

| exposure level | `reference_peak_fraction` | consequence |
|---|---|---|
| `normal` | 0.55 | no clipping |
| `low` | 0.12 | below `low_dynamic_range_peak_fraction` (0.20) → image flagged |
| `saturating` | 1.35 | the strongest lanes exceed `M` and clip at §7 |

**ROI.** A band's ground-truth ROI is the tight integer bounding box of the pixels
where that band's own noise-free layer, *before* dust attenuation (§5), is
`>= bbox_relative_threshold * A`
(0.05, i.e. ±1.565·sigma_x and ±2.45·sigma_y). Because it is derived from the
rendered layer, it follows tilt and smile automatically. `roi_mass_fraction` records
what fraction of the band's total signal the ROI contains — across the committed set
it spans **0.9732 to 0.9899**.

A band is labelled `overlapping` when its ROI has an intersection-over-union of at
least `overlap_iou_threshold = 0.15` with any other band's ROI; the neighbours are
named in `overlapping_band_ids`. In practice only `doublet` cells trip it.

## 4a. Lane ROI (`lanes[].roi`)

Unlike a band ROI, a lane ROI is **not** derived from rendered pixels. It is a
declared rectangle built from the `lane_layout` config block, echoed per image under
`generation.parameters.lane_layout`:

```
half_width = roi_width_fraction_of_pitch * lane_pitch / 2      (1.0 → half a pitch)
excursion  = |s| * roi_tilt_excursion_fraction_of_height * H   (0.5 → |s| * H/2)
x_left  = max(0,   floor(x_nominal - half_width - excursion))
x_right = min(W-1, ceil (x_nominal + half_width + excursion))
roi = {x: x_left, y: 0, width: x_right - x_left + 1, height: H}
```

So: full canvas height, one lane pitch wide about the lane's nominal centre, widened
symmetrically by the distance a tilted lane centre travels between the canvas
mid-row and either edge (§3: `x_lane(y) = x_nominal + s*(y - cy0)`, so that distance
is `|s| * H/2`), then clamped to the canvas.

Consequences, all deliberate:

- With `roi_width_fraction_of_pitch = 1.0`, adjacent lane ROIs **abut exactly** in a
  `straight` cell and **overlap** in a `tilted` one. Lane ROIs are a partition of the
  canvas between the margins, not a tight fit around lane content.
- The smile term does not enter: smile displaces band rows in `y`, not lane centres in
  `x`, so `smile` cells have the same lane ROIs as `straight` cells.
- Edge lanes clamp at `x = 0` and `x = W-1`, so in a tilted cell they widen by less
  than the interior lanes.

A lane ROI is therefore a scoring convention, not a measurement: it says which columns
belong to lane `k` by construction. A pipeline must reach a comparable answer from the
pixels, not by reproducing this rule — see
[What counts as special-casing](#what-counts-as-special-casing).

## 5. Defects (axis `defect`)

Drawn from the `defects` stream, dust first then scratches.

- **`dust`** — 6 specks. Per speck: `cx ~ U(0, W-1)`, `cy ~ U(0, H-1)`,
  `radius ~ U(1.5, 3.5)`. Each multiplies the image by
  `1 - (1 - dust_transmittance) * exp(-0.5 * r²/radius²)` with
  `dust_transmittance = 0.35`. Dust blocks light, so it attenuates background and
  bands alike — and it therefore **reduces the recorded true band intensity** (§8).
- **`scratch`** — 1 segment. `cx, cy ~ U` over the canvas, `angle ~ U(0, π)`,
  `length ~ U(60, 140)`; endpoints are `centre ± (length/2)·(cos θ, sin θ)`. It adds
  `0.25 * M * exp(-0.5 * d²/sigma²)` where `d` is the distance to the segment and
  `sigma = scratch_sigma_px = 1.2`. A scratch is **additive contamination**: it is
  never part of any band's true intensity, so a pipeline that integrates a scratch
  crossing an ROI will legitimately over-read.
- **`none`** — mask is 1 everywhere, scratch layer is 0.

Every speck and segment is recorded in `defects[]` with its geometry.

## 6. Noise (axis `noise`)

Always both, always in this order, drawn from the `noise` stream:

1. **Poisson shot noise.** `gain = M / photon_full_well` DN per photon;
   `noisy = Poisson(composite / gain) * gain`. Expressing the well depth in photons
   makes the *relative* noise identical at 8-bit and 16-bit.
2. **Gaussian read noise.** `+ N(0, read_noise_fraction * M)`, added to every pixel.

| level | `photon_full_well` | `read_noise_fraction` |
|---|---|---|
| `low` | 4096 | 0.004 |
| `high` | 512 | 0.012 |

Noise is applied to the composite, so it is signal-dependent inside bands and
background-dependent outside them. There is no spatial filtering, so the noise is
pixel-independent — a real detector would have a slight PSF-induced correlation;
this generator does not model one.

## 7. Quantization, saturation and containers

`pixels = clip(floor(noisy + 0.5), 0, M)` cast to `uint8`/`uint16`. Unsupported bit
depths raise `UnsupportedBitDepthError`; nothing is ever squashed to 8-bit.

A pixel is counted as **clipped** when its final value equals `M`. This is the
*detectable* definition: a pixel sitting at full scale is indistinguishable from one
that was truncated, so ground truth labels what a pipeline could in principle
observe. A band is labelled `saturated` when at least
`saturated_min_clipped_pixels = 3` pixels in its ROI are clipped (3 rather than 1, so
that a lone noise excursion is not called saturation). The image-level `saturated`
label means *at least one band is labelled saturated* — it is a statement about
bands, not about the image, so it can in principle differ from
`image_stats.clipped_pixel_count > 0` when clipping falls entirely outside every ROI
(no such image exists in the committed set).

`observed_peak_dn` is the brightest quantized pixel inside the ROI in the finished
image: this band's signal plus background, noise, any overlapping band and any
scratch. The band's own peak is `amplitude_dn`.

All clipped-pixel counts are taken on the canonical pre-encoding array. JPEG
re-encoding smooths the clipped plateau and lowers the count on re-decode (measured:
160 → 88 image-wide on `dev_00`), though every band labelled `saturated` in a lossy
image still shows ≥ 15 clipped pixels after re-decode, so the label stays observable
from the delivered file.

Containers (axis `format_depth`, legal pairs only): `tiff8`, `tiff16` (zlib-compressed,
via tifffile), `png8`, `png16` (OpenCV, compression level 6), `jpeg8` (OpenCV,
quality 75). JPEG is 8-bit only; requesting 16-bit JPEG raises
`UnsupportedFormatError`. JPEG images carry the `lossy_format` label.

The **canonical** image is the quantized array, not the file: `pixel_sha256` in the
ground truth is the digest of that array (with dtype and shape folded in, bytes taken
little-endian), because container bytes depend on the local zlib/libjpeg build while
the array does not.

## 8. The ground-truth intensity contract

For each band, `true_integrated_intensity_dn` is:

> the sum, **inside that band's ground-truth ROI**, of **that band's own** noise-free
> signal layer, **after** dust attenuation and **before** quantization clipping.

Consequences, all deliberate:

- It is background-free by construction, so it compares directly against a
  background-corrected pipeline measurement.
- It excludes the additive scratch (§5) and excludes any overlapping neighbour's
  signal. In a `doublet` cell a box sum over an ROI therefore over-reads. Measured
  across the committed set, with a noise-free, background-free, dust-attenuated box
  sum — the best case a pipeline could reach — the over-read is **82.4% to 113.1%** on
  the weaker `target_secondary` partner (whose ROI is dominated by the primary) and
  **37.3% to 51.7%** on the primary. The ranges are wide because tilt, smile and dust
  shift the two ROIs relative to each other, i.e. because the doublet cells are
  crossed with every other difficulty axis. That is the point of the cell, and both
  bands carry `overlapping`.
- For a `saturating` cell it is the intensity the band *would* have produced with
  unlimited headroom. No pipeline can recover it from the clipped image. That is why
  saturated values must be QC-flagged rather than trusted, and why recovery error on
  saturated bands is expected to be large.

`true_total_intensity_dn` is the same sum over the whole canvas;
`roi_mass_fraction` is their quotient.

`normalization.ratios[]` gives, per lane, `true_ratio = target / housekeeping` using
those ROI-integrated truths, plus the QC flags of both bands so the eval can decide
what to exclude and record the decision.

## 9. Determinism

- Seeds: `seed = int(sha256(f"{master_seed}:{label}")[:4])`, with
  `label = f"image:{image_id}:{stream}"` for the four per-image streams
  (`background`, `bands`, `defects`, `noise`) and `f"matrix:{axis}:{split}"` for the
  matrix. All four derived seeds are recorded per image.
- Random numbers come from `numpy.random.RandomState` (the legacy generator), which
  numpy guarantees to be stream-stable across releases. `numpy.random.Generator`
  carries no such guarantee and is not used anywhere in `synth/`.
- Separate streams per component mean adding a draw in one component cannot shift
  another's numbers.
- Floats written to ground truth are rounded to `json_float_decimals = 6`, which
  absorbs last-bit differences in `exp`/`tan` between CPU architectures.
- `config_digest` hashes the config in declaration/insertion order, not sorted order,
  because the order of the levels on an axis is itself a generation parameter: it is
  what the balanced round-robin schedule assigns from. Reordering an axis changes
  which image gets which level, and the digest must see that.

## What counts as special-casing

`pipeline/` must never import from `synth/` and must never exploit anything in this
document that a real gel-doc image would not exhibit. Concretely, the following would
be special-casing and must be rejected in review:

- Assuming the **exact functional forms** here: a flat-top exponent of 4, a Gaussian
  migration profile, a strictly linear background ramp, or isotropic Gaussian blobs.
  Fitting a general smooth background or a general peak shape is fine; hard-coding
  *these* shapes is not.
- Assuming the **layout**: exactly two band rows, rows at 0.32/0.70 of image height,
  4–6 lanes, uniform lane pitch, lanes centred at `margin + (k+0.5)·pitch`, or the
  target/housekeeping row order.
- Assuming the **band aspect ratio**: that `sigma_x/sigma_y` is exactly 5.0, 2.6 or
  4.0, that it takes one of only three values in an image set, that it is constant
  across lanes within an image, or that `sigma_y` can be recovered from a detected
  `sigma_x` by dividing by a constant. That every band here is at least 2.5x wider
  than tall is a *biological* fact a pipeline may rely on as a weak prior (real bands
  are lane-constrained); the specific ratios are generator parameters and are not.
- Assuming the **lane-ROI construction** (§4a): that a lane ROI is exactly one lane
  pitch wide, spans the full canvas height, starts at `y = 0`, is widened only by
  `|s| * H/2`, or that adjacent lane ROIs tile the canvas. Detecting lane boundaries
  from the column profile is the task; emitting `margin + k*pitch .. margin + (k+1)*pitch`
  is scoring against a rule that no real gel-doc image supplies.
- Assuming the **amplitude pattern** `(1.0, 0.72, 0.48, 0.30, 0.62, 0.18)`, that
  housekeeping is ~0.55 of the reference peak, or that housekeeping varies by ≤6%.
- Assuming **noise parameters**: that shot noise is exactly Poisson with
  `M/512` or `M/4096` DN per photon, that read noise is exactly Gaussian, or that
  noise is spatially uncorrelated (it is here; real images are not).
- Assuming **geometry parameters**: tilt is exactly 0° or 4°, smile is exactly
  quadratic with 9 px amplitude, or that tilt and smile never co-occur.
- Assuming the **defect model**: dust is circular with transmittance 0.35, scratches
  are single straight segments 60–140 px long.
- Reading `data/ground_truth/`, `data/generation_config.json`, `pixel_sha256`, the
  image id naming scheme (`dev_NN` / `test_NN`), or the file extension as a proxy for
  difficulty, at any point in the analysis path.
- Any parameter that happens to equal a number in this document without an
  independent justification recorded in the config and in NOTES.md. Coincidence is
  indistinguishable from tuning-to-the-generator, and the reviewer should treat it as
  the latter.

The external check on all of this is ImageJ agreement on real CC-BY blots (Phase 3):
a pipeline that has been fitted to this generator will do well on synthetic data and
badly there.
