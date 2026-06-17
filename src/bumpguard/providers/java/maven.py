"""Maven Central location and fetching for the Java provider.

- A Java package is identified by a ``group:artifact`` coordinate, e.g.
  ``com.google.guava:guava``.
- "installed" = present in the local Maven repository (``~/.m2/repository``).
  Like the .NET cache this is a best-effort baseline: a real build pins exact
  versions, so callers are encouraged to pass an explicit ``from_version``.
- Target versions are downloaded from Maven Central (``repo1.maven.org``) as the
  main ``.jar`` (a zip). The bytecode metadata is read structurally and never
  installed or executed. Downloads are size-capped and time-bounded.

Version ordering uses a faithful port of Maven's ``ComparableVersion`` so that
``1.0`` == ``1.0.0``, pre-releases (``-alpha``/``-rc1``/``-SNAPSHOT``) sort below
the matching release, and ``.Final``/``.RELEASE`` qualifiers are handled.
"""

from __future__ import annotations

import functools
import os
import re
import urllib.request
import xml.etree.ElementTree as ET

_CENTRAL = "https://repo1.maven.org/maven2"
_HTTP_TIMEOUT = 60
_MAX_JAR = 200 * 1024 * 1024
_MAX_POM = 8 * 1024 * 1024
_MAX_METADATA = 16 * 1024 * 1024

# Packaging values that genuinely ship a main jar of compiled classes. Anything
# else (pom aggregators, android aar, etc.) has no readable class surface.
_JAR_PACKAGINGS = {"jar", "bundle", "maven-plugin", "ejb", "", None}


# ---- coordinates -------------------------------------------------------------


def parse_coordinate(package: str) -> tuple[str, str] | None:
    """Split ``group:artifact`` into ``(group, artifact)``. Returns None if the
    coordinate isn't a well-formed ``group:artifact`` pair."""
    if not package or package.count(":") != 1:
        return None
    group, artifact = package.split(":", 1)
    group = group.strip()
    artifact = artifact.strip()
    if not group or not artifact:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", group):
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", artifact):
        return None
    return group, artifact


def _group_path(group: str) -> str:
    return group.replace(".", "/")


# Maven version strings are free-form, but they are interpolated straight into a
# Maven Central URL path. Reject anything that could escape the intended
# {group}/{artifact}/{version}/ segment (path traversal, separators, whitespace,
# or control chars) before building a request. Legitimate versions only use
# alphanumerics plus ``. _ -`` (e.g. ``2.11.0``, ``1.0.0-RC1``, ``3.0-SNAPSHOT``).
def _safe_version(version: str) -> bool:
    return bool(version) and re.fullmatch(r"[A-Za-z0-9_.\-]+", version) is not None


# ---- Maven ComparableVersion (faithful port of the canonical algorithm) ------

_QUALIFIERS = ["alpha", "beta", "milestone", "rc", "snapshot", "", "sp"]
_RELEASE_INDEX = str(_QUALIFIERS.index(""))
_ALIASES = {"ga": "", "final": "", "release": "", "cr": "rc"}


def _comparable_qualifier(qualifier: str) -> str:
    qualifier = _ALIASES.get(qualifier, qualifier)
    if qualifier in _QUALIFIERS:
        return str(_QUALIFIERS.index(qualifier))
    return f"{len(_QUALIFIERS)}-{qualifier}"


class _IntItem:
    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = int(value)

    def is_null(self) -> bool:
        return self.value == 0

    def compare_to(self, other) -> int:
        if other is None:
            return 0 if self.value == 0 else 1
        if isinstance(other, _IntItem):
            return (self.value > other.value) - (self.value < other.value)
        # IntItem > StringItem and IntItem > ListItem.
        return 1


class _StringItem:
    __slots__ = ("value",)

    def __init__(self, value: str, followed_by_digit: bool) -> None:
        if followed_by_digit and len(value) == 1:
            value = {"a": "alpha", "b": "beta", "m": "milestone"}.get(value, value)
        self.value = value

    def is_null(self) -> bool:
        return _comparable_qualifier(self.value) == _RELEASE_INDEX

    def compare_to(self, other) -> int:
        if other is None:
            return _cmp_str(_comparable_qualifier(self.value), _RELEASE_INDEX)
        if isinstance(other, _IntItem):
            return -1  # StringItem < IntItem
        if isinstance(other, _StringItem):
            return _cmp_str(
                _comparable_qualifier(self.value), _comparable_qualifier(other.value)
            )
        # StringItem < ListItem.
        return -1


