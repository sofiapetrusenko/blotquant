import type { ApiRefusal, ApiUnreachable, ValidationDetail } from '@/lib/api';

/**
 * What the API said, said back.
 *
 * **The `detail` string is rendered verbatim.** Not truncated, not sentence-cased, not replaced
 * with friendlier copy. Every refusal this service issues is written to be actionable -- it names
 * the pixel type it will not squash, the rectangle that fell off the image, the config that does
 * not exist and the names that do -- and a card that paraphrased it would throw away the only
 * part the reader can act on. A refusal is a first-class result of this tool, not an error state
 * to apologise for: refusing to produce an unpublishable number is the product.
 *
 * **Two shapes of 422 arrive and both are shown as they are.** This service answers a well formed
 * request it could not carry out with a string `detail` beside an `error` naming the exception
 * class; FastAPI's own request validation answers with a *list* of per-field objects and no
 * `error`. The list is rendered field by field, key by key, rather than stringified -- a
 * `[object Object]` or a wall of JSON on one line would be the same information made unreadable.
 * Each entry's keys are printed as they arrive, so a shape this file has not anticipated still
 * reaches the reader.
 */

function renderValue(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value);
}

function ValidationEntries({ details }: { details: ValidationDetail[] }): React.ReactElement {
  return (
    <ol className="mt-3 grid gap-3">
      {details.map((detail, index) => (
        <li
          key={index}
          className="border border-[var(--color-hairline)] bg-[var(--color-surface-raised)] p-2"
        >
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
            {Object.entries(detail).map(([key, value]) => (
              <div key={key} className="contents">
                <dt className="tabular text-[var(--color-ink-muted)]">{key}</dt>
                <dd className="tabular break-words">{renderValue(value)}</dd>
              </div>
            ))}
          </dl>
        </li>
      ))}
    </ol>
  );
}

export function RefusalCard({ refusal }: { refusal: ApiRefusal }): React.ReactElement {
  return (
    <section
      aria-labelledby="refusal-heading"
      className="border-2 p-4"
      style={{ borderColor: 'var(--color-verdict-blocked)' }}
      data-testid="refusal-card"
    >
      <h2
        id="refusal-heading"
        className="flex items-center gap-2 text-base font-semibold"
        style={{ color: 'var(--color-verdict-blocked)' }}
      >
        <span aria-hidden="true">■</span>
        <span>The API refused this request</span>
      </h2>
      <p className="tabular mt-1 text-xs text-[var(--color-ink-muted)]">
        HTTP <span data-testid="refusal-status">{refusal.status}</span>
        {refusal.error !== null && (
          <>
            {' · '}
            <span data-testid="refusal-error">{refusal.error}</span>
          </>
        )}
        {refusal.isRequestValidation && ' · request validation (FastAPI)'}
      </p>

      {typeof refusal.detail === 'string' ? (
        <p
          data-testid="refusal-detail"
          className="mt-3 border-l-2 border-[var(--color-hairline-strong)] pl-3 text-sm whitespace-pre-wrap"
        >
          {refusal.detail}
        </p>
      ) : (
        <ValidationEntries details={refusal.detail} />
      )}

      <p className="mt-3 text-xs text-[var(--color-ink-muted)]">
        This message is the API&rsquo;s own, shown exactly as it was sent. Fix what it names and
        go back a step.
      </p>
    </section>
  );
}

/**
 * The API was never reached, which is a different thing from being refused by it.
 *
 * Named separately because the fix is different and lives somewhere else: nothing is listening,
 * or the browser blocked a cross-origin request because `api/app.py` installs no CORS middleware.
 * The card names the base URL it tried, so a reader who built the site against the wrong
 * `NEXT_PUBLIC_API_URL` can see it, and quotes the client's own message, which carries the
 * command that starts a server.
 */
export function UnreachableCard({ error }: { error: ApiUnreachable }): React.ReactElement {
  return (
    <section
      aria-labelledby="unreachable-heading"
      className="border-2 border-[var(--color-hairline-strong)] p-4"
      data-testid="unreachable-card"
    >
      <h2 id="unreachable-heading" className="text-base font-semibold">
        The API could not be reached
      </h2>
      <p className="tabular mt-1 text-xs text-[var(--color-ink-muted)]">
        base URL <span data-testid="unreachable-base-url">{error.baseUrl}</span>
      </p>
      <p className="mt-3 text-sm">{error.message}</p>
    </section>
  );
}

/**
 * Something happened that neither the API nor the client had a name for.
 *
 * The alternative was to rethrow, which on a click handler becomes an unhandled rejection no
 * error boundary catches — and leaves the screen showing nothing at all. A blank result page is
 * the worst outcome this tool can produce: it is indistinguishable from a result with no
 * findings. So the error is shown, with its class and its message, and the fact that it was not
 * anticipated is stated rather than dressed up as a known failure.
 *
 * Reachable without exotic conditions: `lib/api.ts` parses any 2xx body as JSON, so a proxy or
 * gateway answering 200 with an HTML page lands here.
 */
export function UnexpectedCard({ error }: { error: Error }): React.ReactElement {
  return (
    <section
      aria-labelledby="unexpected-heading"
      className="border-2 border-[var(--color-hairline-strong)] p-4"
      data-testid="unexpected-card"
    >
      <h2 id="unexpected-heading" className="text-base font-semibold">
        Something went wrong that this screen does not have a name for
      </h2>
      <p className="tabular mt-1 text-xs text-[var(--color-ink-muted)]">
        <span data-testid="unexpected-name">{error.name}</span>
      </p>
      <p
        className="mt-3 border-l-2 border-[var(--color-hairline-strong)] pl-3 text-sm whitespace-pre-wrap"
        data-testid="unexpected-message"
      >
        {error.message}
      </p>
      <p className="mt-3 text-xs text-[var(--color-ink-muted)]">
        This is not a refusal from the API and not a failure to reach it. No result was produced,
        and none is shown.
      </p>
    </section>
  );
}
