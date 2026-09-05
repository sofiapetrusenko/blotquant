import { formatMeasured, measuredTitle } from '@/lib/result-cells';

/**
 * One value on screen, carrying the JSON path it was read from.
 *
 * **The traceability claim, implemented literally.** Every measured number a screen shows goes
 * through `Measured`, which renders it in the tabular face, stamps `data-json-path` with its
 * path in the result document, and puts the path *and the unrounded value* in the `title`. A
 * reader who wants to know where a number came from hovers it; a reviewer who wants to check
 * that the screen did not invent one greps the DOM for `data-json-path`.
 *
 * `Traced` is the same promise for a value that is not a number -- a band id, a flag list, an
 * exclusion reason. It formats nothing, because there is nothing to round.
 */

export function Measured({
  value,
  path,
  className,
}: {
  value: number;
  path: string;
  className?: string;
}): React.ReactElement {
  return (
    <span
      className={className === undefined ? 'tabular' : `tabular ${className}`}
      data-json-path={path}
      title={measuredTitle(path, value)}
    >
      {formatMeasured(value)}
    </span>
  );
}

/**
 * `value` is the document's own value at `path`, and `text` is how it is shown.
 *
 * They differ whenever the display transforms: an empty flag list shows as "none", an exclusion
 * shows as "excluded — <reason>". The tooltip states `path = <the document's value>`, so it stays
 * a true equation rather than a restatement of the presentation. `value` is **required** even
 * where the two coincide: an optional one would let a caller produce `path = none` for a field
 * whose value is `[]`, which is the quiet kind of wrong this whole attribute exists against.
 */
export function Traced({
  text,
  path,
  value,
  className,
}: {
  text: string;
  path: string;
  value: unknown;
  className?: string;
}): React.ReactElement {
  const shown = JSON.stringify(value);
  return (
    <span className={className} data-json-path={path} title={`${path} = ${shown}`}>
      {text}
    </span>
  );
}
