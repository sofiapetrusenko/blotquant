#!/usr/bin/env python3
"""Re-download the Gate 2 source figures and verify them against their recorded sha256.

**Why this exists.** ``data/real/images/`` holds only the 13 figures a measured crop derives
from. The other 8 of the 21 the Gate 2 review downloaded produced no crop, so nothing measures
them and they are not committed. This tool recovers any of the 21 from the record, and --
more usefully -- re-verifies the 13 that *are* committed against the upstream bytes.

**Why the recovery is not trivial, which is the point.** PMC serves figures from a CDN path
containing an unguessable per-blob hash::

    https://cdn.ncbi.nlm.nih.gov/pmc/blobs/<opaque>/<pmcid>/<opaque>/<graphic>.jpg

``data/real/sources.csv`` records ``graphic_href`` -- the bare figure filename as the article
declares it -- and never the CDN URL, because the CDN URL was not stable enough to be worth
recording and cannot be constructed from the parts. So recovery means fetching the article
page and reading the current ``src`` for that graphic, which is exactly what ``fetch_v2.py``
did at acquisition time. This tool repeats that, then checks the bytes against the sha256 the
record already holds.

**A mismatch is a finding, not an error to route around.** If a publisher re-renders a figure,
the sha256 changes, and the measured crop can no longer be audited against a live upstream
copy -- only against the parent committed here. That is the argument for committing the
parents rather than trusting recovery, and this tool is how the argument gets re-tested.

Network-dependent by nature, so it is **not** a CI check: CI verifies the committed bytes
against the record (``tools/check_claims.py``), which needs no network and cannot be broken by
someone else's website. Run this by hand when you want to know whether upstream still agrees.

Stdlib only except for an **optional** :mod:`certifi`, used for its CA bundle when it is
importable and fallen back from with a note when it is not, so this still runs from a
clean clone with no environment -- it just may not get through a TLS-intercepting network.

Exit 0 when every requested file downloaded and matched; 1 on any mismatch or failure.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import ssl
import sys
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SOURCES_CSV = REPO_ROOT / "data" / "real" / "sources.csv"
COMMITTED_DIR = REPO_ROOT / "data" / "real" / "images"

PMC_ARTICLE = "https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"
"""Article page the CDN links are read from. Kept identical to ``fetch_v2.py``'s."""

IMG_SRC = re.compile(
    r'src="(https://cdn\.ncbi\.nlm\.nih\.gov/pmc/blobs/[^"]+?\.(?:jpg|jpeg|png|tif|tiff))"',
    re.I,
)
"""The CDN link pattern, copied verbatim from ``fetch_v2.py`` so recovery matches acquisition."""

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


@lru_cache(maxsize=1)
def tls_context() -> ssl.SSLContext:
    """Return a **verifying** TLS context, preferring certifi's CA bundle over the system store.

    The first networked run of this tool failed on every article URL with
    ``CERTIFICATE_VERIFY_FAILED: self signed certificate in certificate chain``. That is TLS
    interception on the operator's network -- a middlebox re-signing traffic with a CA the
    Python default store does not carry -- and not a PMC failure: the same URL returns 200 when
    the context is built from certifi's bundle, and curl reached the same hosts throughout with
    its own bundle.

    So the context is built from :mod:`certifi` when it is importable and from the system
    default when it is not, and a fallback is logged rather than silent.

    **Verification is never disabled, and that is not a style preference.** Every byte this tool
    fetches is checked against a recorded sha256, and that digest is the only thing standing
    between a silently re-rendered figure and the record. Fetching over a connection nobody
    authenticated and then hashing the result is a contradiction: it would prove the bytes match
    what *someone* served, which is exactly the question at issue. If verification cannot be
    made to work, the honest outcome is the failure this tool already reports.
    """
    try:
        import certifi
    except ImportError:
        print(
            "note: certifi is not installed; using the system CA store. If fetches fail with "
            "CERTIFICATE_VERIFY_FAILED, install it (pip install certifi) -- verification is "
            "not disabled here by design",
            file=sys.stderr,
        )
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def fetch(url: str, referer: str | None = None, retries: int = 3) -> bytes:
    """Return the bytes at ``url``, retrying a few times on transient failure."""
    headers = {"User-Agent": UA, "Accept": "text/html,image/*,*/*;q=0.8"}
    if referer:
        headers["Referer"] = referer
    last: Exception | None = None
    for _ in range(retries):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=60, context=tls_context()) as response:
                return bytes(response.read())
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last = error
    raise RuntimeError(f"could not fetch {url}: {last}")


