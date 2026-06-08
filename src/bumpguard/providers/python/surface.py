"""Python provider — AST-only public API surface extraction.

We never import or execute third-party code. We learn what a package exposes
by parsing its ``.py`` files, exactly like a type checker. This is safe (no
import side effects), deterministic, and works on a package version that isn't
installed (we just need its source on disk).
"""

from __future__ import annotations

import ast
import os

from ...core.models import Kind, Param, ParamKind, Symbol


def _annotation_to_str(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None


def _build_params(args: ast.arguments) -> tuple[list[Param], bool, bool]:
    params: list[Param] = []
    posonly = list(getattr(args, "posonlyargs", []))
    regular = list(args.args)
    positional = posonly + regular
    num_defaults = len(args.defaults)
    default_offset = len(positional) - num_defaults

    for i, a in enumerate(positional):
        kind = ParamKind.POSITIONAL_ONLY if i < len(posonly) else ParamKind.POSITIONAL
        params.append(
            Param(
                name=a.arg,
                kind=kind,
                has_default=i >= default_offset,
                annotation=_annotation_to_str(a.annotation),
            )
        )

    for a, default in zip(args.kwonlyargs, args.kw_defaults):
        params.append(
            Param(
                name=a.arg,
                kind=ParamKind.KEYWORD_ONLY,
                has_default=default is not None,
                annotation=_annotation_to_str(a.annotation),
            )
        )

    return params, args.vararg is not None, args.kwarg is not None


def _signature_str(params: list[Param], varargs: bool, kwargs: bool) -> str:
    parts: list[str] = []
    for p in params:
        if p.name in ("self", "cls"):
            continue
        s = p.name
        if p.annotation:
            s += f": {p.annotation}"
        if p.has_default:
            s += "=..."
        parts.append(s)
    if varargs:
        parts.append("*args")
    if kwargs:
        parts.append("**kwargs")
    return "(" + ", ".join(parts) + ")"


_KEPT_DUNDERS = {
    "__init__",
    "__call__",
    "__enter__",
    "__exit__",
    "__iter__",
    "__next__",
    "__getitem__",
    "__aenter__",
    "__aexit__",
}


def _is_public_name(name: str) -> bool:
    if name.startswith("__") and name.endswith("__"):
        return name in _KEPT_DUNDERS
    return not name.startswith("_")


def _module_dotted_path(file_path: str, root: str, package_name: str) -> str | None:
    rel = os.path.relpath(file_path, root)
    rel_no_ext = os.path.splitext(rel)[0]
    parts = rel_no_ext.replace("\\", "/").split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if any(
        part.startswith("_") and not (part.startswith("__") and part.endswith("__"))
        for part in parts
    ):
        return None
    return ".".join(parts) if parts else package_name


def _literal_str_list(node: ast.expr) -> set[str] | None:
    if isinstance(node, (ast.List, ast.Tuple)):
        out: set[str] = set()
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                out.add(elt.value)
        return out
    return None


def _assigned_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    names: list[str] = []
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                if isinstance(elt, ast.Name):
                    names.append(elt.id)
    return names


def _resolve_anchor(current_module: str, level: int, is_pkg_init: bool) -> str:
    """Resolve the package anchor for a relative import.

    For a package's ``__init__`` the current module name *is* the package, so a
    level-1 import (``from . import x``) resolves against it directly. For a
    regular module, level 1 means its parent package.
    """
    parts = current_module.split(".")
    drop = level - 1 if is_pkg_init else level
    if drop > 0:
        parts = parts[:-drop] if drop < len(parts) else []
    return ".".join(parts)


def _collect_reexports(
    node: ast.ImportFrom,
    current_module: str,
    is_pkg_init: bool,
    package_name: str,
    aliases: list[tuple[str, str]],
) -> None:
    """Record ``from ... import ...`` edges that stay inside this package so the
    re-exported name can be matched the way callers actually import it."""
    if node.level and node.level > 0:
        anchor = _resolve_anchor(current_module, node.level, is_pkg_init)
        target_mod = f"{anchor}.{node.module}" if node.module else anchor
    elif node.module and (node.module == package_name or node.module.startswith(package_name + ".")):
        target_mod = node.module
    else:
        return  # third-party / stdlib import — not part of this package's surface

    for alias in node.names:
        if alias.name == "*":
            continue
        exposed = alias.asname or alias.name
        alias_path = f"{current_module}.{exposed}"
        target_path = f"{target_mod}.{alias.name}"
        if alias_path != target_path:
            aliases.append((alias_path, target_path))


def _collect_reexports(
    node: ast.ImportFrom,
    current_module: str,
    is_pkg_init: bool,
    package_name: str,
    aliases: list[tuple[str, str]],
    is_public,
) -> None:
    """Record ``from ... import ...`` edges that stay inside this package so the
    re-exported name can be matched the way callers actually import it."""
    if node.level and node.level > 0:
        anchor = _resolve_anchor(current_module, node.level, is_pkg_init)
        target_mod = f"{anchor}.{node.module}" if node.module else anchor
    elif node.module and (node.module == package_name or node.module.startswith(package_name + ".")):
        target_mod = node.module
    else:
        return  # third-party / stdlib import — not part of this package's surface

    for alias in node.names:
        if alias.name == "*":
            continue
        exposed = alias.asname or alias.name
        if not is_public(exposed):
            continue
        alias_path = f"{current_module}.{exposed}"
        target_path = f"{target_mod}.{alias.name}"
        if alias_path != target_path:
            aliases.append((alias_path, target_path))


def _extract_from_module(
    tree: ast.Module,
    module_dotted: str,
    is_pkg_init: bool,
    package_name: str,
    symbols: dict[str, Symbol],
    aliases: list[tuple[str, str]],
    dynamic_modules: set[str],
) -> None:
    declared_all: set[str] | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    declared_all = _literal_str_list(node.value)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "__getattr__":
                # Module uses dynamic attribute access; its surface is not
                # statically knowable, so don't confidently flag missing names.
                dynamic_modules.add(module_dotted)

    def name_is_public(name: str) -> bool:
        if declared_all is not None:
            return name in declared_all
        return _is_public_name(name)

    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            _collect_reexports(
                node, module_dotted, is_pkg_init, package_name, aliases, name_is_public
            )
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not name_is_public(node.name):
                continue
            params, va, kw = _build_params(node.args)
            dotted = f"{module_dotted}.{node.name}"
            symbols[dotted] = Symbol(
                dotted_path=dotted,
                kind=Kind.FUNCTION,
                signature=_signature_str(params, va, kw),
                params=params,
                accepts_varargs=va,
                accepts_kwargs=kw,
            )
        elif isinstance(node, ast.ClassDef):
            if not name_is_public(node.name):
                continue
            class_dotted = f"{module_dotted}.{node.name}"
            symbols[class_dotted] = Symbol(dotted_path=class_dotted, kind=Kind.CLASS)
            _extract_class_members(node, class_dotted, symbols)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for name in _assigned_names(node):
                if name == "__all__" or not name_is_public(name):
                    continue
                dotted = f"{module_dotted}.{name}"
                symbols.setdefault(dotted, Symbol(dotted_path=dotted, kind=Kind.ATTRIBUTE))


def _decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name):
            names.add(dec.id)
        elif isinstance(dec, ast.Attribute):
            names.add(dec.attr)
        elif isinstance(dec, ast.Call):
            f = dec.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def _extract_self_attrs(
    method: ast.FunctionDef | ast.AsyncFunctionDef,
    class_dotted: str,
    symbols: dict[str, Symbol],
) -> None:
    """Record ``self.x = ...`` assignments as public instance attributes."""
    for sub in ast.walk(method):
        if not isinstance(sub, (ast.Assign, ast.AnnAssign)):
            continue
        targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and _is_public_name(target.attr)
            ):
                dotted = f"{class_dotted}.{target.attr}"
                symbols.setdefault(dotted, Symbol(dotted_path=dotted, kind=Kind.ATTRIBUTE))


