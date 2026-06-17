"""Unit tests for the .NET provider's pure logic (no dotnet invocation)."""

import os

import pytest

from bumpguard.core.models import Kind
from bumpguard.providers.dotnet import helper, nuget
from bumpguard.providers.dotnet.provider import (
    DotNetProvider,
    _surface_from_json,
    resolve_usages,
)


def test_surface_mapping():
    data = {
        "partial": True,
        "symbols": [
            {"path": "N.Foo", "kind": "class", "overloaded": True},
            {"path": "N.Foo.Bar", "kind": "method", "params": [{"name": "x", "hasDefault": False}]},
            {"path": "N.Foo.Prop", "kind": "attribute"},
        ],
    }
    s = _surface_from_json("Pkg", "1.0", data)
    assert s.partial is True
    assert s.symbols["N.Foo"].overloaded is True
    assert s.symbols["N.Foo.Bar"].kind == Kind.METHOD
    assert s.symbols["N.Foo.Bar"].required_params == {"x"}
    assert s.symbols["N.Foo.Prop"].kind == Kind.ATTRIBUTE


def test_fully_qualified_usage_is_exact():
    data = {
        "usings": [{"name": "Azure.AI.OpenAI"}],
        "locals": [],
        "refs": [{"name": "Azure.AI.OpenAI.AzureOpenAIClient", "isCall": True, "positionalCount": 2, "line": 3}],
    }
    by = {u.dotted_path: u for u in resolve_usages(data)}
    assert by["Azure.AI.OpenAI.AzureOpenAIClient"].confidence == "exact"


def test_short_name_resolves_via_using_as_candidate():
    data = {
        "usings": [{"name": "Azure.AI.OpenAI"}],
        "locals": [],
        "refs": [{"name": "AzureOpenAIClient", "isCall": True, "positionalCount": 1, "line": 2}],
    }
    by = {u.dotted_path: u for u in resolve_usages(data)}
    # expanded candidate via the using namespace
    assert "Azure.AI.OpenAI.AzureOpenAIClient" in by
    assert by["Azure.AI.OpenAI.AzureOpenAIClient"].confidence == "candidate"


def test_instance_method_resolves_through_local_type():
    data = {
        "usings": [{"name": "Azure.AI.OpenAI"}],
        "locals": [{"var": "client", "type": "AzureOpenAIClient"}],
        "refs": [{"name": "client.GetChatClient", "isCall": True, "positionalCount": 1, "line": 5}],
    }
    paths = {u.dotted_path for u in resolve_usages(data)}
    assert "AzureOpenAIClient.GetChatClient" in paths
    assert "Azure.AI.OpenAI.AzureOpenAIClient.GetChatClient" in paths


def test_alias_resolution():
    data = {
        "usings": [{"name": "Old.Namespace.Thing", "alias": "T"}],
        "locals": [],
        "refs": [{"name": "T.DoWork", "isCall": True, "positionalCount": 0, "line": 1}],
    }
    paths = {u.dotted_path for u in resolve_usages(data)}
    assert "Old.Namespace.Thing.DoWork" in paths


def test_version_ordering():
    versions = ["1.0.0", "1.10.0", "1.2.0", "2.0.0-beta", "2.0.0"]
    s = sorted(versions, key=nuget._version_key)
    assert s[-1] == "2.0.0"
    assert s.index("1.10.0") > s.index("1.2.0")  # 1.10 newer than 1.2
    assert s.index("2.0.0-beta") < s.index("2.0.0")  # prerelease below release


def test_pick_tfm_prefers_ref_then_best_framework(tmp_path):
    base = tmp_path / "pkg" / "1.0"
    for sub in ("lib/netstandard2.0", "lib/net8.0", "ref/net8.0"):
        d = base / sub
        d.mkdir(parents=True)
        (d / "X.dll").write_text("")
    chosen = nuget._pick_tfm_dir(str(base))
    assert chosen.endswith(os.path.join("ref", "net8.0"))  # ref preferred over lib


def test_pick_tfm_skips_empty_framework_dirs(tmp_path):
    base = tmp_path / "pkg" / "1.0"
    (base / "lib" / "net8.0").mkdir(parents=True)  # empty, no dll
    good = base / "lib" / "netstandard2.0"
    good.mkdir(parents=True)
    (good / "X.dll").write_text("")
    chosen = nuget._pick_tfm_dir(str(base))
    assert chosen.endswith(os.path.join("lib", "netstandard2.0"))


def test_safe_segment_rejects_blank_and_traversal():
    # Legitimate ids/versions pass.
    for ok in ("Newtonsoft.Json", "newtonsoft.json", "13.0.3", "2.0.0-beta1", "a_b.c-d"):
        assert nuget._safe_segment(ok) is True
    # Blank / path-escaping / traversal inputs are rejected.
    for bad in ("", "..", "a..b", "../secret", "..\\secret", "a/b", "a\\b", "a b", "a\tb", "a;b"):
        assert nuget._safe_segment(bad) is False


def test_blank_package_is_not_reported_installed(tmp_path, monkeypatch):
    """A blank id must not make the cache root itself look like an installed
    package (``os.path.join(root, "")`` == root, whose children would otherwise
    be mistaken for versions)."""
    root = tmp_path / "packages"
    (root / "newtonsoft.json" / "13.0.3").mkdir(parents=True)
    monkeypatch.setattr(nuget, "_packages_root", lambda: str(root))

    assert nuget.installed_versions("") == []
    assert nuget.latest_installed("") is None
    assert DotNetProvider().get_installed("") is None
    # A real package in the same cache still resolves.
    assert nuget.latest_installed("Newtonsoft.Json") == "13.0.3"


def test_unsafe_package_or_version_cannot_escape_cache(tmp_path, monkeypatch):
    """Caller-supplied ids/versions are interpolated into cache paths, so a
    ``..`` traversal must be refused before any filesystem lookup escapes root."""
    cache = tmp_path / "cache"
    cache.mkdir()
    # A sibling dir that a traversal would reach if the guard were missing.
    (tmp_path / "secret-pkg" / "9.9.9").mkdir(parents=True)
    monkeypatch.setattr(nuget, "_packages_root", lambda: str(cache))

    assert nuget.installed_versions(os.path.join("..", "secret-pkg")) == []
    assert nuget.installed_surface_dir("pkg", os.path.join("..", "..", "x")) is None
    assert nuget.fetch_version_dir("pkg", "..") is None
    assert nuget.fetch_version_dir(os.path.join("..", "evil"), "1.0.0") is None


@pytest.mark.skipif(not helper.dotnet_available(), reason="dotnet not installed")
def test_verify_snippet_unsupported_for_dotnet():
    from bumpguard.core import service

    res = service.verify_snippet("dotnet", "using System;\n")
    assert res["verified"] is None
    assert "not supported" in res["note"]
