"""Unit tests for the Java provider's pure logic (no JDK/Maven required).

Covers: bytecode surface extraction (against hand-assembled .class files written
here as an independent format-writer), descriptor parsing, usage resolution
(exact vs candidate), Maven ComparableVersion ordering, coordinate parsing, POM
inspection, and verify_snippet gating. A live Maven Central smoke test runs only
when BUMPGUARD_LIVE=1.
"""

from __future__ import annotations

import io
import os
import struct
import zipfile

import pytest

from bumpguard.core.models import Kind
from bumpguard.providers.java import classfile, maven
from bumpguard.providers.java.classfile import extract_surface, parse_descriptor_params
from bumpguard.providers.java.provider import (
    JavaProvider,
    _surface_from_extract,
    resolve_usages,
)

# Method/field access flags reused by the writer below.
PUBLIC = 0x0001
PRIVATE = 0x0002
STATIC = 0x0008
VARARGS = 0x0080


# --------------------------------------------------------------------------- #
# An independent minimal .class writer (proves the reader against bytes that   #
# were not produced by the reader itself). Only the member tables matter — no  #
# Code attributes are needed because nothing is ever executed.                 #
# --------------------------------------------------------------------------- #
class _ClassWriter:
    def __init__(self) -> None:
        self.pool: list[bytes] = []
        self.cache: dict[tuple, int] = {}

    def _add(self, key: tuple, entry: bytes) -> int:
        if key in self.cache:
            return self.cache[key]
        self.pool.append(entry)
        idx = len(self.pool)  # constant pool is 1-based
        self.cache[key] = idx
        return idx

    def utf8(self, s: str) -> int:
        b = s.encode("utf-8")
        return self._add(("u", s), bytes([1]) + struct.pack(">H", len(b)) + b)

    def class_ref(self, internal: str) -> int:
        ni = self.utf8(internal)
        return self._add(("c", internal), bytes([7]) + struct.pack(">H", ni))

    def build(
        self,
        this_internal: str,
        super_internal: str = "java/lang/Object",
        methods: list[tuple[int, str, str]] | None = None,
        fields: list[tuple[int, str, str]] | None = None,
        inner: list[tuple[str, str | None, str | None, int]] | None = None,
    ) -> bytes:
        methods = methods or []
        fields = fields or []
        this_idx = self.class_ref(this_internal)
        super_idx = self.class_ref(super_internal)

        field_blobs = [(acc, self.utf8(n), self.utf8(d)) for acc, n, d in fields]
        method_blobs = [(acc, self.utf8(n), self.utf8(d)) for acc, n, d in methods]

        inner_attr = None
        if inner:
            ic_name = self.utf8("InnerClasses")
            entries = []
            for inner_internal, outer_internal, simple, iacc in inner:
                ii = self.class_ref(inner_internal)
                oi = self.class_ref(outer_internal) if outer_internal else 0
                si = self.utf8(simple) if simple else 0
                entries.append((ii, oi, si, iacc))
            inner_attr = (ic_name, entries)

        out = b"\xca\xfe\xba\xbe" + struct.pack(">HH", 0, 52)  # Java 8
        out += struct.pack(">H", len(self.pool) + 1)
        for e in self.pool:
            out += e
        out += struct.pack(">H", 0x0021)  # PUBLIC | SUPER
        out += struct.pack(">H", this_idx)
        out += struct.pack(">H", super_idx)
        out += struct.pack(">H", 0)  # interfaces
        out += struct.pack(">H", len(field_blobs))
        for acc, ni, di in field_blobs:
            out += struct.pack(">HHHH", acc, ni, di, 0)
        out += struct.pack(">H", len(method_blobs))
        for acc, ni, di in method_blobs:
            out += struct.pack(">HHHH", acc, ni, di, 0)
        if inner_attr:
            ic_name, entries = inner_attr
            body = struct.pack(">H", len(entries))
            for ii, oi, si, iacc in entries:
                body += struct.pack(">HHHH", ii, oi, si, iacc)
            out += struct.pack(">H", 1)
            out += struct.pack(">H", ic_name) + struct.pack(">I", len(body)) + body
        else:
            out += struct.pack(">H", 0)
        return out


