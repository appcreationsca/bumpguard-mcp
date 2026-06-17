# BumpGuard MCP server (stdio transport).
#
# Used by Glama and anyone who wants a containerized BumpGuard. The image builds
# the Python package and runs the MCP server over stdio.
#
# Note: the .NET (NuGet) provider is intentionally NOT bundled here (it needs the
# .NET SDK). The Python and Java providers are pure Python and need no extra
# toolchain, so all six MCP tools work and `list_languages` reports both `java`
# and `python` in this image (Java fetches from Maven Central and reads bytecode
# without a JDK). For .NET support too, `pip install bumpguard-mcp` into an
# environment that also has the .NET SDK.
FROM python:3.12-slim

WORKDIR /app

# Copy sources and metadata, then install the package (provides the
# `bumpguard-mcp` console entry point).
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

# The MCP server communicates over stdio.
ENTRYPOINT ["bumpguard-mcp"]
