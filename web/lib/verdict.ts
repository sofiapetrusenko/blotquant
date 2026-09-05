/**
 * The display-layer verdict, computed in the browser.
 *
 * **This is a mirror of `api/display.py::lane_verdicts`, and it is debt rather than a design.**
 * The ruled mapping is §2 of `docs/PRE_REGISTRATION_2026-08-25_verdict_mapping.md` (RATIFIED),
 * and Ruling 1 places its derivation in `api/display.py`. It is duplicated here because the API
 * does not serve the verdict -- §5 of that document leaves whether to serve it to a later ruling
 * -- and the live `POST /analyze` screen must show a verdict for a result that was never stored.
 *
 * Two implementations of one ruled mapping diverge silently, so they are pinned to each other:
 * `tools/gallery/verdict_fixtures.py` runs the **Python** function over every stored gallery
 * document and over synthetic documents covering all three classes, both blocked reasons and
 * edge cases E1--E8, and `web/test/verdict.test.ts` asserts this module reproduces every one of
 * them, flag order included. Any change to the mapping belongs in `api/display.py` first; this
 * file follows it, and `tests/test_gallery_verdict_fixtures.py` fails if it has not.
 *
 * The mapping, evaluated in this order for a lane `L`:
 *
 * 1. no ratio of `L` survives with `excluded: false` -- **blocked**;
 * 2. otherwise no QC flag attaches to `L` -- **pass**;
 * 3. otherwise -- **flagged**.
 *
 * A flag attaches to `L` if it appears on one of its bands, on one of its ratios, or on a ratio's
 * `reference_qc_flags` -- the last of these because a lane whose every number was divided by a
 * flagged denominator must not read `pass` on the strength of unflagged numerators (E4).
 *
 * **`image_qc_flags` is never read.** That is Ruling 3 of 2026-08-25 expressed as a data
 * dependency: image-level saturation does not become a blocking cause, so a clean lane inside a
 * saturated image is a `pass` lane.
 *
 * This module imports nothing. It touches no React, nothing under `app/`, and no network.
 */

/** The band QC vocabulary, in the order a flag list is reported in. */
export const BAND_QC_FLAGS = ['saturated', 'overlapping', 'unresolved_shoulder'] as const;

export const PASS = 'pass';
export const FLAGGED = 'flagged';
export const BLOCKED = 'blocked';

/** The closed verdict vocabulary. Three classes, because three were ruled. */
export const VERDICTS = [PASS, FLAGGED, BLOCKED] as const;

export type Verdict = (typeof VERDICTS)[number];

export const ALL_RATIOS_EXCLUDED = 'all_ratios_excluded';
export const NO_RATIO_EMITTED = 'no_ratio_emitted';

/**
 * Why a blocked lane has no number. Only a blocked verdict carries one.
 *
 * `all_ratios_excluded` is the ruled case: the lane produced ratios and every one was excluded.
 * `no_ratio_emitted` is edge case E1: the lane produced no ratio at all, so the ruled sentence's
 * *"because the input to it was excluded"* is not true of it.
 */
export const BLOCKED_REASONS = [ALL_RATIOS_EXCLUDED, NO_RATIO_EMITTED] as const;

export type BlockedReason = (typeof BLOCKED_REASONS)[number];

/**
 * A document that cannot be read back as one this service wrote.
 *
 * Thrown for a missing required key, a band or ratio naming a lane the document does not list,
 * or a field whose shape makes the mapping unevaluable. It is the counterpart of
 * `api.errors.DisplayError`, and it exists so that a damaged document fails loudly instead of
 * defaulting into a confident verdict: a lane whose `qc_flags` quietly read as empty would
 * render as `pass`.
 */
export class VerdictError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'VerdictError';
  }
}

/** One lane's verdict, and every observation the verdict rests on. */
export interface LaneVerdict {
  lane_id: string;
  roi_source: string;
  verdict: Verdict;
  /** `null` when the lane is not blocked; `asDict` omits the key entirely in that case. */
  blocked_reason: BlockedReason | null;
  qc_flags: string[];
  band_count: number;
  ratio_count: number;
  usable_ratio_count: number;
}

