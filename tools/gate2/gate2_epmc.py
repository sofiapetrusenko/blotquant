#!/usr/bin/env python3
"""
gate2_epmc.py — candidate finder for blotquant Gate 2 (real CC-BY western blots).

Two subcommands, deliberately separated so that a human decision sits between them:

    scan   query Europe PMC, keep only permissive-licence OA articles, pull the
           JATS full text, and emit one CSV row per *figure* whose caption looks
           like a western blot. Downloads nothing but XML.

    fetch  read that CSV back after you have marked rows `keep=y`, download the
           ORIGINAL figure files from the PMC OA package, and write provenance
           (DOI, licence, figure label, URL, sha256, pixel size) AT THE MOMENT
           of download — not afterwards from memory.

Why the OA package and not the web JPEG: europepmc.org/articles/<PMCID>/bin/*.jpg
is a downsampled, re-encoded rendering. Measuring densitometry on it would be
measuring the CMS. The OA package holds the file the publisher was given.

Run OUTSIDE the blotquant tree (the phase-4a-api tree is staged and dirty).

    python gate2_epmc.py scan  --limit 300 --out candidates.csv
    # ...human passes over candidates.csv, sets keep=y on rows worth a look...
    python gate2_epmc.py fetch --in candidates.csv --dir images/

Stdlib only. Python 3.9+.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import struct
import sys
import tarfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator, Optional

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
OA_FCGI = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"
UA = "blotquant-gate2/0.1 (research use; contact: sofiapetrusenko.dev)"

# Licences accepted. CC BY and CC0 only: NC conflicts with an MIT-licensed
# repository's terms of use, and ND makes a crop a forbidden derivative.
ACCEPTED_LICENCES = {"cc by", "cc-by", "cc by 4.0", "cc by 3.0", "cc0", "cc by-sa"}
# cc by-sa is listed so it shows up and can be REJECTED on sight rather than
# silently missed; treat_sa_as_accepted stays False.
TREAT_SA_AS_ACCEPTED = False

DEFAULT_QUERY = (
    '(FIG:"western blot" OR FIG:"immunoblot") '
    "AND OPEN_ACCESS:Y AND IN_EPMC:Y AND NOT SRC:PPR"
)

# Caption must look like a blot...
POS = re.compile(
    r"\b(western blot|immunoblot|immuno-blot|blotted with|anti-\w+ antibod|kDa)\b", re.I
)
# ...and must not look like these.
NEG = re.compile(r"\b(dot blot|northern blot|southern blot|far-western|schematic)\b", re.I)
# Weak signal that the panel has countable lanes.
LANEISH = re.compile(r"\b(lane|lanes)\b", re.I)


def get(url: str, *, retries: int = 3, sleep: float = 0.4) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            time.sleep(sleep)  # be a polite client of a shared public API
            return data
        except Exception as exc:  # noqa: BLE001 - surface the last failure verbatim
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET failed after {retries} tries: {url}") from last


def licence_ok(lic: Optional[str]) -> bool:
    if not lic:
        return False
    norm = lic.strip().lower()
    if norm in {"cc by-sa", "cc-by-sa"} and not TREAT_SA_AS_ACCEPTED:
        return False
    return norm in ACCEPTED_LICENCES


@dataclass
class Candidate:
    keep: str = ""          # human writes y here; everything else is ignored by fetch
    pmcid: str = ""
    doi: str = ""
    licence: str = ""
    year: str = ""
    journal: str = ""
    fig_label: str = ""
    graphic_href: str = ""
    laneish: str = ""
    caption: str = ""
    article_url: str = ""
    # filled by fetch:
    file: str = ""
    sha256: str = ""
    px: str = ""
    notes: str = ""


FIELDS = list(asdict(Candidate()).keys())


def search(query: str, limit: int) -> Iterator[dict]:
    cursor, seen = "*", 0
    while seen < limit:
        params = urllib.parse.urlencode(
            {
                "query": query,
                "resultType": "core",
                "format": "json",
                "pageSize": 100,
                "cursorMark": cursor,
            }
        )
        payload = json.loads(get(f"{EPMC}/search?{params}"))
        results = payload.get("resultList", {}).get("result", [])
        if not results:
            return
        for r in results:
            yield r
            seen += 1
            if seen >= limit:
                return
        nxt = payload.get("nextCursorMark")
        if not nxt or nxt == cursor:
            return
        cursor = nxt


def figures(pmcid: str) -> Iterator[tuple[str, str, str]]:
    """Yield (label, caption_text, graphic_href) for each <fig> in the JATS."""
    try:
        xml = get(f"{EPMC}/{pmcid}/fullTextXML")
    except RuntimeError:
        return
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return
    xlink = "{http://www.w3.org/1999/xlink}href"
    for fig in root.iter("fig"):
        label = (fig.findtext("label") or "").strip()
        caption_el = fig.find("caption")
        caption = " ".join(caption_el.itertext()).strip() if caption_el is not None else ""
        caption = re.sub(r"\s+", " ", caption)
        graphic = fig.find(".//graphic")
        href = graphic.get(xlink, "") if graphic is not None else ""
        yield label, caption, href


def cmd_scan(args: argparse.Namespace) -> int:
    rows: list[Candidate] = []
    articles = 0
    for rec in search(args.query, args.limit):
        articles += 1
        lic = rec.get("license")
        if not licence_ok(lic):
            continue
        pmcid = rec.get("pmcid") or ""
        if not pmcid:
            continue
        for label, caption, href in figures(pmcid):
            if not href or NEG.search(caption) or not POS.search(caption):
                continue
            rows.append(
                Candidate(
                    pmcid=pmcid,
                    doi=rec.get("doi", ""),
                    licence=lic or "",
                    year=str(rec.get("pubYear", "")),
                    journal=rec.get("journalInfo", {}).get("journal", {}).get("title", ""),
                    fig_label=label,
                    graphic_href=href,
                    laneish="y" if LANEISH.search(caption) else "",
                    caption=caption[:400],
                    article_url=f"https://europepmc.org/article/PMC/{pmcid}",
                )
            )
        print(f"\rscanned {articles} articles, {len(rows)} candidate figures", end="", file=sys.stderr)
    print(file=sys.stderr)

    out = Path(args.out)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for row in rows:
            w.writerow(asdict(row))
    print(f"{len(rows)} candidate figures from {articles} articles -> {out}")
    print("Set keep=y on the rows worth downloading, then run `fetch`.")
    return 0


def oa_package_url(pmcid: str) -> Optional[str]:
    xml = get(f"{OA_FCGI}?id={pmcid}")
    root = ET.fromstring(xml)
    for link in root.iter("link"):
        if link.get("format") == "tgz":
            href = link.get("href", "")
            # ftp:// is served over https from the same host
            return href.replace("ftp://ftp.ncbi.nlm.nih.gov", "https://ftp.ncbi.nlm.nih.gov")
    return None


def image_size(path: Path) -> str:
    """Width x height for PNG/JPEG/TIFF without pulling in Pillow."""
    data = path.read_bytes()
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", data[16:24])
            return f"{w}x{h}"
        if data[:2] == b"\xff\xd8":
            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker, seglen = data[i + 1], struct.unpack(">H", data[i + 2 : i + 4])[0]
                if marker in range(0xC0, 0xCF) and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                    return f"{w}x{h}"
                i += 2 + seglen
        if data[:4] in (b"II*\x00", b"MM\x00*"):
            return "tiff"
    except Exception:  # noqa: BLE001 - size is a convenience column, never a gate
        pass
    return "?"


def cmd_fetch(args: argparse.Namespace) -> int:
    src = Path(args.infile)
    rows = list(csv.DictReader(src.open(encoding="utf-8")))
    wanted = [r for r in rows if r.get("keep", "").strip().lower() == "y"]
    if not wanted:
        print("No rows marked keep=y. Nothing to do.")
        return 1

    outdir = Path(args.dir)
    outdir.mkdir(parents=True, exist_ok=True)
    by_pmcid: dict[str, list[dict]] = {}
    for r in wanted:
        by_pmcid.setdefault(r["pmcid"], []).append(r)

    for pmcid, group in by_pmcid.items():
        try:
            tgz_url = oa_package_url(pmcid)
            if not tgz_url:
                for r in group:
                    r["notes"] = "no OA package (not in OA subset — do not use)"
                continue
            tgz_path = outdir / f"{pmcid}.tar.gz"
            if not tgz_path.exists():
                tgz_path.write_bytes(get(tgz_url))
            with tarfile.open(tgz_path) as tar:
                members = tar.getmembers()
                for r in group:
                    stem = Path(r["graphic_href"]).name.lower()
                    hit = next(
                        (m for m in members if Path(m.name).stem.lower() == stem), None
                    )
                    if hit is None:
                        r["notes"] = f"graphic {stem} not in package"
                        continue
                    ext = Path(hit.name).suffix
                    dest = outdir / f"{pmcid}_{re.sub(r'[^A-Za-z0-9]+', '', r['fig_label']) or 'fig'}{ext}"
                    fobj = tar.extractfile(hit)
                    if fobj is None:
                        r["notes"] = "unreadable member"
                        continue
                    blob = fobj.read()
                    dest.write_bytes(blob)
                    r["file"] = dest.name
                    r["sha256"] = hashlib.sha256(blob).hexdigest()
                    r["px"] = image_size(dest)
                    if ext.lower() in {".jpg", ".jpeg"}:
                        r["notes"] = "lossy source format"
            tgz_path.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001 - one bad article must not kill the run
            for r in group:
                r["notes"] = f"error: {exc}"

    with src.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    prov = outdir / "provenance.md"
    with prov.open("w", encoding="utf-8") as fh:
        fh.write("# Real-blot source provenance\n\n")
        fh.write(
            "Recorded at download time by `gate2_epmc.py fetch`. Every image used in\n"
            "the ImageJ comparison must appear here with a licence permitting a crop.\n\n"
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
    got = sum(1 for r in wanted if r.get("file"))
    print(f"downloaded {got}/{len(wanted)} -> {outdir}, provenance in {prov}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan")
    s.add_argument("--query", default=DEFAULT_QUERY)
    s.add_argument("--limit", type=int, default=200, help="articles to examine")
    s.add_argument("--out", default="candidates.csv")
    s.set_defaults(func=cmd_scan)

    f = sub.add_parser("fetch")
    f.add_argument("--in", dest="infile", default="candidates.csv")
    f.add_argument("--dir", default="images")
    f.set_defaults(func=cmd_fetch)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
