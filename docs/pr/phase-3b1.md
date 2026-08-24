## What was built

**Phase 3b-1** — the ratified channel collapse implemented, two human-confirmed tables with
guarded read paths, detection run end to end on 12 real crops for the first time, and a
polarity blocker found by a read-only diagnostic one step before the number. **Gate 1 ruling 3
governs the whole phase**: a real blot may falsify a decision, never select a parameter or a
code path. No shipped parameter moved; `configs/`, `synth/` and `data/ground_truth/` are
untouched.

**The phase did not produce N, and N is not small — it is unknown.** That distinction is the
phase's main result and is set out under "The polarity blocker" below.

### 1. W4 — the ruled channel collapse (closes DEBT S19)

The 2026-08-19 §7 amendment is implemented as ruled: a 3-channel 8-bit input whose maximum
per-pixel channel divergence is byte-identical or at or below **2 DN** collapses to its **green**
plane; anything above is refused with a message naming the measured divergence and the bound. The
bound and the method are module constants, not config keys, on the amendment's own authority —
the bound was named before the divergence table was measured, and a value in `configs/` is a
value somebody may tune.

A 16-bit multi-channel input is refused rather than collapsed. Not ruled anywhere; an implementer
decision, recorded in NOTES.md, because 2 DN against a 16-bit full scale is 0.003% rather than
0.8% and extrapolating a human ruling into a domain the human never saw is not the implementer's
to do. Nothing in the real set is 16-bit.

Tested on both sides of the bound with synthetic fixtures — 0, 1 and 2 DN collapse; 3 and 255 DN
refuse — and as a regression over the real set: the 6 excluded crops and E-TIGAR are still
refused. **The schema goes to 1.3.0** for `source.channel_collapse` and
`normalization.reference_designation_source`; both `source` and `normalization` are
`additionalProperties: false`, so a 1.2.0 validator rejects a 1.3.0 document outright and leaving
the version alone would have declared a contract the documents no longer satisfy.

### 2. W5 and W6 — two tables, confirmed at the human gate

`data/real/designations.csv` (12 rows, DEBT D4) and `data/real/blot_identity.csv` (12 rows,
DEBT D5) were built with every row pending and confirmed at the gate of 2026-08-20 by rulings G1
and G2. One row was **corrected rather than confirmed**: `PMC13135410_Figure3__B` carries two
targets — Fibronectin and CCN2 — against one shared GAPDH reference, which the parser had read as
one hyphenated target. The ambiguity flag the implementer set on that row is what sent a human to
look at it.

**The discipline lives in the read path, not the write path**, because that is where a default
would be invisible. `confirmed_reference_label`, `confirmed_blot_id` and `contributes_ratios`
raise for a pending row, a blank cell, a crop with no row, and — the direction that matters — a
source value they do not recognise. All three test membership of an allow-list of confirmations
rather than non-membership of the pending set: a reader written as "not pending, therefore
confirmed" turns a typo into a measurement.

### 3. W7 pass 1 — detection only, and the gate it stops at

`tools/phase3/run_real.py --detection-only` reads both tables through those guarded read paths
rather than from any crop_log column, and demands every ruling before the first crop runs, so an
unruled table leaves nothing written. **12 of 12 crops produced a result document; 430 bands were
detected across 106 lanes**, and each band has one pending row in
`runs/3b1/band_mapping_pending.csv`. No crop errored.

The run stops there. Which detected band carries each confirmed reference label is **a third
human gate of the same class as G1 and G2** — §2 forbids inferring the loading control from the
data, and picking a band by vertical position is that inference. `tools/phase3/band_mapping.py`
holds the read path for it, and it refuses every crop in the tree today, which is the correct
answer before the gate is held.

### 4. The polarity blocker, and why N is unknown

Before "published figures are saturated as a class" could enter the record, a **read-only**
diagnostic established whether the flags describe the corpus or the measurement. They describe
the measurement.

All twelve crops are white-ground published figures whose bands are *darker* than their
background: **every median is 249 or higher against a full scale of 255**, and between **37.7%
and 54.5%** of each image sits at exactly full scale. Detection finds maxima and defines clipping
as pixels at full scale, so the extreme it tests for is the paper. **286 of 430 bands carry
`saturated`, and exactly 286 contain a full-scale pixel inside their own ROI** — the flag is
computed on the band's own rectangle and there is no image-to-band path — with the median
`saturated` band **39.3%** of its rectangle at full scale (range 0.2%–96.5%). The extreme a
genuinely saturated *dark* band would produce, a pixel at 0, occurs in **8 of 430** bands.
`low_dynamic_range` reads the same fact from the other end and fires on 7 of 12.

