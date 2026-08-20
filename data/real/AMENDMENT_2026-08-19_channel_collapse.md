# Amendment 2026-08-19 — §7 channel collapse on the Gate 2 crop set

**Status: DRAFT — not in force until ratified by the human gate.**

Amends `data/real/DECISION_unit_of_analysis.md` §7 (pseudocoloured blots). It does not touch
§7's rule; it decides how that rule applies to the 19 crops Gate 2 approved, now that the
crops have been measured against it. A dated amendment beside the frozen file, on the same
discipline as `AMENDMENT_2026-08-19_delta_and_power.md`: the pre-registration itself stays
byte-identical to the file Gate 2 froze, and `tools/check_claims.py` fails the build if it
does not.

**Ruling recorded now; implementation deferred.** This file is the decision. The loader change
it implies is next-phase work and is deliberately not in this branch — see "What this does not
do" below.

## Why this was needed

The first real-data run put the shipped pipeline on all 19 crops. All 19 were refused at load:
they are 3-channel PNGs, and the pipeline quantifies single-channel images only. The refusal is
§7 working — §7 forbids choosing an RGB→grey conversion, and the loader says so rather than
picking a channel.

§11 had verified §7 "by measurement, not by eye" using a per-pixel **colour fraction**. What
the loader tests is **channel count**, and what §7's rationale is actually about is whether a
conversion would introduce a measurement parameter. Neither is a colour fraction. A read-only
measurement of the crops was therefore taken: per crop, the maximum over pixels of
max(|R−G|, |R−B|, |G−B|), in DN against an 8-bit full scale of 255.

| group | crops | max divergence |
|---|---|---|
| byte-identical channels | 10 | 0 DN |
| divergence at or below the pre-named bound | 2 | 2 DN |
| divergence above the bound | 6 | 3 DN to 43 DN |
| real colour content | 1 | 255 DN |

The per-crop table is `runs/3b0-real/channel_divergence.txt`, produced read-only in the same
session. It is not committed: `runs/` is ignored, and a measurement in the tree that no CI step
re-measures is the class of stale claim `tools/check_claims.py` exists to catch. It is
reproducible from the committed crops.

## The ruling

**(a) Collapse is permitted only where it decides nothing.** A crop may be collapsed to one
channel where its channels are **byte-identical** (10 crops), or where its maximum per-pixel
divergence is **at or below 2 DN** (2 crops: `PMC12708318_Figure5__A1-GSDME-actin` and
`PMC12895598_Fig3__A-EMT-GAPDH`).

**The 2 DN bound was named before the divergence table was measured** — the human fixed it in
the session record of 2026-08-19, in advance of seeing any per-crop value — and **it is not
revised now that the values are visible.** This is the load-bearing sentence of the amendment.
A bound chosen after the data would be a measurement parameter selected without data to select
it, which is exactly what §7 refuses; the same bound chosen before the data is a criterion. The
distinction is not in the number, it is in the order of events, so the order is recorded here
rather than left to memory.

**(b) The collapse rule, fixed here.** Take the **green channel**. Record the operation in
result provenance, in the style `roi_source` already uses for lane origin:

```
channel_collapse: { method: green, max_divergence_dn: <measured> }
```

Green because a colour PNG derived from a greyscale scan carries the same signal in all three
planes up to codec noise, and green is the plane a JPEG chroma-subsampled encoder preserves
most faithfully. The choice is recorded rather than argued from the data: at 2 DN or below, no
plane choice can change a reported ratio by more than the aperture error the pipeline already
carries, which is why the bound and the rule are separable. `max_divergence_dn` is written into
provenance so that a reader of a result can see how close to the bound that image sat.

**(c) The six crops between 3 DN and 43 DN are EXCLUDED, with disclosure.** Their divergence is
plausibly JPEG chroma-subsampling noise. It is not *provably* so, and admitting them would
require moving a pre-named bound after seeing the data — the one move this project refuses
everywhere else. They are excluded as non-single-channel under §7, and the reason is recorded
as this rather than as a finding about the images: **the bound held, and the crops fell outside
it.**

**(d) `PMC13135388_Figure4__E-TIGAR` is rejected under §7 as it stands.** Its maximum divergence
is 255 DN — a fully saturated colour pixel. That is real colour content, not codec residue, and
§7 already rejects it. No amendment is needed for this crop; it is named here only so the
disposition of all 19 is on one page.

**(e) The consequence, stated rather than avoided.** The measurable set is **12 of 19 crops**.
Whether that yields enough surviving ratios is not decided here and is not decidable here: the
pre-registered ≥15 px band-height criterion has never been applied to a real band, because no
crop has ever loaded. **If N after that criterion falls below the pre-registered floor, the
pre-registered descriptive-only outcome applies.** The boundary in (a) was not moved to avoid
that outcome, and this sentence exists so that a later reader can check it was not.

## What this does not do

It changes no code. `pipeline/load.py` still refuses every one of the 19 crops, including the
12 this amendment admits, and it will keep refusing them until the loader implements (b). That
implementation — a loader change, its numeric tests, and the provenance field — is **next-phase
work**, recorded as a ratified deviation in `DEBT.md` rather than smuggled into this branch.
Gate 1 ruling 3 is why: what a real image reveals may falsify a decision, never select a
parameter or a code path inside the phase that saw it. This branch ships the ruling; the next
one ships the code.

It also does not re-crop, re-export or re-log anything. The 19 crops stay byte-identical to
what Gate 2 approved, and `crop_log.csv` is untouched — the collapse happens at load time, on
the way into the pipeline, not by rewriting an approved artefact.

Agreed: Sofia, 2026-08-19.
