"""Language-neutral diffing and breaking-change classification.

Given two API Surfaces (an older and a newer version of the same package),
work out what changed and how dangerous each change is. This module is pure
data-in / data-out and is shared by every provider.
"""

from __future__ import annotations

import difflib

from .models import ApiChange, ChangeType, Severity, Surface, Symbol


def diff_surfaces(old: Surface, new: Surface) -> list[ApiChange]:
    """Compare two surfaces and return the meaningful API changes."""
    changes: list[ApiChange] = []
    old_paths = set(old.symbols)
    new_paths = set(new.symbols)

    removed = old_paths - new_paths
    added = new_paths - old_paths
    common = old_paths & new_paths

    # Try to pair removed symbols with plausibly-renamed new symbols so we can
    # give the agent a concrete "did you mean" instead of a dead end.
    rename_hints = _rename_hints(removed, added, old, new)

    for path in sorted(removed):
        sym = old.symbols[path]
        hint = rename_hints.get(path)
        detail = f"{sym.kind.value} '{path}' was removed"
        if hint:
            detail += f" (possibly renamed to '{hint}')"
        changes.append(
            ApiChange(
                dotted_path=path,
                change_type=ChangeType.REMOVED,
                severity=Severity.BREAKING,
                detail=detail,
            )
        )

    for path in sorted(common):
        change = _diff_symbol(old.symbols[path], new.symbols[path])
        if change:
            changes.append(change)

    for path in sorted(added):
        sym = new.symbols[path]
        changes.append(
            ApiChange(
                dotted_path=path,
                change_type=ChangeType.ADDED,
                severity=Severity.INFO,
                detail=f"{sym.kind.value} '{path}' was added",
            )
        )

    return changes


def _diff_symbol(old: Symbol, new: Symbol) -> ApiChange | None:
    if old.kind != new.kind:
        return ApiChange(
            dotted_path=new.dotted_path,
            change_type=ChangeType.KIND_CHANGED,
            severity=Severity.POTENTIALLY_BREAKING,
            detail=f"'{new.dotted_path}' changed from {old.kind.value} to {new.kind.value}",
        )

    if old.kind.value not in ("function", "method", "class"):
        return None

    # Overloaded members (common in .NET) can't be param-diffed reliably by a
    # single signature, so we only track their presence, not their parameters.
    if old.overloaded or new.overloaded:
        return None

    removed_params = sorted(old.param_names - new.param_names)
    # A removed parameter only breaks *keyword* callers if **kwargs can absorb
    # it; positional breakage is decided precisely at usage-analysis time.
    breaking_removed = removed_params if not new.accepts_kwargs else []

    # Only count keyword-capable additions here; new positional-only/arity
    # changes are caught by the positional-count check during analysis.
    added_required = sorted((new.required_params & new.valid_keywords()) - old.param_names)

    if not breaking_removed and not added_required and not removed_params:
        # Signatures are call-compatible (params unchanged, or removals absorbed
        # by **kwargs and offset by nothing added). Nothing actionable — we only
        # report changes that can affect a caller, not e.g. return-type edits.
        return None

    if breaking_removed or added_required:
        severity = Severity.BREAKING
        bits = []
        if breaking_removed:
            bits.append(f"removed parameter(s): {', '.join(breaking_removed)}")
        if added_required:
            bits.append(f"new required parameter(s): {', '.join(added_required)}")
        detail = f"signature of '{new.dotted_path}' changed — " + "; ".join(bits)
    else:
        severity = Severity.POTENTIALLY_BREAKING
        detail = (
            f"signature of '{new.dotted_path}' changed "
            f"(parameter(s) {', '.join(removed_params)} removed but absorbed by **kwargs)"
        )

    return ApiChange(
        dotted_path=new.dotted_path,
        change_type=ChangeType.SIGNATURE_CHANGED,
        severity=severity,
        detail=detail,
        removed_params=removed_params,
        added_required_params=added_required,
    )


def _rename_hints(
    removed: set[str], added: set[str], old: Surface, new: Surface
) -> dict[str, str]:
    """Best-effort: pair each removed symbol with a similar added one."""
    hints: dict[str, str] = {}
    added_by_leaf: dict[str, list[str]] = {}
    for a in added:
        leaf = a.rsplit(".", 1)[-1]
        added_by_leaf.setdefault(leaf, []).append(a)

    for r in removed:
        leaf = r.rsplit(".", 1)[-1]
        # Same leaf name in a different module/namespace -> very likely a move.
        if leaf in added_by_leaf:
            hints[r] = added_by_leaf[leaf][0]
            continue
        # Otherwise fall back to fuzzy matching on the full dotted path.
        match = difflib.get_close_matches(r, list(added), n=1, cutoff=0.8)
        if match:
            hints[r] = match[0]
    return hints
