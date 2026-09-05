import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { ResultView } from '@/components/ResultView';
import { formatMeasured } from '@/lib/result-cells';
import type { ResultDocument, StoredDisplayBlock } from '@/lib/result';
import { laneVerdicts } from '@/lib/verdict';

/**
 * `ResultView` over documents the shipped service actually produced.
 *
 * The fixtures here are the committed gallery cards -- `public/gallery/<id>/result.json` and its
 * `display.json` -- because a test that invented a result document would be asserting that the
 * component renders documents of a shape nothing produces.
 *
 * Two claims are under test and they are different.
 *
 * **Traceability.** Every measured value on screen carries the JSON path it came from. The test
 * does not take the component's word for the path: it resolves the path against the document and
 * asserts the resolved value is the one rendered. A component that stamped a plausible-looking
 * path onto a number it had computed itself would pass an "attribute is present" check and fail
 * this one.
 *
 * **The image-flags block.** Edge case E7 and Ruling 3 of 2026-08-25: `image_qc_flags` is not an
 * input to the lane verdict, so a clean lane inside a saturated image is a `pass` lane -- and the
 * block that says the image was saturated is still on screen, subordinate and unmissable. The
 * corpus holds no card that is both, so that document is made by taking a real `pass` card and
 * changing exactly one field, `image_qc_flags`, which is precisely the field the derivation is
 * ruled not to read. The test asserts the verdict is still `pass` afterwards, so the premise of
 * the case is checked rather than assumed.
 */

const GALLERY_DIR = join(__dirname, '..', 'public', 'gallery');

/** The ruled pass card: a genuine sample lane, in an image with no image-level flags. */
const PASS_CARD = 'pmc13135410-fig4c-l8';
/** A lane whose every ratio was excluded for saturation: the `excluded — reason` path. */
const BLOCKED_CARD = 'pmc12895598-fig3a-l3';
/**
 * The only card carrying more than one flag on a band, the only `flagged` card, and the only one
 * whose ratios are a *mixture* of excluded and kept.
 *
 * Its absence from the sweep is why a `flagText` mutant survived two review cycles: on the other
 * two cards every non-empty flag list has exactly one element, so `flags.join(', ')` was never
 * exercised at a length where joining differs from taking the first. One id covers three
 * untested states.
 */
const FLAGGED_CARD = 'pmc13135410-fig3b-l11';

/**
 * The ruled detection-beta card: the printed molecular-weight label column, read as five bands
 * and returned `pass`. Used for the single-pixel note and to pin that the verdict is shown as
 * the derivation produced it -- the card discloses what the ROI covers and never softens the
 * verdict, which is the whole point of shipping the defect as itself.
 */
const DETECTION_BETA_CARD = 'pmc12895598-fig3a-l5';

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

/**
 * Resolve a `data-json-path` such as `normalization.ratios[2].ratio` against a document.
 *
 * Written here rather than imported, so the test does not check the component's paths with the
 * component's own path arithmetic.
 */
function resolveJsonPath(root: unknown, path: string): unknown {
  let value: unknown = root;
  for (const segment of path.replace(/\[(\d+)\]/g, '.$1').split('.')) {
    if (typeof value !== 'object' || value === null) {
      throw new Error(`${path}: cannot descend into ${String(value)} at ${segment}`);
    }
    value = (value as Record<string, unknown>)[segment];
  }
  return value;
}

/**
 * The text a non-numeric cell must show for a given document value.
 *
 * Written here, independently of the component, so it kills a mutant in the component's own
 * rendering rather than restating it. The three shapes the tables display: a flag list (joined,
 * or "none" when recorded and empty), a boolean exclusion, and a plain string.
 */
function renderedText(path: string, stored: unknown): string {
  if (path === 'normalization.warnings') {
    const warnings = stored as string[];
    return warnings.length === 0 ? 'no warnings' : `warnings: ${warnings.join(', ')}`;
  }
  if (path === 'image_qc_flags') {
    const flags = stored as string[];
    return flags.length === 0 ? 'none recorded on this image' : flags.join(', ');
  }
  if (path.endsWith('.exclusion_reason')) {
    return path.startsWith('bands[') ? String(stored) : `excluded — ${String(stored)}`;
  }
  if (Array.isArray(stored)) {
    return stored.length === 0 ? 'none' : stored.join(', ');
  }
  if (typeof stored === 'boolean') {
    return stored ? 'excluded' : 'included';
  }
  return String(stored);
}

