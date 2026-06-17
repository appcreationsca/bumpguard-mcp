<p align="center">
  <img src="https://raw.githubusercontent.com/appcreationsca/bumpguard-mcp/main/assets/logo.png" alt="BumpGuard logo" width="120">
</p>

# BumpGuard 🛡️

<!-- mcp-name: io.github.appcreationsca/bumpguard -->

**Guard your dependency bumps.** BumpGuard is a [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server that tells your AI coding agent *exactly which lines of **your** code break* when you upgrade a dependency — and verifies AI‑written code against the API that is **actually installed**, so it stops calling functions that don't exist.

It does this by **static analysis only**. BumpGuard never imports or executes third‑party code; it reads a package's real public API straight from its source.

> Docs tell your agent what *should* exist. BumpGuard tells it what *actually* exists here.

<p align="center">
  <img src="https://raw.githubusercontent.com/appcreationsca/bumpguard-mcp/main/assets/demo.gif"
       alt="BumpGuard check_upgrade demo: out of 2,015 breaking changes in pydantic 2.0, it flags the one (BaseSettings) that hits your code, with the fix"
       width="820">
</p>

---

## Why this exists

The #1 frustration developers report with AI coding tools is code that's *"almost right, but not quite."* A huge slice of that is **API drift and hallucination**:

- The model writes `pydantic.BaseSettings` or `openai.ChatCompletion.create(...)` — perfectly valid two versions ago, **gone** in the version you have installed.
- You bump `pandas` from 1.5 to 2.2 and discover the breakage one stack trace at a time.
- A changelog lists *1,800 breaking changes*; you only care about the **three** your code actually touches.

BumpGuard closes that gap with ground truth from your environment instead of the model's memory.

---

## What it does

A real example — upgrading `pydantic` 1 → 2 in code that uses `BaseSettings`:

```jsonc
// check_upgrade(package="pydantic", to_version="2.0.3", from_version="1.10.13", code="...")
{
  "safe_to_upgrade": false,
  "summary": { "breaking": 1, "total_api_changes": 4919, "breaking_api_changes": 2015 },
  "findings": [
    {
      "symbol": "pydantic.BaseSettings",
      "line": 2,
      "severity": "breaking",
      "message": "You use 'pydantic.BaseSettings', which no longer exists in the target version...",
      "suggestion": "Consider 'pydantic.v1.env_settings.BaseSettings'"
    }
  ]
}
```

Out of **2,015** breaking API changes, BumpGuard surfaced the **one** that affects this code — with the line number and a fix hint.

---

## Tools

| Tool | What it answers |
| --- | --- |
| **`check_upgrade`** ⭐ | *"If I upgrade `package` to `to_version`, what in **this code** breaks?"* Diffs the installed (or `from_version`) API against the target and reports only the changes your code actually hits, with severity and fix hints. |
| **`diff_versions`** | *"What changed between two versions of this library?"* The raw breaking‑change list, no code scan — good for planning a migration. |
| **`verify_snippet`** | *"Do the imports and API calls in this code really exist here?"* Catches hallucinated/typo'd package names (slopsquatting) and attributes that aren't on the installed package. |
| **`check_import`** | *"Is this package installed? If not, what's the closest real name?"* |
| **`list_symbols`** | *"What's the real public API of this package?"* Discover functions/classes/methods + signatures instead of guessing — for the installed version or any fetched version. |
| **`list_languages`** | Which ecosystem providers are available. |

Every answer is grounded in evidence (installed version, source location). Because analysis is static, **"no findings" means "nothing proven to break," not a guarantee** — BumpGuard is explicit about that in its output.

---

## Install

```bash
pip install bumpguard-mcp
```

Requires Python 3.10+. The server speaks MCP over stdio.

> Install BumpGuard into the **same environment as the project you're working on**, so it sees the packages you actually have installed.

---

## Configure your MCP client

**Claude Desktop / Claude Code** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "bumpguard": {
      "command": "bumpguard-mcp"
    }
  }
}
```

**Cursor / Windsurf / VS Code (Copilot)** — point your MCP config at the `bumpguard-mcp` command (or `python -m bumpguard.server`). Any MCP‑compatible client works.

Then ask your agent things like:

- *"Before upgrading pandas to 2.2, check whether my data pipeline breaks."*
- *"Verify this snippet actually uses the installed OpenAI SDK."*
- *"List the real methods on `httpx.Client`."*

---

## How it works

```
                 ┌──────────────── language‑neutral core ────────────────┐
   MCP tools  →  │  diff engine · breaking‑change classifier · analyzer  │
                 │  (matches API changes against YOUR usage)             │
                 └───────────────────────┬──────────────────────────────┘
                                         │ Provider interface
                 ┌───────────────────────┴──────────────────────────────┐
                 │  Python provider  │  .NET (NuGet)   │  Java (planned) │
                 │  • AST surface    │  • DLL metadata │  • jar bytecode │
                 │  • usage scanner  │  • Roslyn scan  │                 │
                 │  • wheel fetch    │  • nupkg fetch  │                 │
                 └──────────────────────────────────────────────────────┘
