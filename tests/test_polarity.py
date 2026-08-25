"""Tests for the ruled polarity input (2026-08-24 amendment, Option A).

Why these exist. Polarity is the third thing this project refuses to infer, after the reference
designation and blot identity, and the refusal is the whole of the ruling: an image whose
polarity is not declared must not be measured. A refusal with no test is a default waiting to
be added by someone who finds the argument inconvenient.

Four properties, matching the four the ruling asks for:

* an undeclared image is refused, and so is a declared-but-unrecognised one;
* a declared image measures, on both sides of the vocabulary;
* a *wrongly* declared polarity changes what is measured and what QC says -- which is what
  makes the declaration load-bearing rather than decorative;
* the generator declares its own polarity, and nothing downstream supplies a default for it.

The fixtures are built here rather than taken from the gold set, because the interesting cases
are a dark-band image and a bright-band image with the *same* geometry, which the gold set does
not contain: it is bright_on_dark throughout, which is the point of the fourth property.
"""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from evals.run import GOLD_SET_POLARITY
from pipeline import RESULT_SCHEMA_VERSION
from pipeline.analyze import analyze_image
from pipeline.config import load_config
from pipeline.detect import Roi
from pipeline.errors import UnsupportedImageError
from pipeline.load import (
    BRIGHT_ON_DARK,
    CANONICAL_POLARITY,
    DARK_ON_BRIGHT,
    POLARITIES,
    invert_pixels,
    load_image,
)
from pipeline.qc import _clipped_pixel_count, is_low_dynamic_range

CONFIG = Path("configs/default.yaml")


def _bright_on_dark(width: int = 120, height: int = 80) -> np.ndarray:
    """Three bright bands in three lanes on a dark ground -- the generator's convention."""
    # Ground at exactly 0 so that the inverted fixture's ground is exactly full scale, which is
    # what makes the Phase 3b-1 saturation finding reproducible here rather than approximated.
    array = np.zeros((height, width), dtype=np.uint8)
    for lane in range(3):
        x = 20 + lane * 35
        array[30:42, x : x + 22] = 230
    return array


def _write(path: Path, array: np.ndarray) -> Path:
    Image.fromarray(array, mode="L").save(path)
    return path


@pytest.fixture
def bright_png(tmp_path: Path) -> Path:
    return _write(tmp_path / "bright.png", _bright_on_dark())


@pytest.fixture
def dark_png(tmp_path: Path) -> Path:
    """The same image inverted: identical geometry, opposite polarity."""
    return _write(tmp_path / "dark.png", 255 - _bright_on_dark())


# --- the refusal ---------------------------------------------------------------------------


@pytest.mark.parametrize("undeclared", ["", "   ", "auto", "unknown", "BRIGHT_ON_DARK", "None"])
def test_an_undeclared_polarity_is_refused(bright_png: Path, undeclared: str) -> None:
    """No default, no inference, and no near-miss accepted as a declaration."""
    with pytest.raises(UnsupportedImageError, match="is not declared"):
        load_image(bright_png, undeclared)


def test_the_refusal_names_the_vocabulary_and_the_flag(bright_png: Path) -> None:
    """A refusal a caller cannot act on is a failure with extra steps."""
    with pytest.raises(UnsupportedImageError) as raised:
        load_image(bright_png, "auto")

    message = str(raised.value)
    assert BRIGHT_ON_DARK in message and DARK_ON_BRIGHT in message
    assert "--polarity" in message
    assert "threshold" in message, "the refusal should say why it will not guess"


def test_polarity_is_refused_before_the_file_is_read(tmp_path: Path) -> None:
    """The declaration is checked first, so a missing file cannot mask a missing declaration."""
    with pytest.raises(UnsupportedImageError, match="is not declared"):
        load_image(tmp_path / "does_not_exist.png", "auto")


def test_there_is_no_third_vocabulary_value() -> None:
    """`auto` would be the forbidden heuristic wearing a vocabulary word."""
    assert POLARITIES == (BRIGHT_ON_DARK, DARK_ON_BRIGHT)
    assert CANONICAL_POLARITY == BRIGHT_ON_DARK


