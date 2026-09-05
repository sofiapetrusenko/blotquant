import { verdictDisplay } from '@/lib/verdict-display';

/**
 * A verdict, shown as colour **and** glyph **and** word -- never fewer than all three.
 *
 * The one component every screen renders a verdict through, so that the gallery card, the card
 * header and the per-lane cards on `/analyze` cannot encode the same verdict differently. The
 * triple itself is defined once in `lib/verdict-display.ts` and nothing here restates it: this
 * file chooses type sizes and spacing and nothing else.
 *
 * The glyph is `aria-hidden` and the word is the accessible name, so a screen reader says
 * "Blocked" rather than "black square Blocked". The colour is applied to the text and the
 * hairline, never as a filled chip: a saturated fill behind small text is the fastest way to
 * lose contrast, and this palette is chosen for being distinguishable rather than for being
 * readable at 4.5:1 against itself.
 */

/** The two places a badge appears: inside a gallery card, and in a result header. */
export type VerdictBadgeSize = 'card' | 'header';

const SIZE_CLASSES: Readonly<Record<VerdictBadgeSize, string>> = {
  card: 'gap-1.5 px-2 py-0.5 text-xs',
  header: 'gap-2 px-3 py-1 text-base',
};

export function VerdictBadge({
  verdict,
  size = 'card',
}: {
  verdict: string;
  size?: VerdictBadgeSize;
}): React.ReactElement {
  const display = verdictDisplay(verdict);
  return (
    <span
      className={`inline-flex items-center border font-medium whitespace-nowrap ${SIZE_CLASSES[size]}`}
      style={{ color: display.color, borderColor: display.color }}
      title={display.meaning}
    >
      <span aria-hidden="true">{display.glyph}</span>
      <span>{display.word}</span>
    </span>
  );
}
