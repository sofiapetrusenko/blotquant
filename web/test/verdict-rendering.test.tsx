import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import GalleryPage from '@/app/page';
import { ResultStep } from '@/app/analyze/ResultStep';
import { VerdictBadge } from '@/components/VerdictBadge';
import { readGalleryIndex } from '@/lib/gallery';
import type { Envelope, ResultDocument, StoredDisplayBlock } from '@/lib/result';
import { BLOCKED, FLAGGED, PASS, laneVerdicts, type Verdict } from '@/lib/verdict';
import { VERDICT_DISPLAY } from '@/lib/verdict-display';

/**
 * That a verdict reaches the screen at all, and that it is the document's own.
 *
 * **This file exists because of a mutation that survived.** Replacing the `verdict` prop at
 * `ResultView.tsx` and `ResultStep.tsx` with a hard-coded `"pass"` left every test in the
 * repository green: nothing anywhere asserted that a `blocked` or a `flagged` verdict is ever
 * rendered. A build that showed a green ● Pass on all three saturated, correctly-refused blocked
 * cards would have shipped. The verdict is the product, so it is pinned here in all three places
 * it appears -- the badge itself, the gallery front page, and the per-lane cards on `/analyze`.
 *
 * Colour, glyph and word are asserted **together** rather than one of them. That triple is the
 * accessibility contract of `lib/verdict-display.ts`: around one in twelve men cannot separate
 * the blocked and pass hues, so a badge that lost its glyph or its word would still look correct
 * to whoever wrote it and would be unreadable to them.
 */

const GALLERY_DIR = join(__dirname, '..', 'public', 'gallery');

/** The three ruled classes, typed, so indexing the display map stays checked. */
const ALL_VERDICTS: Verdict[] = [BLOCKED, FLAGGED, PASS];

const BLOCKED_CARD = 'pmc12895598-fig3a-l3';
const FLAGGED_CARD = 'pmc13135410-fig3b-l11';
const PASS_CARD = 'pmc13135410-fig4c-l8';

/** jsdom reports colours as `rgb(...)`; the palette is written as hex. */
function rgb(hex: string): string {
  const n = Number.parseInt(hex.slice(1), 16);
  return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
}

function card(id: string): { result: ResultDocument; display: StoredDisplayBlock } {
  return {
    result: JSON.parse(
      readFileSync(join(GALLERY_DIR, id, 'result.json'), 'utf-8'),
    ) as ResultDocument,
    display: JSON.parse(
      readFileSync(join(GALLERY_DIR, id, 'display.json'), 'utf-8'),
    ) as StoredDisplayBlock,
  };
}

function envelopeFor(id: string): Envelope {
  const { result, display } = card(id);
  return {
    result,
    display: {
      ...display,
      png_base64: readFileSync(join(GALLERY_DIR, id, 'display.png')).toString('base64'),
    },
  };
}

describe('VerdictBadge', () => {
  it.each(ALL_VERDICTS)('renders %s as colour and glyph and word', (verdict) => {
    const display = VERDICT_DISPLAY[verdict];
    render(<VerdictBadge verdict={verdict} />);

    // The word is the accessible name; the glyph is decorative beside it.
    const badge = screen.getByTitle(display.meaning);
    expect(badge.textContent).toBe(`${display.glyph}${display.word}`);
    expect(badge.style.color).toBe(rgb(display.color));
    expect(badge.style.borderColor).toBe(rgb(display.color));

    const glyph = badge.querySelector('[aria-hidden="true"]');
    expect(glyph?.textContent).toBe(display.glyph);
    expect(within(badge).getByText(display.word)).toBeVisible();
  });

  it('gives the three classes three different colours, glyphs and words', () => {
    const shown = ALL_VERDICTS.map((verdict) => VERDICT_DISPLAY[verdict]);
    expect(new Set(shown.map((entry) => entry.color)).size).toBe(3);
    expect(new Set(shown.map((entry) => entry.glyph)).size).toBe(3);
    expect(new Set(shown.map((entry) => entry.word)).size).toBe(3);
  });

  it('refuses a verdict outside the ruled vocabulary rather than rendering a neutral chip', () => {
    expect(() => render(<VerdictBadge verdict="probably_fine" />)).toThrow(/no display is defined/);
  });
});

