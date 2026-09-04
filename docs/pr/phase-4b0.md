## What was built

[Sofia to write]

A read-only probe of the gallery's composition, and the vocabulary the gallery
reads. The probe found that `pass`/`flagged`/`blocked` existed nowhere in the
code — a gap between PLAN.md's planned "QC badges" and the implementation, not
between README and the implementation. Ruling 1 of 2026-08-25 fixed the mapping
as a display-layer derivation; it was written as a dated pre-registration with
its predictions fixed **before** any measurement was taken with it, then
implemented in `api/display.py` with tests, then measured. Every prediction
held. Two things the pre-registration did not name moved, and both are recorded
as findings rather than absorbed: `image_qc_flags` is scope-dependent, and the
one pass lane in the crop the gallery ruling names is the figure's
molecular-weight label column.

## Scope and what governed it

**Vocabulary and measurement only.** No parameter moves, no threshold is chosen,
no pipeline behaviour changes. Gate 1 ruling 3 governs throughout, and is the
reason Ruling 3 refused to make image-level saturation a blocking cause after
the corpus had been seen to carry the flag on every crop.

| boundary | state on this branch |
|---|---|
| `configs/` | untouched |
| `synth/` | untouched |
| `data/ground_truth/` | untouched |
| `data/` | untouched |
| `pipeline/` | untouched |
| `evals/` | untouched |
| `schema/` | untouched |
| `pipeline/` importing `synth/` | none |
| test split | not read, not tuned on |
| new dependencies | none |

## The rulings this phase implements

| ruling | date | what it fixed |
|---|---|---|
| 1 | 2026-08-25 | the verdict is a display-layer derivation over a stored document, computed in `api/display.py`, recoverable from the document alone |
| 2 | 2026-08-25 | a gallery card is one caller-supplied ROI with its verdict; the unit is the lane |
| 3 | 2026-08-25 | image-level saturation does not become a blocking cause |
| gallery composition | 2026-08-25 | the cards, assigned by image after the renderings were inspected |
| display of every ratio | 2026-08-25 | a card shows every ratio its ROI produced, including the one-pixel detection; no parameter moves to suppress it |
| gallery order | 2026-08-25 | blocked first and by default, then flagged, then detection beta, then pass |

## The probe (`runs/4b0/PROBE.md`)

Read-only, measured on tree `880fd4786149735de986c4479b54d76d1a831a60`. The
stored `runs/3b2/` outputs were verified reproducible at that tree by re-running
every crop through the documented CLI and comparing field by field, rather than
trusting their timestamps.

### Propagation, read from code

| image flag | effect on band outcome | effect on lane outcome |
|---|---|---|
| `saturated` | none | none |
| `lossy_format` | none | none; one result-level warning when a ratio exists |
| `low_dynamic_range` | none | none — no consumer anywhere in `pipeline/` or `api/` |

`report.image_flags` is read at exactly one site, `pipeline/analyze.py:282`,
where it is written into the document. No decision reads it. The only mechanism
that blocks a whole lane is `_LaneProblem` (`pipeline/normalize.py:495-515`),
whose two causes are a missing reference band and a non-positive denominator —
neither of them a QC flag.

### Verdict census, 12 crops

| quantity | value |
|---|---|
| crops measured | 12 |
| crops in the approved crop log | 19 |
| lanes | 116 |
| bands | 420 |
| bands carrying at least one flag | 238 |
| lanes `pass` | 15 |
| lanes `flagged` | 77 |
| lanes `blocked` | 24 |
| crops carrying `image_qc_flags: ["saturated"]` | 12 |
| crops carrying `low_dynamic_range` or `lossy_format` | 0 |

## The pre-registration, and the predictions it fixed

`docs/PRE_REGISTRATION_2026-08-25_verdict_mapping.md`, RATIFIED 2026-08-25,
digest pinned in `tools/check_claims.py` and frozen from that pin under the
amendment freeze rule. It carries the rule, edge cases E1–E8, and four
predictions written before the measurement.

| prediction | outcome |
|---|---|
| P1 — the ruled mapping reproduces the probe census exactly | held: 15 / 77 / 24 over 116 lanes |
| P2 — each candidate reproduces its detected-path measurement under the caller path | held on 9 of 9 candidates |
| P3 — ids and `result_id` change, `roi_source` becomes `caller`; not a P2 failure | held on 9 of 9 |
| P4 — no verdict class empty; pass non-empty while no image is flag-free | held |

One place the document extends the ruled words rather than applying them, and
says so at the point of extension: a lane emitting no ratio at all has no number
but nothing was excluded, so it is `blocked` with
`blocked_reason: no_ratio_emitted` rather than a fourth class.

## The implementation

