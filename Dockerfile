# BumpGuard MCP server (stdio transport).
#
# Used by Glama and anyone who wants a containerized BumpGuard. The image builds
# the Python package and runs the MCP server over stdio.
#
# Note: the .NET (NuGet) provider is intentionally NOT bundled here (it needs the
# .NET SDK). The Python provider and all six MCP tools work; `list_languages`
# will simply report only `python` in this image. For full Python + .NET support,
# `pip install bumpguard-mcp` into an environment that has the .NET SDK.
FROM python:3.12-slim

WORKDIR /app

# Copy sources and metadata, then install the package (provides the
# `bumpguard-mcp` console entry point).
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

# The MCP server communicates over stdio.
ENTRYPOINT ["bumpguard-mcp"]
