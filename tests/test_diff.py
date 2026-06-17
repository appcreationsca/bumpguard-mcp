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


def test_optional_param_becoming_required_is_breaking():
    # A param that had a default and loses it (optional -> required) breaks any
    # caller that relied on the default. This must be flagged, not silently
    # treated as a compatible signature.
    from bumpguard.core.models import Kind, Param, Surface, Symbol

    def fn(params):
        return Symbol(dotted_path="m.f", kind=Kind.FUNCTION, params=params)

    old = Surface("p", "1.0", "python", {"m.f": fn([Param("a"), Param("b", has_default=True)])})
    new = Surface("p", "2.0", "python", {"m.f": fn([Param("a"), Param("b", has_default=False)])})
    by = {c.dotted_path: c for c in diff_surfaces(old, new)}
    assert "m.f" in by, "optional->required parameter change was not detected"
    assert by["m.f"].change_type == ChangeType.SIGNATURE_CHANGED
    assert by["m.f"].severity == Severity.BREAKING
    assert "b" in by["m.f"].added_required_params


def test_required_param_staying_required_is_not_a_change():
    # Guards against a false positive from the optional->required fix: a param
    # required in both versions must not be reported as newly-required.
    from bumpguard.core.models import Kind, Param, Surface, Symbol

    def fn(params):
        return Symbol(dotted_path="m.g", kind=Kind.FUNCTION, params=params)

    old = Surface("p", "1.0", "python", {"m.g": fn([Param("a"), Param("b")])})
    new = Surface("p", "2.0", "python", {"m.g": fn([Param("a"), Param("b")])})
    assert "m.g" not in {c.dotted_path: c for c in diff_surfaces(old, new)}


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
