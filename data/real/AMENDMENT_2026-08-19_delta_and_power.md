# Amendment 2026-08-19 — mean |Δ| and Spearman discriminating power

**Status: DRAFT — not in force until ratified by the human gate.**

**Ratification procedure — one commit, all four steps.** The figure pins in
`tools/check_claims.py` reach this document *by its exact path*, and the check returns quietly
when that path is absent. That is acceptable while this file is a draft and unacceptable once it
is in force, so ratification is a defined sequence rather than an edit:

1. flip the Status line above from DRAFT to ratified, dated;
2. recompute this file's sha256;
3. pin that digest in `tools/check_claims.py` beside the pre-registration's, so an edit to a
   ratified amendment fails the build the way an edit to the pre-registration does;
4. if the file is renamed in the process, update `AMENDMENT_PATH` in the same commit.

Steps two through four are not optional and not deferrable to a follow-up: a ratified amendment
whose digest is unpinned, or whose path no longer resolves, has its figure pins silently retired
while CI stays green — which is the failure this document's own verification note exists to
prevent.

Amends `data/real/DECISION_unit_of_analysis.md`, and **supersedes part of it**. An earlier
draft of this header read "Supplements, does not supersede: no section of the
pre-registration is rewritten"; that was inaccurate and is **withdrawn** by human ruling of
2026-08-19. What this amendment supersedes, named explicitly:

- **§5, the stop rule.** Its ratio floor for a descriptive-only outcome is raised (Ruling B,
  headline 1), and its "30 ratios or 10 blots, whichever comes first" ceiling is joined by a
  blot minimum (Ruling B, operational consequences).
- **§3's treatment of clustered CIs, in the small-N regime only.** §3 decided "disclosed, not
  corrected"; the cluster-bootstrap consequence below corrects it at small N. §3's disclosure
  requirement is otherwise untouched and still applies wherever r_s is reported.

Nothing else in the pre-registration is rewritten. Both supersessions are dated amendments to
a frozen document, not edits of it: `DECISION_unit_of_analysis.md` is byte-identical to the
file Gate 2 froze, and `tools/check_claims.py` fails the build if it is not.

Before this amendment was staged, the implementer read the full pre-registration and reported
the conflicts rather than resolving them; those reports, and the human's rulings on them, are
in the verification note below.

---

<!-- implementer-note-start -->

## Implementer's verification note — 2026-08-19, added while staging, not part of either ruling

