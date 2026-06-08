"""Richer surface extraction: properties, instance attrs, dynamic modules."""

import textwrap

from bumpguard.core.models import Kind
from bumpguard.core.service import _under_dynamic
from bumpguard.providers.python.surface import extract_symbols


def _pkg(tmp_path, init_body):
    root = tmp_path / "proj"
    pkg = root / "widget"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(textwrap.dedent(init_body))
    return str(root)


BODY = """
def __getattr__(name):
    raise AttributeError(name)


class Widget:
    def __init__(self, color):
        self.color = color

    @property
    def label(self):
        return self.color

    def render(self):
        return self.color
"""


def test_property_is_an_attribute(tmp_path):
    symbols, _ = extract_symbols(_pkg(tmp_path, BODY), "widget")
    assert symbols["widget.Widget.label"].kind == Kind.ATTRIBUTE


def test_instance_attribute_extracted(tmp_path):
    symbols, _ = extract_symbols(_pkg(tmp_path, BODY), "widget")
    assert "widget.Widget.color" in symbols
    assert symbols["widget.Widget.color"].kind == Kind.ATTRIBUTE


def test_method_still_a_method(tmp_path):
    symbols, _ = extract_symbols(_pkg(tmp_path, BODY), "widget")
    assert symbols["widget.Widget.render"].kind == Kind.METHOD


def test_class_is_callable_with_init_params(tmp_path):
    symbols, _ = extract_symbols(_pkg(tmp_path, BODY), "widget")
    assert symbols["widget.Widget"].param_names == {"color"}


def test_module_getattr_marks_dynamic(tmp_path):
    _, dynamic = extract_symbols(_pkg(tmp_path, BODY), "widget")
    assert "widget" in dynamic


def test_under_dynamic_helper():
    class FakeSurface:
        dynamic_modules = {"widget"}

    assert _under_dynamic("widget.anything.deep", FakeSurface()) is True
    assert _under_dynamic("other.thing", FakeSurface()) is False