function renderCard(id: string, overrides: Partial<ResultDocument> = {}) {
  const { result, display } = card(id);
  const document = { ...result, ...overrides };
  const lane = document.lanes[0];
  if (lane === undefined) {
    throw new Error(`${id} stores no lane`);
  }
  render(
    <ResultView
      document={document}
      display={display}
      imageSrc={`/gallery/${id}/display.png`}
      laneId={lane.lane_id}
      download={{ href: `/gallery/${id}/result.json`, filename: `${id}-result.json` }}
    />,
  );
  return document;
}

describe('every number on screen is traceable to a JSON path', () => {
  it('renders a band intensity at the path it advertises, with the full value in the title', () => {
    const document = renderCard(PASS_CARD);

    const path = 'bands[0].integrated_intensity';
    const cell = window.document.querySelector(`[data-json-path="${path}"]`);
    expect(cell).not.toBeNull();

    const stored = resolveJsonPath(document, path);
    expect(typeof stored).toBe('number');
    expect(cell?.textContent).toBe(formatMeasured(stored as number));
    // The rounding is on screen; the unrounded value is one hover away.
    expect(cell?.getAttribute('title')).toBe(`${path} = ${String(stored)}`);
  });

  it('renders a ratio at the path it advertises', () => {
    const document = renderCard(PASS_CARD);

    const path = 'normalization.ratios[2].ratio';
    const cell = window.document.querySelector(`[data-json-path="${path}"]`);
    expect(cell).not.toBeNull();

    const stored = resolveJsonPath(document, path);
    expect(typeof stored).toBe('number');
    expect(cell?.textContent).toBe(formatMeasured(stored as number));
    expect(cell?.getAttribute('title')).toBe(`${path} = ${String(stored)}`);
  });

  it('renders the document’s own value at every numeric path it advertises', () => {
    // For every element on screen that names a path, resolve that path against the document and
    // require the rendered text and the tooltip to be that value. A component that stamped a
    // plausible path onto a number it had computed itself passes a "the attribute is present"
    // check and fails this one.
    //
    // What this does *not* catch, stated because the sweep looks stricter than it is: the
    // numeric branch compares against `formatMeasured`, the component's own formatter, so a
    // change to the display precision passes here. `result-cells.test.ts` pins that formatter
    // against literals, and the test below pins one rendered cell against one, so the precision
    // is nailed down somewhere that does not import it.
    for (const id of [PASS_CARD, BLOCKED_CARD, FLAGGED_CARD]) {
      cleanup();
      const document = renderCard(id);

      const advertised = Array.from(
        window.document.querySelectorAll<HTMLElement>('[data-json-path]'),
      );
      expect(advertised.length).toBeGreaterThan(0);

      let numericCells = 0;
      let nonNumericCells = 0;
      let multiFlagCells = 0;
      for (const element of advertised) {
        const path = element.getAttribute('data-json-path') ?? '';
        if (path.startsWith('display.')) {
          continue;
        }
        expect(() => resolveJsonPath(document, path)).not.toThrow();
        const stored = resolveJsonPath(document, path);
        expect(stored).not.toBeUndefined();
        if (typeof stored === 'number') {
          numericCells += 1;
          expect(element.textContent).toBe(formatMeasured(stored));
          expect(element.getAttribute('title')).toBe(`${path} = ${String(stored)}`);
        } else {
          // A non-numeric cell states the document's own value in its tooltip, so the equation
          // is true even where the text is a rendering of it ("none" for an empty flag list).
          expect(element.getAttribute('title')).toBe(`${path} = ${JSON.stringify(stored)}`);
          // ...and the *text* must be a rendering of that value, not of some other one. Only
          // the title was checked here before, and replacing `flagText`'s body with
          // `return 'none'` -- so every band on the saturated card read "none" -- left all 103
          // tests passing. The QC-flag column is the headline claim of this whole tool; it must
          // not be the one column nothing asserts.
          expect(element.textContent).toBe(renderedText(path, stored));
          nonNumericCells += 1;
          if (Array.isArray(stored) && stored.length > 1) {
            multiFlagCells += 1;
          }
        }
      }
      expect(numericCells).toBeGreaterThan(0);
      expect(nonNumericCells).toBeGreaterThan(0);
      if (id === FLAGGED_CARD) {
        // The assertion the mutant escaped: a flag list of length > 1, joined.
        expect(multiFlagCells, 'no cell rendered more than one flag').toBeGreaterThan(0);
      }
    }
  });

  it('renders a known cell at a literal, independent of the component’s formatter', () => {
    // The one assertion in this file that would fail if MEASURED_SIGNIFICANT_DIGITS changed.
    // `L0_B0` of the pass card integrates to a whole number of DN and its ratio is a long
    // decimal, so the pair covers both branches of `formatMeasured` without calling it.
    renderCard(PASS_CARD);
    const intensity = window.document.querySelector(
      '[data-json-path="bands[0].integrated_intensity"]',
    );
    expect(intensity?.textContent).toBe('1166');
    expect(intensity?.getAttribute('title')).toBe('bands[0].integrated_intensity = 1166');

    const ratio = window.document.querySelector(
      '[data-json-path="normalization.ratios[0].ratio"]',
    );
    expect(ratio?.textContent).toBe('0.0111451');
    expect(ratio?.getAttribute('title')).toBe(
      'normalization.ratios[0].ratio = 0.011145096539858537',
    );
  });

  it('shows every band and every ratio the document carries', () => {
    const document = renderCard(PASS_CARD);

    for (const [index] of document.bands.entries()) {
      expect(
        window.document.querySelector(`[data-json-path="bands[${index}].integrated_intensity"]`),
      ).not.toBeNull();
    }
    for (const [index] of document.normalization.ratios.entries()) {
      expect(
        window.document.querySelector(
          `[data-json-path="normalization.ratios[${index}].ratio"]`,
        ),
      ).not.toBeNull();
    }
  });

  it('shows an excluded ratio’s number as well as its exclusion and reason', () => {
    // Ruled 2026-08-25 (docs/pr/phase-4b0.md, "What the cards display"): "Show every ratio the
    // tool produced ... Nothing the tool computed is hidden from the card; that is the same rule
    // that governs flagged bands in the record, applied to the display."
    const document = renderCard(BLOCKED_CARD);

    const ratio = document.normalization.ratios[0];
    expect(ratio?.excluded).toBe(true);

    const value = window.document.querySelector(
      '[data-json-path="normalization.ratios[0].ratio"]',
    );
    expect(value?.textContent).toBe(formatMeasured(ratio?.ratio ?? Number.NaN));
    // Marked as not-to-be-used by a line through it, which survives greyscale and colour blindness.
    expect(value?.className).toContain('line-through');

    const reason = window.document.querySelector(
      '[data-json-path="normalization.ratios[0].exclusion_reason"]',
    );
    expect(reason?.textContent).toBe(`excluded — ${ratio?.exclusion_reason ?? ''}`);
  });

  it('shows every ratio of a fully blocked lane, none of them hidden', () => {
    const document = renderCard(BLOCKED_CARD);
    const verdict = laneVerdicts(document as unknown as Record<string, unknown>)[0];
    expect(verdict?.verdict).toBe('blocked');
    expect(verdict?.usable_ratio_count).toBe(0);

    // The lane carries no usable number, and the card still shows every number it computed.
    expect(document.normalization.ratios.length).toBeGreaterThan(0);
    for (const [index, ratio] of document.normalization.ratios.entries()) {
      const cell = window.document.querySelector(
        `[data-json-path="normalization.ratios[${index}].ratio"]`,
      );
      expect(cell?.textContent).toBe(formatMeasured(ratio.ratio));
    }
  });
});

