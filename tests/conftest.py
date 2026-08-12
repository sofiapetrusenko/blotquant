"""Fixtures shared by the pipeline tests.

The blot builder here is a *test* fixture, deliberately not the shape the synthetic
generator produces: bands are plain 2D Gaussians rather than flat-topped, so a
pipeline that had been fitted to the generator's profile would not pass these
assertions. Nothing under ``pipeline/`` may import it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np
import pytest


@dataclass(frozen=True)
class Band:
    """One synthetic band: a 2D Gaussian with known parameters."""

    center_x: float
    center_y: float
    amplitude: float
    sigma_x: float
    sigma_y: float

    def layer(self, shape: tuple[int, int]) -> np.ndarray:
        """Return this band's noise-free contribution over an image of ``shape``."""
        height, width = shape
        y = np.arange(height, dtype=np.float64)[:, None]
        x = np.arange(width, dtype=np.float64)[None, :]
        return self.amplitude * np.exp(
            -0.5 * ((x - self.center_x) / self.sigma_x) ** 2
            - 0.5 * ((y - self.center_y) / self.sigma_y) ** 2
        )


@dataclass(frozen=True)
class Blot:
    """A synthetic image together with the noise-free layers it was built from."""

    pixels: np.ndarray
    background: np.ndarray
    bands: tuple[Band, ...]
    band_layers: tuple[np.ndarray, ...] = field(repr=False)

    @property
    def signal(self) -> np.ndarray:
        """Return the sum of every band layer, without background or noise."""
        total = np.zeros_like(self.background)
        for layer in self.band_layers:
            total = total + layer
        return total


def build_blot(
    width: int,
    height: int,
    bands: Sequence[Band],
    background_dn: float,
    *,
    gradient_x_dn_per_px: float = 0.0,
    noise_sigma_dn: float = 0.0,
    dtype: type[np.unsignedinteger] = np.uint16,
    seed: int = 12345,
) -> Blot:
    """Return a quantized synthetic blot and the exact float layers behind it.

    Quantization is round-half-up then clip, matching how any real detector delivers
    integers; nothing is rescaled.
    """
    x = np.arange(width, dtype=np.float64)[None, :]
    background = np.full((height, width), float(background_dn)) + gradient_x_dn_per_px * x
    layers = tuple(band.layer((height, width)) for band in bands)
    total = background.copy()
    for layer in layers:
        total = total + layer
    if noise_sigma_dn > 0.0:
        total = total + np.random.RandomState(seed).normal(0.0, noise_sigma_dn, total.shape)
    maximum = np.iinfo(dtype).max
    pixels = np.clip(np.floor(total + 0.5), 0, maximum).astype(dtype)
    return Blot(pixels=pixels, background=background, bands=tuple(bands), band_layers=layers)


@pytest.fixture
def blot() -> Callable[..., Blot]:
    """Return the synthetic-blot builder."""
    return build_blot


@pytest.fixture
def three_lane_blot() -> Blot:
    """Return a noiseless three-lane, two-row blot with generously sized bands.

    Lane centres are 40/100/160 in a 200 px width, so the lane pitch is 60 px and the
    ~38 px wide bands sit clear of the lane boundaries. Sigmas are large enough that
    the pixel grid contributes little to the measured extents.
    """
    bands = [
        Band(center_x=cx, center_y=cy, amplitude=amplitude, sigma_x=8.0, sigma_y=4.0)
        for cx, amplitude in ((40.0, 4000.0), (100.0, 3000.0), (160.0, 2000.0))
        for cy in (30.0, 85.0)
    ]
    return build_blot(width=200, height=120, bands=bands, background_dn=1000.0)
