'use client';

import { useId, useState } from 'react';

import { Measured, Traced } from '@/components/Measured';
import { RoiOverlay, type OverlayShape } from '@/components/RoiOverlay';
import { VerdictBadge } from '@/components/VerdictBadge';
import { exclusionReasonOf, ratioReference } from '@/lib/result-cells';
import type { Band, Ratio, ResultDocument, StoredDisplayBlock } from '@/lib/result';
import { laneVerdicts } from '@/lib/verdict';
import { verdictSentence } from '@/lib/verdict-sentence';

/**
 * One lane of one result, shown whole: the picture, the numbers, and where each number came from.
 *
 * **Rendered verbatim by both result surfaces.** `/result/[id]` shows a stored gallery card and
 * `/analyze` step 3 shows a result the API produced a second ago, and they render this same
 * component with the same props. There is no second copy: a card page and a live page that
 * disagreed about how a flagged ratio is displayed would be two products.
 *
 * **Every number is traceable.** Each measured cell goes through `Measured`, which stamps the
 * value's path in the result document onto the element and puts the unrounded value in its
 * tooltip. Nothing here computes a measurement -- the verdict is the pinned mirror in
 * `lib/verdict.ts`, the sentence is `lib/verdict-sentence.ts`, and every other cell is a field
 * read out of the document at the path it advertises.
 *
 * **The trust feature.** Activating a row highlights that band's rectangle on the image and
 * opens the provenance panel for it: the config id, the ROI, the *entire* parameter set, and the
 * display mapping. That is PLAN.md Phase 4's "click any number -> its ROI highlights and the
 * full parameter set is shown", and it is why the panel prints `provenance.parameters` in full
 * rather than the four or five keys a designer would pick.
 *
 * **Image flags sit in their own de-emphasised block, and it is always present.** Ruling 3 of
 * 2026-08-25: `image_qc_flags` is not an input to the lane verdict, so a saturated image does not
 * make a clean lane blocked. The block is subordinate -- sunken surface, muted ink, below the
 * tables -- but it is never hidden when empty, because a reader who could not find it would have
 * no way to tell "no image flags fired" from "image flags were never checked".
 */

export interface ResultViewProps {
  /** The result document, exactly as the service served it. Never mutated, never added to. */
  document: ResultDocument;
  /** The picture's labelling: the dimensions the ROIs index, and the mapping that produced it. */
  display: StoredDisplayBlock;
  /** Where the picture is: a committed PNG for a gallery card, a `data:` URL for a fresh one. */
  imageSrc: string;
  /** Which lane of the document this view is about. */
  laneId: string;
  /**
   * The download. A gallery card points at its committed `result.json` so the reader gets the
   * byte-identical file the site was built from; `/analyze` points at a blob of the response,
   * because that document exists nowhere else yet. `null` while that blob is still being made,
   * so the first paint offers no link rather than a link to nowhere.
   */
  download: { href: string; filename: string } | null;
}

/** The `<th>` styling shared by both tables. Column headers are labels, not data. */
const TH = 'border-b border-[var(--color-hairline-strong)] px-2 py-1.5 text-left font-medium';
const TD = 'border-b border-[var(--color-hairline)] px-2 py-1.5 align-top';
const ROW_BUTTON =
  'w-full cursor-pointer text-left underline decoration-dotted underline-offset-2 ' +
  'aria-pressed:no-underline aria-pressed:font-semibold';

/**
 * Return the recorded reason a band was excluded from normalization, or throw naming the gap.
 *
 * `schema/result.schema.json` requires `exclusion_reason` whenever
 * `excluded_from_normalization` is true. Showing "excluded" with no cause is the annotation
 * without the information in it.
 */
function bandExclusionReason(band: Band, index: number): string {
  if (band.exclusion_reason === undefined) {
    throw new Error(
      `bands[${index}] is excluded from normalization and records no exclusion_reason; the ` +
        `schema requires one, and a band withheld without a recorded cause cannot be shown as ` +
        `withheld for a reason`,
    );
  }
  return band.exclusion_reason;
}

export const SINGLE_PIXEL_EXTENT = 1;
/**
 * A band ROI extent, in px, at which the region has no width or height to integrate across.
 *
 * Not a threshold and not a filter: nothing is excluded, reweighted or hidden on the strength of
 * it, and no parameter moves. It decides one sentence of *display* — a rectangle one pixel across
 * is a detection a reader should be told about, because a ratio derived from it looks exactly
 * like a ratio derived from a band until you read its ROI.
 *
 * Ruled 2026-08-25: the pass card "names what its third ratio came from", and the same ruling
 * forbids any parameter moving to suppress that ratio. So it is displayed like the other two and
 * the note below says what it is, derived from `bands[i].roi` in the document — the card states
 * a geometry it can point at, never a story about where the region sits.
 */

