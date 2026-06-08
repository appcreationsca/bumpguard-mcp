"""Unit tests for the .NET provider's pure logic (no dotnet invocation)."""

import os

import pytest

from bumpguard.core.models import Kind
from bumpguard.providers.dotnet import helper, nuget
from bumpguard.providers.dotnet.provider import _surface_from_json, resolve_usages


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


@pytest.mark.skipif(not helper.dotnet_available(), reason="dotnet not installed")
def test_verify_snippet_unsupported_for_dotnet():
    from bumpguard.core import service

    res = service.verify_snippet("dotnet", "using System;\n")
    assert res["verified"] is None
    assert "not supported" in res["note"]
