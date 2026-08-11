"""Generation parameters.

Every number the generator uses lives here, in a frozen dataclass, and is echoed
into each ground-truth file (either as a resolved per-image parameter block or via
the config digest). A literal in a rendering function body is a defect.

Level names used as mapping keys are the difficulty-matrix axis levels, which are
declared here too, in :class:`MatrixConfig`. Looking up an unknown level raises
:class:`ConfigError`; there are no fallback parameter sets.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, TypeVar

from synth.errors import ConfigError, UnsupportedBitDepthError

SUPPORTED_BIT_DEPTHS: tuple[int, ...] = (8, 16)
"""Bit depths the generator can render and encode. Anything else raises."""

_T = TypeVar("_T")


def max_value_for_bit_depth(bit_depth: int) -> int:
    """Return the saturation value (2**bit_depth - 1) for a supported bit depth.

    Raises :class:`UnsupportedBitDepthError` for anything outside
    :data:`SUPPORTED_BIT_DEPTHS`; the value is never squashed to 8-bit.
    """
    if bit_depth not in SUPPORTED_BIT_DEPTHS:
        raise UnsupportedBitDepthError(
            f"bit depth {bit_depth} is not supported; supported depths are "
            f"{list(SUPPORTED_BIT_DEPTHS)}"
        )
    return (1 << bit_depth) - 1


def require_level(levels: Mapping[str, _T], key: str, axis: str) -> _T:
    """Return ``levels[key]`` or raise :class:`ConfigError` naming the known levels."""
    try:
        return levels[key]
    except KeyError as exc:
        raise ConfigError(
            f"no parameters configured for {axis} level {key!r}; "
            f"configured levels are {sorted(levels)}"
        ) from exc


@dataclass(frozen=True)
class CanvasConfig:
    """Canvas geometry shared by every generated image."""

    width_px: int
    height_px: int
    margin_x_px: int
    row_y_fractions: tuple[float, ...]
    row_roles: tuple[str, ...]


@dataclass(frozen=True)
class BackgroundParams:
    """Additive background model parameters, as fractions of full scale.

    ``base_fraction`` is a constant offset; the gradient terms are linear ramps
    across the normalised canvas; the speckle terms add ``blob_count`` Gaussian
    blobs of random sign, position, width and amplitude.
    """

    base_fraction: float
    gradient_x_fraction: float
    gradient_y_fraction: float
    blob_count: int
    blob_sigma_px_min: float
    blob_sigma_px_max: float
    blob_amplitude_fraction_min: float
    blob_amplitude_fraction_max: float
    blob_bright_probability: float


@dataclass(frozen=True)
class NoiseParams:
    """Sensor noise model parameters.

    Shot noise is Poisson on photon counts, where ``photon_full_well`` is the photon
    count corresponding to full scale (so the gain in DN/photon is
    ``max_value / photon_full_well`` and the relative noise is bit-depth independent).
    Read noise is additive zero-mean Gaussian with sigma
    ``read_noise_fraction * max_value``.
    """

    shot_noise_enabled: bool
    photon_full_well: float
    read_noise_fraction: float


@dataclass(frozen=True)
class BandShapeParams:
    """Band cross-section parameters.

    The band profile is a flat-topped super-Gaussian across the lane and a plain
    Gaussian along the migration axis. ``doublet`` adds a second, partially
    overlapping band to the target row.

    ``aspect_ratio`` is ``sigma_x / sigma_y``, and it is the *primary* quantity: the
    migration-wise extent is derived from it as ``sigma_y = sigma_x / aspect_ratio``,
    so a level's shape is identical at every lane count even though ``sigma_x`` scales
    with lane pitch. A western blot band is constrained laterally by the lane it ran
    in, so it is always substantially wider than tall;
    :attr:`BandLayoutConfig.min_aspect_ratio` is the floor every level must clear, and
    :meth:`GeneratorConfig.__post_init__` enforces it.
    """

    aspect_ratio: float
    flat_top_exponent: float
    doublet: bool
    doublet_offset_sigma: float
    doublet_amplitude_ratio: float

    def __post_init__(self) -> None:
        """Reject a non-positive aspect ratio, which would give a non-positive sigma_y."""
        if self.aspect_ratio <= 0.0:
            raise ConfigError(
                f"band_shape.aspect_ratio must be positive, got {self.aspect_ratio}; "
                f"sigma_y is derived as sigma_x / aspect_ratio and would be undefined"
            )


@dataclass(frozen=True)
class LaneGeometryParams:
    """Lane/row geometry distortion.

    ``tilt_deg`` is a rigid rotation of the whole gel (lanes lean, rows lean by the
    same angle); ``smile_amplitude_px`` sags each band row quadratically toward the
    canvas edges.
    """

    tilt_deg: float
    smile_amplitude_px: float


@dataclass(frozen=True)
class DefectParams:
    """Membrane defect model parameters.

    Dust specks attenuate multiplicatively (they block light); scratches add a bright
    line. Both are placed uniformly at random inside the canvas.
    """

    dust_count: int
    dust_radius_px_min: float
    dust_radius_px_max: float
    dust_transmittance: float
    scratch_count: int
    scratch_sigma_px: float
    scratch_amplitude_fraction: float
    scratch_length_px_min: float
    scratch_length_px_max: float


@dataclass(frozen=True)
class ExposureParams:
    """Exposure level: the peak amplitude of the strongest target band."""

    reference_peak_fraction: float


@dataclass(frozen=True)
class BandLayoutConfig:
    """How band amplitudes and widths are laid out across lanes.

    ``min_aspect_ratio`` is the smallest ``sigma_x / sigma_y`` any ``band_shape`` level
    may declare. It lives here rather than on a shape level because it is a property of
    the electrophoresis, not of one band: the lane a sample ran in constrains how wide
    a band can get, and nothing constrains it to be equally tall, so a real western
    blot band is always substantially wider than tall. A near-circular blob is a
    dot-blot artifact and is out of scope (PLAN.md). Enforced across every level by
    :meth:`GeneratorConfig.__post_init__`.
    """

    sigma_x_fraction_of_pitch: float
    min_aspect_ratio: float
    bbox_relative_threshold: float
    target_relative_levels: tuple[float, ...]
    housekeeping_relative_level: float
    housekeeping_jitter_fraction: float

    def __post_init__(self) -> None:
        """Reject a floor below 1.0, which would permit taller-than-wide bands."""
        if self.min_aspect_ratio < 1.0:
            raise ConfigError(
                "band_layout.min_aspect_ratio must be at least 1.0, got "
                f"{self.min_aspect_ratio}; a floor below 1 would admit bands taller than "
                f"they are wide, which no lane-constrained western blot band is"
            )


@dataclass(frozen=True)
class LaneLayoutConfig:
    """How a lane's ground-truth ROI is built around its nominal centre.

    ``roi_width_fraction_of_pitch`` is the ROI width in lane pitches;
    ``roi_tilt_excursion_fraction_of_height`` is the fraction of canvas height over
    which a tilted lane centre is allowed to wander, so the ROI is widened on both
    sides by ``|tilt_slope| * fraction * height_px``. Both are layout decisions, not
    derived quantities, so they live here and are echoed per image.
    """

    roi_width_fraction_of_pitch: float
    roi_tilt_excursion_fraction_of_height: float

    def __post_init__(self) -> None:
        """Reject fractions that would produce an empty or inverted lane ROI."""
        if self.roi_width_fraction_of_pitch <= 0.0:
            raise ConfigError(
                "lane_layout.roi_width_fraction_of_pitch must be positive, got "
                f"{self.roi_width_fraction_of_pitch}; a non-positive width would make "
                f"every lane ROI empty or inverted"
            )
        if self.roi_tilt_excursion_fraction_of_height < 0.0:
            raise ConfigError(
                "lane_layout.roi_tilt_excursion_fraction_of_height must not be "
                f"negative, got {self.roi_tilt_excursion_fraction_of_height}; a "
                f"negative excursion would narrow the ROI as tilt grows"
            )


@dataclass(frozen=True)
class EncodingConfig:
    """Container-level encoding parameters."""

    jpeg_quality: int
    png_compression_level: int
    tiff_compression: str


@dataclass(frozen=True)
class QcThresholds:
    """Thresholds that turn rendered geometry into ground-truth QC labels."""

    saturated_min_clipped_pixels: int
    overlap_iou_threshold: float
    low_dynamic_range_peak_fraction: float


@dataclass(frozen=True)
class SplitConfig:
    """Split sizes. Assigned at generation time and recorded per image."""

    dev_count: int
    test_count: int


@dataclass(frozen=True)
class MatrixConfig:
    """The difficulty-matrix axes: which levels images are drawn from.

    These are generation parameters like any other, so they live in the config and are
    covered by :meth:`GeneratorConfig.digest`. Defining them as module constants would
    put them outside the digest, and editing one would change the dataset without the
    freeze guard noticing.

    ``format_depths`` maps a level name to its ``(container, bit depth)`` pair; only
    legal pairs are listed, which is how the impossible 16-bit JPEG stays out of the
    matrix rather than being repaired later.
    """

    format_depths: Mapping[str, tuple[str, int]]
    backgrounds: tuple[str, ...]
    noises: tuple[str, ...]
    band_shapes: tuple[str, ...]
    lane_geometries: tuple[str, ...]
    defects: tuple[str, ...]
    exposures: tuple[str, ...]
    lane_counts: tuple[int, ...]

    def axis_levels(self) -> dict[str, tuple[Any, ...]]:
        """Return every axis and its levels, keyed as the ground truth records them."""
        return {
            "format_depth": tuple(self.format_depths),
            "background": self.backgrounds,
            "noise": self.noises,
            "band_shape": self.band_shapes,
            "lane_geometry": self.lane_geometries,
            "defect": self.defects,
            "exposure": self.exposures,
            "lane_count": self.lane_counts,
        }

    def resolve_format_depth(self, level: str) -> tuple[str, int]:
        """Return the ``(container, bit depth)`` pair for a ``format_depth`` level."""
        container, bit_depth = require_level(self.format_depths, level, "format_depth")
        return container, bit_depth


PARAMETERISED_AXES: tuple[str, ...] = (
    "backgrounds",
    "noises",
    "band_shapes",
    "lane_geometries",
    "defects",
    "exposures",
)
"""Axes whose :class:`MatrixConfig` levels must each have a parameter entry.

