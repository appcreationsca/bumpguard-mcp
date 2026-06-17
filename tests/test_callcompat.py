"""Call-compatibility analysis: positional/keyword/constructor breakages."""

import textwrap

from bumpguard.core.analyze import build_upgrade_report
from bumpguard.core.diff import diff_surfaces
from bumpguard.core.models import Surface
from bumpguard.providers.python.surface import extract_symbols
from bumpguard.providers.python.usage import scan_usage


def _module(tmp_path, name, body):
    root = tmp_path / name
    root.mkdir(parents=True)
    (root / f"{name}.py").write_text(textwrap.dedent(body))
    return str(root)


def _report(tmp_path, v1_body, v2_body, code, pkg="m"):
    old_root = _module(tmp_path / "v1", pkg, v1_body)
    new_root = _module(tmp_path / "v2", pkg, v2_body)
    old = Surface(pkg, "1.0", "python", *_split(extract_symbols(old_root, pkg)))
    new = Surface(pkg, "2.0", "python", *_split(extract_symbols(new_root, pkg)))
    changes = diff_surfaces(old, new)
    usages = scan_usage(code, None)
    return build_upgrade_report(pkg, "python", old, new, changes, usages).to_dict()


def _split(result):
    symbols, dynamic = result
    return symbols, "ast", False, dynamic


def _sev(report, symbol):
    for f in report["findings"]:
        if f["symbol"] == symbol:
            return f["severity"]
    return None


def test_removed_positional_argument_is_breaking(tmp_path):
    report = _report(
        tmp_path,
        "def f(a, b):\n    return a + b\n",
        "def f(a):\n    return a\n",
        "import m\nm.f(1, 2)\n",
    )
    assert _sev(report, "m.f") == "breaking"


def test_removed_keyword_argument_is_breaking(tmp_path):
    report = _report(
        tmp_path,
        "def g(a, debug=False):\n    return a\n",
        "def g(a):\n    return a\n",
        "import m\nm.g(1, debug=True)\n",
    )
    assert _sev(report, "m.g") == "breaking"


def test_kwargs_does_not_absorb_extra_positional(tmp_path):
    # New signature accepts **kwargs but only one positional; passing two
    # positionals still breaks.
    report = _report(
        tmp_path,
        "def f(a, b):\n    return a\n",
        "def f(a, **kwargs):\n    return a\n",
        "import m\nm.f(1, 2)\n",
    )
    assert _sev(report, "m.f") == "breaking"


def test_added_required_constructor_param_is_flagged(tmp_path):
    report = _report(
        tmp_path,
        "class C:\n    def __init__(self, x):\n        self.x = x\n",
        "class C:\n    def __init__(self, x, token):\n        self.x = x\n",
        "import m\nc = m.C(1)\n",
    )
    # C now requires `token`; constructing with one arg is potentially breaking.
    assert _sev(report, "m.C") == "potentially_breaking"


def test_compatible_call_produces_no_finding(tmp_path):
    # Adding an optional parameter is backwards compatible.
    report = _report(
        tmp_path,
        "def f(a):\n    return a\n",
        "def f(a, verbose=False):\n    return a\n",
        "import m\nm.f(1)\n",
    )
    assert report["safe_to_upgrade"] is True


def test_optional_param_becoming_required_is_flagged(tmp_path):
    # A parameter that loses its default (optional -> required) breaks callers
    # that relied on the default. Previously this slipped through as "safe".
    report = _report(
        tmp_path,
        "def f(a, b=1):\n    return a\n",
        "def f(a, b):\n    return a\n",
        "import m\nm.f(1)\n",
    )
    assert _sev(report, "m.f") == "potentially_breaking"


def test_star_args_spread_is_not_a_false_hard_break(tmp_path):
    # `m.f(1, *rest)` against a narrowed signature must NOT be asserted as a hard
    # break: `rest` may be empty, so the call can be perfectly valid. We can't be
    # certain, so it must not claim a definite breakage.
    report = _report(
        tmp_path,
        "def f(a, b):\n    return a\n",
        "def f(a):\n    return a\n",
        "import m\nm.f(1, *rest)\n",
    )
    assert _sev(report, "m.f") != "breaking"


def test_star_args_with_enough_concrete_positionals_still_breaks(tmp_path):
    # Even with a spread, two *concrete* positionals already exceed a 1-arg
    # signature, so this is a certain over-arity break regardless of the spread.
    report = _report(
        tmp_path,
        "def f(a, b, c):\n    return a\n",
        "def f(a):\n    return a\n",
        "import m\nm.f(1, 2, *rest)\n",
    )
    assert _sev(report, "m.f") == "breaking"
