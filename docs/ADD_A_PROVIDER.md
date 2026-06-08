# Adding a language provider

BumpGuard's core (diff, breaking‑change classification, usage→change matching,
reporting, and the MCP tools) is **language‑neutral**. To support a new
ecosystem you implement a single class and register it. Nothing else changes.

## 1. Implement the `Provider` interface

See `src/bumpguard/providers/base.py`. The contract:

```python
class Provider(abc.ABC):
    language: str          # routing id, e.g. "dotnet"
    ecosystem: str         # human name, e.g. "NuGet"
    file_extensions: tuple # e.g. (".cs",)

    def get_installed(self, package) -> InstalledInfo | None: ...
    def list_installed(self, name_filter=None) -> list[InstalledInfo]: ...
    def get_installed_surface(self, package) -> Surface | None: ...
    def get_version_surface(self, package, version) -> Surface | None: ...
    def scan_usage(self, code, package=None) -> list[Usage]: ...
    # optional but recommended:
    def scan_imports(self, code) -> list[ImportRef]: ...
    def import_names(self, package) -> list[str]: ...
```

Everything is expressed in the neutral model from `core/models.py`
(`Surface`, `Symbol`, `Param`, `Usage`, `ImportRef`). Your provider's job is to
**populate `Surface.symbols`** (a `dotted_path -> Symbol` map) and to **parse
usages** out of source. You never need to touch the diff or analysis code.

### Surface extraction per ecosystem

The mechanism differs because each ecosystem ships its public API differently:

| Ecosystem | Package contains | Read the API surface via |
| --- | --- | --- |
| Python | source `.py` | `ast` parse (implemented) |
| .NET / NuGet | compiled `.dll` | assembly metadata (`System.Reflection.MetadataLoadContext`) |
| Java / Maven | `.jar` bytecode | `javap` / ASM |
| JS/TS / npm | `.d.ts` | TypeScript declaration parse |

Emit `Symbol`s with the same shape the Python provider uses:
- `kind`: module / class / function / method / attribute
- `params`: ordered `Param`s with `kind` (positional / keyword‑only / …) and
  `has_default`, so the shared call‑compatibility logic works automatically.

The richer your `params` metadata, the better `check_upgrade` reasons about
positional vs keyword breakage — for free.

## 2. Register it

Add it to `core/registry.py::load_default_providers()` (guard the import so a
missing toolchain never breaks the rest of BumpGuard):

```python
try:
    from ..providers.dotnet.provider import DotNetProvider
    register(DotNetProvider())
except Exception:
    pass
```

## 3. Test it

Mirror `tests/test_surface.py`, `test_diff.py`, `test_usage.py`,
`test_analyze.py` with fixtures for your ecosystem. The diff/analyze layers are
already covered by the Python fixtures, so you mostly need to test your
extractor and usage scanner.

## Notes

- `get_version_surface` should fetch a version **without installing it** into
  the live environment, and clean up after itself.
- Never execute third‑party code. Read metadata/source only.
- Mark a `Surface` `partial=True` when extraction can't be exhaustive, and add
  dynamic namespaces to `Surface.dynamic_modules` to suppress false positives.