describe('an optional flag key that is absent', () => {
  /**
   * `Ratio.qc_flags` and `Ratio.reference_qc_flags` are optional under the schema, and
   * `test/fixtures/verdict/optional_ratio_flag_keys_are_absent.json` exists because documents
   * without them are real. Every committed gallery card happens to carry both, so this case is
   * made by deleting the keys from a real card -- otherwise the branch ships untested.
   *
   * The distinction is not cosmetic. "none" claims the document recorded an empty list; an
   * absent key recorded nothing. And the absent cell must carry **no** `data-json-path`,
   * because a path that resolves to nothing is precisely the way the traceability claim breaks
   * quietest.
   */
  it('reads as "not recorded" and advertises no JSON path', () => {
    const { result, display } = card(PASS_CARD);
    const stripped: ResultDocument = {
      ...result,
      normalization: {
        ...result.normalization,
        ratios: result.normalization.ratios.map((ratio) => {
          const copy: Record<string, unknown> = { ...ratio };
          delete copy['qc_flags'];
          delete copy['reference_qc_flags'];
          return copy as unknown as (typeof result.normalization.ratios)[number];
        }),
      },
    };
    const lane = stripped.lanes[0];
    render(
      <ResultView
        document={stripped}
        display={display}
        imageSrc="/x.png"
        laneId={lane?.lane_id ?? ''}
        download={{ href: '/x.json', filename: 'x.json' }}
      />,
    );

    expect(screen.getAllByText('not recorded').length).toBe(
      stripped.normalization.ratios.length * 2,
    );
    for (const [index] of stripped.normalization.ratios.entries()) {
      for (const key of ['qc_flags', 'reference_qc_flags']) {
        expect(
          window.document.querySelector(
            `[data-json-path="normalization.ratios[${index}].${key}"]`,
          ),
        ).toBeNull();
      }
    }
    // The lane still passes: an absent optional list is a legal document, not a damaged one.
    expect(screen.getByText('No QC flags; every ratio computed.')).toBeVisible();
  });

  it('reads as "none" and does advertise its path when the list is recorded and empty', () => {
    const document = renderCard(PASS_CARD);
    expect(document.normalization.ratios[0]?.qc_flags).toEqual([]);
    const cell = window.document.querySelector(
      '[data-json-path="normalization.ratios[0].qc_flags"]',
    );
    expect(cell?.textContent).toBe('none');
    expect(cell?.getAttribute('title')).toBe('normalization.ratios[0].qc_flags = []');
    expect(screen.queryByText('not recorded')).toBeNull();
  });
});

