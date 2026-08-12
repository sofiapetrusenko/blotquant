"""Every sweep figure quoted in NOTES.md or configs/*.yaml must come from the sweep record.

Three review cycles found stale dev-split figures in those two files, every time because a
parameter moved and the transcribed surfaces were patched by hand. ``evals/sweep.py``
regenerates ``evals/dev_sweeps.json``; this module closes the other half of the loop by
checking the transcription mechanically, so a hand-edited or forgotten figure fails
``pytest`` rather than surviving to a reviewer.

The convention it enforces
--------------------------
A block of transcribed figures is introduced by a marker naming the sweep it came from:

* in YAML, a comment line ``# sweep: <name>`` up to a ``# end sweep`` comment;
* in Markdown, ``<!-- sweep: <name> -->`` up to ``<!-- end sweep -->``.

Both are explicitly terminated so that prose may sit between two blocks without being read
as figures, and an unterminated block is an error rather than a block that quietly swallows
everything after it.

Inside a block every figure must be one the record holds for **the specific value it is
presented against** -- attribution, not mere membership. An earlier version of this checker
unioned every field of every value in the sweep, so swapping two values' figures inside one
block passed silently, and a transposition is precisely the hand-transcription error the
convention exists to catch. Three block shapes are understood, tried in this order:

1. **Single value.** One value of the sweep accounts for every figure in the block. Used
   for prose and for a table describing one configuration.
2. **Markdown table.** Either a *label row* (every populated cell is one value label)
   followed by rows whose k-th cell must hold the k-th label's figures, or rows that begin
   with a label cell and continue with that label's own figures.
3. **Free-form, nearest label.** Each figure is attributed to the nearest value label
   appearing before it on the same line. This is the shape the config comments use --
   ``0.06 -> 0.8506`` pairs, and whitespace-aligned rows whose first column is the value.

A figure that cannot be attributed at all is an error, as is a row with more cells than the
label row has labels. Prose therefore lives *outside* the markers: blocks hold figures only.

This checks transcription, not truth: ``python -m evals.sweep --check`` is what verifies
that the record itself still reproduces from the committed gold set.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from evals.sweep import F1_DECIMALS, JSON_PATH, MARKDOWN_PATH, PERCENT_DECIMALS

REPO_ROOT = Path(__file__).resolve().parent.parent
NOTES_PATH = REPO_ROOT / "NOTES.md"
CONFIG_PATHS = (
    REPO_ROOT / "configs" / "default.yaml",
    REPO_ROOT / "configs" / "rolling_ball.yaml",
)

NUMBER = re.compile(r"(?<![A-Za-z0-9._])-?\d+(?:\.\d+)?(?![A-Za-z0-9]*[A-Za-z])")
"""Standalone numbers only: the ``1`` of ``F1`` and the ``51`` of ``51x51`` are parts of
words rather than figures, and must not be checked as though they were."""

YAML_MARKER = re.compile(r"^\s*#\s*sweep:\s*(\S+)\s*$")
YAML_END = re.compile(r"^\s*#\s*end sweep\s*$")
MARKDOWN_MARKER = re.compile(r"^\s*<!--\s*sweep:\s*(\S+)\s*-->\s*$")
MARKDOWN_END = re.compile(r"^\s*<!--\s*end sweep\s*-->\s*$")

PHASE_1_HEADING = "## Phase 1 — core pipeline"
NEXT_HEADING = "## Open items"

MINIMUM_MARKED_BLOCKS = 5
"""A silently empty checker would pass forever, so require the convention to be in use."""


@pytest.fixture(scope="module")
def record() -> dict[str, object]:
    """Return the committed sweep record."""
    path = REPO_ROOT / JSON_PATH
    if not path.is_file():
        raise FileNotFoundError(
            f"no sweep record at {path}; regenerate it with 'python -m evals.sweep'"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _value_tokens(value: dict[str, object]) -> set[str]:
    """Return every string a figure of this one recorded value is allowed to be."""
    allowed: set[str] = set()
    for field, recorded in value.items():
        if field == "label" or isinstance(recorded, bool):
            continue
        if isinstance(recorded, int):
            allowed.add(str(recorded))
        elif isinstance(recorded, float):
            decimals = PERCENT_DECIMALS if field.endswith("percent") else F1_DECIMALS
            allowed.add(f"{recorded:.{decimals}f}")
            allowed.add(f"{recorded:g}")
    return allowed


def _labelled_values(sweep: dict[str, object]) -> dict[str, dict[str, object]]:
    """Return the sweep's values keyed by their label, as strings."""
    values: dict[str, dict[str, object]] = {}
    for value in sweep["values"]:  # type: ignore[union-attr]
        values[str(value["label"])] = value
    return values


