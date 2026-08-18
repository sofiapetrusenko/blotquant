# Phase 3 — unit of analysis, minimum N, QC-excluded ratios

Decided: 2026-08-18, before any real image was measured.
Decided by: Sofia. Status: pre-registered; frozen once the first image is
downloaded. Amendments after that point require a dated entry below stating
what changed and why, and are themselves part of the record.

## 1. Unit of analysis

DECISION: ratio (one normalized ratio = one point in the ImageJ comparison).

Rationale: blot-level N (8–10 reachable blots) is below Spearman's
discriminating power — the Gate 1 deferred question "can Spearman discriminate
at expected N" answers itself as "no" at that N. Ratio-level N≈30 is reachable
within the time budget and is testable against the pre-registered thresholds
(>=0.9 / 0.7–0.9 / <0.7).

## 2. Target N, source, and ratio definition

DECISION: 30 ratios from 8–10 blots with >=3 usable lanes each.

Ratio definition (fixed now, before any measurement):
- Per blot, the reference lane/band is designated at image-selection time,
  from the figure caption alone, before any measurement is run.
- Each remaining usable lane contributes exactly one ratio to that reference:
  a blot with L usable lanes yields L−1 ratios. No other pairings enter N.
- Normalization mode per blot is likewise fixed at selection time from the
  caption: a stated loading-control band -> housekeeping; stated stain-based
  loading (Ponceau, total protein stain) -> total_protein.
- A blot whose caption states neither is REJECTED at selection, not decided
  at measurement time. Guessing the loading control from the data is
  forbidden (consistent with Phase 2 Ruling 2).

Arithmetic check: 8–10 blots × (L−1) >= 2 ratios each gives 16–30+; the
stopping rule below caps collection, not the definition.

## 3. Non-independence of ratios within a blot

DECISION: disclosed, not corrected. Ratios within one blot share a background
estimate and an exposure; effective N is below nominal N; any reported
confidence interval is optimistic. This becomes a DEBT entry when merged into
the repo. The reported r itself is unaffected; only its uncertainty is
understated, and that is stated wherever r is stated.

Agreed: Sofia, 2026-08-18.

## 4. QC-excluded ratios in the ImageJ comparison

DECISION: excluded from the correlation; reported as a separate count
("n excluded by QC", with the flags that excluded them).

Rationale: the correlation measures agreement on numbers the tool certifies.
Including numbers the tool itself refuses to certify would mix two populations
and answer neither question. A secondary correlation on flagged ratios was
considered and rejected: at the expected flagged n (likely <10) it would be
uninterpretable noise, and reporting an uninterpretable number invites
misreading it.

