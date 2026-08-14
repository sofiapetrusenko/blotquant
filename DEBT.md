# Debt register

Known weaknesses in one place, each with a phase or an explicit trigger. Before this file they were
scattered across [NOTES.md](NOTES.md)'s open items, three PR bodies, and conversations that are not
in the repository at all.

**What this file is.** A register of things that are wrong, unproven, or unfinished — not a backlog
of features. A phase not yet started is not debt; PLAN.md already schedules it. An item belongs
here when it weakens a claim the project makes, or would surprise someone who trusted the tool.

**Grouping.** **Scientific** — affects whether the numbers mean anything. **Engineering** —
affects whether the tool works for someone other than the author. **Process** — affects whether
the record can be trusted.

**Evidence.** Every figure here is measured, and says where from. Where there is no measurement the
entry says so rather than implying one. No dates are assigned, because none have been agreed.

**Status vocabulary.** Every status line begins with one of three words and may carry a qualifier
after it. `Open` — live, unresolved. `Accepted` — understood and deliberately kept, with the reason
recorded. `Permanent` — inherent to the approach, will not close. The three groups above are the
only formal severity; an entry may additionally note in prose where it sits *within* its group
(S17 says low severity, P3 says minor), because a register that flattens a one-line branch-naming nit
against an unvalidated generator is less useful, not more rigorous.