`api.display.lane_verdicts` — one pure function, one document in, verdicts out.

| constraint from Ruling 1 | how it is kept |
|---|---|
| adds no field to the measurement record | `schema/result.schema.json` untouched; verdict derived on demand |
| no code path in `pipeline/` | `pipeline/` untouched; no import from it into the derivation beyond the flag vocabulary |
| recoverable from a stored document alone | reads no pixels, no config object, no file |
| does not read `image_qc_flags` | pinned by `test_e8_no_image_flag_can_change_any_verdict` |

| test group | functions | collected cases |
|---|---:|---:|
| the three ruled classes | 3 | 3 |
| closed-vocabulary check | 1 | 1 |
| mutation tests | 2 | 2 |
| edge cases E1–E8 | 11 | 16 |
| shape, ordering, record | 6 | 6 |
| **total added** | **23** | **28** |

Cases exceed functions because two edge-case tests are parametrised: the
missing-top-level-key check over three keys, and the image-flag invariance check
over four flag sets.

## The caller-ROI measurement (`runs/4b0/CALLER_ROI_MEASUREMENT.md`)

Each candidate lane's detected rectangle re-submitted as the sole
caller-supplied lane ROI — Ruling 2's unit, one card, one ROI.

| card | crop | lane | verdict | blocked_reason | flags | bands | ratios | usable |
|---|---|---|---|---|---|---:|---:|---:|
| blocked | `PMC12895598_Fig3__A-EMT-GAPDH` | `L3` | blocked | all_ratios_excluded | saturated | 5 | 5 | 0 |
| blocked | `PMC12895598_Fig3__A-EMT-GAPDH` | `L2` | blocked | all_ratios_excluded | saturated | 4 | 4 | 0 |
| blocked | `PMC12895598_Fig3__A-EMT-GAPDH` | `L4` | blocked | all_ratios_excluded | saturated | 4 | 4 | 0 |
| flagged | `PMC13025488_Figure5__C-pSMAD-GAPDH` | `L7` | flagged | — | saturated, overlapping, unresolved_shoulder | 7 | 7 | 3 |
| flagged | `PMC12686555_FIGURE1__A-p16p21-ACTIN` | `L3` | flagged | — | saturated, overlapping, unresolved_shoulder | 6 | 6 | 2 |
| flagged | `PMC13135410_Figure3__B-Fib-CCN2-GAPDH` | `L11` | flagged | — | saturated, overlapping, unresolved_shoulder | 6 | 6 | 2 |
| pass | `PMC12895598_Fig3__A-EMT-GAPDH` | `L5` | pass | — | none | 5 | 5 | 5 |
| pass | `PMC12956003_Figure2__A-Htt-tub` | `L2` | pass | — | none | 4 | 4 | 4 |
| pass | `PMC13135410_Figure4__C-PDGFRa-GAPDH` | `L3` | pass | — | none | 3 | 3 | 3 |

## Two findings the pre-registration did not name

[Sofia to write]

### `image_qc_flags` is scope-dependent — DEBT S25

The field is set from the bands a run detected, so a single-ROI run reports the
flags of that ROI, not of the figure it was cut from.

| candidate ROI | whole crop | that ROI alone | saturated bands in ROI |
|---|---|---|---:|
| `PMC12895598_Fig3__A-EMT-GAPDH` `L5` | `["saturated"]` | `[]` | 0 |
| `PMC12956003_Figure2__A-Htt-tub` `L2` | `["saturated"]` | `[]` | 0 |
| `PMC13135410_Figure4__C-PDGFRa-GAPDH` `L3` | `["saturated"]` | `[]` | 0 |
| the six blocked and flagged candidates | `["saturated"]` | `["saturated"]` | 1 to 5 |

The name asserts a property of the image; the value reports a property of the
run. Recorded as S25 with the closing condition stated as a naming or scoping
decision, not a code fix, because a rename touches the schema and the record.
No verdict is affected.

### The pass lane of the gallery's ruled crop is its MW label column

| evidence | value |
|---|---|
| crop width | 218 px |
| `L5` x-range | 180..210 |
| `L5` band x-positions | 186..210 |
| `L5` bands | 5 |
| printed MW labels in the panel | 5 |
| `L5` intensity spread, max/min | 1.31 |
| `L3` intensity spread, max/min, a real lane in the same crop | 3.28 |

The verdict is correct and the lane is not a measurement. The blocked card is
unaffected. Alternatives that are genuine sample lanes are tabulated in
`runs/4b0/CALLER_ROI_MEASUREMENT.md` §5 and rendered in `runs/4b0/gallery/`.

## Register re-evidence — two existing entries, no new entry

[Sofia to write]

Both were found the same way: by drawing the candidate ROIs on the crops and
looking. Neither is visible in any field of a result document, and neither
prompted a code change or a parameter move.