describe('the ROI overlay indexes the picture one to one', () => {
  it('draws the document’s coordinates unchanged, in a viewBox of the source dimensions', () => {
    const document = renderCard(PASS_CARD);
    const { display } = card(PASS_CARD);
    const lane = document.lanes[0];

    // The claim the whole overlay rests on: the derivative is the source's own size, so a
    // rectangle at x=123 is column 123 of the image the pipeline measured. Asserted here as
    // well as in tests/test_gallery_build.py, because this is where it is relied on.
    expect([display.width_px, display.height_px]).toEqual([
      document.source.width_px,
      document.source.height_px,
    ]);

    const svg = window.document.querySelector('svg[viewBox]');
    expect(svg?.getAttribute('viewBox')).toBe(`0 0 ${display.width_px} ${display.height_px}`);

    const laneRect = window.document.querySelector(`[data-roi-id="lane-${lane?.lane_id ?? ''}"]`);
    // Lane solid, bands dashed: the overlay must be readable without colour, on the same rule
    // VerdictBadge follows. Colour was the only discriminator until review; the numeric corner
    // label is on lanes and not on bands, so a greyscale reader had nothing else to go on.
    expect(laneRect?.getAttribute('stroke-dasharray')).toBeNull();
    for (const band of document.bands) {
      expect(
        window.document
          .querySelector(`[data-roi-id="band-${band.band_id}"]`)
          ?.getAttribute('stroke-dasharray'),
      ).toBe('4 3');
    }
    expect(laneRect?.getAttribute('x')).toBe(String(lane?.roi.x));
    expect(laneRect?.getAttribute('y')).toBe(String(lane?.roi.y));
    expect(laneRect?.getAttribute('width')).toBe(String(lane?.roi.width));
    expect(laneRect?.getAttribute('height')).toBe(String(lane?.roi.height));

    // Every band of the lane is drawn, at its own recorded rectangle. No rounding, no inset.
    expect(document.bands.length).toBeGreaterThan(0);
    for (const band of document.bands) {
      const rect = window.document.querySelector(`[data-roi-id="band-${band.band_id}"]`);
      expect(rect).not.toBeNull();
      expect([
        rect?.getAttribute('x'),
        rect?.getAttribute('y'),
        rect?.getAttribute('width'),
        rect?.getAttribute('height'),
      ]).toEqual([
        String(band.roi.x),
        String(band.roi.y),
        String(band.roi.width),
        String(band.roi.height),
      ]);
      // Each band rectangle lies inside the lane rectangle it was measured in.
      expect(band.roi.x).toBeGreaterThanOrEqual(lane?.roi.x ?? 0);
      expect(band.roi.x + band.roi.width).toBeLessThanOrEqual(
        (lane?.roi.x ?? 0) + (lane?.roi.width ?? 0),
      );
    }
  });
});

