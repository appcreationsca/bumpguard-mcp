from conftest import surface_for

from bumpguard.core.models import Kind


def test_extracts_definitions_and_kinds():
    syms = surface_for("pkgv1", "1.0").symbols
    assert syms["acme.core.make_client"].kind == Kind.FUNCTION
    assert syms["acme.core.Client"].kind == Kind.CLASS
    assert syms["acme.core.Client.fetch"].kind == Kind.METHOD


def test_private_names_excluded():
    syms = surface_for("pkgv1", "1.0").symbols
    assert "acme.core.Client._private" not in syms


def test_reexports_are_resolved():
    syms = surface_for("pkgv1", "1.0").symbols
    # __init__ re-exports the class and functions at the top level.
    assert "acme.Client" in syms
    assert "acme.make_client" in syms
    assert "acme.deprecated_helper" in syms


def test_reexported_class_members_are_resolved():
    syms = surface_for("pkgv1", "1.0").symbols
    # Members of a re-exported class must be reachable via the re-export path,
    # because that's how callers reach them (acme.Client().fetch()).
    assert "acme.Client.fetch" in syms
    assert syms["acme.Client.fetch"].kind == Kind.METHOD


def test_signature_params_captured():
    syms = surface_for("pkgv1", "1.0").symbols
    fetch = syms["acme.core.Client.fetch"]
    assert "verify" in fetch.param_names
    assert "self" not in fetch.required_params

    make_client = syms["acme.core.make_client"]
    assert make_client.param_names == {"url", "timeout"}
    assert make_client.required_params == {"url"}  # timeout has a default


def test_all_restricts_public_surface():
    # __all__ lists Client/deprecated_helper/make_client only; nothing else
    # should leak in at the package top level beyond those + the module node.
    syms = surface_for("pkgv1", "1.0").symbols
    top_level = {k for k in syms if k.count(".") == 1 and k.startswith("acme.")}
    # core submodule plus the three re-exports
    assert "acme.Client" in top_level
    assert "acme.make_client" in top_level
    assert "acme.deprecated_helper" in top_level
