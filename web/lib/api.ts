import type { Envelope } from '@/lib/result';

/**
 * The client for `POST /analyze`.
 *
 * **The API's own message is never paraphrased.** Every refusal this service issues is written to
 * be actionable -- it names the lane rectangle that fell off the image, the config that does not
 * exist, the pixel type it will not squash -- and a client that replaced those with "Something
 * went wrong" would throw away the only part a user can act on. `ApiRefusal` carries `detail`
 * verbatim, in whichever of its two shapes it arrived in, and the refusal card renders it.
 *
 * See `api/app.py`: **two different things return 422.** FastAPI's own request validation answers
 * with `detail` as a *list* of per-field objects and no `error` key; this service answers a well
 * formed request it could not carry out with `detail` as a *string* and an `error` key naming the
 * exception class. The `error` key is the discriminator, not the status.
 */

/**
 * The base URL of the API. `NEXT_PUBLIC_API_URL` is read at build time by Next's static export,
 * so a deployed site talks to the deployed API and a developer's build talks to their own.
 */
export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://127.0.0.1:8000';

/** One entry of FastAPI's own request-validation `detail` list. Rendered, never interpreted. */
export interface ValidationDetail {
  loc?: unknown[];
  msg?: string;
  type?: string;
  [key: string]: unknown;
}

/**
 * The API refused the request, and this carries what it said.
 *
 * `detail` is a string when the request reached the pipeline and a list when it failed this
 * endpoint's own request validation; `error` is present only in the first case. Both shapes are
 * kept as they arrived so the UI can render either without the client having decided which.
 */
export class ApiRefusal extends Error {
  readonly status: number;
  readonly detail: string | ValidationDetail[];
  /** The exception class name, when the request reached the pipeline. */
  readonly error: string | null;

  constructor(status: number, detail: string | ValidationDetail[], error: string | null) {
    super(typeof detail === 'string' ? detail : `request validation failed (${status})`);
    this.name = 'ApiRefusal';
    this.status = status;
    this.detail = detail;
    this.error = error;
  }

  /** True when this is FastAPI's own request validation rather than a pipeline refusal. */
  get isRequestValidation(): boolean {
    return this.error === null && Array.isArray(this.detail);
  }
}

export const ANALYZE_TIMEOUT_MS = 600_000;
/**
 * How long to wait for an answer before giving up on the request.
 *
 * An I/O bound, not a processing parameter: nothing measured depends on it, and it matches the
 * read timeout `tools/gallery/build.py` uses for the same endpoint. It exists because without it
 * a server that accepts the connection and never answers leaves the screen busy forever, with
 * the Back button disabled -- a state indistinguishable from a hung browser, and the one failure
 * this client could produce that says nothing at all.
 */

/** The API gave no answer: nothing is listening, the browser blocked it, or it timed out. */
export class ApiUnreachable extends Error {
  readonly baseUrl: string;
  /** True when a connection was made and no answer arrived within `ANALYZE_TIMEOUT_MS`. */
  readonly timedOut: boolean;

  constructor(baseUrl: string, cause: unknown, timedOut = false) {
    super(
      timedOut
        ? `the blotquant API at ${baseUrl} accepted the request and sent no answer within ` +
            `${ANALYZE_TIMEOUT_MS / 1000} s, so the client stopped waiting. The analysis may ` +
            `still be running on the server; nothing was measured here and nothing is shown`
        : `could not reach the blotquant API at ${baseUrl}. Either no server is listening there ` +
            `-- start one with 'python -m api --storage-root results/ --config-dir configs/' -- ` +
            `or the browser blocked the request: a page served from another origin needs CORS ` +
            `enabled on the API, and the API installs no CORS middleware today. See ` +
            `web/README.md`,
    );
    this.name = 'ApiUnreachable';
    this.baseUrl = baseUrl;
    this.timedOut = timedOut;
    this.cause = cause;
  }
}

/**
 * A 2xx whose body is not the envelope this service documents.
 *
 * **Why this exists rather than a cast.** `POST /analyze` answers `{result, display}`, and the
 * client used to assert that with `as Envelope` and hand the value on. A 200 carrying well formed
 * JSON that is not an envelope -- version skew, a gateway's own JSON error body -- then threw
 * during *render*, deep inside a component, where there is no error boundary: React unmounts the
 * tree and the reader gets a blank page. A blank result page is the worst outcome this tool can
 * produce, because it is indistinguishable from a result with no findings. Checking the shape at
 * the boundary moves that failure back into the promise, where the screen has a card for it.
 *
 * The check is deliberately shallow: the keys the screens actually index into, and their kinds.
 * It is not a schema validator -- the API already validates against `schema/result.schema.json`
 * before serving, and re-implementing that here would be a second contract to keep in step.
 */
export class ApiContractError extends Error {
  readonly baseUrl: string;

