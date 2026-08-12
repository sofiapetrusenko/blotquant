"""Integrated intensity per ROI, after background correction.

The measurement is a plain sum of the background-corrected pixels inside the ROI, in
DN of the source bit depth. Two properties are deliberate and are recorded in
provenance rather than assumed:

* Negative corrected pixels are kept by default
  (``quantification.clamp_negative_pixels: false``). Noise is symmetric about the
  background, so keeping both signs makes the sum unbiased; clamping at zero would
  add roughly ``0.4 * sigma`` per pixel to every ROI, which for a large ROI is a
  larger error than the signal in a weak band.
* ``background_estimate`` is reported as the **mean** background level over the ROI,
  in DN. The raw ROI sum is recoverable from the result as
  ``integrated_intensity + background_estimate * width * height``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from pipeline.config import QuantificationConfig
from pipeline.detect import DetectedBand, Roi


@dataclass(frozen=True)
class BandMeasurement:
    """One band's background-corrected integrated intensity and its background level."""

    band_id: str
    lane_id: str
    roi: Roi
    integrated_intensity: float
    background_estimate: float

    def as_dict(self) -> dict[str, object]:
        """Return the ``bands[]`` entry of a result document."""
        return {
            "band_id": self.band_id,
            "lane_id": self.lane_id,
            "roi": self.roi.as_dict(),
            "integrated_intensity": self.integrated_intensity,
            "background_estimate": self.background_estimate,
        }


def _require_inside(roi: Roi, shape: tuple[int, ...]) -> None:
    """Raise :class:`ValueError` when ``roi`` is not fully inside an image of ``shape``."""
    height_px, width_px = shape
    if roi.x + roi.width > width_px or roi.y + roi.height > height_px:
        raise ValueError(
            f"ROI {roi.as_dict()} extends past the {width_px}x{height_px} image; "
            f"detection must clamp ROIs to the image before quantification"
        )


def integrate_roi(corrected: np.ndarray, roi: Roi, clamp_negative_pixels: bool) -> float:
    """Return the sum of background-corrected pixels inside ``roi``, in source DN.

    With ``clamp_negative_pixels`` false the sum is unbiased under symmetric noise;
    with it true, every pixel below the background contributes zero instead.
    """
    if corrected.ndim != 2:
        raise ValueError(f"quantification needs a 2D image, got shape {corrected.shape}")
    _require_inside(roi, corrected.shape)
    patch = corrected[roi.slices()[0], roi.slices()[1]]
    if clamp_negative_pixels:
        patch = np.clip(patch, 0.0, None)
    return float(patch.sum(dtype=np.float64))


def mean_over_roi(surface: np.ndarray, roi: Roi) -> float:
    """Return the mean of ``surface`` inside ``roi``."""
    if surface.ndim != 2:
        raise ValueError(f"background surface must be 2D, got shape {surface.shape}")
    _require_inside(roi, surface.shape)
    rows, columns = roi.slices()
    return float(surface[rows, columns].mean(dtype=np.float64))


def quantify_bands(
    corrected: np.ndarray,
    background: np.ndarray,
    bands: Sequence[DetectedBand],
    config: QuantificationConfig,
) -> tuple[BandMeasurement, ...]:
    """Measure every detected band, preserving the order detection produced."""
    if corrected.shape != background.shape:
        raise ValueError(
            f"corrected image {corrected.shape} and background {background.shape} must "
            f"have the same shape"
        )
    return tuple(
        BandMeasurement(
            band_id=band.band_id,
            lane_id=band.lane_id,
            roi=band.roi,
            integrated_intensity=integrate_roi(corrected, band.roi, config.clamp_negative_pixels),
            background_estimate=mean_over_roi(background, band.roi),
        )
        for band in bands
    )
