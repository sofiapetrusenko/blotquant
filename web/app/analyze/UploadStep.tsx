'use client';

import { useId, useState } from 'react';

import type { Polarity } from '@/lib/result';

/**
 * Step 1: the file, the polarity and the parameter set.
 *
 * **Polarity has no default, and `Next` stays disabled until one is chosen.** `api/app.py`
 * refuses an image whose polarity is not declared, because it is a fact about the file that the
 * pipeline will not guess from the pixels -- inferring it would be choosing an interpretation
 * against the data (2026-08-24 polarity amendment). A pre-ticked radio here would put that guess
 * back, one layer up, with the person's name on it.
 *
 * The thumbnails are drawn inline, in SVG, from the two descriptions themselves: a strip with
 * three bands, light on dark and dark on light. No binary asset, so nothing to fetch, nothing to
 * go stale, and nothing to look like a photograph of a real blot that a reader might try to
 * match their own image against.
 *
 * Drag-and-drop is an addition on top of a real `<input type="file">`, never a replacement for
 * it: the input is what makes the file chooser reachable from the keyboard and announceable by a
 * screen reader, and it stays visible rather than being hidden behind the drop zone.
 */

/** The two config names in `configs/`.
 *
 * **Hardcoded, deliberately.** The API exposes no endpoint that lists parameter sets -- there is
 * no `GET /configs` -- so the only ways to populate this select are to hardcode the names or to
 * probe the API by posting an image under each guess. These are the two files in `configs/`
 * (`default.yaml`, `rolling_ball.yaml`). A name that no longer exists produces an
 * `UnknownConfigError`, which is a 400 whose message lists the names that do exist, and step 3
 * shows it verbatim -- so the failure mode is a visible, actionable refusal rather than a silent
 * mismatch. Adding the endpoint is an API change and is listed as an open question in the PR.
 */
export const CONFIG_NAMES = ['default', 'rolling_ball'] as const;

/** The name of a parameter set this build offers. Narrow, so a typo is a compile error. */
export type ConfigName = (typeof CONFIG_NAMES)[number];

/**
 * The parameter set selected when the screen opens.
 *
 * Derived from the list rather than typed out again: a literal here that stopped matching an
 * entry of `CONFIG_NAMES` would leave `<select value={config}>` with no matching option, and the
 * browser would display the *first* option while the state and the POST body carried the other
 * name -- a displayed parameter set that is not the submitted one, which is exactly the silent
 * mismatch the comment above argues hardcoding is safe from.
 */
export const DEFAULT_CONFIG: ConfigName = CONFIG_NAMES[0];

/** Containers `pipeline/load.py` reads. Offered to the file chooser, enforced by the API. */
const ACCEPTED_TYPES = '.tif,.tiff,.png,.jpg,.jpeg,image/tiff,image/png,image/jpeg';

interface PolarityChoice {
  value: Polarity;
  label: string;
  description: string;
}

const POLARITY_CHOICES: readonly PolarityChoice[] = [
  {
    value: 'bright_on_dark',
    label: 'bright_on_dark',
    description: 'bands brighter than background (gel-doc, chemiluminescence)',
  },
  {
    value: 'dark_on_bright',
    label: 'dark_on_bright',
    description: 'bands darker than background (film, published figure)',
  },
];

/** Thumbnail geometry, in the SVG's own user units. One strip, three bands. */
const THUMB = { width: 72, height: 32, bandWidth: 10, bandY: 6, bandHeight: 20, gap: 8 } as const;

function PolarityThumbnail({ polarity }: { polarity: Polarity }): React.ReactElement {
  const dark = polarity === 'bright_on_dark';
  const ground = dark ? 'var(--color-ink)' : 'var(--color-surface-sunken)';
  const band = dark ? 'var(--color-surface-raised)' : 'var(--color-ink)';
  const firstX = (THUMB.width - (3 * THUMB.bandWidth + 2 * THUMB.gap)) / 2;
  return (
    <svg
      width={THUMB.width}
      height={THUMB.height}
      viewBox={`0 0 ${THUMB.width} ${THUMB.height}`}
      aria-hidden="true"
      className="shrink-0 border border-[var(--color-hairline-strong)]"
    >
      <rect x={0} y={0} width={THUMB.width} height={THUMB.height} fill={ground} />
      {[0, 1, 2].map((index) => (
        <rect
          key={index}
          x={firstX + index * (THUMB.bandWidth + THUMB.gap)}
          y={THUMB.bandY}
          width={THUMB.bandWidth}
          height={THUMB.bandHeight}
          fill={band}
        />
      ))}
    </svg>
  );
}