describe('the gallery front page', () => {
  it('renders each committed card with the verdict its own document produces', () => {
    // The front page had no test at all. It reads the committed gallery at build time, so this
    // renders the real thing rather than a fixture: what is asserted is what ships.
    render(<GalleryPage />);

    for (const entry of readGalleryIndex()) {
      const { result } = card(entry.id);
      const derived = laneVerdicts(result as unknown as Record<string, unknown>)[0];
      expect(derived?.verdict, entry.id).toBe(entry.verdict);

      const heading = screen.getByRole('heading', { name: entry.title });
      const article = heading.closest('li') as HTMLElement;
      const display = VERDICT_DISPLAY[entry.verdict];
      const badge = within(article).getByTitle(display.meaning);
      expect(badge.textContent).toBe(`${display.glyph}${display.word}`);
      expect(badge.style.color).toBe(rgb(display.color));
    }
  });

  it('shows all three verdict classes, so no class is rendered by nothing', () => {
    render(<GalleryPage />);
    const entries = readGalleryIndex();
    expect(entries.filter((entry) => entry.verdict === BLOCKED).length).toBe(3);
    expect(entries.filter((entry) => entry.verdict === FLAGGED).length).toBe(1);
    expect(entries.filter((entry) => entry.verdict === PASS).length).toBe(2);
    expect(screen.getAllByText('Blocked')).toHaveLength(3);
    expect(screen.getAllByText('Flagged')).toHaveLength(1);
    expect(screen.getAllByText('Pass')).toHaveLength(2);
  });

  it('captions a blocked card with its reason and a flagged card with its flag names', () => {
    render(<GalleryPage />);
    const { result } = card(FLAGGED_CARD);
    const flags = laneVerdicts(result as unknown as Record<string, unknown>)[0]?.qc_flags ?? [];
    expect(flags.length).toBeGreaterThan(1);

    // A count would be the one thing a reader cannot act on; the names are what the card shows.
    expect(screen.getByText(flags.join(', '))).toBeVisible();
    expect(screen.getAllByText('all_ratios_excluded')).toHaveLength(3);
    // No caption is a bare count like "3 QC flags".
    expect(screen.queryByText(/^\d+ QC flag/)).toBeNull();
  });
});

describe('the per-lane cards on /analyze', () => {
  beforeEach(() => {
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => 'blob:verdict-test'),
    });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  const cases: [string, Verdict, string | null][] = [
    [BLOCKED_CARD, BLOCKED, 'No ratio carries a number: all_ratios_excluded.'],
    [FLAGGED_CARD, FLAGGED, null],
    [PASS_CARD, PASS, 'No QC flags; every ratio computed.'],
  ];

  it.each(cases)('renders %s as %s', (id, expected, sentence) => {
    const envelope = envelopeFor(id);
    const derived = laneVerdicts(envelope.result as unknown as Record<string, unknown>)[0];
    expect(derived?.verdict).toBe(expected);

    render(<ResultStep envelope={envelope} filename="x.json" />);

    const display = VERDICT_DISPLAY[expected];
    // Twice: the lane card and the result surface's header, both from the same derivation.
    const badges = screen.getAllByTitle(display.meaning);
    expect(badges).toHaveLength(2);
    for (const badge of badges) {
      expect(badge.textContent).toBe(`${display.glyph}${display.word}`);
      expect(badge.style.color).toBe(rgb(display.color));
    }
    if (sentence !== null) {
      expect(screen.getAllByText(sentence).length).toBeGreaterThan(0);
    } else {
      // The flagged sentence names the flags it carries rather than counting them.
      const flags = derived?.qc_flags ?? [];
      expect(flags.length).toBeGreaterThan(0);
      expect(
        screen.getAllByText(`Number reported and annotated: ${flags.join(', ')}.`).length,
      ).toBeGreaterThan(0);
    }
    // And no other class leaked onto the screen.
    for (const other of ALL_VERDICTS.filter((entry) => entry !== expected)) {
      expect(screen.queryByText(VERDICT_DISPLAY[other].word)).toBeNull();
    }
  });
});
