"""CLI entry point: ``python -m synth --seed 42 --out data/``.

Both arguments are required. There is no default seed and no default output
directory: silently generating a gold set from an implicit seed is exactly the kind
of hidden state this project refuses.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from synth import SYNTH_VERSION
from synth.config import DEFAULT_CONFIG
from synth.errors import SynthError
from synth.generator import CONFIG_FILENAME, generate_dataset


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the generator CLI."""
    parser = argparse.ArgumentParser(
        prog="python -m synth",
        description=(
            "Generate the synthetic western-blot gold set (images + ground truth). "
            f"synth version {SYNTH_VERSION}."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        required=True,
        help="master seed; every random stream is derived from it",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="output root; images/ , ground_truth/ and the config echo are written here",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "replace an existing gold set: skips the seed/version/config-digest checks "
            "and deletes any file in images/ or ground_truth/ that this run does not "
            "write (deleted names are printed)"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the generator. Returns 0 on success, 1 on a generator or filesystem error."""
    args = build_parser().parse_args(argv)
    try:
        result = generate_dataset(
            out_dir=args.out,
            master_seed=args.seed,
            config=DEFAULT_CONFIG,
            force=args.force,
        )
    except SynthError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"error: cannot write the dataset to {args.out}: {error}", file=sys.stderr)
        return 1

    for item in result.images:
        print(f"{item.split}\t{item.image_id}\t{item.image_path}")
    if result.removed_files:
        print(
            f"removed {len(result.removed_files)} file(s) left by an earlier generation: "
            f"{', '.join(result.removed_files)}"
        )
    counts: dict[str, int] = {}
    for item in result.images:
        counts[item.split] = counts.get(item.split, 0) + 1
    summary = ", ".join(f"{split}={counts[split]}" for split in sorted(counts))
    print(
        f"wrote {len(result.images)} images ({summary}) to {args.out} "
        f"(config: {CONFIG_FILENAME})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
