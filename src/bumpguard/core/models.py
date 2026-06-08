"""Language-neutral data model shared by every provider.

A provider's only job is to populate these structures for its ecosystem
(Python, .NET, Java, ...). Everything downstream — diffing, breaking-change
classification, reporting, the MCP tools — operates purely on this model and
never needs to know which language it came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Kind(str, Enum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    ATTRIBUTE = "attribute"


class ParamKind(str, Enum):
    POSITIONAL_ONLY = "positional_only"
    POSITIONAL = "positional"
    KEYWORD_ONLY = "keyword_only"


@dataclass
class Param:
    name: str
    kind: ParamKind = ParamKind.POSITIONAL
    has_default: bool = False
    annotation: str | None = None


@dataclass
class Symbol:
    """One public name an ecosystem package exposes."""

    dotted_path: str
    kind: Kind
    signature: str | None = None
    params: list[Param] = field(default_factory=list)
    accepts_varargs: bool = False  # *args / params-array equivalent
    accepts_kwargs: bool = False  # **kwargs / dictionary-splat equivalent
    overloaded: bool = False  # multiple overloads share this path (skip param diff)

    @property
    def param_names(self) -> set[str]:
        return {p.name for p in self.params}

    @property
    def required_params(self) -> set[str]:
        """Params a caller must supply (excludes self/cls and defaulted params)."""
        return {
            p.name
            for p in self.params
            if not p.has_default and p.name not in ("self", "cls")
        }

    def max_positional(self) -> int | None:
        """How many positional args this callable accepts (None = unbounded)."""
        if self.accepts_varargs:
            return None
        return sum(
            1
            for p in self.params
            if p.kind in (ParamKind.POSITIONAL_ONLY, ParamKind.POSITIONAL)
            and p.name not in ("self", "cls")
        )

    def valid_keywords(self) -> set[str]:
        """Names a caller may pass by keyword (excludes positional-only)."""
        return {
            p.name
            for p in self.params
            if p.kind in (ParamKind.POSITIONAL, ParamKind.KEYWORD_ONLY)
            and p.name not in ("self", "cls")
        }


@dataclass
class Surface:
    """The complete public API surface of one package at one version."""

    package: str
    version: str | None
    language: str
    symbols: dict[str, Symbol] = field(default_factory=dict)
    extraction_method: str = "ast"
    partial: bool = False  # True if extraction could not be exhaustive
    dynamic_modules: set[str] = field(default_factory=set)  # define module-level __getattr__
    notes: list[str] = field(default_factory=list)

    def __contains__(self, dotted_path: str) -> bool:
        return dotted_path in self.symbols

    def get(self, dotted_path: str) -> Symbol | None:
        return self.symbols.get(dotted_path)


# ---- Change / diff results ----------------------------------------------------


class ChangeType(str, Enum):
    REMOVED = "removed"
    ADDED = "added"
    SIGNATURE_CHANGED = "signature_changed"
    KIND_CHANGED = "kind_changed"


class Severity(str, Enum):
    BREAKING = "breaking"
    POTENTIALLY_BREAKING = "potentially_breaking"
    INFO = "info"


@dataclass
class ApiChange:
    dotted_path: str
    change_type: ChangeType
    severity: Severity
    detail: str
    removed_params: list[str] = field(default_factory=list)
    added_required_params: list[str] = field(default_factory=list)


# ---- Usage (a reference found in the user's code) -----------------------------


@dataclass
class Usage:
    dotted_path: str  # best-effort resolved fully-qualified symbol
    line: int
    is_call: bool = False
    call_kwargs: set[str] = field(default_factory=set)
    positional_count: int = 0
    raw: str = ""  # the source expression, for reporting
    confidence: str = "exact"  # "exact" | "candidate" (heuristically resolved)


@dataclass
class ImportRef:
    top_package: str  # the importable top-level name, e.g. "requests"
    imported: str  # fully-qualified imported path, e.g. "requests.Session"
    line: int
    raw: str = ""


# ---- Final report -------------------------------------------------------------


@dataclass
class Finding:
    dotted_path: str
    line: int
    severity: Severity
    message: str
    suggestion: str | None = None


@dataclass
class UpgradeReport:
    package: str
    language: str
    from_version: str | None
    to_version: str | None
    findings: list[Finding] = field(default_factory=list)
    total_api_changes: int = 0
    breaking_api_changes: int = 0
    surface_partial: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def is_safe(self) -> bool:
        return not any(f.severity == Severity.BREAKING for f in self.findings)

    def to_dict(self) -> dict:
        return {
            "package": self.package,
            "language": self.language,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "safe_to_upgrade": self.is_safe,
            "summary": {
                "breaking": sum(1 for f in self.findings if f.severity == Severity.BREAKING),
                "potentially_breaking": sum(
                    1 for f in self.findings if f.severity == Severity.POTENTIALLY_BREAKING
                ),
                "info": sum(1 for f in self.findings if f.severity == Severity.INFO),
                "total_api_changes": self.total_api_changes,
                "breaking_api_changes": self.breaking_api_changes,
            },
            "findings": [
                {
                    "symbol": f.dotted_path,
                    "line": f.line,
                    "severity": f.severity.value,
                    "message": f.message,
                    "suggestion": f.suggestion,
                }
                for f in self.findings
            ],
            "surface_partial": self.surface_partial,
            "notes": self.notes,
        }
