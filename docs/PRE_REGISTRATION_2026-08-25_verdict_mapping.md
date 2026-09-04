# Pre-registration 2026-08-25 — the display-layer verdict mapping

**Status: RATIFIED 2026-08-25 by the human gate. In force.**

**Ratification, executed 2026-08-25.** Status flipped, sha256 computed over the flipped file, and
the digest pinned in `tools/check_claims.py` (`RATIFIED_AMENDMENTS`) so that an edit to this file
fails the build the way an edit to the Gate 2 pre-registration does. The path is unchanged.

Ratified **after** the measurement it pre-registers had been taken and reported
(`runs/4b0/CALLER_ROI_MEASUREMENT.md`), which is the correct order only because the predictions in
§4 were fixed and staged *before* that measurement ran and are unaltered by this flip: the bytes
of §4 are the bytes that were staged as a draft. Nothing in §1–§6 changed at ratification except
this status block.

**Frozen from this pin.** Under the amendment freeze rule ruled 2026-08-25 — *"An amendment's
bytes freeze when its digest is first pinned, committed or not. After that, corrections go to
NOTES — except an internal contradiction, which is fixed at source."* — this document's bytes are
frozen as of the pin below. A change to any ruling here is a further pre-registration with its own
date and digest, not a revision of this one.

Written on **Ruling 1 of 2026-08-25**, which is quoted verbatim in §1 below, and **before any
measurement was taken with the mapping it defines**. That ordering is the point of the document:
§4 fixes what the mapping will do to the corpus in advance, so that a number which comes out
differently is a finding rather than a re-record.

This document rules on **vocabulary and its derivation**. It moves no parameter, adds no field to
any measurement record, and changes no code path in `pipeline/`.

---

## 0. Correction to the record this document supersedes

`runs/4b0/PROBE.md` reported that no `pass`/`flagged`/`blocked` vocabulary exists anywhere in the
codebase. That finding stands. The framing around it — that "the premise doesn't hold" — did not,
and is corrected here as it has been corrected in the probe itself.

**README does not promise a pass/flagged/blocked verdict.** What README promises is that a
QC-flagged band is excluded from its ratio with a recorded reason, and that promise is kept in the
shipped code: `README.md:225-226` shows `"excluded_from_normalization": true` beside
`"exclusion_reason": "carries QC flags: saturated"`, and `README.md:232` shows the same pair on the
ratio. Both are emitted by `pipeline/normalize.py:537-541` and `pipeline/analyze.py:124-127`.

**The gap was between the planned UI vocabulary and the code, not between README and the code.**
`PLAN.md` Phase 4 specifies a "results table with QC badges" and never wrote the badge vocabulary
down. This document writes it down. Nothing in the measurement record was wrong, and nothing in it
changes.

---

## 1. The ruling

Quoted verbatim, as ruled:

> "pass / flagged / blocked is a display-layer derivation over an existing result document,
> computed in api/display.py. It adds no field to the measurement record and no code path in
> pipeline/. pass = no QC flags and every ratio computed; flagged = QC flags present, number
> reported and annotated; blocked = no number, because the input to it was excluded. A verdict
> must be fully recoverable from a stored result document alone.
> Ruled by Sofia, 2026-08-25."

Two further rulings of the same date bound what this document may say:

> "A gallery card is one caller-supplied ROI with its verdict. The corpus has no clean image and
> that is a property of published-figure data, not a defect to route around; it has clean lanes,
> and a lane is what v1.0 measures. Ruled by Sofia, 2026-08-25."

> "image-level saturation does not become a blocking cause. Building that after seeing that every
> crop carries the flag would select a code path against real data, which Gate 1 ruling 3 forbids.
> The blocked card comes from behaviour that already exists: a saturated band is excluded from its
> ratio, so the ratio has no number. Ruled by Sofia, 2026-08-25."

### What each ruling fixes

* **The unit is the lane.** Ruling 2 makes a card one caller-supplied ROI, and a caller-supplied
  ROI is a lane (`pipeline/detect.py:391`, `roi_source: "caller"`). So the verdict is computed
  **per lane**. There is no image-level verdict and this document does not define one.
