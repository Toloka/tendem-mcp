# tendem-mcp

MCP server for [Tendem](https://tendem.ai/) - a platform for human-in-the-loop data tasks.

## Quickstart

### Claude Code

```bash
claude mcp add tendem -e TENDEM_API_KEY=<your-api-key> -- uvx tendem-mcp
```

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "tendem": {
      "command": "uvx",
      "args": ["tendem-mcp"],
      "env": {
        "TENDEM_API_KEY": "<your-api-key>"
      }
    }
  }
}
```

Config location:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

### OpenAI Codex

```bash
codex mcp add tendem --env TENDEM_API_KEY=<your-api-key> -- uvx tendem-mcp
```

### OpenCode

Add to `opencode.json`:

```json
{
  "mcp": {
    "tendem": {
      "type": "local",
      "command": ["uvx", "tendem-mcp"],
      "environment": {
        "TENDEM_API_KEY": "<your-api-key>"
      }
    }
  }
}
```
