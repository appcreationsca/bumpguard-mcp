"""Opt-in integration/performance check for a large SQLAlchemy API diff.

Run with ``BUMPGUARD_LIVE=1 pytest -s tests/test_sqlalchemy_diff_perf.py``.
The flag keeps this network-backed, potentially slow check out of the normal
test suite. Timings separate wheel fetch/surface extraction from API diffing.
"""

from __future__ import annotations

import difflib
import os
import re
from collections import Counter
from time import perf_counter

import pytest

from bumpguard.core.analyze import build_upgrade_report
from bumpguard.core import diff as diff_module
from bumpguard.core.models import Severity, Usage
from bumpguard.providers.python.provider import PythonProvider


def _legacy_rename_hints(removed, added, old, new):
    """Original all-pairs implementation, kept here as a benchmark baseline."""
    hints = {}
    added_by_leaf = {}
    for path in added:
        leaf = path.rsplit(".", 1)[-1]
        added_by_leaf.setdefault(leaf, []).append(path)

    for path in removed:
        leaf = path.rsplit(".", 1)[-1]
        if leaf in added_by_leaf:
            hints[path] = added_by_leaf[leaf][0]
            continue
        match = difflib.get_close_matches(path, list(added), n=1, cutoff=0.8)
        if match:
            hints[path] = match[0]
    return hints


def _semantic_result(changes):
    """Drop only the rename suggestion text; preserve all API classifications."""
    return Counter(
        (
            change.dotted_path,
            change.change_type,
            change.severity,
            re.sub(r" \(possibly renamed to '[^']+'\)$", "", change.detail),
            tuple(change.removed_params),
            tuple(change.added_required_params),
        )
        for change in changes
    )


def _rename_suggestions(changes):
    return {
        change.dotted_path: match.group(1)
        for change in changes
        if (match := re.search(r" \(possibly renamed to '([^']+)'\)$", change.detail))
    }


@pytest.mark.skipif(
    os.environ.get("BUMPGUARD_LIVE") != "1",
    reason="live SQLAlchemy version comparison; set BUMPGUARD_LIVE=1",
)
def test_sqlalchemy_version_diff_matches_legacy(monkeypatch):
    provider = PythonProvider()

    started = perf_counter()
    old = provider.get_version_surface("SQLAlchemy", "2.1.1")
    old_seconds = perf_counter() - started
    assert old is not None, "could not fetch/extract SQLAlchemy 2.1.1"
    print(
        f"SQLAlchemy 2.1.1 surface: {len(old.symbols)} symbols in {old_seconds:.2f}s",
        flush=True,
    )

    started = perf_counter()
    new = provider.get_version_surface("SQLAlchemy", "2.0.54")
    new_seconds = perf_counter() - started
    assert new is not None, "could not fetch/extract SQLAlchemy 2.0.54"
    print(
        f"SQLAlchemy 2.0.54 surface: {len(new.symbols)} symbols in {new_seconds:.2f}s",
        flush=True,
    )

    removed = old.symbols.keys() - new.symbols.keys()
    added = new.symbols.keys() - old.symbols.keys()
    print(f"Diff input: {len(removed)} removed, {len(added)} added symbols", flush=True)

    started = perf_counter()
    optimized = diff_module.diff_surfaces(old, new)
    optimized_seconds = perf_counter() - started
    print(
        f"Optimized diff: {len(optimized)} changes in {optimized_seconds:.2f}s",
        flush=True,
    )

    monkeypatch.setattr(diff_module, "_rename_hints", _legacy_rename_hints)
    started = perf_counter()
    legacy = diff_module.diff_surfaces(old, new)
    legacy_seconds = perf_counter() - started
    print(f"Legacy diff: {len(legacy)} changes in {legacy_seconds:.2f}s", flush=True)

    assert optimized, "expected differences between SQLAlchemy 2.1.1 and 2.0.54"
    assert _semantic_result(optimized) == _semantic_result(legacy)

    optimized_hints = _rename_suggestions(optimized)
    legacy_hints = _rename_suggestions(legacy)
    shared_paths = optimized_hints.keys() & legacy_hints.keys()
    same_hints = sum(
        optimized_hints[path] == legacy_hints[path] for path in shared_paths
    )
    changed_hints = len(shared_paths) - same_hints
    print(
        "Rename hints: "
        f"optimized={len(optimized_hints)}, legacy={len(legacy_hints)}, "
        f"same={same_hints}, changed={changed_hints}, "
        f"optimized-only={len(optimized_hints.keys() - legacy_hints.keys())}, "
        f"legacy-only={len(legacy_hints.keys() - optimized_hints.keys())}",
        flush=True,
    )


@pytest.mark.skipif(
    os.environ.get("BUMPGUARD_LIVE") != "1",
    reason="live SQLAlchemy surface check; set BUMPGUARD_LIVE=1",
)
def test_sqlalchemy_mapper_is_in_target_surface():
    provider = PythonProvider()
    old = provider.get_version_surface("SQLAlchemy", "2.0.54")
    new = provider.get_version_surface("SQLAlchemy", "2.1.1")
    assert old is not None, "could not fetch/extract SQLAlchemy 2.0.54"
    assert new is not None, "could not fetch/extract SQLAlchemy 2.1.1"
    mapper_path = "sqlalchemy.orm.Mapper"
    related = sorted(
        path for path in new.symbols
        if path.endswith(".Mapper") and path.startswith("sqlalchemy.orm")
    )
    print(f"{mapper_path} in old surface: {mapper_path in old.symbols}", flush=True)
    print(f"{mapper_path} in target surface: {mapper_path in new.symbols}", flush=True)
    print(f"Related Mapper paths: {related[:20]}", flush=True)
    changes = [
        change for change in diff_module.diff_surfaces(old, new)
        if change.dotted_path == mapper_path
    ]
    print(f"Mapper diff entries: {changes}", flush=True)
    assert mapper_path in old.symbols
    assert mapper_path in new.symbols
    assert all(change.change_type.value != "removed" for change in changes)
    assert len(changes) == 1
    assert changes[0].change_type.value == "signature_changed"
    assert "non_primary" in changes[0].removed_params

    annotation_use = build_upgrade_report(
        "SQLAlchemy",
        "python",
        old,
        new,
        changes,
        [Usage(dotted_path=mapper_path, line=1)],
    )
    constructor_use = build_upgrade_report(
        "SQLAlchemy",
        "python",
        old,
        new,
        changes,
        [
            Usage(
                dotted_path=mapper_path,
                line=2,
                is_call=True,
                call_kwargs={"non_primary"},
            )
        ],
    )
    assert annotation_use.findings[0].severity == Severity.INFO
    assert constructor_use.findings[0].severity == Severity.BREAKING
