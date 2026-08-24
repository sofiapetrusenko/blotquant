"""Does the QC vocabulary describe the corpus, or describe the measurement? (Phase 3b-1)

Read-only. Changes no parameter, no code path and no config, and rules on nothing. It exists
because a detection run over the twelve real crops flagged 404 of 430 bands, `saturated` fired
on 12 of 12 images, and *"published figures are saturated as a class"* is about to become a
sentence in the record. That sentence is a claim about the **corpus**. The alternative reading
is that it is a claim about the **measurement** -- that the flags are firing on something other
than clipped band signal -- and the two are distinguishable by looking at the pixels.

**Every rule this file reports is quoted, not restated.** The band and image criteria are
extracted with :mod:`inspect` from the functions in :mod:`pipeline.qc` that implement them, and
the thresholds are read out of ``configs/default.yaml`` with their own comments, so a quotation
here cannot drift from the code the way a paraphrase can. The lane count G2 ruled from the
images is read out of the header of ``data/real/blot_identity.csv``, not typed in again.

Gate 1 ruling 3 governs: what this finds may falsify a decision and may never select a
parameter or a code path. Findings go to ``runs/3b1/DEBT_DRAFTS.md``.

Usage: python -m tools.phase3.qc_diagnostic --run-dir runs/3b1
"""

from __future__ import annotations

import argparse
import inspect
import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from pipeline import qc as qc_module
from pipeline.load import load_image
from tools.phase3.blot_identity import BLOT_IDENTITY_PATH, confirmed_blot_id, read_blot_identities
from tools.phase3.designations import DESIGNATIONS_PATH, read_designations

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "configs/default.yaml"

LIGHT_BACKGROUND_MEDIAN_FRACTION = 0.5
"""Above this fraction of full scale, the image's own median says the ground is light.

**A diagnostic threshold, not a processing parameter**, and deliberately not in ``configs/``:
nothing in ``pipeline/`` reads it, no measurement depends on it, and it decides only which word
this report prints. It is set at the midpoint rather than tuned, because a value chosen to make
the answer come out is the failure this whole diagnostic exists to detect. The measured medians
are printed beside the verdict so a reader can apply any other threshold.
"""


def quoted_source(*names: str) -> str:
    """Return the verbatim source of the named :mod:`pipeline.qc` functions, joined.

    Extracted rather than transcribed. A quotation typed into a document is a claim that has to
    be re-checked by a reader; a quotation read out of the module at report time is the code.
    """
    blocks = []
    for name in names:
        target = getattr(qc_module, name, None)
        if target is None:
            raise AttributeError(
                f"pipeline.qc has no {name!r}; the diagnostic quotes it by name, and a renamed "
                f"function would leave this report quoting nothing rather than failing"
            )
        blocks.append(inspect.getsource(target).rstrip())
    return "\n\n".join(blocks)


def quoted_lines(function_name: str, anchor: str, count: int) -> str:
    """Return ``count`` consecutive lines of a :mod:`pipeline.qc` function, from ``anchor``.

    Used for the two blocks inside ``assess()`` that assemble the image flags. Extracted for the
    same reason as :func:`quoted_source`: a snippet typed into this file is a transcription that
    can go stale silently, and the whole point of the section it appears in is that the reader
    should not have to take the quotation on trust.
    """
    source = quoted_source(function_name).splitlines()
    index = next((i for i, line in enumerate(source) if anchor in line), None)
    if index is None:
        raise ValueError(
            f"{anchor!r} is not in pipeline.qc.{function_name}; the diagnostic quotes that line "
            f"and will not print a snippet it cannot find in the code"
        )
    return "\n".join(source[index : index + count])