def _label_positions(line: str, labels: list[str]) -> list[tuple[int, str]]:
    """Return ``(position, label)`` for every value label occurring in ``line``, in order.

    A numeric label counts only where it is the whole of a standalone number, so ``0.1``
    does not match inside ``0.15``.
    """
    found: list[tuple[int, str]] = []
    for label in labels:
        if NUMBER.fullmatch(label):
            found += [
                (match.start(), label)
                for match in NUMBER.finditer(line)
                if match.group() == label
            ]
        else:
            start = line.find(label)
            while start != -1:
                found.append((start, label))
                start = line.find(label, start + 1)
    return sorted(found)


def _attribute_free_form(line: str, values: dict[str, dict[str, object]]) -> list[str]:
    """Attribute each figure on ``line`` to the nearest value label before it."""
    labels = _label_positions(line, list(values))
    label_starts = {position for position, _ in labels}
    problems: list[str] = []
    for match in NUMBER.finditer(line):
        if match.start() in label_starts and match.group() in values:
            continue
        preceding = [label for position, label in labels if position < match.start()]
        if not preceding:
            problems.append(
                f"{match.group()!r} on {line.strip()[:56]!r} has no value label before it, "
                f"so it cannot be attributed to one"
            )
            continue
        label = preceding[-1]
        if match.group() not in _value_tokens(values[label]) | {label}:
            problems.append(
                f"{match.group()!r} follows the label {label} but is not one of its figures"
            )
    return problems


def _head_label(text: str, values: dict[str, dict[str, object]]) -> str | None:
    """Return the value label a table row's first cell names, if it names one.

    Numeric labels must be the cell's only figure; textual labels (``default.yaml``,
    ``unflagged``) are recognised as substrings, so a row header may read
    ``` `unflagged` ``` and still identify its record row.
    """
    figures = NUMBER.findall(text)
    if len(figures) == 1 and figures[0] in values:
        return figures[0]
    named = [
        label
        for label in values
        if not NUMBER.fullmatch(label) and label in text
    ]
    return named[0] if len(named) == 1 else None


def _attribute_table_row(
    line: str, values: dict[str, dict[str, object]], order: list[str] | None
) -> tuple[list[str], list[str] | None]:
    """Attribute one Markdown table row; return its problems and any label order it declares."""
    texts = line.strip().strip("|").split("|")
    cells = [NUMBER.findall(text) for text in texts]
    populated = [cell for cell in cells if cell]
    if not populated:
        return [], order
    if len(populated) >= 2 and all(
        len(cell) == 1 and cell[0] in values for cell in populated
    ):
        return [], [cell[0] for cell in populated]

    label = _head_label(texts[0], values) if texts else None
    if label is not None:
        allowed = _value_tokens(values[label]) | {label}
        return (
            [
                f"{token!r} is on the {label} row but is not one of its figures"
                for cell in cells
                for token in cell
                if token not in allowed
            ],
            order,
        )
    if order is None:
        return [f"row {line.strip()[:56]!r} quotes figures with no label row above it"], None
    rest = cells[1:]
    if len(rest) > len(order):
        return [f"row {line.strip()[:56]!r} has {len(rest)} cells for {len(order)} labels"], order
    problems: list[str] = []
    for column, cell in zip(order, rest, strict=False):
        allowed = _value_tokens(values[column]) | {column}
        problems += [
            f"{token!r} is in the {column} column but is not one of its figures"
            for token in cell
            if token not in allowed
        ]
    return problems, order


