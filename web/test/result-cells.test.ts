import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import type { Ratio, ResultDocument } from '@/lib/result';
import {
  MEASURED_SIGNIFICANT_DIGITS,
  exclusionReasonOf,
  formatMeasured,
  measuredTitle,
  ratioReference,
} from '@/lib/result-cells';

/**
 * The cell readings, over real documents and at the magnitudes this pipeline actually emits.
 *
 * The rendering rule is asserted on values taken from the committed cards rather than on round
 * numbers, because the property that matters is that one table can hold an intensity of 31767 and
 * a ratio of 0.011145096539858537 without either becoming unreadable. Both are read out of the
 * committed cards below rather than quoted here from memory -- an earlier revision of this
 * docstring named two values that appear in no card while claiming they were taken from them.
 */

const GALLERY_DIR = join(__dirname, '..', 'public', 'gallery');

function document(id: string): ResultDocument {
  return JSON.parse(
    readFileSync(join(GALLERY_DIR, id, 'result.json'), 'utf-8'),
  ) as ResultDocument;
}

/** Return `value` with one key removed: a deliberately damaged document, for the refusal tests. */
function without<T extends object>(value: T, key: keyof T & string): T {
  const copy = { ...(value as Record<string, unknown>) };
  delete copy[key];
  return copy as T;
}

describe('formatMeasured', () => {
  it('keeps an integer an integer', () => {
    // Pixel coordinates and whole-numbered intensities. `123.000` would show a decimal part the
    // number does not have.
    expect(formatMeasured(123)).toBe('123');
    expect(formatMeasured(0)).toBe('0');
    expect(formatMeasured(31767)).toBe('31767');
  });

  it('shows a small ratio at six significant digits rather than rounding it to nothing', () => {
    expect(formatMeasured(0.011145096539858537)).toBe('0.0111451');
    expect(MEASURED_SIGNIFICANT_DIGITS).toBe(6);
  });

  it('uses values that are actually in the committed cards', () => {
    // The docstring above names two magnitudes as the reason for significant digits. They have
    // to be magnitudes this pipeline emits, or the rationale is about a table nobody will see.
    const all = ['pmc13135410-fig4c-l8', 'pmc12895598-fig3a-l3', 'pmc13135410-fig3b-l11'].map(
      (id) => document(id),
    );
    const intensities = all.flatMap((doc) => doc.bands.map((band) => band.integrated_intensity));
    const ratios = all.flatMap((doc) => doc.normalization.ratios.map((entry) => entry.ratio));
    expect(intensities).toContain(31767);
    expect(ratios).toContain(0.011145096539858537);
  });

  it('does not round a fractional value to fewer digits than it has', () => {
    expect(formatMeasured(22.814814814814813)).toBe('22.8148');
  });

  it('refuses a non-finite value instead of rendering NaN as a measurement', () => {
    expect(() => formatMeasured(Number.NaN)).toThrow(/not a finite number/);
    expect(() => formatMeasured(Number.POSITIVE_INFINITY)).toThrow(/not a finite number/);
  });

  it('renders every measured value in the committed gallery without throwing', () => {
    for (const id of ['pmc13135410-fig4c-l8', 'pmc12895598-fig3a-l3']) {
      const result = document(id);
      for (const band of result.bands) {
        expect(formatMeasured(band.integrated_intensity)).not.toBe('');
        expect(formatMeasured(band.background_estimate)).not.toBe('');
      }
      for (const ratio of result.normalization.ratios) {
        expect(formatMeasured(ratio.ratio)).not.toBe('');
      }
    }
  });
});

describe('measuredTitle', () => {
  it('carries the path and the value the rendering rounded away', () => {
    expect(measuredTitle('normalization.ratios[0].ratio', 0.011145096539858537)).toBe(
      'normalization.ratios[0].ratio = 0.011145096539858537',
    );
  });
});

describe('ratioReference', () => {
  it('reads the lane total under total_protein, where no denominator band exists', () => {
    const result = document('pmc13135410-fig4c-l8');
    expect(result.normalization.mode).toBe('total_protein');
    const ratio = result.normalization.ratios[0] as Ratio;
    expect(ratio.denominator_band_id).toBeUndefined();
    expect(ratio.denominator_band_ids).toBeUndefined();

    const reference = ratioReference(result, 0);
    expect(reference.kind).toBe('laneTotal');
    if (reference.kind !== 'laneTotal') {
      throw new Error('unreachable');
    }
    expect(reference.path).toBe('lanes[0].total_protein_signal');
    expect(reference.signal).toBe(result.lanes[0]?.total_protein_signal);
  });

  it('prefers the singular denominator band id when the document names one', () => {
    const result = document('pmc13135410-fig4c-l8');
    const patched: ResultDocument = {
      ...result,
      normalization: {
        ...result.normalization,
        ratios: [
          { ...(result.normalization.ratios[0] as Ratio), denominator_band_id: 'L0_B2' },
        ],
      },
    };
    const reference = ratioReference(patched, 0);
    expect(reference).toEqual({
      kind: 'bands',
      bandIds: ['L0_B2'],
      singular: true,
      path: 'normalization.ratios[0].denominator_band_id',
    });
  });

  it('joins several denominator band ids when the lane was divided by more than one', () => {
    const result = document('pmc13135410-fig4c-l8');
    const patched: ResultDocument = {
      ...result,
      normalization: {
        ...result.normalization,
        ratios: [
          {
            ...(result.normalization.ratios[0] as Ratio),
            denominator_band_ids: ['L0_B1', 'L0_B2'],
          },
        ],
      },
    };
    const reference = ratioReference(patched, 0);
    expect(reference).toEqual({
      kind: 'bands',
      bandIds: ['L0_B1', 'L0_B2'],
      singular: false,
      path: 'normalization.ratios[0].denominator_band_ids',
    });
  });

  it('refuses when the document records no divisor at all', () => {
    const result = document('pmc13135410-fig4c-l8');
    const lane = result.lanes[0];
    if (lane === undefined) {
      throw new Error('fixture has no lane');
    }
    const patched: ResultDocument = {
      ...result,
      lanes: [without(lane, 'total_protein_signal')],
    };
    expect(() => ratioReference(patched, 0)).toThrow(/does not record what this ratio was/);
  });
});

describe('exclusionReasonOf', () => {
  it('returns the recorded reason on a real excluded ratio', () => {
    const result = document('pmc12895598-fig3a-l3');
    const ratio = result.normalization.ratios[0] as Ratio;
    expect(ratio.excluded).toBe(true);
    expect(exclusionReasonOf(ratio, 0)).toBe('carries QC flags: saturated');
  });

  it('refuses an exclusion with no recorded cause rather than showing a bare "excluded"', () => {
    const result = document('pmc12895598-fig3a-l3');
    const ratio = without(result.normalization.ratios[0] as Ratio, 'exclusion_reason');
    expect(() => exclusionReasonOf(ratio, 0)).toThrow(/records no exclusion_reason/);
  });
});