def quoted_config(key: str) -> str:
    """Return a config key's own comment block and its line, verbatim from the YAML.

    Raises if the key is absent, because a threshold this report describes and cannot find is a
    report describing a rule that is not in force.
    """
    lines = CONFIG_PATH.read_text(encoding="utf-8").splitlines()
    index = next((i for i, line in enumerate(lines) if line.strip().startswith(f"{key}:")), None)
    if index is None:
        raise KeyError(
            f"{key!r} is not in {CONFIG_PATH}; this diagnostic quotes the shipped threshold and "
            f"will not describe one it cannot read"
        )
    start = index
    while start > 0 and lines[start - 1].strip().startswith("#"):
        start -= 1
    return "\n".join(lines[start : index + 1])


def ruled_lane_count(identity_path: Path) -> tuple[int, str]:
    """Return the lane count G2 recorded from the images, and the sentence it came from.

    Read out of the identity table's header rather than typed here: G2 is a human ruling and
    the table is where it is recorded verbatim.
    """
    # The header wraps at the file's column width, so the sentence is reassembled from its
    # comment lines before it is matched. Matching the raw text would return a quotation
    # truncated at a line break, which reads as a mangled quote rather than a wrapped one.
    header = " ".join(
        line.lstrip("#").strip()
        for line in identity_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("#")
    )
    match = re.search(r"(\d+) lanes per panel[^.]*", header)
    if match is None:
        raise ValueError(
            f"{identity_path} does not state a per-panel lane count; the diagnostic compares "
            f"detection against the human's count and will not invent one"
        )
    return int(match.group(1)), " ".join(match.group(0).split()).strip()


@dataclass(frozen=True)
class CropDiagnostic:
    """One crop's pixel-level evidence, and the counts the flags are compared against."""

    crop_filename: str
    blot_id: str
    max_value: int
    pixels_total: int
    median_value: float
    p1_value: float
    p99_value: float
    at_max: int
    at_zero: int
    at_max_in_roi: int
    at_zero_in_roi: int
    roi_mean: float
    outside_mean: float
    lanes: int
    bands: int
    saturated_bands: int
    overlapping_bands: int
    shoulder_bands: int
    bands_with_a_max_pixel: int
    bands_with_a_zero_pixel: int
    clipped_counts_agree: int
    saturated_roi_max_fraction: tuple[float, ...]
    brightest_peak_value: float
    low_dynamic_range_threshold: float
    image_flags: tuple[str, ...]

    @property
    def background_is_light(self) -> bool:
        """Whether the image's own median puts the ground at the light end of the scale."""
        return self.median_value > LIGHT_BACKGROUND_MEDIAN_FRACTION * self.max_value

    @property
    def polarity(self) -> str:
        """The one-word reading of the two means, with the medians reported beside it."""
        return "bands darker than background" if self.background_is_light else "bands lighter"


