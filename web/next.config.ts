import { fileURLToPath } from 'node:url';

import type { NextConfig } from 'next';

/**
 * Static export, deliberately.
 *
 * The site is a gallery of documents that were measured before it was built plus one screen
 * that talks to the API from the browser. Nothing it renders needs a server of its own, and a
 * server would give the site a second place where a number could come from. `out/` is the whole
 * deployable.
 *
 * `trailingSlash` matters to the export rather than to the dev server: without it `/result/<id>`
 * is emitted as `result/<id>.html`, which a plain static host will not serve at the URL the app
 * links to. With it the route becomes `result/<id>/index.html` and the link resolves.
 *
 * `images.unoptimized` because the optimizer is a server, and a static export has none. The
 * gallery's display PNGs are already 8-bit derivatives at the source image's own dimensions --
 * see `api/display.py` -- and re-encoding them is exactly what must not happen to a picture of
 * measured data.
 *
 * `outputFileTracingRoot` pins the workspace root to this package. Next otherwise walks upwards
 * looking for a lockfile and warns when it finds more than one -- a lockfile in a developer's
 * home directory is enough -- and the build is required to be warning-free, so the root is stated
 * rather than inferred from whatever happens to be above the repository.
 */
const nextConfig: NextConfig = {
  output: 'export',
  trailingSlash: true,
  images: { unoptimized: true },
  outputFileTracingRoot: fileURLToPath(new URL('.', import.meta.url)),
};

export default nextConfig;
