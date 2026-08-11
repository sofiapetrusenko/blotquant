"""Exception types raised by the synthetic generator.

Every failure mode in this package raises one of these; the generator never falls
back to a placeholder value.
"""

from __future__ import annotations


class SynthError(Exception):
    """Base class for every error raised by :mod:`synth`."""


class ConfigError(SynthError):
    """A required configuration entry is missing or internally inconsistent."""


class UnsupportedBitDepthError(SynthError):
    """A bit depth outside the supported set (8, 16) was requested."""


class UnsupportedFormatError(SynthError):
    """An image format was requested that cannot represent the given pixel data."""


class RenderError(SynthError):
    """Rendering produced a canvas that violates a documented invariant."""


class GoldSetMismatchError(SynthError):
    """The output directory already holds a gold set from a different seed/version."""