def _extract_class_members(
    cls: ast.ClassDef, class_dotted: str, symbols: dict[str, Symbol]
) -> None:
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not _is_public_name(node.name):
                # Even private methods may set public self.* attributes.
                _extract_self_attrs(node, class_dotted, symbols)
                continue
            decorators = _decorator_names(node)
            if {"property", "cached_property"} & decorators:
                # A property reads like an attribute to callers.
                dotted = f"{class_dotted}.{node.name}"
                symbols[dotted] = Symbol(dotted_path=dotted, kind=Kind.ATTRIBUTE)
            else:
                params, va, kw = _build_params(node.args)
                dotted = f"{class_dotted}.{node.name}"
                symbols[dotted] = Symbol(
                    dotted_path=dotted,
                    kind=Kind.METHOD,
                    signature=_signature_str(params, va, kw),
                    params=params,
                    accepts_varargs=va,
                    accepts_kwargs=kw,
                )
            _extract_self_attrs(node, class_dotted, symbols)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for name in _assigned_names(node):
                if not _is_public_name(name):
                    continue
                dotted = f"{class_dotted}.{name}"
                symbols.setdefault(dotted, Symbol(dotted_path=dotted, kind=Kind.ATTRIBUTE))

    # Make the class callable: copy its __init__ signature onto the class symbol
    # so constructor changes are detected and constructor calls analysed.
    init = symbols.get(f"{class_dotted}.__init__")
    cls_sym = symbols.get(class_dotted)
    if init is not None and cls_sym is not None:
        cls_sym.params = [p for p in init.params if p.name not in ("self", "cls")]
        cls_sym.accepts_varargs = init.accepts_varargs
        cls_sym.accepts_kwargs = init.accepts_kwargs
        cls_sym.signature = init.signature


