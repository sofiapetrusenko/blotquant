"""Determinism of the committed gold set.

``python -m synth --seed 42 --out data/`` must reproduce, bit for bit, the ground
truth and the canonical pixel arrays that are committed to the repository. These
tests are the freeze marker's teeth: if numpy, the config or the renderer changes in
a way that moves a pixel, they fail loudly here rather than silently invalidating
every eval score that was ever recorded against this set.

The canonical artefact is the quantized pixel array, not the encoded file: PNG and
JPEG bytes depend on the zlib/libjpeg build, the arrays do not. Lossless containers
are therefore checked against the recorded digest exactly, and JPEG is checked
against the canonical array within an explicit tolerance.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from synth import SYNTH_VERSION
from synth.config import DEFAULT_CONFIG
from synth.encoding import pixel_digest, read_image
from synth.generator import CONFIG_FILENAME, GROUND_TRUTH_DIRNAME, generate_dataset, generate_image
from synth.matrix import build_matrix

COMMITTED_SEED = 42

# JPEG q75 on 8-bit noisy blots. Measured worst case over the six lossy images of the
# **dev split only** (test-split images are never used to choose a number, even for a
# generator self-check): mean 3.48 DN / max 26 DN. The bounds sit above that with
# headroom for libjpeg build differences -- loose enough not to be a build-fragility
# trap, far too tight to let a genuinely different image through, since a changed band
# moves hundreds of DN. They are deliberately *not* re-fitted to each regeneration's
# measured worst case: tightening a comfortable, already-reviewed bound buys nothing
# and only costs CI robustness. The test applies them to every lossy image, dev and
# test alike.
JPEG_MEAN_ABS_TOLERANCE_DN = 6.0
JPEG_MAX_ABS_TOLERANCE_DN = 48


@pytest.fixture(scope="session")
def regenerated_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Regenerate the whole gold set from the committed seed into a temporary tree."""
    out_dir = tmp_path_factory.mktemp("regenerated")
    generate_dataset(out_dir, COMMITTED_SEED, DEFAULT_CONFIG)
    return out_dir


