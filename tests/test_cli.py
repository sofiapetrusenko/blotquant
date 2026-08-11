"""CLI contract: ``python -m synth --seed 42 --out data/``."""

from __future__ import annotations

from pathlib import Path

import pytest

from synth.__main__ import build_parser, main


def test_seed_and_out_are_required() -> None:
    """No implicit seed and no implicit output directory."""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--out", "data/"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--seed", "42"])


def test_cli_generates_a_dataset(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--seed", "42", "--out", str(tmp_path)]) == 0
    captured = capsys.readouterr().out
    assert "wrote 40 images (dev=30, test=10)" in captured
    assert len(list((tmp_path / "ground_truth").glob("*.json"))) == 40
    assert len(list((tmp_path / "images").iterdir())) == 40


def test_cli_reports_a_gold_set_clash_on_stderr_and_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--seed", "42", "--out", str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["--seed", "43", "--out", str(tmp_path)]) == 1
    assert "--force" in capsys.readouterr().err
    assert main(["--seed", "43", "--out", str(tmp_path), "--force"]) == 0
    assert "left by an earlier generation" in capsys.readouterr().out
