"""Pure-Python Java ``.class`` bytecode reader — the Java surface extractor.

A ``.jar`` is a zip of compiled ``.class`` files. This module reads each class
file's **metadata tables** (constant pool, access flags, field/method
descriptors, ``InnerClasses``/``MethodParameters`` attributes) using ``struct``.
It NEVER executes any bytecode — it only parses the well-defined, deterministic
class-file format (JVMS §4). No JDK is required.

The output mirrors the shape the .NET extractor emits so the provider's mapping
layer is uniform: ``{"partial": bool, "symbols": [...], "notes": [...]}`` where
each symbol is ``{path, kind, overloaded, acceptsVarargs, params:[{name,...}]}``.

Design decisions (see docs/ADD_A_PROVIDER.md and the provider docstring):

- A parameter's *identity* is its **erased type + position** (``"0:int"``,
  ``"1:java.lang.String"``), not a source name. Bytecode parameter names are
  optional and compiler-dependent, but descriptors are always present and
  canonical. This makes the neutral, name-based diff observe real same-arity
  type changes while staying immune to ``-parameters`` inconsistency.
- Overloaded members (same name, >1 entry) are marked ``overloaded`` and compared
  by presence only — individual-overload removal needs semantic binding.
- Varargs keep their trailing array parameter (so ``T[]`` and ``T...`` have an
  identical, non-breaking surface) and set ``acceptsVarargs``.
- Non-static inner-class constructors drop the synthetic leading enclosing
  instance parameter; ``MethodParameters`` synthetic/mandated params are dropped.
- Generics are type-erased at the descriptor level; the surface is always
  ``partial`` and notes the v1 limitations.
"""

from __future__ import annotations

import struct
import zipfile
from dataclasses import dataclass, field
from io import BytesIO

# ---- access flags (JVMS §4.1, §4.5, §4.6, §4.7.6) ----------------------------
ACC_PUBLIC = 0x0001
ACC_PRIVATE = 0x0002
ACC_PROTECTED = 0x0004
ACC_STATIC = 0x0008
ACC_INTERFACE = 0x0200
ACC_SYNTHETIC = 0x1000
ACC_ANNOTATION = 0x2000
ACC_ENUM = 0x4000
ACC_MODULE = 0x8000
ACC_BRIDGE = 0x0040  # methods only
ACC_VARARGS = 0x0080  # methods only
ACC_MANDATED = 0x8000  # MethodParameters entries

# Defensive caps so a hostile/corrupt jar can't exhaust memory.
_MAX_CLASS_BYTES = 16 * 1024 * 1024
_MAX_CLASSES = 200_000

_PRIMITIVES = {
    "B": "byte",
    "C": "char",
    "D": "double",
    "F": "float",
    "I": "int",
    "J": "long",
    "S": "short",
    "Z": "boolean",
    "V": "void",
}


class ClassFileError(Exception):
    """Raised when a class file is too corrupt/truncated to read."""


class _Reader:
    """Big-endian cursor over a bytes buffer."""

    __slots__ = ("buf", "pos", "size")

    def __init__(self, buf: bytes) -> None:
        self.buf = buf
        self.pos = 0
        self.size = len(buf)

    def u1(self) -> int:
        if self.pos + 1 > self.size:
            raise ClassFileError("unexpected EOF (u1)")
        v = self.buf[self.pos]
        self.pos += 1
        return v

    def u2(self) -> int:
        if self.pos + 2 > self.size:
            raise ClassFileError("unexpected EOF (u2)")
        v = struct.unpack_from(">H", self.buf, self.pos)[0]
        self.pos += 2
        return v

    def u4(self) -> int:
        if self.pos + 4 > self.size:
            raise ClassFileError("unexpected EOF (u4)")
        v = struct.unpack_from(">I", self.buf, self.pos)[0]
        self.pos += 4
        return v

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > self.size:
            raise ClassFileError("unexpected EOF (bytes)")
        v = self.buf[self.pos : self.pos + n]
        self.pos += n
        return v

    def skip(self, n: int) -> None:
        if n < 0 or self.pos + n > self.size:
            raise ClassFileError("unexpected EOF (skip)")
        self.pos += n


