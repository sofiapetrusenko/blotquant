/**
 * TypeScript types for the result document and the API's response envelope.
 *
 * Hand-written, and mirroring `schema/result.schema.json` (`schema_version` 1.4.0) key for key:
 * a required key here is required there, an optional key here is one the schema does not
 * require, and no key is widened to `any`. Where the schema requires a key without constraining
 * its type, the type here is `unknown` rather than a guess -- see `DetectionParameters` below,
 * which is the one place that bites.
 *
 * The document is the measurement record. Nothing in `web/` may add a field to it, and nothing
 * in `web/` may compute one: the verdict a card shows is derived on demand in
 * `web/lib/verdict.ts` from these fields alone, exactly as Ruling 1 of 2026-08-25 requires of
 * the Python it mirrors.
 *
 * Two things the schema says that are easy to lose in a type and are therefore stated here:
 *
 * - `Band.qc_flags` is **required**, `Ratio.qc_flags` and `Ratio.reference_qc_flags` are **not**.
 *   That asymmetry is load-bearing for the verdict derivation and is preserved below.
 * - `Ratio.ratio` is **required** even on an excluded ratio. `pipeline/normalize.py` emits it
 *   unconditionally and the schema lists it in `required`, so an excluded ratio still carries the
 *   number it would have reported; `excluded` is what says not to use it.
 */

