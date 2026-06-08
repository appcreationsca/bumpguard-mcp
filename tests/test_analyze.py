from conftest import surface_for

from bumpguard.core.analyze import build_upgrade_report
from bumpguard.core.diff import diff_surfaces
from bumpguard.providers.python.usage import scan_usage

USER_CODE = (
    "import acme\n"
    "c = acme.Client('http://x')\n"
    "c.fetch('/p', verify=True)\n"
    "acme.make_client('http://x')\n"
    "acme.deprecated_helper(1)\n"
)


def _report():
    old = surface_for("pkgv1", "1.0")
    new = surface_for("pkgv2", "2.0")
    changes = diff_surfaces(old, new)
    usages = scan_usage(USER_CODE, None)
    return build_upgrade_report("acme", "python", old, new, changes, usages).to_dict()


def test_report_is_not_safe():
    assert _report()["safe_to_upgrade"] is False


def test_findings_are_scoped_to_used_symbols():
    report = _report()
    by_symbol = {f["symbol"]: f for f in report["findings"]}

    # Removed keyword the call passes -> breaking.
    assert by_symbol["acme.Client.fetch"]["severity"] == "breaking"
    # Removed function the code calls -> breaking.
    assert by_symbol["acme.deprecated_helper"]["severity"] == "breaking"
    # New required parameter -> potentially breaking.
    assert by_symbol["acme.make_client"]["severity"] == "potentially_breaking"


def test_unused_breaking_change_is_not_reported():
    # old_method is removed (a breaking API change) but the user code never
    # calls it, so it must NOT appear as a finding — that's the whole point.
    report = _report()
    assert "acme.Client.old_method" not in {f["symbol"] for f in report["findings"]}


def test_summary_counts_present():
    report = _report()
    summary = report["summary"]
    assert summary["breaking"] >= 2
    assert summary["total_api_changes"] >= 4


def test_clean_code_is_safe():
    old = surface_for("pkgv1", "1.0")
    new = surface_for("pkgv2", "2.0")
    changes = diff_surfaces(old, new)
    safe_code = "import acme\nacme.Client('http://x')\n"  # only touches stable API
    usages = scan_usage(safe_code, None)
    report = build_upgrade_report("acme", "python", old, new, changes, usages).to_dict()
    assert report["safe_to_upgrade"] is True
