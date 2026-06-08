"""Bridge to the C# reflection/Roslyn extractor.

The extractor is a small .NET project shipped with BumpGuard. It is built once
into a per-user cache and then invoked as a compiled assembly (not rebuilt per
call). All invocations are time-bounded so the MCP server can't hang.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading

_HERE = os.path.dirname(__file__)
EXTRACTOR_DIR = os.path.join(_HERE, "extractor")
_BUILD_TIMEOUT = 300
_RUN_TIMEOUT = 150

_lock = threading.Lock()
_resolved: dict[str, str | None] = {}


def dotnet_available() -> bool:
    return shutil.which("dotnet") is not None


def _cache_build_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "bumpguard", "extractor-build")


def _find_built_dll() -> str | None:
    """Return a path to bumpguard-extractor.dll, building it once if needed."""
    env = os.environ.get("BUMPGUARD_EXTRACTOR_DLL")
    if env and os.path.isfile(env):
        return env

    name = "bumpguard-extractor.dll"
    cache_dll = os.path.join(_cache_build_dir(), name)
    if os.path.isfile(cache_dll):
        return cache_dll

    # A dev build alongside the source is also acceptable.
    intree = os.path.join(EXTRACTOR_DIR, "_build", name)
    if os.path.isfile(intree):
        return intree

    if not dotnet_available():
        return None
    csproj = os.path.join(EXTRACTOR_DIR, "Extractor.csproj")
    if not os.path.isfile(csproj):
        return None

    out = _cache_build_dir()
    os.makedirs(out, exist_ok=True)
    try:
        proc = subprocess.run(
            ["dotnet", "build", csproj, "-c", "Release", "-o", out, "--nologo"],
            capture_output=True, text=True, timeout=_BUILD_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0 or not os.path.isfile(cache_dll):
        return None
    return cache_dll


def get_extractor() -> str | None:
    with _lock:
        if "dll" not in _resolved:
            _resolved["dll"] = _find_built_dll()
        return _resolved["dll"]


def _run(args: list[str]) -> dict | None:
    dll = get_extractor()
    if dll is None:
        return None
    try:
        proc = subprocess.run(
            ["dotnet", dll, *args], capture_output=True, text=True, timeout=_RUN_TIMEOUT
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    # The extractor prints a single JSON object on stdout; be tolerant of any
    # stray lines by taking the last line that parses as JSON.
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def surface(target_dir: str, resolver_dirs: list[str] | None = None) -> dict | None:
    return _run(["surface", target_dir, *(resolver_dirs or [])])


def usage(code: str) -> dict | None:
    fd, path = tempfile.mkstemp(suffix=".cs")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        return _run(["usage", path])
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
