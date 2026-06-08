"""BumpGuard MCP server.

Exposes BumpGuard's upgrade-impact and API-verification capabilities as MCP
tools over stdio. Tool docstrings are written for the *agent* — they explain
when and why to call each tool.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .core import service

mcp = FastMCP("bumpguard")


@mcp.tool()
def check_upgrade(
    package: str,
    to_version: str,
    code: str,
    from_version: str | None = None,
    language: str = "python",
) -> dict:
    """Check what breaks in YOUR code when you upgrade a dependency.

    Call this BEFORE bumping a dependency version. It extracts the real public
    API of the currently-installed (or `from_version`) package and the target
    `to_version`, diffs them, then scans the provided `code` to report exactly
    which of your usages break — with line numbers, severity, and fix hints.

    Args:
        package: Distribution name to upgrade (e.g. "pandas").
        to_version: The version you want to move to (e.g. "2.2.0").
        code: The source code that uses the package (a file or snippet).
        from_version: Optional baseline version; defaults to what is installed.
        language: Ecosystem provider id. Default "python".

    Returns a report with `safe_to_upgrade`, a severity `summary`, and per-line
    `findings`. Note: analysis is static, so "no findings" means "nothing
    proven to break", not a guarantee.
    """
    return service.check_upgrade(language, package, to_version, code, from_version)


@mcp.tool()
def diff_versions(
    package: str,
    to_version: str,
    from_version: str | None = None,
    language: str = "python",
) -> dict:
    """List the API changes between two versions of a package (no code scan).

    Use this to understand a library's breaking changes in the abstract — e.g.
    when planning a migration. For "what breaks in my code", use check_upgrade
    instead. Defaults the baseline to the installed version.
    """
    return service.diff_versions(language, package, to_version, from_version)


@mcp.tool()
def verify_snippet(code: str, language: str = "python") -> dict:
    """Verify code against the ACTUALLY-INSTALLED packages to catch hallucinations.

    Call this after generating code to check that the imports and API calls it
    uses really exist in this environment. Flags: imported packages that aren't
    installed (with typo/slopsquat suggestions) and attributes/methods that
    can't be found on installed modules/classes. Static analysis only — treat
    'medium' findings as "verify", not "definitely wrong".
    """
    return service.verify_snippet(language, code)


@mcp.tool()
def check_import(package: str, language: str = "python") -> dict:
    """Check whether a package is installed; if not, suggest close real names.

    Use this before writing an import to avoid hallucinated or typo'd package
    names (a common source of slopsquatting risk).
    """
    return service.check_import(language, package)


@mcp.tool()
def list_symbols(
    package: str,
    version: str | None = None,
    name_filter: str | None = None,
    language: str = "python",
) -> dict:
    """List the REAL public API (functions/classes/methods + signatures) of a package.

    Use this to discover the correct API instead of guessing — for the installed
    version, or a specific `version` (fetched without installing). Optionally
    filter symbols by a substring of their dotted path.
    """
    return service.list_symbols(language, package, version, name_filter)


@mcp.tool()
def list_languages() -> dict:
    """List the ecosystem providers BumpGuard currently supports."""
    return service.list_languages()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
