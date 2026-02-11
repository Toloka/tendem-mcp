# MCPB Desktop Extension

Package for publishing tendem-mcp to [MCP Registry](https://registry.anthropic.com/) via the `.mcpb` format.

## What is this

MCPB (MCP Bundle) is a desktop extension format for Claude Desktop. This package wraps `tendem-mcp` from PyPI into an extension that users can install through the Registry.

## Structure

```
mcpb/
├── manifest.json    # Metadata, server config, tool list
├── pyproject.toml   # Dependency on tendem-mcp from PyPI
├── src/server.py    # Entry point — imports and runs tendem-mcp
└── .mcpbignore      # Files excluded from the bundle
```

- `manifest.json` — describes the extension: name, description, tools, `user_config` (API key), compatibility.
- `pyproject.toml` — single dependency: `tendem-mcp>=0.1.5`.
- `src/server.py` — entry point that Claude Desktop runs via `uv run`.

## How it builds

Build runs automatically on GitHub Release (`.github/workflows/mcpb-release.yml`):

1. Version from git tag (`v0.1.0` → `0.1.0`) is set in `manifest.json` and `pyproject.toml`
2. `scripts/gen_manifest_tools.py` syncs the tool list from server code into `manifest.json`
3. `npx @anthropic-ai/mcpb pack .` builds the `.mcpb` file
4. `tendem-mcp.mcpb` is uploaded to the release

## Tool sync

The tools list in `manifest.json` is auto-generated from registered tools in `tendem_mcp.server`:

```bash
uv run python scripts/gen_manifest_tools.py
```

This ensures manifest tool descriptions always match the code.

## Local build

```bash
cd mcpb
npx @anthropic-ai/mcpb pack . tendem-mcp.mcpb
```

Output: `tendem-mcp.mcpb`.
