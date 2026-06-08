from bumpguard.providers.python.usage import scan_imports, scan_usage


def test_resolves_module_alias():
    usages = scan_usage("import acme\nacme.make_client('u')\n", "acme")
    paths = {u.dotted_path for u in usages}
    assert "acme.make_client" in paths


def test_resolves_from_import_alias():
    code = "from acme import make_client as mk\nmk('u', timeout=5)\n"
    usages = scan_usage(code, "acme")
    mk = next(u for u in usages if u.dotted_path == "acme.make_client")
    assert mk.is_call
    assert "timeout" in mk.call_kwargs


def test_tracks_instance_method_calls():
    code = (
        "import acme\n"
        "c = acme.Client('http://x')\n"
        "c.fetch('/p', verify=True)\n"
    )
    usages = scan_usage(code, "acme")
    fetch = next(u for u in usages if u.dotted_path == "acme.Client.fetch")
    assert fetch.is_call
    assert "verify" in fetch.call_kwargs


def test_resolves_bare_name_and_subclass_usage():
    # Using an imported class as a base class is a bare Name reference, not an
    # attribute/call — it must still be detected.
    code = "from acme import Client\nclass My(Client):\n    pass\n"
    usages = scan_usage(code, "acme")
    assert any(u.dotted_path == "acme.Client" for u in usages)


def test_package_filter_excludes_other_packages():
    code = "import os\nimport acme\nos.getcwd()\nacme.make_client('u')\n"
    usages = scan_usage(code, "acme")
    tops = {u.dotted_path.split('.')[0] for u in usages}
    assert tops == {"acme"}


def test_scan_imports_lists_third_party_imports():
    code = "import acme\nfrom acme import Client\nimport os\n"
    refs = scan_imports(code)
    tops = {r.top_package for r in refs}
    assert "acme" in tops
    assert "os" in tops


def test_syntax_error_returns_empty():
    assert scan_usage("def (:\n", "acme") == []
    assert scan_imports("def (:\n") == []
