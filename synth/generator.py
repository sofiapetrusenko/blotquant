"""Deterministic synthetic blot generator.

:func:`generate_image` is a pure function of ``(matrix cell, config, master seed)``:
it performs no I/O and reads no clock, environment variable or filesystem state.
:func:`generate_dataset` is the only code in this repository that writes
``data/ground_truth/``.

Ground-truth intensity contract
-------------------------------
``true_integrated_intensity_dn`` is the sum, inside the band's ground-truth ROI, of
that band's own noise-free signal layer after dust attenuation and *before*
quantization clipping. It is therefore already background-free, it excludes the
additive scratch contaminant, and for a saturated band it is the intensity the band
*would* have produced with unlimited headroom -- which is precisely why a saturated
band is unrecoverable and must be QC-flagged rather than trusted.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from synth import GROUND_TRUTH_SCHEMA_VERSION, SYNTH_VERSION
from synth.config import (
    DEFAULT_CONFIG,
    GeneratorConfig,
    max_value_for_bit_depth,
    require_level,
)
from synth.encoding import LOSSY_FORMATS, extension_for, pixel_digest, write_image
from synth.errors import ConfigError, GoldSetMismatchError, RenderError
from synth.matrix import MatrixCell, build_matrix
from synth.render import (
    GelGeometry,
    PixelBox,
    apply_noise,
    band_bbox,
    clipped_pixel_count,
    coordinate_grids,
    quantize,
    render_background,
    render_band,
    render_dust,
    render_scratches,
)
from synth.rng import derive_seed, make_rng

GROUND_TRUTH_DIRNAME = "ground_truth"
IMAGES_DIRNAME = "images"
CONFIG_FILENAME = "generation_config.json"

RNG_STREAMS: tuple[str, ...] = ("background", "bands", "defects", "noise")
"""Independent random streams per image; adding draws to one cannot shift another."""

TARGET_ROLE = "target"
SECONDARY_TARGET_ROLE = "target_secondary"
REFERENCE_ROLE = "housekeeping"

FLAG_SATURATED = "saturated"
FLAG_OVERLAPPING = "overlapping"
FLAG_LOSSY_FORMAT = "lossy_format"
FLAG_LOW_DYNAMIC_RANGE = "low_dynamic_range"

BAND_QC_FLAGS: tuple[str, ...] = (FLAG_SATURATED, FLAG_OVERLAPPING)
IMAGE_QC_FLAGS: tuple[str, ...] = (FLAG_SATURATED, FLAG_LOSSY_FORMAT, FLAG_LOW_DYNAMIC_RANGE)


@dataclass(frozen=True)
class BandPlan:
    """A band's resolved parameters, before rendering."""

    band_id: str
    lane_id: str
    lane_index: int
    row_index: int
    role: str
    amplitude_dn: float
    nominal_x: float
    nominal_y: float
    sigma_x_px: float
    sigma_y_px: float


@dataclass(frozen=True)
class GeneratedImage:
    """One rendered image plus the ground truth that describes it."""

    cell: MatrixCell
    pixels: np.ndarray
    ground_truth: dict[str, Any]


@dataclass(frozen=True)
class WrittenImage:
    """Where one generated image landed on disk."""

    image_id: str
    split: str
    image_path: Path
    ground_truth_path: Path


@dataclass(frozen=True)
class DatasetSummary:
    """What one generation run wrote, and what it deleted to make room."""

    images: tuple[WrittenImage, ...]
    removed_files: tuple[str, ...]


def box_iou(a: PixelBox, b: PixelBox) -> float:
    """Return the intersection-over-union of two integer pixel boxes."""
    overlap_x = max(0, min(a.x + a.width, b.x + b.width) - max(a.x, b.x))
    overlap_y = max(0, min(a.y + a.height, b.y + b.height) - max(a.y, b.y))
    intersection = overlap_x * overlap_y
    union = a.width * a.height + b.width * b.height - intersection
    if union <= 0:
        raise ValueError(f"degenerate boxes have no union: {a} and {b}")
    return intersection / union


