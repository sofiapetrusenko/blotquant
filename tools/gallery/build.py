"""Build the committed gallery: post each manifest entry to the API and store what comes back.

``python -m tools.gallery.build``

**Every card in the gallery is a real measurement, made by the shipped service.** Nothing here
computes an intensity, a ratio or a flag; it posts an image and a lane rectangle to a running
``POST /analyze`` and writes the document the service answered with, unedited. The verdict on
each card comes from :func:`api.display.lane_verdicts` -- the ruled derivation itself
(``docs/PRE_REGISTRATION_2026-08-25_verdict_mapping.md``, Ruling 1) -- and never from a rule
restated here.

**It refuses to run without the API.** A gallery assembled from anything other than the live
service would be a set of numbers with the product's name on them that the product did not
produce, and the failure would be invisible in the output. So the build preflights the API and
exits non-zero, naming the URL and the command that starts the server, rather than falling back
to anything. It never starts the server itself: what is running is the operator's decision.

**The unit is one lane.** Ruling 2 of 2026-08-25 makes a card one caller-supplied ROI, so each
manifest entry supplies exactly one ``lane_roi`` and a document that comes back with any other
number of lanes fails the build.

Output, under ``web/public/gallery/`` and committed:

* ``<id>/result.json`` -- the API's ``result`` document, key for key as it was served;
* ``<id>/display.png`` -- the display derivative, decoded from ``display.png_base64``;
* ``<id>/display.json`` -- the rest of the ``display`` block, key for key as it was served and
  with ``png_base64`` removed, because those bytes are already on disk as ``display.png`` and
  committing them twice would put the same picture in the repository in two encodings. What
  remains is the *labelling*: ``is_derivative``, ``note`` and above all ``mapping``, which is
  what says the picture is a scaled 8-bit rendering and how many source DN one output level
  spans. A card view that showed the picture without that block would be inviting a reader to
  judge saturation off the brightest colour in a PNG, which is exactly what
  ``api/display.py``'s note exists to forbid;
* ``index.json`` -- ``{id, title, verdict, blocked_reason?, flag_count}`` per entry, in manifest
  order, with the verdict fields taken from ``lane_verdicts`` over the stored document.

**The gallery root is build output and this module replaces it wholesale.** Every card is built
into a staging directory beside it and the two are swapped only after the last one is written, so
a refusal, a transport failure or an unreachable API leaves the committed tree exactly as it was.
A card the manifest no longer names is dropped by not being rebuilt, and reported. Because the
swap replaces the whole directory, ``--out`` is checked first: a root holding anything this tool
did not write stops the build before a single request is made.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml

from api.display import DISPLAY_BLOCK_KEYS, lane_verdicts
from api.errors import DisplayError

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
"""Repository root. Manifest image paths are resolved against it, not against the cwd."""

MANIFEST_PATH = REPO_ROOT / "web" / "gallery.manifest.yaml"
"""The selection, as ruled.

It implements the composition ruled on 2026-08-25 and recorded in ``docs/pr/phase-4b0.md``
("Gallery composition, ruled and confirmed by image"), in the ruled order. Its header records
where each rectangle came from -- five from the caller-ROI measurement, one measured later by the
same method with a control -- and what the corpus cannot supply. It is not a proposal awaiting
approval: changing the composition means changing the ruling first.
"""

GALLERY_ROOT = REPO_ROOT / "web" / "public" / "gallery"
"""Where the cards are written. Entirely build output, and committed so the site is static."""

INDEX_NAME = "index.json"
RESULT_NAME = "result.json"
DISPLAY_NAME = "display.png"
DISPLAY_METADATA_NAME = "display.json"

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
"""The eight bytes every PNG begins with. Checked before a decoded image is written."""

PNG_BASE64_KEY = "png_base64"
"""The key holding the encoded image. Stripped from ``display.json``; see the module docstring."""

REQUIRED_MANIFEST_KEYS: tuple[str, ...] = ("api_url", "entries")
"""Top-level keys the manifest must carry. Neither has a default: both are decisions."""

REQUIRED_ENTRY_KEYS: tuple[str, ...] = ("id", "image", "polarity", "config", "lane_roi", "title")
"""Keys every entry must carry. ``polarity`` and ``config`` are listed per entry rather than
once at the top of the manifest because both are inputs to the measurement and a card whose
polarity came from a file-level default would not say, on its own row, what it was measured
under."""

ANALYZE_TIMEOUT_SECONDS = 600.0
"""Read timeout for one ``POST /analyze``.

