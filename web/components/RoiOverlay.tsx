'use client';

/* eslint-disable @next/next/no-img-element --
 * `next/image` is an optimizer and a layout wrapper, and this component is neither. It draws an
 * SVG whose coordinate system must be the raster's own intrinsic pixels, so that a lane
 * rectangle at x=123 lands on column 123 of the image the pipeline measured. The export sets
 * `images.unoptimized` anyway (see next.config.ts: re-encoding a picture of measured data is
 * exactly what must not happen), so `next/image` would add a wrapper and buy nothing. And on
 * `/analyze` the source is an object URL for a file the browser has not written to disk, which
 * the image component has no useful opinion about.
 */

import type { Roi } from '@/lib/result';

/**
 * The image with its ROI rectangles drawn on top, exact at any rendered width.
 *
 * **How the exactness works.** The `<img>` is laid out at whatever width the column gives it and
 * an absolutely positioned `<svg>` covers it with a `viewBox` of the *source* dimensions, so one
 * SVG user unit is one source pixel at every scale. The result document's ROI coordinates go
 * into the rectangles unchanged -- no scaling arithmetic anywhere in this file -- which is what
 * makes "this number is that region" a claim the component cannot get subtly wrong. The
 * dimensions are the caller's to supply and `tests/test_gallery_build.py` asserts the stored
 * derivative really is the source's own size.
 *
 * **A highlight is never colour alone.** A highlighted band gets a heavier stroke, a translucent
 * fill and a filled corner marker, so it is distinguishable in greyscale, at a glance, and under
 * every colour deficiency. The stroke transition is the one piece of motion in this project: it
 * carries the information that *this* rectangle is the one the row you just activated names.
 * `app/globals.css` collapses its duration under `prefers-reduced-motion`, which removes the
 * movement and leaves the highlight -- the reader still sees which region is selected, they just
 * see it arrive instantly.
 *
 * Strokes use `vectorEffect="non-scaling-stroke"`, so a hairline stays a hairline whether the
 * card is 200 px wide or the card page is 700 px wide. Without it the outline on a small
 * thumbnail would be thicker than the bands it outlines.
 */

/** One rectangle to draw. `roi` is in source pixels, exactly as the document records it. */
export interface OverlayShape {
  id: string;
  roi: Roi;
  kind: 'lane' | 'band';
  /** Shown as a chip pinned to the rectangle's top-left corner. Lanes have one; bands need not. */
  label?: string;
  highlighted?: boolean;
}

/**
 * Stroke weights in CSS pixels (not source pixels -- see `vectorEffect` above).
 *
 * The lane outline is the heaviest thing that is always on screen; a band is lighter so a lane
 * full of them still reads as one region; a highlighted band is three times a plain one, which
 * is a difference visible without any colour at all.
 */
const STROKE_WIDTH_PX = {
  lane: 1.5,
  band: 1,
  highlightedBand: 3,
} as const;

/** Opacity of the wash inside a highlighted band. Enough to find, light enough to see through. */
const HIGHLIGHT_FILL_OPACITY = 0.2;

/** Side of the filled corner marker on a highlighted band, in CSS pixels. */
const HIGHLIGHT_MARKER_PX = 8;

/** Duration of the highlight transition. Removed, not shortened, under reduced motion. */
const HIGHLIGHT_TRANSITION = 'stroke-width 120ms ease-out, fill-opacity 120ms ease-out';

const BAND_DASH = '4 3';
/**
 * Bands are dashed, lanes are solid, so the two are told apart without colour.
 *
 * Stroke colour alone carried this distinction until review pointed out that the only other
 * discriminator -- the numeric corner label -- appears on lanes and not on bands, leaving a
 * reader with a red-green deficiency, a greyscale print or a projector nothing to read the
 * overlay by. It is the same rule `VerdictBadge` already follows: colour is never the only
 * channel. A highlighted band goes solid, which is the third state and is also marked by the
 * corner square below.
 */

