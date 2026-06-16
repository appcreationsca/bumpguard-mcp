# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/appcreationsca/bumpguard-mcp/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/appcreationsca/bumpguard-mcp/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/appcreationsca/bumpguard-mcp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/appcreationsca/bumpguard-mcp/releases/tag/v0.1.0
