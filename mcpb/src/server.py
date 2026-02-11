#!/usr/bin/env python3
"""Tendem MCP Desktop Extension entry point.

Imports and runs the tendem-mcp server from PyPI.
TENDEM_API_KEY is provided by Claude Desktop via user_config.
"""

from tendem_mcp import main

if __name__ == '__main__':
    main()
