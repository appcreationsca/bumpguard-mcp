"""BumpGuard — guard your dependency bumps.

A Model Context Protocol server that tells an AI agent exactly which parts of
*your* code break when you upgrade a dependency, and verifies AI-written code
against the API that is actually installed — all by static analysis, never by
executing third-party code. Built around pluggable per-ecosystem providers
(Python, .NET, and Java ship today; npm to follow).
"""

__version__ = "0.2.0"
