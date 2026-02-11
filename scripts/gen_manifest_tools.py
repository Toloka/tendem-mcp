"""Sync manifest tools from registered MCP server tools."""

import asyncio
import json
from pathlib import Path

from tendem_mcp.server import mcp


async def main():
    manifest_path = Path(__file__).parent.parent / 'mcpb' / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    tools = await mcp.get_tools()
    manifest['tools'] = [
        {'name': t.name, 'description': t.description or ''} for t in tools.values()
    ]
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')


asyncio.run(main())