type Entry = Record<string, unknown>;

function isEntry(value: unknown): value is Entry {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Return `entry[key]`, or throw naming what is missing.
 *
 * Mirrors `api.display._required`. A stored result document is this derivation's only input, so
 * a missing required key is a damaged document rather than a value to substitute a default for.
 * Presence is tested with `hasOwnProperty` rather than `!== undefined`, so a key explicitly
 * present is never treated as absent.
 */
function required(entry: Entry, key: string, what: string): unknown {
  if (!Object.prototype.hasOwnProperty.call(entry, key)) {
    throw new VerdictError(
      `${what} has no ${JSON.stringify(key)}; the verdict is derived from a stored result ` +
        `document alone, and ${JSON.stringify(key)} is required of it by ` +
        `schema/result.schema.json. This document cannot be read back as the one this service ` +
        `wrote`,
    );
  }
  return entry[key];
}

/**
 * Return `value` as an array of entries, or throw naming where it was expected.
 *
 * Where the Python raises a bare `TypeError`, or does not raise at all: `lanes`/`bands`/`ratios`
 * given as a mapping or a string iterate as empty there, so a lane reads `pass` with no bands or
 * `blocked / no_ratio_emitted` with no ratios rather than being refused. Five cases in
 * `test/fixtures/verdict/divergences.json` reach this check, and `verdict.test.ts` asserts each
 * throws; the fixture records what the Python did with the same document.
 */
function requireEntryList(value: unknown, what: string): Entry[] {
  if (!Array.isArray(value)) {
    throw new VerdictError(
      `${what} is not a list (got ${value === null ? 'null' : typeof value}); ` +
        `schema/result.schema.json declares it an array of objects`,
    );
  }
  return value.map((item, index) => {
    if (!isEntry(item)) {
      throw new VerdictError(`${what}: entry ${index} is not an object`);
    }
    return item;
  });
}

/**
 * Return `value` as a string, or throw naming the field it came from.
 *
 * Used for the ids the derivation groups by and for `roi_source`. The schema declares all of
 * them strings; coercing instead would let a document with a numeric `lane_id` group differently
 * here than in the Python, which reads it as whatever type it arrives as.
 */
function requireString(value: unknown, what: string): string {
  if (typeof value !== 'string') {
    throw new VerdictError(
      `${what} is ${value === null ? 'null' : typeof value}, not a string; ` +
        `schema/result.schema.json declares it a string`,
    );
  }
  return value;
}

/**
 * Return `value` as a boolean, or throw naming the field it came from.
 *
 * Used for `excluded` alone, and it is the one field rule 1 of the mapping is decided on. §2.1 of
 * the pre-registration defines the survivor set as the ratios whose `excluded` **is `false`** --
 * an identity, not a truthiness test. The Python reads it with `not`, and Python and JavaScript
 * disagree about empty collections: a ratio carrying `excluded: []` is a survivor to `not []` in
 * Python and not a survivor to `![]` in JavaScript, which is not a different flag list but a
 * different **verdict class** for the same document -- `flagged` there, `blocked` here.
 *
 * So this refuses rather than guessing, on the same ground as `requireString` below: the schema
 * declares it a boolean, a document that carries anything else cannot be read back as one this
 * service wrote, and the one place a mirror must not quietly diverge is the field the class turns
 * on. The divergence from the Python is deliberate and is recorded in NOTES.md.
 */
function requireBoolean(value: unknown, what: string): boolean {
  if (typeof value !== 'boolean') {
    throw new VerdictError(
      `${what} is ${value === null ? 'null' : typeof value}, not a boolean; ` +
        `schema/result.schema.json declares it a boolean, and it is the field the verdict class ` +
        `is decided on -- reading it as anything else would decide the verdict rather than read it`,
    );
  }
  return value;
}

/** Return `value` as a list of flag names, or throw naming the field it came from. */
function requireFlagList(value: unknown, what: string): string[] {
  if (!Array.isArray(value)) {
    throw new VerdictError(
      `${what} is not a list of QC flags (got ${value === null ? 'null' : typeof value}); ` +
        `schema/result.schema.json declares it an array of flag names`,
    );
  }
  return value.map((flag, index) => {
    if (typeof flag !== 'string') {
      throw new VerdictError(`${what}: flag ${index} is not a string`);
    }
    return flag;
  });
}

/**
 * Compare two strings by Unicode code point, which is what Python's `sorted` does.
 *
 * JavaScript's default sort compares UTF-16 code units, and the two orders disagree above the
 * basic multilingual plane. The flag vocabulary is ASCII and always will be, but this function
 * is what makes "the TypeScript sorts unknown flags the way the Python does" true rather than
 * true-in-practice, and the fixture set cannot demonstrate the difference.
 */
function compareCodePoints(left: string, right: string): number {
  const a = Array.from(left);
  const b = Array.from(right);
  for (let index = 0; index < Math.min(a.length, b.length); index += 1) {
    const first = a[index] as string;
    const second = b[index] as string;
    if (first !== second) {
      return (first.codePointAt(0) as number) - (second.codePointAt(0) as number);
    }
  }
  return a.length - b.length;
}

/**
 * Return the distinct flags in `BAND_QC_FLAGS` order, unknown flags sorted after the known ones.
 *
 * Mirrors `api.display._ordered_flags`. Vocabulary order rather than alphabetical, so a verdict's
 * flag list reads the same way round as every other flag list in the project. A flag outside the
 * vocabulary is sorted last rather than refused: this reads *stored* documents, and one written
 * by a later vocabulary must still be legible.
 */
function orderedFlags(flags: readonly string[]): string[] {
  const distinct = new Set(flags);
  const known = BAND_QC_FLAGS.filter((flag) => distinct.has(flag));
  const unknown = [...distinct]
    .filter((flag) => !(BAND_QC_FLAGS as readonly string[]).includes(flag))
    .sort(compareCodePoints);
  return [...known, ...unknown];
}

/**
 * Return one `LaneVerdict` per lane of `result`, in the document's lane order.
 *
 * A pure function of one result document: it reads no pixels, no config and no network, and it
 * does not mutate what it is given. Throws `VerdictError` for a document that cannot be read
 * back as one this service wrote -- a missing required key, or a band or ratio naming a lane the
 * document does not list (E6).
 */
export function laneVerdicts(result: Record<string, unknown>): LaneVerdict[] {
  const lanes = requireEntryList(required(result, 'lanes', 'the result document'), 'lanes');
  const bands = requireEntryList(required(result, 'bands', 'the result document'), 'bands');
  const normalization = required(result, 'normalization', 'the result document');
  if (!isEntry(normalization)) {
    throw new VerdictError('the result document\'s "normalization" is not an object');
  }
  const ratios = requireEntryList(
    required(normalization, 'ratios', "the result document's normalization block"),
    'normalization.ratios',
  );

  const laneIds = lanes.map((lane) => requireString(required(lane, 'lane_id', 'a lane'), 'a lane\'s lane_id'));
  const bandsByLane = new Map<string, Entry[]>(laneIds.map((laneId) => [laneId, []]));
  const ratiosByLane = new Map<string, Entry[]>(laneIds.map((laneId) => [laneId, []]));

  for (const band of bands) {
    const label = JSON.stringify(band['band_id'] ?? '<unidentified>');
    const laneId = requireString(required(band, 'lane_id', `band ${label}`), `band ${label} lane_id`);
    const bucket = bandsByLane.get(laneId);
    if (bucket === undefined) {
      throw new VerdictError(
        `band ${label} names lane ${JSON.stringify(laneId)}, which is not among the ` +
          `document's lanes ${JSON.stringify(laneIds)}; a verdict cannot be derived for a lane ` +
          `the document does not describe`,
      );
    }
    bucket.push(band);
  }
  for (const ratio of ratios) {
    const laneId = requireString(required(ratio, 'lane_id', 'a ratio'), "a ratio's lane_id");
    const bucket = ratiosByLane.get(laneId);
    if (bucket === undefined) {
      throw new VerdictError(
        `a ratio names lane ${JSON.stringify(laneId)}, which is not among the document's lanes ` +
          `${JSON.stringify(laneIds)}; a verdict cannot be derived for a lane the document does ` +
          `not describe`,
      );
    }
    bucket.push(ratio);
  }

  return lanes.map((lane) => {
    const laneId = requireString(required(lane, 'lane_id', 'a lane'), "a lane's lane_id");
    const laneBands = bandsByLane.get(laneId);
    const laneRatios = ratiosByLane.get(laneId);
    if (laneBands === undefined || laneRatios === undefined) {
      // Unreachable: both maps were keyed from this same list of lane ids. Thrown rather than
      // defaulted to an empty list, because an empty band list is what a `pass` verdict is made
      // of, and a fallback here would manufacture one.
      throw new VerdictError(
        `lane ${JSON.stringify(laneId)} was not indexed while grouping the document's bands ` +
          `and ratios; this is a defect in the verdict derivation, not in the document`,
      );
    }
    const usable = laneRatios.filter(
      (ratio) =>
        requireBoolean(
          required(ratio, 'excluded', `a ratio of lane ${JSON.stringify(laneId)}`),
          `a ratio of lane ${JSON.stringify(laneId)}: excluded`,
        ) === false,
    );

    const flags: string[] = [];
    for (const band of laneBands) {
      const label = JSON.stringify(band['band_id'] ?? '<unidentified>');
      flags.push(
        ...requireFlagList(required(band, 'qc_flags', `band ${label}`), `band ${label} qc_flags`),
      );
    }
    for (const ratio of laneRatios) {
      // Optional on a ratio under schema/result.schema.json, unlike a band's, so an absent list
      // is a legal document rather than a damaged one and reads as no flags. It can never hide a
      // flag: normalize designates references from a lane's own bands, so every reference flag
      // also appears on a band read above.
      if (Object.prototype.hasOwnProperty.call(ratio, 'qc_flags')) {
        flags.push(...requireFlagList(ratio['qc_flags'], `a ratio of lane ${laneId}: qc_flags`));
      }
      if (Object.prototype.hasOwnProperty.call(ratio, 'reference_qc_flags')) {
        flags.push(
          ...requireFlagList(
            ratio['reference_qc_flags'],
            `a ratio of lane ${laneId}: reference_qc_flags`,
          ),
        );
      }
    }
    const ordered = orderedFlags(flags);

    let verdict: Verdict;
    let reason: BlockedReason | null;
    if (usable.length === 0) {
      verdict = BLOCKED;
      reason = laneRatios.length > 0 ? ALL_RATIOS_EXCLUDED : NO_RATIO_EMITTED;
    } else if (ordered.length === 0) {
      verdict = PASS;
      reason = null;
    } else {
      verdict = FLAGGED;
      reason = null;
    }

    return {
      lane_id: laneId,
      roi_source: requireString(
        required(lane, 'roi_source', `lane ${JSON.stringify(laneId)}`),
        `lane ${JSON.stringify(laneId)} roi_source`,
      ),
      verdict,
      blocked_reason: reason,
      qc_flags: ordered,
      band_count: laneBands.length,
      ratio_count: laneRatios.length,
      usable_ratio_count: usable.length,
    };
  });
}

/**
 * Return one verdict as the JSON `api.display.LaneVerdict.as_dict` produces.
 *
 * `blocked_reason` is emitted only when there is one: a `blocked_reason: null` on a passing lane
 * would invite a reader to look for a blocking cause that does not exist. The key order matches
 * the Python's, so a fixture comparison can be a deep equality over the whole object.
 */
export function asDict(verdict: LaneVerdict): Record<string, unknown> {
  const document: Record<string, unknown> = {
    lane_id: verdict.lane_id,
    roi_source: verdict.roi_source,
    verdict: verdict.verdict,
  };
  if (verdict.blocked_reason !== null) {
    document['blocked_reason'] = verdict.blocked_reason;
  }
  // Copied, as `api.display.LaneVerdict.as_dict` copies (`list(self.qc_flags)`). Handing out
  // the verdict's own array would let a caller that sorts the result mutate the verdict.
  document['qc_flags'] = [...verdict.qc_flags];
  document['band_count'] = verdict.band_count;
  document['ratio_count'] = verdict.ratio_count;
  document['usable_ratio_count'] = verdict.usable_ratio_count;
  return document;
}
