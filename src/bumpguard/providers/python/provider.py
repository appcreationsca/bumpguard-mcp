"""Python provider implementation, wiring surface + usage + fetch together."""

from __future__ import annotations

import shutil

from ...core.models import ImportRef, Surface, Usage
from ..base import InstalledInfo, Provider
from . import fetch
from .surface import extract_symbols
from .usage import scan_imports, scan_usage


class PythonProvider(Provider):
    language = "python"
    ecosystem = "PyPI"
    file_extensions = (".py",)

    def get_installed(self, package: str) -> InstalledInfo | None:
        version = fetch.installed_version(package)
        import_name = fetch.import_name_for(package)
        located = fetch.locate_installed_source(import_name)
        if version is None and located is None:
            return None
        location = located[0] if located else None
        return InstalledInfo(name=package, version=version, location=location)

    def list_installed(self, name_filter: str | None = None) -> list[InstalledInfo]:
        out = []
        for name, version in fetch.list_installed():
            if name_filter and name_filter.lower() not in name.lower():
                continue
            out.append(InstalledInfo(name=name, version=version))
        return out

    def get_installed_surface(self, package: str) -> Surface | None:
        import_name = fetch.import_name_for(package)
        located = fetch.locate_installed_source(import_name)
        if located is None:
            return None
        root, name = located
        symbols, dynamic = extract_symbols(root, name)
        return Surface(
            package=package,
            version=fetch.installed_version(package),
            language=self.language,
            symbols=symbols,
            extraction_method="ast",
            partial=True,
            dynamic_modules=dynamic,
            notes=["Surface extracted statically from installed source."],
        )

    def get_version_surface(self, package: str, version: str) -> Surface | None:
        fetched = fetch.fetch_version_source(package, version)
        if fetched is None:
            return None
        root, name = fetched
        try:
            symbols, dynamic = extract_symbols(root, name)
        finally:
            shutil.rmtree(_tempdir_of(root), ignore_errors=True)
        return Surface(
            package=package,
            version=version,
            language=self.language,
            symbols=symbols,
            extraction_method="ast",
            partial=True,
            dynamic_modules=dynamic,
            notes=[f"Surface extracted statically from {package}=={version} wheel."],
        )

    def scan_usage(self, code: str, package: str | None = None) -> list[Usage]:
        return scan_usage(code, package)

    def scan_imports(self, code: str) -> list[ImportRef]:
        return scan_imports(code)

    def import_names(self, package: str) -> list[str]:
        return [fetch.import_name_for(package)]


def _tempdir_of(extract_root: str) -> str:
    """extract_root is '<tempdir>/unpacked'; return '<tempdir>'."""
    import os

    return os.path.dirname(extract_root.rstrip("/\\"))