def test_analyze_image_has_no_polarity_default(bright_png: Path) -> None:
    """Keyword-only and required: a caller that has not decided cannot analyse."""
    with pytest.raises(TypeError, match="polarity"):
        analyze_image(bright_png, load_config(CONFIG))  # type: ignore[call-arg]


# --- a declared image measures ----------------------------------------------------------------


def test_a_declared_bright_image_is_passed_through_untouched(bright_png: Path) -> None:
    """The no-op the amendment's (d) prediction rests on: untouched, not inverted twice."""
    delivered = np.asarray(Image.open(bright_png))

    loaded = load_image(bright_png, BRIGHT_ON_DARK)

    assert np.array_equal(loaded.pixels, delivered)
    assert loaded.polarity == BRIGHT_ON_DARK


def test_a_declared_dark_image_is_inverted_to_the_canonical_convention(dark_png: Path) -> None:
    """Everything below the loader sees one convention, whatever the file was."""
    delivered = np.asarray(Image.open(dark_png))

    loaded = load_image(dark_png, DARK_ON_BRIGHT)

    assert np.array_equal(loaded.pixels, 255 - delivered)
    assert loaded.pixels.dtype == delivered.dtype
    assert loaded.polarity == DARK_ON_BRIGHT, "recorded as declared, not as applied"


def test_the_two_declarations_agree_once_the_dark_one_is_inverted(
    bright_png: Path, dark_png: Path
) -> None:
    """The fixtures differ only in polarity, so after the loader they must be identical."""
    bright = load_image(bright_png, BRIGHT_ON_DARK)
    dark = load_image(dark_png, DARK_ON_BRIGHT)

    assert np.array_equal(bright.pixels, dark.pixels)