def _jar(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _calc_class() -> bytes:
    return _ClassWriter().build(
        "com/example/Calc",
        methods=[
            (PUBLIC, "<init>", "()V"),
            (PUBLIC, "add", "(II)I"),
            (PUBLIC, "add", "(I)I"),  # overload -> overloaded=True
            (PUBLIC, "scale", "(D)Lcom/example/Calc;"),
            (PUBLIC | VARARGS, "sum", "([I)I"),
            (PRIVATE, "secret", "()V"),  # excluded
        ],
        fields=[
            (PUBLIC | STATIC, "PI", "D"),
            (PRIVATE, "hidden", "I"),  # excluded
        ],
    )


# --------------------------------------------------------------------------- #
# Descriptor parsing                                                            #
# --------------------------------------------------------------------------- #
def test_descriptor_params():
    assert parse_descriptor_params("(II)I") == ["int", "int"]
    assert parse_descriptor_params("([I)I") == ["int[]"]
    assert parse_descriptor_params("(Lcom/example/Calc;)V") == ["com.example.Calc"]
    assert parse_descriptor_params("(D)Lcom/example/Calc;") == ["double"]
    assert parse_descriptor_params("()V") == []
    assert parse_descriptor_params("(Ljava/lang/String;[[I)V") == ["java.lang.String", "int[][]"]


# --------------------------------------------------------------------------- #
# Bytecode surface extraction                                                   #
# --------------------------------------------------------------------------- #
def test_extract_surface_members_and_visibility():
    data = extract_surface(_jar({"com/example/Calc.class": _calc_class()}))
    assert data is not None
    syms = {s["path"]: s for s in data["symbols"]}

    assert "com.example.Calc" in syms
    assert syms["com.example.Calc"]["kind"] == "class"
    # single public constructor with no args folds into the class symbol
    assert syms["com.example.Calc"].get("params") == []

    assert syms["com.example.Calc.add"].get("overloaded") is True
    assert syms["com.example.Calc.scale"]["params"] == [{"name": "0:double", "hasDefault": False}]
    assert syms["com.example.Calc.sum"]["acceptsVarargs"] is True
    assert syms["com.example.Calc.sum"]["params"] == [{"name": "0:int[]", "hasDefault": False}]
    assert "com.example.Calc.PI" in syms
    assert syms["com.example.Calc.PI"]["kind"] == "attribute"

    # non-public members are not part of the surface
    assert "com.example.Calc.secret" not in syms
    assert "com.example.Calc.hidden" not in syms

    assert data["partial"] is True
    assert any("no code executed" in n for n in data["notes"])


def test_surface_mapping_to_neutral_model():
    data = {
        "partial": True,
        "symbols": [
            {"path": "p.Foo", "kind": "class", "overloaded": True},
            {"path": "p.Foo.bar", "kind": "method", "params": [{"name": "0:int", "hasDefault": False}]},
            {"path": "p.Foo.BAZ", "kind": "attribute"},
        ],
        "notes": ["n"],
    }
    s = _surface_from_extract("g:a", "1.0", data)
    assert s.language == "java"
    assert s.partial is True
    assert s.symbols["p.Foo"].overloaded is True
    assert s.symbols["p.Foo.bar"].kind == Kind.METHOD
    assert s.symbols["p.Foo.bar"].required_params == {"0:int"}
    assert s.symbols["p.Foo.BAZ"].kind == Kind.ATTRIBUTE


def test_nested_class_naming_via_inner_classes():
    cw = _ClassWriter()
    inner = [("com/example/Outer$Inner", "com/example/Outer", "Inner", PUBLIC | STATIC)]
    blob = cw.build(
        "com/example/Outer$Inner",
        methods=[(PUBLIC, "<init>", "()V"), (PUBLIC, "ping", "()V")],
        inner=inner,
    )
    data = extract_surface(_jar({"com/example/Outer$Inner.class": blob}))
    paths = {s["path"] for s in data["symbols"]}
    assert "com.example.Outer.Inner" in paths
    assert "com.example.Outer.Inner.ping" in paths


def test_non_static_inner_ctor_drops_enclosing_instance():
    cw = _ClassWriter()
    inner = [("com/example/Outer$Member", "com/example/Outer", "Member", PUBLIC)]
    blob = cw.build(
        "com/example/Outer$Member",
        methods=[(PUBLIC, "<init>", "(Lcom/example/Outer;)V")],
        inner=inner,
    )
    data = extract_surface(_jar({"com/example/Outer$Member.class": blob}))
    syms = {s["path"]: s for s in data["symbols"]}
    # the synthetic leading Outer parameter is dropped
    assert syms["com.example.Outer.Member"].get("params") == []


def test_top_level_dollar_name_is_preserved():
    blob = _ClassWriter().build("com/example/Wei$rd", methods=[(PUBLIC, "go", "()V")])
    data = extract_surface(_jar({"com/example/Wei$rd.class": blob}))
    paths = {s["path"] for s in data["symbols"]}
    assert "com.example.Wei$rd" in paths
    assert "com.example.Wei$rd.go" in paths


def test_multi_release_overlay_is_selected():
    base = _ClassWriter().build("com/example/Calc", methods=[(PUBLIC, "add", "(II)I")])
    overlay = _ClassWriter().build(
        "com/example/Calc",
        methods=[(PUBLIC, "add", "(II)I"), (PUBLIC, "mul", "(II)I")],
    )
    jar = _jar(
        {
            "com/example/Calc.class": base,
            "META-INF/versions/11/com/example/Calc.class": overlay,
        }
    )
    data = extract_surface(jar)
    paths = {s["path"] for s in data["symbols"]}
    assert "com.example.Calc.mul" in paths  # newest overlay won
    assert any("ulti-release" in n for n in data["notes"])


def test_extract_surface_returns_none_for_non_jar():
    assert extract_surface(b"not a zip") is None


# --------------------------------------------------------------------------- #
# Usage resolution (exact vs candidate)                                         #
# --------------------------------------------------------------------------- #
def test_fully_qualified_usage_is_exact():
    data = {
        "imports": [],
        "locals": [],
        "refs": [{"name": "com.example.Calc.add", "isCall": True, "positionalCount": 2, "line": 1}],
    }
    by = {u.dotted_path: u for u in resolve_usages(data)}
    assert by["com.example.Calc.add"].confidence == "exact"


def test_short_name_resolves_via_import_as_candidate():
    data = {
        "imports": [{"name": "com.example.Calc", "simple": "Calc", "static": False, "wildcard": False}],
        "locals": [],
        "refs": [{"name": "Calc.add", "isCall": True, "positionalCount": 1, "line": 2}],
    }
    by = {u.dotted_path: u for u in resolve_usages(data)}
    assert "com.example.Calc.add" in by
    assert by["com.example.Calc.add"].confidence == "candidate"


def test_instance_method_resolves_through_local_type():
    data = {
        "imports": [{"name": "com.example.Calc", "simple": "Calc", "static": False, "wildcard": False}],
        "locals": [{"var": "c", "type": "Calc"}],
        "refs": [{"name": "c.add", "isCall": True, "positionalCount": 2, "line": 5}],
    }
    paths = {u.dotted_path for u in resolve_usages(data)}
    assert "com.example.Calc.add" in paths


def test_static_import_resolution():
    data = {
        "imports": [{"name": "com.example.Util.max", "simple": "max", "static": True, "wildcard": False}],
        "locals": [],
        "refs": [{"name": "max", "isCall": True, "positionalCount": 2, "line": 1}],
    }
    paths = {u.dotted_path for u in resolve_usages(data)}
    assert "com.example.Util.max" in paths


def test_wildcard_import_expands_to_candidate():
    data = {
        "imports": [{"name": "com.example", "simple": "example", "static": False, "wildcard": True}],
        "locals": [],
        "refs": [{"name": "Calc.add", "isCall": True, "positionalCount": 1, "line": 1}],
    }
    by = {u.dotted_path: u for u in resolve_usages(data)}
    assert by["com.example.Calc.add"].confidence == "candidate"


def test_scan_usage_end_to_end():
    code = """
import com.example.Calc;
import static com.example.Util.max;

class Demo {
    void run() {
        Calc c = new Calc();
        int r = c.add(1, 2);
        int m = max(3, 4);
        double p = com.example.Calc.PI;
    }
}
"""
    usages = {u.dotted_path: u for u in JavaProvider().scan_usage(code)}
    assert "com.example.Calc" in usages  # new Calc()
    assert usages["com.example.Calc.add"].positional_count == 2
    assert usages["com.example.Calc.add"].confidence == "candidate"
    assert "com.example.Util.max" in usages
    assert usages["com.example.Calc.PI"].confidence == "exact"


def test_scan_ignores_comments_and_strings():
    code = """
class Demo {
    void run() {
        // foo.bar(1, 2)
        String s = "baz.qux(9)";
        com.real.Thing.go();
    }
}
"""
    paths = {u.dotted_path for u in JavaProvider().scan_usage(code)}
    assert "com.real.Thing.go" in paths
    assert not any("foo.bar" in p or "baz.qux" in p for p in paths)


# --------------------------------------------------------------------------- #
# Maven version ordering / coordinates / POM                                    #
# --------------------------------------------------------------------------- #
def test_version_compare_semantics():
    vc = maven.version_compare
    assert vc("1.0", "1.0.0") == 0
    assert vc("2.0.0-beta", "2.0.0") < 0
    assert vc("1.10", "1.2") > 0
    assert vc("1.0-SNAPSHOT", "1.0") < 0
    assert vc("2.0.0.Final", "2.0.0") == 0
    assert vc("2.0.0-rc1", "2.0.0-rc2") < 0
    assert vc("1.0-alpha", "1.0-beta") < 0


def test_version_sorting_picks_release_as_latest():
    versions = ["1.0.0", "1.10.0", "1.2.0", "2.0.0-beta", "2.0.0", "1.0-SNAPSHOT"]
    s = sorted(versions, key=maven._version_key)
    assert s[-1] == "2.0.0"
    assert s.index("1.10.0") > s.index("1.2.0")
    assert s.index("2.0.0-beta") < s.index("2.0.0")


def test_parse_coordinate():
    assert maven.parse_coordinate("com.google.guava:guava") == ("com.google.guava", "guava")
    assert maven.parse_coordinate(" g : a ") == ("g", "a")
    assert maven.parse_coordinate("noColon") is None
    assert maven.parse_coordinate("a:b:c") is None
    assert maven.parse_coordinate(":guava") is None
    assert maven.parse_coordinate("group:") is None


def test_safe_version_rejects_path_tricks():
    # Legit Maven versions are accepted.
    for good in ("2.10.1", "1.0.0-RC1", "3.0-SNAPSHOT", "1.0.0.Final", "20030203.000550"):
        assert maven._safe_version(good) is True
    # Anything that could escape the {group}/{artifact}/{version}/ URL segment is rejected.
    for bad in ("", "../1.0", "1.0/../../etc", "a\\b", "1 0", "1\t0", "1.0\n", "a;b", "%2e%2e",
                "..", "a..b", "1..0", "...."):
        assert maven._safe_version(bad) is False


def test_fetch_rejects_unsafe_version_without_network(monkeypatch):
    # The version guard must short-circuit before any HTTP request is attempted.
    def _boom(*_a, **_k):
        raise AssertionError("network must not be touched for an unsafe version")

    monkeypatch.setattr(maven, "_get", _boom)
    assert maven.fetch_jar("com.example", "lib", "../evil") is None
    assert maven.fetch_pom("com.example", "lib", "1.0/../../x") is None


def test_pom_info_packaging_and_relocation():
    assert maven.pom_info(b"<project><packaging>jar</packaging></project>") == ("jar", None)
    assert maven.pom_info(b"<project><packaging>pom</packaging></project>") == ("pom", None)

    ns = (
        b'<project xmlns="http://maven.apache.org/POM/4.0.0">'
        b"<packaging>bundle</packaging></project>"
    )
    assert maven.pom_info(ns) == ("bundle", None)

    reloc = (
        b"<project><distributionManagement><relocation>"
        b"<groupId>new.group</groupId><artifactId>newart</artifactId>"
        b"</relocation></distributionManagement></project>"
    )
    packaging, relocation = maven.pom_info(reloc)
    assert relocation == ("new.group", "newart")


# --------------------------------------------------------------------------- #
# Provider wiring                                                               #
# --------------------------------------------------------------------------- #
def test_java_provider_registered():
    from bumpguard.core import registry

    registry.load_default_providers()
    assert "java" in registry.available_languages()


def test_verify_snippet_unsupported_for_java():
    from bumpguard.core import service

    res = service.verify_snippet("java", "import com.example.Calc;\n")
    assert res["verified"] is None
    assert "not supported" in res["note"]


def test_invalid_coordinate_surfaces_none():
    p = JavaProvider()
    assert p.get_installed("not-a-coordinate") is None
    assert p.get_version_surface("not-a-coordinate", "1.0") is None


# --------------------------------------------------------------------------- #
# Regression: the "No usages detected" note must NOT fire when a usage resolves #
# to a real symbol in the surface, even though the distribution coordinate      #
# (group:artifact) never prefix-matches the Java package namespace.             #
# --------------------------------------------------------------------------- #
def _surface(package: str, version: str, paths: list[str]):
    from bumpguard.core.models import Kind, Surface, Symbol

    return Surface(
        package=package,
        version=version,
        language="java",
        symbols={p: Symbol(dotted_path=p, kind=Kind.CLASS) for p in paths},
        extraction_method="bytecode",
    )


class _FakeJavaProvider:
    """Mimics the coordinate/namespace split: import_names returns the Maven
    coordinate (e.g. 'com.google.code.gson:gson') which never prefix-matches a
    real Java symbol path (e.g. 'com.google.gson.Gson')."""

    language = "java"
    supports_verify = False

    def __init__(self, paths):
        self._paths = paths

    def get_version_surface(self, package, version):
        return _surface(package, version, self._paths)

    def get_installed_surface(self, package):
        return _surface(package, None, self._paths)

    def scan_usage(self, code, package=None):
        from bumpguard.core.models import Usage

        # Resolves to a real symbol in the surface, but as a heuristically
        # resolved candidate (so it produces no breaking finding on its own).
        return [Usage(dotted_path="com.google.gson.Gson", line=2, confidence="candidate")]

    def import_names(self, package):
        return [package]  # the coordinate, e.g. "com.google.code.gson:gson"


def test_resolved_usage_suppresses_no_usage_note(monkeypatch):
    from bumpguard.core import service

    provider = _FakeJavaProvider(["com.google.gson.Gson", "com.google.gson.GsonBuilder"])
    monkeypatch.setattr(service, "get_provider", lambda lang: provider)

    res = service.check_upgrade(
        "java", "com.google.code.gson:gson", "2.10.1",
        "import com.google.gson.Gson;\nGson g = new Gson();\n",
        from_version="2.8.9",
    )
    assert not any("No usages" in n for n in res.get("notes", []))


def test_unresolved_usage_keeps_no_usage_note(monkeypatch):
    from bumpguard.core import service

    # Surface lacks the used symbol -> usage truly doesn't touch this package.
    provider = _FakeJavaProvider(["com.google.gson.stream.JsonReader"])
    monkeypatch.setattr(service, "get_provider", lambda lang: provider)

    res = service.check_upgrade(
        "java", "com.google.code.gson:gson", "2.10.1",
        "import com.google.gson.Gson;\nGson g = new Gson();\n",
        from_version="2.8.9",
    )
    assert any("No usages" in n for n in res.get("notes", []))


# --------------------------------------------------------------------------- #
# Live smoke (network) — opt in with BUMPGUARD_LIVE=1                            #
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(os.environ.get("BUMPGUARD_LIVE") != "1", reason="live network test")
def test_live_maven_fetch_and_extract():
    p = JavaProvider()
    surface = p.get_version_surface("com.google.code.gson:gson", "2.10.1")
    assert surface is not None
    assert any(path.startswith("com.google.gson.") for path in surface.symbols)
    assert "com.google.gson.Gson" in surface.symbols
