"""High-level operations exposed by the MCP tools.

Language-agnostic: each function picks a provider by ``language`` and drives the
shared core (diff, analysis, reporting). Returns plain JSON-friendly dicts.
"""

from __future__ import annotations

from .analyze import build_upgrade_report
from .diff import diff_surfaces
from .models import Severity
from .registry import available_languages, get_provider, load_default_providers

load_default_providers()

_MAX_ITEMS = 300


def _no_provider(language: str) -> dict:
    return {
        "error": f"No provider registered for language '{language}'.",
        "available_languages": available_languages(),
    }


def check_upgrade(
    language: str,
    package: str,
    to_version: str,
    code: str,
    from_version: str | None = None,
) -> dict:
    """Headline tool: which parts of *your* code break upgrading ``package``."""
    provider = get_provider(language)
    if provider is None:
        return _no_provider(language)

    if from_version:
        old = provider.get_version_surface(package, from_version)
        if old is None:
            return {"error": f"Could not fetch {package}=={from_version} to compare from."}
    else:
        old = provider.get_installed_surface(package)
        if old is None:
            return {
                "error": (
                    f"'{package}' is not installed (or its source can't be found). "
                    "Install it, or pass from_version to compare two released versions."
                ),
                "suggestions": provider.suggest_similar_installed(package),
            }

    new = provider.get_version_surface(package, to_version)
    if new is None:
        return {
            "error": (
                f"Could not fetch {package}=={to_version}. Check the version exists "
                "and a wheel is available, and that you have network access."
            )
        }

    changes = diff_surfaces(old, new)
    usages = provider.scan_usage(code, None)
    report = build_upgrade_report(package, language, old, new, changes, usages)

    result = report.to_dict()
    import_roots = set(provider.import_names(package))

    def _is_used(u) -> bool:
        # A usage counts as "used" if it points under an importable root for the
        # package, or if it resolves to a real symbol in either surface. The
        # latter is essential for ecosystems where the distribution coordinate
        # differs from the symbol namespace (e.g. Java's group:artifact vs the
        # actual Java package), where a prefix match alone would never fire.
        if any(u.dotted_path == r or u.dotted_path.startswith(r + ".") for r in import_roots):
            return True
        return u.dotted_path in new.symbols or u.dotted_path in old.symbols

    used = bool(report.findings) or any(_is_used(u) for u in usages)
    if not used:
        result["notes"].append(
            f"No usages of '{package}' were detected in the provided code, so this "
            "result reflects only that — provide the code that imports the package."
        )
    return result


def diff_versions(
    language: str,
    package: str,
    to_version: str,
    from_version: str | None = None,
) -> dict:
    """Raw API diff between two versions (no user-code scan)."""
    provider = get_provider(language)
    if provider is None:
        return _no_provider(language)

    old = (
        provider.get_version_surface(package, from_version)
        if from_version
        else provider.get_installed_surface(package)
    )
    if old is None:
        return {"error": f"Could not obtain the 'from' surface for {package}."}
    new = provider.get_version_surface(package, to_version)
    if new is None:
        return {"error": f"Could not fetch {package}=={to_version}."}

    changes = diff_surfaces(old, new)
    breaking = [c for c in changes if c.severity == Severity.BREAKING]
    return {
        "package": package,
        "language": language,
        "from_version": old.version,
        "to_version": new.version,
        "summary": {
            "total_changes": len(changes),
            "breaking": len(breaking),
        },
        "breaking_changes": [
            {"symbol": c.dotted_path, "change": c.change_type.value, "detail": c.detail}
            for c in breaking[:_MAX_ITEMS]
        ],
        "other_changes": [
            {
                "symbol": c.dotted_path,
                "change": c.change_type.value,
                "severity": c.severity.value,
                "detail": c.detail,
            }
            for c in changes
            if c.severity != Severity.BREAKING
        ][:_MAX_ITEMS],
        "surface_partial": old.partial or new.partial,
    }


