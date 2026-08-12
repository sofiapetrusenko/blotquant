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
