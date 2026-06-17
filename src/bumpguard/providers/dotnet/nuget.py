"""NuGet package location and fetching for the .NET provider.

- "installed" = present in the NuGet global packages cache (~/.nuget/packages).
  NOTE: this is a best-effort baseline; a real project pins an exact version in
  its .csproj, so callers are encouraged to pass an explicit from_version.
- Target versions are downloaded from the nuget.org flat container as .nupkg
  (a zip), unpacked, and never installed or executed.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import urllib.request
import zipfile

_FLAT = "https://api.nuget.org/v3-flatcontainer"
_HTTP_TIMEOUT = 60
_MAX_NUPKG = 200 * 1024 * 1024


# A NuGet id/version is interpolated into both nuget.org URLs and local
# ~/.nuget cache paths (``{root}/{id}/{version}``). Reject anything that could
# escape the intended segment — path separators, whitespace, control chars, or a
# ".." traversal — and reject blank names so a missing/empty argument can't make
# the cache root itself look like an installed package. "." is legal in both ids
# and versions, so ".." must be rejected explicitly rather than by the character
# class. Legitimate NuGet ids/versions only use alphanumerics plus ``. _ -``
# (e.g. "Newtonsoft.Json", "13.0.3", "2.0.0-beta1").
def _safe_segment(value: str) -> bool:
    return (
        bool(value)
        and ".." not in value
        and re.fullmatch(r"[A-Za-z0-9_.\-]+", value) is not None
    )

# Preference order when a package ships multiple target frameworks. The compile
# surface lives in ref/ when present, otherwise lib/.
_TFM_PREFERENCE = (
    "net8.0", "net7.0", "net6.0", "net9.0", "net10.0", "net5.0",
    "netstandard2.1", "netstandard2.0", "netcoreapp3.1",
)


def _packages_root() -> str:
    return os.path.join(os.path.expanduser("~"), ".nuget", "packages")


def _version_key(v: str):
    # Sort release versions above pre-release; compare numeric release parts.
    core, _, pre = v.partition("-")
    parts = []
    for p in core.split("."):
        parts.append(int(p) if p.isdigit() else 0)
    return (parts, pre == "", pre)


def installed_versions(package: str) -> list[str]:
    if not _safe_segment(package):
        return []
    pdir = os.path.join(_packages_root(), package.lower())
    if not os.path.isdir(pdir):
        return []
    return sorted(
        (d for d in os.listdir(pdir) if os.path.isdir(os.path.join(pdir, d))),
        key=_version_key,
    )


def latest_installed(package: str) -> str | None:
    versions = installed_versions(package)
    return versions[-1] if versions else None


def list_installed() -> list[str]:
    root = _packages_root()
    if not os.path.isdir(root):
        return []
    return sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))


def _pick_tfm_dir(base: str) -> str | None:
    """Given a package version dir, return the best ref/ or lib/ <tfm> dir."""
    for kind in ("ref", "lib"):
        kdir = os.path.join(base, kind)
        if not os.path.isdir(kdir):
            continue
        tfms = [d for d in os.listdir(kdir) if os.path.isdir(os.path.join(kdir, d))]
        if not tfms:
            continue
        for pref in _TFM_PREFERENCE:
            if pref in tfms:
                chosen = os.path.join(kdir, pref)
                if _has_dll(chosen):
                    return chosen
        # Fall back to any tfm that actually contains a managed DLL.
        for t in sorted(tfms):
            chosen = os.path.join(kdir, t)
            if _has_dll(chosen):
                return chosen
    return None


def _has_dll(d: str) -> bool:
    return any(f.lower().endswith(".dll") for f in os.listdir(d))


def installed_surface_dir(package: str, version: str | None = None) -> tuple[str, str] | None:
    """Return (tfm_dir, version) for an installed package, or None."""
    if not _safe_segment(package):
        return None
    version = version or latest_installed(package)
    if version is None or not _safe_segment(version):
        return None
    base = os.path.join(_packages_root(), package.lower(), version)
    if not os.path.isdir(base):
        return None
    tfm_dir = _pick_tfm_dir(base)
    return (tfm_dir, version) if tfm_dir else None


def list_versions_online(package: str) -> list[str]:
    if not _safe_segment(package):
        return []
    url = f"{_FLAT}/{package.lower()}/index.json"
    try:
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:
            import json

            data = json.loads(resp.read().decode("utf-8"))
        return list(data.get("versions", []))
    except Exception:
        return []


def fetch_version_dir(package: str, version: str) -> tuple[str, str] | None:
    """Download package==version as a .nupkg, unpack, and return (tfm_dir, tmp).

    The caller owns ``tmp`` and must remove it. Returns None on failure (and
    cleans up after itself in that case).
    """
    if not _safe_segment(package) or not _safe_segment(version):
        return None
    pid = package.lower()
    url = f"{_FLAT}/{pid}/{version}/{pid}.{version}.nupkg"
    tmp = tempfile.mkdtemp(prefix="bumpguard_nuget_")
    ok = False
    try:
        nupkg = os.path.join(tmp, "pkg.nupkg")
        try:
            with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:
                data = resp.read(_MAX_NUPKG + 1)
        except Exception:
            return None
        if len(data) > _MAX_NUPKG:
            return None
        with open(nupkg, "wb") as f:
            f.write(data)

        extract = os.path.join(tmp, "unpacked")
        if not _safe_unzip(nupkg, extract):
            return None
        tfm_dir = _pick_tfm_dir(extract)
        if tfm_dir is None:
            return None
        ok = True
        return tfm_dir, tmp
    finally:
        if not ok:
            shutil.rmtree(tmp, ignore_errors=True)


def _safe_unzip(path: str, dest: str) -> bool:
    os.makedirs(dest, exist_ok=True)
    dest_abs = os.path.abspath(dest)
    total = 0
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                target = os.path.abspath(os.path.join(dest, info.filename))
                if not (target == dest_abs or target.startswith(dest_abs + os.sep)):
                    return False
                total += info.file_size
                if total > _MAX_NUPKG * 4:
                    return False
            zf.extractall(dest)
        return True
    except (zipfile.BadZipFile, OSError):
        return False