* **The inputs are the document.** Ruling 1's recoverability clause makes the derivation a pure
  function of one stored result document. It may not read pixels, config objects, the filesystem,
  or anything the document does not carry.
* **No new causation.** Ruling 3 forbids image-level saturation acquiring a blocking role it does
  not have. §2 therefore reads `image_qc_flags` **not at all**.

---

## 2. The mapping

### 2.1 The three inputs, all from the stored document

For a lane `L` in `result.lanes`:

| name | definition | document fields read |
|---|---|---|
| `bands(L)` | the lane's bands | `result.bands[]` where `lane_id == L` |
| `ratios(L)` | the lane's ratios | `result.normalization.ratios[]` where `lane_id == L` |
| `numbers(L)` | the ratios that carry a usable number | `ratios(L)` where `excluded` is `false` |
| `flags(L)` | every QC flag attaching to the lane | union of `bands(L)[].qc_flags`, `ratios(L)[].qc_flags`, `ratios(L)[].reference_qc_flags` |

`result.image_qc_flags` is **not** an input. That is Ruling 3 expressed as a data dependency: an
image flag cannot change a lane verdict, because if it could, image-level saturation would have
acquired exactly the blocking role Ruling 3 refuses it.

### 2.2 The rule

Evaluated in this order:

| # | condition | verdict |
|---|---|---|
| 1 | `numbers(L)` is empty | **blocked** |
| 2 | otherwise, `flags(L)` is empty | **pass** |
| 3 | otherwise | **flagged** |

Read back against the ruled words:

* *"pass = no QC flags and every ratio computed"* — rule 2 fires only when no flag attaches to the
  lane. Under `exclude_qc_flagged: true` a ratio is excluded only for flags it or its denominator
  carries (`pipeline/normalize.py:537-541`), so "no flags" implies "every ratio computed"; the
  one way to have no flags and an excluded ratio is a lane-level problem, which empties
  `numbers(L)` and is caught by rule 1 first. The two halves of the ruled sentence cannot come
  apart, and this is the reason why.
* *"flagged = QC flags present, number reported and annotated"* — rule 3: at least one number
  survives, and it is annotated because `ratios[].qc_flags` and `ratios[].reference_qc_flags`
  travel on it already.
* *"blocked = no number, because the input to it was excluded"* — rule 1. See E1 for the one case
  where the ruled `because` clause does not hold and what is done about it.

### 2.3 What a verdict carries

Three classes and no fourth, because three were ruled. A blocked verdict additionally records
**why** there is no number, so that the E1/E2 distinction is visible in the data rather than
buried in this prose:

| `blocked_reason` | meaning |
|---|---|
| `all_ratios_excluded` | the lane emitted ratios and every one is `excluded: true` — the ruled case |
| `no_ratio_emitted` | the lane emitted no ratio at all (E1) |

`pass` and `flagged` carry no reason. The counts the verdict was decided on — bands, ratios,
usable ratios, and the sorted flag names — travel beside it, on the same discipline
`pipeline/qc.py`'s `BandQc` follows: the observation is reported next to the decision so a reader
can re-apply any rule without re-deriving it.

---

## 3. Edge cases, ruled in advance

Each of these is a case the ruled sentence does not decide on its own. Each is decided here,
before measurement, with its reasoning.

**E1 — a lane that emits no ratio at all.** A lane with no detected bands, or (under a housekeeping
mode) a lane whose only bands are its designated references, emits no ratio. There is no number,
but nothing was excluded, so the ruled `because` clause does not apply.
**Ruled: blocked, with `blocked_reason: no_ratio_emitted`.** A card's promise is a number or an
explanation of its absence, and this lane has no number. Introducing a fourth class would invent
vocabulary that was not ruled; recording the reason keeps the distinction without inventing one.
**This is the single place where this document extends the ruled words rather than applying them,
and it is flagged as such for the gate.**

**E2 — a lane blocked by a lane-level problem.** `pipeline/normalize.py:495-515` marks every band
in the lane excluded and emits no ratio when the lane has no designated reference
(`lane_without_reference_band`) or its denominator measured non-positive
(`lane_denominator_not_positive`). `numbers(L)` is empty. **Ruled: blocked, with
`blocked_reason: all_ratios_excluded`** when ratios exist, otherwise E1's reason. Note that
neither cause is a QC flag — this is a pre-existing behaviour the vocabulary describes, not a new
one it creates, which is what Ruling 3 requires.

