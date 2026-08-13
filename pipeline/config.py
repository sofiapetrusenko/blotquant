"""The processing configuration: a YAML file parsed into a frozen, typed object.

Every number the pipeline uses lives here and nowhere else, and everything here is
echoed verbatim into ``provenance.parameters`` of the result. Three rules follow from
that and are enforced by :func:`load_config`:

* **No defaults.** A key the pipeline reads must be present in the file. There is in
  particular no default background method: ``background.method`` is required.
* **No unknown keys.** A misspelled key is a parameter that silently did not apply, so
  an unexpected key raises rather than being ignored.
* **No unused keys.** A config carries the parameters of the *selected* background
  method only; parameters for the other method are rejected, because an echoed
  parameter that nothing read is a false provenance record.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from pipeline.errors import ConfigError

ROLLING_BALL = "rolling_ball"
LOCAL_MEDIAN = "local_median"
BACKGROUND_METHODS: tuple[str, ...] = (ROLLING_BALL, LOCAL_MEDIAN)
"""The background methods this pipeline implements. ``method`` must name one of them."""

DETECTION_METHOD = "profile_projection"
"""The only detection method implemented in Phase 1."""

QUANTIFICATION_METHOD = "roi_sum"
"""The only quantification method implemented in Phase 1."""

HOUSEKEEPING_SINGLE = "housekeeping_single"
HOUSEKEEPING_MULTI = "housekeeping_multi"
TOTAL_PROTEIN = "total_protein"
NORMALIZATION_MODES: tuple[str, ...] = (HOUSEKEEPING_SINGLE, HOUSEKEEPING_MULTI, TOTAL_PROTEIN)
"""The normalization modes this pipeline implements. ``mode`` must name one of them."""

HOUSEKEEPING_MODES: frozenset[str] = frozenset({HOUSEKEEPING_SINGLE, HOUSEKEEPING_MULTI})
"""Modes whose denominator is one or more caller-designated reference bands."""


def _as_mapping(value: Any, where: str) -> Mapping[str, Any]:
    """Return ``value`` as a mapping, or raise :class:`ConfigError` naming ``where``."""
    if not isinstance(value, Mapping):
        raise ConfigError(
            f"{where} must be a mapping of keys to values, got {type(value).__name__}"
        )
    for key in value:
        if not isinstance(key, str):
            raise ConfigError(f"{where} has a non-string key {key!r}; keys must be strings")
    return value


def _require_exact_keys(mapping: Mapping[str, Any], expected: Sequence[str], where: str) -> None:
    """Raise :class:`ConfigError` unless ``mapping`` has exactly the ``expected`` keys."""
    missing = [key for key in expected if key not in mapping]
    unexpected = sorted(set(mapping) - set(expected))
    if missing:
        raise ConfigError(
            f"{where} is missing required key(s) {missing}; the pipeline has no default "
            f"for them. Expected exactly {sorted(expected)}"
        )
    if unexpected:
        raise ConfigError(
            f"{where} has unknown key(s) {unexpected}; a key the pipeline does not read "
            f"is a parameter that silently did not apply. Expected exactly {sorted(expected)}"
        )


def _as_str(mapping: Mapping[str, Any], key: str, where: str) -> str:
    """Return a non-empty string entry, or raise :class:`ConfigError`."""
    value = mapping[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}.{key} must be a non-empty string, got {value!r}")
    return value


def _as_bool(mapping: Mapping[str, Any], key: str, where: str) -> bool:
    """Return a boolean entry, or raise :class:`ConfigError`."""
    value = mapping[key]
    if not isinstance(value, bool):
        raise ConfigError(f"{where}.{key} must be true or false, got {value!r}")
    return value


def _as_int(mapping: Mapping[str, Any], key: str, where: str) -> int:
    """Return an integer entry, or raise :class:`ConfigError` (``bool`` is not an int here)."""
    value = mapping[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where}.{key} must be an integer, got {value!r}")
    return value


def _as_float(mapping: Mapping[str, Any], key: str, where: str) -> float:
    """Return a float entry, or raise :class:`ConfigError` (``bool`` is not a number here)."""
    value = mapping[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where}.{key} must be a number, got {value!r}")
    return float(value)


def _require_odd(value: int, minimum: int, where: str) -> int:
    """Return an odd integer ``>= minimum``, or raise :class:`ConfigError`."""
    if value < minimum or value % 2 == 0:
        raise ConfigError(
            f"{where} must be an odd integer >= {minimum} so the window is centred on "
            f"its pixel, got {value}"
        )
    return value


def _require_range(
    value: float, low: float, high: float, where: str, *, low_open: bool, high_open: bool
) -> float:
    """Return ``value`` if it lies in the requested interval, else raise :class:`ConfigError`."""
    too_low = value <= low if low_open else value < low
    too_high = value >= high if high_open else value > high
    if too_low or too_high:
        left = "(" if low_open else "["
        right = ")" if high_open else "]"
        raise ConfigError(f"{where} must be in {left}{low}, {high}{right}, got {value}")
    return value


@dataclass(frozen=True)
class RollingBallParams:
    """Parameters of the rolling-ball background.

    ``presmooth_px`` is a mean filter applied before the ball is rolled. It is not
    cosmetic: a rolling ball is a morphological opening, so it follows the *minimum* of
    the noise and biases the background low by an amount that grows with the per-pixel
    noise. Pre-smoothing is what ImageJ's implementation does for the same reason.
    """

    radius_px: float
    presmooth_px: int

    def as_parameters(self) -> dict[str, Any]:
        """Return the provenance echo of these parameters."""
        return {"radius_px": self.radius_px, "presmooth_px": self.presmooth_px}


@dataclass(frozen=True)
class LocalMedianParams:
    """Parameters of the local-median background.

    ``window_px`` must be large enough that a majority of the pixels under a band
    centre are still background, otherwise the median climbs onto the band and the
    band's own signal is subtracted from itself.
    """

    window_px: int

    def as_parameters(self) -> dict[str, Any]:
        """Return the provenance echo of these parameters."""
        return {"window_px": self.window_px}


@dataclass(frozen=True)
class BackgroundConfig:
    """The background method and exactly the parameters that method reads."""

    method: str
    params: RollingBallParams | LocalMedianParams

    def as_parameters(self) -> dict[str, Any]:
        """Return the provenance echo, with ``method`` first."""
        return {"method": self.method, **self.params.as_parameters()}


@dataclass(frozen=True)
class LaneDetectionConfig:
    """Parameters of lane detection from the column profile.

    ``min_prominence_fraction`` is taken against a *robust* profile range, the spread
    between the ``robust_range_percentile`` and its complement, rather than against
    max-minus-min: a single near-vertical scratch is brighter than any lane and would
    otherwise set the scale for the whole image.

    ``extent_relative_height`` and ``extent_min_sigma`` are the band extent rule's pair,
    applied to the column profile. They are read only when exactly one lane is detected,
    where there is no spacing between centres to measure a pitch from and the lone lane's
    width has to come from its own extent instead.
    """

    min_separation_px: int
    min_prominence_fraction: float
    robust_range_percentile: float
    extent_relative_height: float
    extent_min_sigma: float

    def as_parameters(self) -> dict[str, Any]:
        """Return the provenance echo of these parameters."""
        return {
            "min_separation_px": self.min_separation_px,
            "min_prominence_fraction": self.min_prominence_fraction,
            "robust_range_percentile": self.robust_range_percentile,
            "extent_relative_height": self.extent_relative_height,
            "extent_min_sigma": self.extent_min_sigma,
        }


@dataclass(frozen=True)
class BandDetectionConfig:
    """Parameters of band detection from the per-lane row profile.

    A peak must clear **both** prominence criteria, because the two reject different
    things. ``min_prominence_sigma`` is a detection threshold in units of the profile's
    own measured noise and rejects noise excursions. ``min_prominence_fraction`` is a
    fraction of the lane's own signal range and rejects gel *structure* -- speckle,
    dust, a scratch crossing the lane -- which is real, not noise, and therefore
    clears any sigma threshold.

    Both ROI edges use the same pair of rules: the peak has to fall to
    ``extent_relative_height`` of its height, or to within ``extent_min_sigma`` of the
    baseline, whichever comes first. Without the noise floor a weak peak never crosses
    a 6% threshold and its ROI grows until it hits its neighbour.
    """

    min_separation_px: int
    min_prominence_fraction: float
    min_prominence_sigma: float
    baseline_percentile: float
    extent_relative_height: float
    extent_min_sigma: float

    def as_parameters(self) -> dict[str, Any]:
        """Return the provenance echo of these parameters."""
        return {
            "min_separation_px": self.min_separation_px,
            "min_prominence_fraction": self.min_prominence_fraction,
            "min_prominence_sigma": self.min_prominence_sigma,
            "baseline_percentile": self.baseline_percentile,
            "extent_relative_height": self.extent_relative_height,
            "extent_min_sigma": self.extent_min_sigma,
        }


@dataclass(frozen=True)
class DetectionConfig:
    """Detection method and its parameters."""

    method: str
    profile_smoothing_px: int
    lane: LaneDetectionConfig
    band: BandDetectionConfig

    def as_parameters(self) -> dict[str, Any]:
        """Return the provenance echo, with ``method`` first."""
        return {
            "method": self.method,
            "profile_smoothing_px": self.profile_smoothing_px,
            "lane": self.lane.as_parameters(),
            "band": self.band.as_parameters(),
        }


@dataclass(frozen=True)
class QuantificationConfig:
    """Quantification method and its parameters."""

    method: str
    clamp_negative_pixels: bool

    def as_parameters(self) -> dict[str, Any]:
        """Return the provenance echo, with ``method`` first."""
        return {"method": self.method, "clamp_negative_pixels": self.clamp_negative_pixels}


@dataclass(frozen=True)
class QcConfig:
    """The five parameters QC decides its flags on.

    Each is chosen from a criterion that can be stated without reference to the synthetic
    generator, because the flags are *scored* against labels the generator's own thresholds
    produced and copying those would make the score circular
    (``synth/MODELS.md``, "What counts as special-casing"). NOTES.md's Phase 2 section
    records each criterion and says which of these values coincide with a generator value.

    * ``saturated_min_clipped_pixels`` -- how many pixels at full scale inside a band's ROI
      make that band's integrated intensity a lower bound rather than a measurement.
    * ``overlap_iou_threshold`` -- how much two same-lane ROIs may overlap before the
      shared pixels are counted into both bands' integrals.
    * ``shoulder_half_maximum_fraction`` and ``shoulder_half_width_ratio`` -- together, the
      shape criterion for ``unresolved_shoulder``. The first is the height, as a fraction of
      the peak's height above its baseline, at which the two half-widths are measured; the
      second is how far their ratio may exceed 1.0 before the profile is reported as more
      structure than a single band. Both are declared here rather than in a function body
      because the human's Ruling 1 requires the criterion to be recorded in config and
      provenance, and the level sets the test's sensitivity as much as the threshold does. A
      shape test, not a second band: it never creates a centre, a boundary or an ROI.
    * ``dynamic_range_min_peak_fraction`` -- how much of the available range the brightest
      band must use before the weakest bands beside it are quantifiable at all.
    """

    saturated_min_clipped_pixels: int
    overlap_iou_threshold: float
    shoulder_half_maximum_fraction: float
    shoulder_half_width_ratio: float
    dynamic_range_min_peak_fraction: float

    def as_parameters(self) -> dict[str, Any]:
        """Return the provenance echo of these parameters."""
        return {
            "saturated_min_clipped_pixels": self.saturated_min_clipped_pixels,
            "overlap_iou_threshold": self.overlap_iou_threshold,
            "shoulder_half_maximum_fraction": self.shoulder_half_maximum_fraction,
            "shoulder_half_width_ratio": self.shoulder_half_width_ratio,
            "dynamic_range_min_peak_fraction": self.dynamic_range_min_peak_fraction,
        }


@dataclass(frozen=True)
class NormalizationConfig:
    """The normalization mode and whether QC-flagged bands are excluded from ratios.

    ``exclude_qc_flagged`` is the default-true policy PLAN.md fixes: a flagged band still
    reports its value and its flags, and its *ratio* is reported too, marked excluded with
    a reason. Setting it false is an explicit override and is recorded as a warning on the
    result.

    Which bands are the housekeeping references is **not** here: band ids are a property of
    one image, not of a parameter set, so they arrive per call and are recorded on the
    result under ``normalization.reference_band_ids``.
    """

    mode: str
    exclude_qc_flagged: bool

    def as_parameters(self) -> dict[str, Any]:
        """Return the provenance echo, with ``mode`` first."""
        return {"mode": self.mode, "exclude_qc_flagged": self.exclude_qc_flagged}


@dataclass(frozen=True)
class PipelineConfig:
    """A complete, explicit parameter set for one pipeline run."""

    config_id: str
    background: BackgroundConfig
    detection: DetectionConfig
    quantification: QuantificationConfig
    qc: QcConfig
    normalization: NormalizationConfig

    def as_parameters(self) -> dict[str, Any]:
        """Return the full provenance echo of every parameter the pipeline reads."""
        return {
            "background": self.background.as_parameters(),
            "detection": self.detection.as_parameters(),
            "quantification": self.quantification.as_parameters(),
            "qc": self.qc.as_parameters(),
            "normalization": self.normalization.as_parameters(),
        }

    def digest(self) -> str:
        """Return a ``sha256:...`` digest of the config id plus every echoed parameter.

        Serialised in declaration order rather than sorted order: the echo is the
        record of what ran, and its ordering is a fixed property of this source file,
        so the digest is stable run to run while remaining sensitive to any edit.
        """
        payload = json.dumps(
            {"config_id": self.config_id, "parameters": self.as_parameters()},
            sort_keys=False,
            separators=(",", ":"),
        )
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_background(raw: Any) -> BackgroundConfig:
    """Build a :class:`BackgroundConfig`, requiring an explicit method and its parameters."""
    mapping = _as_mapping(raw, "background")
    if "method" not in mapping:
        raise ConfigError(
            "background.method is required and has no default: an implicit background "
            f"method would leave every intensity unattributable. Set it to one of "
            f"{list(BACKGROUND_METHODS)}"
        )
    method = _as_str(mapping, "method", "background")
    if method not in BACKGROUND_METHODS:
        raise ConfigError(
            f"background.method must be one of {list(BACKGROUND_METHODS)}, got {method!r}"
        )
    _require_exact_keys(mapping, ("method", method), "background")
    params_where = f"background.{method}"
    params_raw = _as_mapping(mapping[method], params_where)

    params: RollingBallParams | LocalMedianParams
    if method == ROLLING_BALL:
        _require_exact_keys(params_raw, ("radius_px", "presmooth_px"), params_where)
        radius = _as_float(params_raw, "radius_px", params_where)
        if radius <= 0.0:
            raise ConfigError(f"{params_where}.radius_px must be positive, got {radius}")
        params = RollingBallParams(
            radius_px=radius,
            presmooth_px=_require_odd(
                _as_int(params_raw, "presmooth_px", params_where), 1, f"{params_where}.presmooth_px"
            ),
        )
    else:
        _require_exact_keys(params_raw, ("window_px",), params_where)
        params = LocalMedianParams(
            window_px=_require_odd(
                _as_int(params_raw, "window_px", params_where), 3, f"{params_where}.window_px"
            )
        )
    return BackgroundConfig(method=method, params=params)


def _parse_lane(raw: Any) -> LaneDetectionConfig:
    """Build a :class:`LaneDetectionConfig` from the ``detection.lane`` block."""
    where = "detection.lane"
    mapping = _as_mapping(raw, where)
    _require_exact_keys(
        mapping,
        (
            "min_separation_px",
            "min_prominence_fraction",
            "robust_range_percentile",
            "extent_relative_height",
            "extent_min_sigma",
        ),
        where,
    )
    separation = _as_int(mapping, "min_separation_px", where)
    if separation < 1:
        raise ConfigError(f"{where}.min_separation_px must be >= 1, got {separation}")
    lane_extent_sigma = _as_float(mapping, "extent_min_sigma", where)
    if lane_extent_sigma < 0.0:
        raise ConfigError(f"{where}.extent_min_sigma must be >= 0, got {lane_extent_sigma}")
    return LaneDetectionConfig(
        min_separation_px=separation,
        min_prominence_fraction=_require_range(
            _as_float(mapping, "min_prominence_fraction", where),
            0.0,
            1.0,
            f"{where}.min_prominence_fraction",
            low_open=True,
            high_open=False,
        ),
        robust_range_percentile=_require_range(
            _as_float(mapping, "robust_range_percentile", where),
            0.0,
            50.0,
            f"{where}.robust_range_percentile",
            low_open=False,
            high_open=True,
        ),
        extent_relative_height=_require_range(
            _as_float(mapping, "extent_relative_height", where),
            0.0,
            1.0,
            f"{where}.extent_relative_height",
            low_open=True,
            high_open=True,
        ),
        extent_min_sigma=lane_extent_sigma,
    )


def _parse_band(raw: Any) -> BandDetectionConfig:
    """Build a :class:`BandDetectionConfig` from the ``detection.band`` block."""
    where = "detection.band"
    mapping = _as_mapping(raw, where)
    _require_exact_keys(
        mapping,
        (
            "min_separation_px",
            "min_prominence_fraction",
            "min_prominence_sigma",
            "baseline_percentile",
            "extent_relative_height",
            "extent_min_sigma",
        ),
        where,
    )
    separation = _as_int(mapping, "min_separation_px", where)
    if separation < 1:
        raise ConfigError(f"{where}.min_separation_px must be >= 1, got {separation}")
    prominence_sigma = _as_float(mapping, "min_prominence_sigma", where)
    if prominence_sigma < 0.0:
        raise ConfigError(f"{where}.min_prominence_sigma must be >= 0, got {prominence_sigma}")
    extent_sigma = _as_float(mapping, "extent_min_sigma", where)
    if extent_sigma < 0.0:
        raise ConfigError(f"{where}.extent_min_sigma must be >= 0, got {extent_sigma}")
    return BandDetectionConfig(
        min_separation_px=separation,
        min_prominence_fraction=_require_range(
            _as_float(mapping, "min_prominence_fraction", where),
            0.0,
            1.0,
            f"{where}.min_prominence_fraction",
            low_open=True,
            high_open=False,
        ),
        min_prominence_sigma=prominence_sigma,
        baseline_percentile=_require_range(
            _as_float(mapping, "baseline_percentile", where),
            0.0,
            50.0,
            f"{where}.baseline_percentile",
            low_open=False,
            high_open=True,
        ),
        extent_relative_height=_require_range(
            _as_float(mapping, "extent_relative_height", where),
            0.0,
            1.0,
            f"{where}.extent_relative_height",
            low_open=True,
            high_open=True,
        ),
        extent_min_sigma=extent_sigma,
    )


def _parse_detection(raw: Any) -> DetectionConfig:
    """Build a :class:`DetectionConfig` from the ``detection`` block."""
    where = "detection"
    mapping = _as_mapping(raw, where)
    _require_exact_keys(mapping, ("method", "profile_smoothing_px", "lane", "band"), where)
    method = _as_str(mapping, "method", where)
    if method != DETECTION_METHOD:
        raise ConfigError(f"{where}.method must be {DETECTION_METHOD!r}, got {method!r}")
    return DetectionConfig(
        method=method,
        profile_smoothing_px=_require_odd(
            _as_int(mapping, "profile_smoothing_px", where), 1, f"{where}.profile_smoothing_px"
        ),
        lane=_parse_lane(mapping["lane"]),
        band=_parse_band(mapping["band"]),
    )


def _parse_quantification(raw: Any) -> QuantificationConfig:
    """Build a :class:`QuantificationConfig` from the ``quantification`` block."""
    where = "quantification"
    mapping = _as_mapping(raw, where)
    _require_exact_keys(mapping, ("method", "clamp_negative_pixels"), where)
    method = _as_str(mapping, "method", where)
    if method != QUANTIFICATION_METHOD:
        raise ConfigError(f"{where}.method must be {QUANTIFICATION_METHOD!r}, got {method!r}")
    return QuantificationConfig(
        method=method,
        clamp_negative_pixels=_as_bool(mapping, "clamp_negative_pixels", where),
    )


def _parse_qc(raw: Any) -> QcConfig:
    """Build a :class:`QcConfig` from the ``qc`` block."""
    where = "qc"
    mapping = _as_mapping(raw, where)
    _require_exact_keys(
        mapping,
        (
            "saturated_min_clipped_pixels",
            "overlap_iou_threshold",
            "shoulder_half_maximum_fraction",
            "shoulder_half_width_ratio",
            "dynamic_range_min_peak_fraction",
        ),
        where,
    )
    clipped = _as_int(mapping, "saturated_min_clipped_pixels", where)
    if clipped < 1:
        raise ConfigError(
            f"{where}.saturated_min_clipped_pixels must be >= 1, got {clipped}; a band with "
            f"no pixel at full scale is not saturated, so 0 would flag every band"
        )
    ratio = _as_float(mapping, "shoulder_half_width_ratio", where)
    if ratio <= 1.0:
        raise ConfigError(
            f"{where}.shoulder_half_width_ratio must be > 1.0, got {ratio}; it is the ratio "
            f"of a profile's wider half-width to its narrower one, which is 1.0 for a "
            f"symmetric peak, so a value at or below 1.0 flags every band"
        )
    return QcConfig(
        saturated_min_clipped_pixels=clipped,
        overlap_iou_threshold=_require_range(
            _as_float(mapping, "overlap_iou_threshold", where),
            0.0,
            1.0,
            f"{where}.overlap_iou_threshold",
            low_open=True,
            high_open=False,
        ),
        shoulder_half_maximum_fraction=_require_range(
            _as_float(mapping, "shoulder_half_maximum_fraction", where),
            0.0,
            1.0,
            f"{where}.shoulder_half_maximum_fraction",
            low_open=True,
            high_open=True,
        ),
        shoulder_half_width_ratio=ratio,
        dynamic_range_min_peak_fraction=_require_range(
            _as_float(mapping, "dynamic_range_min_peak_fraction", where),
            0.0,
            1.0,
            f"{where}.dynamic_range_min_peak_fraction",
            low_open=True,
            high_open=True,
        ),
    )


def _parse_normalization(raw: Any) -> NormalizationConfig:
    """Build a :class:`NormalizationConfig` from the ``normalization`` block."""
    where = "normalization"
    mapping = _as_mapping(raw, where)
    _require_exact_keys(mapping, ("mode", "exclude_qc_flagged"), where)
    mode = _as_str(mapping, "mode", where)
    if mode not in NORMALIZATION_MODES:
        raise ConfigError(
            f"{where}.mode must be one of {list(NORMALIZATION_MODES)}, got {mode!r}"
        )
    return NormalizationConfig(
        mode=mode,
        exclude_qc_flagged=_as_bool(mapping, "exclude_qc_flagged", where),
    )


def parse_config(raw: Any, source: str) -> PipelineConfig:
    """Build a :class:`PipelineConfig` from already-parsed YAML data.

    ``source`` names the origin of the data and appears in every error message.
    """
    mapping = _as_mapping(raw, source)
    _require_exact_keys(
        mapping, ("id", "background", "detection", "quantification", "qc", "normalization"), source
    )
    return PipelineConfig(
        config_id=_as_str(mapping, "id", source),
        background=_parse_background(mapping["background"]),
        detection=_parse_detection(mapping["detection"]),
        quantification=_parse_quantification(mapping["quantification"]),
        qc=_parse_qc(mapping["qc"]),
        normalization=_parse_normalization(mapping["normalization"]),
    )


def load_config(path: Path) -> PipelineConfig:
    """Read and validate a YAML config file.

    Guarantees that the returned object is complete: every parameter the pipeline reads
    is present, in range, and came from the file. Raises :class:`ConfigError` for a
    missing file, unparseable YAML, a missing or unknown key, or an out-of-range value.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ConfigError(
            f"config file not found: {path}. Pass an existing config with --config; "
            f"the pipeline has no built-in parameter set"
        ) from error
    except OSError as error:
        raise ConfigError(f"cannot read config file {path}: {error}") from error
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ConfigError(f"config file {path} is not valid YAML: {error}") from error
    if raw is None:
        raise ConfigError(f"config file {path} is empty; it must define id, background, "
                          f"detection, quantification, qc and normalization")
    return parse_config(raw, str(path))
