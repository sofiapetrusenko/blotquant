/**
 * The lane list the analyse screen builds, and the rules it is held to before it is posted.
 *
 * Pure: no React, no DOM, no network. The drawing surface and the numeric table are two views of
 * the value this module describes, which is what makes the binding between them a property of one
 * list rather than a synchronisation between two widgets.
 *
 * **Nothing here silently corrects a lane.** `laneIssues` reports; it never returns a fixed
 * rectangle. A typed `w` that runs past the right edge stays exactly as typed, is reported in
 * words that name the number and the edge, and blocks submission until the person changes it.
 * The alternative -- quietly clamping to the image -- would measure a region nobody asked for and
 * report it as the region they did.
 *
 * The one place a coordinate *is* clamped is `rectFromDrag`, and that is not a correction of an
 * entered value: a pointer dragged past the edge of the picture never expressed a coordinate
 * outside it in the first place.
 */

/** One lane being edited. `key` is a stable React key and is never sent to the API. */
export interface LaneDraft {
  key: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

/** The image the rectangles are measured against, in its own pixels. */
export interface ImageBounds {
  width: number;
  height: number;
}

/** The four editable fields of a lane, in the order the table shows them. */
export const LANE_FIELDS = ['x', 'y', 'width', 'height'] as const;

export type LaneField = (typeof LANE_FIELDS)[number];

export const MIN_LANE_EXTENT_PX = 1;
/**
 * The smallest extent this form will produce or accept.
 *
 * `schema/result.schema.json` gives `roi.width` and `roi.height` a minimum of 1. It is **not**
 * the smallest lane the pipeline will measure: `pipeline/detect.py::minimum_lane_extent_px`
 * derives a larger bound from the profile machinery's own needs, and refuses anything under it
 * with a message naming the rectangle. This form does not restate that bound — it is computed
 * from the config and would drift — so a rectangle between the two is accepted here and refused
 * there, visibly.
 */

let nextKey = 0;

/** Return a key no lane in this session has used. Presentation only; never posted. */
export function newLaneKey(): string {
  nextKey += 1;
  return `lane-${nextKey}`;
}

/**
 * Return a lane covering the whole image.
 *
 * What the keyboard route to "add a lane" produces. The whole image rather than an invented
 * fraction of it: every other starting rectangle would be a number this file made up, and the
 * point of the numeric table is that the person types the region they mean.
 */
export function wholeImageLane(bounds: ImageBounds): LaneDraft {
  return { key: newLaneKey(), x: 0, y: 0, width: bounds.width, height: bounds.height };
}

/**
 * Return the rectangle two drag points describe, in whole image pixels and inside the image.
 *
 * Points arrive already converted to image coordinates. The rectangle is normalised so a drag in
 * any direction gives a positive extent, rounded to whole pixels because ROIs are integers, and
 * given at least `MIN_LANE_EXTENT_PX` in each direction so that a click that barely moved still
 * produces a rectangle the *schema* admits rather than a zero-extent one.
 *
 * That floor is the schema's, not the pipeline's, and it is deliberately not the pipeline's. A
 * 1x1 lane is still refused by `pipeline/detect.py`, whose `minimum_lane_extent_px` is derived
 * from what the profile machinery needs to estimate noise at all — a bound this form does not
 * restate, because it is computed from the config and would drift the moment that config
 * changed. The API is the authority and its refusal is shown verbatim.
 */
export function rectFromDrag(
  start: { x: number; y: number },
  end: { x: number; y: number },
  bounds: ImageBounds,
): Omit<LaneDraft, 'key'> {
  const clampX = (value: number): number =>
    Math.min(Math.max(Math.round(value), 0), bounds.width);
  const clampY = (value: number): number =>
    Math.min(Math.max(Math.round(value), 0), bounds.height);
  const x0 = clampX(Math.min(start.x, end.x));
  const x1 = clampX(Math.max(start.x, end.x));
  const y0 = clampY(Math.min(start.y, end.y));
  const y1 = clampY(Math.max(start.y, end.y));
  const width = Math.max(x1 - x0, MIN_LANE_EXTENT_PX);
  const height = Math.max(y1 - y0, MIN_LANE_EXTENT_PX);
  return {
    x: Math.min(x0, bounds.width - width),
    y: Math.min(y0, bounds.height - height),
    width,
    height,
  };
}

/**
 * Return every reason lane number `position` (1-based) cannot be posted, in words that say what
 * to change. An empty list means the rectangle is postable.
 *
 * The checks mirror what `pipeline/detect.py` enforces on a caller-supplied ROI -- whole numbers,
 * a positive extent, inside the image -- so that the common mistakes are caught before a round
 * trip. They are not a substitute for it: the API remains the authority, and whatever it refuses
 * is shown verbatim on step 3. In particular, overlapping lanes are *not* checked here. The rule
 * belongs to the pipeline, and a second implementation of it in a form would be free to drift
 * from the one that actually decides.
 */
export function laneIssues(lane: LaneDraft, position: number, bounds: ImageBounds): string[] {
  const issues: string[] = [];
  const name = `Lane ${position}`;
  for (const field of LANE_FIELDS) {
    const value = lane[field];
    if (!Number.isFinite(value)) {
      issues.push(`${name}: ${field} is empty. Enter a whole number of pixels.`);
    } else if (!Number.isInteger(value)) {
      issues.push(
        `${name}: ${field} is ${value}, which is not a whole number. ROI coordinates are ` +
          `image pixels, so enter ${Math.round(value)} or another integer.`,
      );
    }
  }
  if (issues.length > 0) {
    return issues;
  }
  if (lane.x < 0 || lane.y < 0) {
    issues.push(
      `${name}: x=${lane.x}, y=${lane.y}. The image starts at 0,0, so neither may be negative.`,
    );
  }
  if (lane.width < MIN_LANE_EXTENT_PX || lane.height < MIN_LANE_EXTENT_PX) {
    issues.push(
      `${name}: width=${lane.width}, height=${lane.height}. Both must be at least ` +
        `${MIN_LANE_EXTENT_PX} px; a lane with no extent has no pixels to measure.`,
    );
  }
  if (lane.x + lane.width > bounds.width) {
    issues.push(
      `${name}: x=${lane.x} plus width=${lane.width} reaches ${lane.x + lane.width}, past the ` +
        `image's right edge at ${bounds.width}. Reduce width to ${bounds.width - lane.x} or ` +
        `move x left.`,
    );
  }
  if (lane.y + lane.height > bounds.height) {
    issues.push(
      `${name}: y=${lane.y} plus height=${lane.height} reaches ${lane.y + lane.height}, past ` +
        `the image's bottom edge at ${bounds.height}. Reduce height to ` +
        `${bounds.height - lane.y} or move y up.`,
    );
  }
  return issues;
}

/** Return every issue across the list, in table order. Empty means the list can be posted. */
export function laneListIssues(lanes: readonly LaneDraft[], bounds: ImageBounds): string[] {
  if (lanes.length === 0) {
    return [
      'No lane has been added. Draw a rectangle on the image, or use "Add lane" and type its ' +
        'coordinates.',
    ];
  }
  return lanes.flatMap((lane, index) => laneIssues(lane, index + 1, bounds));
}

/**
 * Return the `lane_roi` form values, in the table's order.
 *
 * **The table's order is the submitted order**, because `api/app.py` appends one `lane_roi` field
 * per rectangle and `pipeline/detect.py` numbers supplied lanes in the order they arrive. The
 * screen says so beside the table, and this function is the only place the strings are built.
 */
export const INCOMPLETE_LANE_ROI = '(incomplete — fill in every field)';
/**
 * What an unfinished row reads as in place of a `lane_roi` value.
 *
 * Not a value that could be posted, and deliberately not shaped like one: `laneListIssues`
 * blocks submission while a row is incomplete, and showing `lane_roi=123,0,NaN,122` would put a
 * string on screen that would never be sent, under the heading that says this is what is sent.
 */

export function laneRoiStrings(lanes: readonly LaneDraft[]): string[] {
  return lanes.map((lane) =>
    // A row with an empty field describes no rectangle. `laneListIssues` blocks submission while
    // one exists, so this string is never posted -- but it is displayed, and showing
    // `lane_roi=0,0,NaN,122` would put a value on screen that would never be sent.
    LANE_FIELDS.every((field) => Number.isFinite(lane[field]))
      ? `${lane.x},${lane.y},${lane.width},${lane.height}`
      : INCOMPLETE_LANE_ROI,
  );
}

/**
 * Return `lanes` with the lane at `index` moved by `delta` positions, or unchanged at an end.
 *
 * Unchanged rather than wrapping: a "move up" on the first row that sent it to the bottom would
 * silently change the submitted order into one nobody asked for.
 */
export function moveLane(
  lanes: readonly LaneDraft[],
  index: number,
  delta: number,
): LaneDraft[] {
  const target = index + delta;
  if (index < 0 || index >= lanes.length || target < 0 || target >= lanes.length) {
    return [...lanes];
  }
  const next = [...lanes];
  const [moved] = next.splice(index, 1);
  if (moved === undefined) {
    return [...lanes];
  }
  next.splice(target, 0, moved);
  return next;
}

/** Return `lanes` without the lane at `index`. */
export function removeLane(lanes: readonly LaneDraft[], index: number): LaneDraft[] {
  return lanes.filter((_, position) => position !== index);
}

/** Return `lanes` with one field of the lane at `index` replaced. */
export function setLaneField(
  lanes: readonly LaneDraft[],
  index: number,
  field: LaneField,
  value: number,
): LaneDraft[] {
  return lanes.map((lane, position) =>
    position === index ? { ...lane, [field]: value } : lane,
  );
}