Generous on purpose. ``api/__init__.py`` records 23.49 s for a 1.4 MP image on a developer
machine, and a build that timed out mid-corpus would leave a half-written gallery behind. This
is an I/O bound, not a processing parameter: nothing measured depends on it.
"""

PREFLIGHT_TIMEOUT_SECONDS = 10.0
"""Connect/read timeout for the preflight. Short: an API that is up answers this immediately."""

START_COMMAND = "python -m api --storage-root results/ --config-dir configs/"
"""The command that starts the server this build needs, quoted in the refusal message."""


class GalleryBuildError(Exception):
    """The gallery could not be built from the manifest and the running service.

    Every raise site names the entry, the file or the URL at fault and what to do about it.
    There is no partial-success path: a gallery missing a card it was asked for would be a
    selection nobody made.
    """


def _load_manifest(path: Path) -> dict[str, Any]:
    """Return the parsed manifest, or raise naming what is missing.

    Guarantees the returned mapping carries every key in :data:`REQUIRED_MANIFEST_KEYS` and
    that ``entries`` is a non-empty list of mappings, each carrying every key in
    :data:`REQUIRED_ENTRY_KEYS` and a unique ``id``.
    """
    if not path.is_file():
        raise GalleryBuildError(
            f"no gallery manifest at {path}; the gallery is built from a manifest the human "
            f"approves, and there is nothing to build without one"
        )
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise GalleryBuildError(
            f"{path} does not parse as a YAML mapping (got {type(parsed).__name__}); it must "
            f"carry {list(REQUIRED_MANIFEST_KEYS)} at its top level"
        )
    missing = [key for key in REQUIRED_MANIFEST_KEYS if key not in parsed]
    if missing:
        raise GalleryBuildError(
            f"{path} is missing {missing}; a manifest must name the API it posts to "
            f"('api_url') and the cards to build ('entries')"
        )
    entries = parsed["entries"]
    if not isinstance(entries, list):
        raise GalleryBuildError(
            f"{path}: 'entries' must be a list of card entries, got "
            f"{type(entries).__name__}"
        )
    if not entries:
        raise GalleryBuildError(
            f"{path} lists no entries, so there is no gallery to build. Its composition is "
            f"ruled -- see docs/pr/phase-4b0.md, 'Gallery composition, ruled and confirmed by "
            f"image' -- and the file's own header records where every rectangle came from. "
            f"Restore the entries rather than inventing a selection here"
        )
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise GalleryBuildError(
                f"{path}: entry {index} is a {type(entry).__name__}, not a mapping carrying "
                f"{list(REQUIRED_ENTRY_KEYS)}"
            )
        absent = [key for key in REQUIRED_ENTRY_KEYS if key not in entry]
        if absent:
            raise GalleryBuildError(
                f"{path}: entry {index} is missing {absent}; every card entry carries "
                f"{list(REQUIRED_ENTRY_KEYS)}, and none of them has a default"
            )
        # ``str(...)`` because the directory name is ``str(entry["id"])``: ids ``1`` and ``"1"``
        # are distinct YAML values that name one directory, and the second card would silently
        # overwrite the first.
        entry_id = str(entry["id"])
        if entry_id in seen:
            raise GalleryBuildError(
                f"{path}: entry {index} repeats the id {entry_id!r}, which already names a "
                f"card. Ids become directory names under {GALLERY_ROOT.name}/ and the second "
                f"card would overwrite the first"
            )
        seen.add(entry_id)
    return parsed


def _preflight(client: httpx.Client, api_url: str) -> None:
    """Raise unless ``api_url`` is answering as this service.

    ``/openapi.json`` rather than the bare root: the root of a FastAPI app is a 404, which
    something else listening on the port would also return, and a gallery built against the
    wrong server would be measurements from a service nobody chose.
    """
    url = f"{api_url.rstrip('/')}/openapi.json"
    try:
        response = client.get(url, timeout=PREFLIGHT_TIMEOUT_SECONDS)
    except httpx.HTTPError as error:
        raise GalleryBuildError(
            f"the blotquant API at {api_url} is not reachable ({error}); the gallery is built "
            f"from live measurements and this build will not fabricate one. Start the server "
            f"in another shell with:\n\n    {START_COMMAND}\n\n"
            f"and run this build again. It never starts the server itself"
        ) from error
    if response.status_code != 200:
        raise GalleryBuildError(
            f"{url} answered {response.status_code}, so something is listening on {api_url} "
            f"but it is not the blotquant API. Start the right server with:\n\n"
            f"    {START_COMMAND}"
        )


def _refusal_detail(response: httpx.Response) -> str:
    """Return the API's own ``detail`` for a failed request, verbatim, never paraphrased.

    Two shapes reach here and both are quoted as they arrive: this service's own refusal
    (``detail`` a string, beside an ``error`` naming the class) and FastAPI's request
    validation (``detail`` a list of per-field objects, no ``error``). See ``api/app.py``.
    """
    try:
        body = response.json()
    except ValueError:
        return f"<non-JSON body> {response.text!r}"
    if not isinstance(body, dict) or "detail" not in body:
        return json.dumps(body, sort_keys=True)
    detail = body["detail"]
    error = body.get("error")
    rendered = detail if isinstance(detail, str) else json.dumps(detail, sort_keys=True)
    return f"{rendered} (error: {error})" if error is not None else str(rendered)


MEDIA_TYPE_BY_SUFFIX: dict[str, str] = {
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
"""Multipart content type per suffix, for the containers ``pipeline/load.py`` reads.

