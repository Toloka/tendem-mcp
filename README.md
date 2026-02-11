<!-- mcp-name: io.github.Toloka/tendem-mcp -->
# tendem-mcp

MCP server for [Tendem](https://tendem.ai/), AI + Human Agent to get tasks done.

To manage API keys, log into your Tendem account and visit https://agent.tendem.ai/tokens

## Desktop Extension (One-Click Install)

1. Download the latest `tendem-mcp.mcpb` from [Releases](https://github.com/toloka/tendem-mcp/releases)
2. Double-click the file or drag it into Claude Desktop
3. Enter your API key when prompted (get one at https://agent.tendem.ai/tokens)

### Building from source

```bash
cd mcpb
npx @anthropic-ai/mcpb pack . tendem-mcp.mcpb
```

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