def verify_snippet(language: str, code: str) -> dict:
    """Bonus tool: flag imports/symbols that don't exist in the *installed* env
    (catches hallucinated packages, typos, and likely-invented attributes)."""
    provider = get_provider(language)
    if provider is None:
        return _no_provider(language)

    if not getattr(provider, "supports_verify", True):
        return {
            "language": language,
            "verified": None,
            "findings": [],
            "note": (
                f"verify_snippet is not supported for {language} in this version "
                "(accurate detection needs semantic binding). Use check_upgrade "
                "and diff_versions instead."
            ),
        }

    findings: list[dict] = []
    info_by_top: dict[str, object] = {}
    surfaces: dict[str, object] = {}

    # Short-circuit on code that doesn't even parse: the scanners return no
    # imports/usages for broken code, which would otherwise be reported as a
    # clean "verified: true". Surface it honestly instead.
    perr = provider.parse_error(code)
    if perr:
        return {
            "language": language,
            "verified": False,
            "findings": [
                {
                    "line": 0,
                    "severity": "high",
                    "symbol": None,
                    "message": f"Code does not parse, so it could not be verified: {perr}",
                    "suggestion": None,
                }
            ],
            "note": (
                "The snippet failed to parse, so no symbol-level verification was "
                "possible. Fix the syntax error and re-run."
            ),
        }

    def installed(top: str):
        if top not in info_by_top:
            info_by_top[top] = provider.get_installed(top)
        return info_by_top[top]

    def surface_of(top: str):
        if top not in surfaces:
            surfaces[top] = provider.get_installed_surface(top) if installed(top) else None
        return surfaces[top]

    # 1. Imports: missing packages (typo/slopsquat) and missing imported names.
    for ref in provider.scan_imports(code):
        top = ref.top_package
        if installed(top) is None:
            if not any(f["symbol"] == top for f in findings):
                findings.append(
                    {
                        "line": ref.line,
                        "severity": "high",
                        "symbol": top,
                        "message": f"Import '{top}' has no matching installed package.",
                        "suggestion": _suggest(provider.suggest_similar_installed(top)),
                    }
                )
            continue
        surface = surface_of(top)
        if surface is None or ref.imported.endswith(".*"):
            continue
        if ref.imported in surface.symbols:
            continue
        parent = ref.imported.rsplit(".", 1)[0]
        if parent in surface.symbols and not _under_dynamic(ref.imported, surface):
            findings.append(
                {
                    "line": ref.line,
                    "severity": "medium",
                    "symbol": ref.imported,
                    "message": (
                        f"'{ref.imported}' was not found in installed {top}"
                        f"{_ver(surface)} via static analysis."
                    ),
                    "suggestion": None,
                }
            )

    # 2. Attribute/method usages on installed packages.
    flagged = {f["symbol"] for f in findings}
    for usage in provider.scan_usage(code, None):
        top = usage.dotted_path.split(".", 1)[0]
        if installed(top) is None:
            continue  # already covered by the import check
        surface = surface_of(top)
        if surface is None or usage.dotted_path in surface.symbols:
            continue
        if usage.dotted_path in flagged:
            continue
        parent = usage.dotted_path.rsplit(".", 1)[0]
        # Only flag when we can see the parent namespace and it isn't dynamic —
        # avoids false positives on dynamically-created members.
        if parent in surface.symbols and not _under_dynamic(usage.dotted_path, surface):
            findings.append(
                {
                    "line": usage.line,
                    "severity": "medium",
                    "symbol": usage.dotted_path,
                    "message": (
                        f"'{usage.dotted_path}' was not found on installed "
                        f"{top}{_ver(surface)} via static analysis. "
                        "It may be hallucinated, renamed, or dynamically created."
                    ),
                    "suggestion": None,
                }
            )
            flagged.add(usage.dotted_path)

    findings.sort(key=lambda f: (0 if f["severity"] == "high" else 1, f["line"]))
    return {
        "language": language,
        "verified": len(findings) == 0,
        "findings": findings[:_MAX_ITEMS],
        "note": (
            "Static analysis only. Absence of findings is not proof of correctness; "
            "a 'medium' finding may be a dynamically-created member."
        ),
    }


def _under_dynamic(path: str, surface) -> bool:
    parts = path.split(".")
    for i in range(1, len(parts)):
        if ".".join(parts[:i]) in surface.dynamic_modules:
            return True
    return False


def _ver(surface) -> str:
    v = getattr(surface, "version", None)
    return f" v{v}" if v else ""


def check_import(language: str, package: str) -> dict:
    """Is ``package`` installed? Return version + typo suggestions if not."""
    provider = get_provider(language)
    if provider is None:
        return _no_provider(language)

    info = provider.get_installed(package)
    if info is None:
        return {
            "package": package,
            "installed": False,
            "suggestions": provider.suggest_similar_installed(package),
            "message": f"'{package}' is not installed in this environment.",
        }
    return {
        "package": package,
        "installed": True,
        "version": info.version,
        "location": info.location,
    }


def list_symbols(
    language: str,
    package: str,
    version: str | None = None,
    name_filter: str | None = None,
) -> dict:
    """List the real public API of a package (installed, or a fetched version)."""
    provider = get_provider(language)
    if provider is None:
        return _no_provider(language)

    surface = (
        provider.get_version_surface(package, version)
        if version
        else provider.get_installed_surface(package)
    )
    if surface is None:
        return {"error": f"Could not read the API surface for {package}."}

    items = []
    for path, sym in sorted(surface.symbols.items()):
        if name_filter and name_filter.lower() not in path.lower():
            continue
        items.append(
            {"symbol": path, "kind": sym.kind.value, "signature": sym.signature}
        )
    truncated = len(items) > _MAX_ITEMS
    return {
        "package": package,
        "version": surface.version,
        "count": len(items),
        "truncated": truncated,
        "symbols": items[:_MAX_ITEMS],
    }


def list_languages() -> dict:
    provider_info = []
    for lang in available_languages():
        p = get_provider(lang)
        provider_info.append({"language": lang, "ecosystem": getattr(p, "ecosystem", "")})
    return {"languages": provider_info}


def _suggest(matches: list[str]) -> str | None:
    if not matches:
        return None
    return "Did you mean: " + ", ".join(matches) + "?"