```

1. **Extract** a package's public API surface by parsing its source with Python's `ast` — for the installed version, and for the target version (downloaded as a wheel and unpacked, **never installed or executed**).
2. **Diff** the two surfaces into removed / signature‑changed / added symbols, and classify each as breaking, potentially breaking, or info.
3. **Scan** your code (also via `ast`) for usages — resolving import aliases, re‑exports, instance‑method calls, and the keyword/positional arguments each call passes.
4. **Match** usages against changes and report a precise, per‑line verdict.

**Safety:** BumpGuard never imports third‑party code, so there are no import side effects, no hangs from heavy packages, and no arbitrary code execution. Wheel downloads are sandboxed to a temp dir, time‑bounded, and guarded against path traversal / zip bombs.

---

## Multi‑language by design

BumpGuard is built around a **pluggable provider interface**. The diff engine, breaking‑change classifier, analyzer, reporting, and MCP tools are all language‑neutral; only the *surface extraction* and *usage scanning* are ecosystem‑specific.

- ✅ **Python (PyPI)** — available now.
- ✅ **.NET (NuGet)** — available now. Reads public API from assembly metadata via reflection-only loading (no code executed); needs the **.NET SDK** (`dotnet`) on PATH. A small helper is built once on first use.
- 🔜 **Java (Maven)** — extract from `.jar` bytecode.
- 🔜 **JS/TS (npm)** — parse `.d.ts` declarations.

Adding an ecosystem means implementing one `Provider` — see [`docs/ADD_A_PROVIDER.md`](docs/ADD_A_PROVIDER.md).

### .NET specifics (v1)

- Pass `language: "dotnet"`. Example: *"Before upgrading Azure.AI.OpenAI to 2.1.0, check whether my client code breaks (from_version 1.0.0-beta.17)."*
- Supported: `check_upgrade`, `diff_versions`, `list_symbols`, `check_import`.
- **Prefer passing `from_version`** — the "installed" baseline is taken from the NuGet global cache, which isn't your project's pinned version.
- Reliable signal: **type / method / property removals and additions** (e.g. the `OpenAIClient` → `AzureOpenAIClient` rename is caught as a breaking removal with a suggestion). Parameter-level diffs run only for **unambiguous single-overload** members; overloaded members are tracked by presence (a documented v1 limit).
- Fully-qualified references are reported confidently; short names resolved via `using` are reported as **lower-confidence "potentially breaking"** to avoid false hard-breaks from namespace collisions.
- `verify_snippet` is **not supported for .NET in v1** (accurate C# hallucination detection needs semantic binding).

---

## Known limitations (v1, Python)

BumpGuard is honest about static analysis. It may **miss** (false negatives) or, rarely, **over‑flag** (false positives):

- Dynamically generated APIs (`__getattr__` modules, plugin registries, `boto3`‑style clients). BumpGuard detects `__getattr__` modules and *suppresses* confident "missing symbol" findings under them.
- Members created at runtime that aren't visible in source.
- Compiled (C/Rust) extension internals — the Python‑level surface is still read.
- Deep instance‑flow tracking is limited to direct `x = Class(...)` patterns.
- Star re‑exports (`from .x import *`) are not expanded.

Treat findings as **high‑signal guidance**, and absence of findings as "not proven unsafe," not a guarantee.

---

## Development

```bash
git clone https://github.com/appcreationsca/bumpguard-mcp
cd bumpguard-mcp
python -m venv .venv && . .venv/Scripts/activate   # Windows
pip install -e ".[dev]"
pytest
```

The test suite (42 tests) runs offline using fixture packages — no network required.

### Releasing

Releases are automated via GitHub Actions. To cut a release:

1. Bump the version in `pyproject.toml` and `src/bumpguard/__init__.py`.
2. Move the `CHANGELOG.md` "Unreleased" notes under a new version heading.
3. Commit, then tag and push:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The **Release** workflow runs the tests, builds the wheel + sdist, and publishes
to PyPI via **Trusted Publishing** (OIDC — no stored tokens). The **CI** workflow
runs the test matrix (Linux + Windows, Python 3.10/3.13) on every push and PR.

---

## License

MIT — see [LICENSE](LICENSE).