### S23 re-evidenced — the detector merges adjacent sample lanes

Distinct from the two classes S23 already carries — more lanes than the human
counted, and lanes that are not lanes — because it runs the other way. The
detector under-segments and over-segments in the same run on the same corpus.

Sample lanes counted from each figure's own content, not from detection: the
printed `siControl` `+`/`-` row on the pSMAD panel, the ACTIN loading-control
row on the p16p21 panel.

| crop | sample lanes | ROIs detected | ROIs with no sample lane | ROIs with two or more |
|---|---:|---:|---:|---:|
| `PMC13025488_Figure5__C-pSMAD-GAPDH` | 12 | 10 | 3 | 3 |
| `PMC12686555_FIGURE1__A-p16p21-ACTIN` | 6 | 5 | 1 | 2 |

A lane ROI is what a `total_protein` denominator integrates over, so a merged
ROI sums two samples into one denominator and reports it as one lane's
measurement — a wrong number, not a wrong caption, and invisible in every field
of the result document. No entry added, no parameter moved, no code changed.

### S22 re-evidenced — over-detection reached the demonstration crops

Not a new count. Two instances, both on crops chosen *because* they looked like
good demonstrations, both found by drawing the ROIs on the images.

| instance | crop | ROI | what was called a band | panel bands | detected |
|---|---|---|---|---:|---:|
| printed MW column | `PMC12895598_Fig3__A-EMT-GAPDH` | `L5` | five printed MW labels | 0 | 5 |
| caption fragment | `PMC13135410_Figure4__C-PDGFRa-GAPDH` | `L8` | a slice of the printed caption | 2 | 3 |

What this adds to the counts already in S22: **nothing anywhere in the system
asks whether a detected region is a band at all.** Every flag in the QC
vocabulary qualifies a measurement, and printed type measured cleanly is a clean
measurement of printed type. Neither instance carries a flag, an exclusion or a
warning; neither is visible in any field of the result document. That is the
argument for the beta label made on the gallery rather than on a count.

## Gallery composition, ruled and confirmed by image

Ruled 2026-08-25 after the renderings under `runs/4b0/gallery/` were inspected.
Four cards, not three: the fourth is the detection defect the probe surfaced,
shipped as itself rather than suppressed.

| order | card | crop | lane | verdict | why this one |
|---:|---|---|---|---|---|
| 1 | blocked | `PMC12895598_Fig3__A-EMT-GAPDH` | `L2`, `L3`, `L4` | blocked | real sample lanes, saturated, correctly refused |
| 2 | flagged | `PMC13135410_Figure3__B-Fib-CCN2-GAPDH` | `L11` | flagged | denervated `M4`; the ROI holds one sample lane |
| 3 | detection beta | `PMC12895598_Fig3__A-EMT-GAPDH` | `L5` | pass | the printed molecular-weight column, read as five bands and returned `pass` |
| 4 | pass | `PMC13135410_Figure4__C-PDGFRa-GAPDH` | `L8` | pass | denervated `M2`, both bands plainly visible |

**Order is ruled, and blocked is the default card.** Pass sits after the
detection-beta card so that its third ratio (below) reads as the disclosed limit
it is rather than as an unexplained number.

The pass card's lane was chosen on a presentation ground and the ruling says so:
`L3` is equally a real lane and equally `pass`, and `L8` was taken because a card
must be legible. That is not a measurement reason and is not recorded as one.

Two flagged candidates were rejected because their ROIs each span two sample
lanes, which the renderings show and which S23 now carries as evidence:
`PMC13025488_Figure5__C-pSMAD-GAPDH` `L7` and
`PMC12686555_FIGURE1__A-p16p21-ACTIN` `L3`. Pass and flagged therefore come from
two figures of one article, `PMC13135410`; each card names its figure.

| card | article | figure |
|---|---|---|
| blocked | PMC12895598 · 10.1186/s12931-026-03506-8 | Fig. 3, panel A-EMT-GAPDH |
| pass | PMC13135410 · 10.1172/jci.insight.198388 | Figure 4, panel C-PDGFRa-GAPDH |
| flagged | PMC13135410 · 10.1172/jci.insight.198388 | Figure 3, panel B-Fib-CCN2-GAPDH |
| detection beta | PMC12895598 · 10.1186/s12931-026-03506-8 | Fig. 3, panel A-EMT-GAPDH |

All four are recorded `cc by` in `data/real/sources.csv`, pending the licence gate.

### What the cards display — ruled

> *"Show every ratio the tool produced, including L8_B0's 0.011145 from the
> one-pixel detection in the caption band. Nothing the tool computed is hidden
> from the card; that is the same rule that governs flagged bands in the record,
> applied to the display. No parameter moves to suppress it. The card names what
> the third ratio came from."* — ruled 2026-08-25.