@pytest.fixture(scope="session")
def second_regenerated_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A second, independent regeneration, for run-to-run comparison."""
    out_dir = tmp_path_factory.mktemp("regenerated_again")
    generate_dataset(out_dir, COMMITTED_SEED, DEFAULT_CONFIG)
    return out_dir


def _ground_truth_files(root: Path) -> list[Path]:
    """Return the sorted ground-truth files under a dataset root."""
    return sorted((root / GROUND_TRUTH_DIRNAME).glob("*.json"))


def test_committed_set_has_the_expected_shape(committed_data_dir: Path) -> None:
    files = _ground_truth_files(committed_data_dir)
    assert len(files) == 40
    splits = [json.loads(path.read_text(encoding="utf-8"))["split"] for path in files]
    assert splits.count("dev") == 30
    assert splits.count("test") == 10


def test_regeneration_reproduces_the_committed_ground_truth_byte_for_byte(
    committed_data_dir: Path, regenerated_dir: Path
) -> None:
    committed = _ground_truth_files(committed_data_dir)
    regenerated = _ground_truth_files(regenerated_dir)
    assert [path.name for path in committed] == [path.name for path in regenerated]
    for left, right in zip(committed, regenerated, strict=True):
        assert left.read_text(encoding="utf-8") == right.read_text(encoding="utf-8"), (
            f"{left.name} differs from a fresh generation; the gold set is no longer "
            f"reproducible from seed {COMMITTED_SEED}"
        )


def test_regeneration_reproduces_the_committed_config_echo_byte_for_byte(
    committed_data_dir: Path, regenerated_dir: Path
) -> None:
    """The echo carries the axis definitions, so it is frozen like the ground truth."""
    assert (regenerated_dir / CONFIG_FILENAME).read_text(encoding="utf-8") == (
        committed_data_dir / CONFIG_FILENAME
    ).read_text(encoding="utf-8")


def test_two_independent_runs_agree_on_every_pixel_digest(
    regenerated_dir: Path, second_regenerated_dir: Path
) -> None:
    first = _ground_truth_files(regenerated_dir)
    second = _ground_truth_files(second_regenerated_dir)
    for left, right in zip(first, second, strict=True):
        left_truth = json.loads(left.read_text(encoding="utf-8"))
        right_truth = json.loads(right.read_text(encoding="utf-8"))
        assert left_truth["pixel_sha256"] == right_truth["pixel_sha256"]
        assert left_truth == right_truth


def test_generate_image_is_pure() -> None:
    cell = build_matrix(DEFAULT_CONFIG, COMMITTED_SEED)[0]
    first = generate_image(cell, DEFAULT_CONFIG, COMMITTED_SEED)
    second = generate_image(cell, DEFAULT_CONFIG, COMMITTED_SEED)
    assert np.array_equal(first.pixels, second.pixels)
    assert first.ground_truth == second.ground_truth


def test_a_different_seed_produces_different_pixels() -> None:
    cell = build_matrix(DEFAULT_CONFIG, COMMITTED_SEED)[0]
    baseline = generate_image(cell, DEFAULT_CONFIG, COMMITTED_SEED)
    other = generate_image(cell, DEFAULT_CONFIG, COMMITTED_SEED + 1)
    assert not np.array_equal(baseline.pixels, other.pixels)


def test_committed_lossless_images_match_their_recorded_digest(committed_data_dir: Path) -> None:
    checked = 0
    for path in _ground_truth_files(committed_data_dir):
        truth = json.loads(path.read_text(encoding="utf-8"))
        if truth["lossy_format"]:
            continue
        array = read_image(
            committed_data_dir / truth["image_path"], truth["image_format"], truth["bit_depth"]
        )
        assert array.shape == (truth["height_px"], truth["width_px"])
        assert pixel_digest(array) == truth["pixel_sha256"], truth["image_id"]
        checked += 1
    assert checked > 0


def test_committed_jpeg_images_match_the_canonical_array_within_tolerance(
    committed_data_dir: Path,
) -> None:
    cells = {cell.image_id: cell for cell in build_matrix(DEFAULT_CONFIG, COMMITTED_SEED)}
    checked = 0
    for path in _ground_truth_files(committed_data_dir):
        truth = json.loads(path.read_text(encoding="utf-8"))
        if not truth["lossy_format"]:
            continue
        decoded = read_image(
            committed_data_dir / truth["image_path"], truth["image_format"], truth["bit_depth"]
        ).astype(np.int32)
        canonical = generate_image(
            cells[truth["image_id"]], DEFAULT_CONFIG, COMMITTED_SEED
        ).pixels.astype(np.int32)
        difference = np.abs(decoded - canonical)
        assert float(difference.mean()) <= JPEG_MEAN_ABS_TOLERANCE_DN, truth["image_id"]
        assert int(difference.max()) <= JPEG_MAX_ABS_TOLERANCE_DN, truth["image_id"]
        checked += 1
    assert checked > 0


def test_every_committed_band_clears_the_minimum_aspect_ratio(
    committed_data_dir: Path,
) -> None:
    """The delivered gold set, not just the generator, is free of circular bands.

    Read from the committed JSON rather than regenerated, so a hand-edit or a stale
    file fails here too. Ratios are checked against the floor each file records for
    itself, so the assertion cannot drift from the config it was generated under.
    """
    checked = 0
    for path in _ground_truth_files(committed_data_dir):
        truth = json.loads(path.read_text(encoding="utf-8"))
        minimum = truth["generation"]["parameters"]["band_layout"]["min_aspect_ratio"]
        assert minimum == DEFAULT_CONFIG.band_layout.min_aspect_ratio
        for band in truth["bands"]:
            ratio = band["sigma_x_px"] / band["sigma_y_px"]
            assert ratio >= minimum, f"{truth['image_id']}/{band['band_id']}: {ratio:.4f}"
            checked += 1
    assert checked > 0


def test_every_committed_image_file_exists_and_carries_the_frozen_version(
    committed_data_dir: Path,
) -> None:
    for path in _ground_truth_files(committed_data_dir):
        truth = json.loads(path.read_text(encoding="utf-8"))
        assert (committed_data_dir / truth["image_path"]).is_file()
        assert truth["synth_version"] == SYNTH_VERSION
        assert truth["generation"]["master_seed"] == COMMITTED_SEED


def test_committed_config_echo_matches_the_current_config(committed_data_dir: Path) -> None:
    """Catches a config edit that was never propagated into the committed gold set."""
    echo = json.loads((committed_data_dir / CONFIG_FILENAME).read_text(encoding="utf-8"))
    assert echo["master_seed"] == COMMITTED_SEED
    assert echo["synth_version"] == SYNTH_VERSION
    assert echo["config_digest"] == DEFAULT_CONFIG.digest()
    for path in _ground_truth_files(committed_data_dir):
        truth = json.loads(path.read_text(encoding="utf-8"))
        assert truth["generation"]["config_digest"] == DEFAULT_CONFIG.digest()


def test_committed_config_echo_records_the_axis_order_actually_used(
    committed_data_dir: Path,
) -> None:
    """The echo is the only written record of the axis definitions, order included.

    Level order is a generation parameter -- the balanced schedule assigns containers
    from ``format_depths`` in this order -- so an echo sorted alphabetically would
    describe a dataset that was never generated.
    """
    echo = json.loads((committed_data_dir / CONFIG_FILENAME).read_text(encoding="utf-8"))
    assert list(echo["config"]["matrix"]["format_depths"]) == list(
        DEFAULT_CONFIG.matrix.format_depths
    )
    for axis, levels in dataclasses.asdict(DEFAULT_CONFIG.matrix).items():
        assert list(echo["config"]["matrix"][axis]) == list(levels), axis


def test_committed_config_echo_rehashes_to_the_recorded_digest(
    committed_data_dir: Path,
) -> None:
    """Provenance must round-trip: the echoed config alone reproduces the digest."""
    echo = json.loads((committed_data_dir / CONFIG_FILENAME).read_text(encoding="utf-8"))
    payload = json.dumps(echo["config"], separators=(",", ":"))
    rehashed = "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
    assert rehashed == echo["config_digest"] == DEFAULT_CONFIG.digest()
