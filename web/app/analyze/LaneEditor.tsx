'use client';

/* eslint-disable @next/next/no-img-element --
 * The same reason as components/RoiOverlay.tsx: the SVG drawing surface over this image works in
 * the image's own intrinsic pixels, and the source is an object URL for a file the browser holds
 * in memory. `next/image` would wrap it in a layout it has no use for and optimize nothing, since
 * the export is unoptimized by design.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import {
  INCOMPLETE_LANE_ROI,
  LANE_FIELDS,
  laneRoiStrings,
  moveLane,
  newLaneKey,
  rectFromDrag,
  removeLane,
  setLaneField,
  wholeImageLane,
  type ImageBounds,
  type LaneDraft,
  type LaneField,
} from '@/app/analyze/lanes';
import type { Roi } from '@/lib/result';

/**
 * Step 2: the lane rectangles, drawn and typed, which are the same rectangles.
 *
 * **One value, two views.** The lane list lives in the parent and is handed down; the SVG surface
 * renders it and the table edits it, and both go through `onChange`. Typing 40 into a `w` field
 * moves the rectangle in the same render; finishing a drag writes four numbers into the row. There
 * is no separate "canvas state" to fall out of step with the table, which is the failure this
 * arrangement exists to make impossible. `web/test/lane-editor.test.tsx` asserts both directions
 * with real numbers.
 *
 * **The numbers are the keyboard route.** "Add lane" appends a rectangle covering the whole image
 * and the four inputs edit it, so every lane can be specified, adjusted, reordered and deleted
 * without a pointer. Dragging is the shortcut, not the mechanism.
 *
 * **Nothing typed is silently corrected.** A rectangle that runs off the image stays exactly as
 * typed and is reported in words that name the number and the edge it passed; `Analyse` stays
 * disabled while any issue stands. A drag is clamped to the picture, which is different: a pointer
 * dragged past the edge never expressed a coordinate outside it.
 *
 * Drawing uses mouse events rather than pointer events on purpose -- the surface is a mouse
 * affordance, the keyboard route is the table, and mouse events are what a component test can
 * actually dispatch with coordinates.
 */

export interface LaneEditorProps {
  imageSrc: string;
  bounds: ImageBounds;
  lanes: readonly LaneDraft[];
  onChange: (lanes: LaneDraft[]) => void;
  /** Ask the pipeline where the lanes are and replace the table with what it says. */
  onDetect: () => void;
  detecting: boolean;
  /** True while an analysis is in flight, so neither request can overwrite the other's input. */
  busy: boolean;
  /**
   * Every problem standing in the way of analysing, in table order.
   *
   * Passed in rather than recomputed here. The per-lane issues this component used to render
   * itself said nothing at all about the *empty* list -- so a step 2 with no lanes showed
   * "1 problem to fix" beside an empty issue list, with the actionable message written in
   * `laneListIssues` and displayed nowhere. One check, one list, one place it is shown.
   */
  issues: readonly string[];
  /**
   * Band rectangles the last detection returned, drawn read-only.
   *
   * They are what a lane boundary is being corrected *against* — PLAN.md Phase 4's "auto-detected
   * lanes/bands overlaid on the image". Not editable, because the caller path accepts lane
   * rectangles and never band ones: an editable band here would imply an input the API has no
   * field for.
   */
  detectedBands: readonly Roi[];
}

const LANE_STROKE_PX = 1.5;
const DRAFT_STROKE_PX = 1.5;
const DRAFT_DASH = '4 3';
/** Detected bands are drawn lighter than a lane: they are context, not the thing being edited. */
const DETECTED_BAND_STROKE_PX = 1;

const FIELD_LABEL: Readonly<Record<LaneField, string>> = {
  x: 'x',
  y: 'y',
  width: 'w',
  height: 'h',
};

const BUTTON =
  'border border-[var(--color-hairline-strong)] bg-[var(--color-surface-raised)] px-2 py-0.5 ' +
  'text-xs disabled:cursor-not-allowed disabled:text-[var(--color-ink-faint)]';

interface DragState {
  start: { x: number; y: number };
  current: { x: number; y: number };
}

/** True when all four numbers are present, so the row describes a rectangle that can be drawn. */
function isDrawable(lane: LaneDraft): boolean {
  return LANE_FIELDS.every((field) => Number.isFinite(lane[field]));
}