The field name is the same on both objects: ``matrix.backgrounds`` lists the levels,
``backgrounds`` maps each of them to its parameters. The remaining two axes,
``format_depths`` and ``lane_counts``, carry their values inline and have no
parameter mapping.
"""


@dataclass(frozen=True)
class GeneratorConfig:
    """The complete parameter set for a generation run."""

    matrix: MatrixConfig
    canvas: CanvasConfig
    band_layout: BandLayoutConfig
    lane_layout: LaneLayoutConfig
    encoding: EncodingConfig
    qc: QcThresholds
    split: SplitConfig
    json_float_decimals: int
    backgrounds: Mapping[str, BackgroundParams]
    noises: Mapping[str, NoiseParams]
    band_shapes: Mapping[str, BandShapeParams]
    lane_geometries: Mapping[str, LaneGeometryParams]
    defects: Mapping[str, DefectParams]
    exposures: Mapping[str, ExposureParams]

    def __post_init__(self) -> None:
        """Validate cross-field invariants that would otherwise fail silently later."""
        canvas = self.canvas
        if len(canvas.row_y_fractions) != len(canvas.row_roles):
            raise ConfigError(
                "canvas.row_y_fractions and canvas.row_roles must have equal length; got "
                f"{len(canvas.row_y_fractions)} and {len(canvas.row_roles)}"
            )
        if canvas.margin_x_px * 2 >= canvas.width_px:
            raise ConfigError(
                f"canvas.margin_x_px={canvas.margin_x_px} leaves no room in a "
                f"{canvas.width_px}px-wide canvas"
            )
        if not self.band_layout.target_relative_levels:
            raise ConfigError("band_layout.target_relative_levels must not be empty")
        for axis in PARAMETERISED_AXES:
            declared: tuple[str, ...] = getattr(self.matrix, axis)
            configured: Mapping[str, Any] = getattr(self, axis)
            missing = [level for level in declared if level not in configured]
            if missing:
                raise ConfigError(
                    f"matrix.{axis} names levels with no parameters in {axis}: {missing}; "
                    f"configured levels are {sorted(configured)}. Add the parameters or "
                    f"drop the levels from the matrix; a mismatch would fail mid-render."
                )
        minimum = self.band_layout.min_aspect_ratio
        for level, shape in self.band_shapes.items():
            if shape.aspect_ratio < minimum:
                raise ConfigError(
                    f"band_shape level {level!r} has aspect_ratio "
                    f"{shape.aspect_ratio} (sigma_x / sigma_y), below "
                    f"band_layout.min_aspect_ratio {minimum}. Western blot bands are "
                    f"constrained laterally by their lane and are always substantially "
                    f"wider than tall; a near-circular band is a dot-blot artifact. "
                    f"Raise the level's aspect_ratio to at least {minimum}, or lower "
                    f"min_aspect_ratio deliberately."
                )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable deep copy of the whole config."""
        return asdict(self)

    def digest(self) -> str:
        """Return a stable ``sha256:...`` digest of the config, recorded per image.

        Serialised in declaration/insertion order rather than sorted, because order is
        itself a generation parameter: :meth:`MatrixConfig.axis_levels` hands the level
        order to the balanced round-robin schedule, so reordering an axis changes which
        image gets which level. A sorted payload would hide that edit from the freeze
        guard. Both orders are fixed properties of the source, so the digest is stable.
        """
        payload = json.dumps(self.to_dict(), separators=(",", ":"))
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