This section is the implementer's, not the author's. The rulings below are **unedited** except for
the corrections listed under (A). Everything in (B) and (C) is reported for the human gate to
decide, exactly as the preamble above requires ("a conflict is reported to the human, not resolved
silently"). **Nothing here has been applied to the rulings.**

**Figures in this note live in tables only.** The prose below references a figure by its tag and
never restates its value. This is enforced, not merely intended: `tools/check_claims.py` fails the
build on any **digit** appearing in this note outside a table row, a quoted block, or a short list
of declared patterns (section marks, dates, gate and phase and ruling references, workpackage
names, and the digest line). The enforcement is digit-based, so it is a floor and not a ceiling —
a figure spelled out in words would pass it, and writing one is still a breach of the rule.
Review cycles repeatedly repaired figure-staleness at the sites a reviewer happened to name, and
each time the same defect survived one copy away; a rule about where figures may live closes the
class rather than the instance.

### (A) Corrections made to the rulings against the committed output

The committed output is authoritative.

| tag | the draft said | the committed output says | reference cell |
|---|---|---|---|
| `fix-declaration-rate` | declares agreement "~41% of the time" | `0.417`, so ~42% | Table E, row `30`, column `true 0.90` |
| `fix-central-tendency` | "median observed ≈ 0.89 at true 0.90" | `0.884`, and it is a **mean** | Table C, row `N= 30`, column `mean r_s` |
| `fix-false-claim-rate` | "~3% at true 0.80" | `0.035`, so ~3.5% | Table E, row `30`, column `true 0.80` |

One further correction is not a figure: Ruling B's parenthetical claim about what CI verifies was
narrowed to what the check actually does.

The script reports no median at any N, so the draft's central-tendency figure
(`fix-central-tendency`) could not be checked and was replaced by the statistic the script does
report. If the median is the intended quantity, the script must print it — a change to
`rs_power.py` and its committed output, not to this document.

### Figures this note reads out of the committed output

Every row below is pinned by `tools/check_claims.py` to the cell it names, in both directions.

| tag | reference cell | value |
|---|---|---|
| `ci-lower-at-floor` | Table A, row `15`, column `lower` | `0.665` |
| `N-needed-at-agreement` | Table B, row `observed r_s = 0.90` | `N >= 18` |
| `sd-observed-smallest-N` | Table C, row `N= 10`, column `sd(z)` | `0.592` |
| `sd-analytic-smallest-N` | Table C, row `N= 10`, column `(analytic)` | `0.448` |
| `sd-observed-next-N` | Table C, row `N= 15`, column `sd(z)` | `0.321` |
| `sd-analytic-next-N` | Table C, row `N= 15`, column `(analytic)` | `0.342` |
| `neff-no-clustering` | Table D, row `10 blots x 3, ICC 0.0`, column `N_eff` | `34.0` |
| `neff-mid-icc` | Table D, row `10 blots x 3, ICC 0.5`, column `N_eff` | `26.8` |
| `neff-few-blots` | Table D, row `6 blots x 3, ICC 0.5`, column `N_eff` | `15.9` |
| `neff-more-ratios-fewer-blots` | Table D, row `8 blots x 4, ICC 0.5`, column `N_eff` | `26.0` |
| `neff-more-blots` | Table D, row `12 blots x 3, ICC 0.5`, column `N_eff` | `31.5` |

### Arithmetic this note derives, which CI does **not** check

Derivations are review findings, not build failures. The inputs are pinned; these results are not.

| tag | derived from | value |
|---|---|---|
| `control-inflation` | `neff-no-clustering` against that row's nominal N | runs about 13% high |
| `retention-against-control` | `neff-mid-icc` over `neff-no-clustering` | 0.79, i.e. a loss of 21% |
| `retention-flat-in-blots` | `neff-few-blots`, `neff-mid-icc` and `neff-more-blots`, each over its own nominal N | 88%, 89%, 88% (the last is 87.5%, rounded up) |
| `verdict-gap` | `N-needed-at-agreement` against §5's descriptive-only floor | N of 15, 16 and 17 — short by three |

**What CI checks, exactly.** `tools/check_claims.py` re-checks every figure the rulings quote and
every figure the tables above record, in the row and column of `rs_power_expected.txt` each was
read from, and fails if either side changes or if a reference row is duplicated. It does **not**
check the rounding of a value into prose, nor the derived arithmetic in the table above. Ruling B's
parenthetical originally claimed CI re-verifies the figures without qualification; it is narrowed
below to what the check actually does.

### (B) Claims the committed output does not support

Each is arithmetic from the same file.

**(B-a) — the stop rule was not "confirmed with margin"; it was short by the band `verdict-gap`
names.** As drafted, headline one claimed the descriptive-only cutoff was confirmed with margin.
Table B says an observed r_s at the agreement threshold needs `N-needed-at-agreement` to exclude
the lower threshold, while Table A puts the CI lower bound at the cutoff as drafted at
`ci-lower-at-floor` — below that threshold. So across the band `verdict-gap` names, the
pre-registration permitted a full threshold verdict the CI could not support.

**Resolved by R3.** The human raised the cutoff to the N Table B requires, before any measurement,
on the authority NOTES.md's own deferral gives for a pre-run revision. Headline one is rewritten
accordingly and the gap is closed by moving the boundary rather than by disclosing it. The finding
stays recorded here because the draft that reached the gate contained it.

**(B-b) — "clustering costs less than feared" compares against the wrong baseline.** The claim
rests on comparing effective N to *nominal* N. Table D's first row is the ICC-zero control, where
the same estimator returns `neff-no-clustering` — that is, it runs high even with no clustering at
all (`control-inflation`). Measured against its own zero-clustering calibration rather than against
nominal, the loss at the middle ICC is `retention-against-control`. The control row is not quoted
in the ruling.

**(B-c) — "blots drive power, not ratios" is true, but not for the reason given.** The few-blot row
(`neff-few-blots`) is weak mostly because that design's total N is small, not because of its blot
count: retention is flat across the number of blots at a fixed number of ratios per blot
(`retention-flat-in-blots`). The claim's real support is the row with more ratios spread over fewer
blots (`neff-more-ratios-fewer-blots`), which carries more ratios than the middle-ICC design
(`neff-mid-icc`) and yields *less* effective N. Worth quoting that row instead.

### (C) Conflicts with the frozen pre-registration

The preamble states this amendment "supplements, does not supersede: no section of the
pre-registration is rewritten". Two consequences do rewrite one.

**(C-a) — the blot-minimum consequence versus §5.** §5 reads:

> DECISION: collection stops at 30 ratios or 10 blots, whichever comes first.

Under that rule, reaching the ratio target from fewer blots ends collection. The consequence turns
that ceiling into a floor. Either the preamble's claim or the consequence had to change.

**Resolved by R2.** The preamble's claim changed: "supplements, does not supersede" is withdrawn,
and the header now names §5 as superseded.

**(C-b) — the cluster-bootstrap consequence versus §3.** §3 is a DECISION: *"disclosed, not
corrected"*. A cluster bootstrap over blots is a correction for within-blot non-independence, so
the consequence supersedes §3 in the small-N regime it names, while the disclosure consequence
immediately after it says the caveat "stays … it does not discharge" the disclosure. Two further
points, both from the committed output. First, the evidence the consequence quotes
(`sd-observed-smallest-N` against `sd-analytic-smallest-N`) is Table C, the **independent
observations** table: no clustered design was simulated at that N, and `rs_power.py` simulates no
bootstrap anywhere. Second, the boundary the consequence names is not what the output shows — at
the next N up, the observed spread (`sd-observed-next-N`) is already *below* the analytic
(`sd-analytic-next-N`), the formula erring the safe way. Only the smallest-N row shows the stated
concern, and the band between the consequence's two boundaries is left unassigned.

**Partly resolved by R2.** The header now names §3's small-N treatment as superseded, so the
contradiction between the consequence and the preamble is gone. The other two points stand as
recorded: the evidence cited for the bootstrap is from the independent-observations table and no
bootstrap is simulated anywhere, and the boundary the consequence names is not the one the output
supports. Both remain open questions about the consequence's *content*, which R2 did not reach.

**(C-c) — Ruling B's stated reason for not revising the thresholds contradicts NOTES.md.** Ruling B
says "revision after a power calculation is the fitting Gate 1 ruling 8 exists to prevent".
NOTES.md, under "Deferred to Gate 2", says the opposite about revision *before* the run, on this
exact question:

> **Whether Spearman at the expected N can discriminate 0.9 from 0.7 at all.** … This must be
> checked once N is known. If it cannot discriminate, **the thresholds are revised before the run,
> not after**; revising them afterwards would be exactly the fitting Gate 1 ruling 8 exists to
> prevent.

So the record already authorises a pre-run revision, and this power calculation is that check, run
before any measurement. The conclusion may still be right — the thresholds may not need revising —
but the reason given for it is not the one the record supports, and (B-a) shows there is something
to revise.

**Resolved by R3.** Ruling B's reason is rewritten along the line this finding drew: post-hoc
revision is the fitting Gate 1 ruling 8 forbids; pre-run revision on measured power is what
NOTES.md prescribes, and it is applied to the sample-size floor while the three agreement
thresholds stand.

### On the W0 conflict check itself

The implementer was directed to §1's rationale paragraph (discriminating power) and §3's decision
paragraph (CI optimism), and told to STOP if either already *fixes* an answer to a question this
amendment closes. §1 says two things, and both matter:

> Rationale: blot-level N (8–10 reachable blots) is below Spearman's discriminating power — the
> Gate 1 deferred question "can Spearman discriminate at expected N" answers itself as "no" at
> that N. Ratio-level N≈30 is reachable within the time budget and is testable against the
> pre-registered thresholds (>=0.9 / 0.7–0.9 / <0.7).

The first sentence answers "no" for the blot-level unit §1 is *rejecting*. The second asserts,
**affirmatively and for the unit §1 adopts**, that the ratio-level target "is testable against the
pre-registered thresholds". An earlier draft of this note quoted only the first sentence and called
§1 silent on the adopted unit; that was incomplete, and the second sentence is the one closest to
fixing an answer.

W0 proceeded anyway, on this reading: §1 asserts testability without a power calculation and
without saying at which N testability begins, which is the question left open and the question
`rs_power.py` measures. The measurement **agrees** with §1 at the pre-registered target — the N
Table B requires (`N-needed-at-agreement`) is below that target — so there is no contradiction to
report, and the amendment supplies the quantity §1 asserted without. Where the measurement bites is
not §1 but §5: (B-a) shows the band `verdict-gap` names permits a verdict the CI cannot support.
**If the human reads §1's second sentence as fixing the answer rather than asserting it, this is
the point at which W0 should have stopped, and the ruling is theirs to make.**

**Ruled by R1: the continuation is ratified retroactively.** §1's testability claim is confirmed
by the computation at the pre-registered target, so no substantive conflict exists. The breach of
the stop gate is **not erased** — it stays recorded above, and this paragraph is the ruling on it,
not its deletion. A gate that was crossed stays crossed in the record even when crossing it turns
out to have been harmless; the alternative is a record that only ever shows gates being honoured.

§3 discloses a caveat without quantifying it, which the kickoff states is not a conflict. The
conflicts in (C) run the other way — the amendment against the frozen text — and are reported
rather than resolved.

<!-- implementer-note-end -->

---

This closes the two items NOTES.md records under "Deferred to Gate 2" that
`DECISION_unit_of_analysis.md` did not close: **PLAN.md's `mean |Δ|`** (which quantity
is meant, and whether it carries a criterion) and **whether Spearman at the expected N
can discriminate 0.9 from 0.7** (deferred as a question; answered there only by the
qualitative caveat that clustered CIs are optimistic, without a power calculation).

