"""Language-neutral diffing and breaking-change classification.

Given two API Surfaces (an older and a newer version of the same package),
work out what changed and how dangerous each change is. This module is pure
data-in / data-out and is shared by every provider.
"""

from __future__ import annotations

import difflib
from bisect import bisect_left
from collections import Counter, defaultdict

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

    # Keyword-capable params a caller must now supply but didn't have to before —
    # either brand-new names OR existing params that lost their default (optional
    # -> required). Both break a caller that omitted the argument. Subtracting
    # old.required_params (not old.param_names) is what catches the optional ->
    # required case. New positional-only/arity changes are caught by the
    # positional-count check during analysis.
    added_required = sorted(
        (new.required_params & new.valid_keywords()) - old.required_params
    )

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
            bits.append(f"newly-required parameter(s): {', '.join(added_required)}")
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
    """Suggest likely moves/renames without comparing every pair of paths.

    Exact leaf-name matches are found through an index. Fuzzy candidates are
    retrieved by shared character bigrams, capped, then ranked by leaf-name
    and parent-path similarity. This keeps the expensive string comparisons
    local while avoiding the old removed × added SequenceMatcher scan.
    """
    hints: dict[str, str] = {}
    if not removed or not added:
        return hints

    def parts(path: str) -> tuple[str, str]:
        parent, _, leaf = path.rpartition(".")
        return parent, leaf

    def normalized(value: str) -> str:
        return "".join(char for char in value.casefold() if char.isalnum())

    def grams(value: str) -> set[str]:
        value = normalized(value)
        if len(value) < 2:
            return {value} if value else set()
        return {value[index:index + 2] for index in range(len(value) - 1)}

    added_by_leaf: dict[str, list[str]] = defaultdict(list)
    added_by_gram: dict[str, list[str]] = defaultdict(list)
    added_parts: dict[str, tuple[str, str]] = {}
    for path in sorted(added):
        parent, leaf = parts(path)
        added_parts[path] = (parent, leaf)
        added_by_leaf[leaf].append(path)
        for gram in grams(leaf):
            added_by_gram[gram].append(path)

    # Reuse parent-path comparisons: large packages often have many symbols
    # in a small number of modules.
    parent_similarity: dict[tuple[str, str], float] = {}

    def parent_ratio(left: str, right: str) -> float:
        key = (left, right)
        ratio = parent_similarity.get(key)
        if ratio is None:
            ratio = difflib.SequenceMatcher(None, left, right, autojunk=False).ratio()
            parent_similarity[key] = ratio
        return ratio

    max_candidates = 64
    # Keep the total number of votes per removed symbol bounded by the number
    # of its distinct bigrams times this small posting limit.
    max_gram_posting = 32
    for removed_path in sorted(removed):
        old_parent, old_leaf = parts(removed_path)
        old_kind = old.symbols[removed_path].kind
        same_leaf = added_by_leaf.get(old_leaf, [])
        # Prefer same-kind candidates when duplicate leaf names exist.
        if same_leaf:
            # The leaf bucket can be very large for names such as ``__init__``.
            # Inspect only nearby paths in the sorted bucket before ranking.
            middle = bisect_left(same_leaf, removed_path)
            nearby = same_leaf[
                max(0, middle - max_candidates // 2):middle + max_candidates // 2
            ]
            compatible = [path for path in nearby if new.symbols[path].kind == old_kind]
            candidates = compatible or nearby
            hints[removed_path] = max(
                candidates,
                key=lambda path: (
                    parent_ratio(old_parent, added_parts[path][0]),
                    path,
                ),
            )
            continue

        # Candidate generation is sparse: only symbols sharing leaf-name
        # bigrams are ranked. Very common bigrams act like stop words: skipping
        # their postings bounds candidate collection even for huge packages.
        # Kind mismatches are discarded where possible.
        votes: Counter[str] = Counter()
        for gram in grams(old_leaf):
            posting = added_by_gram.get(gram, ())
            if len(posting) <= max_gram_posting:
                votes.update(posting)
        compatible_votes = {
            path: count
            for path, count in votes.items()
            if new.symbols[path].kind == old_kind
        }
        if not compatible_votes:
            compatible_votes = dict(votes)
        if not compatible_votes:
            continue

        shortlist = sorted(
            compatible_votes,
            key=lambda path: (-compatible_votes[path], path),
        )[:max_candidates]
        old_leaf_norm = normalized(old_leaf)
        ranked: list[tuple[float, str]] = []
        for path in shortlist:
            candidate_parent, candidate_leaf = added_parts[path]
            leaf_ratio = difflib.SequenceMatcher(
                None, old_leaf_norm, normalized(candidate_leaf), autojunk=False
            ).ratio()
            score = 0.8 * leaf_ratio + 0.2 * parent_ratio(old_parent, candidate_parent)
            ranked.append((score, path))

        score, match = max(ranked, key=lambda item: (item[0], item[1]))
        # Require a strong leaf match; shared package prefixes alone should not
        # turn unrelated API removals into rename suggestions.
        if score >= 0.78:
            hints[removed_path] = match
    return hints
