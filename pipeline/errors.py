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


class QcError(PipelineError):
    """QC was given inputs it cannot assess -- mismatched images, or an ROI off the image."""


class NormalizationError(PipelineError):
    """The *caller* asked normalization for something it cannot honestly compute.

    Raised for a mistake in the request, never for an outcome of the data:

    * a housekeeping mode called without ``reference_band_ids`` at all, or ``total_protein``
      called with them -- a reference is never inferred (PLAN.md Phase 2 human ruling), and an
      input no mode reads is a false provenance record;
    * ``total_protein`` called without the lane integrals, or a housekeeping mode called with
      them;
    * a reference id that names no measured band, a repeated reference id, a repeated band id,
      a repeated lane id, or a band naming a lane that was not detected;
    * a lane holding the wrong *number* of designated references for the mode -- not one under
      ``housekeeping_single``, or fewer than two under ``housekeeping_multi``;
    * ``total_protein`` given lane integrals that omit one of the detected lanes;
    * a band carrying a QC flag outside the band vocabulary, which would put an unscorable
      flag name into a result document through the ratios.

    **What does not raise**, because it is an outcome of the data and failing the whole image
    over it would discard the lanes that measured fine: a *lane* with no designated reference,
    and a denominator that measured zero or negative. Both are recorded instead -- a warning on
    the result, no ratios for that lane, and every band in it carrying
    ``excluded_from_normalization`` with the reason. A consumer catching this class must not
    expect those two conditions to arrive as exceptions; it reads them off the result. NOTES.md
    (Phase 2, "A wrong *number* of references in a lane raises; a non-positive denominator does
    not") records the split as a deliberate decision.
    """