Every rule the diagnostic quotes is extracted from `pipeline/qc.py` and `configs/default.yaml` at
report time rather than transcribed, and G2's lane count is read out of the identity table's
header. `runs/3b1/QC_DIAGNOSTIC.md`; `runs/` is gitignored, so the figures above are reproducible
from the committed tooling rather than stored.

**`runs/3b1/RATIO_BOUND.md` is withdrawn as evidence about N (human ruling R2).** It counted
lanes with enough *unflagged* bands to pair; its arithmetic is correct and its input is the flags
just characterised. **So N is unknown rather than small, and no stop-rule branch is selected by
this phase.** A small N would select the pre-registered descriptive-only outcome; an unknown N
selects nothing. The file is kept, carrying that withdrawal at its head — emitted by the
generator, so a re-run reproduces it rather than erasing it — because a measurement correctly
computed and wrongly premised is the evidence for why the premise was wrong.

### 5. What the rulings changed in the register

- **S19 closed** — the collapse is implemented and tested on both sides of the bound.
- **S20 added** — `lossy_format` is structurally unreachable on this corpus: all 12 crops are
  PNG and all 19 parents are `.jpg`, so §6's expected finding cannot be evidenced by the flag.
  Promoted from a Phase 3b-0 draft by ruling R3 of 2026-08-20.
- **S14 gains its first real-data evidence and its first ruling** (R3 and R4 of 2026-08-24).
  The sentence that stood in it — *"there is no measurement of polarity because no inverted image
  exists to test"* — is superseded. **Polarity becomes a declared caller input, never inferred**:
  an image whose polarity is not declared is refused rather than guessed. Auto-detection was
  considered and forbidden, because a light-versus-dark test would be a threshold chosen against
  real data. That puts polarity in the same class as the reference designation (S6) and blot
  identity (G2). **Nothing is implemented under R4 in this phase**, by the ruling's own terms;
  S14 stays `Open`, now ruled on polarity and still unruled on tilt.
- **P2 gains three entries** — (9) the `--reference-designation-source` flag, which PLAN.md's
  Phase 1 CLI contract does not have; (10) the MVP-scope widening the loader's 3-channel
  admission represents, since PLAN.md's "Scope (MVP)" says single-channel grayscale and the §7
  amendment amends `DECISION_unit_of_analysis.md`, not PLAN.md; and (11) the review cap
  extension, under "Review record" below.

## Review record

**Seven cycles, against PLAN.md's cap of five**, and the eighth replaced rather than run.
Cycle-by-cycle REQUIRED counts: 8, 4, 3, 1, 2, 12, 7 — **37 REQUIRED across the seven Phase 3b-1
cycles; 18 in the five Phase 3b-1 cycles inside PLAN.md's cap.** No cycle returned zero.

All seven verdicts are committed verbatim in **`docs/review/phase-3b1/`**, extracted from the
session log and byte-compared against it after writing. Six session logs were searched with the
same filter; exactly one holds Phase 3b-1 cycles, and all seven were found. This closes a hole
the review itself found: DEBT P2 entry (11) had reproduced entry (8)'s premise "with equal
specificity and none of the evidence", and four figures were withdrawn for want of an artefact.
Three are restored against the committed record. **The fourth stays withdrawn** — it read that a
mutation "failed the build from four independent directions in cycle 4 and six in cycle 5", and
the artefact shows both numbers belong to cycle 5, so restoring it would have reintroduced an
error rather than an unevidenced claim.

**Why the eighth cycle was not run.** Cycles 5, 6 and 7 were almost entirely claim accuracy, and
the fixes were generating claim defects at roughly one per fix: of cycle 7's seven findings, four
were introduced by cycle-6 fixes. The worst was not a stale sentence but a fabricated one — the
repair for "NOTES.md still says W7 was not run" deleted a true sentence and replaced it with a
claim that W7 had run and that its results were recorded in a section that does not exist, in the
same diff that withdrew figures from DEBT P2 on the principle that a claim with no artefact is a
stale claim. The human ruled the eighth cycle would not converge and replaced it with a direct
prose review plus a **record-edit freeze** enforced by snapshot digests over NOTES.md, DEBT.md,
README.md and `docs/`. Every record edit since has been listed and diffed against that snapshot.

