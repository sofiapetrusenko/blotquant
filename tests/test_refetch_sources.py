"""The recovery tool's exit contract, and the TLS decision behind its fetches.

The contract exists because of a real incident: the first networked run reported
``0 of 8 verified`` and the operator read the exit status as 0. The status was in fact 1 --
the 0 came from ``$?`` after a shell pipe, which reports the last command in the pipeline --
but the episode is the reason the contract is pinned rather than assumed. A recovery tool that
can report total failure alongside a success code is worse than no tool, because a caller or a
CI step reads it as fine.

Nothing here touches the network: every test substitutes :func:`refetch_sources.fetch`.
"""

from __future__ import annotations

import csv
import hashlib
import ssl
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools" / "gate2"))

import refetch_sources as tool  # noqa: E402

SOURCES = REPO_ROOT / "data" / "real" / "sources.csv"


def _rows(committed: str) -> list[dict[str, str]]:
    """Return the manifest rows whose ``committed`` column equals ``committed``."""
    with SOURCES.open(encoding="utf-8") as handle:
        return [row for row in csv.DictReader(handle) if row["committed"] == committed]


def _page(rows: list[dict[str, str]], pmcid: str) -> bytes:
    """Return a stand-in article page declaring every graphic that article contributes.

    A real page lists all of an article's figures, and six of the 21 sources share a PMCID with
    another, so a stub that emitted one graphic per article would fail rows for reasons that
    have nothing to do with the tool.
    """
    tags = "".join(
        f'<img src="https://cdn.ncbi.nlm.nih.gov/pmc/blobs/x/{pmcid}/y/{row["graphic_href"]}">'
        for row in rows
        if row["pmcid"] == pmcid
    )
    return f"<html>{tags}</html>".encode()


def _serving(
    rows: list[dict[str, str]], payloads: dict[str, bytes]
) -> Callable[..., bytes]:
    """Return a ``fetch`` stand-in serving article pages and the given graphic bytes."""

    def fetch(url: str, referer: str | None = None, retries: int = 3) -> bytes:
        if "/articles/" in url:
            return _page(rows, url.rstrip("/").rsplit("/", 1)[-1])
        return payloads[url.rsplit("/", 1)[-1].lower()]

    return fetch


@pytest.fixture
def committed_rows() -> list[dict[str, str]]:
    """Return the manifest rows whose source figure is committed to this repository."""
    return _rows("yes")


def test_a_run_where_every_fetch_fails_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Total failure must not be reported with a success code.

    This is the incident case: every article page unreachable, nothing verified. The summary
    line and the exit status are required to agree, because they are the two things a caller
    reads and a disagreement between them is what made the original run look successful.
    """

    def always_fails(url: str, referer: str | None = None, retries: int = 3) -> bytes:
        raise RuntimeError(f"could not fetch {url}: simulated network failure")

    monkeypatch.setattr(tool, "fetch", always_fails)

    exit_code = tool.main(["--only", "uncommitted"])

    captured = capsys.readouterr().out
    assert exit_code != 0, "a run that verified nothing must not exit 0"
    assert "0 of 8 verified" in captured
    assert captured.count("FAIL ") == 8


def test_a_fully_successful_run_exits_zero(
    monkeypatch: pytest.MonkeyPatch, committed_rows: list[dict[str, str]]
) -> None:
    """The contract's other direction: everything verified is the only route to 0."""
    payloads = {
        row["graphic_href"].lower(): (
            REPO_ROOT / "data" / "real" / "images" / row["file"]
        ).read_bytes()
        for row in committed_rows
    }
    monkeypatch.setattr(tool, "fetch", _serving(committed_rows, payloads))

    assert tool.main(["--only", "committed"]) == 0