The record's rule is that a flagged band is reported with its flags and its
exclusion, never omitted. The display inherits it: a card shows every ratio its
ROI produced.

| card | rows the panel has | bands detected in the ROI | ratios the card shows |
|---|---:|---:|---:|
| flagged — `PMC13135410_Figure3__B` `L11` | 3 (Fibronectin, CCN2, GAPDH) | 6 | 6 |
| detection beta — `PMC12895598_Fig3__A` `L5` | 0 (printed MW labels) | 5 | 5 |
| pass — `PMC13135410_Figure4__C` `L8` | 2 (PDGFRα, GAPDH) | 3 | 3 |

The pass card's third ratio is `L8_B0`: a region **1 pixel wide** and 11 tall at
`x=281`, in the `2 weeks denervated` caption band above the blot. It carries no
QC flag, is not excluded, and emits `0.011145` beside the two real ones,
`0.283942` and `0.303642`. The card names where it came from.

No parameter moved to suppress it, and none may: a minimum band width or a margin
exclusion introduced here would be a parameter chosen against a real crop that
looked wrong (Gate 1 ruling 3). Both instances are recorded as re-evidence on
S22 instead.

## Register movement

| entry | what Phase 4b-0 did to it |
|---|---|
| **S25** | added — `image_qc_flags` is scope-dependent |
| **S23** | re-evidenced — a third detection defect class: adjacent sample lanes merged into one ROI |
| **S22** | re-evidenced — over-detection reached the chosen demonstration crops |

| quantity | before | after |
|---|---|---|
| entries | 40 | 41 |
| `Open` | 23 | 24 |
| `Accepted` or `Permanent` | 17 | 17 |

**Closed by this phase: none.**

## Proposal — `DISPLAY_BLOCK_KEYS` and a response-only field

Ruled 2026-08-25: the verdict is computed at response time and never stored; a
document written before today must stay valid and must still produce a verdict.
Not implemented; awaiting a ruling.

`DISPLAY_BLOCK_KEYS` is the labelling contract for the block that is **written
to disk**, and `_require_labelled_display` checks a stored block against it on
every `GET`. A response-only field must therefore not enter it.

| option | what it does | cost |
|---|---|---|
| A — leave `DISPLAY_BLOCK_KEYS` as the stored contract; add the verdict in `_envelope` only | the stored record keeps exactly today's keys; the verdict is assembled per response from the document | one more thing `_envelope` does; the constant's name no longer describes the whole response |
| B — extend `DISPLAY_BLOCK_KEYS` and store the verdict | one contract | breaks the ruling: the verdict becomes stored, and every record written before today fails the labelling check |
| C — add a second constant, `RESPONSE_ONLY_KEYS`, checked against the response and never against the stored block | both contracts stated explicitly, neither confused for the other | one more constant and one more check to keep aligned |

**Recommendation: C.** A is correct and silent — the split between what is
stored and what is served would live only in `_envelope`'s body, which is where
this service has already been bitten once by a labelling rule that no schema
covered. C states the split as data, so a later reader cannot mistake a
response-only key for a missing stored one, and the `GET` path keeps checking
the stored block against the stored contract unchanged.

**One-line consequence for old records:** none — under A or C no stored record
is read differently, and every document written before today produces a verdict
on read, because the derivation reads only fields
`schema/result.schema.json` already required.

## Open questions for the human

- The `DISPLAY_BLOCK_KEYS` proposal above awaits a ruling before implementation.
- The fourth card ships a detection defect as a labelled example. What "detection
  beta" says on the card, and whether the label sits on that card alone or on
  every card, is a presentation decision that is not made here.
- S25's closing condition is a naming-or-scoping decision on `image_qc_flags`
  that touches the schema; it is not scheduled.
- The pre-registration is pinned in `RATIFIED_AMENDMENTS` but is **not** in
  `SCANNED`, so its prose numbers are frozen but not claim-checked. Whether
  frozen documents should also be scanned is a decision about the checking
  surface.
- Whether the verdict, once wired, is served on `POST` only or on both
  endpoints.

## Verification

`ruff check .` exit 0. `pytest` exit 0. `python -m tools.check_claims` exit 0, with the
prose-count advisories report-only. `python -m evals.sweep --check` exit 0. Every figure in
this body is a table row read from an artefact.

The suite total is deliberately not quoted here. `check_claims` tracks "test count on this
branch" as one repo-wide quantity, so a per-branch total collides with the one Phase 4a
recorded; `docs/pr/phase-3b2.md` omits it for the same reason. The count this phase is
answerable for is the tests it added, which is the table above.