@dataclass
class _Member:
    access: int
    name: str
    descriptor: str
    # MethodParameters access flags per parameter, when the attribute is present.
    param_flags: list[int] | None = None


@dataclass
class _InnerEntry:
    inner: str | None  # internal name of the inner class
    outer: str | None  # internal name of the enclosing class
    simple: str | None  # simple source name; None == anonymous
    access: int = 0


@dataclass
class _ClassFile:
    internal_name: str
    access: int
    fields: list[_Member] = field(default_factory=list)
    methods: list[_Member] = field(default_factory=list)
    inner_classes: dict[str, _InnerEntry] = field(default_factory=dict)


# ---- constant pool -----------------------------------------------------------


def _parse_constant_pool(r: _Reader) -> dict[int, object]:
    """Return ``{index: value}`` where Utf8 -> str and Class -> name_index(int).

    Other constants are stored as ``None`` (we only need Utf8 + Class), but their
    bytes are still consumed so the cursor stays aligned.
    """
    count = r.u2()
    pool: dict[int, object] = {}
    i = 1
    while i < count:
        tag = r.u1()
        if tag == 1:  # Utf8
            length = r.u2()
            raw = r.take(length)
            pool[i] = _decode_modified_utf8(raw)
        elif tag == 7:  # Class -> name_index
            pool[i] = ("class", r.u2())
        elif tag in (9, 10, 11, 12, 17, 18):  # *ref / NameAndType / (Invoke)Dynamic
            r.skip(4)
            pool[i] = None
        elif tag in (3, 4):  # Integer / Float
            r.skip(4)
            pool[i] = None
        elif tag in (5, 6):  # Long / Double take two slots
            r.skip(8)
            pool[i] = None
            i += 1
        elif tag == 8:  # String
            r.skip(2)
            pool[i] = None
        elif tag == 15:  # MethodHandle
            r.skip(3)
            pool[i] = None
        elif tag == 16:  # MethodType
            r.skip(2)
            pool[i] = None
        elif tag in (19, 20):  # Module / Package
            r.skip(2)
            pool[i] = None
        else:
            raise ClassFileError(f"unknown constant pool tag {tag}")
        i += 1
    return pool


