import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import AnalyzePage from '@/app/analyze/page';
import { formatMeasured } from '@/lib/result-cells';
import type { Envelope, ResultDocument, StoredDisplayBlock } from '@/lib/result';

/**
 * The whole `/analyze` flow, driven the way a person drives it.
 *
 * **No browser ran.** `/analyze` was to be exercised in a real Chrome against the live API. The
 * extension was never connected -- `list_connected_browsers` returns `[]` -- so this drives every
 * step the person takes, over the real page components, with one thing replaced: the network
 * transport. Layout, focus order and the pointer-drawing path are therefore not covered by
 * anything, here or elsewhere.
 *
 * The transport was exercised separately, by hand, against the live API at this tree: a
 * `POST /analyze` over HTTP on this same image, polarity, config and rectangle --
 * `272,0,36,122` -- answering HTTP 200 with `pass`, 3 bands, 3 of 3 ratios usable, no QC flags,
 * and ratios `0.011145096539858537`, `0.2839418849168419`, `0.3036417510992162`.
 *
 * **What that does and does not establish**, because the difference is the whole of the CORS
 * question in `web/README.md`: the request carried an `Origin` header and the service answered
 * 200, but it answered with no `Access-Control-Allow-Origin` header, because `api/app.py`
 * installs no CORS middleware. A command-line client does not enforce the same-origin policy and
 * a browser does. So the service answers correctly; a browser on another origin still cannot
 * reach it.
 *
 * The document replayed below is `pmc13135410-fig4c-l8`, the committed gallery card for that same
 * request, so the answer the page renders is a measurement the shipped service made rather than
 * one written for this test.
 *
 * Two browser facilities jsdom does not have are stubbed, and neither is logic under test: the
 * image decoder (jsdom decodes no pixels, so `naturalWidth` would be 0) and `URL.createObjectURL`.
 * The dimensions the decoder reports are the real image's own, taken from the document.
 */

const REPO_ROOT = join(__dirname, '..', '..');
const CARD_ID = 'pmc13135410-fig4c-l8';
const CARD_DIR = join(__dirname, '..', 'public', 'gallery', CARD_ID);
const IMAGE_PATH = join(
  REPO_ROOT,
  'data',
  'real',
  'crops',
  'PMC13135410_Figure4__C-PDGFRa-GAPDH.png',
);

/** The rectangle the manifest measured this card at, typed in through the numeric inputs. */
const LANE_ROI = { x: 272, y: 0, width: 36, height: 122 };

const result = JSON.parse(
  readFileSync(join(CARD_DIR, 'result.json'), 'utf-8'),
) as ResultDocument;
const storedDisplay = JSON.parse(
  readFileSync(join(CARD_DIR, 'display.json'), 'utf-8'),
) as StoredDisplayBlock;
const envelope: Envelope = {
  result,
  display: {
    ...storedDisplay,
    png_base64: readFileSync(join(CARD_DIR, 'display.png')).toString('base64'),
  },
};

let sentBody: FormData | null = null;
const originalImageSrc = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');

beforeEach(() => {
  sentBody = null;
  // jsdom fetches nothing and decodes nothing. The decoder is stubbed to report the source's own
  // dimensions, which is what a browser would report for this file.
  Object.defineProperty(HTMLImageElement.prototype, 'src', {
    configurable: true,
    get(this: HTMLImageElement) {
      return this.getAttribute('src') ?? '';
    },
    set(this: HTMLImageElement, value: string) {
      this.setAttribute('src', value);
      queueMicrotask(() => this.dispatchEvent(new Event('load')));
    },
  });
  Object.defineProperty(HTMLImageElement.prototype, 'naturalWidth', {
    configurable: true,
    get: () => result.source.width_px,
  });
  Object.defineProperty(HTMLImageElement.prototype, 'naturalHeight', {
    configurable: true,
    get: () => result.source.height_px,
  });
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    value: vi.fn(() => 'blob:analyze-test'),
  });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });

  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, init: RequestInit) => {
      sentBody = init.body as FormData;
      return {
        ok: true,
        status: 200,
        json: async () => envelope,
      } as Response;
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  if (originalImageSrc !== undefined) {
    Object.defineProperty(HTMLImageElement.prototype, 'src', originalImageSrc);
  }
});