---

## Ruling A — `mean |Δ|` is a difference of normalized ratios, reported descriptively, with no threshold

**`mean |Δ|` means the mean absolute difference of normalized ratios**, blotquant vs
ImageJ, over the same ratio set that enters r_s. Not absolute intensities: those are
excluded by DEBT S11 and by Gate 1 ruling 8's own reasoning — two tools with different
aperture conventions would be measuring the conventions, not the methods. Normalization
divides the per-image scale factor out, so a difference of ratios survives that argument.

**No threshold is attached.** A criterion on `mean |Δ|` would require a prior tolerance
for how far two aperture conventions may disagree, and no such prior exists anywhere in
this project. A number invented now would be exactly the class of unjustified constant
the debt register exists to catch. The verdict remains on r_s alone (Gate 1 ruling 8).

**Bland–Altman on the log scale is reported alongside it.** Ratios are multiplicative:
|Δ| = 0.3 means different things at a ratio of 0.5 and a ratio of 5.0. For each paired
ratio, `d = log2(ratio_blotquant / ratio_imagej)`; report `mean(d)` (systematic
fold-scale bias between the tools) and `sd(d)` (limits of agreement). Spearman sees
monotonicity and is blind to systematic bias; Bland–Altman sees exactly that. Both are
descriptive, no thresholds.

