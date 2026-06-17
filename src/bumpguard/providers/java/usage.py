"""Pure-Python Java source usage scanner.

Parses Java *source* (no compilation, no execution) and extracts:

- ``imports``: ``import [static] a.b.C[.*];`` directives.
- ``locals``: declared local/parameter variable types (``List list = ...``),
  used to resolve ``list.add(...)`` back to the declaring type.
- ``refs``: object creations (``new Foo(...)``), method invocations
  (``foo(...)`` / ``a.b.c(...)``) with positional argument counts, and
  qualified member accesses (``Foo.CONSTANT``).

The provider's resolver turns these into neutral ``Usage`` objects, expanding
short type names via imports to *candidate* confidence so a namespace collision
can never produce a false definite breakage.

This is deliberately a robust heuristic scanner, not a full Java grammar: it
strips comments and string/char/text-block literals first (so their contents
can't be mistaken for code), then walks the cleaned text. It is honest about
what it can't see (generics are ignored; see provider notes).

Known v1 limitations (intentional, low-impact):

- The scanner does not distinguish a *use* of a name from its *declaration*. A
  method or constructor declaration (``void m(...)``) is captured as a ``m``
  call ref, and an ``import a.b.C;`` line contributes a ``C`` member ref. These
  spurious self-references resolve to single-segment / unqualified names, which
  the provider caps at *candidate* confidence — so they can never escalate to a
  definite (hard) breakage, only ever to a "potentially breaking" hint at worst.
  A stricter declaration-vs-reference pass is deferred to avoid regressing the
  comment/literal stripping that keeps the scanner safe.
"""

from __future__ import annotations

import bisect
import re

# Java keywords that can appear immediately before "(" but are not call targets.
_KEYWORDS = {
    "if", "for", "while", "switch", "catch", "synchronized", "return", "new",
    "assert", "throw", "throws", "else", "do", "try", "finally", "instanceof",
    "super", "this", "case", "yield", "break", "continue", "import", "package",
    "class", "interface", "enum", "extends", "implements", "void", "true",
    "false", "null", "var", "record",
}

_PRIMITIVES = {"int", "long", "short", "byte", "char", "boolean", "float", "double"}

_IMPORT_RE = re.compile(
    r"^[ \t]*import[ \t]+(static[ \t]+)?([A-Za-z_$][\w.$]*?)(\.\*)?[ \t]*;",
    re.MULTILINE,
)

# Local/parameter declarations: a Capitalized (or qualified) type, optional
# generics/array markers, then a lower-case variable, then a terminator.
_LOCAL_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*)"  # type
    r"(?:\s*<[^;{}()=]*>)?"                                      # generics (ignored)
    r"(?:\s*\[\s*\])*"                                           # array dims
    r"\s+([a-z_$][A-Za-z0-9_$]*)"                                 # variable
    r"\s*[=;:)]"                                                  # terminator
)

_NAME_CHAIN = re.compile(
    r"[A-Za-z_$][A-Za-z0-9_$]*(?:\s*\.\s*[A-Za-z_$][A-Za-z0-9_$]*)*"
)


def _clean(code: str) -> str:
    """Replace comments and string/char/text-block literals with spaces, keeping
    every newline so character offsets still map to the right source line."""
    out = []
    i = 0
    n = len(code)
    while i < n:
        ch = code[i]
        two = code[i : i + 2]
        if two == "//":
            j = code.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif two == "/*":
            j = code.find("*/", i + 2)
            j = n if j < 0 else j + 2
            chunk = code[i:j]
            out.append("".join(c if c == "\n" else " " for c in chunk))
            i = j
        elif code[i : i + 3] == '"""':
            j = code.find('"""', i + 3)
            j = n if j < 0 else j + 3
            chunk = code[i:j]
            out.append("".join(c if c == "\n" else " " for c in chunk))
            i = j
        elif ch == '"' or ch == "'":
            quote = ch
            j = i + 1
            while j < n:
                if code[j] == "\\":
                    j += 2
                    continue
                if code[j] == quote or code[j] == "\n":
                    break
                j += 1
            j = min(j + 1, n) if j < n and code[j] == quote else j
            chunk = code[i:j]
            out.append("".join(c if c == "\n" else " " for c in chunk))
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _line_index(code: str) -> list[int]:
    """Return sorted offsets of each line start, for offset -> line lookup."""
    starts = [0]
    for m in re.finditer("\n", code):
        starts.append(m.end())
    return starts