async function typeNumber(label: string, value: number): Promise<void> {
  const user = userEvent.setup();
  const input = screen.getByLabelText(label);
  await user.clear(input);
  if (value !== 0) {
    await user.type(input, String(value));
  } else {
    await user.type(input, '0');
  }
}

/**
 * Walk steps 1 and 2 exactly as a person would, and press Analyse.
 *
 * The lane is placed through the four numeric inputs and nothing else: the keyboard route is the
 * one exercised here, so a change that made the drawing surface the only way to add a lane would
 * fail this test rather than merely making the screen worse for someone without a pointer.
 */
async function walkToSubmit(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  const file = new File(
    [readFileSync(IMAGE_PATH)],
    'PMC13135410_Figure4__C-PDGFRa-GAPDH.png',
    { type: 'image/png' },
  );
  await user.upload(screen.getByLabelText(/Image file/), file);

  const next = screen.getByRole('button', { name: /Next: lanes/ });
  // No polarity is chosen yet, so there is nothing to go on with.
  expect(next).toBeDisabled();

  await user.click(screen.getByRole('radio', { name: /dark_on_bright/ }));
  expect(screen.getByRole('combobox', { name: /Parameter set/ })).toHaveValue('default');

  await waitFor(() => expect(next).toBeEnabled());
  await user.click(next);

  await user.click(screen.getByRole('button', { name: 'Add lane' }));
  await typeNumber('Lane 1 x', LANE_ROI.x);
  await typeNumber('Lane 1 y', LANE_ROI.y);
  await typeNumber('Lane 1 w', LANE_ROI.width);
  await typeNumber('Lane 1 h', LANE_ROI.height);

  expect(screen.getByTestId('submitted-order').textContent).toBe(
    `lane_roi=${LANE_ROI.x},${LANE_ROI.y},${LANE_ROI.width},${LANE_ROI.height}`,
  );
  expect(screen.getByTestId('lane-issues').textContent).toBe('');

  await user.click(screen.getByRole('button', { name: /Analyse/ }));
}

