"""Exception types raised by the pipeline.

Every failure mode raises one of these with an actionable message; the pipeline never
substitutes a placeholder value for a parameter it was not given, and never squashes
an input it cannot represent.
"""

from __future__ import annotations


class PipelineError(Exception):
    """Base class for every error raised by :mod:`pipeline`."""


class ConfigError(PipelineError):
    """A configuration file is malformed, incomplete, or carries unknown keys."""


class UnsupportedFormatError(PipelineError):
    """The input is not one of the container formats this pipeline reads."""


class UnsupportedBitDepthError(PipelineError):
    """The input pixel type is outside the supported set (8-bit and 16-bit unsigned)."""


class UnsupportedImageError(PipelineError):
    """The input decodes to something other than a single-channel 2D image."""


class BackgroundError(PipelineError):
    """Background estimation was asked for something it cannot deliver."""


class DetectionError(PipelineError):
    """Detection found no structure to report, or was given a degenerate image."""


class LaneRoiError(PipelineError):
    """A *caller-supplied* lane rectangle is unusable, and the caller is told which one.

    Separate from :class:`DetectionError` because the two say different things to whoever
    is on the other end: a ``DetectionError`` reports what the pixels do not contain, while
    this reports a mistake in the request -- an unparseable ``x,y,w,h``, a rectangle off the
    image, a non-positive extent, or two rectangles that overlap. Every message names the
    offending rectangle by its 1-based position in the supplied order and by its
    coordinates, because a caller who supplied six of them cannot act on "a lane ROI is
    invalid".
    """


class QcError(PipelineError):
    """QC was given inputs it cannot assess -- mismatched images, or an ROI off the image."""


class NormalizationError(PipelineError):
    """Normalization was asked for something it cannot honestly compute.

    Raised for a broken invariant of the analysis, never for an outcome of the data. Every
    condition below is produced *inside* this pipeline -- band ids, lane ids, QC flags and the
    lane-integral map all come out of :func:`pipeline.analyze.analyze_image` -- so a caller
    who is one process away cannot cause any of them, and a service reporting one of them as
    a caller mistake would be blaming the wrong party:

    * ``total_protein`` called without the lane integrals, or a housekeeping mode called with
      them;
    * ``total_protein`` given lane integrals that omit one of the detected lanes;
    * a repeated band id, a repeated lane id, or a band naming a lane that was not detected;
    * a band carrying a QC flag outside the band vocabulary, which would put an unscorable
      flag name into a result document through the ratios.

    A mistake in the *request* -- everything about which bands were designated as references
    -- raises :class:`ReferenceBandError` instead. That class is a subclass of this one, so a
    consumer catching this class keeps catching both; the split exists so that a layer with
    somewhere to send the blame, such as the HTTP API's status mapping, can tell them apart.

    **What does not raise**, because it is an outcome of the data and failing the whole image
    over it would discard the lanes that measured fine: a *lane* with no designated reference,
    and a denominator that measured zero or negative. Both are recorded instead -- a warning on
    the result, no ratios for that lane, and every band in it carrying
    ``excluded_from_normalization`` with the reason. A consumer catching this class must not
    expect those two conditions to arrive as exceptions; it reads them off the result. NOTES.md
    (Phase 2, "A wrong *number* of references in a lane raises; a non-positive denominator does
    not") records the split as a deliberate decision.
    """


class ReferenceBandError(NormalizationError):
    """The caller's designation of the housekeeping reference bands is unusable.

    The one part of normalization's input that comes from whoever asked for the analysis
    rather than from the analysis itself, so it is the one part a caller can fix:

    * a housekeeping mode called without ``reference_band_ids`` at all, or ``total_protein``
      called with them -- a reference is never inferred (PLAN.md Phase 2 human ruling), and an
      input no mode reads is a false provenance record;
    * a reference id that names no measured band, or a repeated reference id;
    * a lane holding the wrong *number* of designated references for the mode -- not one under
      ``housekeeping_single``, or fewer than two under ``housekeeping_multi``.

    A subclass of :class:`NormalizationError` so that nothing which already caught that class
    stops catching these, and a distinct class so that they are not reported as this service's
    defects alongside the invariant breaks its parent covers.
    """