def _decode_modified_utf8(raw: bytes) -> str:
    """Decode JVM 'modified UTF-8'. Plain UTF-8 covers the overwhelming majority
    of names/descriptors; fall back to a latin-1 best effort if needed."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")


def _utf8(pool: dict[int, object], idx: int) -> str:
    v = pool.get(idx)
    if isinstance(v, str):
        return v
    raise ClassFileError(f"expected Utf8 at constant-pool index {idx}")


def _class_name(pool: dict[int, object], idx: int) -> str | None:
    if idx == 0:
        return None
    v = pool.get(idx)
    if isinstance(v, tuple) and v[0] == "class":
        return _utf8(pool, v[1])
    return None


# ---- attribute parsing -------------------------------------------------------


def _read_attributes(r: _Reader) -> list[tuple[int, bytes]]:
    """Read an attributes table as ``[(name_index, info_bytes)]``.

    Every attribute carries its own ``u4`` length, so unknown attributes are
    skipped exactly — this keeps the parser forward-compatible with newer class
    formats (fail-closed: we never misread later tables)."""
    out: list[tuple[int, bytes]] = []
    n = r.u2()
    for _ in range(n):
        name_index = r.u2()
        length = r.u4()
        out.append((name_index, r.take(length)))
    return out


def _parse_method_parameters(info: bytes) -> list[int]:
    """Return the per-parameter access flags from a MethodParameters attribute."""
    r = _Reader(info)
    count = r.u1()
    flags: list[int] = []
    for _ in range(count):
        r.u2()  # name_index (often 0)
        flags.append(r.u2())
    return flags


def _parse_inner_classes(
    pool: dict[int, object], info: bytes
) -> dict[str, _InnerEntry]:
    r = _Reader(info)
    number = r.u2()
    entries: dict[str, _InnerEntry] = {}
    for _ in range(number):
        inner_info = r.u2()
        outer_info = r.u2()
        inner_name_index = r.u2()
        access = r.u2()
        inner = _class_name(pool, inner_info)
        outer = _class_name(pool, outer_info)
        simple = _utf8(pool, inner_name_index) if inner_name_index != 0 else None
        if inner:
            entries[inner] = _InnerEntry(inner=inner, outer=outer, simple=simple, access=access)
    return entries


# ---- class file --------------------------------------------------------------


def parse_class_file(data: bytes) -> _ClassFile:
    if len(data) > _MAX_CLASS_BYTES:
        raise ClassFileError("class file too large")
    r = _Reader(data)
    if r.u4() != 0xCAFEBABE:
        raise ClassFileError("bad magic")
    r.u2()  # minor
    r.u2()  # major
    pool = _parse_constant_pool(r)

    access = r.u2()
    this_class = r.u2()
    internal_name = _class_name(pool, this_class)
    if not internal_name:
        raise ClassFileError("missing this_class name")
    r.u2()  # super_class
    interfaces_count = r.u2()
    r.skip(interfaces_count * 2)

    fields = _read_members(r, pool, methods=False)
    methods = _read_members(r, pool, methods=True)

    inner_classes: dict[str, _InnerEntry] = {}
    for name_index, info in _read_attributes(r):
        if _utf8(pool, name_index) == "InnerClasses":
            inner_classes = _parse_inner_classes(pool, info)

    return _ClassFile(
        internal_name=internal_name,
        access=access,
        fields=fields,
        methods=methods,
        inner_classes=inner_classes,
    )


def _read_members(r: _Reader, pool: dict[int, object], methods: bool) -> list[_Member]:
    count = r.u2()
    out: list[_Member] = []
    for _ in range(count):
        access = r.u2()
        name = _utf8(pool, r.u2())
        descriptor = _utf8(pool, r.u2())
        param_flags: list[int] | None = None
        for attr_name_index, info in _read_attributes(r):
            if methods and _utf8(pool, attr_name_index) == "MethodParameters":
                try:
                    param_flags = _parse_method_parameters(info)
                except ClassFileError:
                    param_flags = None
        out.append(_Member(access=access, name=name, descriptor=descriptor, param_flags=param_flags))
    return out


# ---- descriptor helpers ------------------------------------------------------


def _readable_type(internal: str) -> str:
    """Erased descriptor field type -> readable Java type. ``[Ljava/lang/String;``
    -> ``java.lang.String[]``; ``I`` -> ``int``."""
    dims = 0
    while internal.startswith("["):
        dims += 1
        internal = internal[1:]
    if internal.startswith("L") and internal.endswith(";"):
        base = internal[1:-1].replace("/", ".")
    else:
        base = _PRIMITIVES.get(internal, internal)
    return base + "[]" * dims


def parse_descriptor_params(descriptor: str) -> list[str]:
    """Return the readable erased types of a method descriptor's parameters."""
    if not descriptor.startswith("("):
        return []
    end = descriptor.find(")")
    if end < 0:
        return []
    body = descriptor[1:end]
    params: list[str] = []
    i = 0
    n = len(body)
    while i < n:
        start = i
        while i < n and body[i] == "[":
            i += 1
        if i >= n:
            break
        c = body[i]
        if c == "L":
            semi = body.find(";", i)
            if semi < 0:
                break
            i = semi + 1
        else:
            i += 1
        params.append(_readable_type(body[start:i]))
    return params


# ---- naming + visibility -----------------------------------------------------


def _source_name(cf: _ClassFile) -> tuple[str | None, int, bool]:
    """Return ``(source_dotted_name, effective_access, is_static)``.

    Uses ``InnerClasses`` to map a genuinely-nested ``Outer$Inner`` to
    ``Outer.Inner`` and to recover the accurate inner access flags (a nested
    class file's own top-level flags don't carry protected/private). A literal
    ``$`` in a true top-level class name is preserved. Returns ``(None, ...)`` for
    anonymous/synthetic classes that should be skipped.
    """
    name = cf.internal_name
    entry = cf.inner_classes.get(name)
    if entry is None:
        # Not nested (or no InnerClasses record): a top-level type. Keep '$'.
        return name.replace("/", "."), cf.access, True

    if entry.simple is None:
        return None, 0, False  # anonymous / synthetic local class

    parts = [entry.simple]
    cur = entry.outer
    guard = 0
    while cur is not None and guard < 64:
        guard += 1
        outer_entry = cf.inner_classes.get(cur)
        if outer_entry is not None and outer_entry.simple:
            parts.append(outer_entry.simple)
            cur = outer_entry.outer
        else:
            # Reached a top-level enclosing class.
            base = cur.replace("/", ".")
            parts.append(base)
            cur = None
            break
    else:
        cur = None
    if cur is not None:
        parts.append(cur.replace("/", "."))

    source = ".".join(reversed(parts))
    is_static = bool(entry.access & ACC_STATIC)
    return source, entry.access, is_static


def _is_visible(access: int) -> bool:
    return bool(access & (ACC_PUBLIC | ACC_PROTECTED))


def _kind_for_class(access: int) -> str:
    # Interfaces, enums, annotations all map to the neutral "class" kind.
    return "class"


# ---- parameter mapping -------------------------------------------------------


def _member_params(m: _Member, *, is_ctor: bool, drop_leading: bool) -> tuple[list[dict], bool]:
    """Map a method/ctor descriptor to neutral params + accepts_varargs.

    Parameter identity is ``"<index>:<erased type>"`` so the neutral diff observes
    real type/arity changes. Synthetic/mandated params (via MethodParameters) and
    a non-static inner class's leading enclosing-instance param are dropped so the
    surface matches what Java *source* callers actually pass.
    """
    types = parse_descriptor_params(m.descriptor)
    keep = [True] * len(types)

    if m.param_flags is not None and len(m.param_flags) == len(types):
        for i, fl in enumerate(m.param_flags):
            if fl & (ACC_SYNTHETIC | ACC_MANDATED):
                keep[i] = False
    elif is_ctor and drop_leading and types:
        keep[0] = False

    accepts_varargs = bool(m.access & ACC_VARARGS)
    params: list[dict] = []
    pos = 0
    for i, t in enumerate(types):
        if not keep[i]:
            continue
        params.append({"name": f"{pos}:{t}", "hasDefault": False})
        pos += 1
    return params, accepts_varargs


# ---- surface assembly --------------------------------------------------------


def _extract_class_symbols(cf: _ClassFile, symbols: list[dict]) -> bool:
    """Append this class's public/protected symbols. Return True if it was part
    of the public surface (i.e. emitted)."""
    source, access, is_static = _source_name(cf)
    if source is None:
        return False
    if access & (ACC_SYNTHETIC | ACC_MODULE):
        return False
    if not _is_visible(access):
        return False

    nested = cf.internal_name in cf.inner_classes
    drop_leading = nested and not is_static

    # Constructors fold into the type symbol (1 -> params, >1 -> overloaded).
    ctors = [
        m
        for m in cf.methods
        if m.name == "<init>"
        and _is_visible(m.access)
        and not (m.access & (ACC_SYNTHETIC | ACC_BRIDGE))
    ]
    class_sym: dict = {"path": source, "kind": _kind_for_class(access)}
    if len(ctors) == 1:
        params, varargs = _member_params(ctors[0], is_ctor=True, drop_leading=drop_leading)
        class_sym["params"] = params
        class_sym["acceptsVarargs"] = varargs
    elif len(ctors) > 1:
        class_sym["overloaded"] = True
    symbols.append(class_sym)

    # Fields first, then methods, so a method wins a rare field/method name clash
    # (consistent on both sides -> no spurious kind-change).
    for fld in cf.fields:
        if not _is_visible(fld.access) or (fld.access & ACC_SYNTHETIC):
            continue
        symbols.append({"path": f"{source}.{fld.name}", "kind": "attribute"})

    groups: dict[str, list[_Member]] = {}
    for m in cf.methods:
        if m.name in ("<init>", "<clinit>"):
            continue
        if not _is_visible(m.access) or (m.access & (ACC_SYNTHETIC | ACC_BRIDGE)):
            continue
        groups.setdefault(m.name, []).append(m)

    for mname, overloads in groups.items():
        sym: dict = {"path": f"{source}.{mname}", "kind": "method"}
        if len(overloads) == 1:
            params, varargs = _member_params(overloads[0], is_ctor=False, drop_leading=False)
            sym["params"] = params
            sym["acceptsVarargs"] = varargs
        else:
            sym["overloaded"] = True
        symbols.append(sym)
    return True


def _select_class_entries(jar: zipfile.ZipFile) -> dict[str, str]:
    """Map a class path to the zip entry to read for it, applying multi-release
    overlay selection: the highest ``META-INF/versions/N`` wins, else base."""
    base: dict[str, str] = {}
    overlays: dict[str, tuple[int, str]] = {}
    for name in jar.namelist():
        if not name.endswith(".class"):
            continue
        if name in ("module-info.class",) or name.endswith("/module-info.class"):
            continue
        if name.endswith("package-info.class"):
            continue
        if name.startswith("META-INF/versions/"):
            rest = name[len("META-INF/versions/") :]
            ver, _, cls = rest.partition("/")
            if not cls or cls.endswith("module-info.class"):
                continue
            try:
                n = int(ver)
            except ValueError:
                continue
            cur = overlays.get(cls)
            if cur is None or n > cur[0]:
                overlays[cls] = (n, name)
        elif name.startswith("META-INF/"):
            continue
        else:
            base[name] = name
    selected = dict(base)
    for cls, (_n, entry) in overlays.items():
        selected[cls] = entry
    return selected


def extract_surface(jar_bytes: bytes) -> dict | None:
    """Extract the public/protected API surface of a ``.jar``.

    Returns the surface dict, or ``None`` if the input is not a readable jar or
    contains no public API. The result is always ``partial`` (generics are erased
    and a few categories aren't represented); ``notes`` spell out the limits.
    """
    try:
        jar = zipfile.ZipFile(BytesIO(jar_bytes))
    except (zipfile.BadZipFile, OSError):
        return None

    symbols: list[dict] = []
    parsed = 0
    failed = 0
    multi_release = False
    emitted_any = False

    with jar:
        entries = _select_class_entries(jar)
        if len(entries) > _MAX_CLASSES:
            return None
        for cls_path, entry in entries.items():
            if entry.startswith("META-INF/versions/"):
                multi_release = True
            try:
                data = jar.read(entry)
            except (zipfile.BadZipFile, OSError, KeyError):
                failed += 1
                continue
            if len(data) > _MAX_CLASS_BYTES:
                failed += 1
                continue
            try:
                cf = parse_class_file(data)
            except ClassFileError:
                failed += 1
                continue
            parsed += 1
            try:
                if _extract_class_symbols(cf, symbols):
                    emitted_any = True
            except ClassFileError:
                failed += 1

    if parsed == 0 and not emitted_any:
        return None

    notes = [
        "Public API extracted from .class bytecode metadata (no code executed).",
        "Generics are type-erased and not compared; return-type-only changes and "
        "individual overloaded-method changes are not detected in v1.",
    ]
    if multi_release:
        notes.append("Multi-release jar: the newest bytecode overlay per class was used.")
    if failed:
        notes.append(f"{failed} class file(s) could not be parsed and were skipped.")

    return {"partial": True, "symbols": symbols, "notes": notes}
