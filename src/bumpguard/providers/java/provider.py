"""The Java / Maven provider.

Everything is **pure Python** — no JDK or Maven toolchain is required:

- **Surface** is read straight out of a ``.jar`` by parsing each ``.class``
  file's metadata tables (see ``classfile.py``). Reading bytecode structurally is
  not execution; no third-party code ever runs.
- **Usage** comes from a heuristic Java source scanner (``usage.py``). Short type
  names are resolved to *candidate* symbols via the file's ``import`` directives,
  so a namespace collision can never produce a false definite breakage.
- **Fetch** pulls a target version's main jar from Maven Central over HTTP,
  size-capped and time-bounded (``maven.py``).

A Java package is addressed by its ``group:artifact`` coordinate, e.g.
``com.google.guava:guava``. ``verify_snippet`` is intentionally unsupported in
v1 (accurate hallucination detection needs semantic binding), exactly like the
.NET provider.
"""

from __future__ import annotations

from ...core.models import ImportRef, Kind, Param, ParamKind, Surface, Symbol, Usage
from ..base import InstalledInfo, Provider
from . import classfile, maven, usage as usage_scan


def _kind(s: str) -> Kind:
    try:
        return Kind(s)
    except ValueError:
        return Kind.CLASS


def _surface_from_extract(package: str, version: str | None, data: dict) -> Surface:
    symbols: dict[str, Symbol] = {}
    for s in data.get("symbols", []):
        path = s.get("path")
        if not path:
            continue
        params = [
            Param(
                name=p.get("name", ""),
                kind=ParamKind.POSITIONAL,
                has_default=p.get("hasDefault", False),
            )
            for p in (s.get("params") or [])
        ]
        # Fields are inserted before methods upstream, so on the rare field/method
        # name clash the method (inserted later) wins — consistent on both sides.
        symbols[path] = Symbol(
            dotted_path=path,
            kind=_kind(s.get("kind", "class")),
            params=params,
            accepts_varargs=s.get("acceptsVarargs", False),
            overloaded=s.get("overloaded", False),
        )
    return Surface(
        package=package,
        version=version,
        language="java",
        symbols=symbols,
        extraction_method="bytecode-metadata",
        partial=data.get("partial", True),
        notes=list(data.get("notes") or []),
    )


def resolve_usages(data: dict) -> list[Usage]:
    """Turn the source scanner's output into neutral Usages.

    A reference is 'exact' only when it is written out as a fully-qualified
    ``pkg…Type.member`` path (lower-case package root) and is not derived from an
    import or local — anything resolved through an import/local/wildcard is a
    'candidate' (later capped in severity), so collisions can't hard-break.
    """
    imports = data.get("imports", [])
    simple_to_fqn = {
        im["simple"]: im["name"]
        for im in imports
        if not im.get("static") and not im.get("wildcard") and im.get("simple")
    }
    static_members = {
        im["simple"]: im["name"]
        for im in imports
        if im.get("static") and not im.get("wildcard") and im.get("simple")
    }
    static_wildcards = [
        im["name"] for im in imports if im.get("static") and im.get("wildcard")
    ]
    wildcard_pkgs = [
        im["name"] for im in imports if not im.get("static") and im.get("wildcard")
    ]
    locals_map = {
        l["var"]: l["type"]
        for l in data.get("locals", [])
        if l.get("var") and l.get("type")
    }

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
                call_kwargs=set(),  # Java has no keyword arguments
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
        if root in locals_map:
            t = locals_map[root]
            tparts = t.split(".")
            if len(tparts) == 1 and t in simple_to_fqn:
                tparts = simple_to_fqn[t].split(".")
            base_parts = tparts + rest
            derived = True
        elif root in simple_to_fqn:
            base_parts = simple_to_fqn[root].split(".") + rest
            derived = True
        elif root in static_members and not rest:
            base_parts = static_members[root].split(".")
            derived = True
        else:
            base_parts = parts

        base = ".".join(base_parts)
        exact = (not derived) and len(base_parts) >= 3 and base_parts[0][:1].islower()
        add(base, ref, "exact" if exact else "candidate")

        for pkg in wildcard_pkgs:
            add(f"{pkg}.{base}", ref, "candidate")
        if not derived and len(parts) == 1:
            for pre in static_wildcards:
                add(f"{pre}.{base}", ref, "candidate")

    return usages


class JavaProvider(Provider):
    language = "java"
    ecosystem = "Maven"
    file_extensions = (".java",)
    supports_verify = False  # Java hallucination detection needs semantic binding

    def get_installed(self, package: str) -> InstalledInfo | None:
        parsed = maven.parse_coordinate(package)
        if parsed is None:
            return None
        group, artifact = parsed
        version = maven.latest_installed(group, artifact)
        if version is None:
            return None
        return InstalledInfo(name=package, version=version, location="m2-local-repository")

    def list_installed(self, name_filter: str | None = None) -> list[InstalledInfo]:
        out = []
        for coord in maven.list_installed():
            if name_filter and name_filter.lower() not in coord.lower():
                continue
            group, artifact = coord.split(":", 1)
            out.append(InstalledInfo(name=coord, version=maven.latest_installed(group, artifact)))
        return out

    def get_installed_surface(self, package: str) -> Surface | None:
        parsed = maven.parse_coordinate(package)
        if parsed is None:
            return None
        group, artifact = parsed
        located = maven.installed_jar_path(group, artifact)
        if located is None:
            return None
        jar_path, version = located
        try:
            with open(jar_path, "rb") as f:
                jar_bytes = f.read(maven._MAX_JAR + 1)
        except OSError:
            return None
        if len(jar_bytes) > maven._MAX_JAR:
            return None
        data = classfile.extract_surface(jar_bytes)
        if data is None:
            return None
        surface = _surface_from_extract(package, version, data)
        surface.notes.append(
            "Baseline taken from the newest version in your local ~/.m2 repository; "
            "pass from_version to compare against the version your build pins."
        )
        return surface

    def get_version_surface(self, package: str, version: str) -> Surface | None:
        parsed = maven.parse_coordinate(package)
        if parsed is None:
            return None
        group, artifact = parsed

        # Best-effort POM check: skip artifacts that ship no main jar (pom
        # aggregators, relocations, android aar) rather than returning confusing
        # empty surfaces. A failed POM fetch doesn't block the jar attempt.
        pom = maven.fetch_pom(group, artifact, version)
        if pom is not None:
            packaging, relocation = maven.pom_info(pom)
            if relocation is not None:
                return None
            if packaging is not None and packaging not in maven._JAR_PACKAGINGS:
                return None

        jar_bytes = maven.fetch_jar(group, artifact, version)
        if jar_bytes is None:
            return None
        data = classfile.extract_surface(jar_bytes)
        if data is None:
            return None
        return _surface_from_extract(package, version, data)

    def scan_usage(self, code: str, package: str | None = None) -> list[Usage]:
        data = usage_scan.scan(code)
        return resolve_usages(data)

    def scan_imports(self, code: str) -> list[ImportRef]:
        data = usage_scan.scan(code)
        refs = []
        for im in data.get("imports", []):
            name = im.get("name")
            if not name:
                continue
            imported = name + ".*" if im.get("wildcard") else name
            prefix = "import static " if im.get("static") else "import "
            refs.append(
                ImportRef(
                    top_package=name.split(".")[0],
                    imported=imported,
                    line=0,
                    raw=f"{prefix}{imported};",
                )
            )
        return refs
