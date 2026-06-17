"""Combine an API diff with the user's actual usage into an upgrade report.

This is the heart of BumpGuard's value: not "what changed in the library" (a
plain changelog) but "what changed *that you actually use*". Pure, neutral
logic shared across every language provider.
"""

from __future__ import annotations

from .models import (
    ApiChange,
    ChangeType,
    Finding,
    Severity,
    Surface,
    UpgradeReport,
    Usage,
)


def build_upgrade_report(
    package: str,
    language: str,
    old: Surface,
    new: Surface,
    changes: list[ApiChange],
    usages: list[Usage],
) -> UpgradeReport:
    report = UpgradeReport(
        package=package,
        language=language,
        from_version=old.version,
        to_version=new.version,
        total_api_changes=len(changes),
        breaking_api_changes=sum(1 for c in changes if c.severity == Severity.BREAKING),
        surface_partial=old.partial or new.partial,
    )
    if old.partial or new.partial:
        report.notes.append(
            "API surface was extracted statically and may be incomplete for "
            "dynamically-generated members; treat 'no finding' as 'not proven safe'."
        )

    changes_by_path: dict[str, ApiChange] = {c.dotted_path: c for c in changes}

    for usage in usages:
        change = changes_by_path.get(usage.dotted_path)
        if change is None:
            continue
        finding = _finding_for(usage, change, new)
        if finding:
            _apply_confidence(finding, usage)
            report.findings.append(finding)

    # Stable, useful ordering: breaking first, then by line.
    sev_order = {Severity.BREAKING: 0, Severity.POTENTIALLY_BREAKING: 1, Severity.INFO: 2}
    report.findings.sort(key=lambda f: (sev_order[f.severity], f.line))
    return report


def _finding_for(usage: Usage, change: ApiChange, new: Surface) -> Finding | None:
    path = usage.dotted_path

    if change.change_type == ChangeType.REMOVED:
        return Finding(
            dotted_path=path,
            line=usage.line,
            severity=Severity.BREAKING,
            message=f"You use '{path}', which no longer exists in the target version. {change.detail}.",
            suggestion=_rename_suggestion(change),
        )

    if change.change_type == ChangeType.KIND_CHANGED:
        return Finding(
            dotted_path=path,
            line=usage.line,
            severity=Severity.POTENTIALLY_BREAKING,
            message=f"You use '{path}', whose kind changed. {change.detail}.",
        )

    if change.change_type == ChangeType.SIGNATURE_CHANGED:
        # Use the *new* signature plus the actual call to decide precisely
        # whether this specific call site breaks.
        if usage.is_call:
            new_sym = new.get(path)
            reasons = _call_break_reasons(new_sym, usage) if new_sym else []
            if reasons:
                return Finding(
                    dotted_path=path,
                    line=usage.line,
                    severity=Severity.BREAKING,
                    message=f"Your call to '{path}' breaks in the target version: "
                    + "; ".join(reasons)
                    + ".",
                    suggestion="Update the call to match the new signature.",
                )
        if change.added_required_params:
            return Finding(
                dotted_path=path,
                line=usage.line,
                severity=Severity.POTENTIALLY_BREAKING,
                message=(
                    f"You use '{path}', which now requires parameter(s): "
                    f"{', '.join(change.added_required_params)}."
                ),
                suggestion=f"Provide {', '.join(change.added_required_params)} when calling '{path}'.",
            )
        return Finding(
            dotted_path=path,
            line=usage.line,
            severity=Severity.INFO,
            message=f"You use '{path}', whose signature changed. {change.detail}.",
            suggestion="Review the call site against the new signature.",
        )

    return None


def _apply_confidence(finding: Finding, usage: Usage) -> None:
    """Heuristically-resolved usages (e.g. short C# names resolved via `using`)
    can't be asserted as definite breakages, so cap them at potentially-breaking
    and flag the uncertainty."""
    if usage.confidence == "exact":
        return
    if finding.severity == Severity.BREAKING:
        finding.severity = Severity.POTENTIALLY_BREAKING
    finding.message += " (symbol resolved heuristically from the import context — verify it refers to this package)"


def _call_break_reasons(new_sym, usage: Usage) -> list[str]:
    """Concrete reasons this call breaks against the new signature, or []."""
    reasons: list[str] = []

    # Keyword argument the new signature doesn't accept (removed/renamed).
    if not new_sym.accepts_kwargs:
        bad_kwargs = sorted(usage.call_kwargs - new_sym.valid_keywords())
        if bad_kwargs:
            reasons.append(
                f"keyword argument(s) {', '.join(bad_kwargs)} are no longer accepted"
            )

    # Too many positional arguments for the new signature.
    max_pos = new_sym.max_positional()
    if max_pos is not None and usage.positional_count > max_pos:
        reasons.append(
            f"it passes {usage.positional_count} positional argument(s) but the "
            f"new signature accepts at most {max_pos}"
        )

    return reasons


def _rename_suggestion(change: ApiChange) -> str | None:
    if "possibly renamed to" in change.detail:
        # Pull the suggested name back out of the detail text.
        try:
            return "Consider " + change.detail.split("possibly renamed to ")[1].rstrip(")")
        except IndexError:
            return None
    return None