describe('the verdict ResultView shows', () => {
  // Pinned here as well as in verdict-rendering.test.tsx, because this is the component whose
  // `verdict` prop a mutation replaced with a hard-coded "pass" while every test stayed green.
  it.each([
    [BLOCKED_CARD, 'blocked', 'Blocked', 'No ratio carries a number: all_ratios_excluded.'],
    [FLAGGED_CARD, 'flagged', 'Flagged', null],
    [PASS_CARD, 'pass', 'Pass', 'No QC flags; every ratio computed.'],
  ])('renders %s as %s', (id, expected, word, sentence) => {
    const document = renderCard(id);
    const derived = laneVerdicts(document as unknown as Record<string, unknown>)[0];
    expect(derived?.verdict).toBe(expected);

    expect(screen.getByText(word)).toBeVisible();
    for (const other of ['Blocked', 'Flagged', 'Pass'].filter((entry) => entry !== word)) {
      expect(screen.queryByText(other)).toBeNull();
    }
    expect(
      screen.getByText(sentence ?? `Number reported and annotated: ${derived?.qc_flags.join(', ')}.`),
    ).toBeVisible();
  });
});

describe('the ruled disclosures', () => {
  it('names the one-pixel-wide detection on the pass card, from the document', () => {
    // Ruled 2026-08-25: "The card names where it came from." Derived from `bands[i].roi`, not
    // from the manifest and not from a hard-coded phrase, so it is traceable like every other
    // claim on the page.
    const document = renderCard(PASS_CARD);
    const narrow = document.bands.findIndex((band) => band.roi.width <= 1);
    expect(narrow, 'the pass card no longer holds a one-pixel band').toBeGreaterThanOrEqual(0);

    const note = screen.getByTestId('single-pixel-note');
    expect(note.textContent).toContain(document.bands[narrow]?.band_id ?? '');
    expect(
      note.querySelector(`[data-json-path="bands[${narrow}].roi.width"]`)?.textContent,
    ).toBe('1');
  });

  it('shows every ratio of the pass card, the one-pixel one included', () => {
    // The same ruling forbids hiding any ratio and forbids a parameter moving to suppress it.
    const document = renderCard(PASS_CARD);
    expect(document.normalization.ratios).toHaveLength(3);
    for (const [index, ratio] of document.normalization.ratios.entries()) {
      expect(ratio.excluded).toBe(false);
      const cell = window.document.querySelector(
        `[data-json-path="normalization.ratios[${index}].ratio"]`,
      );
      expect(cell?.textContent).toBe(formatMeasured(ratio.ratio));
    }
  });

  it('shows the detection-beta card’s verdict as derived, without softening it', () => {
    // The ROI is printed type, the derivation says `pass`, and both facts are on the page: the
    // verdict is not suppressed and the disclosure is not a substitute for it.
    const document = renderCard(DETECTION_BETA_CARD);
    const verdict = laneVerdicts(document as unknown as Record<string, unknown>)[0];
    expect(verdict?.verdict).toBe('pass');
    expect(screen.getAllByText('Pass').length).toBeGreaterThan(0);
    expect(screen.getByText('No QC flags; every ratio computed.')).toBeVisible();
    expect(document.bands.length).toBe(5);
  });

  it('states the note truthfully for a one-pixel-tall band, not only a narrow one', () => {
    // The trigger is `width <= 1 || height <= 1`; the prose was written for width alone, so a
    // 30x1 band produced a sentence contradicting the dimensions printed inside it. Reachable:
    // `/analyze` accepts arbitrary ROIs.
    const { result, display } = card(PASS_CARD);
    const bands = [...result.bands];
    const first = bands[0];
    if (first === undefined) {
      throw new Error('fixture has no band');
    }
    bands[0] = { ...first, roi: { ...first.roi, width: 30, height: 1 } };
    const lane = result.lanes[0];
    render(
      <ResultView
        document={{ ...result, bands }}
        display={display}
        imageSrc="/x.png"
        laneId={lane?.lane_id ?? ''}
        download={{ href: '/x.json', filename: 'x.json' }}
      />,
    );
    const note = screen.getByTestId('single-pixel-note');
    expect(note.textContent).toContain('30');
    expect(note.textContent).toContain('one pixel tall a single row with no height');
  });

  it('has no single-pixel note on a card with no single-pixel band', () => {
    const document = renderCard(BLOCKED_CARD);
    expect(document.bands.every((band) => band.roi.width > 1 && band.roi.height > 1)).toBe(true);
    expect(screen.queryByTestId('single-pixel-note')).toBeNull();
  });
});

