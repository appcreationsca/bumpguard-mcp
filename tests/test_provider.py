"""Tests that touch the live environment via the Python provider/service."""

from bumpguard.core import service


def test_check_import_finds_installed_package():
    # pytest is guaranteed installed in the test environment.
    result = service.check_import("python", "pytest")
    assert result["installed"] is True


def test_check_import_missing_package_suggests():
    result = service.check_import("python", "pytessst")
    assert result["installed"] is False
    # close to the real "pytest" -> should be suggested
    assert "pytest" in result.get("suggestions", [])


def test_check_import_blank_name_is_graceful():
    # An empty/whitespace distribution name must not raise out of a tool — it
    # should degrade to a clean "not installed" result.
    for blank in ("", "   "):
        result = service.check_import("python", blank)
        assert result["installed"] is False


def test_verify_snippet_flags_unknown_import():
    code = "import totally_not_a_real_pkg_xyz\n"
    result = service.verify_snippet("python", code)
    assert result["verified"] is False
    symbols = {f["symbol"] for f in result["findings"]}
    assert "totally_not_a_real_pkg_xyz" in symbols


def test_verify_snippet_passes_real_stdlib_like_usage():
    # `json` is always importable; valid usage should not be flagged.
    code = "import json\njson.dumps({'a': 1})\n"
    result = service.verify_snippet("python", code)
    flagged = {f["symbol"] for f in result["findings"]}
    assert "json" not in flagged


def test_verify_snippet_unparseable_code_is_not_verified():
    # Broken syntax: the scanners return nothing, which must NOT be reported as a
    # clean "verified: true". It should surface a high-severity parse finding.
    code = "import json\ndef (:::\n"
    result = service.verify_snippet("python", code)
    assert result["verified"] is False
    assert any(f["severity"] == "high" for f in result["findings"])
    assert any("does not parse" in f["message"] for f in result["findings"])


def test_verify_snippet_empty_code_is_verified():
    # An empty (or whitespace-only) snippet parses fine and has nothing to flag.
    result = service.verify_snippet("python", "   \n")
    assert result["verified"] is True
    assert result["findings"] == []


def test_python_provider_parse_error_hook():
    from bumpguard.providers.python.provider import PythonProvider

    provider = PythonProvider()
    assert provider.parse_error("x = 1\n") is None
    assert provider.parse_error("def (:::") is not None


def test_unknown_language_returns_error():
    result = service.check_import("cobol", "anything")
    assert "error" in result
    assert "python" in result["available_languages"]


def test_list_languages_includes_python():
    langs = {item["language"] for item in service.list_languages()["languages"]}
    assert "python" in langs