/** A rectangle in image pixels. Integers, and `width`/`height` are at least 1. */
export interface Roi {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** The band-level QC vocabulary. Closed in the schema; see `$defs.band_qc_flags`. */
export type BandQcFlag = 'saturated' | 'overlapping' | 'unresolved_shoulder';

/** The image-level QC vocabulary. Never read by the verdict derivation (Ruling 3, 2026-08-25). */
export type ImageQcFlag = 'saturated' | 'lossy_format' | 'low_dynamic_range';

/** The normalization modes the pipeline implements. */
export type NormalizationMode = 'housekeeping_single' | 'housekeeping_multi' | 'total_protein';

/** The closed warning vocabulary of `normalization.warnings`. */
export type NormalizationWarning =
  | 'single_housekeeping_reference'
  | 'reference_band_qc_flagged'
  | 'reference_band_saturated'
  | 'reference_band_overlapping'
  | 'reference_band_unresolved_shoulder'
  | 'reference_band_lossy_format'
  | 'qc_flagged_bands_included_by_override'
  | 'lane_without_reference_band'
  | 'lane_denominator_not_positive';

/** Where a lane rectangle came from. A caller-supplied region is never reported as detected. */
export type RoiSource = 'detected' | 'caller';

/** The declared signal polarity of the source file, never inferred from its pixels. */
export type Polarity = 'bright_on_dark' | 'dark_on_bright';

/** Containers the loader admits. */
export type ImageFormat = 'tiff' | 'png' | 'jpeg';

/**
 * Present only when a multi-channel input was collapsed to one channel on the way in.
 * Its absence records that the file was already single-channel.
 */
export interface ChannelCollapse {
  method: 'green';
  max_divergence_dn: number;
}

/** The input as loaded. */
export interface Source {
  path: string;
  sha256?: string;
  image_format: ImageFormat;
  bit_depth: 8 | 16;
  max_value?: 255 | 65535;
  width_px: number;
  height_px: number;
  lossy_format: boolean;
  polarity: Polarity;
  channel_collapse?: ChannelCollapse;
  ground_truth_image_id?: string;
}

/**
 * The background stage's echo. `method` is the one key the schema constrains to a vocabulary.
 * The stage objects stay open because the pipeline owns their exact keys.
 */
export interface BackgroundParameters {
  method: 'rolling_ball' | 'local_median';
  [key: string]: unknown;
}

/**
 * The detection and quantification echoes. The schema requires `method` and says **nothing**
 * about its type, so it is `unknown` here. Typing it `string` would assert a contract the
 * schema does not make; a consumer that needs to display it narrows it at the point of use.
 */
export interface DetectionParameters {
  method: unknown;
  [key: string]: unknown;
}

/** The QC threshold echo. Open: the pipeline owns the key names. */
export type QcParameters = Record<string, unknown>;

/** The normalization echo. `mode` and `exclude_qc_flagged` are required of it. */
export interface NormalizationParameters {
  mode: NormalizationMode;
  exclude_qc_flagged: boolean;
  [key: string]: unknown;
}

/**
 * The complete, explicit processing config. Every stage is present; no parameter the pipeline
 * reads may be absent from it.
 */
export interface ProvenanceParameters {
  background: BackgroundParameters;
  detection: DetectionParameters;
  quantification: DetectionParameters;
  qc: QcParameters;
  normalization: NormalizationParameters;
  [key: string]: unknown;
}

/** Everything needed to reproduce this result. */
export interface Provenance {
  software_version: string;
  config_id: string;
  config_digest: string;
  created_at: string;
  parameters: ProvenanceParameters;
}

/** One lane. `roi_source` is always present, whatever its value. */
export interface Lane {
  lane_id: string;
  lane_index?: number;
  roi: Roi;
  roi_source: RoiSource;
  /** The lane-integral denominator, present only under `total_protein`. */
  total_protein_signal?: number;
  qc_flags?: BandQcFlag[];
}

/** One band. A flagged band still reports its value; its exclusion is explicit beside it. */
export interface Band {
  band_id: string;
  lane_id: string;
  roi: Roi;
  integrated_intensity: number;
  background_estimate: number;
  peak_value?: number;
  clipped_pixel_count?: number;
  /** Explicitly `null` when the statistic was censored, which is not the same as symmetric. */
  row_half_width_ratio?: number | null;
  qc_flags: BandQcFlag[];
  excluded_from_normalization: boolean;
  /** Required by the schema whenever `excluded_from_normalization` is true. */
  exclusion_reason?: string;
}

/** One normalized ratio. */
export interface Ratio {
  lane_id: string;
  numerator_band_id: string;
  /** Present when the lane was divided by exactly one reference band. */
  denominator_band_id?: string;
  /** Every reference band the lane was divided by; absent under `total_protein`. */
  denominator_band_ids?: string[];
  ratio: number;
  excluded: boolean;
  /** Required by the schema whenever `excluded` is true. */
  exclusion_reason?: string;
  /** Optional under the schema, unlike a band's. An absent list is a legal document. */
  qc_flags?: BandQcFlag[];
  reference_qc_flagged?: boolean;
  /** Optional under the schema, like `qc_flags`. */
  reference_qc_flags?: BandQcFlag[];
}

/** The normalization block. */
export interface Normalization {
  mode: NormalizationMode;
  exclude_qc_flagged: boolean;
  reference_band_ids?: string[];
  /** Free text describing where a designation came from; never parsed, never a designation. */
  reference_designation_source?: string;
  warnings: NormalizationWarning[];
  ratios: Ratio[];
}

/** One analysed image, valid against `schema/result.schema.json`. */
export interface ResultDocument {
  schema_version: string;
  result_id: string;
  source: Source;
  provenance: Provenance;
  lanes: Lane[];
  bands: Band[];
  normalization: Normalization;
  image_qc_flags: ImageQcFlag[];
  methods_paragraph?: string;
}

/**
 * How the display PNG was produced. Carried beside the picture so a viewer comparing a bright
 * region against a QC flag knows what the picture went through to get there -- in particular
 * `source_dn_per_output_level`, which is why a 255 in the PNG is not evidence of saturation.
 */
export interface DisplayMapping {
  name: string;
  formula: string;
  source_max_value: number;
  source_polarity: Polarity;
  source_inverted: boolean;
  output_max_value: number;
  scales: boolean;
  clips: boolean;
  source_dn_per_output_level: number;
}

/**
 * The `display` half of the envelope. `width_px` and `height_px` are the source image's own
 * dimensions, so a result ROI's coordinates index the PNG's pixels one to one.
 */
export interface DisplayBlock {
  is_derivative: true;
  note: string;
  media_type: string;
  width_px: number;
  height_px: number;
  mapping: DisplayMapping;
  png_base64: string;
}

/**
 * The display block without the encoded image: everything that *labels* the picture.
 *
 * This is the shape a screen actually needs. A component is handed the picture as a `src` --
 * a committed PNG for a gallery card, a `data:` URL for a fresh analysis -- and what it needs
 * from the block beside it is the labelling: the dimensions the ROI coordinates index, and the
 * `mapping` that says the picture is a scaled rendering rather than the measured data. It is
 * also exactly what `tools/gallery/build.py` commits as `<id>/display.json`, and exactly what
 * `api.display.DisplayDerivative.as_block` returns before `api/app.py` adds the base64 image.
 */
export type StoredDisplayBlock = Omit<DisplayBlock, 'png_base64'>;

/** What `POST /analyze` and `GET /results/{id}` both answer with. */
export interface Envelope {
  result: ResultDocument;
  display: DisplayBlock;
}
