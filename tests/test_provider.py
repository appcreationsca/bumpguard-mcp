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


def test_unknown_language_returns_error():
    result = service.check_import("cobol", "anything")
    assert "error" in result
    assert "python" in result["available_languages"]


def test_list_languages_includes_python():
    langs = {item["language"] for item in service.list_languages()["languages"]}
    assert "python" in langs
