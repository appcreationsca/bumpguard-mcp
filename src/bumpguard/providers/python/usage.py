"""Python provider — scan user code for API usages.

Parses a snippet/file with AST and resolves references back to fully-qualified
dotted paths (matching how the surface extractor names symbols), so the two can
be compared. Handles import aliases, ``from x import y`` re-export paths, and
simple "instance of a constructed class" tracking — the patterns that cover the
vast majority of real call sites — while staying honest about what it can't do.
"""

from __future__ import annotations

import ast

from ...core.models import ImportRef, Usage


def _top_package(dotted: str) -> str:
    return dotted.split(".", 1)[0]


class _ImportCollector(ast.NodeVisitor):
    """First pass: map local names to fully-qualified module/symbol paths."""

    def __init__(self) -> None:
        # local name -> fully qualified path it refers to
        self.bindings: dict[str, str] = {}

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.asname:
                self.bindings[alias.asname] = alias.name
            else:
                # `import a.b.c` binds the top name `a`.
                top = alias.name.split(".")[0]
                self.bindings[top] = top
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level and node.level > 0:
            # Relative imports in user code can't be resolved to a third-party
            # package; skip them.
            return
        if not node.module:
            return
        for alias in node.names:
            if alias.name == "*":
                continue
            local = alias.asname or alias.name
            self.bindings[local] = f"{node.module}.{alias.name}"
        self.generic_visit(node)


def _attr_chain(node: ast.AST) -> list[str] | None:
    """Return the dotted parts of an attribute chain rooted at a Name."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        parts.reverse()
        return parts
    return None


class _UsageCollector(ast.NodeVisitor):
    def __init__(self, bindings: dict[str, str], package: str | None) -> None:
        self.bindings = bindings
        self.package = package
        # local var name -> class path it was constructed from
        self.instances: dict[str, str] = {}
        self.usages: list[Usage] = []
        self._seen: set[tuple[str, int]] = set()
        # ids of AST nodes already accounted for by a larger resolved chain,
        # so we don't double-count the pieces of e.g. `a.b.c(...)`.
        self._consumed: set[int] = set()

    # Track `x = SomeClass(...)` so later `x.method(...)` resolves to the class.
    def visit_Assign(self, node: ast.Assign) -> None:
        if (
            isinstance(node.value, ast.Call)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            resolved = self._resolve(node.value.func)
            if resolved:
                self.instances[node.targets[0].id] = resolved
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        resolved = self._resolve(node.func)
        if resolved:
            kwargs = {kw.arg for kw in node.keywords if kw.arg}
            self._add(
                resolved,
                node.func,
                is_call=True,
                call_kwargs=kwargs,
                positional_count=len(node.args),
            )
            self._mark_consumed(node.func)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if id(node) not in self._consumed:
            resolved = self._resolve(node)
            if resolved:
                self._add(resolved, node, is_call=False)
                self._mark_consumed(node)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        # Bare references to imported symbols: `class S(BaseSettings)`,
        # `raise SomeError`, `@decorator`, callbacks passed by name, etc.
        if id(node) in self._consumed or isinstance(node.ctx, ast.Store):
            return
        resolved = self._resolve(node)
        # Only record references to a specific imported symbol (has a dotted
        # path), not bare module-name mentions like `os`.
        if resolved and "." in resolved:
            self._add(resolved, node, is_call=False)

    def _mark_consumed(self, node: ast.AST) -> None:
        cur = node
        while isinstance(cur, ast.Attribute):
            self._consumed.add(id(cur))
            cur = cur.value
        if isinstance(cur, ast.Name):
            self._consumed.add(id(cur))

    def _resolve(self, node: ast.AST) -> str | None:
        chain = _attr_chain(node)
        if not chain:
            return None
        root = chain[0]
        rest = chain[1:]

        if root in self.bindings:
            base = self.bindings[root]
            full = ".".join([base, *rest]) if rest else base
        elif root in self.instances:
            base = self.instances[root]
            full = ".".join([base, *rest]) if rest else base
        else:
            return None

        if self.package and _top_package(full) != self.package:
            return None
        return full

    def _add(self, path: str, node: ast.AST, **kw) -> None:
        line = getattr(node, "lineno", 0)
        key = (path, line)
        if key in self._seen:
            return
        self._seen.add(key)
        try:
            raw = ast.unparse(node)
        except Exception:
            raw = path
        self.usages.append(Usage(dotted_path=path, line=line, raw=raw, **kw))


def scan_usage(code: str, package: str | None = None) -> list[Usage]:
    """Return API usages found in ``code``. If ``package`` is given, only
    usages whose top-level package matches are returned."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    imports = _ImportCollector()
    imports.visit(tree)

    collector = _UsageCollector(imports.bindings, package)
    collector.visit(tree)
    return collector.usages


def parse_error(code: str) -> str | None:
    """Return the Python ``SyntaxError`` message if ``code`` doesn't parse, else
    None. Used so ``verify_snippet`` doesn't report broken code as ``verified``."""
    try:
        ast.parse(code)
    except SyntaxError as exc:
        where = f" (line {exc.lineno})" if exc.lineno else ""
        return f"{exc.msg}{where}"
    return None


def scan_imports(code: str) -> list[ImportRef]:
    """Return the third-party imports in ``code`` (top package + fq path)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    refs: list[ImportRef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                refs.append(
                    ImportRef(
                        top_package=top,
                        imported=alias.name,
                        line=getattr(node, "lineno", 0),
                        raw=f"import {alias.name}",
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            if (node.level and node.level > 0) or not node.module:
                continue
            top = node.module.split(".")[0]
            for alias in node.names:
                if alias.name == "*":
                    refs.append(
                        ImportRef(top, node.module, getattr(node, "lineno", 0), f"from {node.module} import *")
                    )
                else:
                    refs.append(
                        ImportRef(
                            top_package=top,
                            imported=f"{node.module}.{alias.name}",
                            line=getattr(node, "lineno", 0),
                            raw=f"from {node.module} import {alias.name}",
                        )
                    )
    return refs
