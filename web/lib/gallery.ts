import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import type { ResultDocument, StoredDisplayBlock } from '@/lib/result';
import type { BlockedReason, Verdict } from '@/lib/verdict';

/**
 * Reading the committed gallery off disk, at build time only.
 *
 * `web/public/gallery/` is written by `tools/gallery/build.py` from a live `POST /analyze`, so
 * every card is a measurement the shipped service made. This module reads it with `node:fs`
 * during the static export and must never be imported from a client component: the browser gets
 * the rendered page, not the filesystem.
 *
 * The index's shape is fixed by the build and nothing here may add to it. What a screen needs
 * beyond `{id, title, verdict, blocked_reason?, flag_count}` comes from the card's own
 * `result.json`, which is the measurement record.
 */

/** One row of `web/public/gallery/index.json`, in the manifest's order. */
export interface GalleryEntry {
  id: string;
  title: string;
  verdict: Verdict;
  /** Present only on a blocked card, on the convention `LaneVerdict.as_dict` uses. */
  blocked_reason?: BlockedReason;
  flag_count: number;
}

const GALLERY_DIR = join(process.cwd(), 'public', 'gallery');

/**
 * Return the gallery index in manifest order, or throw if it has not been built.
 *
 * Throws rather than returning an empty list: a site that rendered an empty gallery because a
 * build step had not run would ship as though the gallery were empty by choice.
 */
export function readGalleryIndex(): GalleryEntry[] {
  const path = join(GALLERY_DIR, 'index.json');
  let raw: string;
  try {
    raw = readFileSync(path, 'utf-8');
  } catch (cause) {
    throw new Error(
      `the gallery has not been built: ${path} is unreadable. Start the API ` +
        `('python -m api --storage-root results/ --config-dir configs/') and run ` +
        `'python -m tools.gallery.build'`,
      { cause },
    );
  }
  const entries = JSON.parse(raw) as GalleryEntry[];
  if (!Array.isArray(entries) || entries.length === 0) {
    throw new Error(`${path} lists no cards; rebuild it with 'python -m tools.gallery.build'`);
  }
  return entries;
}

/**
 * Read one file of one card, distinguishing "it is not there" from "it is not JSON".
 *
 * Two different faults with two different fixes: a missing file means the gallery is
 * inconsistent with its index and needs rebuilding, while a file that will not parse means the
 * committed artefact is damaged. One `try` around both would report the second as the first and
 * send whoever hits it looking in the wrong place.
 */
function readCardJson<T>(id: string, name: string, what: string): T {
  const path = join(GALLERY_DIR, id, name);
  let raw: string;
  try {
    raw = readFileSync(path, 'utf-8');
  } catch (cause) {
    throw new Error(
      `no ${what} for gallery card ${JSON.stringify(id)} at ${path}; the card is named by ` +
        `index.json, so the gallery is inconsistent -- rebuild it with ` +
        `'python -m tools.gallery.build'`,
      { cause },
    );
  }
  try {
    return JSON.parse(raw) as T;
  } catch (cause) {
    throw new Error(
      `the ${what} for gallery card ${JSON.stringify(id)} at ${path} is not valid JSON; the ` +
        `committed artefact is damaged -- rebuild it with 'python -m tools.gallery.build'`,
      { cause },
    );
  }
}

/** Return one card's stored result document, exactly as the service served it. */
export function readGalleryResult(id: string): ResultDocument {
  return readCardJson<ResultDocument>(id, 'result.json', 'stored result');
}

/**
 * Return one card's stored display labelling: the dimensions and the mapping, without the image.
 *
 * The picture itself is `public/gallery/<id>/display.png`, referenced by URL. What this returns
 * is what says the picture is a *derivative* -- `is_derivative`, `note`, and the `mapping` block
 * that records how many source DN one output level spans. The card view shows it in the
 * provenance panel, so a reader looking at a bright region beside a QC flag can see what the
 * picture went through to get there.
 */
export function readGalleryDisplay(id: string): StoredDisplayBlock {
  return readCardJson<StoredDisplayBlock>(id, 'display.json', 'stored display block');
}