DEFAULT_CONFIG = GeneratorConfig(
    matrix=MatrixConfig(
        format_depths={
            "tiff8": ("tiff", 8),
            "tiff16": ("tiff", 16),
            "png8": ("png", 8),
            "png16": ("png", 16),
            "jpeg8": ("jpeg", 8),
        },
        backgrounds=("flat", "gradient", "speckle"),
        noises=("low", "high"),
        band_shapes=("sharp", "smeared", "doublet"),
        lane_geometries=("straight", "tilted", "smile"),
        defects=("none", "dust", "scratch"),
        exposures=("normal", "low", "saturating"),
        lane_counts=(4, 5, 6),
    ),
    canvas=CanvasConfig(
        width_px=256,
        height_px=192,
        margin_x_px=14,
        row_y_fractions=(0.32, 0.70),
        row_roles=("target", "housekeeping"),
    ),
    band_layout=BandLayoutConfig(
        sigma_x_fraction_of_pitch=0.24,
        min_aspect_ratio=2.5,
        bbox_relative_threshold=0.05,
        target_relative_levels=(1.0, 0.72, 0.48, 0.30, 0.62, 0.18),
        housekeeping_relative_level=0.55,
        housekeeping_jitter_fraction=0.06,
    ),
    lane_layout=LaneLayoutConfig(
        roi_width_fraction_of_pitch=1.0,
        roi_tilt_excursion_fraction_of_height=0.5,
    ),
    encoding=EncodingConfig(
        jpeg_quality=75,
        png_compression_level=6,
        tiff_compression="zlib",
    ),
    qc=QcThresholds(
        saturated_min_clipped_pixels=3,
        overlap_iou_threshold=0.15,
        low_dynamic_range_peak_fraction=0.20,
    ),
    split=SplitConfig(dev_count=30, test_count=10),
    json_float_decimals=6,
    backgrounds={
        "flat": BackgroundParams(
            base_fraction=0.08,
            gradient_x_fraction=0.0,
            gradient_y_fraction=0.0,
            blob_count=0,
            blob_sigma_px_min=0.0,
            blob_sigma_px_max=0.0,
            blob_amplitude_fraction_min=0.0,
            blob_amplitude_fraction_max=0.0,
            blob_bright_probability=0.0,
        ),
        "gradient": BackgroundParams(
            base_fraction=0.06,
            gradient_x_fraction=0.10,
            gradient_y_fraction=0.04,
            blob_count=0,
            blob_sigma_px_min=0.0,
            blob_sigma_px_max=0.0,
            blob_amplitude_fraction_min=0.0,
            blob_amplitude_fraction_max=0.0,
            blob_bright_probability=0.0,
        ),
        "speckle": BackgroundParams(
            base_fraction=0.09,
            gradient_x_fraction=0.0,
            gradient_y_fraction=0.0,
            blob_count=14,
            blob_sigma_px_min=4.0,
            blob_sigma_px_max=18.0,
            blob_amplitude_fraction_min=0.010,
            blob_amplitude_fraction_max=0.045,
            blob_bright_probability=0.6,
        ),
    },
    noises={
        "low": NoiseParams(
            shot_noise_enabled=True,
            photon_full_well=4096.0,
            read_noise_fraction=0.004,
        ),
        "high": NoiseParams(
            shot_noise_enabled=True,
            photon_full_well=512.0,
            read_noise_fraction=0.012,
        ),
    },
    band_shapes={
        "sharp": BandShapeParams(
            aspect_ratio=5.0,
            flat_top_exponent=4.0,
            doublet=False,
            doublet_offset_sigma=0.0,
            doublet_amplitude_ratio=0.0,
        ),
        "smeared": BandShapeParams(
            aspect_ratio=2.6,
            flat_top_exponent=4.0,
            doublet=False,
            doublet_offset_sigma=0.0,
            doublet_amplitude_ratio=0.0,
        ),
        "doublet": BandShapeParams(
            aspect_ratio=4.0,
            flat_top_exponent=4.0,
            doublet=True,
            doublet_offset_sigma=2.2,
            doublet_amplitude_ratio=0.65,
        ),
    },
    lane_geometries={
        "straight": LaneGeometryParams(tilt_deg=0.0, smile_amplitude_px=0.0),
        "tilted": LaneGeometryParams(tilt_deg=4.0, smile_amplitude_px=0.0),
        "smile": LaneGeometryParams(tilt_deg=0.0, smile_amplitude_px=9.0),
    },
    defects={
        "none": DefectParams(
            dust_count=0,
            dust_radius_px_min=0.0,
            dust_radius_px_max=0.0,
            dust_transmittance=1.0,
            scratch_count=0,
            scratch_sigma_px=0.0,
            scratch_amplitude_fraction=0.0,
            scratch_length_px_min=0.0,
            scratch_length_px_max=0.0,
        ),
        "dust": DefectParams(
            dust_count=6,
            dust_radius_px_min=1.5,
            dust_radius_px_max=3.5,
            dust_transmittance=0.35,
            scratch_count=0,
            scratch_sigma_px=0.0,
            scratch_amplitude_fraction=0.0,
            scratch_length_px_min=0.0,
            scratch_length_px_max=0.0,
        ),
        "scratch": DefectParams(
            dust_count=0,
            dust_radius_px_min=0.0,
            dust_radius_px_max=0.0,
            dust_transmittance=1.0,
            scratch_count=1,
            scratch_sigma_px=1.2,
            scratch_amplitude_fraction=0.25,
            scratch_length_px_min=60.0,
            scratch_length_px_max=140.0,
        ),
    },
    exposures={
        "normal": ExposureParams(reference_peak_fraction=0.55),
        "low": ExposureParams(reference_peak_fraction=0.12),
        "saturating": ExposureParams(reference_peak_fraction=1.35),
    },
)
"""The parameter set used for the committed gold set (``--seed 42``)."""
