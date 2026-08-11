"""The difficulty matrix.

Each generated image is one cell of a matrix over eight axes. Cells are built per
split by taking a balanced level sequence (levels repeated round-robin to the split
size, so counts differ by at most one) and permuting it with a seed derived from the
master seed and the axis name. Balanced-plus-permuted gives two properties the tests
assert: every level of every axis occurs in both splits, and the axes are not
correlated with one another the way a plain modulo schedule would make them.

Bit depth and container format are one combined axis because JPEG cannot carry
16-bit data; enumerating only the legal pairs keeps the illegal combination out of
the matrix instead of silently repairing it later.

The axis levels themselves are generation parameters and live in
:class:`synth.config.MatrixConfig`, so they are covered by the config digest that
guards the frozen gold set. This module only schedules them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

import numpy as np

from synth.config import GeneratorConfig, MatrixConfig
from synth.errors import ConfigError
from synth.rng import derive_seed

_T = TypeVar("_T")

DEV_SPLIT = "dev"
TEST_SPLIT = "test"
SPLITS: tuple[str, ...] = (DEV_SPLIT, TEST_SPLIT)


@dataclass(frozen=True)
class MatrixCell:
    """One image's position in the difficulty matrix.

    A plain value object. The ``format_depth`` level is resolved against the
    :class:`~synth.config.MatrixConfig` once, at construction, so ``image_format`` and
    ``bit_depth`` are carried as fields and no consumer of a cell needs the axis
    definitions to interpret it.
    """

    image_id: str
    split: str
    index_in_split: int
    format_depth: str
    background: str
    noise: str
    band_shape: str
    lane_geometry: str
    defect: str
    exposure: str
    lane_count: int
    image_format: str
    bit_depth: int

    @classmethod
    def from_levels(
        cls,
        matrix: MatrixConfig,
        image_id: str,
        split: str,
        index_in_split: int,
        levels: Mapping[str, Any],
    ) -> MatrixCell:
        """Return the cell for one assignment of axis levels.

        ``levels`` must name every axis in :meth:`~synth.config.MatrixConfig.axis_levels`,
        keyed as :meth:`levels` returns them; a missing axis or an unknown
        ``format_depth`` level raises :class:`ConfigError`.
        """
        missing = sorted(set(matrix.axis_levels()) - set(levels))
        if missing:
            raise ConfigError(
                f"matrix cell {image_id!r} has no level for axes {missing}; every axis in "
                f"MatrixConfig.axis_levels() must be assigned a level"
            )
        format_depth = str(levels["format_depth"])
        image_format, bit_depth = matrix.resolve_format_depth(format_depth)
        return cls(
            image_id=image_id,
            split=split,
            index_in_split=index_in_split,
            format_depth=format_depth,
            background=str(levels["background"]),
            noise=str(levels["noise"]),
            band_shape=str(levels["band_shape"]),
            lane_geometry=str(levels["lane_geometry"]),
            defect=str(levels["defect"]),
            exposure=str(levels["exposure"]),
            lane_count=int(levels["lane_count"]),
            image_format=image_format,
            bit_depth=bit_depth,
        )

    def levels(self) -> dict[str, Any]:
        """Return this cell's axis levels, keyed as :meth:`MatrixConfig.axis_levels`."""
        return {
            "format_depth": self.format_depth,
            "background": self.background,
            "noise": self.noise,
            "band_shape": self.band_shape,
            "lane_geometry": self.lane_geometry,
            "defect": self.defect,
            "exposure": self.exposure,
            "lane_count": self.lane_count,
        }


def balanced_permutation(levels: Sequence[_T], count: int, seed: int) -> list[_T]:
    """Return ``count`` levels, balanced round-robin then permuted by ``seed``.

    Guarantees every level appears at least once when ``count >= len(levels)``, and
    that per-level counts differ by at most one.
    """
    if not levels:
        raise ConfigError("cannot build a matrix axis from an empty level list")
    if count < len(levels):
        raise ConfigError(
            f"split size {count} is smaller than the {len(levels)} levels to cover; "
            f"every level must appear at least once in every split"
        )
    sequence = [levels[i % len(levels)] for i in range(count)]
    order = np.random.RandomState(seed).permutation(count)
    return [sequence[int(i)] for i in order]


def build_matrix(config: GeneratorConfig, master_seed: int) -> tuple[MatrixCell, ...]:
    """Return every matrix cell for a run, dev split first, then test.

    The returned order and content depend only on ``config.matrix``, ``config.split``
    and ``master_seed``.
    """
    split_counts = {
        DEV_SPLIT: config.split.dev_count,
        TEST_SPLIT: config.split.test_count,
    }
    axis_levels = config.matrix.axis_levels()
    cells: list[MatrixCell] = []
    for split in SPLITS:
        count = split_counts[split]
        columns = {
            axis: balanced_permutation(
                levels, count, derive_seed(master_seed, f"matrix:{axis}:{split}")
            )
            for axis, levels in axis_levels.items()
        }
        for i in range(count):
            cells.append(
                MatrixCell.from_levels(
                    config.matrix,
                    image_id=f"{split}_{i:02d}",
                    split=split,
                    index_in_split=i,
                    levels={axis: column[i] for axis, column in columns.items()},
                )
            )
    return tuple(cells)
