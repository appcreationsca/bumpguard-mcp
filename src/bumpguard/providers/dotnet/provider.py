"""The .NET / NuGet provider.

Surface comes from reflection-only metadata (no code execution); usage comes
from Roslyn syntax parsing. Short C# names are resolved to *candidate* symbols
via the file's `using` directives — these are reported at reduced confidence so
namespace collisions never produce a false definite breakage.
"""

from __future__ import annotations

import shutil

from ...core.models import ImportRef, Kind, Param, ParamKind, Surface, Symbol, Usage
from ..base import InstalledInfo, Provider
from . import helper, nuget


def _surface_from_json(package: str, version: str | None, data: dict) -> Surface:
    symbols: dict[str, Symbol] = {}
    for s in data.get("symbols", []):
        path = s.get("path")
        if not path:
            continue
        kind = _kind(s.get("kind", "class"))
        params = [
            Param(name=p.get("name", ""), kind=ParamKind.POSITIONAL, has_default=p.get("hasDefault", False))
            for p in (s.get("params") or [])
        ]
        symbols[path] = Symbol(
            dotted_path=path,
            kind=kind,
            params=params,
            accepts_varargs=s.get("acceptsVarargs", False),
            overloaded=s.get("overloaded", False),
        )
    return Surface(
        package=package,
        version=version,
        language="dotnet",
        symbols=symbols,
        extraction_method="reflection-metadata",
        partial=data.get("partial", True),
        notes=["Public API extracted via reflection-only metadata (no code executed)."],
    )


def _kind(s: str) -> Kind:
    try:
        return Kind(s)
    except ValueError:
        return Kind.CLASS


def resolve_usages(data: dict) -> list[Usage]:
    """Turn the extractor's usage JSON into neutral Usages.

    Fully-qualified references are reported as 'exact'; short names resolved via
    `using`/aliases/locals are 'candidate' (and later capped in severity).
    """
    usings = data.get("usings", [])
    namespaces = [u["name"] for u in usings if u.get("name") and not u.get("isStatic") and not u.get("alias")]
    static_types = [u["name"] for u in usings if u.get("isStatic") and u.get("name")]
    aliases = {u["alias"]: u["name"] for u in usings if u.get("alias") and u.get("name")}
    locals_map = {l["var"]: l["type"] for l in data.get("locals", []) if l.get("var") and l.get("type")}

    usages: list[Usage] = []
    seen: set[tuple[str, int]] = set()

    def add(path: str, ref: dict, confidence: str) -> None:
        key = (path, ref.get("line", 0))
        if key in seen:
            return
        seen.add(key)
        usages.append(
            Usage(
                dotted_path=path,
                line=ref.get("line", 0),
                is_call=ref.get("isCall", False),
                call_kwargs=set(ref.get("kwargs") or []),
                positional_count=ref.get("positionalCount", 0),
                raw=ref.get("name", ""),
                confidence=confidence,
            )
        )

    for ref in data.get("refs", []):
        name = ref.get("name") or ""
        parts = name.split(".")
        if not parts or not parts[0]:
            continue
        root, rest = parts[0], parts[1:]

        derived = False
        if root in aliases:
            base_parts = aliases[root].split(".") + rest
            derived = True
        elif root in locals_map:
            base_parts = locals_map[root].split(".") + rest
            derived = True
        else:
            base_parts = parts

        base = ".".join(base_parts)
        # A written-out, dotted path (>= namespace.Type.Member) is trustworthy;
        # short or locally-derived names are candidates.
        exact = (not derived) and len(base_parts) >= 3
        add(base, ref, "exact" if exact else "candidate")

        for ns in namespaces:
            add(f"{ns}.{base}", ref, "candidate")
        for st in static_types:
            add(f"{st}.{base}", ref, "candidate")

    return usages


class DotNetProvider(Provider):
    language = "dotnet"
    ecosystem = "NuGet"
    file_extensions = (".cs",)
    supports_verify = False  # C# hallucination detection needs semantic binding

    def get_installed(self, package: str) -> InstalledInfo | None:
        version = nuget.latest_installed(package)
        if version is None:
            return None
        return InstalledInfo(name=package, version=version, location="nuget-global-cache")

    def list_installed(self, name_filter: str | None = None) -> list[InstalledInfo]:
        out = []
        for name in nuget.list_installed():
            if name_filter and name_filter.lower() not in name.lower():
                continue
            out.append(InstalledInfo(name=name, version=nuget.latest_installed(name)))
        return out

    def get_installed_surface(self, package: str) -> Surface | None:
        located = nuget.installed_surface_dir(package)
        if located is None:
            return None
        tfm_dir, version = located
        data = helper.surface(tfm_dir)
        if data is None:
            return None
        surface = _surface_from_json(package, version, data)
        surface.notes.append(
            "Baseline taken from the newest version in the NuGet cache; pass "
            "from_version to compare against the version your project pins."
        )
        return surface

    def get_version_surface(self, package: str, version: str) -> Surface | None:
        fetched = nuget.fetch_version_dir(package, version)
        if fetched is None:
            return None
        tfm_dir, tmp = fetched
        try:
            data = helper.surface(tfm_dir)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if data is None:
            return None
        return _surface_from_json(package, version, data)

    def scan_usage(self, code: str, package: str | None = None) -> list[Usage]:
        data = helper.usage(code)
        if data is None:
            return []
        usages = resolve_usages(data)
        if package:
            root = package.lower()
            usages = [u for u in usages if u.dotted_path.lower().startswith(root)] or usages
        return usages

    def scan_imports(self, code: str) -> list[ImportRef]:
        data = helper.usage(code)
        if data is None:
            return []
        refs = []
        for u in data.get("usings", []):
            name = u.get("name")
            if not name:
                continue
            refs.append(ImportRef(top_package=name.split(".")[0], imported=name, line=0, raw=f"using {name}"))
        return refs