def _attribute(block: list[str], sweep: dict[str, object]) -> list[str]:
    """Return one message per figure in ``block`` the record does not attribute that way."""
    values = _labelled_values(sweep)
    figures = [token for line in block for token in NUMBER.findall(line)]
    if not figures:
        return []
    for label, value in values.items():
        if all(token in _value_tokens(value) | {label} for token in figures):
            return []

    problems: list[str] = []
    order: list[str] | None = None
    for line in block:
        if not NUMBER.findall(line):
            continue
        if line.strip().lstrip("#").strip().startswith("|"):
            found, order = _attribute_table_row(line, values, order)
            problems += found
        else:
            problems += _attribute_free_form(line, values)
    return problems


def _yaml_blocks(text: str) -> list[tuple[str, list[str]]]:
    """Return ``(sweep name, figure lines)`` for every marked block in a YAML file."""
    blocks: list[tuple[str, list[str]]] = []
    name: str | None = None
    figures: list[str] = []
    for line in text.splitlines():
        marker = YAML_MARKER.match(line)
        if marker is not None:
            if name is not None:
                raise AssertionError(f"sweep block {name!r} is not terminated by '# end sweep'")
            name, figures = marker.group(1), []
            continue
        if YAML_END.match(line):
            if name is None:
                raise AssertionError("'# end sweep' without a matching '# sweep:' marker")
            blocks.append((name, figures))
            name = None
            continue
        if name is None:
            continue
        if not line.lstrip().startswith("#"):
            raise AssertionError(
                f"sweep block {name!r} reached a non-comment line before '# end sweep'"
            )
        figures.append(line)
    if name is not None:
        raise AssertionError(f"sweep block {name!r} is not terminated by '# end sweep'")
    return blocks


def _markdown_blocks(text: str) -> list[tuple[str, list[str]]]:
    """Return ``(sweep name, figure lines)`` for every marked block in a Markdown file."""
    blocks: list[tuple[str, list[str]]] = []
    name: str | None = None
    figures: list[str] = []
    for line in text.splitlines():
        marker = MARKDOWN_MARKER.match(line)
        if marker is not None:
            if name is not None:
                raise AssertionError(f"nested sweep marker for {marker.group(1)!r}")
            name, figures = marker.group(1), []
            continue
        if MARKDOWN_END.match(line):
            if name is None:
                raise AssertionError("'end sweep' without a matching sweep marker")
            blocks.append((name, figures))
            name = None
            continue
        if name is not None:
            figures.append(line)
    if name is not None:
        raise AssertionError(f"unterminated sweep block for {name!r}")
    return blocks


def _phase_1_section(text: str) -> str:
    """Return NOTES.md's Phase 1 section."""
    start = text.index(PHASE_1_HEADING)
    return text[start : text.index(NEXT_HEADING, start)]


def _check(blocks: list[tuple[str, list[str]]], record: dict[str, object], where: str) -> list[str]:
    """Return one message per figure in ``blocks`` the record does not attribute that way."""
    sweeps = record["sweeps"]  # type: ignore[index]
    problems: list[str] = []
    for name, figures in blocks:
        if name not in sweeps:
            problems.append(f"{where}: no sweep named {name!r} in the record")
            continue
        problems += [
            f"{where}: in the {name} block, {message}. Re-run 'python -m evals.sweep' and "
            f"copy from {MARKDOWN_PATH}"
            for message in _attribute(figures, sweeps[name])
        ]
    return problems


def test_at_least_one_block_is_marked_in_each_file(record: dict[str, object]) -> None:
    """The convention has to actually be in use, or this module guards nothing."""
    for path in CONFIG_PATHS:
        assert _yaml_blocks(path.read_text(encoding="utf-8")), f"{path.name} marks no sweep block"
    section = _phase_1_section(NOTES_PATH.read_text(encoding="utf-8"))
    assert len(_markdown_blocks(section)) >= MINIMUM_MARKED_BLOCKS