describe('upload → lanes → result, over the real page', () => {
  it('analyses a real crop at a typed lane rectangle and renders its verdict', async () => {
    const user = userEvent.setup();
    render(<AnalyzePage />);
    await walkToSubmit(user);

    // -- the request the client actually built ------------------------------------------------
    await waitFor(() => expect(sentBody).not.toBeNull());
    const form = sentBody as unknown as FormData;
    expect(form.get('config')).toBe('default');
    expect(form.get('polarity')).toBe('dark_on_bright');
    expect(form.getAll('lane_roi')).toEqual(['272,0,36,122']);
    expect((form.get('image') as File).name).toBe(
      'PMC13135410_Figure4__C-PDGFRa-GAPDH.png',
    );

    // -- step 3: the verdict, the sentence and the numbers ------------------------------------
    // Twice: once on the lane card, once in the heading of the result surface below it. Both
    // come from `verdictSentence`, which is why they cannot disagree.
    const sentences = await screen.findAllByText('No QC flags; every ratio computed.');
    expect(sentences).toHaveLength(2);
    expect(screen.getAllByText('Pass')).toHaveLength(2);

    const laneCard = screen.getByRole('button', { name: /Pass L0/ });
    expect(laneCard.textContent).toContain('3 bands · 3/3 ratios usable');

    // Every ratio the document carries is on screen at the path it came from, unrounded value
    // in the tooltip. These are the three numbers the live API returned for this rectangle.
    expect(result.normalization.ratios).toHaveLength(3);
    for (const [index, ratio] of result.normalization.ratios.entries()) {
      const path = `normalization.ratios[${index}].ratio`;
      const cell = window.document.querySelector(`[data-json-path="${path}"]`);
      expect(cell?.textContent).toBe(formatMeasured(ratio.ratio));
      expect(cell?.getAttribute('title')).toBe(`${path} = ${String(ratio.ratio)}`);
    }

    // The image-flags block is there, saying nothing fired, which is the honest empty case.
    const block = screen
      .getByRole('heading', { name: 'image-level flags (do not affect the lane verdict)' })
      .closest('section') as HTMLElement;
    expect(within(block).getByText('none recorded on this image')).toBeVisible();
  }, 30000);

  it('detects lanes, puts them in the table, and lets them be corrected before analysing', async () => {
    // PLAN.md Phase 4: "upload → auto-detected lanes/bands overlaid on the image → correction via
    // numeric nudge fields → recompute", and its done-when is that a scientist "corrects one lane
    // boundary". The detected rectangles land in the same editable rows a typed lane lands in.
    const urls: string[] = [];
    const bodies: FormData[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init: RequestInit) => {
        urls.push(url);
        bodies.push(init.body as FormData);
        return { ok: true, status: 200, json: async () => envelope } as Response;
      }),
    );

    const user = userEvent.setup();
    render(<AnalyzePage />);

    const file = new File(
      [readFileSync(IMAGE_PATH)],
      'PMC13135410_Figure4__C-PDGFRa-GAPDH.png',
      { type: 'image/png' },
    );
    await user.upload(screen.getByLabelText(/Image file/), file);
    await user.click(screen.getByRole('radio', { name: /dark_on_bright/ }));
    const next = screen.getByRole('button', { name: /Next: lanes/ });
    await waitFor(() => expect(next).toBeEnabled());
    await user.click(next);

    expect(screen.getByTestId('submitted-order').textContent).toBe('');
    await user.click(screen.getByRole('button', { name: /Detect lanes for me/ }));

    // Detection is "post with no lane_roi at all", which is what api/app.py reads as `None`.
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect((bodies[0] as FormData).getAll('lane_roi')).toEqual([]);
    expect(urls[0]).toBe('http://127.0.0.1:8000/analyze');

    // The document's lane comes back into the table as editable numbers, and its bands are
    // drawn on the surface as the context the boundary is corrected against.
    const lane = result.lanes[0];
    await waitFor(() =>
      expect(screen.getByLabelText('Lane 1 x')).toHaveValue(lane?.roi.x ?? -1),
    );
    expect(screen.getByLabelText('Lane 1 w')).toHaveValue(lane?.roi.width ?? -1);
    expect(screen.getByTestId('submitted-order').textContent).toBe(
      `lane_roi=${lane?.roi.x},${lane?.roi.y},${lane?.roi.width},${lane?.roi.height}`,
    );
    expect(screen.getByTestId('detected-band-0')).toHaveAttribute(
      'width',
      String(result.bands[0]?.roi.width),
    );

    // Correct one boundary, then recompute — the loop PLAN.md's done-when describes.
    await typeNumber('Lane 1 w', 40);
    await user.click(screen.getByRole('button', { name: /Analyse/ }));

    await waitFor(() => expect(bodies).toHaveLength(2));
    expect((bodies[1] as FormData).getAll('lane_roi')).toEqual([
      `${lane?.roi.x},${lane?.roi.y},40,${lane?.roi.height}`,
    ]);
  }, 30000);

  it('renders a detection refusal in place on step 2, without advancing', async () => {
    const body = JSON.stringify({
      detail: 'no lane cleared the prominence threshold',
      error: 'DetectionError',
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, status: 422, text: async () => body }) as Response),
    );

    const user = userEvent.setup();
    render(<AnalyzePage />);
    const file = new File([readFileSync(IMAGE_PATH)], 'crop.png', { type: 'image/png' });
    await user.upload(screen.getByLabelText(/Image file/), file);
    await user.click(screen.getByRole('radio', { name: /dark_on_bright/ }));
    const next = screen.getByRole('button', { name: /Next: lanes/ });
    await waitFor(() => expect(next).toBeEnabled());
    await user.click(next);

    await user.click(screen.getByRole('button', { name: /Detect lanes for me/ }));

    const problem = await screen.findByTestId('detection-problem');
    expect(within(problem).getByTestId('refusal-detail').textContent).toBe(
      'no lane cleared the prominence threshold',
    );
    // Still on step 2: the answer belongs beside the rectangles being edited.
    expect(screen.getByRole('button', { name: 'Add lane' })).toBeVisible();
  }, 30000);

  it('renders an unexpected failure rather than leaving a blank result screen', async () => {
    // A gateway answering 200 with an HTML page: `lib/api.ts` parses any 2xx as JSON, so this is
    // reachable without exotic conditions. Before, the throw became an unhandled rejection and
    // step 3 rendered nothing at all.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => {
          throw new SyntaxError('Unexpected token < in JSON at position 0');
        },
      }) as unknown as Response),
    );

    const user = userEvent.setup();
    render(<AnalyzePage />);
    await walkToSubmit(user);

    const card = await screen.findByTestId('unexpected-card');
    expect(within(card).getByTestId('unexpected-name').textContent).toBe('SyntaxError');
    expect(within(card).getByTestId('unexpected-message').textContent).toBe(
      'Unexpected token < in JSON at position 0',
    );
    expect(screen.queryByRole('heading', { name: 'Bands' })).toBeNull();
    expect(screen.queryByTestId('refusal-card')).toBeNull();
  }, 30000);

  it('discards lanes and detected bands when a different file is chosen', async () => {
    // The rectangles were placed on image A and mean nothing about image B. If B is larger they
    // still fit, nothing is reported, and Analyse stays enabled — a silent measurement of a
    // region nobody placed on that picture.
    const user = userEvent.setup();
    render(<AnalyzePage />);

    const first = new File([readFileSync(IMAGE_PATH)], 'a.png', { type: 'image/png' });
    await user.upload(screen.getByLabelText(/Image file/), first);
    await user.click(screen.getByRole('radio', { name: /dark_on_bright/ }));
    const next = screen.getByRole('button', { name: /Next: lanes/ });
    await waitFor(() => expect(next).toBeEnabled());
    await user.click(next);

    await user.click(screen.getByRole('button', { name: 'Add lane' }));
    expect(screen.getByTestId('submitted-order').textContent).not.toBe('');

    await user.click(screen.getByRole('button', { name: /Back/ }));
    const second = new File([readFileSync(IMAGE_PATH)], 'b.png', { type: 'image/png' });
    await user.upload(screen.getByLabelText(/Image file/), second);
    await waitFor(() => expect(screen.getByRole('button', { name: /Next: lanes/ })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: /Next: lanes/ }));

    expect(screen.getByTestId('submitted-order').textContent).toBe('');
    expect(screen.queryByLabelText('Lane 1 x')).toBeNull();
    expect(screen.queryByTestId('detected-band-0')).toBeNull();
    expect(screen.getByTestId('lane-issues').textContent).toContain('No lane has been added');
  }, 30000);

  it('will not start a detection while an analysis is in flight', async () => {
    // A detect resolving after Analyse was pressed would replace the table with rectangles the
    // submitted request never used.
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        await gate;
        return { ok: true, status: 200, json: async () => envelope } as Response;
      }),
    );

    const user = userEvent.setup();
    render(<AnalyzePage />);
    await walkToSubmit(user);

    // The analysis is still pending, so both entry points into the editor are shut.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Detect lanes for me/ })).toBeDisabled(),
    );
    expect(screen.getByRole('button', { name: 'Add lane' })).toBeDisabled();

    release();
    await screen.findAllByText('No QC flags; every ratio computed.');
  }, 30000);

  it('disables Analyse during a detection instead of leaving it enabled and inert', async () => {
    // `submit()` early-returns while `detecting`, so an enabled button here was a control that
    // did nothing for the whole round trip -- no message, no state change, no advance.
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        await gate;
        return { ok: true, status: 200, json: async () => envelope } as Response;
      }),
    );

    const user = userEvent.setup();
    render(<AnalyzePage />);
    const file = new File([readFileSync(IMAGE_PATH)], 'crop.png', { type: 'image/png' });
    await user.upload(screen.getByLabelText(/Image file/), file);
    await user.click(screen.getByRole('radio', { name: /dark_on_bright/ }));
    const next = screen.getByRole('button', { name: /Next: lanes/ });
    await waitFor(() => expect(next).toBeEnabled());
    await user.click(next);

    await user.click(screen.getByRole('button', { name: 'Add lane' }));
    expect(screen.getByRole('button', { name: /Analyse/ })).toBeEnabled();

    await user.click(screen.getByRole('button', { name: /Detect lanes for me/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: /Analyse/ })).toBeDisabled());
    expect(screen.getByText(/Waiting for the pipeline to report/)).toBeVisible();

    release();
    await waitFor(() => expect(screen.getByRole('button', { name: /Analyse/ })).toBeEnabled());
  }, 30000);

  it('reports a 200 that is not an envelope instead of rendering a blank page', async () => {
    // `analyze()` used to cast the body to `Envelope`, so a well-formed JSON body that is not
    // one threw during render, where there is no error boundary, and React unmounted the tree.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({ message: 'hello from a gateway' }),
      }) as unknown as Response),
    );

    const user = userEvent.setup();
    render(<AnalyzePage />);
    await walkToSubmit(user);

    const card = await screen.findByTestId('unexpected-card');
    expect(within(card).getByTestId('unexpected-name').textContent).toBe('ApiContractError');
    expect(within(card).getByTestId('unexpected-message').textContent).toContain(
      "it carries no 'result' object",
    );
    expect(screen.queryByRole('heading', { name: 'Bands' })).toBeNull();
  }, 30000);

  it('warns when the pipeline measured a different frame from the one lanes were drawn in', async () => {
    // A JPEG carrying an EXIF orientation tag: browsers apply it, `cv2.imdecode` does not, and
    // for a transposing orientation the two frames have different dimensions.
    const transposed: Envelope = {
      ...envelope,
      result: {
        ...envelope.result,
        source: {
          ...envelope.result.source,
          width_px: envelope.result.source.height_px,
          height_px: envelope.result.source.width_px,
        },
      },
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, status: 200, json: async () => transposed }) as Response),
    );

    const user = userEvent.setup();
    render(<AnalyzePage />);
    await walkToSubmit(user);

    const warning = await screen.findByTestId('frame-mismatch');
    expect(warning.textContent).toContain('EXIF orientation');
    expect(warning.textContent).toContain(
      `${result.source.width_px}×${result.source.height_px} px`,
    );
  }, 30000);

  it('warns on step 2, while the lanes are still editable, when detection measured another frame', async () => {
    // The detect-path half of the same hazard, and the one that matters more: these rectangles
    // are the pipeline's own, dropped onto an image the browser decoded. If the frames disagree
    // the overlay is misaligned from the first paint and the person corrects a lane boundary
    // against a picture that is not the one measured.
    //
    // An earlier revision computed this warning and rendered it nowhere -- the banner lived only
    // inside the step 3 branch, and `submit()` cleared it on the way there -- so the warning was
    // unobservable for exactly as long as it was actionable. Replacing the `setFrameMismatch`
    // call in `detect()` with `void mismatch;` left the whole suite green. This test is what
    // makes that mutation fail, so it asserts the banner is on screen at step 2, *before*
    // anything is submitted.
    const transposed: Envelope = {
      ...envelope,
      result: {
        ...envelope.result,
        source: {
          ...envelope.result.source,
          width_px: envelope.result.source.height_px,
          height_px: envelope.result.source.width_px,
        },
      },
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, status: 200, json: async () => transposed }) as Response),
    );

    const user = userEvent.setup();
    render(<AnalyzePage />);

    const file = new File(
      [readFileSync(IMAGE_PATH)],
      'PMC13135410_Figure4__C-PDGFRa-GAPDH.png',
      { type: 'image/png' },
    );
    await user.upload(screen.getByLabelText(/Image file/), file);
    await user.click(screen.getByRole('radio', { name: /dark_on_bright/ }));
    const next = screen.getByRole('button', { name: /Next: lanes/ });
    await waitFor(() => expect(next).toBeEnabled());
    await user.click(next);
    await user.click(screen.getByRole('button', { name: /Detect lanes for me/ }));

    const warning = await screen.findByTestId('frame-mismatch-lanes');
    expect(warning.textContent).toContain('EXIF orientation');
    expect(warning.textContent).toContain(
      `${transposed.result.source.width_px}×${transposed.result.source.height_px} px`,
    );
    // Still on step 2 with the lane table in hand: the warning arrives while it can still change
    // what the person does, not on the result screen afterwards.
    expect(screen.getByLabelText('Lane 1 x')).toBeInTheDocument();
  }, 30000);

  it('keeps the frame-mismatch warning when a later Analyse is refused', async () => {
    // The refusal path is the one that leaves the person still holding the lane table, so it is
    // the worst path on which to drop a warning about what those lanes are being placed against.
    // `submit()` used to clear `frameMismatch` before the request and only the *success* path
    // recomputed it, so a mismatch found at detect time vanished on exactly the outcome where it
    // still mattered.
    const transposed: Envelope = {
      ...envelope,
      result: {
        ...envelope.result,
        source: {
          ...envelope.result.source,
          width_px: envelope.result.source.height_px,
          height_px: envelope.result.source.width_px,
        },
      },
    };
    const body = readFileSync(join(__dirname, 'fixtures', 'api', 'refusal-415.json'), 'utf-8');
    let call = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        call += 1;
        return call === 1
          ? ({ ok: true, status: 200, json: async () => transposed } as Response)
          : ({ ok: false, status: 415, text: async () => body } as Response);
      }),
    );

    const user = userEvent.setup();
    render(<AnalyzePage />);
    const file = new File(
      [readFileSync(IMAGE_PATH)],
      'PMC13135410_Figure4__C-PDGFRa-GAPDH.png',
      { type: 'image/png' },
    );
    await user.upload(screen.getByLabelText(/Image file/), file);
    await user.click(screen.getByRole('radio', { name: /dark_on_bright/ }));
    const next = screen.getByRole('button', { name: /Next: lanes/ });
    await waitFor(() => expect(next).toBeEnabled());
    await user.click(next);

    await user.click(screen.getByRole('button', { name: /Detect lanes for me/ }));
    await screen.findByTestId('frame-mismatch-lanes');

    await user.click(screen.getByRole('button', { name: /Analyse/ }));
    await screen.findByTestId('refusal-card');

    // The refusal is shown *and* the warning survives it.
    const warning = await screen.findByTestId('frame-mismatch');
    expect(warning.textContent).toContain('EXIF orientation');
  }, 30000);

  it('shows a refusal, not a result, when the API refuses the request', async () => {
    // The genuine 415 body captured from the running service; see test/refusal.test.tsx.
    const body = readFileSync(join(__dirname, 'fixtures', 'api', 'refusal-415.json'), 'utf-8');
    const detail = (JSON.parse(body) as { detail: string }).detail;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, status: 415, text: async () => body }) as Response),
    );

    const user = userEvent.setup();
    render(<AnalyzePage />);
    await walkToSubmit(user);

    const card = await screen.findByTestId('refusal-card');
    expect(within(card).getByTestId('refusal-detail').textContent).toBe(detail);
    expect(within(card).getByTestId('refusal-error').textContent).toBe(
      'UnsupportedBitDepthError',
    );
    expect(within(card).getByTestId('refusal-status').textContent).toBe('415');
    // No result surface is rendered beside it: there is no number to show.
    expect(screen.queryByRole('heading', { name: 'Bands' })).toBeNull();
  }, 30000);
});