/** An excluded ratio's value: struck through, which is a mark rather than a colour. */
const EXCLUDED_VALUE = 'line-through decoration-2 text-[var(--color-ink-muted)]';

function flagText(flags: readonly string[]): string {
  return flags.length === 0 ? 'none' : flags.join(', ');
}

/**
 * A QC flag list, distinguishing a recorded empty list from a key that is not there.
 *
 * `Ratio.qc_flags` and `Ratio.reference_qc_flags` are **optional** under
 * `schema/result.schema.json`, and `web/test/fixtures/verdict/optional_ratio_flag_keys_are_absent
 * .json` exists because documents without them are real. Rendering an absent key as "none" would
 * report a fact the document does not carry, and stamping a `data-json-path` on it would
 * advertise a path that resolves to nothing -- breaking the traceability claim in the one shape
 * most likely to break it. This is the same refusal `lib/result-cells.ts` makes for the reference
 * column, and the two behave alike.
 */
function FlagCell({
  flags,
  path,
}: {
  flags: readonly string[] | undefined;
  path: string;
}): React.ReactElement {
  if (flags === undefined) {
    return (
      <span className="text-xs text-[var(--color-ink-faint)]" title={`${path} is not present`}>
        not recorded
      </span>
    );
  }
  return <Traced text={flagText(flags)} path={path} value={flags} className="text-xs" />;
}