def test_inversion_is_exact_and_its_own_inverse() -> None:
    """Integer arithmetic, no float round-trip: a clipped dark pixel lands exactly on full scale."""
    for dtype, max_value in ((np.uint8, 255), (np.uint16, 65535)):
        array = np.array([0, 1, max_value // 2, max_value - 1, max_value], dtype=dtype)
        inverted = invert_pixels(array, max_value)
        assert inverted.dtype == dtype
        assert inverted[0] == max_value and inverted[-1] == 0
        assert np.array_equal(invert_pixels(inverted, max_value), array)


def test_the_declaration_reaches_provenance(bright_png: Path) -> None:
    """A result that does not say which way its signal ran cannot be re-measured."""
    result = analyze_image(bright_png, load_config(CONFIG), polarity=BRIGHT_ON_DARK)

    assert result["source"]["polarity"] == BRIGHT_ON_DARK
    assert result["schema_version"] == RESULT_SCHEMA_VERSION


def test_the_declaration_is_hashed_into_the_result_id(dark_png: Path) -> None:
    """Two documents describing opposite declarations must not share a content address.

    **One file, both declarations.** An earlier version of this test compared two *different*
    files, whose ids differ through ``source_sha256`` whether or not polarity is hashed at all
    -- so it stayed green when the polarity input was removed from ``_result_id``, and it was
    catching nothing. Holding the path fixed is the whole of the test: ``sha256`` identifies the
    delivered bytes, so polarity is the only input that varies here.

    The failure this prevents is not cosmetic. ``api.storage.ResultStore.save`` keys on
    ``result_id``, so a shared id means one measurement silently overwrites the other -- and the
    two share no measured number, because one measures the bands and the other the gaps.
    """
    config = load_config(CONFIG)

    bright = analyze_image(dark_png, config, polarity=BRIGHT_ON_DARK)
    dark = analyze_image(dark_png, config, polarity=DARK_ON_BRIGHT)

    assert bright["source"]["sha256"] == dark["source"]["sha256"], "same file, by construction"
    assert bright["source"]["polarity"] != dark["source"]["polarity"]
    assert bright["result_id"] != dark["result_id"]


# --- a wrong declaration changes the measurement -------------------------------------------------


def test_a_wrongly_declared_polarity_changes_the_flags(dark_png: Path) -> None:
    """The property that makes the declaration load-bearing rather than decorative.

    The same dark-band file, declared both ways. Declared correctly its bands are measured;
    declared wrongly the pipeline measures the background between them, and QC says something
    different about it. If these two agreed, the declaration would not be doing anything.
    """
    config = load_config(CONFIG)

    correct = analyze_image(dark_png, config, polarity=DARK_ON_BRIGHT)
    wrong = analyze_image(dark_png, config, polarity=BRIGHT_ON_DARK)

    correct_flags = sorted({flag for band in correct["bands"] for flag in band["qc_flags"]})
    wrong_flags = sorted({flag for band in wrong["bands"] for flag in band["qc_flags"]})

    assert (correct["image_qc_flags"], correct_flags, len(correct["bands"])) != (
        wrong["image_qc_flags"],
        wrong_flags,
        len(wrong["bands"]),
    ), "a wrong declaration must not measure the same thing as a right one"


def test_a_wrong_declaration_puts_the_extremes_at_the_wrong_end(dark_png: Path) -> None:
    """The Phase 3b-1 mechanism, asserted where it lives: which end of the scale the ground is at.

    `pipeline.qc` defines clipping as pixels at *full scale*. On a light-ground file measured as
    delivered -- which is what declaring `bright_on_dark` does -- the pixels at full scale are the
    paper, and every band rectangle drawn over it contains some. Declared correctly, the paper is
    at zero and the clipping test finds nothing to fire on.

    Asserted on the pixel distribution rather than on the flags, because the flags additionally
    depend on what detection happens to find, and the claim here is about the pixels.
    """
    delivered = load_image(dark_png, BRIGHT_ON_DARK).pixels
    corrected = load_image(dark_png, DARK_ON_BRIGHT).pixels

    assert int((delivered == 255).sum()) > 0, "the wrongly-declared array's ground is at full scale"
    assert int((corrected == 255).sum()) == 0, "declared correctly, nothing sits at full scale"
    assert int((corrected == 0).sum()) == int((delivered == 255).sum()), (
        "the same pixels, moved to the end of the scale the clipping test does not look at"
    )


def test_the_clipping_test_fires_on_the_band_after_inversion_not_on_the_paper() -> None:
    """`pipeline/qc.py`'s justification paragraph, asserted rather than left readable.

    The paragraph claims that a dark band clipped to 0 in the delivered file lands exactly on
    ``max_value`` after an exact integer inversion, so ``_clipped_pixel_count``'s
    ``>= max_value`` fires on the band instead of on the paper. A reviewer flipped that sentence
    to say the opposite and nothing in the suite noticed, which is how a wrong DEBT entry got
    written from it. This is the assertion that would have caught both.

    The fixture is the narrow-container case from DEBT S21 -- 12-bit data in a uint16 container,
    the archetypal ``dark_on_bright`` source -- because that is where the arithmetic is least
    obvious and where the first version of S21 got it backwards.
    """
    max_value = 65535
    delivered = np.full((20, 20), 4000, dtype=np.uint16)
    delivered[8:12, 8:12] = 300
    delivered[9:11, 9:11] = 0  # a band core clipped at the low end, as a dark band clips

    measured = invert_pixels(delivered, max_value)
    background = Roi(x=0, y=0, width=5, height=5)
    band = Roi(x=8, y=8, width=4, height=4)

    assert _clipped_pixel_count(measured, background, max_value) == 0, (
        "the paper must contribute no clipped pixels: inverted it sits 4000 DN below full scale"
    )
    assert _clipped_pixel_count(measured, band, max_value) == 4, (
        "the genuinely clipped band core must be exactly at full scale after inversion"
    )
    assert int(measured[0, 0]) == max_value - 4000
    assert int(measured[9, 9]) == max_value


@pytest.mark.parametrize(
    ("label", "ground", "band_value", "polarity", "expect_clipped"),
    [
        ("bright_on_dark clipped at the 12-bit white point", 0, 4095, BRIGHT_ON_DARK, 0),
        ("dark_on_bright clipped at the container floor", 4095, 0, DARK_ON_BRIGHT, 16),
        ("dark_on_bright clipped at a black point of 100", 4095, 100, DARK_ON_BRIGHT, 0),
    ],
)
def test_the_container_width_limit_is_polarity_dependent_for_saturated(
    label: str, ground: int, band_value: int, polarity: str, expect_clipped: int
) -> None:
    """DEBT S21's table, pinned, because its prose has now been wrong twice.

    12-bit data in a ``uint16`` container. ``saturated`` counts pixels at the *container's* full
    scale, so whether a genuinely clipped band is noticed depends on where the clip lands after
    the declared polarity is applied — which is **not** polarity-independent, though an earlier
    version of S21 said it was. The one case that works is the one where the data's floor
    coincides with the container's, so the inversion puts it exactly on the ceiling.
    """
    max_value = 65535
    delivered = np.full((20, 20), ground, dtype=np.uint16)
    delivered[8:12, 8:12] = band_value

    measured = delivered if polarity == BRIGHT_ON_DARK else invert_pixels(delivered, max_value)
    band = Roi(x=8, y=8, width=4, height=4)

    assert _clipped_pixel_count(measured, band, max_value) == expect_clipped, label


def test_the_container_width_limit_is_polarity_independent_for_dynamic_range() -> None:
    """The other half of S21, and this one really is the same in both directions."""
    config = load_config(CONFIG).qc
    twelve_bit_peak = 4095.0

    assert is_low_dynamic_range(twelve_bit_peak, 65535, config), (
        "a 12-bit peak is 4095 against a threshold of 0.25 * 65535 = 16384, so the flag fires "
        "on every narrow-container file whatever its real range -- in either polarity, because "
        "the peak height is unchanged by an inversion"
    )


# --- the CLI and the API both require it ----------------------------------------------------


def test_the_cli_refuses_to_run_without_the_flag(bright_png: Path, tmp_path: Path) -> None:
    """argparse's own failure, so an omitted flag cannot become a downstream None check."""
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pipeline",
            "run",
            str(bright_png),
            "--config",
            str(CONFIG),
            "--out",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "--polarity" in completed.stderr
    assert not list((tmp_path / "out").glob("*.json"))


def test_the_cli_records_the_declaration_it_was_given(dark_png: Path, tmp_path: Path) -> None:
    """End to end through the documented path, on the side of the vocabulary that inverts."""
    out = tmp_path / "out"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pipeline",
            "run",
            str(dark_png),
            "--config",
            str(CONFIG),
            "--out",
            str(out),
            "--polarity",
            DARK_ON_BRIGHT,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    document = json.loads((out / f"{dark_png.stem}.json").read_text())
    assert document["source"]["polarity"] == DARK_ON_BRIGHT


def test_the_cli_refuses_a_value_outside_the_vocabulary(bright_png: Path, tmp_path: Path) -> None:
    """`--polarity auto` is the one a user would most plausibly try."""
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pipeline",
            "run",
            str(bright_png),
            "--config",
            str(CONFIG),
            "--out",
            str(tmp_path / "out"),
            "--polarity",
            "auto",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "auto" in completed.stderr


# --- the generator declares its own ------------------------------------------------------------


def test_the_gold_set_declaration_is_the_generators_own_convention() -> None:
    """The value is read out of the generator, not chosen by the harness that passes it."""
    assert GOLD_SET_POLARITY == BRIGHT_ON_DARK
    assert GOLD_SET_POLARITY in POLARITIES


def test_the_gold_set_declaration_matches_what_the_generator_documents() -> None:
    """Three statements had to agree before the constant was written; they still must.

    This is what keeps the declaration honest without editing the frozen package: if what the
    generator produces ever changes, these three move and this test fails before a gold-set
    figure is re-measured under a stale declaration.
    """
    render = " ".join(Path("synth/render.py").read_text().split())
    models = " ".join(Path("synth/MODELS.md").read_text().split())
    generator = " ".join(Path("synth/generator.py").read_text().split())

    assert "signal-positive (bright bands on a dark background" in render
    assert "Signal is positive on a dark background" in models
    assert "signal = background + sum(band_layers)" in generator


def test_the_declaration_lives_where_the_ratified_amendment_puts_it() -> None:
    """The amendment's (b) forecloses the frozen package; it does not choose between the rest.

    It names a per-image ground-truth field or a constant the eval harness passes, calls the
    choice between them "an implementation question for (c), not a further ruling", and says of
    ``synth/`` that "none is proposed here". So this test pins the *exclusion*, which is ruled,
    and not the selection, which is not. It exists because an earlier implementation put the
    constant in ``synth/__init__.py``, and nothing mechanical would otherwise have caught it.
    """
    amendment = Path("data/real/AMENDMENT_2026-08-24_polarity.md").read_text()

    assert "none is proposed here" in amendment
    assert "a constant the eval harness passes" in amendment
    assert "POLARITY" not in Path("synth/__init__.py").read_text()


def test_the_gold_set_measures_as_the_declaration_says() -> None:
    """Corroboration on the pixels, over the WHOLE gold set, with the quoted figures asserted.

    Every file, not a ``*.png`` glob: the docstring on ``GOLD_SET_POLARITY`` quotes figures for
    40 files and an earlier version quoted figures for 16 while calling them the gold set. The
    bounds below are loose enough to survive a re-render and tight enough that a polarity flip
    or a scope narrowing fails them.
    """
    paths = sorted(p for p in Path("data/images").glob("*") if p.is_file())
    fractions, at_max, at_zero, total = [], 0, 0, 0
    for path in paths:
        loaded = load_image(path, GOLD_SET_POLARITY)
        pixels = loaded.pixels.astype(np.int64)
        fractions.append(float(np.median(pixels)) / loaded.max_value)
        at_max += int((pixels >= loaded.max_value).sum())
        at_zero += int((pixels <= 0).sum())
        total += pixels.size

    assert len(paths) == 40, f"the gold set is 40 files, found {len(paths)}"
    assert 0.07 < min(fractions) and max(fractions) < 0.15, (
        f"gold-set medians {min(fractions):.4f}-{max(fractions):.4f} must sit at the dark end; "
        f"anything above 0.5 would contradict the bright_on_dark declaration outright"
    )
    assert 0.05 < 100 * at_max / total < 0.2, "the bright tail is a tail, not the ground"
    assert 100 * at_zero / total < 0.01, "and the dark end is where the ground is"


def test_the_harness_declares_the_gold_set_once() -> None:
    """One constant, imported by the sweep rather than restated, so the two cannot drift."""
    run = Path("evals/run.py").read_text()
    sweep = Path("evals/sweep.py").read_text()

    assert "GOLD_SET_POLARITY = BRIGHT_ON_DARK" in run
    assert "GOLD_SET_POLARITY" in sweep
    assert '"bright_on_dark"' not in sweep, "the sweep must import the value, not restate it"


def test_no_pipeline_entry_point_supplies_a_polarity_default(tmp_path: Path) -> None:
    """The refusal is only as good as the absence of a default one import away.

    Checked with :func:`inspect.signature` rather than by grepping for a spelling, because
    ``polarity: str=BRIGHT_ON_DARK`` slips past a string search and is the same defect.

    Scoped to the pipeline's own entry points and the service in front of them. A *tool* that
    declares its corpus is a caller declaring, which is what the amendment requires -- but it
    must not default either, which `test_the_real_data_runner_requires_its_declaration` checks
    one layer up.
    """
    for target in (load_image, analyze_image):
        parameter = inspect.signature(target).parameters["polarity"]
        assert parameter.default is inspect.Parameter.empty, (
            f"{target.__name__} defaults the polarity"
        )

    assert inspect.signature(analyze_image).parameters["polarity"].kind is (
        inspect.Parameter.KEYWORD_ONLY
    )
    # The API leg is asserted on the OpenAPI document rather than on the source text: a
    # FastAPI default takes the shape `polarity: Annotated[str, Form(...)] = "bright_on_dark"`,
    # which a string search for `polarity=` does not see, and `required` is where it shows.
    from api.app import create_app

    app = create_app(storage_root=tmp_path, config_dir=Path("configs"))
    schema = app.openapi()["paths"]["/analyze"]["post"]["requestBody"]["content"]
    body = next(iter(schema.values()))["schema"]
    required = body.get("required") or []
    if "$ref" in str(body):
        name = str(body["$ref"] if "$ref" in body else body).rsplit("/", 1)[-1].rstrip("'\"}")
        required = app.openapi()["components"]["schemas"][name].get("required", [])
    assert "polarity" in required, f"polarity must be a required form field, saw {required}"


def test_the_real_data_runner_requires_its_declaration() -> None:
    """The corpus is a flag, so its polarity must be one too, or the two can drift.

    `--crops-dir` and `--crop-log` are themselves flags with defaults. A default polarity would
    let the tool be pointed at another directory while still asserting that those images are
    white-ground published figures.
    """
    from tools.phase3.run_real import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--crop-log", "nowhere.csv"])