def _line_of(starts: list[int], offset: int) -> int:
    return bisect.bisect_right(starts, offset)


def _count_args(text: str, open_idx: int) -> tuple[int, int]:
    """Given ``text[open_idx] == '('``, return ``(positional_count, close_idx)``.

    Commas are only counted at the call's own nesting depth, so nested calls,
    arrays and lambdas don't inflate the count.
    """
    depth = 0
    commas = 0
    saw_content = False
    i = open_idx
    n = len(text)
    pairs = {")": "(", "]": "[", "}": "{"}
    opens = set("([{")
    while i < n:
        c = text[i]
        if c in opens:
            depth += 1
        elif c in pairs:
            depth -= 1
            if depth == 0:
                return (commas + 1 if saw_content else 0), i
        elif depth == 1:
            if c == ",":
                commas += 1
            elif not c.isspace():
                saw_content = True
        i += 1
    return (commas + 1 if saw_content else 0), n


def scan(code: str) -> dict:
    """Return ``{"imports": [...], "locals": [...], "refs": [...]}``."""
    text = _clean(code)
    starts = _line_index(text)

    imports = []
    for m in _IMPORT_RE.finditer(text):
        is_static = bool(m.group(1))
        fqn = m.group(2)
        wildcard = bool(m.group(3))
        simple = fqn.rsplit(".", 1)[-1]
        imports.append(
            {"name": fqn, "simple": simple, "static": is_static, "wildcard": wildcard}
        )

    locals_ = []
    seen_locals: set[str] = set()
    for m in _LOCAL_RE.finditer(text):
        type_name = m.group(1)
        var = m.group(2)
        if var in _KEYWORDS or var in seen_locals:
            continue
        seen_locals.add(var)
        locals_.append({"var": var, "type": type_name})

    refs = []
    prev_text = ""
    prev_end = -1
    for m in _NAME_CHAIN.finditer(text):
        chain_raw = m.group(0)
        chain = re.sub(r"\s+", "", chain_raw)
        last_seg = chain.rsplit(".", 1)[-1]
        start, end = m.start(), m.end()

        # Is this a constructor target? (immediately preceded by `new`)
        is_new = prev_text == "new" and prev_end <= start

        # Find the next significant char.
        j = end
        while j < len(text) and text[j].isspace():
            j += 1
        next_char = text[j] if j < len(text) else ""

        if next_char == "(" and last_seg not in _KEYWORDS:
            count, _close = _count_args(text, j)
            refs.append(
                {
                    "name": chain,
                    "isCall": True,
                    "positionalCount": count,
                    "line": _line_of(starts, start),
                }
            )
        elif is_new and last_seg not in _KEYWORDS and chain not in _PRIMITIVES:
            # `new Foo[...]` / `new Foo{...}` array creation: still a type ref.
            refs.append(
                {
                    "name": chain,
                    "isCall": True,
                    "positionalCount": 0,
                    "line": _line_of(starts, start),
                }
            )
        elif "." in chain and last_seg not in _KEYWORDS and chain.split(".")[0] not in _KEYWORDS:
            # Qualified member access not being called: `Foo.CONSTANT`, `obj.field`.
            refs.append(
                {
                    "name": chain,
                    "isCall": False,
                    "positionalCount": 0,
                    "line": _line_of(starts, start),
                }
            )

        prev_text = chain
        prev_end = end

    return {"imports": imports, "locals": locals_, "refs": refs}
