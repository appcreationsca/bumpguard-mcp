from conftest import surface_for

from bumpguard.core.diff import diff_surfaces
from bumpguard.core.models import ChangeType, Severity


def _changes():
    old = surface_for("pkgv1", "1.0")
    new = surface_for("pkgv2", "2.0")
    return {c.dotted_path: c for c in diff_surfaces(old, new)}


def test_removed_symbol_is_breaking():
    by = _changes()
    assert by["acme.core.deprecated_helper"].change_type == ChangeType.REMOVED
    assert by["acme.core.deprecated_helper"].severity == Severity.BREAKING
    # also flagged via its re-export path
    assert "acme.deprecated_helper" in by


def test_removed_method_is_breaking():
    by = _changes()
    assert by["acme.core.Client.old_method"].change_type == ChangeType.REMOVED
    assert by["acme.core.Client.old_method"].severity == Severity.BREAKING


def test_removed_parameter_is_breaking():
    by = _changes()
    change = by["acme.core.Client.fetch"]
    assert change.change_type == ChangeType.SIGNATURE_CHANGED
    assert change.severity == Severity.BREAKING
    assert "verify" in change.removed_params


def test_added_required_parameter_flagged():
    by = _changes()
    change = by["acme.core.make_client"]
    assert change.change_type == ChangeType.SIGNATURE_CHANGED
    assert "proxy" in change.added_required_params


def test_added_symbol_is_info():
    by = _changes()
    assert by["acme.core.renamed_thing"].change_type == ChangeType.ADDED
    assert by["acme.core.renamed_thing"].severity == Severity.INFO


def test_unchanged_symbol_has_no_change():
    by = _changes()
    # Client.__init__ is identical across versions.
    assert "acme.core.Client.__init__" not in by


def test_identical_surface_has_no_changes():
    old = surface_for("pkgv1", "1.0")
    assert diff_surfaces(old, old) == []
