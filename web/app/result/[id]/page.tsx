import Link from 'next/link';

import { ResultView } from '@/components/ResultView';
import { readGalleryDisplay, readGalleryIndex, readGalleryResult } from '@/lib/gallery';

/**
 * One gallery card, in full.
 *
 * Static: the document, the display labelling and the picture are all committed under
 * `public/gallery/`, read here at build time, and the page that ships is HTML. Nothing on this
 * route touches the API, which is why the gallery keeps working when nothing is running.
 *
 * The page is a thin frame around `ResultView`. Everything a reader looks at -- the overlay, the
 * tables, the provenance panel, the image-flags block -- is that component, rendered with the
 * same props `/analyze` gives it, so a stored card and a fresh analysis are the same surface.
 *
 * The download points at the committed `result.json` itself rather than at a blob assembled in
 * the browser: the reader gets the byte-identical file this page was built from.
 */

/** Every card the static export renders, read from the built index at build time. */
export function generateStaticParams(): { id: string }[] {
  return readGalleryIndex().map((entry) => ({ id: entry.id }));
}

export default async function ResultPage({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<React.ReactElement> {
  const { id } = await params;
  const entry = readGalleryIndex().find((candidate) => candidate.id === id);
  if (entry === undefined) {
    throw new Error(
      `gallery card ${JSON.stringify(id)} is not listed in index.json, so it has no title and ` +
        `no verdict row; rebuild the gallery with 'python -m tools.gallery.build'`,
    );
  }
  const result = readGalleryResult(id);
  const display = readGalleryDisplay(id);
  const lane = result.lanes[0];
  if (lane === undefined) {
    throw new Error(
      `the stored document for gallery card ${JSON.stringify(id)} holds no lane; a card is one ` +
        `caller-supplied ROI with its verdict (Ruling 2, 2026-08-25)`,
    );
  }
  return (
    <main className="mx-auto max-w-6xl px-6 py-8">
      <p className="text-sm">
        <Link href="/" className="text-[var(--color-accent)] underline">
          ← gallery
        </Link>
      </p>
      <h1 className="mt-4 text-xl font-semibold">{entry.title}</h1>
      <p className="tabular mt-1 text-xs text-[var(--color-ink-muted)]">
        {id} · {result.source.width_px}×{result.source.height_px} px ·{' '}
        {result.source.image_format} · {result.source.bit_depth}-bit ·{' '}
        {result.source.polarity} · config {result.provenance.config_id} · pipeline{' '}
        {result.provenance.software_version}
      </p>
      <div className="mt-6">
        <ResultView
          document={result}
          display={display}
          imageSrc={`/gallery/${id}/display.png`}
          laneId={lane.lane_id}
          download={{ href: `/gallery/${id}/result.json`, filename: `${id}-result.json` }}
        />
      </div>
    </main>
  );
}
