from tendem_mcp.client import TendemAPIError
from tendem_mcp.server import mcp


def main() -> None:
    """Entry point for the tendem-mcp CLI."""
    mcp.run()


__all__ = ['TendemAPIError', 'main', 'mcp']