describe('the image-level flags block', () => {
  const HEADING = 'image-level flags (do not affect the lane verdict)';

  it('is present, and says so, when no image flag fired', () => {
    const document = renderCard(PASS_CARD);
    expect(document.image_qc_flags).toEqual([]);

    const heading = screen.getByRole('heading', { name: HEADING });
    expect(heading.textContent).toBe(HEADING);
    const block = heading.closest('section');
    expect(block).not.toBeNull();
    expect(within(block as HTMLElement).getByText('none recorded on this image')).toBeVisible();
  });

  it('is rendered on a pass lane inside a saturated image (E7)', () => {
    // Exactly one field of a real, service-written pass card is changed, and it is the field
    // Ruling 3 makes irrelevant to the verdict. The corpus contains no card that is both.
    const document = renderCard(PASS_CARD, { image_qc_flags: ['saturated'] });

    const verdict = laneVerdicts(document as unknown as Record<string, unknown>)[0];
    expect(verdict?.verdict).toBe('pass');
    expect(verdict?.qc_flags).toEqual([]);

    expect(screen.getByText('No QC flags; every ratio computed.')).toBeVisible();

    const heading = screen.getByRole('heading', { name: HEADING });
    const block = heading.closest('section');
    expect(block).not.toBeNull();
    const flags = within(block as HTMLElement).getByText('saturated');
    expect(flags).toHaveAttribute('data-json-path', 'image_qc_flags');
  });

  it('is subordinate: it sits on the sunken surface and in muted ink', () => {
    renderCard(PASS_CARD, { image_qc_flags: ['saturated'] });
    const block = screen.getByRole('heading', { name: HEADING }).closest('section');
    expect(block?.className).toContain('bg-[var(--color-surface-sunken)]');
    expect(block?.className).not.toContain('border-2');
  });
});

describe('the derivative disclosure', () => {
  it("renders the service's own note, so a 255 in the PNG is never read as saturation", () => {
    // `display.note` is `api/display.py::DERIVATIVE_NOTE`, and it is the *only* place on screen
    // that says the picture is a derivative: `is_derivative` reaches the screen nowhere, and the
    // provenance panel prints `provenance.parameters` and `display.mapping`, neither of which
    // contains it. It is the stated reason `display.json` is committed at all -- without it, a
    // card view invites a reader to judge saturation off the brightest colour in a PNG, which is
    // exactly what that note exists to forbid, on a gallery whose blocked cards are saturated.
    //
    // It was rendered and asserted by nothing: emptying the paragraph passed 165/165.
    const { display } = card(PASS_CARD);
    renderCard(PASS_CARD);
    expect(display.note).toContain('not evidence of saturation');
    expect(screen.getByText(display.note)).toBeInTheDocument();
  });

  it('carries the source-DN-per-output-level figure the note refers to', () => {
    // The note points at `mapping.source_dn_per_output_level` by name, so the number it names has
    // to be on the page too; a caveat referring to a figure the reader cannot see is half a
    // caveat. It lives in the provenance panel's display-mapping block.
    const { display } = card(PASS_CARD);
    renderCard(PASS_CARD);
    expect(display.mapping.source_dn_per_output_level).toBeGreaterThan(0);
  });
});

describe('the highlight is never colour alone', () => {
  it('marks a highlighted band by stroke weight, fill, dash and a corner marker', async () => {
    // PLAN.md Phase 4 requires colourblind-safe QC encoding, and `RoiOverlay`'s docstring makes
    // the promise explicitly: a highlighted band gets a heavier stroke, a translucent fill and a
    // filled corner marker, "so it is distinguishable in greyscale, at a glance, and under every
    // colour deficiency".
    //
    // None of that was asserted. `data-roi-highlighted` is an attribute, not an encoding, and the
    // dash assertion above covers only the unhighlighted state. Flattening the stroke to the band
    // width, the fill opacity to 0, the dash to unconditional and deleting the corner marker left
    // the highlight differing from a plain band by *hue only* -- and passed 165/165. This is the
    // trust feature: the one signal that says which region a number came from. A hue-only version
    // of it is unreadable to the same readers `verdict-display.ts` exists for.
    const user = userEvent.setup();
    const document = renderCard(PASS_CARD);
    const band = document.bands[1];
    expect(band).toBeDefined();
    const id = band?.band_id ?? '';
    const rect = () => window.document.querySelector(`[data-roi-id="band-${id}"]`);

    const plainStroke = rect()?.getAttribute('stroke-width');
    expect(rect()?.getAttribute('fill-opacity')).toBe('0');
    expect(rect()?.getAttribute('stroke-dasharray')).toBe('4 3');
    expect(window.document.querySelector(`[data-roi-marker="band-${id}"]`)).toBeNull();

    await user.click(screen.getAllByRole('button', { name: id })[0] as HTMLElement);

    // Four channels change, and not one of them is the colour.
    const highlightedStroke = rect()?.getAttribute('stroke-width');
    expect(Number(highlightedStroke)).toBeGreaterThan(Number(plainStroke));
    expect(Number(rect()?.getAttribute('fill-opacity'))).toBeGreaterThan(0);
    expect(rect()?.getAttribute('stroke-dasharray')).toBeNull();
    expect(window.document.querySelector(`[data-roi-marker="band-${id}"]`)).not.toBeNull();
  });
});

