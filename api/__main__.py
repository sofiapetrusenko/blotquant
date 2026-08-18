"""Serve the API locally: ``python -m api --storage-root results/ --config-dir configs/``.

Both directories are required and have no defaults, for the same reason ``--config`` is
required on the pipeline CLI: where results are written and which parameter sets are
offered are decisions, and a built-in guess would make a running server's behaviour
depend on something nobody wrote down.

This is a local development server. Deployment is Phase 4b.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

from api.app import create_app
from pipeline.errors import PipelineError

DEFAULT_HOST = "127.0.0.1"
"""Loopback, not 0.0.0.0: a development server binds where it can be reached by its author."""

DEFAULT_PORT = 8000


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the API server CLI."""
    parser = argparse.ArgumentParser(
        prog="python -m api", description="Serve the blotquant API."
    )
    parser.add_argument(
        "--storage-root",
        type=Path,
        required=True,
        help="directory analysed results are stored under; never inside a ground_truth/ tree",
    )
    parser.add_argument(
        "--config-dir",
        type=Path,
        required=True,
        help="directory of YAML parameter sets callers select by name (e.g. configs/)",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"bind address ({DEFAULT_HOST})")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"bind port ({DEFAULT_PORT})"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Build the app and serve it. Returns 0 when the server exits normally, 1 on a bad setup.

    A misconfigured server fails before it binds -- an unwritable storage root, or a config
    directory that does not exist -- and it reports that the way ``python -m pipeline`` does:
    the actionable message on stderr and exit 1, not a traceback. The two CLIs are the same
    kind of program and a user should not have to learn two failure shapes.
    """
    args = build_parser().parse_args(argv)
    try:
        app = create_app(storage_root=args.storage_root, config_dir=args.config_dir)
    except PipelineError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