def test_one_re_rendered_figure_fails_the_whole_run(
    monkeypatch: pytest.MonkeyPatch,
    committed_rows: list[dict[str, str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A single upstream byte change is a failure, not a warning beside 12 successes.

    The digest is the only thing distinguishing a silently re-rendered figure from the one the
    record approves, so a run that met 12 of 13 must not exit 0.
    """
    payloads = {}
    for index, row in enumerate(committed_rows):
        blob = (REPO_ROOT / "data" / "real" / "images" / row["file"]).read_bytes()
        payloads[row["graphic_href"].lower()] = blob + b"\x00" if index == 0 else blob
    monkeypatch.setattr(tool, "fetch", _serving(committed_rows, payloads))

    exit_code = tool.main(["--only", "committed"])

    captured = capsys.readouterr().out
    assert exit_code != 0
    assert f"12 of {len(committed_rows)} verified" in captured
    assert "re-rendered this figure" in captured


def test_an_altered_committed_copy_is_reported_as_a_failure_not_an_ok(
    monkeypatch: pytest.MonkeyPatch,
    committed_rows: list[dict[str, str]],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Upstream matching while the local copy does not is a failure line, never an ``ok`` line.

    An earlier version printed ``ok <name>: ...COMMITTED COPY DIFFERS`` and still counted the
    row against the total. A line beginning ``ok`` is what a reader skims past, which is the
    same defect as a success exit code in smaller type.
    """
    payloads = {
        row["graphic_href"].lower(): (
            REPO_ROOT / "data" / "real" / "images" / row["file"]
        ).read_bytes()
        for row in committed_rows
    }
    monkeypatch.setattr(tool, "fetch", _serving(committed_rows, payloads))
    victim = committed_rows[0]["file"]
    monkeypatch.setattr(tool, "COMMITTED_DIR", tmp_path)
    for row in committed_rows:
        target = tmp_path / row["file"]
        blob = payloads[row["graphic_href"].lower()]
        target.write_bytes(blob + b"\x00" if row["file"] == victim else blob)

    exit_code = tool.main(["--only", "committed"])

    captured = capsys.readouterr().out
    assert exit_code != 0
    assert f"FAIL {victim}" in captured
    assert f"ok   {victim}" not in captured
    assert "has been altered since Gate 2" in captured


def test_the_summary_and_the_exit_code_cannot_disagree(
    monkeypatch: pytest.MonkeyPatch,
    committed_rows: list[dict[str, str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Printed count and status come from one counter, so they agree by construction."""
    payloads = {}
    for index, row in enumerate(committed_rows):
        blob = (REPO_ROOT / "data" / "real" / "images" / row["file"]).read_bytes()
        payloads[row["graphic_href"].lower()] = blob + b"\x00" if index < 3 else blob
    monkeypatch.setattr(tool, "fetch", _serving(committed_rows, payloads))

    exit_code = tool.main(["--only", "committed"])

    summary = capsys.readouterr().out.strip().splitlines()[-1]
    verified = int(summary.split(" of ")[0])
    assert verified == len(committed_rows) - 3
    assert (exit_code == 0) == (verified == len(committed_rows))


def test_the_tls_context_verifies_and_never_disables_verification() -> None:
    """Fetches are authenticated, because an unverified fetch feeding a sha256 check is circular.

    Disabling verification would leave the digest proving only that the bytes match what *some*
    server sent, which is the question the digest exists to answer.
    """
    context = tool.tls_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_the_manifest_covers_every_committed_source_and_records_full_digests() -> None:
    """``sources.csv`` is what makes recovery possible, so its shape is pinned.

    A truncated digest would make verification impossible while looking like provenance -- which
    is what ``provenance.md`` records, and why the manifest exists beside it.
    """
    with SOURCES.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    images = REPO_ROOT / "data" / "real" / "images"

    assert len(rows) == 21
    assert all(len(row["sha256"]) == 64 for row in rows)
    assert {row["file"] for row in rows if row["committed"] == "yes"} == {
        path.name for path in images.glob("*.jpg")
    }
    for row in rows:
        if row["committed"] == "yes":
            blob = (images / row["file"]).read_bytes()
            assert hashlib.sha256(blob).hexdigest() == row["sha256"], row["file"]
