"""Pixel-level rendering primitives.

Every function here is pure: same inputs (including the RandomState) give the same
float64 array. All models are documented in ``synth/MODELS.md``; the formulas below
and that document must agree.

Canvas convention: ``image[y, x]``, x increasing to the right, y increasing
downward (the migration direction). Intensities are in DN of the target bit depth,
signal-positive (bright bands on a dark background, i.e. chemiluminescence).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from synth.config import (
    BackgroundParams,
    DefectParams,
    NoiseParams,
    max_value_for_bit_depth,
)
from synth.errors import ConfigError, RenderError


@dataclass(frozen=True)
class PixelBox:
    """An integer, half-open-free ROI: pixel columns ``x .. x+width-1``."""

    x: int
    y: int
    width: int
    height: int

    def as_dict(self) -> dict[str, int]:
        """Return the box as a JSON-serialisable dict."""
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass(frozen=True)
class GelGeometry:
    """Resolved geometric distortion of one gel image.

    ``tilt_slope`` is ``tan(tilt_deg)`` applied as a rigid rotation: lane centres
    shift in x with y, band rows shift in y with x by the same slope, opposite sign.
    ``smile_amplitude_px`` sags band rows quadratically toward the canvas edges.
    """

    tilt_slope: float
    smile_amplitude_px: float
    center_x: float
    center_y: float
    half_width_px: float

    def lane_x_at(self, y: np.ndarray | float) -> np.ndarray | float:
        """Return the x offset (relative to a lane's nominal x) at row ``y``."""
        return self.tilt_slope * (y - self.center_y)

    def row_y_at(self, x: np.ndarray | float) -> np.ndarray | float:
        """Return the y offset (relative to a row's nominal y) at column ``x``."""
        normalised = (x - self.center_x) / self.half_width_px
        return -self.tilt_slope * (x - self.center_x) + self.smile_amplitude_px * normalised**2


def coordinate_grids(width_px: int, height_px: int) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(xs, ys)`` float64 pixel-centre coordinate grids of shape (h, w)."""
    if width_px <= 0 or height_px <= 0:
        raise ConfigError(f"canvas must be positive, got {width_px}x{height_px}")
    ys, xs = np.meshgrid(
        np.arange(height_px, dtype=np.float64),
        np.arange(width_px, dtype=np.float64),
        indexing="ij",
    )
    return xs, ys


def render_background(
    xs: np.ndarray,
    ys: np.ndarray,
    params: BackgroundParams,
    max_value: int,
    rng: np.random.RandomState,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Return the background layer in DN plus a record of every speckle blob.

    Guarantees a strictly non-negative layer, or raises :class:`RenderError`: a
    negative background would make the Poisson stage undefined.
    """
    height, width = xs.shape
    layer = np.full(
        xs.shape,
        params.base_fraction * max_value,
        dtype=np.float64,
    )
    layer += params.gradient_x_fraction * max_value * (xs / max(width - 1, 1))
    layer += params.gradient_y_fraction * max_value * (ys / max(height - 1, 1))

    blobs: list[dict[str, Any]] = []
    for _ in range(params.blob_count):
        cx = rng.uniform(0.0, float(width - 1))
        cy = rng.uniform(0.0, float(height - 1))
        sigma = rng.uniform(params.blob_sigma_px_min, params.blob_sigma_px_max)
        amplitude_fraction = rng.uniform(
            params.blob_amplitude_fraction_min, params.blob_amplitude_fraction_max
        )
        sign = 1.0 if rng.uniform(0.0, 1.0) < params.blob_bright_probability else -1.0
        amplitude = sign * amplitude_fraction * max_value
        layer += amplitude * np.exp(-0.5 * (((xs - cx) ** 2 + (ys - cy) ** 2) / sigma**2))
        blobs.append(
            {
                "center_x": float(cx),
                "center_y": float(cy),
                "sigma_px": float(sigma),
                "amplitude_dn": float(amplitude),
            }
        )

    if float(layer.min()) < 0.0:
        raise RenderError(
            f"background model produced negative DN (min={float(layer.min()):.3f}); "
            f"raise base_fraction or lower the blob amplitudes in the config"
        )
    return layer, blobs


def render_band(
    xs: np.ndarray,
    ys: np.ndarray,
    *,
    amplitude_dn: float,
    nominal_x: float,
    nominal_y: float,
    sigma_x_px: float,
    sigma_y_px: float,
    flat_top_exponent: float,
    geometry: GelGeometry,
) -> np.ndarray:
    """Return one band's noise-free signal layer in DN.

    The profile is ``A * exp(-0.5*|u|^p) * exp(-0.5*v^2)`` with ``u`` the lane-wise
    offset in units of ``sigma_x_px`` and ``v`` the migration-wise offset in units of
    ``sigma_y_px``, both measured from the geometry-distorted band centre.
    """
    if sigma_x_px <= 0 or sigma_y_px <= 0:
        raise ConfigError(
            f"band sigmas must be positive, got sigma_x={sigma_x_px}, sigma_y={sigma_y_px}"
        )
    if flat_top_exponent < 2.0:
        raise ConfigError(
            f"flat_top_exponent must be >= 2 (2 = plain Gaussian), got {flat_top_exponent}"
        )
    u = (xs - (nominal_x + geometry.lane_x_at(ys))) / sigma_x_px
    v = (ys - (nominal_y + geometry.row_y_at(xs))) / sigma_y_px
    return amplitude_dn * np.exp(-0.5 * np.abs(u) ** flat_top_exponent) * np.exp(-0.5 * v**2)


def band_bbox(layer: np.ndarray, amplitude_dn: float, relative_threshold: float) -> PixelBox:
    """Return the tight integer ROI where the band layer exceeds a relative threshold.

    The box is derived from the rendered layer, so it follows tilt and smile
    distortion automatically. Raises :class:`RenderError` if nothing clears the
    threshold, which would mean an invisible band.
    """
    if not 0.0 < relative_threshold < 1.0:
        raise ConfigError(
            f"bbox_relative_threshold must be in (0, 1), got {relative_threshold}"
        )
    mask = layer >= relative_threshold * amplitude_dn
    if not bool(mask.any()):
        raise RenderError(
            f"band with amplitude {amplitude_dn:.3f} DN has no pixel above "
            f"{relative_threshold:.3f} of its peak; the ROI is undefined"
        )
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    return PixelBox(
        x=int(cols[0]),
        y=int(rows[0]),
        width=int(cols[-1] - cols[0] + 1),
        height=int(rows[-1] - rows[0] + 1),
    )


def render_dust(
    xs: np.ndarray,
    ys: np.ndarray,
    params: DefectParams,
    rng: np.random.RandomState,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Return a multiplicative transmittance mask in (0, 1] plus the speck records.

    Dust blocks light, so it attenuates background and bands alike.
    """
    if not 0.0 < params.dust_transmittance <= 1.0:
        raise ConfigError(
            f"dust_transmittance must be in (0, 1], got {params.dust_transmittance}"
        )
    height, width = xs.shape
    mask = np.ones(xs.shape, dtype=np.float64)
    specks: list[dict[str, Any]] = []
    for _ in range(params.dust_count):
        cx = rng.uniform(0.0, float(width - 1))
        cy = rng.uniform(0.0, float(height - 1))
        radius = rng.uniform(params.dust_radius_px_min, params.dust_radius_px_max)
        profile = np.exp(-0.5 * (((xs - cx) ** 2 + (ys - cy) ** 2) / radius**2))
        mask *= 1.0 - (1.0 - params.dust_transmittance) * profile
        specks.append(
            {
                "kind": "dust",
                "center_x": float(cx),
                "center_y": float(cy),
                "radius_px": float(radius),
                "transmittance": float(params.dust_transmittance),
            }
        )
    return mask, specks


def render_scratches(
    xs: np.ndarray,
    ys: np.ndarray,
    params: DefectParams,
    max_value: int,
    rng: np.random.RandomState,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Return an additive scratch layer in DN plus the scratch records.

    A scratch is a bright line segment with a Gaussian cross-section. It is a
    contaminant: it is never counted as band signal in the ground truth.
    """
    height, width = xs.shape
    layer = np.zeros(xs.shape, dtype=np.float64)
    scratches: list[dict[str, Any]] = []
    for _ in range(params.scratch_count):
        if params.scratch_sigma_px <= 0:
            raise ConfigError(
                f"scratch_sigma_px must be positive when scratch_count > 0, "
                f"got {params.scratch_sigma_px}"
            )
        cx = rng.uniform(0.0, float(width - 1))
        cy = rng.uniform(0.0, float(height - 1))
        angle = rng.uniform(0.0, float(np.pi))
        length = rng.uniform(params.scratch_length_px_min, params.scratch_length_px_max)
        half_dx = 0.5 * length * float(np.cos(angle))
        half_dy = 0.5 * length * float(np.sin(angle))
        x0, y0 = cx - half_dx, cy - half_dy
        x1, y1 = cx + half_dx, cy + half_dy
        dx, dy = x1 - x0, y1 - y0
        squared_length = dx * dx + dy * dy
        t = np.clip(((xs - x0) * dx + (ys - y0) * dy) / squared_length, 0.0, 1.0)
        distance = np.hypot(xs - (x0 + t * dx), ys - (y0 + t * dy))
        amplitude = params.scratch_amplitude_fraction * max_value
        layer += amplitude * np.exp(-0.5 * (distance / params.scratch_sigma_px) ** 2)
        scratches.append(
            {
                "kind": "scratch",
                "x0": float(x0),
                "y0": float(y0),
                "x1": float(x1),
                "y1": float(y1),
                "sigma_px": float(params.scratch_sigma_px),
                "amplitude_dn": float(amplitude),
            }
        )
    return layer, scratches


def apply_noise(
    image: np.ndarray,
    params: NoiseParams,
    max_value: int,
    rng: np.random.RandomState,
) -> np.ndarray:
    """Return the image with Poisson shot noise and Gaussian read noise applied.

    Shot noise is applied first on photon counts (gain = ``max_value /
    photon_full_well`` DN per photon), read noise second as an additive Gaussian.
    Raises :class:`RenderError` on negative input, where the Poisson stage is undefined.
    """
    if float(image.min()) < 0.0:
        raise RenderError(
            f"cannot apply Poisson noise to negative DN (min={float(image.min()):.3f})"
        )
    if params.shot_noise_enabled and params.photon_full_well <= 0.0:
        raise ConfigError(
            f"photon_full_well must be positive when shot noise is enabled, "
            f"got {params.photon_full_well}"
        )
    if params.read_noise_fraction < 0.0:
        raise ConfigError(
            f"read_noise_fraction must be non-negative, got {params.read_noise_fraction}"
        )
    noisy = image
    if params.shot_noise_enabled:
        gain = max_value / params.photon_full_well
        noisy = rng.poisson(noisy / gain).astype(np.float64) * gain
    return noisy + rng.normal(0.0, params.read_noise_fraction * max_value, size=image.shape)


def quantize(image: np.ndarray, bit_depth: int) -> np.ndarray:
    """Return the image rounded and clipped into the unsigned integer type of ``bit_depth``.

    Rounding is ``floor(x + 0.5)``. Values above full scale are clipped (that is how
    saturation is produced); values below zero are clipped to zero. Unsupported bit
    depths raise :class:`UnsupportedBitDepthError` rather than being squashed.
    """
    max_value = max_value_for_bit_depth(bit_depth)
    dtype = np.uint8 if bit_depth == 8 else np.uint16
    rounded = np.floor(image + 0.5)
    return np.clip(rounded, 0.0, float(max_value)).astype(dtype)


def clipped_pixel_count(quantized: np.ndarray, bit_depth: int, box: PixelBox | None = None) -> int:
    """Return how many pixels sit at full scale, over the whole image or inside ``box``.

    This is deliberately the *detectable* definition of saturation: a pixel at full
    scale is indistinguishable from one that was clipped, so ground truth labels what
    a pipeline could in principle observe.
    """
    max_value = max_value_for_bit_depth(bit_depth)
    region = quantized
    if box is not None:
        region = quantized[box.y : box.y + box.height, box.x : box.x + box.width]
    return int(np.count_nonzero(region >= max_value))
