'use client';

import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';

import { LaneEditor } from '@/app/analyze/LaneEditor';
import { RefusalCard, UnexpectedCard, UnreachableCard } from '@/app/analyze/RefusalCard';
import { ResultStep } from '@/app/analyze/ResultStep';
import { StepIndicator } from '@/app/analyze/StepIndicator';
import { DEFAULT_CONFIG, UploadStep, type ConfigName } from '@/app/analyze/UploadStep';
import {
  laneListIssues,
  laneRoiStrings,
  newLaneKey,
  type LaneDraft,
} from '@/app/analyze/lanes';
import { analyze, API_BASE_URL, ApiRefusal, ApiUnreachable } from '@/lib/api';
import type { Envelope, Polarity, Roi } from '@/lib/result';

/**
 * Analyse your own image: upload, place the lanes, read the result.
 *
 * One page and three steps, because the three are one decision -- what was measured, over which
 * regions, under which parameter set -- and splitting them across routes would let a reader reach
 * a result without seeing what produced it.
 *
 * **The browser must be able to show the image.** The pipeline reads TIFF and this screen cannot:
 * no browser decodes TIFF, and a lane editor that could not show the picture would be asking a
 * person to place rectangles on a region they cannot see. The screen says so before a file is
 * chosen and again if a chosen file will not decode, and it names the CLI, which has no such
 * limitation. It never proceeds with an image it could not display.
 *
 * **The API's refusals are the other half of this screen.** A 415, a 413 or a 422 is not an error
 * state to be smoothed over; it is the product working. Step 3 renders the API's own message
 * verbatim, and a request that never arrived is a visibly different card that names the base URL
 * and the CORS prerequisite -- see `web/README.md`, which records that `api/app.py` installs no
 * CORS middleware.
 */

type StepNumber = 1 | 2 | 3;

/**
 * Return a warning if the pipeline measured a different frame from the one the browser decoded.
 *
 * The lane rectangles are placed in the browser's frame and measured in the pipeline's. Those
 * differ for a JPEG carrying an EXIF orientation tag -- browsers honour it, `cv2.imdecode` does
 * not -- and for a transposing orientation the dimensions disagree, which is checkable.
 *
 * **A 180-degree rotation leaves the dimensions equal and is not caught.** The ROI lands on
 * rotated pixels with nothing failing. Closing that needs the loader to read the tag, which is an
 * `api/`/`pipeline/` change; it is recorded as a limitation in NOTES.md rather than papered over
 * with a check that cannot see it.
 */
function frameMismatchOf(
  source: { width_px: number; height_px: number },
  decoded: { width: number; height: number } | null,
): string | null {
  if (
    decoded === null ||
    (source.width_px === decoded.width && source.height_px === decoded.height)
  ) {
    return null;
  }
  return (
    `Your browser decoded this image as ${decoded.width}×${decoded.height} px and blotquant ` +
    `measured it as ${source.width_px}×${source.height_px} px. The lane rectangles are placed ` +
    `in the browser's frame, so they do not name the regions that were measured. This happens ` +
    `with a JPEG carrying an EXIF orientation tag, which browsers apply and the pipeline does ` +
    `not. Re-save the image without orientation metadata, or convert it to PNG.`
  );
}

const BUTTON =
  'border px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed ' +
  'disabled:border-[var(--color-hairline)] disabled:text-[var(--color-ink-faint)]';