**Snapshot.** Phase 2 of 5 complete; **Phase 3 Gate 1 has ruled** (NOTES.md, "Phase 3 Gate 1 — eval
design sign-off"), but no Phase 3 measurement has been run. This register describes the repository
at commit `52200a5` (Phases 0–2 merged, plus the README and this register). **Re-check every entry
at each phase gate**: confirm the evidence still reproduces, close what the phase closed, and add
what it introduced. A register without a snapshot goes stale invisibly.

Nine entries carry a **Ruled at Phase 3 Gate 1** paragraph alongside their original evidence — S2,
S3, S4, S5, S7, S8, S10, S12 and S16 — plus a consequence recorded on S11. The Gate 1 ruling numbers 1–10 are this record's; the human described the first batch as seven and listed eight, then added two more. A ruled entry keeps its history — the measurement that made it debt
is unchanged; what is added is the decision taken about it.

---

## Where the weight actually sits

Three entries carry most of the consequence:

- **S1** — every accuracy figure in the project measures the pipeline against one seeded model of a
  blot that has never been checked against a real one. If the model is wrong, nothing here would
  reveal it.
- **S2** — `total_protein`, one of the four things PLAN.md claims distinguishes this tool from
  ImageJ, currently measures worse than the mode it is meant to improve on, and produces a negative
  denominator on one lane. The differentiating claim is not yet supported by its own measurement.
- **E1** — nobody has ever followed the documented install-and-run path on another machine, so
  "it works" is untested outside one working tree.

The rest divides three ways. **Fifteen of the 29 entries are `Accepted` or `Permanent`; 14 are
`Open`.** Nine entries carry a Gate 1 ruling: of those, **six moved to Accepted at the gate**
(S2, S5, S7, S8, S10, S12), **two were already Accepted before it** (S4, S16), and **one stays
Open** (S3). The remaining seven Accepted-or-Permanent entries (S9, S11, S13, E5, E9, P1, P2) were settled before Gate 1. Accepted does not mean fixed: S5's QC flag
still scores F1 0.000 and S10's still under-warns on a third of the low-dynamic-range images; the
gate decided to keep and disclose them rather than change them, and both keep their measurements.
**Two are one-line fixes** (E4's empty GitHub metadata, P3's branch naming). **The remaining `Open`
entries are real and not minor**: S6's housekeeping figure rests on an oracle no real blot supplies
and Gate 1 explicitly did not settle it; S3's two parameters have no gold-set evidence and, after Gate 1 rulings 3 and 5, no route to any measurement against ground truth; S17 is a silent drop against a stated project rule; E1 means nobody
has run this outside one working tree; and S2's comparability gap is open — now owned by
Phase 3, conditional on a common subset existing — even though the denominator question above it is
ruled. The project is not broken; it is measured, and this file is
the list of what the measurements do not yet cover.

---

## Scientific

### S1 — The generator has never been validated against real blot images

**What.** Every accuracy figure the project reports measures the pipeline against one seeded model
of what a western blot looks like (`synth/MODELS.md`). That model has never been checked against a
real blot.

**Why it matters.** If the model is wrong in some respect, the pipeline can score well on the gold
set while being wrong on real data, and no figure in the repository would reveal it. This is the
single largest threat to the meaning of every number quoted anywhere.

**Evidence that the risk is real, not theoretical.** An earlier cut of the generator produced
bands far too round for a western blot: 13 of the 40 images fell below an aspect ratio of 2.5, with
the 6-lane `smeared` cells at 1.14 — near-circular, which is a dot-blot shape, not a lane
cross-section. Automated review passed it; a human domain review caught it, and it was fixed
**before the gold set was frozen**. **The two recorded figures do not conflict** — they are two
thresholds over the same population. PR #1's body gives "13 of 40 images fell below 2.5"; NOTES.md
("Band aspect ratio is a configured, validated quantity") gives "13 of the 40 committed images —
every `smeared` cell — held bands rounder than 1.75". Same count, same images, and both sources
give the same worst case of 1.14 at 6-lane `smeared`: every affected image was below 1.75, hence
also below the 2.5 floor that was subsequently adopted. Note that neither figure is re-derivable
from the tree — the pre-fix generator config was never committed (there is one `synth/` commit, and
it is post-fix) — so both rest on the two written records agreeing, which they do. **The committed
gold set is not affected** — all 467 bands in all 40 images clear the
floor, minimum measured aspect ratio 2.5999999 — so this is evidence about the *review process*,
not a defect in the data anyone is using. What it shows is that a physically impossible modelling
assumption survived automated review and was caught only by a human who knew what a blot looks
like. Nothing rules out further unvalidated assumptions in `synth/MODELS.md`; that one was found by
eye, not by a test.

**Closes.** Partially at Phase 3's ImageJ cross-validation on real CC-BY blots. **Never fully** —
agreement with ImageJ on a sample of real blots narrows this risk, it does not eliminate it.

**Status.** Open, and permanently partial. State it as a limitation in any publication of these
numbers.

### S2 — `total_protein`, the differentiating mode, is not yet supported by measurement

**What.** Total-protein normalization as a first-class mode is one of the four things PLAN.md
claims distinguishes this tool from ImageJ. On the dev split it currently measures worse than the
single-housekeeping mode it is meant to improve on, and on one lane of one image it fails
outright.

**Why it matters.** The differentiating claim is a claim about quality, and the measurement does
not currently support it. Until the denominator is fixed or the comparison is made properly, the
project should not assert that total-protein normalization is better — only that it is available
and needs no oracle.

**Evidence.** `total_protein` 31.65% mean / 13.94% median over 150 included ratios from 86 lanes;
`housekeeping_single` 12.22% / 3.96% over 77 included of 129 ratios from 129 lanes. **The two are
not directly comparable** — different lane subsets, different ratio counts, and the housekeeping
figure uses an oracle reference — so the direction is consistent but the size of the gap is not
established. On `dev_03` one lane's integral is negative (−5411 DN exactly) and that lane yields no
ratios, reported as `lane_denominator_not_positive`; the image's other three lanes are unaffected
and it emits 7 ratios in total. Cause: the denominator integrates ~9000 px of
full-height lane, accumulating background-estimator residual over an area far larger than any band.

**Closes.** Closed at Phase 3 Gate 1 — see below.

**Ruled at Phase 3 Gate 1 (Gate 1 ruling 7).** **`total_protein` keeps the ROI pixel-sum denominator.** No
alternative was shown to be better, and changing the measure would invalidate the recorded figures
without evidence the replacement improves on them. What the ruling requires instead is disclosure,
now in README's Limitations: that the 31.65% follows from integrating background-estimator residual
over a full-height lane, and that **the claim of total-protein normalization being *better* than
single-housekeeping is not supported by measurement** — only that it needs no oracle reference.

**Still live after the ruling.** The gap between the two modes' errors remains unestablished — the
route recorded before the gate, "a common-subset comparison would settle the gap independently of
the denominator choice", was **not** carried out, and Gate 1 ruled without it. NOTES.md still says
that comparison is worth producing. The ruling settles which denominator ships; it does not settle
how much worse `total_protein` actually is.

**Owner for the comparability gap.** **Phase 3, conditional on a common subset existing.** The two
modes' errors are measured over different lane subsets, so settling the gap needs a set of lanes
both modes score — which the ImageJ comparison will have to define anyway, since it must decide
which lanes and ratios enter the correlation. If that work produces no usable common subset, the
gap stays unmeasured and the register says so rather than inventing a comparison; but it is Phase 3
work with a trigger, not an item without an owner.

**Status.** Accepted for the denominator (Gate 1 ruling 7). The comparability gap is Open, owned by
Phase 3.

### S3 — Two shipped parameters have zero evidence on the committed gold set

**What.** `detection.lane.extent_relative_height` and `detection.lane.extent_min_sigma` are read
only when exactly one lane is detected. The committed gold set contains no single-lane image, so
no sweep can reach them.

**Why it matters.** They can be neither confirmed nor refuted with current data. They ship on a
consistency argument — they inherit the band extent rule's dev-selected values.

**This is the weakest *evidential basis* in the shipped set** — no sweep reaches these two at all.
It is a different weakness from S12's `qc.shoulder_half_width_ratio`, which has the weakest
*justification* (its criterion restates its value) but does have a recorded surface and is testable
against the gold set. Phase 3 Gate 1 split the two rather than ranking one as simply "weakest", and
judged **this pair the more serious**: a poor justification can be improved by argument, whereas
absent evidence gives nothing to argue from. Under Gate 1 ruling 3 this pair can now only be falsified on
a real single-lane image, never selected.

**Evidence.** Lane counts in the dev split — the only split sweeps read — are 4, 5 and 6 at ten
images each; across the whole 40-image gold set they are 4 (×14), 5 (×13), 6 (×13). Never 1. The
single-lane path is covered by unit tests only; on the unit fixture the rule yields a 15% narrower
band ROI excluding about 6 px of true band signal, and the effect on *recovery error* is unmeasured.

**Closes.** Originally: a gold-set regeneration decision that adds single-lane images, or Phase 3.
The first is foreclosed (below). The second stands, but only in the narrow form Gate 1 ruling 3
permits — observation on a real single-lane blot, never selection.

**Ruled at Phase 3 Gate 1 (Gate 1 rulings 3 and 5).** The first of those routes is now **foreclosed** —
Gate 1 ruling 5 keeps
`synth/` frozen and the gold set unregenerated, so no single-lane image is coming. Gate 1 ruling 3 opens a
different one: on a real single-lane image the two parameters can be **falsified**, or observed to
pass, but never tuned. So the entry's substance is unchanged — still no gold-set evidence — while
its route has narrowed to observation on data with no ground truth.

**Status.** Open — no evidence from the gold set, and no route to *tuning* them. Gate 1 ruling 3 does
permit pass/fail observation on a real single-lane blot, which is weak positive evidence at best;
it is not a measurement against ground truth, because none exists for real images.

### S4 — Band recall is capped at 0.852 by design, regardless of parameters

**What.** The detector reports one band per resolved local maximum and does not deconvolve
shoulders, so an unresolved doublet yields one ROI. The doublet partner is unreachable by any
parameter choice.

**Why it matters.** No amount of tuning can raise recall past this ceiling. A reader comparing the
reported recall of 0.793 against 1.0 is measuring against a target the design excludes.

**Evidence.** The dev split contains 52 doublet partners; the ruling costs all of them (46 missed
as `target_secondary`, and in the other 6 the partner matched instead so the primary is missed),
capping recall at 300/352 = 0.852 against the measured 0.793.

**Closes.** Not by tuning. Only by either resolving doublets — which Phase 1 declined, because a
shoulder-splitter must invent a second centre from an inflection and would be fitting noise in
exactly the weak, high-noise cells where it matters — or by a generator-side ruling that the
doublet cell should be resolvable (see S8).

**Ruled at Phase 3 Gate 1 (Gate 1 ruling 5).** The alternative route — a generator-side ruling that the
doublet cell
should be resolvable — was **declined**: `synth/` stays frozen and the gold set is not regenerated.
The 0.852 ceiling is therefore a standing property of the shipped configuration, not a number
awaiting correction.

**Status.** Accepted, with the cost measured and recorded, and the alternative route now closed.

### S5 — The geometric `overlapping` flag currently misinforms

**What.** The flag scores F1 0.000 against ground truth, and the few bands it does flag are mostly
not real bands.

**Why it matters.** A QC flag that never fires on the condition it names, and fires instead on
detector artifacts, is worse than no flag: it is a claim of coverage the tool does not have. This
sits directly against the project's premise.

**Evidence.** 0 true positives, 1 false positive, 52 misses over 279 matched bands. At the shipped
threshold of 0.05 only 3 detected same-lane pairs overlap enough to flag, putting the flag on 6
detected bands — and **only 1 of those 6 is a matched detection; the other 5 match no truth band at
all**, i.e. the flag fires predominantly on detection false positives overlapping each other. The
cause is structural: because unresolved doublets yield one ROI, there is usually nothing to overlap
with. `unresolved_shoulder` is the flag that answers the question a reader of "overlapping" is
actually asking, and it separates the populations 0.596 against 0.022.

**Closes.** Closed at Phase 3 Gate 1 — see below.

**Ruled at Phase 3 Gate 1 (Gate 1 ruling 6).** **The flag is retained as measured evidence, not as a working
feature.** Retiring it would remove the evidence that a plausible-sounding QC flag can fail
completely, which is a result worth keeping; `unresolved_shoulder` covers the real need. README
states that it is kept as a recorded negative result rather than as functionality. Everything
measured above stands — it is kept *because* those numbers are bad, not despite them.

**Status.** Accepted — retained deliberately as a recorded negative result.

### S6 — Housekeeping normalization figures rest on an oracle that does not exist on a real blot

**What.** The reference band for the housekeeping modes is designated in evals from ground truth's
`role` field. The pipeline itself refuses to infer a reference and requires the caller to name one.

**Why it matters.** The reported housekeeping error is conditional on a *correct* reference being
supplied. It is not evidence that the tool can find a loading control, and it cannot be reproduced
on data without ground truth.

**Evidence.** Human-ratified in Phase 2 on condition of disclosure, which is carried in the eval
output, NOTES.md, the Phase 2 PR body and README.md. The figure it qualifies is 12.22% mean.

**Closes.** Phase 3 needs a real-blot substitute. The honest answer may be that it cannot be
measured without a human in the loop, in which case the register entry becomes permanent.

**Status.** Open.

### S7 — The clean-band selector cannot be computed on real blots

**What.** Most shipped parameters were selected on "clean mean" — mean recovery error over bands
whose *ground truth* carries no QC flag.

**Why it matters.** The selection criterion is unavailable on any real image, so the basis on which
parameters were chosen cannot be re-applied outside the gold set.

**Evidence.** Recorded in the Phase 1 PR body and NOTES.md. Verified non-circular with respect to
the generator's own ROI rule — re-scoring the aperture sweep against `true_total_intensity_dn`
leaves the argmin at 0.05–0.06 — but that addresses circularity, not availability.

**Closes.** Superseded — see the ruling below.

**Ruled at Phase 3 Gate 1 (Gate 1 ruling 3).** **No substitute selector is needed, because no selection
happens on
real blots.** Real blots may falsify a parameter but never select one: `data/real/` is for
measurement only — ImageJ agreement, and pass/fail observation of whether a code path produces
sensible output. The weakness the entry records is real and unchanged — the criterion genuinely
cannot be computed outside the gold set — but it is no longer a gap awaiting a replacement, because
nothing downstream needs one. Note this decouples S7 from S6: the housekeeping **reference band**
oracle is a measurement input rather than a parameter choice, and remains open.

**Status.** Accepted — the selector stays synthetic-only by ruling, not by omission.

### S8 — Whether the `doublet` cell is meant to be resolvable is unruled

**What.** At the committed `doublet_offset_sigma = 2.2` the two partners sum to a single local
maximum with a shoulder. Whether the cell is *intended* to be resolvable was never decided.

**Why it matters.** It determines whether S4's recall ceiling is a property of the tool or an
artifact of the gold set. "Resolves two closely spaced bands" and "notices a shoulder" are
different capabilities, and the project currently scores the second while the cell was arguably
built for the first.

**Evidence.** On the pixel grid a second maximum appears from 2.55 σ; 2.8 σ gives a 12% dip.
Raising the offset would need one regeneration and drops partner ROI IoU to 0.269–0.333, still
above the generator's `overlap_iou_threshold` — though NOTES.md flags that range as "measured in
memory, not written", so unlike most figures here it cannot be re-derived from the tree.

**Closes.** Closed at Phase 3 Gate 1 — see below.

**Ruled at Phase 3 Gate 1 (Gate 1 ruling 5).** **`synth/` stays frozen, `doublet_offset_sigma` is not
raised, and
the gold set is not regenerated.** The cost — a break in eval comparability, a `SYNTH_VERSION` bump
and regeneration of every recorded figure — exceeds the benefit, and the recall ceiling the cell
produces is already measured and disclosed (S4). The cell therefore stays as it is: one peak with a
shoulder, scored as such.

**Status.** Accepted — ruled, with the consequence carried by S4.

### S9 — Lane detection F1 measures horizontal lane-finding only

**What.** A detected lane ROI always spans the full image height. Nothing measures where a lane
begins or ends vertically.

**Why it matters.** The y-axis contributes exactly 1.0 to every lane IoU by construction, so the
reported lane F1 of 0.997 is structurally flattered and is not a lane-localisation figure.

**Evidence.** Truth lane ROIs are `y = 0, height = 192` and the pipeline emits `y = 0, height =
height_px`. Disclosed at the point of reporting in README.md and NOTES.md. Separately, the emitted
ROI reproduces three of the five properties `MODELS.md` §4a declares; the tilt widening is
deliberately not reproduced, and tilted cells cap at IoU 0.7475 as a result (recorded in the
`lane_roi_geometry` surface; an earlier PR body said ≈ 0.72, which is stale).

**Not settled by Gate 1, though it looks adjacent.** Gate 1 ruling 7 froze the `total_protein`
denominator, which is computed over exactly this full-height rectangle. It ruled on the
denominator, **not** on the lane geometry: the vertical extent is still undetected and the lane F1
is still structurally flattered.

**Closes.** Phase 3.

**Status.** Accepted for now — the convention is deliberate, because Phase 2's total-protein
integral needs the whole lane — but the reported number needs its caveat wherever it appears.

### S10 — `low_dynamic_range` under-warns, and the fix collides with anti-circularity

**What.** The flag misses 3 of the 10 genuinely low-range dev images (recall 0.700, precision
1.000). The threshold that would catch all ten was refused.

**Why it matters.** For a QC-first tool, under-warning is the more dangerous direction: a silently
unflagged low-range image yields numbers a reader trusts. Precision is 1.000 at every swept
threshold, so on this gold set there is no measured false-positive cost to raising it.

**Evidence.** Recorded surface `qc.dynamic_range_min_peak_fraction`: 0.15 → recall 0.500, 0.20 →
0.700, **0.25 shipped → 0.700**, 0.30 → 0.800, 0.40 → 1.000, precision 1.000 throughout. 0.40 was
refused because it sits above the generator's scratch amplitude (`0.25·M`), so clearing it would be
tuning to an artifact only this generator produces. The three misses are the three images that are
both `exposure: low` and `defect: scratch`; a scratch is additive contamination a peak measurement
cannot distinguish from signal.

**Closes.** Closed at Phase 3 Gate 1 — see below.

**Ruled at Phase 3 Gate 1 (Gate 1 ruling 4).** **The threshold stays at 0.25 and the question is closed on
synthetic
data.** Under Gate 1 ruling 3 it cannot be selected on real blots either, and the entire disagreement is
about a generator constant, so waiting for real-blot evidence would defer the decision permanently.
The measured curve above is unchanged and stays as the record of what was traded.

**Still live after the ruling.** Gate 1 fixed the *threshold*. It did not rule on the route this
entry's underlying NOTES item proposes — replacing the **measure** (a robust upper quantile of band
peaks, or the lane-profile peak, instead of the brightest band's peak), which is what would address
the additive-contamination cause rather than trade recall against it. That remains Phase 3 work.

**Status.** Accepted for the threshold — a known limitation, not an open decision: the flag
under-warns, missing 3 of the 10 low-dynamic-range dev images. The measure-replacement route is
open.

### S11 — Absolute integrated intensities are convention-dependent

**What.** A band's ROI depends on the lane slice it was measured in, which differs between
single- and multi-lane images.

**Why it matters.** Absolute integrated intensities must not be compared across images or
conventions. Within one image the convention is uniform, so normalization ratios are unaffected —
but a user exporting raw intensities across a set could compare numbers that are not comparable.

**Evidence.** On the unit fixture the same band measures 39 px wide in a multi-lane ROI and 33 px
in a single-lane one — ~15% narrower, excluding about 6 px of true band signal, not only
background. Mechanism: a tighter slice raises the column profile's
`detection.band.baseline_percentile`, lifting
the edge threshold.

**Closes.** Not scheduled. Mitigation in force is disclosure, in README.md and NOTES.md.

**Consequence recorded at Phase 3 Gate 1 (Gate 1 ruling 8).** This is why the ImageJ comparison is specified on
**normalized ratios, not absolute intensities**: an absolute comparison across two tools with
different aperture conventions would measure the conventions rather than the methods.

**Status.** Accepted with disclosure. Revisit if export (Phase 5) makes cross-image comparison easy.

### S12 — Parameter selection pre-empted the human gate that was supposed to precede it

**What.** PLAN.md puts Gate 1 — human sign-off on the eval design — *before* parameter iteration.
13 of the 20 shipped numeric parameters were selected from dev-split sweeps before that gate ran.

**Why it matters.** The gate exists so that a human agrees the measurement design before numbers
are optimised against it. Running it afterwards means Gate 1 must ratify or redo a selection that
already happened, which is a weaker check than the plan intended.

**Evidence.** 13 of 20 dev-sweep-selected; the other 7 comprise 2 that no sweep can exercise (S3)
and 5 QC parameters chosen from stated criteria. Two of the five still want the human's eye:
`qc.shoulder_half_width_ratio = 1.5`, whose criterion restates the value rather than deriving it and
which sits at the widest separation on its recorded surface measured as a fold ratio (27× against
4.4× at 1.3; on the raw rate *difference* 1.3 is marginally wider, 0.578 against 0.574), and
`qc.saturated_min_clipped_pixels = 1`
(already ruled, kept for ratification).

**Closes.** Closed at Phase 3 Gate 1 for 18 of 20 — rulings 1, 2, 4, 9 and 10. S3's pair is not
ratified and stays with S3.

**Ruled at Phase 3 Gate 1 (Gate 1 rulings 1 and 2).** The thirteen dev-selected parameters are ratified
**as a procedure, not re-selected**: re-running selection on the same split with the same selector
would produce the same values and change nothing epistemically.
`qc.shoulder_half_width_ratio = 1.5` was ratified **separately, as a fourteenth value on its own
terms** — it is one of the five QC parameters chosen from stated criteria, **not** one of the
thirteen, so it is not a carve-out from them. It carries the **weakest justification** among the
shipped parameters, because its criterion restates the value rather than deriving it. The evidence
above is unchanged — what changed is that the gate has seen it and accepted it.

**What each ruling covered.** Gate 1 ruling 1 covers the thirteen dev-selected parameters. Of the five QC
parameters chosen from stated criteria, **two were taken up**: `qc.shoulder_half_width_ratio`
(Gate 1 ruling 2) and `qc.dynamic_range_min_peak_fraction` (Gate 1 ruling 4, which fixed it at 0.25 — see S10).
**The remaining three** — `qc.saturated_min_clipped_pixels`, `qc.overlap_iou_threshold` and
`qc.shoulder_half_maximum_fraction` — were **ratified explicitly by Gate 1 ruling 10**, on their
recorded criteria, which completes the five QC parameters rather than leaving three in an undefined
state. It does **not** complete the twenty: S3's single-lane pair remains unratified. Ratification is of the criteria as recorded, not a claim that any of the three is optimal;
none was selected on a surface.

**Status.** Accepted — **18 of the 20 shipped numeric parameters are ratified**: the thirteen
dev-selected as a procedure (Gate 1 ruling 1, with `background.local_median.window_px` re-examined
on its merits within that set by Gate 1 ruling 9), plus all five QC parameters chosen from stated
criteria — `qc.shoulder_half_width_ratio` (Gate 1 ruling 2), `qc.dynamic_range_min_peak_fraction`
(Gate 1 ruling 4) and the remaining three (Gate 1 ruling 10). **The other two are not ratified**:
DEBT S3's single-lane pair, which Gate 1 ruling 1's scope leaves "untouched" because there is no
evidence to ratify. The "weakest" question is
**split, not assigned**: this parameter has the weakest *justification* but is testable against the
gold set; S3's pair has the weakest *evidential basis* and is not testable at all. S3's is the more
serious — a poor justification can be improved by argument, absent evidence cannot.

### S13 — Chemiluminescence is not linear in protein amount

**What.** The detection chemistry is not linear over an arbitrary range, and the tool measures the
image it is given.

**Why it matters.** No amount of image processing recovers protein amount from an exposure taken
outside the detector's linear range. The tool can only warn that the dynamic range looks wrong.

**Evidence.** None needed — this is a property of the assay, not of the code. Named in PLAN.md's
scope and in README.md's limitations.

**Closes.** Never.

**Status.** Permanent.

### S14 — Two modelling assumptions are unexamined: signal polarity and lane tilt

**What.** Detection assumes bright-signal-on-dark, and profiles are projected along image axes, so
a tilted lane smears its column profile. Neither has been examined as a scope decision.

**Why it matters.** A transmissive or film workflow inverts polarity and would silently misbehave.
Tilt is a real gel-doc condition that the projection does not model.

**Evidence.** Both raised as open questions in the Phase 1 PR body and unanswered. The gold set
includes a `tilted` lane-geometry level, and tilted cells cap at IoU 0.7475; there is no
measurement of polarity because no inverted image exists to test.

**Closes.** Unscheduled. Polarity was raised as "config parameter later, or out of scope?" and tilt
as "sheared projection
= Phase 3, or out of scope?"; neither has been ruled.

**Status.** Open, unruled.

### S15 — A weak-band floor is shipped without confirmation that it suits real blots

**What.** `detection.band.min_prominence_fraction = 0.30`: a band weaker than 30% of the strongest feature in
its lane is not reported at all.

**Why it matters.** On a real blot a biologically meaningful band can easily be under 30% of the
strongest band in the same lane. Such a band is silently absent rather than flagged.

**Evidence.** Chosen on the dev surface — matched bands hold at 279 through 0.30 and drop to 274
at 0.35 — so it is the largest value that costs no dev band. Whether that generalises to real
blots is unconfirmed; raised in the Phase 1 PR body and unanswered.

**Closes.** Phase 3, against real blots.

**Status.** Open.

### S16 — The `total_protein` denominator is not literally what PLAN.md specifies

**What.** PLAN.md calls the mode's denominator a "lane-profile integral". The implementation uses
an ROI pixel sum over the lane rectangle.

**Why it matters.** The two differ by the lane width and so diverge between lanes of unequal width.
The difference is deliberate and documented, but the plan and the code do not use the same words.

**Evidence.** The pixel sum is the right comparable — same DN units as the band sums, matching the
eval's truth analogue — and it integrates over the Phase 1 Voronoi lane ROI without re-deriving it.
Declared in the Phase 2 PR body; the schema description was corrected to say so.

**Closes.** Phase 3 Gate 1, alongside S2's denominator decision, or by amending PLAN.md's wording.

**Ruled at Phase 3 Gate 1 (Gate 1 ruling 7).** Ratified. The pixel-sum denominator stays, so PLAN.md's
"lane-profile integral" wording and the implementation remain different words for a deliberate
choice rather than a discrepancy to reconcile in code.

**Status.** Accepted — declared deviation, ratified at Phase 3 Gate 1.

### S17 — Detection can drop a peak without recording it

**What.** `_detect_bands_in_lane` skips a peak whose lane columns carry no spread over its rows
(`if column_height <= 0.0: continue`). The peak cleared both prominence criteria and is then
discarded with nothing emitted — no band, no count, no flag.

**Why it matters.** CLAUDE.md's rule is "QC annotates, never silently drops", and this is the one
place in the pipeline that drops something silently. On a real blot — where a genuinely flat column
region is more plausible than on the gold set — a detected peak could vanish from the results with
no trace for the user to notice.

**Evidence.** Unreachable on any committed image: it needs a lane exactly constant across its whole
width at a peak's rows, which no gold-set image produces, so **no figure this project reports
depends on it**. The guard is documented and directly tested; what is missing is a recorded count.
Phase 1 flagged it, Phase 2 deliberately did not do it — recorded in NOTES.md, "One item Phase 1
put on this phase's list and this phase did not do" — because surfacing a count means another
`result.schema.json` field and a counter that is always zero on this data is a field a reader
learns to ignore.

**Closes.** Whichever phase next touches the detection contract, per the Phase 2 deferral.

**Status.** Open, low severity — a real gap against a stated project rule, currently unreachable.

---

## Engineering

### E1 — The documented install-and-run path has never been followed on another machine

**What.** Every run has happened in the author's working tree, in one pre-existing virtualenv.
There has been no clone-install-run on a fresh machine.

**Why it matters.** The README's setup instructions are unverified in the one condition that
matters. Missing system libraries, a stale lockfile assumption, a path that only resolves locally,
or a dependency that fails to build on another platform would all be invisible.

**Evidence.** None — that is the point. The README instructions were verified by an agent inside
the same environment they were written in, which does not test the thing at issue. Note that CI
does install from `requirements.txt` on a fresh Ubuntu runner and runs lint, schema validation,
tests and the eval, so the dependency install is not wholly untested; what is untested is the
documented *user* path end to end, including on macOS.

**Closes.** A clean-clone smoke test, ideally in CI on a fresh runner, following README.md's
instructions literally and analysing one image.

**Status.** Open.

### E2 — No packaging, no version tag, no release

**What.** There is no pip-installable distribution, no tagged version, and no release. Installation
means cloning the repository.

**Why it matters.** A scientist cannot install the tool. `pipeline.PIPELINE_VERSION` is `0.1.0` and
appears in every result document's provenance, but nothing in git corresponds to it, so a result
cannot be tied to a released artifact.

**Evidence.** No `pyproject.toml` build target (the file exists but configures ruff and pytest
only), no `git tag`, no GitHub release.

**Closes.** Phase 5.

**Status.** Open.

### E3 — `evals.sweep --check` runs on every push and costs ~9 minutes of CPU

**What.** The CI step that re-measures every recorded figure runs unconditionally.

**Why it matters.** It is the slowest thing in CI by a wide margin and it cannot be affected by a
change that touches no measurement code, so most of those runs are waste. Slow CI on documentation
commits discourages small commits.

**Evidence.** Measured at about nine and a quarter minutes of CPU on an arm64 developer machine —
a figure that exists as prose only, in NOTES.md and `.github/workflows/ci.yml`, with no captured
`time` output committed. The CI step's wall-clock has run 6m23s–7m41s, read from GitHub Actions
step timings rather than from anything in the repository. It is genuinely load-bearing —
it is what catches a stale recorded figure — so the fix is to scope it, not to remove it.

**Closes.** Phase 5, or earlier if it obstructs Phase 3. Intended shape: run it only when
`evals/`, `configs/`, `pipeline/` or `synth/` change, with lint and pytest staying on every push.

**Status.** Open.

### E4 — The GitHub repository has no description and no topics

**What.** The repository's description and topics are both empty.

**Why it matters.** It is invisible in GitHub search and uninformative in a profile listing. For a
project whose README now explains itself well, the metadata is the one place a reader looks first
and finds nothing.

**Evidence.** `gh repo view --json description,repositoryTopics` returns `""` and `null`.

**Closes.** Immediately, by the human — it is a repository setting, not a code change.

**Status.** Open.

### E5 — The QC and normalization figures are only loosely re-measurement-checked

**What.** `--check` compares the Phase 2 figures within four tolerance classes: `QC_FLAG_COUNT`
(±4 counts), `IMAGE_FLAG_COUNT` (±2), `NORMALIZATION_ERROR` (±35% of the recorded value) and
`EXTREME_RELATIVE` (±10%).

**Why it matters.** A real regression smaller than those bounds passes CI, which makes `--check` a
staleness alarm for these rows rather than a regression alarm. The bounds are honest: the
band-scope ones derive from movement the detection-count class already permits, and
`IMAGE_FLAG_COUNT` is *tighter* — ±2 over 30 images, derived independently rather than inherited —
so the looseness is not uniform across the group.

**Evidence.** Bounds and derivations are in `evals/sweep.py`, each with a docstring. The tight
guarantees for QC live in `tests/test_pipeline_qc.py` instead, which asserts flags against
gold-set cases directly.

**Closes.** Phase 3, by scoring band flags over truth ROIs so the QC item set does not move with
detection.

**Status.** Accepted, with the mitigation named.

### E6 — The aperture ordering is transcription-checked but not re-measurement-checked

**What.** The paired-bootstrap differences that justify the shipped ROI aperture carry a tolerance
several times the difference the selection turns on.

**Why it matters.** A code or platform change that inverted the aperture ordering would not fail
`--check`. The recorded surface would still be transcribed correctly while no longer describing
reality.

**Evidence.** The guarding tolerance is 0.3 pp. The comparison the selection actually turns on —
shipped `h = 0.06` against runner-up 0.05, both at clean mean 7.12% — is a **0.01 pp** difference
whose interval spans zero, so it is not resolved at all; the finest *resolved* difference on the
surface is 0.06 pp (the 0.07 row, CI 0.03–0.10). Either way the tolerance is far larger than the
quantity, so an inversion among the near rows 0.04–0.08 passes `--check` with 0 differences —
verified by driving the comparator, though inverting *every* row including the far ones does produce
9 differences, because their magnitudes exceed the bound. That experiment is not recorded anywhere:
`tests/test_sweep_check.py` has no inverted-ordering case, and NOTES.md states the conclusion as
reasoning rather than as a measurement. The bound itself is defensible — it follows from the shared
subset being allowed to move four bands — which is why the *claim* built on it was withdrawn rather
than the bound tightened.

**Closes.** Phase 3, by adopting a selector whose resolution is not of the same order as platform
noise. Note that the obvious alternative — recording the sign or rank of each difference — is
considered and rejected in NOTES.md, on the grounds that recording the resolved sign "would only
move the fragility": the narrowest interval sits closer to zero than a few bands of leverage, so a
recorded sign would itself be platform-fragile. (`BOOTSTRAP_POINT`'s docstring rejects *tightening
the bound*, which is a different argument.)

**Status.** Open.

### E7 — The `total_protein` lane-skip breakdown is not in the sweep record

**What.** The measured reasons the shipped mode is scored on 86 of 150 lanes exist only as prose in
NOTES.md and the Phase 2 PR body.

**Why it matters.** It is the one aggregate figure in the Phase 2 section the record does not hold,
so it is outside the transcription checker and can go stale silently — the exact failure the
checker exists to prevent.

**Evidence.** 59 lanes unscored because not every truth band in them matched, 4 because none
matched inside the matched lane, 1 because detection missed the lane; the missing band is
overwhelmingly the doublet partner (46 `target_secondary` of the 71 truth bands unmatched across
detected lanes; 73 over the whole split, the difference being the one undetected lane). Recording it needs new
`normalization_modes` fields plus a tolerance class, since `evals/sweep.py` raises on an
unclassified field.

**Closes.** The next pass that touches `evals/sweep.py`.

**Status.** Open, deferred deliberately — the pass that measured it was instructed to change no
code.

### E8 — `evals/history.md` does not exist

**What.** Both CLAUDE.md and PLAN.md reference `evals/history.md` as the per-iteration record and
as the place a generator break marker would go. The file has never been created.

**Why it matters.** The freeze protocol depends on it: a `SYNTH_VERSION` bump is supposed to be
paired with a break marker there, because scores across the break are not comparable. If `synth/`
were ever unfrozen today there would be nowhere to record it.

**Evidence.** File absent; both references present in CLAUDE.md and PLAN.md. Disclosed in README's
"What does not exist yet".

**Closes.** Phase 3, which owns the iteration log — or sooner if S8 forces a `synth/` change first.

**Status.** Open.

### E9 — Figures outside marked blocks are not transcription-checked

**What.** The transcription checker covers numbers inside explicit `sweep:` blocks, in
`configs/*.yaml` and in NOTES.md from the Phase 1 heading onward. Everything else is unchecked.

**Why it matters.** NOTES.md is long, and a figure quoted in prose can drift from the record it
came from without any test failing.

**Evidence.** Scope stated in `tests/test_recorded_figures.py` and in NOTES.md in those words.
NOTES.md mitigates it by enumerating what sits outside a block and why — a list now on its fourth
attempt after three failures, which is itself evidence the hazard is live.

**Closes.** Unscheduled. Mitigation is the enumerated list plus the rule in P1.

**Status.** Accepted, with the mitigation named.

---

## Process

### P1 — Numeric claims restated from memory have been wrong repeatedly

**What.** Across all three phases, figures written from memory or from an earlier report — rather
than read from the artefact that produces them — have been wrong. Test counts, parameter counts,
subset denominators, and threshold surfaces have all drifted this way.

**Why it matters.** This project's entire claim is that its numbers are defensible. A register of
weaknesses is worthless if its own figures are unreliable, and the failure is systematic rather
than incidental: it recurred in every phase, and reviewers caught it every time only because they
re-measured.

**Evidence, marked per instance as checkable or not.** Checkable from the tree:

- **Four consecutive Phase 1 review cycles found recorded dev-split figures in NOTES.md and
  `configs/*.yaml` that did not reproduce**, hand-transcription the cause each time — NOTES.md
  states this verbatim.
- **A recorded "3–7 F1 points" claim did not reproduce**; NOTES.md now carries the corrected
  surface and says outright that an earlier version claimed it and was wrong.
- **The outside-a-block taxonomy is on its fourth attempt after three failures** (E9), each failure
  being figures that fell outside a list claiming to be exhaustive.

Reported in review but **not checkable from the tree** — no artefact records them, so a reader
cannot verify these four and should treat them as reported only:

- a test count given as 526 when the suite had 518 (Phase 2);
- a "three of five" enumeration standing over six bullets (README anti-circularity list);
- a tolerance quoted at ±1.0 pp where the class is ±4.0 pp (README `--check` description);
- a comparison stated as "both modes worse" when one of its four figures improved (README);
- a stale lane count (`skipped_total_protein_lanes` 63 where the regenerated record said 64),
  caught by `tests/test_recorded_figures.py` rather than by a reader. Only the corrected 64
  survives in the tree, so the instance itself cannot be checked — what a reader can confirm is
  that the checker exists and that the current figure is 64.

A fifth instance — a test count of 390 against an actual 369 — was reported in review but I could
find no trace of it: `390` appears nowhere in the repository or the archived PR bodies, and
`.git/PHASE1_PR_BODY.md` records **369**, which is correct. If it occurred it was corrected before
the PR body was written. It is named here rather than dropped, so the next reader does not go
looking for it.

**Standing rule, in force.** *Every numeric claim in a PR body, README, or this file must be read
from the artefact that produces it immediately before being written, never restated from a report
or from memory.* Two mechanical supports exist — `evals.sweep --check` re-measures the record, and
`tests/test_recorded_figures.py` fails on a figure that is not the record's — but both are scoped
(E5, E9), so the rule is the primary control, not a fallback.

**Closes.** Never — it is a standing rule, not a task.

**Status.** Permanent.

### P2 — Ratified deviations from PLAN.md

**What.** Six deviations from PLAN.md have been made and recorded rather than folded in silently.

**Why it matters.** PLAN.md is the contract. A deviation that is not recorded becomes invisible
drift; a deviation that is recorded stays auditable.

**Evidence.** (1) `evals/sweep.py`, `evals/dev_sweeps.{json,md}` and `tests/test_recorded_figures.py`
are not in PLAN.md's Phase 1 file list — ratified after Phase 1 cycle 4, because four review cycles
found non-reproducing figures. Phase 2's expansion of `evals/run.py` and `evals/sweep.py` is a
continuation of the same ratification and NOTES.md says so. (2) The README arrived between Phase 2
and Phase 3, where PLAN.md schedules it for Phase 5 — ratified because a public repository with
three merged phases and no README misrepresents itself. (3) MIT licensing was chosen by the human;
nothing in PLAN.md or CLAUDE.md specified a licence. (4) `total_protein`'s denominator wording
(S16). (5) **The CLI gained a repeatable `--reference-band`** (`pipeline/__main__.py`), which
PLAN.md's Phase 1 CLI contract does not have; it exists because Phase 2's human ruling forbade the
pipeline from inferring a loading control, so a housekeeping mode needs the caller to name one.
(6) **`evals/metrics.py` gained `flag_coincidence`** — purely additive, existing signatures and
tests untouched — to report `unresolved_shoulder` as a coincidence rather than as an accuracy
against a truth label that does not exist. (1)–(4) are recorded in NOTES.md; (5) is in NOTES.md's
Phase 2 deviations list and (6) in the Phase 2 PR body.

**Closes.** Not applicable — each is settled. Phase 5's README supersedes the interim one.

**Status.** Accepted.

### P3 — Branch naming has deviated from the phase convention for documentation work

**What.** CLAUDE.md requires work on a branch named `phase-N-<short-name>`. The README landed on
`docs/readme`, this register on `docs/debt`, and the Gate 1 record on `docs/gate-1`.

**Why it matters.** Minor, but the convention exists so that branch names map to phases. Three
documentation branches now do not, and the count grows with each docs task.

**Evidence.** All three branches exist; the convention is stated in CLAUDE.md without exception.

**Closes.** By a ruling: either amend CLAUDE.md to allow non-phase documentation branches, or stop
creating them.

**Status.** Open, minor.

---

## Two NOTES.md open items that are already settled

Listed so a reader sweeping NOTES.md's `## Open items` does not mistake them for live debt:

- **`saturated_min_clipped_pixels` stays at 1** — settled by human ruling, with the reasoning
  recorded: any clipped pixel truncates the integrated intensity, and 3 is the generator's
  labelling threshold rather than a biological one. The entry is retained in NOTES.md to prevent
  re-litigation, and is marked "No longer open".
- **Lane ROI width** — a Phase 0 item, settled in Phase 1 as a detected Voronoi partition one pitch
  wide. The residual concern about the *vertical* extent is separate and is live as S9.

Phase 1's open question about `excluded_from_normalization` always being emitted was closed by
Phase 2 and is not listed here. Note that the Phase 1 → Phase 2 handoff was *not* fully discharged:
of the three items Phase 1 left, two were done and the third — surfacing a count for the peak
detection drops — was deliberately deferred and is carried above as S17.