**The behavioural half converged and is the strongest part of the phase.** Every cycle was
mutation-tested. The bound-widening mutation — admitting 3 DN where the amendment bounds the
collapse at 2 — appears in all seven and bit every time, bringing down 15 tests by cycle 7 across
the loader, the real-set regression, the schema pin and both committed tables. No mutation
survived in any cycle.

## Deviations and disclosures

- **Seven review cycles against a cap of five**, with the eighth replaced by a human prose review
  and a mechanical freeze. Recorded as DEBT P2 entry (11), which now cites `docs/review/phase-3b1/`.
- **A three-value `source_of_designation` vocabulary** where the task text specified one. Reported
  as a deviation and **ratified by R4 of 2026-08-20** as a dated correction to the task text: the
  ambiguity flag is what sent a human to the one row that was wrong.
- **`reference_strip_confirmed_human` and `NO_RATIO_SOURCES` were an implementer invention**,
  flagged as one. **Ratified 2026-08-24** as a dated correction to G1: collapsing "a human ruled
  here" into "measure this" would make a ruled-and-excluded row indistinguishable from an unruled
  one at the moment N is counted.
- **`blot_id` went into a sibling file, not `crop_log.csv`.** §9 of the frozen pre-registration
  provides for the column in the crop record and DEBT draft D5 asks for it there. The conflict is
  **reported, not resolved** — R2 of 2026-08-20 ruled the sibling file the mechanism, and the
  frozen Gate 2 record is untouched.
- **`molecular_weight_confirmed_human` is an implementer proposal, not a ruling.** The band-mapping
  table needs a word for a confirmation and the human has not named one. Flagged in the module and
  in this list.
- **The band-mapping table has no column for the label the human will name.** The task fixed eight
  columns; adding a ninth would be the implementer shaping the table at the moment the human is
  about to rule on its contents. Two shapes work and the choice is reported, not taken.
- **The band-mapping gate is deferred, not cancelled** (R5). The band set it would map changes
  once polarity is handled, so every mapping ruled against today's band ids would have to be made
  again. Nothing about the gate is retracted and its read path stays in the tree.
- **No new dependency.** `requirements.txt` and `package.json` are unchanged on this branch.
- **`tools/check_claims.py` was widened, not weakened.** It now globs `docs/review/*/` so a later
  phase's record is covered by adding files rather than editing a list, and `Quantity` gained a
  `point_in_time` flag so that a dated reviewer baseline inside a verbatim extract is not read as
  a competing claim about today — the same distinction the P2 counts already drew between a frozen
  historical delta and a running total.

## What this phase did not produce

**N.** Neither a value nor a bound. The ratio bound that existed is withdrawn (R2), the band
mapping that would have made ratios possible is deferred (R5), and no Spearman, Bland-Altman or
other agreement statistic was computed at any point — there is no ImageJ side yet, and producing
half a comparison and a statistic over it is this phase's one serious failure mode. **No stop-rule
branch is selected.**

## Open questions for the human

1. **Which phase implements R4**, and whether the declared polarity input is a CLI flag, a config
   key, or a per-image field in a caller-supplied manifest. R4 rules the *what*; the *where* is
   unruled.
2. **Whether D13 and D14 are the last of it.** D13 is promoted into S14 and D14 narrows D10, but
   the remaining drafts — D8 (13 lanes detected where G2 counted 12), D10, D11, D12 — all wait on
   the same thing: a human count of true lanes and true bands per panel from the figures.
3. **The band-mapping vocabulary word**, when the gate is un-deferred.
4. **Whether `runs/` should stay gitignored** for this class of artefact. The QC diagnostic is the
   most consequential measurement this phase made and it lives in an ignored directory,
   reproducible only by re-running the committed tooling.

## Verification

`ruff check .` exit 0. `pytest` exit 0, 891 tests collected. `tools/check_claims.py` exit 0 over
24 files. Every figure in this body was read from an artefact produced in the session that wrote
it.
