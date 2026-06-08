"""Python provider — locate installed package source and fetch other versions.

Fetching a target version uses ``pip download`` to grab the wheel only (never
installing it into the live environment) and unpacks it so the surface
extractor can read its ``.py`` files. All subprocess calls are time-bounded so
the MCP server can never hang.
"""

from __future__ import annotations

import importlib.metadata as ilm
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

_DOWNLOAD_TIMEOUT = 120
_MAX_UNCOMPRESSED = 600 * 1024 * 1024  # 600 MB safety cap for a single wheel
_MAX_FILES = 20000


def installed_version(name: str) -> str | None:
    for candidate in (name, name.replace("_", "-"), name.replace("-", "_")):
        try:
            return ilm.version(candidate)
        except ilm.PackageNotFoundError:
            continue
    return None


def import_name_for(dist_name: str) -> str:
    """Best-effort map a distribution name to its importable top-level name."""
    for candidate in (dist_name, dist_name.replace("_", "-"), dist_name.replace("-", "_")):
        try:
            text = ilm.distribution(candidate).read_text("top_level.txt")
        except ilm.PackageNotFoundError:
            continue
        if text:
            for line in text.splitlines():
                line = line.strip()
                if line:
                    return line
    return dist_name.replace("-", "_")


def locate_installed_source(import_name: str) -> tuple[str, str] | None:
    """Return (package_root, import_name) where package_root contains the
    package dir or the single-file module. None if not importable on disk."""
    try:
        spec = importlib.util.find_spec(import_name)
    except (ModuleNotFoundError, ValueError, ImportError):
        return None
    if spec is None:
        return None
    if spec.submodule_search_locations:
        pkg_dir = list(spec.submodule_search_locations)[0]
        return os.path.dirname(pkg_dir), import_name
    if spec.origin and spec.origin.endswith(".py"):
        return os.path.dirname(spec.origin), import_name
    return None


def list_installed() -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for dist in ilm.distributions():
        try:
            name = dist.metadata["Name"]
        except Exception:
            continue
        if not name or name in seen:
            continue
        seen.add(name)
        out.append((name, dist.version))
    return sorted(out, key=lambda x: x[0].lower())


def fetch_version_source(package: str, version: str) -> tuple[str, str] | None:
    """Download ``package==version`` as a wheel and unpack it.

    Returns (extracted_root, import_name) — extracted_root being the directory
    that contains the package dir — or None if the download/extract fails. On
    success the caller owns the temp tree (``_tempdir_of(extracted_root)``); on
    any failure this function cleans up after itself.
    """
    tmp = tempfile.mkdtemp(prefix="bumpguard_")
    ok = False
    try:
        proc = subprocess.run(
            [
                sys.executable, "-m", "pip", "download",
                f"{package}=={version}",
                "--no-deps", "--only-binary", ":all:",
                "--dest", tmp,
            ],
            capture_output=True, text=True, timeout=_DOWNLOAD_TIMEOUT,
        )
        if proc.returncode != 0:
            return None
        wheel = next((f for f in os.listdir(tmp) if f.endswith(".whl")), None)
        if wheel is None:
            return None
        extract_dir = os.path.join(tmp, "unpacked")
        if not _safe_extract(os.path.join(tmp, wheel), extract_dir):
            return None
        import_name = _import_name_from_wheel(extract_dir, package)
        if import_name is None:
            return None
        ok = True
        return extract_dir, import_name
    except (subprocess.TimeoutExpired, zipfile.BadZipFile, OSError):
        return None
    finally:
        if not ok:
            shutil.rmtree(tmp, ignore_errors=True)


def _safe_extract(wheel_path: str, dest: str) -> bool:
    """Extract a wheel, guarding against path traversal and zip bombs."""
    os.makedirs(dest, exist_ok=True)
    dest_abs = os.path.abspath(dest)
    total = 0
    with zipfile.ZipFile(wheel_path) as zf:
        infos = zf.infolist()
        if len(infos) > _MAX_FILES:
            return False
        for info in infos:
            target = os.path.abspath(os.path.join(dest, info.filename))
            if not (target == dest_abs or target.startswith(dest_abs + os.sep)):
                return False  # path traversal attempt
            total += info.file_size
            if total > _MAX_UNCOMPRESSED:
                return False
        zf.extractall(dest)
    return True


def _import_name_from_wheel(extract_dir: str, package: str) -> str | None:
    # Prefer the wheel's declared top-level name.
    for entry in os.listdir(extract_dir):
        if entry.endswith(".dist-info"):
            top = os.path.join(extract_dir, entry, "top_level.txt")
            if os.path.isfile(top):
                with open(top, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            return line
    # Fall back to the sole importable top-level entry.
    candidates = [
        e
        for e in os.listdir(extract_dir)
        if not e.endswith((".dist-info", ".data"))
        and (os.path.isdir(os.path.join(extract_dir, e)) or e.endswith(".py"))
    ]
    if len(candidates) == 1:
        name = candidates[0]
        return name[:-3] if name.endswith(".py") else name
    guess = package.replace("-", "_")
    if guess in candidates or f"{guess}.py" in candidates:
        return guess
    return None
