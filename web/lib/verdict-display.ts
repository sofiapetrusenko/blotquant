import { BLOCKED, FLAGGED, PASS, type Verdict } from '@/lib/verdict';

/**
 * How a verdict is shown: one colour, one glyph, one word, defined once.
 *
 * **Colour alone never carries the verdict.** Around one in twelve men has a red-green colour
 * deficiency, and vermilion against bluish green is precisely the pair that goes. So each verdict
 * ships a glyph and a word beside its colour, every screen renders all three, and a reader who
 * sees no colour at all still reads the verdict. The palette is Okabe--Ito, which is chosen for
 * being distinguishable under the common deficiencies rather than for looking scientific.
 *
 * This module is the only place the triple is written down. A screen that encoded it again -- a
 * hard-coded hex in a card, a glyph chosen inline -- could drift from the others, and a gallery
 * whose blocked cards were two different oranges would be telling the reader something untrue
 * about the data. Wave 2's screens import from here.
 *
 * The colours are also declared as CSS custom properties in `app/globals.css` so Tailwind
 * utilities can reach them; `cssVariable` below names the property for each verdict, and the two
 * are kept in step by `verdict-display.test.ts`.
 */
export interface VerdictDisplay {
  /** The verdict this describes. */
  verdict: Verdict;
  /** The word shown to the reader. Sentence case: it is a label, not a shout. */
  word: string;
  /** A shape that differs from the others at any size and in any colour. */
  glyph: string;
  /** Okabe--Ito hex, the value `cssVariable` resolves to. */
  color: string;
  /** The CSS custom property carrying `color`, for utilities and inline styles. */
  cssVariable: string;
  /**
   * What the verdict means, in one sentence, quoting the ruled definition rather than
   * paraphrasing it. Screens use it for the title attribute and for assistive text.
   */
  meaning: string;
}

export const VERDICT_DISPLAY: Readonly<Record<Verdict, VerdictDisplay>> = {
  [BLOCKED]: {
    verdict: BLOCKED,
    word: 'Blocked',
    glyph: '■',
    color: '#D55E00',
    cssVariable: '--color-verdict-blocked',
    meaning: 'No number: every ratio in this lane was excluded, or the lane emitted none.',
  },
  [FLAGGED]: {
    verdict: FLAGGED,
    word: 'Flagged',
    glyph: '▲',
    color: '#E69F00',
    cssVariable: '--color-verdict-flagged',
    meaning: 'QC flags present. The number is reported and annotated with them.',
  },
  [PASS]: {
    verdict: PASS,
    word: 'Pass',
    glyph: '●',
    color: '#009E73',
    cssVariable: '--color-verdict-pass',
    meaning: 'No QC flags, and every ratio in this lane was computed.',
  },
};

/**
 * Return how to show `verdict`, or throw if it is not one of the three ruled classes.
 *
 * Throws rather than falling back to a neutral badge: a verdict this vocabulary does not know is
 * a document written under a mapping this build does not implement, and rendering it as an
 * unremarkable grey chip would present an unknown state as an ordinary one.
 */
export function verdictDisplay(verdict: string): VerdictDisplay {
  const display = (VERDICT_DISPLAY as Record<string, VerdictDisplay | undefined>)[verdict];
  if (display === undefined) {
    throw new Error(
      `no display is defined for the verdict ${JSON.stringify(verdict)}; the ruled vocabulary ` +
        `is ${JSON.stringify(Object.keys(VERDICT_DISPLAY))} (pre-registration of 2026-08-25, §2.3)`,
    );
  }
  return display;
}