describe('activating a row', () => {
  it('highlights the band region and opens its provenance, from the keyboard', async () => {
    const user = userEvent.setup();
    const document = renderCard(PASS_CARD);
    const band = document.bands[1];
    expect(band).toBeDefined();

    const rect = () =>
      window.document.querySelector(`[data-roi-id="band-${band?.band_id ?? ''}"]`);
    expect(rect()).toHaveAttribute('data-roi-highlighted', 'false');
    expect(screen.queryByRole('heading', { name: /Provenance/ })).toBeNull();

    // The band table's row; the ratio table has a second button naming the same band.
    const rowButton = screen.getAllByRole('button', { name: band?.band_id ?? '' })[0] as HTMLElement;
    expect(rowButton).toHaveAttribute('aria-pressed', 'false');
    rowButton.focus();
    await user.keyboard('{Enter}');

    expect(rowButton).toHaveAttribute('aria-pressed', 'true');
    expect(rect()).toHaveAttribute('data-roi-highlighted', 'true');

    const panel = screen
      .getByRole('heading', { name: /Provenance/ })
      .closest('section') as HTMLElement;
    // The whole parameter set, not a chosen few keys, and the display mapping beside it.
    const parameters = panel.querySelector('[data-json-path="provenance.parameters"]');
    expect(parameters?.textContent).toBe(
      JSON.stringify(document.provenance.parameters, null, 2),
    );
    expect(
      within(panel).getByText(document.provenance.config_id),
    ).toHaveAttribute('data-json-path', 'provenance.config_id');

    const { display } = card(PASS_CARD);
    for (const [key, value] of Object.entries(display.mapping)) {
      const cell = panel.querySelector(`[data-json-path="display.mapping.${key}"]`);
      expect(cell?.textContent).toBe(String(value));
    }

    // The ROI is integer pixels and is shown as integers. `123.000` here would present a pixel
    // coordinate as having a decimal part, in the one panel whose whole job is to be exact.
    for (const field of ['x', 'y', 'width', 'height'] as const) {
      const cell = panel.querySelector(
        `[data-json-path="bands[1].roi.${field}"]`,
      );
      expect(cell?.textContent).toBe(String(band?.roi[field]));
      expect(cell?.textContent).not.toContain('.');
    }
  });

  it('links a ratio row to the same region as its numerator band', async () => {
    const user = userEvent.setup();
    const document = renderCard(BLOCKED_CARD);
    const ratio = document.normalization.ratios[0];
    const bandId = ratio?.numerator_band_id ?? '';

    // Two rows name this band -- one in each table -- and activating either highlights it.
    const buttons = screen.getAllByRole('button', { name: bandId });
    expect(buttons).toHaveLength(2);
    await user.click(buttons[1] as HTMLElement);

    expect(
      window.document.querySelector(`[data-roi-id="band-${bandId}"]`),
    ).toHaveAttribute('data-roi-highlighted', 'true');
    for (const button of buttons) {
      expect(button).toHaveAttribute('aria-pressed', 'true');
    }
  });
});

describe('the download', () => {
  it('points at the committed document for a gallery card', () => {
    renderCard(PASS_CARD);
    const link = screen.getByRole('link', { name: 'Download result.json' });
    expect(link).toHaveAttribute('href', `/gallery/${PASS_CARD}/result.json`);
    expect(link).toHaveAttribute('download', `${PASS_CARD}-result.json`);
  });
});