const LANE_STROKE = 'var(--color-accent)';
const BAND_STROKE = 'var(--color-ink)';
const HIGHLIGHT_STROKE = 'var(--color-accent)';

function percent(part: number, whole: number): string {
  return `${(part / whole) * 100}%`;
}

export function RoiOverlay({
  src,
  alt,
  width,
  height,
  shapes,
  className,
}: {
  src: string;
  alt: string;
  /** The source image's own width in pixels. The `viewBox` and every ROI are in these units. */
  width: number;
  height: number;
  shapes: readonly OverlayShape[];
  className?: string;
}): React.ReactElement {
  if (width <= 0 || height <= 0) {
    throw new Error(
      `RoiOverlay was given a ${width}x${height} source; ROI coordinates cannot be placed on ` +
        `an image with no extent, and a zero dimension would divide the label positions by zero`,
    );
  }
  return (
    <div className={className} style={{ position: 'relative', lineHeight: 0 }}>
      <img
        src={src}
        alt={alt}
        width={width}
        height={height}
        style={{
          display: 'block',
          width: '100%',
          height: 'auto',
          // The crops are small and are shown enlarged. Smoothing would draw pixels the camera
          // never recorded on top of a picture whose whole job is to show what was measured.
          imageRendering: 'pixelated',
        }}
      />
      <svg
        viewBox={`0 0 ${width} ${height}`}
        aria-hidden="true"
        style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}
      >
        {shapes.map((shape) => {
          const highlighted = shape.highlighted === true;
          const isLane = shape.kind === 'lane';
          const strokeWidth = isLane
            ? STROKE_WIDTH_PX.lane
            : highlighted
              ? STROKE_WIDTH_PX.highlightedBand
              : STROKE_WIDTH_PX.band;
          return (
            <rect
              key={shape.id}
              data-roi-id={shape.id}
              data-roi-kind={shape.kind}
              data-roi-highlighted={highlighted ? 'true' : 'false'}
              x={shape.roi.x}
              y={shape.roi.y}
              width={shape.roi.width}
              height={shape.roi.height}
              fill={highlighted ? HIGHLIGHT_STROKE : 'none'}
              fillOpacity={highlighted ? HIGHLIGHT_FILL_OPACITY : 0}
              stroke={isLane ? LANE_STROKE : highlighted ? HIGHLIGHT_STROKE : BAND_STROKE}
              strokeWidth={strokeWidth}
              strokeDasharray={isLane || highlighted ? undefined : BAND_DASH}
              vectorEffect="non-scaling-stroke"
              style={{ transition: HIGHLIGHT_TRANSITION }}
            />
          );
        })}
      </svg>
      {shapes.map((shape) =>
        shape.highlighted === true && shape.kind === 'band' ? (
          // The second, non-colour half of the highlight: a filled square pinned to the
          // rectangle's corner. Drawn in HTML rather than SVG so its size is in screen pixels
          // and it stays findable on a rectangle only a few source pixels across.
          <span
            key={`marker-${shape.id}`}
            aria-hidden="true"
            data-roi-marker={shape.id}
            style={{
              position: 'absolute',
              left: percent(shape.roi.x, width),
              top: percent(shape.roi.y, height),
              width: `${HIGHLIGHT_MARKER_PX}px`,
              height: `${HIGHLIGHT_MARKER_PX}px`,
              backgroundColor: HIGHLIGHT_STROKE,
            }}
          />
        ) : null,
      )}
      {shapes.map((shape) =>
        shape.label === undefined ? null : (
          <span
            key={`label-${shape.id}`}
            className="tabular"
            style={{
              position: 'absolute',
              left: percent(shape.roi.x, width),
              top: percent(shape.roi.y, height),
              lineHeight: 1.2,
              padding: '1px 4px',
              fontSize: '11px',
              color: 'var(--color-ink)',
              backgroundColor: 'var(--color-surface-raised)',
              border: '1px solid var(--color-hairline-strong)',
              whiteSpace: 'nowrap',
            }}
          >
            {shape.label}
          </span>
        ),
      )}
    </div>
  );
}
