"""Provider registry: maps a language id to its Provider implementation."""

from __future__ import annotations

from ..providers.base import Provider

_REGISTRY: dict[str, Provider] = {}


def register(provider: Provider) -> None:
    _REGISTRY[provider.language] = provider


def get_provider(language: str) -> Provider | None:
    return _REGISTRY.get(language)


def available_languages() -> list[str]:
    return sorted(_REGISTRY)


def load_default_providers() -> None:
    """Import and register the built-in providers. Import errors are tolerated
    so a missing optional toolchain (e.g. no .NET SDK) never breaks the rest."""
    try:
        from ..providers.python.provider import PythonProvider

        register(PythonProvider())
    except Exception:  # pragma: no cover - defensive
        pass
