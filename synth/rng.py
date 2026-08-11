"""Deterministic seed derivation.

Two guarantees are provided here, and the reproducibility of the whole gold set
rests on them:

1. Seeds are derived from ``(master_seed, label)`` by SHA-256, so they do not
   depend on numpy, on iteration order, or on the platform.
2. Random numbers come from :class:`numpy.random.RandomState` (the *legacy*
   generator), which numpy guarantees to be stream-stable across releases.
   :class:`numpy.random.Generator` carries no such guarantee and is therefore not
   used anywhere in this package.

Each component of an image (background, bands, defects, noise) draws from its own
labelled stream, so adding draws in one component cannot shift another.
"""

from __future__ import annotations

import hashlib

import numpy as np

SEED_BYTES = 4
"""Number of SHA-256 digest bytes consumed per derived seed (RandomState takes uint32)."""


def derive_seed(master_seed: int, label: str) -> int:
    """Return a uint32 seed determined solely by ``master_seed`` and ``label``.

    The mapping is stable across platforms, Python versions and numpy versions.
    """
    if not isinstance(master_seed, int) or isinstance(master_seed, bool):
        raise TypeError(f"master_seed must be an int, got {type(master_seed).__name__}")
    if master_seed < 0:
        raise ValueError(f"master_seed must be non-negative, got {master_seed}")
    if not label:
        raise ValueError("label must be a non-empty string identifying the random stream")
    digest = hashlib.sha256(f"{master_seed}:{label}".encode()).digest()
    return int.from_bytes(digest[:SEED_BYTES], "big")


def make_rng(master_seed: int, label: str) -> np.random.RandomState:
    """Return the legacy RandomState for the stream ``label`` under ``master_seed``."""
    return np.random.RandomState(derive_seed(master_seed, label))
