"""Filesystem-backed storage of result documents and their display derivatives.

Rooted at a directory the store is *constructed* with, never a module-level constant path,
so a test points it at ``tmp_path`` and a deployment points it wherever it likes without
either of them editing this file.

The root goes through :func:`pipeline.analyze.require_writable_destination`, which is the
same guard the CLI's ``--out`` passes: PLAN.md's first key invariant is that
``data/ground_truth/`` is written only by ``python -m synth``, and that boundary has to hold
for every writer, not only the one it was first written for. It is checked when the store is
constructed, so a misconfigured server fails at start-up rather than on its first upload.

One directory per result, holding the two artifacts a ``GET`` has to reassemble the envelope
from: the result document as JSON, and the display derivative as its PNG bytes plus the
mapping record that describes them.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from api.errors import CorruptStoredResultError, MalformedResultIdError, UnknownResultError
from pipeline.analyze import require_writable_destination

RESULT_DOCUMENT_NAME = "result.json"
DISPLAY_METADATA_NAME = "display.json"
DISPLAY_IMAGE_NAME = "display.png"

SAFE_RESULT_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
"""Characters a result id may consist of.

A result id reaches this module straight from a URL path, so it is checked against a closed
character set before it is ever joined to a path: no separators, no ``..``, no empty string.
Deliberately wider than the 16 hex digits :func:`pipeline.analyze._result_id` currently
produces, because this store's job is to be safe, not to re-declare the id's format.
"""


def _read_bytes(path: Path, result_id: str) -> bytes:
    """Return ``path``'s bytes, or raise :class:`CorruptStoredResultError` naming both."""
    try:
        return path.read_bytes()
    except OSError as error:
        raise CorruptStoredResultError(
            f"the stored result {result_id!r} exists but {path.name} could not be read from "
            f"{path}: {error}. Re-analysing the image reproduces this result, id and all"
        ) from error


def _read_json(path: Path, result_id: str) -> dict[str, Any]:
    """Return ``path`` parsed as a JSON object, or raise :class:`CorruptStoredResultError`.

    An unreadable or truncated file is a damaged store, not a missing one and not a caller
    mistake, so it leaves as a :class:`pipeline.errors.PipelineError` with an actionable
    message rather than as a bare ``JSONDecodeError`` through the framework's generic
    handler, which would be the one error in this service without the shared JSON shape.

    Decoding is **strict**, and that is the point of this function rather than a detail of
    it. ``errors="replace"`` would turn bytes damaged inside a JSON string into U+FFFD, the
    document would parse, and the altered content would be served as a 200: a band id or a
    note silently rewritten, which the result schema cannot catch because a mangled string is
    still a string. Repairing a damaged store is the opposite of detecting one.
    """
    raw = _read_bytes(path, result_id)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CorruptStoredResultError(
            f"the stored result {result_id!r} exists but {path.name} is not valid UTF-8 "
            f"({path}, byte {error.start}: {error.reason}). It was written by this service as "
            f"UTF-8, so it has been damaged since; the undecodable bytes are not replaced, "
            f"because a document repaired into something parseable would be served as though "
            f"it were what was written. Re-analysing the image reproduces this result, id and "
            f"all"
        ) from error
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise CorruptStoredResultError(
            f"the stored result {result_id!r} exists but {path.name} is not valid JSON "
            f"({path}, line {error.lineno} column {error.colno}: {error.msg}). It was written "
            f"by this service, so it has been damaged or truncated since; re-analysing the "
            f"image reproduces this result, id and all"
        ) from error
    if not isinstance(parsed, dict):
        raise CorruptStoredResultError(
            f"the stored result {result_id!r} exists but {path.name} holds a "
            f"{type(parsed).__name__} rather than a JSON object ({path}); re-analysing the "
            f"image reproduces this result, id and all"
        )
    return parsed


class ResultStore:
    """Stores and retrieves one analysis per result id, under a caller-chosen root."""

    def __init__(self, root: Path) -> None:
        """Bind the store to ``root``, refusing any root inside a ground-truth tree."""
        require_writable_destination(root)
        self._root = root

    def _directory(self, result_id: str) -> Path:
        """Return the directory for ``result_id``, raising on an id that is not one."""
        if not SAFE_RESULT_ID.fullmatch(result_id):
            raise MalformedResultIdError(
                f"{result_id!r} is not a result id; a result id is 1 to 64 characters from "
                f"[A-Za-z0-9_-], and this service issues 16 hex digits. It is the "
                f"'result_id' field of a document returned by POST /analyze"
            )
        return self._root / result_id

    def save(
        self,
        result_id: str,
        result: Mapping[str, Any],
        display_block: Mapping[str, Any],
        png: bytes,
    ) -> Path:
        """Write one analysis and return the directory it was written to.

        **Overwrites an existing entry for the same id, and two documents under one id are not
        guaranteed identical.** An earlier version of this docstring claimed they were "byte
        for byte, apart from ``provenance.created_at``", and that is false over the API. The
        id is content-addressed over the image digest, the config digest, the reference band
        ids and the lane rectangles -- but the *document* also carries ``source.path``, which
        is not one of those inputs. On an upload that path is a fresh
        :class:`tempfile.TemporaryDirectory` joined to the client's own filename, so two POSTs
        of identical bytes under different filenames produce the same ``result_id`` and
        different documents, and the second replaces the first.

        What is stable under one id, and is what a consumer should rely on: ``source.sha256``,
        every ROI, every intensity, every ratio, every QC flag -- everything the analysis
        measured. What is not: ``provenance.created_at``, and ``source.path``, which names a
        directory that no longer exists by the time the response is sent.

        This is recorded in DEBT.md E10 rather than fixed here, because the fix is a decision
        about what provenance should record for an upload, not a bug in this method.
        """
        directory = self._directory(result_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / RESULT_DOCUMENT_NAME).write_text(
            json.dumps(result, indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )
        (directory / DISPLAY_METADATA_NAME).write_text(
            json.dumps(display_block, indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )
        (directory / DISPLAY_IMAGE_NAME).write_bytes(png)
        return directory

    def load(self, result_id: str) -> tuple[dict[str, Any], dict[str, Any], bytes]:
        """Return ``(result, display_block, png)`` for ``result_id``.

        Raises :class:`api.errors.UnknownResultError` naming the id when nothing is stored
        under it, and when an entry exists but is incomplete -- a half-written directory
        cannot serve the envelope it promises, and reporting it as "found" would return a
        document with no image or an image with no document. Raises
        :class:`api.errors.CorruptStoredResultError` when the files are all there and one of
        them cannot be read back as what was written.
        """
        directory = self._directory(result_id)
        document = directory / RESULT_DOCUMENT_NAME
        metadata = directory / DISPLAY_METADATA_NAME
        image = directory / DISPLAY_IMAGE_NAME
        missing = [path.name for path in (document, metadata, image) if not path.is_file()]
        if missing:
            raise UnknownResultError(
                f"no result stored under id {result_id!r}"
                + (
                    ""
                    if len(missing) == 3
                    else f" (its directory exists but {missing} are missing)"
                )
                + ". Ids are issued by POST /analyze as the 'result_id' field of the result"
            )
        return (
            _read_json(document, result_id),
            _read_json(metadata, result_id),
            _read_bytes(image, result_id),
        )
