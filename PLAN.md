# blotquant — Project Plan

QC-first western blot densitometry with a measured evaluation harness. CLI-first; UI last.

**Problem.** Densitometry is the most commonly misused quantification method in molecular biology: saturated bands, exposures outside the linear range, arbitrary background subtraction, normalization to housekeeping proteins that themselves shift under treatment. Existing tools (ImageJ/Fiji) let all of this pass silently. blotquant quantifies bands *and* refuses to produce unpublishable numbers silently: every value carries QC flags, explicit parameters, and full provenance down to ROI coordinates.

**Differentiation vs ImageJ:**
1. QC-first — pixel-clipping detection, dynamic-range warnings, lossy-format warnings.
2. Total-protein normalization as a first-class mode; single-housekeeping normalization emits a recorded warning (per current journal guidelines).
3. Measured accuracy — synthetic ground truth + held-out test split + ImageJ agreement on real blots. README states numbers.
4. Provenance — every exported number traceable to ROI + parameter set + software version; auto-generated methods paragraph.

**Scope (MVP).** Single-channel grayscale gel-doc images: 8/16-bit TIFF, PNG, JPEG. Chemiluminescence workflow. Out of scope: multichannel fluorescence, dot blots, 2D gels.

---

## Architecture

```
synth/               synthetic blot generator — FROZEN after Phase 0 (SYNTH_VERSION)
  MODELS.md          documents the generator's noise/background models
data/ground_truth/   generator-written ground truth JSON — never hand-edited, never written by pipeline
data/real/           CC-BY real blots + provenance.md (source, DOI, license per image)
pipeline/            load, detect, quantify, background, qc, normalize
evals/               metrics, runner, history.md, imagej/ (headless Fiji comparison)
api/                 FastAPI
web/                 Next.js + TypeScript UI
schema/              ground_truth + result JSON Schemas (source of truth)
configs/             explicit processing parameter sets (YAML)
```

**Stack:** Python 3.11, OpenCV, scikit-image, NumPy, FastAPI; Next.js + TypeScript; pytest; GitHub Actions.

**Key invariants:**
- `data/ground_truth/` is written ONLY by `python -m synth`. Pipeline/api/web code never writes there. Hand-editing never happens.
- `synth/` is FROZEN after the Phase 0 merge. Changes require an explicit human instruction, bump `SYNTH_VERSION`, and add a break marker in `evals/history.md` (scores across the break are not comparable).
- Anti-circularity: `pipeline/` never imports from `synth/` and never special-cases generator artifacts. Generator models are documented in `synth/MODELS.md` so the reviewer can check for special-casing. External accuracy check = ImageJ agreement on real blots.
- Split discipline: every synthetic image is assigned dev or test at generation time, recorded in its ground truth. Parameter iteration happens on dev only. Test is run once per phase, at the end, and logged.
- Every processing parameter lives in an explicit config object and is recorded in per-result provenance. No hidden auto-tuning, no magic numbers in function bodies.
- QC annotates, never silently drops: a saturated band still reports a value plus `qc_flags: ["saturated"]`; flagged bands are excluded from normalization ratios by default, with an explicit, recorded override.
- Loud failure over silent fallback. Unsupported bit depth raises; it is not squashed to 8-bit.

---

## Phase 0 — Synthetic ground truth + metrics (the gold set, generated)