**PLAN.md consequence.** PLAN.md's Phase 3 wording "correlation + mean |Δ|" is now
specified rather than amended — the reported quantity is literally a mean |Δ|, of the
quantity this ruling names. The addition of the Bland–Altman pair is a recorded
deviation from PLAN.md's reporting list: log it in DEBT P2's convention.

## Ruling B — the thresholds stand; their operating characteristics are measured, committed, and pinned

The discriminating-power question is answered by measurement, not caveat. The
instrument is `tools/stats/rs_power.py` — deterministic, seed 20260819 — whose full
output is committed as `tools/stats/rs_power_expected.txt` and re-verified in CI by
diff, the same pinning discipline as `check_claims.py`. Headline results, quoted from
its output (CI re-checks each figure below against the cell of that reference it was read
from; it does not check the rounding):

1. **An observed r_s = 0.90 first excludes 0.70 (95% Bonett–Wright CI) at N ≥ 18**;
   an observed 0.85 requires N ≥ 38. The pre-registered target of N ≈ 30 is therefore
   inside the working range. **§5's descriptive-only cutoff moves from N < 15 to N < 18**,
   by human ruling of 2026-08-19 and on this measurement: at N of 15, 16 or 17 the
   pre-registration permitted a full threshold verdict whose CI could not exclude 0.70,
   and that gap is closed by aligning the cutoff with the measured bound rather than by
   disclosing it. This is a **pre-run** revision on measured power, which NOTES.md's own
   deferral prescribes; see headline 3 for why that is the opposite of the fitting Gate 1
   ruling 8 forbids.