Stated rather than assumed: sending every manifest image as ``image/png`` happens to work while
every entry is a PNG, and would mislabel the first TIFF anyone adds.
"""


def _media_type(image_path: Path) -> str:
    """Return the multipart content type for ``image_path``, or raise naming the suffix.

    Refuses an unknown suffix rather than defaulting: a container this pipeline does not read is
    a mistake in the manifest, and labelling it as something else would send the API an image it
    would then refuse for a reason that named the wrong thing.
    """
    suffix = image_path.suffix.lower()
    media_type = MEDIA_TYPE_BY_SUFFIX.get(suffix)
    if media_type is None:
        raise GalleryBuildError(
            f"{image_path} has suffix {suffix!r}, which is not one of the containers this "
            f"pipeline reads ({sorted(MEDIA_TYPE_BY_SUFFIX)}). The gallery posts the file with "
            f"its own content type and will not guess one"
        )
    return media_type


def _analyze(client: httpx.Client, api_url: str, entry: dict[str, Any]) -> dict[str, Any]:
    """POST one card's image and lane rectangle, and return the response envelope.

    Raises :class:`GalleryBuildError` on any non-2xx, quoting the API's own ``detail``. The
    entry is never skipped: a card the manifest asked for and the service refused is a finding
    about the selection or about the service, and swallowing it would hide both.
    """
    image_path = REPO_ROOT / str(entry["image"])
    if not image_path.is_file():
        raise GalleryBuildError(
            f"card {entry['id']!r} names the image {entry['image']!r}, which does not exist "
            f"at {image_path}; manifest image paths are relative to the repository root"
        )
    url = f"{api_url.rstrip('/')}/analyze"
    image_bytes = image_path.read_bytes()
    try:
        response = client.post(
            url,
            files={"image": (image_path.name, image_bytes, _media_type(image_path))},
            data={
                "config": str(entry["config"]),
                "polarity": str(entry["polarity"]),
                "lane_roi": str(entry["lane_roi"]),
            },
            timeout=ANALYZE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as error:
        # The transport, not the status. A read timeout or a dropped connection would otherwise
        # escape main()'s handler as a traceback -- which is what ANALYZE_TIMEOUT_SECONDS' own
        # rationale describes and, until this was added, did not prevent.
        raise GalleryBuildError(
            f"card {entry['id']!r}: the request to {url} did not complete ({error}). The read "
            f"timeout is {ANALYZE_TIMEOUT_SECONDS:g} s. Nothing was written for this card"
        ) from error
    if response.status_code // 100 != 2:
        raise GalleryBuildError(
            f"card {entry['id']!r} was refused by {url} with HTTP "
            f"{response.status_code}: {_refusal_detail(response)}. The request was "
            f"config={entry['config']!r}, polarity={entry['polarity']!r}, "
            f"lane_roi={entry['lane_roi']!r} on {entry['image']!r}"
        )
    try:
        envelope = response.json()
    except ValueError as error:
        raise GalleryBuildError(
            f"card {entry['id']!r}: {url} answered HTTP {response.status_code} with a body that "
            f"is not JSON ({response.text[:200]!r}); this service answers every request with a "
            f"JSON envelope"
        ) from error
    if not isinstance(envelope, dict):
        raise GalleryBuildError(
            f"card {entry['id']!r}: {url} answered with a {type(envelope).__name__}, not the "
            f"{{'result', 'display'}} envelope both endpoints of this service answer with"
        )
    for key in ("result", "display"):
        if key not in envelope:
            raise GalleryBuildError(
                f"card {entry['id']!r}: the response from {url} carries no {key!r}; both "
                f"endpoints of this service answer with a {{'result', 'display'}} envelope"
            )
    _require_measured_image(entry, envelope["result"], image_bytes, image_path)
    return envelope


def _require_measured_image(
    entry: dict[str, Any], result: Any, image_bytes: bytes, image_path: Path
) -> None:
    """Raise unless the served document's ``source.sha256`` is the digest of the file we sent.

    The card's title, its figure attribution and its licence row all come from the manifest;
    its numbers come from whatever the API measured. Nothing else in this build checks that
    those are the same image. Without this, a manifest edited to name a different crop -- or a
    crop regenerated on disk -- produces a card that attributes one figure and measures another,
    and no test in the project would fail.

    ``source.sha256`` is optional under ``schema/result.schema.json``, so its absence is not a
    failure; a *mismatch* is. The digest is of the bytes as delivered, which is what was posted.
    """
    if not isinstance(result, dict):
        raise GalleryBuildError(
            f"card {entry['id']!r}: the response's 'result' is a {type(result).__name__}, "
            f"not the result document this service answers with"
        )
    source = result.get("source")
    served = source.get("sha256") if isinstance(source, dict) else None
    if served is None:
        return
    expected = f"sha256:{hashlib.sha256(image_bytes).hexdigest()}"
    if served != expected:
        raise GalleryBuildError(
            f"card {entry['id']!r}: the served document records source.sha256={served!r} but "
            f"{image_path} hashes to {expected!r}. The card would attribute one figure and "
            f"report measurements of another"
        )


def _card_verdict(entry_id: str, title: str, result: dict[str, Any]) -> dict[str, Any]:
    """Return the ``index.json`` row for one card, from the ruled derivation over ``result``.

    The verdict is never re-derived here: :func:`api.display.lane_verdicts` is the ruled
    mapping, and a second implementation of it in a build script is exactly the divergence
    ``tools/gallery/verdict_fixtures.py`` exists to prevent for the TypeScript one.

    ``lane_verdicts`` checks key *presence* and not type, so a served document with a
    wrong-typed ``lanes`` or ``bands`` raises ``TypeError``/``AttributeError`` rather than
    ``DisplayError``. Those are caught here too: every way the derivation can fail on a document
    this service produced is the same finding, and one of them escaping as a traceback would
    contradict ``main()``'s promise of an actionable message on any failure.

    Fails loudly unless the document holds exactly one lane, because a card is one lane
    (Ruling 2) and a two-lane document has no single verdict to show. ``blocked_reason`` is
    emitted only for a blocked card, on the same convention as
    :meth:`api.display.LaneVerdict.as_dict`.
    """
    try:
        verdicts = lane_verdicts(result)
    except (DisplayError, TypeError, AttributeError, KeyError) as error:
        raise GalleryBuildError(
            f"card {entry_id!r}: the document the API served cannot be read back by the "
            f"ruled verdict derivation ({type(error).__name__}: {error}). The gallery stores "
            f"documents this service produced, so this is a defect in the service rather than "
            f"in the manifest"
        ) from error
    if len(verdicts) != 1:
        raise GalleryBuildError(
            f"card {entry_id!r} came back with {len(verdicts)} lanes; a gallery card is one "
            f"caller-supplied ROI with its verdict (Ruling 2, 2026-08-25), so its document "
            f"must hold exactly one lane. Check that the manifest entry supplies exactly one "
            f"lane_roi"
        )
    verdict = verdicts[0]
    row: dict[str, Any] = {"id": entry_id, "title": title, "verdict": verdict.verdict}
    if verdict.blocked_reason is not None:
        row["blocked_reason"] = verdict.blocked_reason
    row["flag_count"] = len(verdict.qc_flags)
    return row


def _write_card(
    directory: Path, result: dict[str, Any], display: dict[str, Any], png: bytes
) -> None:
    """Write one card's stored document, its display labelling and its display PNG.

    The document is serialised with ``json.dumps`` at its own key order: no key is added,
    removed or reordered, so what is committed is what the service served. ``display.json`` is
    the same, minus :data:`PNG_BASE64_KEY` alone -- nothing is renamed, reordered or summarised,
    so the ``mapping`` block a card view shows is the one the service wrote.

    Raises :class:`GalleryBuildError` if the served block is missing any of
    :data:`api.display.DISPLAY_BLOCK_KEYS`, or if ``png`` is not a PNG. An unlabelled derivative
    is refused rather than stored, because a stored picture with no ``mapping`` beside it is a
    picture of measured data whose provenance nothing on disk records -- and the picture itself
    gets the same treatment, since refusing a missing *label* while accepting arbitrary bytes as
    the *image* would be checking the caption and not the photograph.
    """
    absent = [key for key in DISPLAY_BLOCK_KEYS if key not in display]
    if absent:
        raise GalleryBuildError(
            f"the display block served for {directory.name!r} is missing {absent}; a stored "
            f"derivative carries the labelling that says it is one "
            f"({list(DISPLAY_BLOCK_KEYS)}), and a card view without it would show a rendering "
            f"of measured pixels with nothing on disk recording how it was rendered"
        )
    if not png.startswith(PNG_SIGNATURE):
        raise GalleryBuildError(
            f"the bytes decoded for {directory.name!r} do not begin with the PNG signature; a "
            f"card stores a picture of measured data, and this build will not write something "
            f"that is not one. The display block refused a missing label -- it refuses an "
            f"unreadable image on the same ground"
        )
    metadata = {key: value for key, value in display.items() if key != PNG_BASE64_KEY}
    directory.mkdir(parents=True, exist_ok=True)
    (directory / RESULT_NAME).write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (directory / DISPLAY_METADATA_NAME).write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (directory / DISPLAY_NAME).write_bytes(png)


def _unlisted_cards(gallery_root: Path, listed: set[str]) -> list[str]:
    """Return the ids of cards present in the *current* gallery that the manifest no longer names.

    Reporting only: a de-listed card is dropped by not being built rather than by being deleted.
    The wholesale replacement that makes that true is what :func:`_require_safe_output_root`
    guards, since it is the swap -- not this function -- that can destroy a mis-aimed ``--out``.
    Only directories holding a ``result.json`` are counted, so the count means "cards".
    """
    if not gallery_root.is_dir():
        return []
    return [
        child.name
        for child in sorted(gallery_root.iterdir())
        if child.is_dir() and child.name not in listed and (child / RESULT_NAME).is_file()
    ]



GALLERY_ROOT_ALLOWED_FILES: frozenset[str] = frozenset({INDEX_NAME, ".DS_Store"})
"""Files the output root may hold besides card directories.