**E3 — `exclude_qc_flagged: false`.** Flagged bands are not excluded
(`pipeline/normalize.py:484-486`), so flagged lanes keep every number. `flags(L)` is non-empty and
`numbers(L)` is non-empty. **Ruled: flagged.** This is the ruled sentence working as written —
"number reported and annotated" is exactly this configuration's output — and it means the verdict
is a function of the document including its `normalization.exclude_qc_flagged`, which the document
carries. No override can silently turn a flagged lane into a pass one.

**E4 — a flagged reference band under a housekeeping mode.** The reference is never excluded
(`pipeline/normalize.py:527-535`); the caveat travels as `ratios[].reference_qc_flags`. Because
§2.1 reads that field, `flags(L)` is non-empty. **Ruled: flagged.** Reading only
`bands[].qc_flags` would have called such a lane `pass` while every number in it was divided by a
flagged denominator, which is the single worst failure this vocabulary could have.

**E5 — a lane holding only reference bands under a housekeeping mode.** References are skipped as
numerators (`pipeline/normalize.py:527-535`), so the lane emits no ratio. **Ruled: E1 — blocked,
`no_ratio_emitted`.**

**E6 — a lane id in `ratios` or `bands` that is not in `lanes`.** Not an edge case in the data; a
corrupt document. **Ruled: raise.** Loud failure over silent fallback. Likewise a missing
required key: no placeholder defaults.

**E7 — image flags on a lane that is otherwise clean.** `image_qc_flags: ["saturated"]` with no
band flag in the lane. **Ruled: pass.** This follows from §2.1 reading no image field, and it is
the case Ruling 2 exists to permit: a clean lane inside an image the product calls saturated is
a pass card. It is also, per §4, the situation of every pass lane in the corpus.

**E8 — `low_dynamic_range` on the image.** Same as E7: not read, no effect. The probe found this
flag has no consumer anywhere in `pipeline/` or `api/`; this document does not give it one.

---

## 4. What this mapping will do to the corpus — fixed before measurement

**This section is the pre-registration. A number that comes out differently is a finding, to be
written up as one before anything is re-recorded.**

### 4.1 On the detected-path corpus: a reproduction, not a prediction

`runs/4b0/PROBE.md` §2 already counted the 12-crop, 116-lane corpus under a derivation invented ad
hoc for that probe, which tested only `bands[].qc_flags`: **15 pass, 77 flagged, 24 blocked**.
Restating those numbers here would be recording an answer already known, so they are stated as
what they are — a prior measurement — and what is pre-registered is a **falsifiable claim about
the relationship between the two derivations**:

> **P1. The ruled mapping reproduces the probe's census exactly: 15 pass, 77 flagged, 24 blocked
> over 116 lanes.**

The mechanism, so the claim is checkable rather than asserted: the two derivations differ only in
that §2.1 also reads `ratios[].reference_qc_flags`. The corpus ran under
`normalization.mode: total_protein`, and in that mode `pipeline/normalize.py:517-523` sets the
denominator's flags to the union of every band flag in the lane. That union is exactly the set the
probe's derivation already tested. The extra input is therefore redundant **on this corpus and in
this mode**, and adds nothing to any lane's flag set.

**If P1 fails, the probe's derivation and the ruled mapping disagree somewhere**, and the
disagreement is the finding — not a reason to adjust either number.

### 4.2 On the caller-supplied-ROI path: a genuine prediction

Nothing on this path has been measured. Every figure in the probe came from
`roi_source: detected` (116 of 116 lanes).

The re-measurement will take each candidate lane's **detected rectangle** and re-submit it as the
sole caller-supplied lane ROI on the same crop (`python -m pipeline run … --lane-roi X,Y,W,H`),
which is exactly Ruling 2's unit: one card, one ROI.

> **P2. Each candidate lane reproduces its detected-path measurement exactly under the caller
> path: the same band count, the same integrated intensities, the same QC flags, the same ratios,
> and therefore the same verdict.**