export interface UploadStepProps {
  file: File | null;
  polarity: Polarity | null;
  config: ConfigName;
  /** Reported once the browser has decoded the file, so step 2 knows the image's own pixels. */
  imageSize: { width: number; height: number } | null;
  onFile: (file: File) => void;
  onPolarity: (polarity: Polarity) => void;
  onConfig: (config: ConfigName) => void;
}

export function UploadStep({
  file,
  polarity,
  config,
  imageSize,
  onFile,
  onPolarity,
  onConfig,
}: UploadStepProps): React.ReactElement {
  const ids = useId();
  const [dragging, setDragging] = useState(false);

  const takeFirst = (files: FileList | null): void => {
    const first = files?.[0];
    if (first !== undefined) {
      onFile(first);
    }
  };

  return (
    <div className="max-w-2xl">
      <h2 className="text-base font-semibold">1 · Upload an image</h2>

      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          takeFirst(event.dataTransfer.files);
        }}
        className="mt-3 border border-dashed p-4"
        style={{
          borderColor: dragging ? 'var(--color-accent)' : 'var(--color-hairline-strong)',
          backgroundColor: dragging ? 'var(--color-surface-sunken)' : undefined,
        }}
      >
        <label htmlFor={`${ids}-file`} className="block text-sm font-medium">
          Image file (TIFF, PNG or JPEG)
        </label>
        <input
          id={`${ids}-file`}
          type="file"
          accept={ACCEPTED_TYPES}
          onChange={(event) => takeFirst(event.target.files)}
          className="mt-2 block text-sm"
        />
        <p className="mt-2 text-xs text-[var(--color-ink-muted)]">
          Or drop a file anywhere in this box. The chooser above is the same thing and works from
          the keyboard.
        </p>
        {file !== null && (
          <p className="tabular mt-2 text-xs">
            {file.name} · {file.size} bytes
            {imageSize !== null && ` · ${imageSize.width}×${imageSize.height} px`}
          </p>
        )}
      </div>

      <fieldset className="mt-6 border border-[var(--color-hairline)] p-4">
        <legend className="px-1 text-sm font-medium">
          Signal polarity <span className="text-[var(--color-ink-muted)]">(required)</span>
        </legend>
        <p className="text-xs text-[var(--color-ink-muted)]">
          A fact about the file, not something the pipeline guesses from the pixels. There is no
          default: choose the one that describes your image.
        </p>
        <div className="mt-3 grid gap-3">
          {POLARITY_CHOICES.map((choice) => (
            <label
              key={choice.value}
              className="flex cursor-pointer items-center gap-3 border border-[var(--color-hairline)] p-2"
              style={{
                borderColor:
                  polarity === choice.value
                    ? 'var(--color-accent)'
                    : 'var(--color-hairline)',
              }}
            >
              <input
                type="radio"
                name={`${ids}-polarity`}
                value={choice.value}
                checked={polarity === choice.value}
                onChange={() => onPolarity(choice.value)}
              />
              <PolarityThumbnail polarity={choice.value} />
              <span className="text-sm">
                <span className="tabular font-medium">{choice.label}</span>
                <span className="block text-xs text-[var(--color-ink-muted)]">
                  {choice.description}
                </span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="mt-6">
        <label htmlFor={`${ids}-config`} className="block text-sm font-medium">
          Parameter set
        </label>
        <select
          id={`${ids}-config`}
          value={config}
          onChange={(event) => onConfig(event.target.value as ConfigName)}
          className="tabular mt-2 border border-[var(--color-hairline-strong)] bg-[var(--color-surface-raised)] px-2 py-1 text-sm"
        >
          {CONFIG_NAMES.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <p className="mt-2 text-xs text-[var(--color-ink-muted)]">
          The names of the files in <span className="tabular">configs/</span>. Every parameter the
          pipeline reads comes from the set you pick here and is echoed back in the result&rsquo;s
          provenance.
        </p>
      </div>
    </div>
  );
}