Failure mode I accept: QC may leave fewer than 15 surviving ratios. I will
report that count as a finding in its own right ("QC excluded X of Y ratios
from N real blots") and will not relax any QC threshold to save N. QC
thresholds are frozen as shipped (Phase 3 Gate 1); nothing here reopens them.

## 5. Stopping rule

DECISION: collection stops at 30 ratios or 10 blots, whichever comes first.

If fewer than 15 ratios survive QC, the ImageJ correlation is reported with
the explicit caveat that N is below the pre-registered minimum and the
agreement claim is downgraded to descriptive (no threshold verdict). The
survival count itself is reported as a primary finding. Criteria are not
relaxed to reach 15.

Agreed: Sofia, 2026-08-18.
## 6. Source image format (recorded before download, 2026-08-18)

Every candidate figure in the Gate 2 shortlist is distributed as JPEG. The
`lossy_format` QC flag is therefore expected to fire on the entire real-blot
set. This is reported as a finding about the OA literature — published figures
do not preserve the acquisition format the measurement should be made on — and
not treated as grounds to suppress or exempt the flag.

## 7. Pseudocoloured blots (recorded before download, 2026-08-18)

DECISION: a blot panel whose pixels are not single-channel is REJECTED at
selection; it is not converted to greyscale.

Scope: the rule applies to the CROPPED blot panel, not to the source figure.
Published figures are montages — coloured bar charts and MW ladders share a
file with greyscale blot panels — so a whole-figure colour test would reject
nearly every candidate while saying nothing about the measured region. The
download step records a whole-figure colour fraction as a screening aid only.

Rationale: choosing an RGB→grey conversion would introduce a measurement
parameter selected without data to select it. Phase 3's only reference is the
ImageJ comparison, and ImageJ applies its own conversion; part of any observed
disagreement would then be attributable to the conversion rather than to the
method, with no third reference available to separate the two contributions.

Failure mode I accept: fluorescence-detection blots are common, so this may
remove a substantial share of candidates and push N down. That count is
reported ("X of Y candidate panels rejected as pseudocoloured") rather than
recovered by converting.

Carried to backlog, not Phase 3: blotquant should detect non-single-channel
input and BLOCK with an explanatory message directing the user to the
scanner's single-channel export, consistent with the three-state QC model
(pass | flagged | blocked). Silently converting and returning a number is the
failure mode this project exists to refuse. -> DEBT entry.

## 8. Gate 2 image review — ratified 2026-08-18, after download, before any crop

(a) FINDING: every total-protein stain panel in the downloaded set (Ponceau,
2 blots) is reproduced in colour. Under §7 these reference panels are not
single-channel and are excluded; no per-channel extraction is applied. The
real-blot set is therefore HOUSEKEEPING-ONLY, and the README will state that
total_protein agreement was not measurable on published figures for this
reason. Feeds the non_grayscale_source blocking-flag DEBT entry.

(b) Removed at image review: PMC13135388 Figure 1 (reference panel
pseudocoloured), PMC13019689 Figures 1 and 3 (Ponceau reference in colour),
PMC13138064 Figure 4 (2 lanes, below the >=3 minimum — visible only in the
image, not the caption). Retained with restriction: PMC13135388 Figure 4,
panels TIGAR + vinculin only (adjacent Parkin panel is pseudocoloured; §7
applies to the cropped panel). Retained: PMC12956003 Figure 2 (Pon S panel is
coloured but is not the designated reference; α-tubulin is, and is greyscale).

(c) Borderline-resolution rule: no figure is excluded by eye for band size.
All remaining figures are cropped; the crop's measured pixel height decides
against the pre-registered >=15 px band-height minimum. Selection by
measurement, not by impression.

## 9. Crop rule — fixed before the first crop, 2026-08-18

- One crop = one blot panel: the rectangle bounds the panel's lanes, full lane
  height, plus >=10 px background margin on every side where the montage
  allows it.
- The crop must include the panel's designated reference band row if the
  reference shares the panel; if the reference is a separate strip (e.g.
  loading-control row below), it is cropped as part of the same rectangle
  whenever they are vertically contiguous in the figure, else as a second
  rectangle recorded against the same blot_id.
- Annotation overlays (arrows, kDa markers, labels) may remain if they sit
  outside lane boundaries; a crop where annotation touches a measured lane is
  rejected.
- No rotation, no rescaling, no level/contrast adjustment, no resampling.
  Export as PNG (lossless container; the source pixels are already JPEG and
  are not re-encoded into further loss).
- Every crop records: parent file, parent sha256, crop rectangle is NOT
  recorded manually — the crop file's own sha256 and pixel size are recorded
  at creation by the logging step, and the parent linkage makes the crop
  auditable against its source.
- ImageJ runs on the SAME crop file, byte-identical, never on its own crop.

## 10. Gate 2 CLOSED — 2026-08-18

Real-image list approved by Sofia. The approved set is exactly the rows of
crops/crop_log.csv at this timestamp; provenance (DOI, licence, sha256 of crop
and parent) recorded per image in images/provenance.md and crops/crop_log.csv.
Housekeeping-only per §8(a). Borderline crops enter measurement; the >=15 px
band-height minimum and lane-width reality decide there, per §8(c).

## 11. §7 verified mechanically on crops — 2026-08-18

Colour fraction measured on all 19 crops after Gate 2 closure: 13 crops at
0.0000, 5 between 0.0034 and 0.0068 (labels and kDa markers, not bands),
maximum 0.0158 (PMC13135388_Figure4__E-Vinculin, adjacent to the
pseudocoloured Parkin panel). All below the 0.02 screening threshold. The §7
single-channel rule is therefore confirmed by measurement, not by eye.

### 11a. Amendment — 2026-08-18, after §11

PMC13135388_Figure4__E-Vinculin was re-cropped: the original rectangle
included the lower edge of the adjacent pseudocoloured Parkin panel
(colour fraction 0.0158). The band region was unaffected, but that edge sits
in the background region the pipeline uses for background estimation. The
re-crop excludes it; measured colour fraction is now 0.0000 and the crop
sha256 in crop_log.csv is updated accordingly. §11's stated maximum of 0.0158
refers to the superseded crop; the current set maximum is 0.0068.