- [ ] `synth/generator.py`: seeded, deterministic. Difficulty matrix — bit depth (8/16), format (TIFF / PNG / JPEG q75), background (flat / gradient / speckle), noise (Gaussian + Poisson), band shape (sharp / smeared / overlapping doublet), lane geometry (straight / tilted / gel-smile curvature), defects (dust, scratch), saturation (clipped peaks at bit-depth max)
- [ ] `schema/ground_truth.schema.json` + `schema/result.schema.json`
- [ ] 40+ images: 30 dev / 10 test, split assigned at generation and recorded in ground truth
- [ ] `evals/metrics.py`: band detection P/R/F1 at IoU ≥ 0.5; intensity recovery error (relative % vs true integrated intensity, post-background); normalization ratio error; QC flag accuracy. Unit-tested on toy fixtures.
- [ ] `synth/MODELS.md` documenting every noise/background model used
- [ ] `NOTES.md` started (running log of design decisions — feeds README's Design Decisions)

**Done when:** `python -m synth --seed 42 --out data/` reproduces identical ground-truth JSON and identical pixel-array hashes (determinism test in pytest); all ground truth validates against schema in CI; metric functions unit-tested.

## Phase 1 — Core pipeline (CLI) + minimal eval loop

- [ ] `pipeline/load.py`: bit-depth-aware loading; raises on unsupported input; records source format
- [ ] `pipeline/detect.py`: lane detection via profile projection, band detection via peak finding → ROIs
- [ ] `pipeline/background.py`: rolling-ball + local-median; method is a required explicit parameter, no default
- [ ] `pipeline/quantify.py`: integrated intensity per ROI after background correction
- [ ] CLI: `python -m pipeline run <image> --config configs/default.yaml --out results/`
- [ ] `evals/run.py` v0: runs pipeline over the synthetic dev split, prints detection F1 + recovery error table

**Done when:** end-to-end on the full synthetic dev set without manual intervention; eval table prints; failures are loud and logged. Test split NOT touched.

## Phase 2 — QC + normalization + provenance

- [ ] `pipeline/qc.py`: saturation (pixel clipping at bit-depth max), dynamic-range warning, band-overlap warning, `lossy_format` flag for JPEG input
- [ ] `pipeline/normalize.py`: modes = `housekeeping_single` (emits a recorded warning), `housekeeping_multi` (geometric mean), `total_protein` (lane-profile integral). QC-flagged bands excluded from ratios by default; override is explicit and recorded.
- [ ] Provenance JSON per result: ROI coordinates, full parameter set, software version, qc_flags, normalization mode + warnings. Validates against `result.schema.json`.

**Done when:** QC flags fire correctly on the synthetic saturated/clipped cases, tested against ground truth's saturation labels; normalization verified against hand-computed fixtures; provenance validates against schema.

## Phase 3 — Full evals + real-blot cross-validation — HUMAN GATES

Gate 1 (human): eval design sign-off before parameter iteration begins.
Gate 2 (human): approve the real-image list — CC-BY figures only, DOI + license recorded per image in `data/real/provenance.md`.

- [ ] `evals/run.py` full: per-difficulty-cell breakdown (which matrix cells fail); `evals/history.md` — one row per iteration: what changed, dev scores; test scores reported once at phase end
- [ ] Iterate detection/background parameters on the dev split to plateau
- [ ] `evals/imagej/`: headless Fiji comparison — script downloads a pinned Fiji release, runs an ImageJ macro per real blot, parses Results.csv, reports correlation + mean |Δ| vs blotquant. Fallback if headless install fails in this environment: documented manual ImageJ protocol, human runs it once (~1 h).

**Done when:** README can state "band detection F1 = X and mean intensity recovery error = Y% on a held-out synthetic test set; agreement with ImageJ r = Z on N real CC-BY blots." Numbers honest, methodology documented.

## Phase 4 — API + UI

- [ ] FastAPI: `POST /analyze` (image + config) → result JSON; `GET /results/{id}`. Synchronous processing is acceptable for MVP (images are small) — decide in-phase, note in PR.
- [ ] Next.js UI: upload → auto-detected lanes/bands overlaid on the image → correction via draggable ROI edges AND numeric nudge fields (keyboard-accessible) → recompute → results table with QC badges
- [ ] Trust feature: click any number → its ROI highlights on the image + full parameter set shown (the provenance mirror)
- [ ] Aesthetic register: serious research tool (UniProt / Linear, not SaaS landing page); colorblind-safe QC encoding

**Done when:** a scientist uploads a gel-doc export, corrects one lane boundary, and gets a normalized, QC-annotated table in under 2 minutes.

## Phase 5 — Export + polish + deploy

- [ ] CSV + XLSX export; per-figure PNG/SVG chart of normalized values with QC annotations
- [ ] Methods-paragraph generator from provenance ("Bands were quantified using rolling-ball background subtraction (radius = 50 px); signals normalized to total lane protein…")
- [ ] pytest coverage of failure modes; CI green
- [ ] Deploy: API on DigitalOcean, web on Vercel
- [ ] README: problem, architecture, eval numbers, screenshots, Design Decisions (from NOTES.md), honest Limitations (JPEG caveat, chemiluminescence non-linearity, no fluorescence support)
- [ ] Stretch: replicate-aware stats (mean ± SD across replicate lanes)

**Done when:** a stranger can clone, run, and understand the project from the README alone; CI green; demo live.

---

## Loop protocol (working agreements)

- One phase = one session = one PR. Branch `phase-N-<short-name>`. Never push to `main`.
- Inside a session: implementer → reviewer (fresh context, full diff vs main) → fix → reviewer again, until the reviewer returns zero REQUIRED. Hard cap: 5 review cycles; if not converged, stop and report the unresolved items instead of grinding.
- Commit messages are written by the human. Claude stages changes only.
- `NOTES.md` is updated whenever a design decision is made mid-phase.
- When PLAN.md is ambiguous, ask the human; do not decide silently.