@pytest.mark.parametrize("path", CONFIG_PATHS, ids=lambda path: path.name)
def test_config_figures_come_from_the_sweep_record(path: Path, record: dict[str, object]) -> None:
    """Every figure a config comment quotes is the record's, for the value it sits against."""
    problems = _check(_yaml_blocks(path.read_text(encoding="utf-8")), record, path.name)

    assert problems == [], "\n".join(problems)


def test_notes_figures_come_from_the_sweep_record(record: dict[str, object]) -> None:
    """Same for NOTES.md's Phase 1 section."""
    section = _phase_1_section(NOTES_PATH.read_text(encoding="utf-8"))

    problems = _check(_markdown_blocks(section), record, "NOTES.md")

    assert problems == [], "\n".join(problems)


def test_a_within_sweep_transposition_is_caught(record: dict[str, object]) -> None:
    """Swapping two values' figures inside one block must fail, not merely be a member.

    This is the hole cycle 4 found in the previous checker, pinned here so it cannot
    reopen: the union-of-all-fields test this replaced passed the swap below.
    """
    sweep = record["sweeps"]["band.extent_relative_height"]  # type: ignore[index]
    values = _labelled_values(sweep)
    tight, loose = values["0.06"], values["0.08"]
    honest = [
        "| `h` | 0.06 | 0.08 |",
        f"| band F1 | {tight['band_f1']:.4f} | {loose['band_f1']:.4f} |",
    ]
    transposed = [
        "| `h` | 0.06 | 0.08 |",
        f"| band F1 | {loose['band_f1']:.4f} | {tight['band_f1']:.4f} |",
    ]

    assert _attribute(honest, sweep) == []
    assert _attribute(transposed, sweep) != []


def test_a_figure_from_another_sweep_is_caught(record: dict[str, object]) -> None:
    """A figure copied out of the wrong sweep fails even if it is a real measurement."""
    sweeps = record["sweeps"]  # type: ignore[index]
    other = _labelled_values(sweeps["band.extent_min_sigma"])["2.5"]
    block = [f"#   0.06 -> {other['band_f1']:.4f}"]

    assert _attribute(block, sweeps["band.extent_relative_height"]) != []


def test_an_unattributable_figure_is_caught(record: dict[str, object]) -> None:
    """A figure with no value label before it cannot be checked, so it is rejected.

    Two figures from two different values, neither line naming which -- so the
    single-value shortcut cannot apply and there is nothing to attribute them to.
    """
    sweeps = record["sweeps"]  # type: ignore[index]

    problems = _attribute(
        ["#   band F1 was 0.8506", "#   or perhaps 0.7847"], sweeps["shipped_configs"]
    )

    assert problems != []


def test_the_record_describes_the_dev_split_only(record: dict[str, object]) -> None:
    """Split discipline, asserted on the artifact and not only on the code that wrote it."""
    assert record["split"] == "dev"
    assert record["images"] == 30
    assert record["iou_threshold"] == 0.5


def test_the_record_matches_the_shipped_config_digests(record: dict[str, object]) -> None:
    """A record generated from different parameters than the shipped ones is stale."""
    from pipeline.config import load_config

    shipped = record["shipped"]  # type: ignore[index]
    assert shipped["default"] == load_config(REPO_ROOT / "configs" / "default.yaml").digest()
    assert shipped["rolling_ball"] == load_config(
        REPO_ROOT / "configs" / "rolling_ball.yaml"
    ).digest()


def test_the_sweep_tool_cannot_be_pointed_at_another_split() -> None:
    """Split discipline for the sweep harness: it takes no path and no split argument."""
    from evals.sweep import build_parser

    options = {
        option for action in build_parser()._actions for option in action.option_strings
    }

    assert options == {"-h", "--help", "--check"}
