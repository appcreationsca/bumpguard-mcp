"""The Provider contract every language plugin implements.

To add support for a new ecosystem (.NET, Java, npm, ...) you implement this
interface and register it. Nothing else in BumpGuard changes — the core diff,
analysis, reporting and MCP tools all work against the neutral model.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass

from ..core.models import ImportRef, Surface, Usage


@dataclass
class InstalledInfo:
    name: str
    version: str | None
    location: str | None = None


class Provider(abc.ABC):
    #: Short language id used to route requests, e.g. "python".
    language: str = ""
    #: Human-facing ecosystem/registry name, e.g. "PyPI".
    ecosystem: str = ""
    #: Source file extensions this provider can scan, e.g. (".py",).
    file_extensions: tuple[str, ...] = ()
    #: Whether verify_snippet is meaningful for this ecosystem.
    supports_verify: bool = True

    @abc.abstractmethod
    def get_installed(self, package: str) -> InstalledInfo | None:
        """Return install info for ``package`` in the current environment, or None."""

    @abc.abstractmethod
    def list_installed(self, name_filter: str | None = None) -> list[InstalledInfo]:
        """List packages installed in the current environment."""

    @abc.abstractmethod
    def get_installed_surface(self, package: str) -> Surface | None:
        """Extract the public API surface of the *installed* version."""

    @abc.abstractmethod
    def get_version_surface(self, package: str, version: str) -> Surface | None:
        """Fetch ``package==version`` (without installing into the live env) and
        extract its public API surface. Returns None if it can't be fetched."""

    @abc.abstractmethod
    def scan_usage(self, code: str, package: str | None = None) -> list[Usage]:
        """Parse ``code`` and return references to ``package`` (or all packages)."""

    def scan_imports(self, code: str) -> list[ImportRef]:
        """Return import statements found in ``code``. Default: none. Providers
        override this to power import-existence / typo checks."""
        return []

    def parse_error(self, code: str) -> str | None:
        """Return a human-readable reason if ``code`` is *definitely* unparseable
        for this ecosystem, else None.

        This lets the neutral service layer avoid reporting ``verified: true`` for
        a snippet that doesn't even parse (the scanners return no imports/usages
        for broken code, which would otherwise look like a clean bill of health).
        The default returns None — "parseable, or this provider has no cheap,
        execution-free way to tell" — so it stays honest rather than guessing.
        """
        return None

    def import_names(self, package: str) -> list[str]:
        """Importable top-level name(s) for a distribution. Default: the name
        itself; providers override when import name != distribution name."""
        return [package]

    def suggest_similar_installed(self, name: str, limit: int = 3) -> list[str]:
        """Default typo/slopsquat helper: nearest installed package names."""
        import difflib

        names = [p.name for p in self.list_installed()]
        return difflib.get_close_matches(name, names, n=limit, cutoff=0.6)