export function ResultView({
  document: result,
  display,
  imageSrc,
  laneId,
  download,
}: ResultViewProps): React.ReactElement {
  const headingIds = useId();
  const [activeBandId, setActiveBandId] = useState<string | null>(null);

  const laneIndex = result.lanes.findIndex((candidate) => candidate.lane_id === laneId);
  const lane = laneIndex === -1 ? undefined : result.lanes[laneIndex];
  if (lane === undefined) {
    throw new Error(
      `the document has no lane ${JSON.stringify(laneId)}; it lists ` +
        `${JSON.stringify(result.lanes.map((candidate) => candidate.lane_id))}`,
    );
  }

  const verdict = laneVerdicts({ ...result } as Record<string, unknown>).find(
    (candidate) => candidate.lane_id === laneId,
  );
  if (verdict === undefined) {
    throw new Error(
      `no verdict was derived for lane ${JSON.stringify(laneId)}, although the document lists ` +
        `it; this is a defect in lib/verdict.ts rather than in the document`,
    );
  }

  // Indices are kept beside every row because they are half of the traceability claim: the path
  // a cell advertises is the path in *this document*, not a position in a filtered list.
  const bands: { band: Band; index: number }[] = result.bands
    .map((band, index) => ({ band, index }))
    .filter((entry) => entry.band.lane_id === laneId);
  const ratios: { ratio: Ratio; index: number }[] = result.normalization.ratios
    .map((ratio, index) => ({ ratio, index }))
    .filter((entry) => entry.ratio.lane_id === laneId);

  // A selection that names no band of *this* lane is no selection. That is what keeps the
  // highlight honest when the caller switches lanes without remounting: the state cannot point
  // at a rectangle that is not on screen.
  const activeBand =
    bands.find((entry) => entry.band.band_id === activeBandId) ?? null;

  const toggle = (bandId: string): void => {
    setActiveBandId((current) => (current === bandId ? null : bandId));
  };

  // Derived from the document, never from the manifest or a hard-coded phrase: the bands of this
  // lane whose ROI is a single pixel across in either direction.
  //
  // The note this feeds is worded about **bands**, not about ratio rows, because that is what is
  // filtered here. An earlier wording said "One ratio below comes from ..." -- true of all six
  // committed cards, every one of which is `total_protein` so every band has a ratio row, but
  // false the moment a single-pixel band emits no ratio (a designated housekeeping reference is
  // skipped as a numerator), which would print a sentence about a row that is not in the table.
  const singlePixelBands = bands.filter(
    ({ band }) =>
      band.roi.width <= SINGLE_PIXEL_EXTENT || band.roi.height <= SINGLE_PIXEL_EXTENT,
  );

  const shapes: OverlayShape[] = [
    {
      id: `lane-${lane.lane_id}`,
      roi: lane.roi,
      kind: 'lane',
      label: `${lane.lane_id} (${lane.roi_source})`,
    },
    ...bands.map(({ band }) => ({
      id: `band-${band.band_id}`,
      roi: band.roi,
      kind: 'band' as const,
      highlighted: activeBand?.band.band_id === band.band_id,
    })),
  ];

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <div>
        <RoiOverlay
          src={imageSrc}
          alt={
            `Display rendering of the analysed image, with lane ${lane.lane_id} outlined and ` +
            `its ${bands.length} band region(s) drawn inside it.`
          }
          width={display.width_px}
          height={display.height_px}
          shapes={shapes}
          className="border border-[var(--color-hairline-strong)]"
        />
        <p className="mt-2 text-xs text-[var(--color-ink-muted)]">
          {display.note}
        </p>
      </div>

      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-3">
          <VerdictBadge verdict={verdict.verdict} size="header" />
          <span className="tabular text-sm text-[var(--color-ink-muted)]">
            lane{' '}
            <Traced
              text={verdict.lane_id}
              path={`lanes[${laneIndex}].lane_id`}
              value={verdict.lane_id}
            />{' '}
            ·{' '}
            <Traced
              text={verdict.roi_source}
              path={`lanes[${laneIndex}].roi_source`}
              value={verdict.roi_source}
            />{' '}
            ROI
          </span>
        </div>
        <p className="mt-2 text-sm">{verdictSentence(verdict)}</p>

        <h3 className="mt-6 text-sm font-semibold">Bands</h3>
        <p className="mt-1 text-xs text-[var(--color-ink-muted)]">
          Activate a row to highlight its region on the image and open its provenance.
        </p>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr>
                <th scope="col" className={TH}>
                  band
                </th>
                <th scope="col" className={TH}>
                  integrated intensity
                </th>
                <th scope="col" className={TH}>
                  background
                </th>
                <th scope="col" className={TH}>
                  qc_flags
                </th>
                <th scope="col" className={TH}>
                  in normalization
                </th>
              </tr>
            </thead>
            <tbody>
              {bands.map(({ band, index }) => {
                const selected = activeBand?.band.band_id === band.band_id;
                return (
                  <tr
                    key={band.band_id}
                    onClick={() => toggle(band.band_id)}
                    aria-current={selected ? 'true' : undefined}
                    className={selected ? 'bg-[var(--color-surface-sunken)]' : undefined}
                  >
                    <th scope="row" className={`${TD} font-normal`}>
                      <button
                        type="button"
                        aria-pressed={selected}
                        onClick={(event) => {
                          event.stopPropagation();
                          toggle(band.band_id);
                        }}
                        className={`tabular ${ROW_BUTTON}`}
                      >
                        {band.band_id}
                      </button>
                    </th>
                    <td className={TD}>
                      <Measured
                        value={band.integrated_intensity}
                        path={`bands[${index}].integrated_intensity`}
                      />
                    </td>
                    <td className={TD}>
                      <Measured
                        value={band.background_estimate}
                        path={`bands[${index}].background_estimate`}
                      />
                    </td>
                    <td className={TD}>
                      <Traced
                        text={flagText(band.qc_flags)}
                        path={`bands[${index}].qc_flags`}
                        value={band.qc_flags}
                        className="text-xs"
                      />
                    </td>
                    <td className={TD}>
                      {/*
                        `excluded_from_normalization` is schema-required and true for most bands
                        on a saturated card. A band that fed no ratio, shown as an ordinary
                        number with nothing beside it, is the record's "exclusion is explicit and
                        recorded" discipline abandoned at the display layer -- and the Ratios
                        table below already gets this right for the ratios those bands feed.
                      */}
                      <Traced
                        text={band.excluded_from_normalization ? 'excluded' : 'included'}
                        path={`bands[${index}].excluded_from_normalization`}
                        value={band.excluded_from_normalization}
                        className={
                          band.excluded_from_normalization
                            ? 'text-xs font-medium'
                            : 'text-xs text-[var(--color-ink-muted)]'
                        }
                      />
                      {band.excluded_from_normalization && (
                        <Traced
                          text={bandExclusionReason(band, index)}
                          path={`bands[${index}].exclusion_reason`}
                          value={bandExclusionReason(band, index)}
                          className="mt-0.5 block text-xs text-[var(--color-ink-muted)]"
                        />
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <h3 className="mt-6 text-sm font-semibold">Ratios</h3>
        {singlePixelBands.length > 0 && (
          <p
            data-testid="single-pixel-note"
            className="mt-1 border-l-2 border-[var(--color-hairline-strong)] py-0.5 pl-2 text-xs"
          >
            {singlePixelBands.length === 1 ? 'One band in this lane was' : 'Bands in this lane were'}{' '}
            detected one pixel across in one direction:{' '}
            {singlePixelBands.map(({ band, index }, position) => (
              <span key={band.band_id}>
                {position > 0 && ', '}
                <Traced
                  text={band.band_id}
                  path={`bands[${index}].band_id`}
                  value={band.band_id}
                  className="tabular"
                />
                {' at '}
                <Measured value={band.roi.width} path={`bands[${index}].roi.width`} />
                {' × '}
                <Measured value={band.roi.height} path={`bands[${index}].roi.height`} />
                {' px'}
              </span>
            ))}
            . Any ratio such a band produced is reported like every other — nothing is hidden and
            no parameter moved to suppress it — and a region one pixel wide is a single column with
            no width to integrate over, one pixel tall a single row with no height.
          </p>
        )}
        {/*
          `normalization.warnings` is required by schema/result.schema.json and was rendered
          nowhere -- including on cards whose every ratio was excluded *because* of what these
          warnings say. Its vocabulary is the normalization stage's own QC annotation:
          `single_housekeeping_reference` is the journal-guideline warning this project exists to
          surface, and `qc_flagged_bands_included_by_override` records that the default exclusion
          was overridden. A display that dropped them kept the numbers and discarded the caveats
          attached to them.
        */}
        <p className="mt-1 text-xs">
          <span className="text-[var(--color-ink-muted)]">
            normalization ({result.normalization.mode}, exclude_qc_flagged{' '}
            {String(result.normalization.exclude_qc_flagged)}) —{' '}
          </span>
          <Traced
            text={
              result.normalization.warnings.length === 0
                ? 'no warnings'
                : `warnings: ${result.normalization.warnings.join(', ')}`
            }
            path="normalization.warnings"
            value={result.normalization.warnings}
            className={
              result.normalization.warnings.length === 0
                ? 'text-[var(--color-ink-muted)]'
                : 'font-medium'
            }
          />
        </p>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr>
                <th scope="col" className={TH}>
                  numerator
                </th>
                <th scope="col" className={TH}>
                  reference
                </th>
                <th scope="col" className={TH}>
                  value
                </th>
                <th scope="col" className={TH}>
                  qc_flags
                </th>
                <th scope="col" className={TH}>
                  reference_qc_flags
                </th>
              </tr>
            </thead>
            <tbody>
              {ratios.map(({ ratio, index }) => {
                const selected = activeBand?.band.band_id === ratio.numerator_band_id;
                const reference = ratioReference(result, index);
                return (
                  <tr
                    key={`${ratio.numerator_band_id}-${index}`}
                    onClick={() => toggle(ratio.numerator_band_id)}
                    aria-current={selected ? 'true' : undefined}
                    className={selected ? 'bg-[var(--color-surface-sunken)]' : undefined}
                  >
                    <th scope="row" className={`${TD} font-normal`}>
                      <button
                        type="button"
                        aria-pressed={selected}
                        onClick={(event) => {
                          event.stopPropagation();
                          toggle(ratio.numerator_band_id);
                        }}
                        className={`tabular ${ROW_BUTTON}`}
                      >
                        {ratio.numerator_band_id}
                      </button>
                    </th>
                    <td className={TD}>
                      {reference.kind === 'bands' ? (
                        <Traced
                          text={reference.bandIds.join(', ')}
                          path={reference.path}
                          value={reference.singular ? reference.bandIds[0] : reference.bandIds}
                          className="tabular"
                        />
                      ) : (
                        <span className="text-xs">
                          lane total{' '}
                          <Measured value={reference.signal} path={reference.path} />
                        </span>
                      )}
                    </td>
                    <td className={TD}>
                      {/*
                        The number is shown whether or not it was used. Ruled 2026-08-25: "Show
                        every ratio the tool produced ... Nothing the tool computed is hidden from
                        the card; that is the same rule that governs flagged bands in the record,
                        applied to the display." An excluded ratio is struck through -- a mark,
                        not a colour -- and carries the recorded reason underneath, so the reader
                        sees both what was computed and why it is not to be used.
                      */}
                      <Measured
                        value={ratio.ratio}
                        path={`normalization.ratios[${index}].ratio`}
                        className={ratio.excluded ? EXCLUDED_VALUE : undefined}
                      />
                      {ratio.excluded && (
                        <Traced
                          text={`excluded — ${exclusionReasonOf(ratio, index)}`}
                          path={`normalization.ratios[${index}].exclusion_reason`}
                          value={exclusionReasonOf(ratio, index)}
                          className="mt-0.5 block text-xs text-[var(--color-ink-muted)]"
                        />
                      )}
                    </td>
                    <td className={TD}>
                      <FlagCell
                        flags={ratio.qc_flags}
                        path={`normalization.ratios[${index}].qc_flags`}
                      />
                    </td>
                    <td className={TD}>
                      <FlagCell
                        flags={ratio.reference_qc_flags}
                        path={`normalization.ratios[${index}].reference_qc_flags`}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {activeBand === null ? (
          <p className="mt-6 border border-dashed border-[var(--color-hairline)] px-3 py-2 text-xs text-[var(--color-ink-muted)]">
            No row is selected. Activate a band or a ratio above to see the region it names and
            the parameter set it was measured under.
          </p>
        ) : (
          <section
            aria-labelledby={`${headingIds}-provenance`}
            className="mt-6 border border-[var(--color-hairline-strong)] p-3"
          >
            <h3 id={`${headingIds}-provenance`} className="text-sm font-semibold">
              Provenance ·{' '}
              <span className="tabular">{activeBand.band.band_id}</span>
            </h3>
            <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-xs">
              <dt className="text-[var(--color-ink-muted)]">config_id</dt>
              <dd>
                <Traced
                  text={result.provenance.config_id}
                  path="provenance.config_id"
                  value={result.provenance.config_id}
                  className="tabular"
                />
              </dd>
              <dt className="text-[var(--color-ink-muted)]">band ROI (px)</dt>
              <dd>
                <Measured
                  value={activeBand.band.roi.x}
                  path={`bands[${activeBand.index}].roi.x`}
                />
                {', '}
                <Measured
                  value={activeBand.band.roi.y}
                  path={`bands[${activeBand.index}].roi.y`}
                />
                {', '}
                <Measured
                  value={activeBand.band.roi.width}
                  path={`bands[${activeBand.index}].roi.width`}
                />
                {', '}
                <Measured
                  value={activeBand.band.roi.height}
                  path={`bands[${activeBand.index}].roi.height`}
                />
              </dd>
            </dl>

            <h4 className="mt-3 text-xs font-semibold">provenance.parameters</h4>
            <pre
              data-json-path="provenance.parameters"
              title={`provenance.parameters = ${JSON.stringify(result.provenance.parameters)}`}
              className="tabular mt-1 max-h-80 overflow-auto border border-[var(--color-hairline)] bg-[var(--color-surface-sunken)] p-2 text-[11px] leading-snug"
            >
              {JSON.stringify(result.provenance.parameters, null, 2)}
            </pre>

            <h4 className="mt-3 text-xs font-semibold">display.mapping</h4>
            <dl className="mt-1 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-xs">
              {Object.entries(display.mapping).map(([key, value]) => (
                <div key={key} className="contents">
                  <dt className="tabular text-[var(--color-ink-muted)]">{key}</dt>
                  <dd
                    className="tabular break-words"
                    data-json-path={`display.mapping.${key}`}
                    title={`display.mapping.${key} = ${String(value)}`}
                  >
                    {String(value)}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        )}

        <section
          aria-labelledby={`${headingIds}-image-flags`}
          className="mt-6 border border-[var(--color-hairline)] bg-[var(--color-surface-sunken)] px-3 py-2"
        >
          <h3
            id={`${headingIds}-image-flags`}
            className="text-xs font-medium text-[var(--color-ink-muted)]"
          >
            image-level flags (do not affect the lane verdict)
          </h3>
          <p className="mt-1 text-xs text-[var(--color-ink-muted)]">
            <Traced
              text={
                result.image_qc_flags.length === 0
                  ? 'none recorded on this image'
                  : result.image_qc_flags.join(', ')
              }
              path="image_qc_flags"
              value={result.image_qc_flags}
              className="tabular"
            />
          </p>
          <p className="mt-1 text-xs text-[var(--color-ink-faint)]">
            Shown whether or not any fired: an absent block would read as &ldquo;never
            checked&rdquo;. These describe the whole image and are not an input to the lane
            verdict above, so a clean lane inside a saturated image still passes.
          </p>
        </section>

        <p className="mt-6 text-sm">
          {download === null ? (
            <span className="text-[var(--color-ink-faint)]">Preparing result.json…</span>
          ) : (
            <a
              href={download.href}
              download={download.filename}
              className="text-[var(--color-accent)] underline"
            >
              Download result.json
            </a>
          )}
        </p>
      </div>
    </div>
  );
}
