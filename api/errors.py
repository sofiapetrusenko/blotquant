"""API-specific error types, and the deliberate ``PipelineError`` -> HTTP status mapping.

Two rules the mapping exists to enforce:

* **A caller mistake is a 4xx and keeps its message verbatim.** The pipeline's errors are
  written to be actionable -- they name the offending lane rectangle, the missing config
  key, the pixel type that was refused -- and an HTTP layer that replaced them with
  "Bad Request" would throw away the only part a caller can act on.
* **A 200 never carries an error string.** Every failure leaves through an exception
  handler, so no success response has an "error" field to inspect. Nothing leaks a
  traceback either: the handler sends the exception's message and its class name, not its
  stack.
"""

from __future__ import annotations

from pipeline.errors import (
    BackgroundError,
    ConfigError,
    DetectionError,
    LaneRoiError,
    NormalizationError,
    PipelineError,
    QcError,
    ReferenceBandError,
    UnsupportedBitDepthError,
    UnsupportedFormatError,
    UnsupportedImageError,
)


class UnknownConfigError(PipelineError):
    """The caller named a parameter set that ``configs/`` does not contain."""


class MalformedResultIdError(PipelineError):
    """A result id that is not a result id at all -- wrong characters, or a path fragment."""


class UnknownResultError(PipelineError):
    """No stored result carries the requested id."""


class CorruptStoredResultError(PipelineError):
    """A stored result exists under its id but cannot be read back as what was written.

    Distinct from :class:`UnknownResultError`, which means nothing is stored: here the files
    are present and one of them is unreadable or is not the JSON this service wrote. It is a
    :class:`PipelineError` so that it leaves through the same handler as every other failure
    -- the API promises one JSON error shape, and a bare ``JSONDecodeError`` escaping into the
    framework's generic 500 would break that promise for the one caller least able to guess.
    """


class DisplayError(PipelineError):
    """The display derivative could not be rendered from an image the pipeline just measured."""


class ResultSchemaError(PipelineError):
    """A produced result document failed ``schema/result.schema.json``.

    Never a caller's fault: the caller supplies an image, a config name, lane rectangles and
    reference band ids, none of which can make the writer emit a field the contract forbids.
    It is an internal defect, and it is raised before the document is returned so that an
    invalid document is never served as a 200.
    """


INTERNAL_SERVER_ERROR = 500
"""Status for every failure that is this service's fault rather than the caller's."""

STATUS_BY_ERROR: tuple[tuple[type[PipelineError], int], ...] = (
    # -- 4xx: the caller can fix these, and the message says how. ------------------------
    # The id names no stored result. 404 is the whole meaning of GET /results/{id} missing.
    (UnknownResultError, 404),
    # The id is not well formed, so it names nothing and could not have been issued by this
    # service. A 404 would suggest "try again later"; this is a request that can never work.
    (MalformedResultIdError, 400),
    # The caller chose a config name; the message lists the names that exist.
    (UnknownConfigError, 400),
    # The caller's lane rectangles: unparseable, off the image, non-positive, or overlapping.
    (LaneRoiError, 400),
    # The reference band designation is the one part of normalization's input that arrives
    # off the wire, so it is the one part a caller can have got wrong: a reference naming no
    # band, a repeated reference, references supplied under total_protein or omitted under a
    # housekeeping mode, or a lane holding the wrong number of them. Listed before its parent
    # NormalizationError, which is a 500 below, because status_for takes the first match.
    (ReferenceBandError, 400),
    # The upload is an image this service cannot quantify: a container outside TIFF/PNG/JPEG,
    # a pixel type outside uint8/uint16, a multi-channel image, or a payload that does not
    # decode. 415 is exactly "the entity's media type is unsupported", and the verbatim
    # message distinguishes the four cases. The pipeline refuses to squash or rescale any of
    # them, so there is no representation of these inputs that a 200 could have carried.
    (UnsupportedFormatError, 415),
    (UnsupportedBitDepthError, 415),
    (UnsupportedImageError, 415),
    # The request was well formed and the image was read, but its content cannot be
    # processed: no lane cleared the prominence threshold, or a background footprint is
    # larger than the image. Not malformed (400) and not a defect (500) -- 422 is the code
    # for a request that is understood and still cannot be carried out.
    (DetectionError, 422),
    (BackgroundError, 422),
    # -- 5xx: this service's fault, still without a traceback. ---------------------------
    # Config is selected by name from configs/ and never posted, so any ConfigError that
    # survives name resolution means a *shipped* config file is broken: a deployment defect.
    (ConfigError, INTERNAL_SERVER_ERROR),
    # QC is handed images and ROIs this package produced; a mismatch is an internal bug.
    (QcError, INTERNAL_SERVER_ERROR),
    # Every NormalizationError that is not a ReferenceBandError is an invariant of the
    # analysis this service just ran -- a duplicate band or lane id, a band naming an
    # undetected lane, a QC flag outside the vocabulary, a lane the total-protein map does
    # not cover. All four quantities are produced here and none of them can be posted, so a
    # 400 would tell the caller to fix a request they got right.
    (NormalizationError, INTERNAL_SERVER_ERROR),
    (DisplayError, INTERNAL_SERVER_ERROR),
    (ResultSchemaError, INTERNAL_SERVER_ERROR),
    # The caller's id was well formed and named something; that something is damaged on this
    # service's disk, which the caller can do nothing about.
    (CorruptStoredResultError, INTERNAL_SERVER_ERROR),
)
"""Ordered most specific first: :func:`status_for` returns the first class that matches."""


def status_for(error: PipelineError) -> int:
    """Return the HTTP status for ``error``, defaulting to 500 for an unmapped subclass.

    The default is deliberately the server's fault rather than the caller's: a
    :class:`PipelineError` subclass nobody has classified is an unreviewed failure mode, and
    reporting it as a 400 would blame the caller for a case this service has not thought
    about.
    """
    for error_type, status in STATUS_BY_ERROR:
        if isinstance(error, error_type):
            return status
    return INTERNAL_SERVER_ERROR
