"""blotquant HTTP API.

Processing is **synchronous**: ``POST /analyze`` runs the whole pipeline inside the
request and returns the finished result. PLAN.md Phase 4 permits this for the MVP, and the
alternative -- a job queue with a polling endpoint -- would add a second source of truth for
"what has this image been analysed as" without changing a single number. The endpoint
handlers are declared with ``def`` rather than ``async def``, so Starlette runs them in its
threadpool and one long analysis does not block the event loop for other requests.

**How long an analysis actually takes, measured rather than assumed** (this branch,
``configs/default.yaml``, one arm64 developer machine, wall clock):

Dimensions are width x height, the convention this package's own messages use.

===========================  ========
image                        time
===========================  ========
256x192 (the gold-set size)  1.19 s
512x384 (0.2 MP)             4.05 s
1360x1024 (1.4 MP)           23.49 s
===========================  ========

An earlier version of this docstring claimed "a gel-doc export analyses in about two
seconds" and had measured nothing. That is roughly right for the 256x192 synthetic images
this project has been developed against and roughly an order of magnitude wrong for a real
gel-doc export, which is megapixels. **PLAN.md's premise for allowing synchronous
processing is "images are small", and for a real gel-doc image that premise does not
hold.** Synchronous still ships for 4a, where the caller is a test client or a developer,
and the decision is recorded in the PR. What it means for 4b is a live question rather than
a settled one: a 23-second request sits close to or beyond common proxy and browser
timeouts, and DEBT.md E10 carries it alongside the missing upload cap.

Two boundaries this package keeps, both inherited from PLAN.md:

* it never writes into ``data/ground_truth/``. Every *stored result* goes through
  :class:`api.storage.ResultStore`, which is built on
  :func:`pipeline.analyze.require_writable_destination` and checks the boundary when it is
  constructed. The one other write this package makes is the uploaded bytes, which go to a
  :class:`tempfile.TemporaryDirectory` for the length of one request and are never given a
  caller-chosen destination -- so the boundary holds there by construction rather than by
  that check, which is worth saying rather than implying "every write is guarded";
* it never rescales measured pixels. The 8-bit PNG a browser can display is produced in
  :mod:`api.display`, is labelled a derivative, and records the mapping it went through.
  See that module's docstring for why the code lives here rather than in ``pipeline/``.
"""

from __future__ import annotations

API_VERSION = "0.1.0"
"""Version of this HTTP contract, reported in the OpenAPI document."""