def round_floats(value: Any, decimals: int) -> Any:
    """Return ``value`` with every float rounded, recursively.

    Rounding keeps serialised ground truth stable against last-bit differences in
    transcendental functions between CPU architectures.
    """
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, decimals)
    if isinstance(value, dict):
        return {key: round_floats(item, decimals) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [round_floats(item, decimals) for item in value]
    return value


def _lane_centers(cell: MatrixCell, config: GeneratorConfig) -> tuple[list[float], float]:
    """Return the nominal lane centre x positions and the lane pitch in pixels."""
    canvas = config.canvas
    pitch = (canvas.width_px - 2 * canvas.margin_x_px) / cell.lane_count
    centers = [canvas.margin_x_px + (index + 0.5) * pitch for index in range(cell.lane_count)]
    return centers, pitch


def plan_bands(
    cell: MatrixCell,
    config: GeneratorConfig,
    rng: np.random.RandomState,
) -> tuple[list[BandPlan], dict[str, Any]]:
    """Return the band plan for one image plus the derived layout quantities.

    Target-row amplitudes follow ``band_layout.target_relative_levels`` (a fixed
    biological pattern across lanes); the reference row is near-constant with a
    bounded jitter drawn from ``rng``. When the band-shape level is a doublet, the
    target row -- and only the target row -- gains a partially overlapping partner.

    Both band sigmas are tied to the lane pitch: ``sigma_x`` is a configured fraction
    of it, and ``sigma_y`` is ``sigma_x / aspect_ratio``. Every band in the returned
    plan therefore satisfies ``sigma_x / sigma_y == shape.aspect_ratio`` exactly, at
    every lane count -- which is what makes
    ``band_layout.min_aspect_ratio`` a guarantee about the pixels rather than about
    one lane count.
    """
    canvas = config.canvas
    layout = config.band_layout
    shape = require_level(config.band_shapes, cell.band_shape, "band_shape")
    exposure = require_level(config.exposures, cell.exposure, "exposure")
    max_value = max_value_for_bit_depth(cell.bit_depth)

    centers, pitch = _lane_centers(cell, config)
    sigma_x = layout.sigma_x_fraction_of_pitch * pitch
    sigma_y = sigma_x / shape.aspect_ratio
    reference_peak_dn = exposure.reference_peak_fraction * max_value
    row_y = [fraction * canvas.height_px for fraction in canvas.row_y_fractions]

    if TARGET_ROLE not in canvas.row_roles or REFERENCE_ROLE not in canvas.row_roles:
        raise ConfigError(
            f"canvas.row_roles must contain {TARGET_ROLE!r} and {REFERENCE_ROLE!r}, "
            f"got {list(canvas.row_roles)}"
        )

    plans: list[BandPlan] = []
    for lane_index, center_x in enumerate(centers):
        lane_id = f"{cell.image_id}_L{lane_index}"
        for row_index, role in enumerate(canvas.row_roles):
            if role == TARGET_ROLE:
                level = layout.target_relative_levels[
                    lane_index % len(layout.target_relative_levels)
                ]
            else:
                jitter = rng.uniform(-1.0, 1.0) * layout.housekeeping_jitter_fraction
                level = layout.housekeeping_relative_level * (1.0 + jitter)
            amplitude = reference_peak_dn * level
            plans.append(
                BandPlan(
                    band_id=f"{lane_id}_{role}",
                    lane_id=lane_id,
                    lane_index=lane_index,
                    row_index=row_index,
                    role=role,
                    amplitude_dn=amplitude,
                    nominal_x=center_x,
                    nominal_y=row_y[row_index],
                    sigma_x_px=sigma_x,
                    sigma_y_px=sigma_y,
                )
            )
            if role == TARGET_ROLE and shape.doublet:
                plans.append(
                    BandPlan(
                        band_id=f"{lane_id}_{SECONDARY_TARGET_ROLE}",
                        lane_id=lane_id,
                        lane_index=lane_index,
                        row_index=row_index,
                        role=SECONDARY_TARGET_ROLE,
                        amplitude_dn=amplitude * shape.doublet_amplitude_ratio,
                        nominal_x=center_x,
                        nominal_y=row_y[row_index] + shape.doublet_offset_sigma * sigma_y,
                        sigma_x_px=sigma_x,
                        sigma_y_px=sigma_y,
                    )
                )

    derived = {
        "lane_pitch_px": float(pitch),
        "lane_center_x_px": [float(value) for value in centers],
        "band_sigma_x_px": float(sigma_x),
        "band_sigma_y_px": float(sigma_y),
        "row_center_y_px": [float(value) for value in row_y],
        "reference_peak_dn": float(reference_peak_dn),
        "max_value": int(max_value),
    }
    return plans, derived


def _lane_roi(
    center_x: float,
    pitch: float,
    geometry: GelGeometry,
    config: GeneratorConfig,
) -> PixelBox:
    """Return the full-height ROI of one lane, widened to cover its tilt excursion.

    Width and tilt allowance come from ``config.lane_layout``; the box spans the whole
    canvas height and is clamped to the canvas. Documented in ``synth/MODELS.md`` §4.
    """
    canvas = config.canvas
    lane_layout = config.lane_layout
    half_width = lane_layout.roi_width_fraction_of_pitch * pitch / 2.0
    excursion = abs(geometry.tilt_slope) * (
        lane_layout.roi_tilt_excursion_fraction_of_height * canvas.height_px
    )
    left = max(0, int(math.floor(center_x - half_width - excursion)))
    right = min(canvas.width_px - 1, int(math.ceil(center_x + half_width + excursion)))
    return PixelBox(x=left, y=0, width=right - left + 1, height=canvas.height_px)


def generate_image(
    cell: MatrixCell,
    config: GeneratorConfig,
    master_seed: int,
) -> GeneratedImage:
    """Render one matrix cell and return its pixels and ground truth.

    Deterministic: identical ``(cell, config, master_seed)`` always yield an identical
    pixel array and an identical ground-truth dictionary.
    """
    canvas = config.canvas
    max_value = max_value_for_bit_depth(cell.bit_depth)
    background_params = require_level(config.backgrounds, cell.background, "background")
    noise_params = require_level(config.noises, cell.noise, "noise")
    shape_params = require_level(config.band_shapes, cell.band_shape, "band_shape")
    geometry_params = require_level(config.lane_geometries, cell.lane_geometry, "lane_geometry")
    defect_params = require_level(config.defects, cell.defect, "defect")
    exposure_params = require_level(config.exposures, cell.exposure, "exposure")

    streams = {name: make_rng(master_seed, f"image:{cell.image_id}:{name}") for name in RNG_STREAMS}
    seeds = {
        name: derive_seed(master_seed, f"image:{cell.image_id}:{name}") for name in RNG_STREAMS
    }

    xs, ys = coordinate_grids(canvas.width_px, canvas.height_px)
    geometry = GelGeometry(
        tilt_slope=math.tan(math.radians(geometry_params.tilt_deg)),
        smile_amplitude_px=geometry_params.smile_amplitude_px,
        center_x=(canvas.width_px - 1) / 2.0,
        center_y=(canvas.height_px - 1) / 2.0,
        half_width_px=(canvas.width_px - 1) / 2.0,
    )

    background, blobs = render_background(
        xs, ys, background_params, max_value, streams["background"]
    )
    plans, derived = plan_bands(cell, config, streams["bands"])
    dust_mask, dust_records = render_dust(xs, ys, defect_params, streams["defects"])
    scratch_layer, scratch_records = render_scratches(
        xs, ys, defect_params, max_value, streams["defects"]
    )

    band_layers: list[np.ndarray] = []
    band_boxes: list[PixelBox] = []
    for plan in plans:
        layer = render_band(
            xs,
            ys,
            amplitude_dn=plan.amplitude_dn,
            nominal_x=plan.nominal_x,
            nominal_y=plan.nominal_y,
            sigma_x_px=plan.sigma_x_px,
            sigma_y_px=plan.sigma_y_px,
            flat_top_exponent=shape_params.flat_top_exponent,
            geometry=geometry,
        )
        band_layers.append(layer)
        band_boxes.append(
            band_bbox(layer, plan.amplitude_dn, config.band_layout.bbox_relative_threshold)
        )

    signal = background + sum(band_layers)
    composite = signal * dust_mask + scratch_layer
    noisy = apply_noise(composite, noise_params, max_value, streams["noise"])
    pixels = quantize(noisy, cell.bit_depth)

    bands: list[dict[str, Any]] = []
    for index, (plan, layer, box) in enumerate(zip(plans, band_layers, band_boxes, strict=True)):
        attenuated = layer * dust_mask
        window = attenuated[box.y : box.y + box.height, box.x : box.x + box.width]
        integrated = float(window.sum())
        total = float(attenuated.sum())
        if total <= 0.0:
            raise RenderError(
                f"band {plan.band_id} carries no signal (total {total} DN); its ROI mass "
                f"fraction and its recovery error would both be undefined"
            )
        clipped = clipped_pixel_count(pixels, cell.bit_depth, box)
        overlapping = [
            other.band_id
            for other_index, other in enumerate(plans)
            if other_index != index
            and box_iou(box, band_boxes[other_index]) >= config.qc.overlap_iou_threshold
        ]
        qc_flags: list[str] = []
        if clipped >= config.qc.saturated_min_clipped_pixels:
            qc_flags.append(FLAG_SATURATED)
        if overlapping:
            qc_flags.append(FLAG_OVERLAPPING)
        region = pixels[box.y : box.y + box.height, box.x : box.x + box.width]
        bands.append(
            {
                "band_id": plan.band_id,
                "lane_id": plan.lane_id,
                "lane_index": plan.lane_index,
                "row_index": plan.row_index,
                "role": plan.role,
                "nominal_center_x_px": plan.nominal_x,
                "nominal_center_y_px": plan.nominal_y,
                "sigma_x_px": plan.sigma_x_px,
                "sigma_y_px": plan.sigma_y_px,
                "amplitude_dn": plan.amplitude_dn,
                "roi": box.as_dict(),
                "true_integrated_intensity_dn": integrated,
                "true_total_intensity_dn": total,
                "roi_mass_fraction": integrated / total,
                "observed_peak_dn": int(region.max()),
                "clipped_pixel_count": clipped,
                "overlapping_band_ids": sorted(overlapping),
                "qc_flags": qc_flags,
            }
        )

    centers = derived["lane_center_x_px"]
    pitch = derived["lane_pitch_px"]
    lanes = [
        {
            "lane_id": f"{cell.image_id}_L{lane_index}",
            "lane_index": lane_index,
            "nominal_center_x_px": float(center_x),
            "roi": _lane_roi(center_x, pitch, geometry, config).as_dict(),
        }
        for lane_index, center_x in enumerate(centers)
    ]

    by_id = {band["band_id"]: band for band in bands}
    ratios = []
    for lane in lanes:
        numerator = f"{lane['lane_id']}_{TARGET_ROLE}"
        denominator = f"{lane['lane_id']}_{REFERENCE_ROLE}"
        reference_value = by_id[denominator]["true_integrated_intensity_dn"]
        if reference_value <= 0.0:
            raise ConfigError(
                f"reference band {denominator} has non-positive true intensity "
                f"({reference_value}); the normalization ratio would be undefined"
            )
        ratios.append(
            {
                "lane_id": lane["lane_id"],
                "numerator_band_id": numerator,
                "denominator_band_id": denominator,
                "true_ratio": by_id[numerator]["true_integrated_intensity_dn"] / reference_value,
                "numerator_qc_flags": list(by_id[numerator]["qc_flags"]),
                "denominator_qc_flags": list(by_id[denominator]["qc_flags"]),
            }
        )

    image_flags: list[str] = []
    if any(FLAG_SATURATED in band["qc_flags"] for band in bands):
        image_flags.append(FLAG_SATURATED)
    if cell.image_format in LOSSY_FORMATS:
        image_flags.append(FLAG_LOSSY_FORMAT)
    if exposure_params.reference_peak_fraction < config.qc.low_dynamic_range_peak_fraction:
        image_flags.append(FLAG_LOW_DYNAMIC_RANGE)

    ground_truth: dict[str, Any] = {
        "schema_version": GROUND_TRUTH_SCHEMA_VERSION,
        "synth_version": SYNTH_VERSION,
        "image_id": cell.image_id,
        "split": cell.split,
        "image_path": f"{IMAGES_DIRNAME}/{cell.image_id}{extension_for(cell.image_format)}",
        "image_format": cell.image_format,
        "lossy_format": cell.image_format in LOSSY_FORMATS,
        "bit_depth": cell.bit_depth,
        "max_value": max_value,
        "width_px": canvas.width_px,
        "height_px": canvas.height_px,
        "pixel_sha256": pixel_digest(pixels),
        "generation": {
            "master_seed": master_seed,
            "config_digest": config.digest(),
            "stream_seeds": {name: seeds[name] for name in RNG_STREAMS},
            "difficulty": cell.levels(),
            "parameters": {
                "canvas": {
                    "width_px": canvas.width_px,
                    "height_px": canvas.height_px,
                    "margin_x_px": canvas.margin_x_px,
                    "row_y_fractions": list(canvas.row_y_fractions),
                    "row_roles": list(canvas.row_roles),
                },
                "band_layout": asdict(config.band_layout),
                "lane_layout": asdict(config.lane_layout),
                "background": asdict(background_params),
                "noise": asdict(noise_params),
                "band_shape": asdict(shape_params),
                "lane_geometry": asdict(geometry_params),
                "defect": asdict(defect_params),
                "exposure": asdict(exposure_params),
                "encoding": asdict(config.encoding),
                "qc": asdict(config.qc),
                "derived": derived | {"tilt_slope": geometry.tilt_slope},
            },
        },
        "background_truth": {
            "model": cell.background,
            "min_dn": float(background.min()),
            "max_dn": float(background.max()),
            "mean_dn": float(background.mean()),
            "speckle_blobs": blobs,
        },
        "defects": dust_records + scratch_records,
        "lanes": lanes,
        "bands": bands,
        "normalization": {
            "reference_role": REFERENCE_ROLE,
            "ratios": ratios,
        },
        "image_qc_flags": image_flags,
        "image_stats": {
            "min_dn": int(pixels.min()),
            "max_dn": int(pixels.max()),
            "mean_dn": float(pixels.mean()),
            "clipped_pixel_count": clipped_pixel_count(pixels, cell.bit_depth),
        },
    }
    return GeneratedImage(
        cell=cell,
        pixels=pixels,
        ground_truth=round_floats(ground_truth, config.json_float_decimals),
    )


def _write_json(path: Path, payload: dict[str, Any], *, sort_keys: bool) -> None:
    """Write ``payload`` as newline-terminated UTF-8 JSON.

    ``sort_keys`` is explicit at every call site because it is not cosmetic for this
    package: the config echo contains order-bearing mappings (``matrix.format_depths``
    is what the balanced schedule assigns containers from, and
    :meth:`GeneratorConfig.digest` hashes it in declaration order), so sorting it would
    record an axis order that was never used and could not be hashed back to the
    recorded digest. Ground truth contains no order-bearing mapping and is sorted for
    readable diffs.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=sort_keys, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


def _existing_entry_names(directory: Path) -> set[str]:
    """Return every entry name directly inside ``directory``, files and directories.

    Directories count: a leftover ``images/old/`` is exactly as much of a mixed-set
    hazard as a leftover image, and it must not be invisible to the guard.
    """
    if not directory.is_dir():
        return set()
    return {path.name for path in directory.iterdir()}


def _read_json_object(path: Path) -> dict[str, Any]:
    """Return ``path`` parsed as a JSON object, or raise :class:`GoldSetMismatchError`.

    A truncated or half-written file is the realistic corruption mode this guard
    exists for, so it must produce an actionable error rather than a raw traceback.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GoldSetMismatchError(
            f"{path} could not be read ({exc}); the gold set cannot be verified. Fix the "
            f"read error rather than forcing a regeneration over data you cannot inspect."
        ) from exc
    try:
        document = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GoldSetMismatchError(
            f"{path} could not be read as JSON ({exc}); the gold set at "
            f"{path.parent} looks corrupt. Re-run with --force to replace it."
        ) from exc
    if not isinstance(document, dict):
        raise GoldSetMismatchError(
            f"{path} does not contain a JSON object (found "
            f"{type(document).__name__}); the gold set at {path.parent} looks corrupt. "
            f"Re-run with --force to replace it."
        )
    return document


def _recorded_provenance(document: dict[str, Any]) -> tuple[Any, Any, Any]:
    """Return ``(master_seed, synth_version, config_digest)`` from a written artefact.

    Accepts both shapes this package writes: a ground-truth file (seed and digest
    nested under ``generation``) and the ``generation_config.json`` echo (all three at
    the top level). Anything else yields ``None`` values, which fail the comparison.
    """
    generation = document.get("generation")
    if isinstance(generation, dict):
        return (
            generation.get("master_seed"),
            document.get("synth_version"),
            generation.get("config_digest"),
        )
    return (
        document.get("master_seed"),
        document.get("synth_version"),
        document.get("config_digest"),
    )


def _reject_stale_directories(directory: Path, expected: set[str]) -> None:
    """Raise if ``directory`` holds subdirectories this run would neither write nor delete.

    Checked before any file is written, and regardless of ``force``: the generator
    never deletes a directory tree, so this is the one leftover it cannot resolve, and
    discovering it after the write loop would leave a half-replaced gold set.
    """
    if not directory.is_dir():
        return
    stale = sorted(
        path.name for path in directory.iterdir() if path.is_dir() and path.name not in expected
    )
    if stale:
        raise GoldSetMismatchError(
            f"{directory} contains subdirectories this run would not rewrite: {stale}. "
            f"Remove them by hand; neither the generator nor --force deletes directories."
        )


def _guard_existing_gold_set(
    out_dir: Path,
    master_seed: int,
    config: GeneratorConfig,
    expected_ground_truth: set[str],
    expected_images: set[str],
    force: bool,
) -> None:
    """Refuse to half-overwrite a gold set that this run would not fully replace.

    Checks the seed, the freeze marker and the config digest recorded in *every*
    existing ground-truth file **and** in ``generation_config.json``, then checks both
    output directories for entries the run would leave behind. Reading the config echo
    matters: deleting ``ground_truth/`` is exactly how a user ends up pointing a new
    seed -- or an edited config, which does not change any filename at all -- at
    another generation's images. Raises :class:`GoldSetMismatchError` unless ``force``
    is set.

    The stale-*directory* check runs even under ``force``, because it is the one
    condition the run cannot resolve by overwriting: refusing it after the write loop
    would leave a half-replaced set whose provenance contradicts its data.
    """
    ground_truth_dir = out_dir / GROUND_TRUTH_DIRNAME
    images_dir = out_dir / IMAGES_DIRNAME
    _reject_stale_directories(ground_truth_dir, expected_ground_truth)
    _reject_stale_directories(images_dir, expected_images)
    if force:
        return

    sources = sorted(ground_truth_dir.glob("*.json")) if ground_truth_dir.is_dir() else []
    config_echo = out_dir / CONFIG_FILENAME
    if config_echo.is_file():
        sources.append(config_echo)

    for path in sources:
        recorded_seed, recorded_version, recorded_digest = _recorded_provenance(
            _read_json_object(path)
        )
        if recorded_seed != master_seed or recorded_version != SYNTH_VERSION:
            raise GoldSetMismatchError(
                f"{path} belongs to a gold set from seed {recorded_seed!r} / synth "
                f"version {recorded_version!r}, but this run is seed {master_seed} / "
                f"{SYNTH_VERSION}. Re-run with --force to replace it deliberately."
            )
        if recorded_digest != config.digest():
            raise GoldSetMismatchError(
                f"{path} was generated with config digest {recorded_digest!r} but this "
                f"run uses {config.digest()!r}. A parameter changed without a "
                f"SYNTH_VERSION bump; bump it and record a break marker in "
                f"evals/history.md, or re-run with --force if this is deliberate."
            )

    stale_ground_truth = _existing_entry_names(ground_truth_dir) - expected_ground_truth
    stale_images = _existing_entry_names(images_dir) - expected_images
    if stale_ground_truth or stale_images:
        raise GoldSetMismatchError(
            f"{out_dir} holds entries this run would not rewrite: ground truth "
            f"{sorted(stale_ground_truth)}, images {sorted(stale_images)}. "
            f"Re-run with --force to replace the set deliberately."
        )


def _remove_stale(directory: Path, expected: set[str]) -> list[str]:
    """Delete regular files in ``directory`` that are not in ``expected``.

    Only ever called under ``force``: without it the guard has already proved there
    is nothing stale, so this function must never be the thing that silently deletes
    a gold-set file. Returns the names removed, for the caller to report.

    Stale directories are rejected by :func:`_reject_stale_directories` before any
    write; meeting one here means that check was bypassed, so it raises rather than
    skipping silently. Removing a tree is too destructive to do as a side effect.
    """
    if not directory.is_dir():
        return []
    removed: list[str] = []
    for path in sorted(directory.iterdir()):
        if path.name in expected:
            continue
        if path.is_dir():
            raise GoldSetMismatchError(
                f"{path} is a directory this run would not rewrite; remove it by hand, "
                f"--force will not delete directories"
            )
        path.unlink()
        removed.append(path.name)
    return removed


def generate_dataset(
    out_dir: Path,
    master_seed: int,
    config: GeneratorConfig = DEFAULT_CONFIG,
    force: bool = False,
) -> DatasetSummary:
    """Generate the full dataset into ``out_dir`` and return what was written.

    Writes ``out_dir/images/`` , ``out_dir/ground_truth/`` and
    ``out_dir/generation_config.json``. The config echo is the only written record of
    the difficulty-matrix axis definitions, so it is serialised in declaration order:
    re-hashing its ``config`` block reproduces the recorded ``config_digest`` exactly.
    This is the only writer of ground truth in the repository. On return, both output
    directories hold exactly this run's set:
    leftovers from an earlier generation are refused, or -- only under ``force`` --
    deleted and reported in :attr:`DatasetSummary.removed_files`.
    """
    cells = build_matrix(config, master_seed)
    ground_truth_dir = out_dir / GROUND_TRUTH_DIRNAME
    images_dir = out_dir / IMAGES_DIRNAME
    expected_ground_truth = {f"{cell.image_id}.json" for cell in cells}
    expected_images = {
        f"{cell.image_id}{extension_for(cell.image_format)}" for cell in cells
    }
    _guard_existing_gold_set(
        out_dir,
        master_seed,
        config,
        expected_ground_truth,
        expected_images,
        force,
    )

    ground_truth_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    written: list[WrittenImage] = []
    for cell in cells:
        generated = generate_image(cell, config, master_seed)
        image_path = out_dir / generated.ground_truth["image_path"]
        write_image(
            image_path, generated.pixels, cell.image_format, cell.bit_depth, config.encoding
        )
        ground_truth_path = ground_truth_dir / f"{cell.image_id}.json"
        _write_json(ground_truth_path, generated.ground_truth, sort_keys=True)
        written.append(
            WrittenImage(
                image_id=cell.image_id,
                split=cell.split,
                image_path=image_path,
                ground_truth_path=ground_truth_path,
            )
        )

    removed: list[str] = []
    if force:
        removed = _remove_stale(ground_truth_dir, expected_ground_truth) + _remove_stale(
            images_dir, expected_images
        )

    _write_json(
        out_dir / CONFIG_FILENAME,
        round_floats(
            {
                "synth_version": SYNTH_VERSION,
                "ground_truth_schema_version": GROUND_TRUTH_SCHEMA_VERSION,
                "master_seed": master_seed,
                "config_digest": config.digest(),
                "image_count": len(cells),
                "split_counts": {
                    "dev": config.split.dev_count,
                    "test": config.split.test_count,
                },
                "config": config.to_dict(),
            },
            config.json_float_decimals,
        ),
        sort_keys=False,
    )
    return DatasetSummary(images=tuple(written), removed_files=tuple(removed))