2. **Clustering costs less than feared.** 10 blots × 3 ratios at within-blot ICC 0.5
   gives effective N ≈ 27 of nominal 30; at ICC 0.7, ≈ 20. But 6 blots × 3 gives
   effective N ≈ 16 — the number of *blots* drives power, not the number of ratios.
3. **The point-estimate verdict rule is conservative, and that is its accepted
   character.** With truth exactly at 0.90 and N = 30, the rule declares agreement
   (observed ≥ 0.90) only ~42% of the time (0.417), because small-N Spearman is biased
   low (mean observed r_s 0.884 at true 0.90, N = 30). Conversely it almost never
   declares agreement that is not there: ~0.3% at true 0.70, ~3.5% at true 0.80. The error
   direction is false modesty, not false confidence — the right direction for this
   project. **The three agreement thresholds themselves (>=0.9 / 0.7–0.9 / <0.7) are not
   revised**, and the distinction matters: what Gate 1 ruling 8 exists to prevent is
   revision *after* seeing a result, which is fitting. Revising the *sample-size floor*
   before any measurement, on a power calculation, is the mechanism NOTES.md's "Deferred to
   Gate 2" prescribes in terms — "If it cannot discriminate, the thresholds are revised
   before the run, not after". Headline 1 is that revision. It moves the floor at which a
   verdict may be issued; it does not move the verdict boundaries, and it was made before
   the first real ratio was measured.

**Four operational consequences, in force from the first ImageJ run:**

- **A 95% CI accompanies the point estimate wherever r_s is reported**, including the
  README line. If the CI crosses a band boundary, the verdict word is prefixed
  "provisional" — wording fixed here, before any data: *"provisional agreement"*,
  *"provisional partial agreement"*, *"provisionally not corroborated"*.
- **Stop rule clarified: a minimum of 10 blots**, not 30 ratios from fewer blots.
  Headline result 2 is the reason. The ratio-level descriptive-only rule sits beneath this
  and is **not** unchanged: headline 1 moves it from N < 15 to N < 18.
- **At N ≤ 15 the CI is computed by cluster bootstrap over blots**, not by formula:
  the Monte Carlo shows the analytic SE understates the spread at small N
  (sd(z) observed 0.59 vs analytic 0.45 at N = 10); at N ≥ 20 the discrepancy
  vanishes and the Bonett–Wright formula is used.
- **The clustered-CI caveat already recorded in `DECISION_unit_of_analysis.md`
  (CI optimistic, DEBT entry on merge) stays** — this amendment quantifies it
  (headline result 2), it does not discharge the disclosure.

## What this amendment does not do

It selects no parameter, touches no code path, and reads no real-blot pixel. It fixes
reporting semantics and statistical machinery before the first measurement, which is
the only time they can be fixed without fitting.