  constructor(baseUrl: string, detail: string) {
    super(
      `the blotquant API at ${baseUrl} answered 200 with a body that is not a ` +
        `{result, display} envelope: ${detail}. Nothing was measured here and nothing is shown`,
    );
    this.name = 'ApiContractError';
    this.baseUrl = baseUrl;
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Return `body` as an `Envelope`, or throw `ApiContractError` naming the first thing missing.
 *
 * Checks only what the screens index into before anything is rendered: the two halves of the
 * envelope, the arrays `laneVerdicts` walks, and the display fields the picture is built from.
 */
function requireEnvelope(body: unknown, baseUrl: string): Envelope {
  if (!isObject(body)) {
    throw new ApiContractError(baseUrl, `the body is ${body === null ? 'null' : typeof body}`);
  }
  const { result, display } = body as { result?: unknown; display?: unknown };
  if (!isObject(result)) {
    throw new ApiContractError(baseUrl, "it carries no 'result' object");
  }
  if (!isObject(display)) {
    throw new ApiContractError(baseUrl, "it carries no 'display' object");
  }
  for (const key of ['lanes', 'bands', 'image_qc_flags'] as const) {
    if (!Array.isArray(result[key])) {
      throw new ApiContractError(baseUrl, `result.${key} is not an array`);
    }
  }
  const normalization = result['normalization'];
  if (!isObject(normalization) || !Array.isArray(normalization['ratios'])) {
    throw new ApiContractError(baseUrl, 'result.normalization.ratios is not an array');
  }
  if (!isObject(result['provenance']) || !isObject(result['source'])) {
    throw new ApiContractError(baseUrl, 'result.provenance or result.source is missing');
  }
  if (typeof display['png_base64'] !== 'string' || typeof display['media_type'] !== 'string') {
    throw new ApiContractError(baseUrl, 'display.png_base64 or display.media_type is missing');
  }
  if (typeof display['width_px'] !== 'number' || typeof display['height_px'] !== 'number') {
    throw new ApiContractError(baseUrl, 'display.width_px or display.height_px is not a number');
  }
  if (!isObject(display['mapping'])) {
    throw new ApiContractError(baseUrl, 'display.mapping is missing');
  }
  return body as unknown as Envelope;
}

/** One analysis request. `laneRois` are `"x,y,width,height"` strings, in lane order. */
export interface AnalyzeRequest {
  image: File;
  config: string;
  polarity: string;
  laneRois: readonly string[];
}

/**
 * Read a refused response's body into the two shapes `api/app.py` documents.
 *
 * A body that is neither -- a proxy's HTML error page, say -- becomes a string `detail` holding
 * the raw text, because showing what actually came back is more useful than reporting that it
 * could not be classified.
 */
async function refusalFrom(response: Response): Promise<ApiRefusal> {
  const text = await response.text();
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    return new ApiRefusal(response.status, text, null);
  }
  if (typeof body !== 'object' || body === null || !('detail' in body)) {
    return new ApiRefusal(response.status, text, null);
  }
  const detail = (body as { detail: unknown }).detail;
  const error = (body as { error?: unknown }).error;
  if (typeof detail === 'string') {
    return new ApiRefusal(response.status, detail, typeof error === 'string' ? error : null);
  }
  if (Array.isArray(detail)) {
    return new ApiRefusal(
      response.status,
      detail as ValidationDetail[],
      typeof error === 'string' ? error : null,
    );
  }
  // Neither documented shape. The raw body is more use than a cast that would make the refusal
  // card map over something that is not a list.
  return new ApiRefusal(response.status, text, typeof error === 'string' ? error : null);
}

/**
 * Analyse one image and return the envelope.
 *
 * The form carries one `lane_roi` field per ROI, appended in the order given, because the
 * pipeline numbers supplied lanes in the order they arrive. Throws `ApiRefusal` for any non-2xx,
 * carrying the API's own `detail`, and `ApiUnreachable` when the request never got an answer.
 */
export async function analyze(
  request: AnalyzeRequest,
  baseUrl: string = API_BASE_URL,
): Promise<Envelope> {
  const form = new FormData();
  form.append('image', request.image, request.image.name);
  form.append('config', request.config);
  form.append('polarity', request.polarity);
  for (const roi of request.laneRois) {
    form.append('lane_roi', roi);
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ANALYZE_TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetch(`${baseUrl.replace(/\/+$/, '')}/analyze`, {
      method: 'POST',
      body: form,
      signal: controller.signal,
    });
  } catch (cause) {
    throw new ApiUnreachable(baseUrl, cause, controller.signal.aborted);
  } finally {
    clearTimeout(timer);
  }
  if (!response.ok) {
    throw await refusalFrom(response);
  }
  return requireEnvelope(await response.json(), baseUrl);
}
