"""Repo-root conftest.

Guarantees that the repository root is on ``sys.path`` for the test session, so
``synth`` and ``evals`` import as top-level packages without an editable install.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Return the absolute repository root directory."""
    return REPO_ROOT


@pytest.fixture(scope="session")
def committed_data_dir(repo_root: Path) -> Path:
    """Return the committed gold-set root (``data/``), failing loudly if absent."""
    data_dir = repo_root / "data"
    if not data_dir.is_dir():
        raise FileNotFoundError(
            f"committed gold set missing at {data_dir}; regenerate with "
            f"'python -m synth --seed 42 --out data/'"
        )
    return data_dir
