import { BLOCKED, FLAGGED, PASS, type LaneVerdict } from '@/lib/verdict';

/**
 * The one sentence a screen says about a lane verdict.
 *
 * **Derived from the verdict record alone**, which is what makes it safe to show beside a
 * badge: it reads no pixels, no image-level flags and no ratio values, so the sentence and the
 * badge can never disagree about the same lane. `/result/[id]` and `/analyze` step 3 both call
 * this, so the two screens cannot drift into saying different things about the same document.
 *
 * The three forms are fixed, one per ruled class:
 *
 * - blocked -- `No ratio carries a number: <blocked_reason>.`
 * - flagged -- `Number reported and annotated: <flags, comma-separated in verdict order>.`
 * - pass    -- `No QC flags; every ratio computed.`
 *
 * The blocked sentence names the machine's own `blocked_reason` token rather than an English
 * gloss of it, because the token is what the document carries and what a reader will grep the
 * JSON for. The flagged sentence lists the flags in the order `laneVerdicts` produced -- the
 * vocabulary order of `BAND_QC_FLAGS`, unknown flags last -- and never a count instead of the
 * names: "3 flags" tells a reader nothing they can act on.
 */
export function verdictSentence(verdict: LaneVerdict): string {
  if (verdict.verdict === BLOCKED) {
    if (verdict.blocked_reason === null) {
      throw new Error(
        `lane ${JSON.stringify(verdict.lane_id)} is blocked with no blocked_reason; the ` +
          `derivation in lib/verdict.ts assigns one to every blocked lane, so a sentence ` +
          `cannot be written for this record without inventing the cause`,
      );
    }
    return `No ratio carries a number: ${verdict.blocked_reason}.`;
  }
  if (verdict.verdict === FLAGGED) {
    if (verdict.qc_flags.length === 0) {
      throw new Error(
        `lane ${JSON.stringify(verdict.lane_id)} is flagged with no QC flags; a flagged lane ` +
          `is by definition one a flag attached to, so this record cannot be described`,
      );
    }
    return `Number reported and annotated: ${verdict.qc_flags.join(', ')}.`;
  }
  if (verdict.verdict === PASS) {
    return 'No QC flags; every ratio computed.';
  }
  throw new Error(
    `no sentence is defined for the verdict ${JSON.stringify(verdict.verdict)}; the ruled ` +
      `vocabulary is ${JSON.stringify([PASS, FLAGGED, BLOCKED])}`,
  );
}
