# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-06-15

### Added
- **Java (Maven) provider** — a third shipping ecosystem. Reads a package's
  public API **directly from compiled `.jar` bytecode** (constant pool, access
  flags, field/method descriptors, `InnerClasses`/`MethodParameters`) in **pure
  Python** — no JDK or Maven toolchain required, and no third-party code is ever
  executed. Includes a pure-Python Java source usage scanner (imports, `new`,
  method calls, argument arity) with `import`-based candidate resolution, target
  jar fetching from Maven Central (sandboxed, size-capped, time-bounded), a
  local `~/.m2` baseline, and a faithful port of Maven's `ComparableVersion`
  ordering. Packages are identified by their `group:artifact` coordinate.
- Java v1 reliably detects type/method/field/constructor removals, additions,
  and arity changes; fully-qualified references hard-break while import-resolved
  short names are reported as lower-confidence "potentially breaking" to avoid
  false hard-breaks from namespace collisions. `verify_snippet` is gated off for
  Java (accurate hallucination detection needs semantic binding), as with .NET.

### Fixed
- `check_upgrade` no longer appends a misleading "No usages detected" note when
  a usage actually resolves to a real symbol in the package's API surface. The
  used-detection is now surface-membership aware, which is essential for
  ecosystems whose distribution coordinate differs from the symbol namespace
  (e.g. Java's `group:artifact` vs. the actual Java package).

## [0.1.3] - 2026-06-17

### Added
- Project logo (`assets/logo.svg` + `assets/logo.png`, with a reproducible
  generator) shown at the top of the README and used by directory listings.

## [0.1.2] - 2026-06-15

### Added
- Animated `check_upgrade` demo GIF in the README (shown on GitHub and PyPI).
- `Dockerfile`, `.dockerignore`, and `glama.json` so the server can run in a
  container and pass the Glama directory's listing checks.

## [0.1.1] - 2026-06-15

### Added
- Published to the **Official MCP Registry** (`io.github.appcreationsca/bumpguard`)
  via a `server.json` manifest and an automated OIDC publish step in the release
  workflow.

## [0.1.0] - 2026-06-15

### Added
- Initial release of the **BumpGuard** MCP server.
- Language-neutral core: API-surface diffing, breaking-change classification,
  and usage→change impact analysis behind a pluggable provider interface.
- **Python (PyPI) provider** — AST-only public API extraction (re-exports,
  properties, instance attributes, callable classes, dynamic-module detection),
  a usage scanner (import aliases, instance tracking, call kwargs/positional),
  and target-version fetching via wheel download (no install/execution).
- **.NET (NuGet) provider** — public API extraction via reflection-only metadata
  (`System.Reflection.MetadataLoadContext`), C# usage scanning via Roslyn,
  `.nupkg` target-version fetching, and `using`-based candidate resolution with
  confidence levels. v1 reliably detects type/method/property removals and
  additions; parameter-level diffs run for unambiguous single-overload members.
- Six MCP tools: `check_upgrade`, `diff_versions`, `verify_snippet`,
  `check_import`, `list_symbols`, `list_languages`.

[Unreleased]: https://github.com/appcreationsca/bumpguard-mcp/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/appcreationsca/bumpguard-mcp/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/appcreationsca/bumpguard-mcp/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/appcreationsca/bumpguard-mcp/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/appcreationsca/bumpguard-mcp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/appcreationsca/bumpguard-mcp/releases/tag/v0.1.0