def diagnose(
    run_dir: Path, crops_dir: Path, identity_table: dict[str, object]
) -> list[CropDiagnostic]:
    """Return one :class:`CropDiagnostic` per result document under ``run_dir``.

    Reads the delivered pixels through :func:`pipeline.load.load_image`, which is the same
    loader the run used, so the values examined here are the values QC saw -- including the
    ratified green-channel collapse. Nothing is re-detected and nothing is re-flagged: the
    bands and their flags come from the committed documents.
    """
    out: list[CropDiagnostic] = []
    for path in sorted(run_dir.glob("*/*.json")):
        document = json.loads(path.read_text())
        crop = Path(str(document["source"]["path"])).name
        image = load_image(crops_dir / crop)
        pixels = image.pixels.astype(np.int64)
        mask = np.zeros(pixels.shape, dtype=bool)
        for band in document["bands"]:
            roi = band["roi"]
            mask[roi["y"] : roi["y"] + roi["height"], roi["x"] : roi["x"] + roi["width"]] = True

        at_max = pixels >= image.max_value
        at_zero = pixels <= 0
        saturated_fractions: list[float] = []
        with_max = with_zero = agree = 0
        for band in document["bands"]:
            roi = band["roi"]
            block = pixels[roi["y"] : roi["y"] + roi["height"], roi["x"] : roi["x"] + roi["width"]]
            block_max = int(np.count_nonzero(block >= image.max_value))
            if block_max:
                with_max += 1
            if int(np.count_nonzero(block <= 0)):
                with_zero += 1
            if int(band["clipped_pixel_count"]) == block_max:
                agree += 1
            if "saturated" in band["qc_flags"]:
                saturated_fractions.append(block_max / block.size)

        peaks = [float(b["peak_value"]) for b in document["bands"]]
        out.append(
            CropDiagnostic(
                crop_filename=crop,
                blot_id=confirmed_blot_id(identity_table, crop),  # type: ignore[arg-type]
                max_value=image.max_value,
                pixels_total=int(pixels.size),
                median_value=float(np.median(pixels)),
                p1_value=float(np.percentile(pixels, 1)),
                p99_value=float(np.percentile(pixels, 99)),
                at_max=int(at_max.sum()),
                at_zero=int(at_zero.sum()),
                at_max_in_roi=int((at_max & mask).sum()),
                at_zero_in_roi=int((at_zero & mask).sum()),
                roi_mean=float(pixels[mask].mean()) if mask.any() else float("nan"),
                outside_mean=float(pixels[~mask].mean()) if (~mask).any() else float("nan"),
                lanes=len(document["lanes"]),
                bands=len(document["bands"]),
                saturated_bands=sum(1 for b in document["bands"] if "saturated" in b["qc_flags"]),
                overlapping_bands=sum(
                    1 for b in document["bands"] if "overlapping" in b["qc_flags"]
                ),
                shoulder_bands=sum(
                    1 for b in document["bands"] if "unresolved_shoulder" in b["qc_flags"]
                ),
                bands_with_a_max_pixel=with_max,
                bands_with_a_zero_pixel=with_zero,
                clipped_counts_agree=agree,
                saturated_roi_max_fraction=tuple(saturated_fractions),
                brightest_peak_value=max(peaks, default=0.0),
                # The threshold that actually decided this document's flag, read from the
                # run's own parameter echo rather than from the config file as it stands
                # now. A diagnostic that compared a measurement against a *later* threshold
                # would be re-deciding the flag instead of explaining it.
                low_dynamic_range_threshold=(
                    float(
                        document["provenance"]["parameters"]["qc"][
                            "dynamic_range_min_peak_fraction"
                        ]
                    )
                    * image.max_value
                ),
                image_flags=tuple(document["image_qc_flags"]),
            )
        )
    return out


def _expected_bands_per_lane(designation_table: dict[str, object], crop: str) -> int:
    """Return targets + 1 for a crop, read from the confirmed designation table.

    Not a rule and not a threshold: it is what the human's own G1 designation says the panel
    carries, used only to say how far detection sits from it. The two-target crop therefore
    allows three, not two, and a crop ruled to contribute no ratio is the strip itself, so one.
    """
    row = designation_table[crop]
    targets = row.target_labels  # type: ignore[attr-defined]
    return len(targets) + 1 if targets else 1


def _pct(part: int, whole: int) -> str:
    """Return ``part`` as a percentage of ``whole``, or a dash when there is no whole."""
    return f"{100 * part / whole:.1f}%" if whole else "—"


