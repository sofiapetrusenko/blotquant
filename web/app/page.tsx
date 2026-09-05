import Link from 'next/link';

import { RoiOverlay } from '@/components/RoiOverlay';
import { VerdictBadge } from '@/components/VerdictBadge';
import { readGalleryDisplay, readGalleryIndex, readGalleryResult } from '@/lib/gallery';
import type { GalleryEntry } from '@/lib/gallery';
import type { Roi } from '@/lib/result';
import { BLOCKED, laneVerdicts } from '@/lib/verdict';

/**
 * The gallery.
 *
 * Cards in the manifest's order, because the order is a decision the human made in
 * `web/gallery.manifest.yaml` and not an alphabetisation. The first blocked card is rendered
 * larger, which is the masthead's claim made visible: the thing this tool does that ImageJ does
 * not is refuse, so the refusal is the largest thing on the page.
 *
 * Each card needs the lane rectangle to outline, and the rectangle lives in that card's own
 * `result.json` -- the measurement record -- not in `index.json`, which stays the fixed
 * `{id, title, verdict, blocked_reason?, flag_count}` the build writes. This server component
 * reads each document at build time and passes the rectangle down; the browser gets HTML.
 */

const MASTHEAD =
  'blotquant — QC-first western blot densitometry. ' +
  'Warnings can be ignored; refusal cannot.';

const FOOTER = 'Gallery results were computed by the blotquant API on the images shown.';

interface Card {
  entry: GalleryEntry;
  /**
   * The lane's QC flags, in verdict order, read from the card's own document at build time.
   *
   * `index.json` carries only `flag_count`, and its shape is ruled. But a count is what
   * `lib/verdict-sentence.ts` refuses to print in place of the names -- "3 QC flags" tells a
   * reader nothing they can act on -- and the names are one file away in the stored document
   * this server component already reads. So the card names them without `index.json` gaining a
   * field.
   */
  qcFlags: readonly string[];
  laneId: string;
  roi: Roi;
  width: number;
  height: number;
  /** The one card rendered larger: the first blocked one in manifest order. */
  large: boolean;
}

/**
 * Return the cards in manifest order, each with the lane rectangle read from its document.
 *
 * Throws rather than skipping a card whose document holds no lane: an index that names a card
 * the gallery cannot draw is an inconsistent build, and a silently shorter gallery would look
 * like a shorter selection.
 */
function readCards(): Card[] {
  const entries = readGalleryIndex();
  const firstBlocked = entries.find((entry) => entry.verdict === BLOCKED);
  return entries.map((entry) => {
    const result = readGalleryResult(entry.id);
    const lane = result.lanes[0];
    if (lane === undefined) {
      throw new Error(
        `gallery card ${JSON.stringify(entry.id)} stores a document with no lane; a card is ` +
          `one caller-supplied ROI with its verdict (Ruling 2, 2026-08-25)`,
      );
    }
    // The picture's own dimensions, from the block that describes the picture. The source's
    // dimensions are equal to them (tests/test_gallery_build.py asserts it), but one number
    // should have one home, and the overlay is drawn on the derivative.
    const display = readGalleryDisplay(entry.id);
    const verdict = laneVerdicts(result as unknown as Record<string, unknown>)[0];
    if (verdict === undefined) {
      throw new Error(
        `no verdict was derived for gallery card ${JSON.stringify(entry.id)}, although its ` +
          `document lists a lane`,
      );
    }
    return {
      entry,
      qcFlags: verdict.qc_flags,
      laneId: lane.lane_id,
      roi: lane.roi,
      width: display.width_px,
      height: display.height_px,
      large: entry.id === firstBlocked?.id,
    };
  });
}

/**
 * Return the one line a card says under its badge.
 *
 * A blocked card names its reason, because "0 flags" is true of a blocked lane and would be the
 * most misleading thing the card could say: nothing was flagged precisely because nothing
 * survived to carry a number.
 */
function cardCaption(card: Card): string {
  const { entry, qcFlags } = card;
  if (entry.verdict === BLOCKED) {
    if (entry.blocked_reason === undefined) {
      throw new Error(
        `gallery card ${JSON.stringify(entry.id)} is blocked and index.json records no ` +
          `blocked_reason; the build writes one for every blocked card`,
      );
    }
    return entry.blocked_reason;
  }
  if (qcFlags.length === 0) {
    return 'no QC flags';
  }
  // The names, in verdict order. A count would be the one thing a reader cannot act on.
  return qcFlags.join(', ');
}

export default function GalleryPage(): React.ReactElement {
  const cards = readCards();
  return (
    <main className="mx-auto max-w-6xl px-6 py-10">
      <h1 className="max-w-3xl text-lg leading-relaxed">
        <span className="font-semibold">blotquant</span>
        {MASTHEAD.slice('blotquant'.length)}
      </h1>
      <p className="mt-4 text-sm">
        <Link
          href="/analyze/"
          className="inline-block border border-[var(--color-accent)] px-3 py-1.5 font-medium text-[var(--color-accent)]"
        >
          Analyse your own image →
        </Link>
      </p>

      <ul className="mt-10 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {cards.map((card) => (
          <li
            key={card.entry.id}
            className={card.large ? 'sm:col-span-2 lg:col-span-2' : undefined}
          >
            <Link
              href={`/result/${card.entry.id}/`}
              className="block h-full border border-[var(--color-hairline)] bg-[var(--color-surface-raised)]"
            >
              <RoiOverlay
                src={`/gallery/${card.entry.id}/display.png`}
                alt={`Display rendering of ${card.entry.title}, with lane ${card.laneId} outlined.`}
                width={card.width}
                height={card.height}
                shapes={[
                  { id: card.laneId, roi: card.roi, kind: 'lane', label: card.laneId },
                ]}
                className="border-b border-[var(--color-hairline)]"
              />
              <div className="p-3">
                <h2 className={card.large ? 'text-base font-semibold' : 'text-sm font-semibold'}>
                  {card.entry.title}
                </h2>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <VerdictBadge verdict={card.entry.verdict} size={card.large ? 'header' : 'card'} />
                  <span className="tabular text-xs text-[var(--color-ink-muted)]">
                    {cardCaption(card)}
                  </span>
                </div>
              </div>
            </Link>
          </li>
        ))}
      </ul>

      <footer className="mt-12 border-t border-[var(--color-hairline)] pt-4 text-xs text-[var(--color-ink-muted)]">
        {FOOTER}
      </footer>
    </main>
  );
}