The mechanism: background correction runs on the whole image before lanes exist
(`pipeline/analyze.py:203-204`), so it is unaffected by how lanes are chosen. Supplying a
rectangle identical to the one detection produced hands band detection the identical sub-image
(`pipeline/detect.py:796`: band detection inside a supplied lane is "the same code"). Under
`total_protein` the denominator is the integral over the lane rectangle
(`pipeline/analyze.py:221-230`), and the rectangle is unchanged, so the denominator is unchanged.

> **P3. Two fields will change, and their changing is not a failure of P2.** The lane and band
> **ids** (the candidate becomes `L0`, its bands `L0_B0…`, because supplied lanes are numbered in
> the order given — `pipeline/detect.py:388-393`), and the **`result_id`**, because the supplied
> rectangles are a hashed input to it (`pipeline/analyze.py:252-259`, and the reasoning at
> `pipeline/analyze.py:62-70`). `roi_source` becomes `caller`.

**What would falsify P2**, named now so it is checked rather than explained afterwards: any
difference in band count, in an integrated intensity, or in a flag set. Two mechanisms could
produce one, and each means something different — lane detection doing something beyond emitting a
rectangle, which would be a finding about `detect_lanes`; or `validate_lane_rois` altering a
rectangle it accepts (`pipeline/detect.py:309-326`), which would be a finding about the caller
path. Neither is expected. If P2 fails, the gallery cannot be composed from probe figures at all
and the whole corpus needs re-measuring on the caller path.

> **P4. No verdict class will be empty on the corpus, and the pass class will be non-empty while
> no image is flag-free.** The probe established 15 clean lanes across 12 crops of which all 12
> carry `image_qc_flags: ["saturated"]`. Under E7 those lanes are `pass`. This is stated as a
> prediction because it is the substantive content of Ruling 2 and it is the claim the gallery
> rests on: **a pass card exists at lane level and would not exist at image level.**

### 4.3 What no part of this section licenses

No parameter moves. No threshold is chosen. If the caller-path re-measurement disagrees with the
detected path, that is recorded as a measurement and carried as debt; it does not license a
detection or QC parameter change, and Gate 1 ruling 3 is not suspended by a prediction coming
true or by one failing.

---

## 5. Implementation, and its boundary

**Where.** One pure function in `api/display.py`, per Ruling 1. It takes a result document and
returns one verdict record per lane.

**What it may not do**, each of these being a restatement of the ruling as a constraint on code:

* it adds no field to the result document, and `schema/result.schema.json` is untouched — the
  verdict is derived on demand and never stored as a measurement;
* it adds no code path to `pipeline/`, and imports nothing from it;
* it reads no pixels, no config object and no file — one document in, verdicts out;
* it does not read `image_qc_flags` (Ruling 3).

**Tests**, per the ruling's requirement:

* one test pinning each of the three verdict classes on a document built for it;
* one test per edge case E1–E8;
* a **mutation test**: a lane that is `pass`, mutated by adding one QC flag to one band, becomes
  `flagged`; mutated further so no ratio survives, becomes `blocked`. The point is that the
  verdict is a function of the flags and cannot be reached by a code path that ignores them.

**Deliberately out of scope, and needing its own ruling.** The verdict is **not** wired into the
`POST /analyze` or `GET /results/{id}` response envelope. Serving it would add keys to the
`display` block, which is stored on disk and checked against `DISPLAY_BLOCK_KEYS`
(`api/display.py:84-99`, `api/app.py:185-218`), so every stored record written before the change
would fail the labelling check that exists to catch a damaged one. That is a migration decision
with a compatibility cost, and Ruling 1 rules where the derivation is computed, not that it is
served. This document proposes it as the next phase's question and does not answer it.

---

## 6. What this document does not do

It does not select a gallery. Selection stays with Sofia; §4 fixes only what the mapping will
report. It does not change any measurement, any recorded figure, or any parameter. It does not
give `image_qc_flags` a consumer. It does not re-crop, re-log or re-run the corpus, and
`runs/3b2/` is read and not modified. It does not define an image-level verdict, and nothing here
should be read as licensing one.

Proposed: implementer, 2026-08-25.

Awaiting ratification: Sofia.
