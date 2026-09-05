import type { Ratio, ResultDocument } from '@/lib/result';

/**
 * Reading a result document into the cells a table shows, each with the JSON path it came from.
 *
 * **Nothing here computes a measured quantity.** Every number a screen displays is a number the
 * pipeline wrote, read out of the document at a path this module names, and the only
 * transformation applied to it is a fixed-precision rendering for the column it sits in -- with
 * the unrounded value carried alongside so the reader can see what was rounded away. A display
 * layer that re-scaled, summed or re-derived a value would be a second place a number could come
 * from, and the whole point of the document is that there is one.
 */

export const MEASURED_SIGNIFICANT_DIGITS = 6;
/**
 * Significant digits shown for a measured value.
 *
 * Significant digits rather than decimal places, because one table holds an integrated intensity
 * of 31767 and a ratio of 0.011145 -- both from the committed gallery -- and a fixed number of
 * decimal places renders one of them as noise and the other as zero. Six is enough to distinguish any two values this pipeline emits at the
 * magnitudes it emits them, and it is a *display* choice, not a processing parameter: it changes
 * no stored number, and `measuredTitle` puts the full value one hover away from every cell.
 */

/**
 * Return `value` at :data:`MEASURED_SIGNIFICANT_DIGITS`, or throw if it is not a finite number.
 *
 * A value that is exactly an integer is rendered as one. Pixel coordinates, clipped-pixel counts
 * and intensities that summed to a whole number are integers in the document, and padding them to
 * `123.000` would show a decimal part the number does not have -- in the provenance panel, beside
 * an ROI, that reads as sub-pixel precision nothing claims.
 *
 * Throws rather than rendering `NaN` or `∞`: the schema declares every measured field a number,
 * `pipeline/normalize.py` emits no ratio for a non-positive denominator, and a non-finite value
 * reaching a table means the document is not the one this service wrote. A cell reading "NaN"
 * would look like a measurement outcome rather than a broken document.
 */
export function formatMeasured(value: number): string {
  if (!Number.isFinite(value)) {
    throw new Error(
      `${String(value)} is not a finite number; schema/result.schema.json declares every ` +
        `measured field a number, so this document cannot be read back as one this service wrote`,
    );
  }
  if (Number.isInteger(value)) {
    return String(value);
  }
  return value.toPrecision(MEASURED_SIGNIFICANT_DIGITS);
}

/**
 * Return the `title` a measured cell carries: its JSON path and its **unrounded** value.
 *
 * Both halves matter. The path is the traceability claim -- every number on screen can be found
 * in the document a reader can download -- and the unrounded value is the honesty claim, because
 * the cell shows a rounding and the tooltip shows what was rounded.
 */
export function measuredTitle(path: string, value: number): string {
  return `${path} = ${String(value)}`;
}

/**
 * What a ratio was divided by, and the key of the document that says so.
 *
 * Two shapes because the document has two, and which one arrives is decided by the normalization
 * mode rather than by this module. Under a housekeeping mode the denominator is one or more
 * measured bands and the document names them; under `total_protein` there is no denominator band
 * at all -- `pipeline/normalize.py` emits neither key -- and the divisor is the lane's own
 * `total_protein_signal`, a measured number carried on the lane.
 */
export type RatioReference =
  | {
      kind: 'bands';
      bandIds: readonly string[];
      /** True when the document named one band with `denominator_band_id`, singular. */
      singular: boolean;
      path: string;
    }
  | { kind: 'laneTotal'; laneId: string; signal: number; path: string };

/**
 * Return what `result.normalization.ratios[index]` was divided by, with its JSON path.
 *
 * Reads the document rather than the mode: `denominator_band_id` is preferred when present,
 * `denominator_band_ids` when the lane was divided by several, and the lane's
 * `total_protein_signal` when neither key is there. Throws when neither key is present *and* the
 * lane carries no total, because at that point the document does not record what the number was
 * divided by, and a reference column that said "—" would be presenting an unrecorded divisor as
 * an ordinary one.
 */
export function ratioReference(result: ResultDocument, index: number): RatioReference {
  const ratio: Ratio | undefined = result.normalization.ratios[index];
  if (ratio === undefined) {
    throw new Error(
      `the document has no normalization.ratios[${index}]; it holds ` +
        `${result.normalization.ratios.length} ratio(s)`,
    );
  }
  if (ratio.denominator_band_id !== undefined) {
    return {
      kind: 'bands',
      bandIds: [ratio.denominator_band_id],
      singular: true,
      path: `normalization.ratios[${index}].denominator_band_id`,
    };
  }
  if (ratio.denominator_band_ids !== undefined) {
    return {
      kind: 'bands',
      bandIds: ratio.denominator_band_ids,
      singular: false,
      path: `normalization.ratios[${index}].denominator_band_ids`,
    };
  }
  const laneIndex = result.lanes.findIndex((lane) => lane.lane_id === ratio.lane_id);
  const lane = laneIndex === -1 ? undefined : result.lanes[laneIndex];
  if (lane === undefined || lane.total_protein_signal === undefined) {
    throw new Error(
      `normalization.ratios[${index}] names no denominator band and lane ` +
        `${JSON.stringify(ratio.lane_id)} carries no total_protein_signal, so the document ` +
        `does not record what this ratio was divided by. Under normalization mode ` +
        `${JSON.stringify(result.normalization.mode)} one of the three is always written`,
    );
  }
  return {
    kind: 'laneTotal',
    laneId: lane.lane_id,
    signal: lane.total_protein_signal,
    path: `lanes[${laneIndex}].total_protein_signal`,
  };
}

/**
 * Return the recorded reason an excluded ratio carries, or throw naming the path that is empty.
 *
 * `schema/result.schema.json` requires `exclusion_reason` whenever `excluded` is true. QC
 * annotates and never silently drops, so an exclusion with no reason beside it is precisely the
 * failure mode this project exists to prevent, and rendering "excluded" on its own would hide it.
 */
export function exclusionReasonOf(ratio: Ratio, index: number): string {
  if (ratio.exclusion_reason === undefined) {
    throw new Error(
      `normalization.ratios[${index}] is excluded and records no exclusion_reason; the schema ` +
        `requires one, and a number withheld without a recorded cause cannot be shown as ` +
        `withheld for a reason`,
    );
  }
  return ratio.exclusion_reason;
}
