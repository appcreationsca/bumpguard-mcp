import os

from bumpguard.core.models import Surface
from bumpguard.providers.python.surface import extract_symbols

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def surface_for(version_dir: str, version: str) -> Surface:
    root = os.path.join(FIXTURES, version_dir)
    symbols, dynamic = extract_symbols(root, "acme")
    return Surface(
        package="acme",
        version=version,
        language="python",
        symbols=symbols,
        dynamic_modules=dynamic,
    )