def image_map(pmcid: str) -> dict[str, str]:
    """Return ``{graphic filename: CDN url}`` as the article page currently declares them."""
    article = PMC_ARTICLE.format(pmcid=pmcid)
    html = fetch(article).decode("utf-8", errors="replace")
    found: dict[str, str] = {}
    for url in IMG_SRC.findall(html):
        found.setdefault(url.rsplit("/", 1)[-1].lower(), url)
    return found


def resolve(row: dict[str, str]) -> str | None:
    """Return the current CDN url for ``row``'s graphic, or ``None`` if the page has no match."""
    images = image_map(row["pmcid"])
    name = row["graphic_href"].rsplit("/", 1)[-1].lower()
    url = images.get(name) or images.get(name + ".jpg")
    if url is None:
        stem = name.rsplit(".", 1)[0]
        url = next((u for n, u in images.items() if n.startswith(stem)), None)
    return url


def main(argv: list[str] | None = None) -> int:
    """Re-download and verify. Returns 0 when every requested row matched."""
    parser = argparse.ArgumentParser(
        prog="python tools/gate2/refetch_sources.py",
        description=__doc__.split("\n\n")[0],
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="directory to write downloads into; omit to verify in memory and write nothing",
    )
    parser.add_argument(
        "--only",
        choices=("all", "committed", "uncommitted"),
        default="all",
        help="which rows to process; 'uncommitted' recovers the 8 figures no crop derives from",
    )
    args = parser.parse_args(argv)

    if not SOURCES_CSV.is_file():
        print(f"error: no source manifest at {SOURCES_CSV}", file=sys.stderr)
        return 1
    rows = list(csv.DictReader(SOURCES_CSV.open(encoding="utf-8")))
    if args.only != "all":
        want = "yes" if args.only == "committed" else "no"
        rows = [r for r in rows if r["committed"] == want]
    if not rows:
        print(f"error: no rows selected by --only {args.only}", file=sys.stderr)
        return 1
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)

    verified = 0
    for row in rows:
        name = row["file"]
        try:
            url = resolve(row)
            if url is None:
                print(f"FAIL {name}: the article page declares no graphic matching "
                      f"{row['graphic_href']!r}; the figure may have been renamed or withdrawn")
                continue
            payload = fetch(url, referer=PMC_ARTICLE.format(pmcid=row["pmcid"]))
        except RuntimeError as error:
            print(f"FAIL {name}: {error}")
            continue
        digest = hashlib.sha256(payload).hexdigest()
        if digest != row["sha256"]:
            print(
                f"FAIL {name}: upstream now hashes {digest[:16]}… but the record says "
                f"{row['sha256'][:16]}…. The publisher has re-rendered this figure. The crops "
                f"derived from it are still auditable against data/real/images/, which is why "
                f"the parents are committed; upstream is no longer byte-identical"
            )
            continue
        note = "matches record"
        if row["committed"] == "yes":
            local = COMMITTED_DIR / name
            if local.is_file():
                if hashlib.sha256(local.read_bytes()).hexdigest() != digest:
                    # Upstream matches the record and the committed copy does not, so the copy
                    # in this tree has been altered since Gate 2. Reported as a failure rather
                    # than as an "ok" carrying a warning: a line beginning "ok" is what a reader
                    # skims past.
                    print(
                        f"FAIL {name}: upstream matches the record but the committed copy at "
                        f"{local} does not. The file in this tree has been altered since Gate 2 "
                        f"-- re-download it, or find out what rewrote it"
                    )
                    continue
                note += "; committed copy identical"
        if args.out:
            (args.out / name).write_bytes(payload)
        verified += 1
        print(f"ok   {name}: {note}")

    print(f"\n{verified} of {len(rows)} verified against the recorded sha256")
    # Non-zero whenever anything asked for was not verified. A recovery tool that reports total
    # failure with a success code is worse than none: a caller reads it as fine. The exit code is
    # derived from the same counter the summary prints, so the two cannot disagree.
    return 0 if verified == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