The swap replaces the *whole* output tree, so ``--out`` aimed one directory too high would take
everything with it. :func:`_require_safe_output_root` refuses any root holding something this
tool does not recognise, which is the guard that makes wholesale replacement safe.
"""


def _require_safe_output_root(gallery_root: Path) -> None:
    """Raise unless ``gallery_root`` is empty, absent, or a gallery this tool wrote.

    The build replaces the output root wholesale. That is safe for ``web/public/gallery/``, whose
    entire contents are build output, and catastrophic for a directory that holds anything else:
    ``--out web/public`` would destroy the committed gallery *and* everything beside it. So the
    root is inspected first, and anything that is not a card directory or ``index.json`` stops
    the build before a single request is made.
    """
    if not gallery_root.exists():
        return
    if not gallery_root.is_dir():
        raise GalleryBuildError(
            f"{gallery_root} is not a directory; the gallery output root is a directory this "
            f"tool owns and replaces wholesale"
        )
    strangers = sorted(
        child.name
        for child in gallery_root.iterdir()
        if not (child.is_dir() or child.name in GALLERY_ROOT_ALLOWED_FILES)
        or (child.is_dir() and not (child / RESULT_NAME).is_file())
    )
    if strangers:
        raise GalleryBuildError(
            f"{gallery_root} holds {strangers}, which this tool did not write. The build "
            f"replaces the output root wholesale, so it refuses a directory that is not "
            f"entirely gallery build output -- check --out"
        )


def _recover_interrupted_swap(gallery_root: Path, previous: Path) -> None:
    """Put back a gallery left in ``<name>.previous`` by a build that died mid-swap.

    The swap renames the live tree aside and the new tree into its place. If the process is
    killed between those two renames -- Ctrl-C, a full disk, an OSError -- the only copy of the
    gallery is sitting under ``previous``. Deleting it at the start of the next run, which is
    what this function replaced, would destroy the last copy of a committed directory.
    """
    if not previous.exists():
        return
    if not gallery_root.exists():
        previous.rename(gallery_root)
        print(
            f"recovered {gallery_root} from {previous.name}: a previous build was interrupted "
            f"between the two halves of its swap"
        )
        return
    shutil.rmtree(previous)


def _swap_in(staging: Path, gallery_root: Path, previous: Path) -> None:
    """Replace ``gallery_root`` with ``staging``, putting the old tree back if anything fails.

    The window between the two renames is the only moment at which the gallery is not in place,
    and an exception inside it used to leave the directory simply gone -- the built tree deleted
    by the caller's ``finally``, the old one stranded under ``previous`` and destroyed at the
    start of the next run. Both renames are now guarded, the old tree is restored on failure, and
    the failure arrives as a :class:`GalleryBuildError` rather than a bare ``OSError``.
    """
    moved_aside = False
    try:
        if gallery_root.exists():
            gallery_root.rename(previous)
            moved_aside = True
        staging.rename(gallery_root)
    except OSError as error:
        if moved_aside and not gallery_root.exists():
            previous.rename(gallery_root)
        raise GalleryBuildError(
            f"the built gallery could not be swapped into {gallery_root} ({error}). The "
            f"previous gallery has been left in place; nothing was changed"
        ) from error
    if previous.exists():
        shutil.rmtree(previous)


def build(
    manifest_path: Path, gallery_root: Path, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
    """Build every card the manifest names and return the ``index.json`` rows in its order.

    Guarantees on return: every listed card has a ``result.json``, a ``display.json`` and a
    ``display.png`` under ``gallery_root``, every one of those documents holds exactly one lane,
    and no card directory exists that the manifest does not name.

    Guarantee on *failure*, which is the one that matters for a committed directory: nothing
    under ``gallery_root`` has changed. The build assembles a complete gallery beside it and
    swaps it in only after the last card is written, so a refusal, a transport failure or an
    unreachable API leaves the working tree as it was.

    ``client`` is injectable so that the entry loop -- every refusal path in it included -- is
    reachable from a test without a listening server. Passing one leaves its lifetime to the
    caller; omitting it makes one for the duration of the build.
    """
    manifest = _load_manifest(manifest_path)
    api_url = str(manifest["api_url"])
    entries: list[dict[str, Any]] = list(manifest["entries"])
    rows: list[dict[str, Any]] = []

    # Everything is built into a staging directory and swapped in only once every card has been
    # measured, validated and written. Nothing under ``gallery_root`` is touched until then.
    #
    # The previous arrangement wrote each card in place and removed the index first, which made
    # the two most likely failures *destructive*: an API that is not running deleted a committed
    # index and rebuilt nothing, and a refusal at entry N left the directory holding a mixture of
    # two builds -- some cards new, some stale, no index. Both states are commit-able, because
    # this directory is committed. Building beside the gallery makes a failed build leave the
    # working tree exactly as it found it, which is what "there is no partial-success path"
    # has to mean if it is to mean anything.
    staging = gallery_root.parent / f"{gallery_root.name}.building"
    previous = gallery_root.parent / f"{gallery_root.name}.previous"

    _require_safe_output_root(gallery_root)
    _recover_interrupted_swap(gallery_root, previous)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    try:
        owned = client is None
        http = httpx.Client() if client is None else client
        try:
            _preflight(http, api_url)
            print(f"API reachable at {api_url}; building {len(entries)} card(s)")
            for entry in entries:
                entry_id = str(entry["id"])
                envelope = _analyze(http, api_url, entry)
                result = envelope["result"]
                display = envelope["display"]
                if PNG_BASE64_KEY not in display:
                    raise GalleryBuildError(
                        f"card {entry_id!r}: the display block carries no {PNG_BASE64_KEY!r}, "
                        f"so there is no derivative to store beside its document"
                    )
                try:
                    png = base64.b64decode(display[PNG_BASE64_KEY], validate=True)
                except (binascii.Error, ValueError) as error:
                    # ``validate=True`` matters: the default discards characters outside the
                    # base64 alphabet instead of raising, so a payload corrupted mid-string
                    # decodes to *something* -- and, since the corruption shifts only the bytes
                    # after it, to something that can still begin with the PNG signature. The
                    # signature check below would wave it through and a subtly wrong picture
                    # would ship beside correct numbers with the build exiting 0.
                    raise GalleryBuildError(
                        f"card {entry_id!r}: {PNG_BASE64_KEY!r} is not valid base64 ({error}); "
                        f"the served derivative cannot be decoded and will not be stored"
                    ) from error
                # Validated before it is written: a card the build is about to reject must not
                # reach the disk first.
                row = _card_verdict(entry_id, str(entry["title"]), result)
                _write_card(staging / entry_id, result, display, png)
                rows.append(row)
                reason = row.get("blocked_reason", "-")
                print(
                    f"  {entry_id}: {row['verdict']} (blocked_reason={reason}, "
                    f"flag_count={row['flag_count']})"
                )
        finally:
            if owned:
                http.close()

        (staging / INDEX_NAME).write_text(
            json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        unlisted = _unlisted_cards(gallery_root, {str(entry["id"]) for entry in entries})
        _swap_in(staging, gallery_root, previous)
        for name in unlisted:
            print(f"  removed card {name!r}, which the manifest no longer names")
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    index_path = gallery_root / INDEX_NAME
    print(f"wrote {index_path}")
    return rows


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the gallery build CLI."""
    parser = argparse.ArgumentParser(
        prog="python -m tools.gallery.build",
        description="Build web/public/gallery/ by posting each manifest entry to a live API.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=MANIFEST_PATH,
        help=f"gallery manifest to build from (default: {MANIFEST_PATH.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=GALLERY_ROOT,
        help=f"gallery output root (default: {GALLERY_ROOT.relative_to(REPO_ROOT)})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Build the gallery. Returns 0 on success, 1 with an actionable message on any failure.

    Every failure this tool can produce leaves through here as a message: refusals and contract
    breaks as :class:`GalleryBuildError`, and the filesystem's own errors as ``OSError``. Neither
    reaches the operator as a traceback.
    """
    args = build_parser().parse_args(argv)
    try:
        build(args.manifest, args.out)
    except GalleryBuildError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        # The filesystem, not the manifest or the service: a full disk, a permission, a rename
        # that could not complete. Reported the same way, because "1 with an actionable message
        # on any failure" is a promise about every failure and a traceback is not a message.
        print(f"error: the gallery build could not complete ({error})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
