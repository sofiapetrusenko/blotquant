import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { VERDICTS } from '@/lib/verdict';
import { VERDICT_DISPLAY, verdictDisplay } from '@/lib/verdict-display';

/**
 * The verdict triple is defined once, and this pins the ways it could quietly stop being once:
 * a verdict added to the vocabulary with no display, a colour edited in the CSS and not in the
 * module, two verdicts sharing a glyph, or an unknown verdict rendering as an ordinary badge.
 */

const GLOBALS_CSS = readFileSync(join(__dirname, '..', 'app', 'globals.css'), 'utf-8');

describe('the verdict display triple', () => {
  it('covers every verdict in the ruled vocabulary and nothing else', () => {
    expect(Object.keys(VERDICT_DISPLAY).sort()).toEqual([...VERDICTS].sort());
  });

  it('gives every verdict a distinct colour, glyph and word', () => {
    const entries = Object.values(VERDICT_DISPLAY);
    expect(new Set(entries.map((entry) => entry.color)).size).toBe(entries.length);
    expect(new Set(entries.map((entry) => entry.glyph)).size).toBe(entries.length);
    expect(new Set(entries.map((entry) => entry.word)).size).toBe(entries.length);
  });

  it('declares each colour in globals.css under the custom property it names', () => {
    // The two files are the only places a verdict colour appears. If they drift, a screen using
    // the Tailwind token and one using the module's hex would show two different oranges for the
    // same verdict, which reads as a difference in the data.
    for (const entry of Object.values(VERDICT_DISPLAY)) {
      expect(GLOBALS_CSS).toContain(`${entry.cssVariable}: ${entry.color.toLowerCase()};`);
    }
  });

  it('refuses a verdict outside the ruled vocabulary rather than showing a neutral badge', () => {
    expect(() => verdictDisplay('inconclusive')).toThrow(/no display is defined/);
  });
});