def _parse_file(
    fpath: str,
    module_dotted: str,
    is_pkg_init: bool,
    package_name: str,
    symbols: dict[str, Symbol],
    aliases: list[tuple[str, str]],
    dynamic_modules: set[str],
) -> None:
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return
    symbols.setdefault(module_dotted, Symbol(dotted_path=module_dotted, kind=Kind.MODULE))
    _extract_from_module(
        tree, module_dotted, is_pkg_init, package_name, symbols, aliases, dynamic_modules
    )


def _resolve_aliases(symbols: dict[str, Symbol], aliases: list[tuple[str, str]]) -> None:
    """Materialise re-exported names by copying the target symbol — and all of
    its members — under the alias path. Repeats to a fixpoint so chained
    re-exports (and re-exported classes/modules) resolve fully."""
    for _ in range(25):
        additions: dict[str, Symbol] = {}
        for alias_path, target_path in aliases:
            if alias_path in symbols:
                continue
            target = symbols.get(target_path)
            if target is None:
                continue
            clone = _shallow_clone(target)
            clone.dotted_path = alias_path
            additions[alias_path] = clone
            # Re-exporting a class or module also exposes its members.
            prefix = target_path + "."
            for path, sym in symbols.items():
                if path.startswith(prefix):
                    new_path = alias_path + "." + path[len(prefix):]
                    if new_path not in symbols:
                        member = _shallow_clone(sym)
                        member.dotted_path = new_path
                        additions.setdefault(new_path, member)
        if not additions:
            break
        symbols.update(additions)


def _shallow_clone(sym: Symbol) -> Symbol:
    return Symbol(
        dotted_path=sym.dotted_path,
        kind=sym.kind,
        signature=sym.signature,
        params=list(sym.params),
        accepts_varargs=sym.accepts_varargs,
        accepts_kwargs=sym.accepts_kwargs,
    )


def extract_symbols(
    package_root: str, package_name: str
) -> tuple[dict[str, Symbol], set[str]]:
    """Walk ``package_root`` (the dir containing ``<package_name>/`` or
    ``<package_name>.py``) and return (dotted-path -> Symbol, dynamic-modules)."""
    symbols: dict[str, Symbol] = {package_name: Symbol(dotted_path=package_name, kind=Kind.MODULE)}
    aliases: list[tuple[str, str]] = []
    dynamic_modules: set[str] = set()

    pkg_dir = os.path.join(package_root, package_name)
    if os.path.isdir(pkg_dir):
        for dirpath, dirnames, filenames in os.walk(pkg_dir):
            dirnames[:] = [
                d
                for d in dirnames
                if d not in ("tests", "test", "__pycache__")
                and not (d.startswith("_") and not (d.startswith("__") and d.endswith("__")))
            ]
            for fn in sorted(filenames):
                if not fn.endswith(".py"):
                    continue
                fpath = os.path.join(dirpath, fn)
                module_dotted = _module_dotted_path(fpath, package_root, package_name)
                if module_dotted is None:
                    continue
                _parse_file(
                    fpath, module_dotted, fn == "__init__.py", package_name,
                    symbols, aliases, dynamic_modules,
                )
    else:
        fpath = os.path.join(package_root, f"{package_name}.py")
        if os.path.isfile(fpath):
            _parse_file(fpath, package_name, False, package_name, symbols, aliases, dynamic_modules)

    _resolve_aliases(symbols, aliases)
    return symbols, dynamic_modules
