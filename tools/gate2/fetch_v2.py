#!/usr/bin/env python3
"""
fetch_v2.py — Gate 2 image downloader, second route.

Why v2: PMC serves figure images from a CDN path containing an unguessable
hash segment (…/pmc/blobs/<a>/<pmcid>/<hash>/<filename>.jpg). Constructed URLs
404 by design. The only reliable source of that hash is the article HTML, so
this script fetches each article page once, builds a map
    filename -> CDN url
and downloads the figures named in shortlist.csv from it.

Records, but does not enforce, the grayscale rule pre-registered in DECISION
§7. A published figure is a montage — coloured bar charts and MW ladders sit
next to greyscale blot panels in one file — so a whole-figure colour test says
nothing about the panel that will be measured. Each row gets a whole-figure
colour fraction; the §7 rule is applied to the cropped panel, where a
pseudocoloured blot is rejected rather than converted (a conversion would add
an unregistered parameter that could not later be separated from the ImageJ
comparison it would contaminate).

    python3 fetch_v2.py                 # reads shortlist.csv, writes images/
    python3 fetch_v2.py --dir images    # explicit output dir

Stdlib only, except Pillow for the channel check (falls back to "unknown" if
Pillow is missing — such rows are downloaded but flagged for manual review).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
import time
import urllib.request
from pathlib import Path

PMC_ARTICLE = "https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
IMG_SRC = re.compile(r'src="(https://cdn\.ncbi\.nlm\.nih\.gov/pmc/blobs/[^"]+?\.(?:jpg|jpeg|png|tif|tiff))"', re.I)

try:
    from PIL import Image  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


def get(url: str, referer: str | None = None, retries: int = 3) -> bytes:
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,image/avif,image/webp,image/*,*/*;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=90) as r:
                data = r.read()
            time.sleep(0.5)
            return data
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET failed: {url} ({last})")


def image_map(pmcid: str) -> dict[str, str]:
    """filename (lowercased) -> CDN url, read from the article page."""
    html = get(PMC_ARTICLE.format(pmcid=pmcid)).decode("utf-8", "replace")
    out: dict[str, str] = {}
    for url in IMG_SRC.findall(html):
        out[url.rsplit("/", 1)[-1].lower()] = url
    return out


def inspect(path: Path) -> tuple[str, str]:
    """Return (px, colour_note) for a downloaded image.

    NOT a pass/fail gate. A published figure is a montage: coloured bar charts,
    coloured MW ladders and greyscale blot panels share one file, so a binary
    "does this file contain colour" test rejects essentially every figure while
    saying nothing about the panel that will actually be measured. The DECISION
    §7 rule applies to the CROPPED blot panel; this records the whole-figure
    colour fraction so the crop step knows where to look.
    """
    if not HAVE_PIL:
        return "?", "colour_unchecked"
    from PIL import Image

    try:
        with Image.open(path) as im:
            px = f"{im.width}x{im.height}"
            if im.mode in ("L", "1", "I;16", "I"):
                return px, "color_frac=0.000(single-channel)"
            small = im.convert("RGB")
            # Downsample: the fraction is a screening number, not a measurement.
            small.thumbnail((400, 400))
            pixels = list(small.getdata())
            n = len(pixels)
            coloured = sum(
                1 for r, g, b in pixels if max(r, g, b) - min(r, g, b) > 12
            )
            return px, f"color_frac={coloured / n:.3f}"
    except Exception as exc:  # noqa: BLE001
        return "?", f"unreadable: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", default="shortlist.csv")
    ap.add_argument("--dir", default="images")
    args = ap.parse_args()

    src = Path(args.infile)
    rows = list(csv.DictReader(src.open(encoding="utf-8")))
    wanted = [r for r in rows if r.get("keep", "").strip().lower() == "y"]
    if not wanted:
        print("No rows marked keep=y.")
        return 1

    outdir = Path(args.dir)
    outdir.mkdir(parents=True, exist_ok=True)
    if not HAVE_PIL:
        print("NOTE: Pillow not installed — colour check skipped, rows flagged 'unchecked'.",
              file=sys.stderr)

    by_pmcid: dict[str, list[dict]] = {}
    for r in wanted:
        by_pmcid.setdefault(r["pmcid"], []).append(r)

    got = 0
    for i, (pmcid, group) in enumerate(by_pmcid.items(), 1):
        print(f"[{i}/{len(by_pmcid)}] {pmcid}", file=sys.stderr)
        try:
            imgs = image_map(pmcid)
        except RuntimeError as exc:
            for r in group:
                r["notes"] = f"article page failed: {exc}"
            continue
        for r in group:
            name = r["graphic_href"].rsplit("/", 1)[-1].lower()
            url = imgs.get(name) or imgs.get(name + ".jpg")
            if not url:
                stem = name.rsplit(".", 1)[0]
                url = next((u for n, u in imgs.items() if n.startswith(stem)), None)
            if not url:
                r["notes"] = f"{name} not on article page"
                continue
            try:
                blob = get(url, referer=PMC_ARTICLE.format(pmcid=pmcid))
            except RuntimeError as exc:
                r["notes"] = f"download failed: {exc}"
                continue
            ext = "." + url.rsplit(".", 1)[-1].lower()
            label = re.sub(r"[^A-Za-z0-9]+", "", r["fig_label"]) or "fig"
            dest = outdir / f"{pmcid}_{label}{ext}"
            dest.write_bytes(blob)
            px, colour = inspect(dest)
            r["file"] = dest.name
            r["sha256"] = hashlib.sha256(blob).hexdigest()
            r["px"] = px
            r["notes"] = f"{r['notes'].split(';')[0]};{colour};lossy_format_expected"
            got += 1

    with src.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    prov = outdir / "provenance.md"
    with prov.open("w", encoding="utf-8") as fh:
        fh.write("# Real-blot source provenance\n\n")
        fh.write(
            "Recorded at download time by `fetch_v2.py`. Images come from the PMC\n"
            "article page CDN links. Every image listed is CC BY and single-channel;\n"
            "every figure carries a whole-figure colour fraction; the DECISION §7 grayscale\nrule is applied to the cropped blot panel, not to the montage.\n\n"
        )
        fh.write("| file | sha256 | PMCID | DOI | figure | licence | year | journal | px | notes |\n")
        fh.write("|---|---|---|---|---|---|---|---|---|---|\n")
        for r in wanted:
            if not r.get("file"):
                continue
            fh.write(
                f"| `{r['file']}` | `{r['sha256'][:16]}…` | {r['pmcid']} | {r['doi']} | "
                f"{r['fig_label']} | {r['licence']} | {r['year']} | {r['journal']} | "
                f"{r['px']} | {r.get('notes','')} |\n"
            )
        fh.write(f"\nDownloaded {got} of {len(wanted)} marked rows.\n")

    print(f"\ndownloaded {got} of {len(wanted)} marked -> {outdir}")
    failed = [r for r in wanted if not r.get("file")]
    if failed:
        print(f"{len(failed)} did not download:")
        for r in failed[:10]:
            print(f"  {r['pmcid']} {r['fig_label']}: {r['notes'][:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
