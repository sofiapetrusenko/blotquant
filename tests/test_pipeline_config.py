"""Config loading: the shipped files parse, and every malformed config raises."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from pipeline.config import (
    BACKGROUND_METHODS,
    PipelineConfig,
    load_config,
    parse_config,
)
from pipeline.errors import ConfigError

CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"


def _default_mapping() -> dict[str, Any]:
    """Return the shipped default config as plain data, for mutation in tests."""
    return yaml.safe_load((CONFIG_DIR / "default.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["default.yaml", "rolling_ball.yaml"])
def test_shipped_configs_parse(name: str) -> None:
    """Every config in configs/ loads and names an implemented background method."""
    config = load_config(CONFIG_DIR / name)

    assert isinstance(config, PipelineConfig)
    assert config.background.method in BACKGROUND_METHODS
    assert config.config_id == name.removesuffix(".yaml")


def test_parameters_echo_covers_every_stage() -> None:
    """The provenance echo names a method for each stage and carries its parameters."""
    parameters = load_config(CONFIG_DIR / "default.yaml").as_parameters()

    assert set(parameters) == {"background", "detection", "quantification"}
    for stage in parameters.values():
        assert "method" in stage
    assert parameters["background"]["window_px"] == 51
    assert "radius_px" not in parameters["background"]


def test_digest_is_stable_and_sensitive() -> None:
    """The digest reproduces across loads and changes when any parameter changes."""
    first = load_config(CONFIG_DIR / "default.yaml")
    second = load_config(CONFIG_DIR / "default.yaml")
    assert first.digest() == second.digest()
    assert first.digest().startswith("sha256:")

    mapping = _default_mapping()
    mapping["detection"]["profile_smoothing_px"] = 7
    assert parse_config(mapping, "<test>").digest() != first.digest()


def test_the_two_shipped_configs_differ_only_in_their_background() -> None:
    """configs/rolling_ball.yaml claims to isolate the background choice; keep that true.

    Its header comment compares its dev scores against default.yaml's and says the
    difference is between backgrounds alone. That claim silently becomes false the first
    time default.yaml's detection block is retuned without the other file following.
    """
    default = load_config(CONFIG_DIR / "default.yaml")
    rolling_ball = load_config(CONFIG_DIR / "rolling_ball.yaml")

    assert default.detection == rolling_ball.detection
    assert default.quantification == rolling_ball.quantification
    assert default.background != rolling_ball.background


def test_digest_distinguishes_the_two_shipped_configs() -> None:
    """Two configs that differ only in background method get different digests."""
    assert (
        load_config(CONFIG_DIR / "default.yaml").digest()
        != load_config(CONFIG_DIR / "rolling_ball.yaml").digest()
    )


def test_missing_background_method_raises() -> None:
    """There is no default background method: omitting it raises."""
    mapping = _default_mapping()
    del mapping["background"]["method"]

    with pytest.raises(ConfigError, match="background.method is required"):
        parse_config(mapping, "<test>")


@pytest.mark.parametrize("method", ["", "   ", None, 7])
def test_empty_or_non_string_background_method_raises(method: Any) -> None:
    """A blank or non-string method is not a method."""
    mapping = _default_mapping()
    mapping["background"]["method"] = method

    with pytest.raises(ConfigError):
        parse_config(mapping, "<test>")


def test_unknown_background_method_raises() -> None:
    """A method the pipeline cannot run raises at load time, not at run time."""
    mapping = _default_mapping()
    mapping["background"] = {"method": "subtract_the_mean"}

    with pytest.raises(ConfigError, match="must be one of"):
        parse_config(mapping, "<test>")


def test_parameters_for_an_unselected_method_raise() -> None:
    """A config may not carry parameters it does not use."""
    mapping = _default_mapping()
    mapping["background"]["rolling_ball"] = {"radius_px": 50.0, "presmooth_px": 9}

    with pytest.raises(ConfigError, match="unknown key"):
        parse_config(mapping, "<test>")


def test_missing_method_parameter_block_raises() -> None:
    """Selecting a method without its parameters raises."""
    mapping = _default_mapping()
    del mapping["background"]["local_median"]

    with pytest.raises(ConfigError, match="missing required key"):
        parse_config(mapping, "<test>")


@pytest.mark.parametrize(
    "path",
    [
        ("detection", "profile_smoothing_px"),
        ("detection", "lane", "min_separation_px"),
        ("detection", "band", "extent_relative_height"),
        ("quantification", "clamp_negative_pixels"),
        ("id",),
    ],
)
def test_missing_key_raises(path: tuple[str, ...]) -> None:
    """Every key the pipeline reads is required; none of them has a fallback."""
    mapping = _default_mapping()
    target = mapping
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]

    with pytest.raises(ConfigError, match="missing required key"):
        parse_config(mapping, "<test>")


def test_unknown_key_raises() -> None:
    """A misspelled key would silently not apply, so it raises."""
    mapping = _default_mapping()
    mapping["detection"]["band"]["extent_relative_hieght"] = 0.06

    with pytest.raises(ConfigError, match="unknown key"):
        parse_config(mapping, "<test>")


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("detection", "profile_smoothing_px"), 4),
        (("detection", "profile_smoothing_px"), 0),
        (("background", "local_median", "window_px"), 50),
        (("background", "local_median", "window_px"), 1),
        (("detection", "lane", "min_prominence_fraction"), 0.0),
        (("detection", "lane", "min_prominence_fraction"), 1.5),
        (("detection", "lane", "robust_range_percentile"), 50.0),
        (("detection", "band", "min_prominence_sigma"), -1.0),
        (("detection", "band", "extent_min_sigma"), -0.1),
        (("detection", "band", "extent_relative_height"), 1.0),
        (("detection", "band", "baseline_percentile"), 60.0),
        (("detection", "band", "min_separation_px"), 0),
        (("quantification", "clamp_negative_pixels"), "yes"),
        (("detection", "method"), "deep_learning"),
        (("quantification", "method"), "trapezoid"),
    ],
)
def test_out_of_range_value_raises(path: tuple[str, ...], value: Any) -> None:
    """Values outside the range each parameter is defined on raise."""
    mapping = _default_mapping()
    target = mapping
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(ConfigError):
        parse_config(mapping, "<test>")


def test_integer_parameters_reject_booleans() -> None:
    """``True`` is an int in Python but is not a pixel count here."""
    mapping = _default_mapping()
    mapping["detection"]["profile_smoothing_px"] = True

    with pytest.raises(ConfigError, match="must be an integer"):
        parse_config(mapping, "<test>")


def test_non_mapping_section_raises() -> None:
    """A section that is a scalar or a list is not a parameter block."""
    mapping = _default_mapping()
    mapping["detection"] = [1, 2, 3]

    with pytest.raises(ConfigError, match="must be a mapping"):
        parse_config(mapping, "<test>")


def test_malformed_yaml_raises(tmp_path: Path) -> None:
    """Unparseable YAML raises ConfigError, not a raw yaml exception."""
    path = tmp_path / "broken.yaml"
    path.write_text("id: default\nbackground: [unclosed\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(path)


def test_empty_config_file_raises(tmp_path: Path) -> None:
    """An empty file is not a parameter set."""
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")

    with pytest.raises(ConfigError, match="empty"):
        load_config(path)


def test_missing_config_file_raises(tmp_path: Path) -> None:
    """A missing config raises with a message that says what to do."""
    with pytest.raises(ConfigError, match="config file not found"):
        load_config(tmp_path / "absent.yaml")


def test_deep_copy_of_mapping_is_not_mutated() -> None:
    """Parsing does not modify the mapping it was handed."""
    mapping = _default_mapping()
    snapshot = copy.deepcopy(mapping)
    parse_config(mapping, "<test>")
    assert mapping == snapshot
