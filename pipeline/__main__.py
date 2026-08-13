"""CLI entry point: ``python -m pipeline run <image> --config configs/default.yaml --out results/``.

``--config`` is required and has no default: the parameter set is the result, and a
built-in one would make every number unattributable.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline import PIPELINE_VERSION
from pipeline.analyze import analyze_image, write_result
from pipeline.config import load_config
from pipeline.errors import PipelineError


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the pipeline CLI."""
    parser = argparse.ArgumentParser(
        prog="python -m pipeline",
        description=f"Quantify a gel-doc image. blotquant pipeline version {PIPELINE_VERSION}.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="analyse one image and write its result JSON")
    run.add_argument("image", type=Path, help="path to an 8/16-bit grayscale TIFF, PNG or JPEG")
    run.add_argument(
        "--config",
        type=Path,
        required=True,
        help="YAML parameter set; every parameter used is echoed into the result",
    )
    run.add_argument(
        "--out",
        type=Path,
        required=True,
        help="output directory; the result is written as <image stem>.json",
    )
    run.add_argument(
        "--reference-band",
        dest="reference_band_ids",
        action="append",
        metavar="BAND_ID",
        help=(
            "band id to normalize against; repeat once per reference band. Required by the "
            "housekeeping normalization modes and refused by total_protein: the pipeline "
            "never infers which band is the loading control. Ids come from the bands[] of a "
            "previous run on the same image"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Returns 0 on success, 1 on any pipeline or filesystem error."""
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        result = analyze_image(
            args.image, config, reference_band_ids=args.reference_band_ids
        )
        path = write_result(result, args.out, args.image.stem)
    except (PipelineError, FileNotFoundError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"error: cannot write the result to {args.out}: {error}", file=sys.stderr)
        return 1

    normalization = result["normalization"]
    print(
        f"{args.image}: {len(result['lanes'])} lane(s), {len(result['bands'])} band(s), "
        f"background={result['provenance']['parameters']['background']['method']}, "
        f"normalization={normalization['mode']}"
    )
    flagged = sum(1 for band in result["bands"] if band["qc_flags"])
    excluded = sum(1 for ratio in normalization["ratios"] if ratio["excluded"])
    print(
        f"QC: image {result['image_qc_flags'] or 'clean'}; {flagged} of "
        f"{len(result['bands'])} band(s) flagged; {excluded} of "
        f"{len(normalization['ratios'])} ratio(s) excluded from normalization"
    )
    for warning in normalization["warnings"]:
        print(f"warning: normalization: {warning}")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