class _ListItem(list):
    def is_null(self) -> bool:
        return len(self) == 0

    def normalize(self) -> None:
        for i in range(len(self) - 1, -1, -1):
            last = self[i]
            if last.is_null():
                del self[i]
            elif not isinstance(last, _ListItem):
                break

    def compare_to(self, other) -> int:
        if other is None:
            if len(self) == 0:
                return 0
            return self[0].compare_to(None)
        if isinstance(other, _IntItem):
            return -1  # ListItem < IntItem
        if isinstance(other, _StringItem):
            return 1  # ListItem > StringItem
        # ListItem vs ListItem: element-wise.
        i = 0
        while i < len(self) or i < len(other):
            left = self[i] if i < len(self) else None
            right = other[i] if i < len(other) else None
            if left is None:
                result = 0 if right is None else -right.compare_to(None)
            else:
                result = left.compare_to(right)
            if result != 0:
                return result
            i += 1
        return 0


def _cmp_str(a: str, b: str) -> int:
    return (a > b) - (a < b)


def _parse_item(is_digit: bool, buf: str):
    if is_digit:
        return _IntItem(buf.lstrip("0") or "0")
    return _StringItem(buf, False)


class ComparableVersion:
    """Port of org.apache.maven.artifact.versioning.ComparableVersion."""

    def __init__(self, version: str) -> None:
        self.value = version
        self.items = self._parse(version)

    @staticmethod
    def _parse(version: str) -> _ListItem:
        version = version.lower()
        items = _ListItem()
        current = items
        stack = [items]
        is_digit = False
        start = 0
        for i, ch in enumerate(version):
            if ch == ".":
                if i == start:
                    current.append(_IntItem("0"))
                else:
                    current.append(_parse_item(is_digit, version[start:i]))
                start = i + 1
            elif ch == "-":
                if i == start:
                    current.append(_IntItem("0"))
                else:
                    current.append(_parse_item(is_digit, version[start:i]))
                start = i + 1
                new = _ListItem()
                current.append(new)
                current = new
                stack.append(new)
            elif ch.isdigit():
                if not is_digit and i > start:
                    current.append(_StringItem(version[start:i], True))
                    start = i
                    new = _ListItem()
                    current.append(new)
                    current = new
                    stack.append(new)
                is_digit = True
            else:
                if is_digit and i > start:
                    current.append(_parse_item(True, version[start:i]))
                    start = i
                    new = _ListItem()
                    current.append(new)
                    current = new
                    stack.append(new)
                is_digit = False
        if len(version) > start:
            current.append(_parse_item(is_digit, version[start:]))
        for lst in reversed(stack):
            lst.normalize()
        return items

    def compare_to(self, other: "ComparableVersion") -> int:
        return self.items.compare_to(other.items)


def version_compare(a: str, b: str) -> int:
    try:
        return ComparableVersion(a).compare_to(ComparableVersion(b))
    except Exception:  # pragma: no cover - never let a weird version crash sort
        return _cmp_str(a, b)


_version_key = functools.cmp_to_key(version_compare)


# ---- local repository (~/.m2) ------------------------------------------------


def _m2_repository() -> str:
    return os.path.join(os.path.expanduser("~"), ".m2", "repository")


def _artifact_dir(group: str, artifact: str) -> str:
    return os.path.join(_m2_repository(), *group.split("."), artifact)


def installed_versions(group: str, artifact: str) -> list[str]:
    adir = _artifact_dir(group, artifact)
    if not os.path.isdir(adir):
        return []
    out = []
    for d in os.listdir(adir):
        vdir = os.path.join(adir, d)
        if os.path.isdir(vdir) and os.path.isfile(
            os.path.join(vdir, f"{artifact}-{d}.jar")
        ):
            out.append(d)
    return sorted(out, key=_version_key)