export function LaneEditor({
  imageSrc,
  bounds,
  lanes,
  onChange,
  onDetect,
  detecting,
  busy,
  detectedBands,
  issues,
}: LaneEditorProps): React.ReactElement {
  const surface = useRef<HTMLDivElement | null>(null);
  const [drag, setDrag] = useState<DragState | null>(null);

  /** Convert a viewport point to image pixels using the surface's rendered size. */
  const toImage = useCallback(
    (clientX: number, clientY: number): { x: number; y: number } | null => {
      const element = surface.current;
      if (element === null) {
        return null;
      }
      const box = element.getBoundingClientRect();
      if (box.width === 0 || box.height === 0) {
        return null;
      }
      return {
        x: ((clientX - box.left) / box.width) * bounds.width,
        y: ((clientY - box.top) / box.height) * bounds.height,
      };
    },
    [bounds.height, bounds.width],
  );

  // The move and release listeners live on the window so a drag that leaves the picture still
  // tracks and still commits, rather than stranding a half-drawn rectangle on screen.
  useEffect(() => {
    if (drag === null) {
      return;
    }
    const onMove = (event: MouseEvent): void => {
      const point = toImage(event.clientX, event.clientY);
      if (point !== null) {
        setDrag((current) => (current === null ? null : { ...current, current: point }));
      }
    };
    const onUp = (): void => {
      // Committed here rather than inside a `setDrag` updater: an updater runs during render,
      // and calling the parent's setter from there is a state update in another component's
      // render pass. `drag` is current because this effect re-registers whenever it changes.
      onChange([
        ...lanes,
        { key: newLaneKey(), ...rectFromDrag(drag.start, drag.current, bounds) },
      ]);
      setDrag(null);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [drag, lanes, bounds, onChange, toImage]);

  const draftRect = drag === null ? null : rectFromDrag(drag.start, drag.current, bounds);

  return (
    <div>
      <h2 className="text-base font-semibold">2 · Lanes</h2>
      <p className="mt-1 text-sm text-[var(--color-ink-muted)]">
        Drag on the image to draw a lane, or add one and type its coordinates. Rectangles are in
        the image&rsquo;s own pixels: {bounds.width}×{bounds.height}.
      </p>

      <div className="mt-4 grid gap-6 lg:grid-cols-2">
        <div>
          <div
            ref={surface}
            data-testid="lane-surface"
            onMouseDown={(event) => {
              // Primary button only: a right-click opens a context menu and must not also draw.
              if (event.button !== 0) {
                return;
              }
              const point = toImage(event.clientX, event.clientY);
              if (point !== null) {
                event.preventDefault();
                setDrag({ start: point, current: point });
              }
            }}
            style={{ position: 'relative', lineHeight: 0, cursor: 'crosshair' }}
            className="border border-[var(--color-hairline-strong)] select-none"
          >
            <img
              src={imageSrc}
              alt="The image you uploaded. Drag on it to draw a lane rectangle."
              width={bounds.width}
              height={bounds.height}
              draggable={false}
              style={{
                display: 'block',
                width: '100%',
                height: 'auto',
                imageRendering: 'pixelated',
              }}
            />
            <svg
              viewBox={`0 0 ${bounds.width} ${bounds.height}`}
              aria-hidden="true"
              style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}
            >
              {detectedBands.map((roi, index) => (
                <rect
                  key={`detected-band-${index}`}
                  data-testid={`detected-band-${index}`}
                  x={roi.x}
                  y={roi.y}
                  width={roi.width}
                  height={roi.height}
                  fill="none"
                  stroke="var(--color-ink)"
                  strokeWidth={DETECTED_BAND_STROKE_PX}
                  vectorEffect="non-scaling-stroke"
                />
              ))}
              {lanes.map((lane, index) =>
                // A row with an empty field describes no rectangle, so none is drawn. Drawing
                // the last complete one instead would show a region the numbers no longer say.
                isDrawable(lane) ? (
                  <rect
                    key={lane.key}
                    data-testid={`lane-rect-${index}`}
                    x={lane.x}
                    y={lane.y}
                    width={lane.width}
                    height={lane.height}
                    fill="none"
                    stroke="var(--color-accent)"
                    strokeWidth={LANE_STROKE_PX}
                    vectorEffect="non-scaling-stroke"
                  />
                ) : null,
              )}
              {draftRect !== null && (
                <rect
                  data-testid="lane-rect-draft"
                  x={draftRect.x}
                  y={draftRect.y}
                  width={draftRect.width}
                  height={draftRect.height}
                  fill="none"
                  stroke="var(--color-ink)"
                  strokeWidth={DRAFT_STROKE_PX}
                  strokeDasharray={DRAFT_DASH}
                  vectorEffect="non-scaling-stroke"
                />
              )}
            </svg>
            {lanes.map((lane, index) =>
              !isDrawable(lane) ? null : (
              <span
                key={`label-${lane.key}`}
                className="tabular"
                style={{
                  position: 'absolute',
                  left: `${(lane.x / bounds.width) * 100}%`,
                  top: `${(lane.y / bounds.height) * 100}%`,
                  padding: '1px 4px',
                  fontSize: '11px',
                  lineHeight: 1.2,
                  color: 'var(--color-ink)',
                  backgroundColor: 'var(--color-surface-raised)',
                  border: '1px solid var(--color-hairline-strong)',
                }}
              >
                {index + 1}
              </span>
              ),
            )}
          </div>
        </div>

        <div className="min-w-0">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => onChange([...lanes, wholeImageLane(bounds)])}
              disabled={detecting || busy}
              className={BUTTON}
            >
              Add lane
            </button>
            <button
              type="button"
              onClick={onDetect}
              disabled={detecting || busy}
              className={BUTTON}
            >
              {detecting ? 'Detecting…' : 'Detect lanes for me'}
            </button>
            {busy && <span className="text-xs text-[var(--color-ink-muted)]">Analysing…</span>}
          </div>
          <p className="mt-2 text-xs text-[var(--color-ink-muted)]">
            <span className="tabular">Add lane</span> adds a rectangle covering the whole image;
            edit the numbers below.{' '}
            <span className="tabular">Detect lanes for me</span> asks the pipeline where they are
            and replaces the table with its answer — which you then correct here and analyse. Its
            band rectangles are drawn on the image as context and are not editable.
          </p>

          <div className="mt-3 overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <caption className="sr-only">
                Lane rectangles in the order they will be submitted
              </caption>
              <thead>
                <tr>
                  <th
                    scope="col"
                    className="border-b border-[var(--color-hairline-strong)] px-2 py-1 text-left font-medium"
                  >
                    #
                  </th>
                  {LANE_FIELDS.map((field) => (
                    <th
                      key={field}
                      scope="col"
                      className="border-b border-[var(--color-hairline-strong)] px-2 py-1 text-left font-medium"
                    >
                      {FIELD_LABEL[field]}
                    </th>
                  ))}
                  <th
                    scope="col"
                    className="border-b border-[var(--color-hairline-strong)] px-2 py-1 text-left font-medium"
                  >
                    order
                  </th>
                  <th
                    scope="col"
                    className="border-b border-[var(--color-hairline-strong)] px-2 py-1 text-left font-medium"
                  >
                    remove
                  </th>
                </tr>
              </thead>
              <tbody>
                {lanes.map((lane, index) => (
                  <tr key={lane.key}>
                    <th
                      scope="row"
                      className="tabular border-b border-[var(--color-hairline)] px-2 py-1 text-left font-normal"
                    >
                      {index + 1}
                    </th>
                    {LANE_FIELDS.map((field) => (
                      <td
                        key={field}
                        className="border-b border-[var(--color-hairline)] px-2 py-1"
                      >
                        <input
                          type="number"
                          step={1}
                          aria-label={`Lane ${index + 1} ${FIELD_LABEL[field]}`}
                          value={Number.isFinite(lane[field]) ? lane[field] : ''}
                          onChange={(event) =>
                            onChange(
                              setLaneField(
                                lanes,
                                index,
                                field,
                                event.target.value === ''
                                  ? Number.NaN
                                  : Number(event.target.value),
                              ),
                            )
                          }
                          className="tabular w-20 border border-[var(--color-hairline-strong)] bg-[var(--color-surface-raised)] px-1 py-0.5"
                        />
                      </td>
                    ))}
                    <td className="border-b border-[var(--color-hairline)] px-2 py-1">
                      <span className="flex gap-1">
                        <button
                          type="button"
                          className={BUTTON}
                          disabled={index === 0}
                          aria-label={`Move lane ${index + 1} up`}
                          onClick={() => onChange(moveLane(lanes, index, -1))}
                        >
                          ↑
                        </button>
                        <button
                          type="button"
                          className={BUTTON}
                          disabled={index === lanes.length - 1}
                          aria-label={`Move lane ${index + 1} down`}
                          onClick={() => onChange(moveLane(lanes, index, 1))}
                        >
                          ↓
                        </button>
                      </span>
                    </td>
                    <td className="border-b border-[var(--color-hairline)] px-2 py-1">
                      <button
                        type="button"
                        className={BUTTON}
                        aria-label={`Delete lane ${index + 1}`}
                        onClick={() => onChange(removeLane(lanes, index))}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h3 className="mt-4 text-xs font-medium">
            Submitted order — one <span className="tabular">lane_roi</span> field per row, in this
            order
          </h3>
          <p className="mt-1 text-xs text-[var(--color-ink-muted)]">
            The table&rsquo;s order <em>is</em> the submitted order: the pipeline numbers lanes in
            the order the fields arrive, so row 1 becomes L0. Use ↑ and ↓ to change it.
          </p>
          <ol data-testid="submitted-order" className="tabular mt-1 text-xs">
            {laneRoiStrings(lanes).map((roi, index) => (
              <li key={`${roi}-${index}`}>
                {roi === INCOMPLETE_LANE_ROI ? roi : `lane_roi=${roi}`}
              </li>
            ))}
          </ol>

          <ul className="mt-4 text-xs" data-testid="lane-issues">
            {issues.map((issue) => (
              <li
                key={issue}
                className="mt-1 border-l-2 border-[var(--color-verdict-blocked)] py-0.5 pl-2"
              >
                {issue}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
