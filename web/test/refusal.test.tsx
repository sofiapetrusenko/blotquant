import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { RefusalCard, UnreachableCard } from '@/app/analyze/RefusalCard';
import { ApiRefusal, ApiUnreachable, type ValidationDetail } from '@/lib/api';

/**
 * A refusal reaches the reader exactly as the API wrote it.
 *
 * **The fixtures are real bodies, captured from a running service**, not prose invented here.
 * `refusal-415.json` is the answer to a float32 TIFF posted to `POST /analyze` -- the message is
 * `pipeline/load.py`'s, complete with the temporary path the upload was written to, which is
 * itself an argument for verbatim rendering: no client could have reconstructed it.
 * `refusal-422-request-validation.json` is FastAPI's own answer to a request that omitted the
 * `config` form field, which is the *other* 422 shape, with a list `detail` and no `error` key.
 *
 * What is asserted is equality with the fixture, not a substring match: a card that truncated the
 * message, wrapped it, or replaced the path with something friendlier would pass a `toContain`
 * and fail here, and truncation is the exact failure mode this whole design is against.
 */

const FIXTURE_DIR = join(__dirname, 'fixtures', 'api');

function body(name: string): { detail: unknown; error?: unknown } {
  return JSON.parse(readFileSync(join(FIXTURE_DIR, name), 'utf-8')) as {
    detail: unknown;
    error?: unknown;
  };
}

describe('RefusalCard, on a real 415 from the pipeline', () => {
  const fixture = body('refusal-415.json');
  const detail = fixture.detail as string;
  const refusal = new ApiRefusal(415, detail, fixture.error as string);

  it('renders the API message verbatim, whole', () => {
    render(<RefusalCard refusal={refusal} />);
    const rendered = screen.getByTestId('refusal-detail');
    expect(rendered.textContent).toBe(detail);
    expect(rendered.textContent).toHaveLength(detail.length);
  });

  it('shows the status and the exception class the API named', () => {
    render(<RefusalCard refusal={refusal} />);
    expect(screen.getByTestId('refusal-status').textContent).toBe('415');
    expect(screen.getByTestId('refusal-error').textContent).toBe('UnsupportedBitDepthError');
    expect(fixture.error).toBe('UnsupportedBitDepthError');
  });

  it('keeps the part a caller can act on: the pixel type and the supported set', () => {
    render(<RefusalCard refusal={refusal} />);
    const text = screen.getByTestId('refusal-detail').textContent ?? '';
    expect(text).toContain('float32');
    expect(text).toContain('uint8 and uint16');
  });
});

describe("RefusalCard, on FastAPI's own list-shaped 422", () => {
  const fixture = body('refusal-422-request-validation.json');
  const details = fixture.detail as ValidationDetail[];
  const refusal = new ApiRefusal(422, details, null);

  it('is recognised as request validation rather than a pipeline refusal', () => {
    expect(refusal.isRequestValidation).toBe(true);
    expect(fixture.error).toBeUndefined();
  });

  it('renders every field of every entry rather than stringifying the list', () => {
    render(<RefusalCard refusal={refusal} />);
    expect(details.length).toBeGreaterThan(0);
    for (const entry of details) {
      for (const [key, value] of Object.entries(entry)) {
        expect(screen.getByText(key)).toBeInTheDocument();
        const shown = typeof value === 'string' ? value : JSON.stringify(value);
        expect(screen.getByText(shown)).toBeInTheDocument();
      }
    }
    // The failure this asserts against: `${detail}` on an array of objects.
    expect(screen.queryByText(/\[object Object\]/)).toBeNull();
  });

  it('says which field is missing, in the words FastAPI used', () => {
    render(<RefusalCard refusal={refusal} />);
    expect(screen.getByText('["body","config"]')).toBeInTheDocument();
    expect(screen.getByText('Field required')).toBeInTheDocument();
  });
});

describe('UnreachableCard', () => {
  it('names the base URL it tried and the CORS prerequisite', () => {
    const error = new ApiUnreachable('http://127.0.0.1:8000', new TypeError('Failed to fetch'));
    render(<UnreachableCard error={error} />);
    expect(screen.getByTestId('unreachable-base-url').textContent).toBe('http://127.0.0.1:8000');
    expect(screen.getByText(/CORS/)).toBeInTheDocument();
    // A network failure is a different card from a refusal; neither may be shown as the other.
    expect(screen.queryByTestId('refusal-card')).toBeNull();
  });
});