def latest_installed(group: str, artifact: str) -> str | None:
    versions = installed_versions(group, artifact)
    return versions[-1] if versions else None


def installed_jar_path(
    group: str, artifact: str, version: str | None = None
) -> tuple[str, str] | None:
    """Return ``(jar_path, version)`` for an installed artifact, or None."""
    version = version or latest_installed(group, artifact)
    if version is None or not _safe_version(version):
        return None
    jar = os.path.join(_artifact_dir(group, artifact), version, f"{artifact}-{version}.jar")
    return (jar, version) if os.path.isfile(jar) else None


def list_installed(limit: int = 500) -> list[str]:
    """Best-effort list of ``group:artifact`` coordinates in ~/.m2. Bounded."""
    root = _m2_repository()
    if not os.path.isdir(root):
        return []
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # An artifact dir has version subdirs each holding a matching jar.
        rel = os.path.relpath(dirpath, root)
        if rel == ".":
            continue
        jars = [f for f in filenames if f.endswith(".jar")]
        if not jars:
            continue
        # dirpath is .../<group-as-path>/<artifact>/<version>; the artifact is
        # the parent dir, the group is everything above it.
        version = os.path.basename(dirpath)
        artifact = os.path.basename(os.path.dirname(dirpath))
        group_path = os.path.relpath(os.path.dirname(os.path.dirname(dirpath)), root)
        if f"{artifact}-{version}.jar" not in jars:
            continue
        group = group_path.replace(os.sep, ".")
        coord = f"{group}:{artifact}"
        if coord not in found:
            found.append(coord)
            if len(found) >= limit:
                break
    return sorted(found)


# ---- Maven Central HTTP ------------------------------------------------------


def _get(url: str, cap: int) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:
            data = resp.read(cap + 1)
    except Exception:
        return None
    if len(data) > cap:
        return None
    return data


def list_versions_online(group: str, artifact: str) -> list[str]:
    url = f"{_CENTRAL}/{_group_path(group)}/{artifact}/maven-metadata.xml"
    data = _get(url, _MAX_METADATA)
    if data is None:
        return []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    versions = [e.text for e in root.findall(".//versioning/versions/version") if e.text]
    return sorted(versions, key=_version_key)


def fetch_pom(group: str, artifact: str, version: str) -> bytes | None:
    if not _safe_version(version):
        return None
    url = f"{_CENTRAL}/{_group_path(group)}/{artifact}/{version}/{artifact}-{version}.pom"
    return _get(url, _MAX_POM)


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def pom_info(pom_bytes: bytes) -> tuple[str | None, tuple[str, str] | None]:
    """Return ``(packaging, relocation)`` from a POM.

    ``packaging`` is the declared ``<packaging>`` (default ``jar``).
    ``relocation`` is ``(group, artifact)`` if the POM relocates to new
    coordinates (``<distributionManagement><relocation>``), else None.
    """
    try:
        root = ET.fromstring(pom_bytes)
    except ET.ParseError:
        return None, None

    packaging = None
    relocation = None
    for child in root:
        tag = _strip_ns(child.tag)
        if tag == "packaging" and child.text:
            packaging = child.text.strip().lower()
        elif tag == "distributionManagement":
            for dm in child:
                if _strip_ns(dm.tag) != "relocation":
                    continue
                g = a = None
                for r in dm:
                    rt = _strip_ns(r.tag)
                    if rt == "groupId" and r.text:
                        g = r.text.strip()
                    elif rt == "artifactId" and r.text:
                        a = r.text.strip()
                if g or a:
                    relocation = (g or "", a or "")
    return packaging, relocation


def fetch_jar(group: str, artifact: str, version: str) -> bytes | None:
    """Download the main ``{artifact}-{version}.jar`` (never -sources/-javadoc).

    Returns the jar bytes (held in memory only), or None on any failure.
    """
    if not _safe_version(version):
        return None
    url = f"{_CENTRAL}/{_group_path(group)}/{artifact}/{version}/{artifact}-{version}.jar"
    return _get(url, _MAX_JAR)
