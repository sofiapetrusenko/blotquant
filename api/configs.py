"""The named parameter sets this service will run.

Config is chosen by **name** from a directory of shipped YAML files; the caller cannot post
one. Two reasons, both structural rather than defensive:

* a posted config widens the input surface from "one of a handful of reviewed parameter
  sets" to "any mapping the loader accepts", and every parameter in it reaches image
  processing;
* ``provenance.config_digest`` is only worth anything if the digest can be traced back to a
  file someone can read. A posted config produces a digest of something that exists nowhere
  and can never be looked up again.

An unknown name is refused loudly, listing what does exist, so a typo is a message rather
than a fallback to some default parameter set. Selecting by name also means the catalog is
the only path to a config file, so a name cannot address a file outside the directory.
"""

from __future__ import annotations

from pathlib import Path

from api.errors import UnknownConfigError
from pipeline.config import PipelineConfig, load_config
from pipeline.errors import ConfigError

CONFIG_SUFFIX = ".yaml"
"""Extension of a shipped parameter set. A config name is its file stem."""


class ConfigCatalog:
    """The parameter sets available under one directory, addressed by name."""

    def __init__(self, directory: Path) -> None:
        """Bind the catalog to ``directory``, refusing a directory that is not one.

        The *contents* are read on every lookup and are deliberately not cached: a config file
        edited under a running server should take effect on the next request rather than at
        the next restart, and the digest recorded in provenance makes the change visible in
        every result it affected. Its *existence* is checked here instead, for the same reason
        ``storage_root`` is checked when the store is constructed: a mistyped ``--config-dir``
        makes :meth:`names` return nothing, which would answer every request with a 400
        blaming the caller for naming a config that does exist -- the exact inversion
        :mod:`api.errors` maps :class:`pipeline.errors.ConfigError` to a 500 to avoid.
        """
        if not directory.is_dir():
            raise ConfigError(
                f"the config directory {directory} does not exist or is not a directory, so "
                f"this service has no parameter set to run: every request would be refused as "
                f"an unknown config, which would report a deployment defect as a caller "
                f"mistake. Point --config-dir at the directory holding the shipped "
                f"*{CONFIG_SUFFIX} parameter sets"
            )
        self._directory = directory

    def names(self) -> tuple[str, ...]:
        """Return every available config name, sorted."""
        return tuple(sorted(path.stem for path in self._directory.glob(f"*{CONFIG_SUFFIX}")))

    def load(self, name: str) -> PipelineConfig:
        """Return the parsed config called ``name``.

        Raises :class:`api.errors.UnknownConfigError` naming what is available, and
        :class:`pipeline.errors.ConfigError` if the shipped file itself is malformed -- the
        second is a deployment defect rather than a caller mistake, which is why the two
        are different types and get different status codes.
        """
        available = self.names()
        if name not in available:
            raise UnknownConfigError(
                f"unknown config {name!r}; this service runs the reviewed parameter sets in "
                f"{self._directory} and does not accept a posted config. Available: "
                f"{list(available)}"
            )
        return load_config(self._directory / f"{name}{CONFIG_SUFFIX}")
