#!/usr/bin/env python3
"""
refilter.py — fix for the 856 -> 3 collapse.

Root causes it corrects:
  1. scan truncated captions to 400 chars, cutting off trailing
     "...GAPDH was used as loading control" sentences before the filter ran.
  2. the shortlist filter required the word "lane" (present in only 5% of
     captions); lane count is a property of the image, judged by eye, not
     of the caption.

What it does: for each unique PMCID already in candidates.csv, re-fetch the
full-text XML once, rebuild FULL captions, keep figures whose caption names a
loading control (and still looks like a blot), write shortlist.csv with the
full caption. No new article search — works off the scan you already ran.

    python3 refilter.py            # reads candidates.csv, writes shortlist.csv
"""

from __future__ import annotations

import csv
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
UA = "blotquant-gate2/0.1 (research use; contact: sofiapetrusenko.dev)"

CTRL = re.compile(
    r"loading control|gapdh|β-?actin|beta-?actin|\bactin\b|tubulin|vinculin"
    r"|hsp90|hsc70|lamin|histone h3|ponceau|total protein|coomassie|stain-?free"
    r"|amido black|revert",
    re.I,
)
POS = re.compile(
    r"\b(western blot|immunoblot|immuno-blot|blotted with|anti-\w+ antibod|kDa)\b", re.I
)
NEG = re.compile(r"\b(dot blot|northern blot|southern blot|far-western|schematic)\b", re.I)
LANEISH = re.compile(r"\b(lane|lanes)\b", re.I)


def get(url: str, retries: int = 3) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            time.sleep(0.4)
            return data
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET failed: {url}") from last


def full_captions(pmcid: str) -> dict[str, tuple[str, str]]:
    """href -> (label, full caption) for every <fig> in the article."""
    out: dict[str, tuple[str, str]] = {}
    try:
        root = ET.fromstring(get(f"{EPMC}/{pmcid}/fullTextXML"))
    except (RuntimeError, ET.ParseError):
        return out
    xlink = "{http://www.w3.org/1999/xlink}href"
    for fig in root.iter("fig"):
        label = (fig.findtext("label") or "").strip()
        cap_el = fig.find("caption")
        caption = " ".join(cap_el.itertext()).strip() if cap_el is not None else ""
        caption = re.sub(r"\s+", " ", caption)
        graphic = fig.find(".//graphic")
        href = graphic.get(xlink, "") if graphic is not None else ""
        if href:
            out[href] = (label, caption)
    return out


def main() -> int:
    rows = list(csv.DictReader(open("candidates.csv", encoding="utf-8")))
    pmcids = sorted({r["pmcid"] for r in rows})
    print(f"{len(rows)} candidate figures across {len(pmcids)} articles; refetching full captions...")

    kept: list[dict] = []
    for i, pmcid in enumerate(pmcids, 1):
        caps = full_captions(pmcid)
        for r in (x for x in rows if x["pmcid"] == pmcid):
            label, cap = caps.get(r["graphic_href"], ("", ""))
            if not cap:
                cap = r["caption"]  # fall back to the truncated one
            if NEG.search(cap) or not POS.search(cap) or not CTRL.search(cap):
                continue
            r = dict(r)
            r["caption"] = cap
            r["laneish"] = "y" if LANEISH.search(cap) else ""
            ctrl_hit = CTRL.search(cap)
            r["notes"] = f"ctrl_word={ctrl_hit.group(0).lower()}"
            kept.append(r)
        print(f"\r{i}/{len(pmcids)} articles, {len(kept)} kept", end="", file=sys.stderr)
    print(file=sys.stderr)

    if not kept:
        print("0 rows kept — send this output back before doing anything else.")
        return 1

    with open("shortlist.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(kept)
    lane_y = sum(1 for r in kept if r["laneish"] == "y")
    print(f"{len(rows)} -> {len(kept)} figures with a stated loading control -> shortlist.csv")
    print(f"(of those, {lane_y} also mention lanes — sort by the laneish column to review those first)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