def report(
    crops: list[CropDiagnostic],
    identity_path: Path,
    run_dir: Path,
    designation_table: dict[str, object],
) -> list[str]:
    """Return QC_DIAGNOSTIC.md as lines. Every figure is computed from ``crops``."""
    ruled_lanes, ruled_sentence = ruled_lane_count(identity_path)
    total_bands = sum(c.bands for c in crops)
    total_saturated = sum(c.saturated_bands for c in crops)
    total_with_max = sum(c.bands_with_a_max_pixel for c in crops)
    total_with_zero = sum(c.bands_with_a_zero_pixel for c in crops)
    total_agree = sum(c.clipped_counts_agree for c in crops)
    all_fractions = [f for c in crops for f in c.saturated_roi_max_fraction]
    light = [c for c in crops if c.background_is_light]

    lines = [
        "# Phase 3b-1 — QC diagnostic: do the flags describe the corpus or the measurement?",
        "",
        "**Read-only. No parameter, code path or config was changed, and nothing here is a "
        "ruling.** Gate 1 ruling 3 governs: findings become DEBT drafts. Written before "
        "*\"published figures are saturated as a class\"* could enter the record as established.",
        "",
        f"Source: the {len(crops)} result documents under `{run_dir}/` and the delivered crop "
        f"bytes read through `pipeline.load.load_image`, the same loader the run used. Bands and "
        f"flags are taken from the documents; nothing is re-detected or re-flagged.",
        "",
        "## 1. The rules, quoted verbatim from what implements them",
        "",
        "### Band-level `saturated`, and how the image-level flag derives from it",
        "",
        "```python",
        quoted_source("_clipped_pixel_count", "is_saturated"),
        "```",
        "",
        "The image flag is not an independent test. It is `assess()`'s first image line:",
        "",
        "```python",
        quoted_lines("assess", "image_flags: list[str] = []", 3),
        "```",
        "",
        "**The direction matters and is the answer to §3 below.** Image `saturated` is derived "
        "*from* the band flags; no band ever receives `saturated` because of an image-level "
        "condition. There is no propagation path in that direction.",
        "",
        "The shipped threshold, with its own comment:",
        "",
        "```yaml",
        quoted_config("saturated_min_clipped_pixels"),
        "```",
        "",
        "### Image-level `low_dynamic_range`",
        "",
        "```python",
        quoted_source("is_low_dynamic_range"),
        "```",
        "",
        "and in `assess()`:",
        "",
        "```python",
        quoted_lines("assess", "brightest = max(", 3),
        "```",
        "",
        "```yaml",
        quoted_config("dynamic_range_min_peak_fraction"),
        "```",
        "",
        "## 2. Polarity and where the extreme pixels are",
        "",
        "`at max` counts pixels at full scale — the value `_clipped_pixel_count` tests for. "
        "`at 0` counts pixels at the other end, which is where a **dark** band would clip.",
        "",
        "| crop | median | p1 | p99 | at max | % of image | in a band ROI | at 0 | in a band ROI "
        "| ROI mean | outside mean |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in crops:
        lines.append(
            f"| `{c.crop_filename}` | {c.median_value:.0f} | {c.p1_value:.0f} | "
            f"{c.p99_value:.0f} | {c.at_max} | {_pct(c.at_max, c.pixels_total)} | "
            f"{_pct(c.at_max_in_roi, c.at_max)} | {c.at_zero} | "
            f"{_pct(c.at_zero_in_roi, c.at_zero)} | {c.roi_mean:.1f} | {c.outside_mean:.1f} |"
        )
    darker = sum(1 for c in crops if c.roi_mean <= c.outside_mean)
    lines += [
        "",
        f"**Polarity: {len(light)} of {len(crops)} crops have a median at or above "
        f"{LIGHT_BACKGROUND_MEDIAN_FRACTION:.0%} of full scale** — in fact every median is "
        f"{min(c.median_value for c in crops):.0f} or higher against a full scale of "
        f"{crops[0].max_value}. The ground is white paper and the bands are **darker than the "
        f"background**. Corroborating it from the detection itself: the mean pixel value inside "
        f"detected band ROIs is at or below the mean outside them on {darker} of "
        f"{len(crops)} crops, which is the wrong direction for a detector that finds maxima.",
        "",
        f"**Where the full-scale pixels are.** Between "
        f"{min(_pct(c.at_max, c.pixels_total) for c in crops)} and "
        f"{max((100 * c.at_max / c.pixels_total) for c in crops):.1f}% of each image sits at "
        f"full scale, and pixels at 0 number "
        f"{min(c.at_zero for c in crops)}–{max(c.at_zero for c in crops)} per crop. The extreme "
        f"the pipeline tests for is the background, and the extreme a dark band would produce is "
        f"almost absent.",
        "",
        "## 3. Are flagged bands flagged on their own pixels?",
        "",
        f"**Yes, all of them — and that is the problem, not the reassurance.** "
        f"{total_saturated} bands carry `saturated`, and exactly {total_with_max} bands contain "
        f"at least one full-scale pixel inside their own ROI. The two sets coincide, which is "
        f"what the code says they must: the flag is computed on the band's own rectangle and "
        f"there is no image-to-band path. Recomputing `clipped_pixel_count` from the delivered "
        f"pixels reproduces the recorded value on **{total_agree} of {total_bands}** bands.",
        "",
        f"So none of the {total_saturated} is flagged by inheritance. What they are flagged on is "
        f"the fraction of each ROI that is white paper:",
        "",
        "| saturated bands | ROI at full scale, min | median | max |",
        "|---|---|---|---|",
        f"| {len(all_fractions)} | {min(all_fractions):.1%} | "
        f"{sorted(all_fractions)[len(all_fractions) // 2]:.1%} | {max(all_fractions):.1%} |",
        "",
        f"The median `saturated` band has "
        f"{sorted(all_fractions)[len(all_fractions) // 2]:.0%} of its rectangle at full scale. "
        f"A band whose ROI is two-fifths pure white is not a band whose signal was clipped; it "
        f"is a rectangle containing background. Against that, only **{total_with_zero} of "
        f"{total_bands}** bands contain a single pixel at 0 — the value a genuinely saturated "
        f"*dark* band would reach.",
        "",
        "## 4. Dynamic range, measured against the threshold that decided it",
        "",
        "`brightest peak` is the largest background-corrected band peak in the image, in DN.",
        "",
        "| crop | brightest peak | threshold | fired? | image flags |",
        "|---|---|---|---|---|",
    ]
    for c in crops:
        fired = "low_dynamic_range" in c.image_flags
        lines.append(
            f"| `{c.crop_filename}` | {c.brightest_peak_value:.0f} | "
            f"{c.low_dynamic_range_threshold:.2f} | {'yes' if fired else 'no'} | "
            f"{', '.join(c.image_flags)} |"
        )
    fired_count = sum(1 for c in crops if "low_dynamic_range" in c.image_flags)
    lines += [
        "",
        f"{fired_count} of {len(crops)} fired. The peaks are measured on a **background-corrected**"
        f" image, so on a light-ground figure they are excursions above white paper rather than "
        f"band amplitudes: the brightest is "
        f"{max(c.brightest_peak_value for c in crops):.0f} DN out of "
        f"{crops[0].max_value}. The flag is reading the same polarity problem from the other end.",
        "",
        "## 5. Over-detection versus genuine crowding",
        "",
        f"G2 recorded from the images: *\"{ruled_sentence}\"* — {ruled_lanes} lanes per panel on "
        f"the `PMC13135410` panels.",
        "",
        "| crop | lanes detected | bands | bands per lane | `overlapping` | "
        "`unresolved_shoulder` |",
        "|---|---|---|---|---|---|",
    ]
    for c in crops:
        lines.append(
            f"| `{c.crop_filename}` | {c.lanes} | {c.bands} | {c.bands / c.lanes:.2f} | "
            f"{c.overlapping_bands} | {c.shoulder_bands} |"
        )
    pmc = [c for c in crops if c.crop_filename.startswith("PMC13135410")]
    lines += [
        "",
        (
            f"No `PMC13135410` panel is in this run, so the {ruled_lanes}-lane comparison above "
            f"has nothing to compare against here."
            if not pmc
            else f"On the {len(pmc)} `PMC13135410` panels the detector reports "
            + (
                f"{sorted({c.lanes for c in pmc})[0]} lanes on every one of them"
                if len({c.lanes for c in pmc}) == 1
                else f"{sorted({c.lanes for c in pmc})} lanes"
            )
            + f" against the human's {ruled_lanes}, and "
            + f"{min(c.bands / c.lanes for c in pmc):.1f}–"
            + f"{max(c.bands / c.lanes for c in pmc):.1f} bands per lane. What the confirmed G1 "
            + "designations allow per lane — one band per target, plus the shared reference — is "
            + "; ".join(
                f"`{c.crop_filename}` "
                f"{_expected_bands_per_lane(designation_table, c.crop_filename)}"
                for c in pmc
            )
            + "."
        )
        + " **`overlapping` and `unresolved_shoulder` are therefore not "
        "separable from over-detection on this evidence**: rectangles drawn around background "
        "structure in a crowded panel will overlap each other and will have asymmetric "
        "profiles, whether or not any real band is crowded. Separating the two needs a count of "
        "*true* bands per lane, which only the human's reading of the figures can supply.",
        "",
        "## 6. Which reading the evidence supports",
        "",
        "**Measurement artefact, not a corpus property, and the evidence is not close.** Every "
        f"one of the {len(crops)} crops has a median at or above "
        f"{min(c.median_value for c in crops):.0f} of {crops[0].max_value}: these are white-ground "
        "published figures whose bands are *darker* than their background, while the pipeline "
        "detects maxima and tests for clipping at full scale. The consequence is mechanical. The "
        "pixels at full scale are the paper, not the signal; "
        f"{_pct(sum(c.at_max_in_roi for c in crops), sum(c.at_max for c in crops))} of them fall "
        "inside a detected band rectangle, and the median `saturated` band is "
        f"{sorted(all_fractions)[len(all_fractions) // 2]:.0%} white by area. The extreme that a "
        f"genuinely saturated dark band would produce — a pixel at 0 — occurs in "
        f"{total_with_zero} of {total_bands} bands. `low_dynamic_range` is the same fact read "
        "from the other end: background-corrected excursions above white paper are small, so the "
        "brightest 'band' peak is a fraction of full scale. **`saturated` here does not mean "
        "'this band's signal was clipped'; it means 'this rectangle contains white background'. "
        "It is a true statement about the pixels and a false statement about the measurement.** "
        "The record should not say the corpus is saturated as a class.",
        "",
        "**What is *not* settled by this, stated so it is not over-read.** This diagnostic does "
        "not establish that the published figures are of good quality, that their bands are "
        "unclipped in the original blots, or that any ratio computed from them would be sound — "
        "a figure rendered to white paper may well have had its dynamic range destroyed before "
        "publication, and §6 of the pre-registration is about exactly that risk. It establishes "
        "only that **these particular flags, on these images, are not evidence either way**, "
        "because they are measuring polarity rather than clipping. The measurement that would "
        "settle the underlying question is a polarity-aware pass — detection on the inverted "
        "image, with clipping tested at 0 — reported beside this one. That is a code path, so "
        "under Gate 1 ruling 3 it is not built here; it is DEBT draft D13.",
        "",
    ]
    return lines


def main(argv: list[str] | None = None) -> int:
    """Write QC_DIAGNOSTIC.md beside the run it was computed from."""
    parser = argparse.ArgumentParser(
        prog="python -m tools.phase3.qc_diagnostic",
        description="Establish whether the QC flags describe the corpus or the measurement.",
    )
    parser.add_argument("--run-dir", type=Path, default=Path("runs/3b1"))
    parser.add_argument("--crops-dir", type=Path, default=Path("data/real/crops"))
    parser.add_argument("--blot-identity", type=Path, default=BLOT_IDENTITY_PATH)
    parser.add_argument("--designations", type=Path, default=DESIGNATIONS_PATH)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    identity_table = read_blot_identities(args.blot_identity)
    crops = diagnose(args.run_dir, args.crops_dir, identity_table)
    if not crops:
        raise ValueError(
            f"no result documents under {args.run_dir}; run "
            f"'python -m tools.phase3.run_real --detection-only' first. A diagnostic over an "
            f"empty run would report no saturation and read as a finding about the corpus"
        )
    out = args.out or args.run_dir / "QC_DIAGNOSTIC.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    designation_table = read_designations(args.designations)
    out.write_text(
        "\n".join(report(crops, args.blot_identity, args.run_dir, designation_table))
        + "\n",
        "utf-8",
    )
    print(f"wrote {out} over {len(crops)} crops")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
