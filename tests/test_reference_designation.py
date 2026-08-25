"""``--reference-band`` and the provenance of the designation that fills it (DEBT S6/D4).

Two rules are asserted here, and they are the two halves of the same rule. **A designated
reference produces a ratio**, and the designation's origin travels with it into provenance.
**An undesignated one produces no ratio and says so** -- it does not fall back to another
normalization mode, and it does not pick a band that looks like a loading control. Which band
is the loading control is not visible in the pixels (Phase 2 human ruling, §2 of the
pre-registration: "Guessing the loading control from the data is forbidden"), so the failure
mode this file exists to catch is a *quiet success*: a run that reports a ratio nobody
designated a denominator for.

``reference_designation_source`` is free text and the pipeline never parses it. That is
deliberate: the vocabulary of designation states belongs to whoever is doing the designating,
not to the pipeline, and a pipeline that recognised ``confirmed`` as a keyword would be one
step from acting on it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cv2
import pytest
import yaml
from jsonschema import Draft202012Validator

from pipeline.__main__ import main
from pipeline.analyze import analyze_image
from pipeline.config import load_config
from pipeline.errors import ReferenceBandError
from pipeline.load import BRIGHT_ON_DARK
from tests.conftest import Blot
from tests.test_pipeline_config import CONFIG_DIR
from tests.test_pipeline_result import _schema

PARSED_PENDING = "designations.csv:parsed_pending_human"
"""A source string in the state W5 leaves every row in: proposed, not confirmed."""


@pytest.fixture
def blot_png(tmp_path: Path, three_lane_blot: Blot) -> Path:
    """Write the three-lane fixture as a PNG and return its path."""
    path = tmp_path / "fixture_blot.png"
    assert cv2.imwrite(str(path), three_lane_blot.pixels)
    return path


@pytest.fixture
def housekeeping_config(tmp_path: Path) -> Path:
    """Return the shipped config with the normalization mode set to housekeeping_single."""
    mapping = yaml.safe_load((CONFIG_DIR / "default.yaml").read_text(encoding="utf-8"))
    mapping["normalization"]["mode"] = "housekeeping_single"
    path = tmp_path / "housekeeping_single.yaml"
    path.write_text(yaml.safe_dump(mapping), encoding="utf-8")
    return path


REFERENCES = ["L0_B1", "L1_B1", "L2_B1"]


def test_a_designated_reference_produces_ratios_and_records_its_source(
    blot_png: Path, housekeeping_config: Path
) -> None:
    """The designated half: ratios exist, and provenance says where the designation came from."""
    result = analyze_image(
        blot_png,
        load_config(housekeeping_config),
        reference_band_ids=REFERENCES,
        reference_designation_source=PARSED_PENDING,
        polarity=BRIGHT_ON_DARK,
    )

    normalization = result["normalization"]
    assert normalization["reference_band_ids"] == REFERENCES
    assert normalization["reference_designation_source"] == PARSED_PENDING
    assert normalization["ratios"], "a designated reference must produce ratios"
    assert Draft202012Validator(_schema()).is_valid(result)


def test_an_undesignated_reference_produces_no_ratio_and_refuses_to_guess(
    blot_png: Path, housekeeping_config: Path
) -> None:
    """The undesignated half: it raises, and the message says why rather than choosing a band.

    Asserted on the message as well as the class. "It raised" would also be satisfied by a
    pipeline that raised for some unrelated reason; what matters is that the refusal is the
    refusal to infer.
    """
    with pytest.raises(ReferenceBandError) as raised:
        analyze_image(blot_png, load_config(housekeeping_config), polarity=BRIGHT_ON_DARK)

    message = str(raised.value)
    assert "not visible in the pixels" in message
    assert "will not fall back to another mode" in message


def test_the_source_is_refused_when_it_describes_no_designation(
    blot_png: Path, tmp_path: Path
) -> None:
    """A designation source with no designation is a provenance record about nothing.

    Under ``total_protein`` there is no reference band at all, so a source naming one would
    put a claim into provenance that no field of the document supports.
    """
    with pytest.raises(ReferenceBandError, match="nothing designated"):
        analyze_image(
            blot_png,
            load_config(CONFIG_DIR / "default.yaml"),
            reference_designation_source=PARSED_PENDING,
            polarity=BRIGHT_ON_DARK,
        )


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_a_blank_source_is_refused_rather_than_recorded(
    blot_png: Path, housekeeping_config: Path, blank: str
) -> None:
    """An empty source records that an origin exists while naming none. That is worse than none."""
    with pytest.raises(ReferenceBandError, match="empty"):
        analyze_image(
            blot_png,
            load_config(housekeeping_config),
            reference_band_ids=REFERENCES,
            reference_designation_source=blank,
            polarity=BRIGHT_ON_DARK,
        )


def test_the_source_is_absent_rather_than_null_when_it_was_not_given(
    blot_png: Path, housekeeping_config: Path
) -> None:
    """Absence says the origin was not stated. It must not be readable as confirmation."""
    result = analyze_image(
        blot_png, load_config(housekeeping_config), reference_band_ids=REFERENCES,
        polarity=BRIGHT_ON_DARK,
    )

    assert "reference_designation_source" not in result["normalization"]


def test_the_result_id_covers_the_designation_source(
    blot_png: Path, housekeeping_config: Path
) -> None:
    """Two documents differing only in the designation's origin are two documents.

    Otherwise a result labelled as a human's confirmation and one labelled as a parser's
    proposal share an id, and the API serves either in answer to a request for the other.
    """
    config = load_config(housekeeping_config)
    proposed = analyze_image(
        blot_png,
        config,
        reference_band_ids=REFERENCES,
        reference_designation_source=PARSED_PENDING,
        polarity=BRIGHT_ON_DARK,
    )
    confirmed = analyze_image(
        blot_png,
        config,
        reference_band_ids=REFERENCES,
        reference_designation_source="figure caption, confirmed by the human",
        polarity=BRIGHT_ON_DARK,
    )
    unstated = analyze_image(
        blot_png,
        config,
        reference_band_ids=REFERENCES,
        polarity=BRIGHT_ON_DARK,
    )
    repeated = analyze_image(
        blot_png,
        config,
        reference_band_ids=REFERENCES,
        reference_designation_source=PARSED_PENDING,
        polarity=BRIGHT_ON_DARK,
    )

    assert len({proposed["result_id"], confirmed["result_id"], unstated["result_id"]}) == 3
    assert proposed["result_id"] == repeated["result_id"], "still content-addressed"


def test_the_cli_plumbs_the_designation_and_its_source_end_to_end(
    blot_png: Path, housekeeping_config: Path, tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The whole path: flags on the command line, ratios and the source in the written file."""
    out = tmp_path / "out"
    argv = ["run", str(blot_png), "--config", str(housekeeping_config), "--out", str(out),
        "--polarity", BRIGHT_ON_DARK,
    ]
    for band_id in REFERENCES:
        argv += ["--reference-band", band_id]
    argv += ["--reference-designation-source", PARSED_PENDING]

    assert main(argv) == 0

    document: dict[str, Any] = json.loads((out / f"{blot_png.stem}.json").read_text())
    assert document["normalization"]["reference_designation_source"] == PARSED_PENDING
    assert document["normalization"]["ratios"]
    assert PARSED_PENDING in capsys.readouterr().out


def test_the_cli_refuses_a_source_without_a_designation(
    blot_png: Path, housekeeping_config: Path, tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Naming an origin without naming a reference exits non-zero and writes nothing."""
    out = tmp_path / "out"

    code = main(
        ["run", str(blot_png),
            "--config", str(housekeeping_config),
            "--out", str(out),
            "--reference-designation-source", PARSED_PENDING,
            "--polarity",
            BRIGHT_ON_DARK,
        ]
    )

    assert code == 1
    assert "error:" in capsys.readouterr().err
    assert not out.exists() or not list(out.iterdir())
