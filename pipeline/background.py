"""Background estimation and correction.

Two methods, selected by an explicit, required config key -- there is no default (see
:mod:`pipeline.config`). Both return a background *surface* in the units of the input
image, and correction is a plain subtraction of that surface.

Neither method is unbiased, and the biases run in opposite directions:

* ``rolling_ball`` is a grayscale opening, so it rides on the local *minimum* of the
  noise and estimates the background low, which makes quantification read high.
  ``presmooth_px`` reduces but does not remove that bias.
* ``local_median`` is contaminated upward by the band pixels inside its own window --
  a window whose pixels are a fraction ``f`` of band signal reports the
  ``0.5/(1-f)`` quantile of the background instead of its median -- so it estimates
  the background high, which makes quantification read low. A larger ``window_px``
  shrinks ``f``.

Both are measured on the dev split in NOTES.md rather than being asserted here.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import median_filter, uniform_filter
from skimage.restoration import rolling_ball

from pipeline.config import (
    LOCAL_MEDIAN,
    ROLLING_BALL,
    BackgroundConfig,
    LocalMedianParams,
    RollingBallParams,
)
from pipeline.errors import BackgroundError


@dataclass(frozen=True)
class BackgroundCorrection:
    """A background surface and the image with that surface removed."""

    background: np.ndarray
    corrected: np.ndarray


def _require_2d_float(pixels: np.ndarray) -> np.ndarray:
    """Return ``pixels`` as a 2D float64 array, raising :class:`BackgroundError` otherwise."""
    if pixels.ndim != 2:
        raise BackgroundError(
            f"background estimation needs a 2D grayscale image, got shape {pixels.shape}"
        )
    if pixels.size == 0:
        raise BackgroundError("background estimation needs a non-empty image")
    return pixels.astype(np.float64, copy=False)


def _require_footprint_fits(extent_px: int, shape: tuple[int, ...], what: str) -> None:
    """Raise :class:`BackgroundError` when a filter footprint is larger than the image."""
    shortest = min(shape)
    if extent_px > shortest:
        raise BackgroundError(
            f"{what} spans {extent_px} px but the image is only {shortest} px across its "
            f"shortest axis; the estimate would be a single constant. Reduce it or use a "
            f"larger image"
        )


def estimate_background(pixels: np.ndarray, config: BackgroundConfig) -> np.ndarray:
    """Return the background surface of ``pixels`` as float64, same shape as the input.

    At the borders the image is extended by edge replication. For a median with an odd
    window that is exact on a background ramp -- the replicated half fills exactly half
    the window, so the median still lands on the centre sample -- which is why no
    slope-preserving padding is applied here (NOTES.md records the measurement).
    """
    image = _require_2d_float(pixels)
    params = config.params
    if config.method == LOCAL_MEDIAN and isinstance(params, LocalMedianParams):
        window = params.window_px
        _require_footprint_fits(window, image.shape, f"local_median window ({window} px)")
        return np.asarray(median_filter(image, size=window, mode="nearest"), dtype=np.float64)
    if config.method == ROLLING_BALL and isinstance(params, RollingBallParams):
        diameter = int(2 * params.radius_px) + 1
        presmooth = params.presmooth_px
        _require_footprint_fits(diameter, image.shape, f"rolling ball (diameter {diameter} px)")
        _require_footprint_fits(presmooth, image.shape, f"presmooth window ({presmooth} px)")
        smoothed = (
            image if presmooth == 1 else uniform_filter(image, size=presmooth, mode="nearest")
        )
        return np.asarray(rolling_ball(smoothed, radius=params.radius_px), dtype=np.float64)
    raise BackgroundError(
        f"no background estimator is implemented for method {config.method!r} with "
        f"{type(params).__name__}; pipeline.config accepts only the methods it can run"
    )


def correct_background(pixels: np.ndarray, config: BackgroundConfig) -> BackgroundCorrection:
    """Return the background surface and ``pixels - background``, both float64.

    The difference is not clamped at zero here: clamping would turn symmetric noise
    into a positive offset and bias every integrated intensity upward. Whether the
    *quantifier* clamps is a separate, recorded parameter
    (``quantification.clamp_negative_pixels``).
    """
    image = _require_2d_float(pixels)
    background = estimate_background(image, config)
    return BackgroundCorrection(background=background, corrected=image - background)