export default function AnalyzePage(): React.ReactElement {
  const [step, setStep] = useState<StepNumber>(1);

  const [file, setFile] = useState<File | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [imageSize, setImageSize] = useState<{ width: number; height: number } | null>(null);
  const [decodeError, setDecodeError] = useState<string | null>(null);

  const [polarity, setPolarity] = useState<Polarity | null>(null);
  const [config, setConfig] = useState<ConfigName>(DEFAULT_CONFIG);
  const [lanes, setLanes] = useState<LaneDraft[]>([]);

  const [frameMismatch, setFrameMismatch] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [envelope, setEnvelope] = useState<Envelope | null>(null);
  const [refusal, setRefusal] = useState<ApiRefusal | null>(null);
  const [unreachable, setUnreachable] = useState<ApiUnreachable | null>(null);
  const [unexpected, setUnexpected] = useState<Error | null>(null);

  const [detecting, setDetecting] = useState(false);
  const [detectedBands, setDetectedBands] = useState<Roi[]>([]);
  const [detectionProblem, setDetectionProblem] = useState<
    ApiRefusal | ApiUnreachable | Error | null
  >(null);

  // Decode the chosen file to learn its own pixel dimensions. Those dimensions are what every
  // lane rectangle is expressed in, so nothing downstream has to scale a coordinate.
  useEffect(() => {
    if (file === null) {
      return;
    }
    const url = URL.createObjectURL(file);
    setImageUrl(url);
    setImageSize(null);
    setDecodeError(null);
    // Everything below was measured against the *previous* image and means nothing about this
    // one. Carrying lane rectangles across a change of file is how a person ends up analysing a
    // region they placed on a different picture -- and if the new image is larger, every
    // rectangle still fits, `laneListIssues` reports nothing, and Analyse stays enabled. That is
    // the silent wrong measurement this screen's no-clamping rule exists to prevent, arriving by
    // another route.
    setLanes([]);
    setDetectedBands([]);
    setDetectionProblem(null);
    setFrameMismatch(null);
    const probe = new Image();
    probe.onload = () => setImageSize({ width: probe.naturalWidth, height: probe.naturalHeight });
    probe.onerror = () =>
      setDecodeError(
        `Your browser could not decode ${file.name}. Browsers do not display TIFF, and this ` +
          `screen needs to show the image to place lanes on it. blotquant itself reads TIFF: ` +
          `convert the file to PNG for this screen, or run the CLI on the original — the exact ` +
          `command is under the parameter set below.`,
      );
    probe.src = url;
    return () => {
      URL.revokeObjectURL(url);
    };
  }, [file]);

  /**
   * Ask the pipeline where the lanes are, and put what it says into the table.
   *
   * Posting with **no** `lane_roi` field is what `api/app.py` reads as "detect them"
   * (`lane_roi_specs is None`), which is why `laneRois` is empty rather than absent-by-omission
   * here: `lib/api.ts` appends one field per entry, so an empty list sends none.
   *
   * The detected rectangles land in the same editable rows a drawn or typed lane lands in, so
   * PLAN.md Phase 4's loop -- detected lanes overlaid, corrected with the numeric fields,
   * recomputed -- is one list being edited rather than two modes. The detected *bands* are drawn
   * on the surface as well, read-only: they are what the lane boundary is being corrected
   * against, and they are not editable because the caller path does not accept band rectangles.
   */
  const detect = useCallback(async (): Promise<void> => {
    // Guarded against `busy` as well as against itself: a detect resolving after Analyse was
    // pressed would overwrite the table with rectangles the submitted request never used,
    // leaving the result screen describing one set of regions beside an editor showing another.
    if (file === null || polarity === null || detecting || busy) {
      return;
    }
    setDetecting(true);
    setDetectionProblem(null);
    setDetectedBands([]);
    try {
      const detected = await analyze({ image: file, config, polarity, laneRois: [] });
      // The same frame check `submit()` runs, and it matters more here: these rectangles are the
      // pipeline's own, dropped onto an image the browser decoded. If the two frames disagree the
      // overlay is misaligned from the first paint, and the person would be correcting a lane
      // boundary against a picture that is not the one measured.
      //
      // So it is rendered on **step 2**, beside the rectangles, and not only on step 3. An
      // earlier revision computed this and displayed it nowhere -- the banner lived inside the
      // step 3 branch and `submit()` cleared it on the way there, so a mismatch found at detect
      // time was unobservable while the person was still editing. A warning about correcting
      // against the wrong picture is worth nothing after the correction has been made.
      const mismatch = frameMismatchOf(detected.result.source, imageSize);
      setFrameMismatch(mismatch);
      setLanes(
        detected.result.lanes.map((lane) => ({ key: newLaneKey(), ...lane.roi })),
      );
      setDetectedBands(detected.result.bands.map((band) => band.roi));
    } catch (error) {
      // A mismatch banner from an earlier successful detect describes a response this one did
      // not get, so it is cleared rather than left standing beside a fresh refusal.
      setFrameMismatch(null);
      // Shown in place on step 2 rather than advancing: the person asked a question about this
      // image and the answer -- including "no lane cleared the prominence threshold", which is a
      // 422 the pipeline is entitled to give -- belongs beside the rectangles they are editing.
      setDetectionProblem(
        error instanceof ApiRefusal || error instanceof ApiUnreachable || error instanceof Error
          ? error
          : new Error(String(error)),
      );
    } finally {
      setDetecting(false);
    }
  }, [busy, config, detecting, file, imageSize, polarity]);

  const submit = useCallback(async (): Promise<void> => {
    // `laneListIssues` is re-checked here rather than trusted from the `disabled` attribute two
    // components away: `lanes.ts` states that an incomplete row is never posted, and a claim of
    // that kind has to be enforced where the posting happens.
    if (
      file === null ||
      polarity === null ||
      busy ||
      detecting ||
      imageSize === null ||
      laneListIssues(lanes, imageSize).length > 0
    ) {
      return;
    }
    setBusy(true);
    setEnvelope(null);
    setRefusal(null);
    setUnreachable(null);
    setUnexpected(null);
    // `frameMismatch` is deliberately *not* cleared here. Clearing it meant that a mismatch found
    // at detect time vanished the moment an Analyse was refused -- the success path recomputes it
    // and the refusal path did not, so the one outcome that leaves the person still editing was
    // also the one that dropped the warning about what they are editing against. Whatever it
    // holds now concerns the current file (the file-change effect above clears it), so leaving it
    // standing is correct on every path, and the success path overwrites it with this response's
    // own comparison.
    try {
      const result = await analyze({
        image: file,
        config,
        polarity,
        laneRois: laneRoiStrings(lanes),
      });
      setFrameMismatch(frameMismatchOf(result.result.source, imageSize));
      setEnvelope(result);
    } catch (error) {
      // Never rethrown. A throw here leaves through an onClick handler as an unhandled rejection
      // that no error boundary catches, and the `finally` below would already have moved the
      // screen to step 3 -- leaving a blank result page on a tool whose thesis is loud failure.
      // An outcome nobody anticipated is rendered as one.
      if (error instanceof ApiRefusal) {
        setRefusal(error);
      } else if (error instanceof ApiUnreachable) {
        setUnreachable(error);
      } else {
        setUnexpected(error instanceof Error ? error : new Error(String(error)));
      }
    } finally {
      setBusy(false);
      setStep(3);
    }
  }, [busy, config, detecting, file, imageSize, lanes, polarity]);


  const uploadReady = file !== null && polarity !== null && imageSize !== null;
  const laneProblems = imageSize === null ? [] : laneListIssues(lanes, imageSize);

  return (
    <main className="mx-auto max-w-6xl px-6 py-8">
      <p className="text-sm">
        <Link href="/" className="text-[var(--color-accent)] underline">
          ← gallery
        </Link>
      </p>
      <h1 className="mt-4 text-xl font-semibold">Analyse an image</h1>
      <p className="tabular mt-1 text-xs text-[var(--color-ink-muted)]">
        Posts to {API_BASE_URL}
      </p>

      <div className="mt-6 border-b border-[var(--color-hairline)] pb-4">
        <StepIndicator current={step} />
      </div>

      <div className="mt-8">
        {step === 1 && (
          <>
            <UploadStep
              file={file}
              polarity={polarity}
              config={config}
              imageSize={imageSize}
              onFile={setFile}
              onPolarity={setPolarity}
              onConfig={setConfig}
            />
            <p className="mt-4 max-w-2xl text-xs text-[var(--color-ink-muted)]">
              This screen needs to display your image so you can place lanes on it. Browsers do
              not decode TIFF; blotquant does. For a TIFF, use the CLI:{' '}
              <span className="tabular">
                python -m pipeline run &lt;image&gt; --config configs/{config}.yaml --out results/
              </span>
            </p>
            {decodeError !== null && (
              <p
                data-testid="decode-error"
                className="mt-4 max-w-2xl border-l-2 py-1 pl-3 text-sm"
                style={{ borderColor: 'var(--color-verdict-blocked)' }}
              >
                {decodeError}
              </p>
            )}
          </>
        )}

        {step === 2 && imageUrl !== null && imageSize !== null && (
          <>
            <LaneEditor
              imageSrc={imageUrl}
              bounds={imageSize}
              lanes={lanes}
              onChange={setLanes}
              onDetect={() => void detect()}
              detecting={detecting}
              busy={busy}
              detectedBands={detectedBands}
              issues={laneProblems}
            />
            {frameMismatch !== null && (
              <p
                data-testid="frame-mismatch-lanes"
                className="mt-6 max-w-2xl border-l-2 py-1 pl-3 text-sm"
                style={{ borderColor: 'var(--color-verdict-blocked)' }}
              >
                {frameMismatch}
              </p>
            )}
            {detectionProblem !== null && (
              <div className="mt-6" data-testid="detection-problem">
                {detectionProblem instanceof ApiRefusal ? (
                  <RefusalCard refusal={detectionProblem} />
                ) : detectionProblem instanceof ApiUnreachable ? (
                  <UnreachableCard error={detectionProblem} />
                ) : (
                  <UnexpectedCard error={detectionProblem} />
                )}
              </div>
            )}
          </>
        )}

        {step === 3 && (
          <>
            {frameMismatch !== null && (
              <p
                data-testid="frame-mismatch"
                className="mb-4 border-l-2 py-1 pl-3 text-sm"
                style={{ borderColor: 'var(--color-verdict-blocked)' }}
              >
                {frameMismatch}
              </p>
            )}
            {refusal !== null && <RefusalCard refusal={refusal} />}
            {unreachable !== null && <UnreachableCard error={unreachable} />}
            {unexpected !== null && <UnexpectedCard error={unexpected} />}
            {envelope !== null && (
              <ResultStep
                envelope={envelope}
                filename={`${envelope.result.result_id}-result.json`}
              />
            )}
          </>
        )}
      </div>

      <div className="mt-10 flex flex-wrap items-center gap-3 border-t border-[var(--color-hairline)] pt-4">
        <button
          type="button"
          className={`${BUTTON} border-[var(--color-hairline-strong)]`}
          disabled={step === 1 || busy}
          onClick={() => setStep((current) => (current === 3 ? 2 : 1))}
        >
          ← Back
        </button>
        {step === 1 && (
          <button
            type="button"
            className={`${BUTTON} border-[var(--color-accent)] text-[var(--color-accent)]`}
            disabled={!uploadReady}
            onClick={() => setStep(2)}
          >
            Next: lanes →
          </button>
        )}
        {step === 2 && (
          <button
            type="button"
            className={`${BUTTON} border-[var(--color-accent)] text-[var(--color-accent)]`}
            disabled={busy || detecting || laneProblems.length > 0}
            onClick={() => void submit()}
          >
            Analyse →
          </button>
        )}
        {step === 1 && !uploadReady && (
          <span className="text-xs text-[var(--color-ink-muted)]">
            Choose a file the browser can display, and a polarity, to continue.
          </span>
        )}
        {step === 2 && detecting && (
          <span className="text-xs text-[var(--color-ink-muted)]">
            Waiting for the pipeline to report where the lanes are.
          </span>
        )}
        {step === 2 && !detecting && laneProblems.length > 0 && (
          <span className="text-xs text-[var(--color-ink-muted)]">
            {laneProblems.length} problem{laneProblems.length === 1 ? '' : 's'} to fix before this
            can be analysed; {laneProblems.length === 1 ? 'it is' : 'they are'} listed beside the
            table.
          </span>
        )}
      </div>
    </main>
  );
}
