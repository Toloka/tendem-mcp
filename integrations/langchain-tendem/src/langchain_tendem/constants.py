"""Endpoint, attribution and polling constants for the Tendem MCP server."""

from __future__ import annotations

from typing import Final

TENDEM_MCP_BASE_URL: Final[str] = "https://mcp.tendem.ai/mcp"
"""Bare streamable-HTTP endpoint of the hosted Tendem MCP server."""

LANGCHAIN_UTM_HASH: Final[str] = "9cfb868c94"
"""Attribution hash assigned to the LangChain distribution channel.

Every install path Tendem publishes carries its own hash so the team can see
which ecosystem a task came from. Users who wire the server up by hand through
the generic MCP adapter carry no hash at all; this package exists partly so
LangChain traffic is attributable.
"""

TENDEM_MCP_URL: Final[str] = f"{TENDEM_MCP_BASE_URL}?utm_hash={LANGCHAIN_UTM_HASH}"
"""Default endpoint used by :class:`langchain_tendem.Tendem`."""

TENDEM_TOKENS_URL: Final[str] = "https://agent.tendem.ai/tokens"
"""Where a human mints an API key for headless (non-OAuth) use."""

TENDEM_APP_URL: Final[str] = "https://agent.tendem.ai"

API_KEY_ENV_VAR: Final[str] = "TENDEM_API_KEY"

SERVER_NAME: Final[str] = "tendem"
"""Logical MCP server name; also the tool-name prefix if prefixing is enabled."""

TOOL_NAMES: Final[tuple[str, ...]] = (
    "create_task",
    "get_task",
    "get_contract",
    "approve_task",
    "cancel_task",
    "get_task_result",
    "list_tasks",
    "read_chat",
    "send_message",
    "get_account",
    "get_file_upload_url",
)
"""The 11 tools the hosted server exposes, as of the 1.0.x server release."""

APPROVAL_TOOL_NAME: Final[str] = "approve_task"
"""The only tool that spends the user's money."""

DEFAULT_WAIT_FOR_CHANGE_SECONDS: Final[int] = 30
"""Server-side long-poll window. ``get_task`` blocks up to this long."""

DEFAULT_MAX_POLL_ROUNDS: Final[int] = 6
"""Bounded number of long-poll rounds before :func:`Tendem.poll` gives up."""

MIN_POLL_INTERVAL_SECONDS: Final[float] = 1.0
"""Floor on the wall-clock cost of one poll round.

If the server answers a long-poll instantly (misconfigured proxy, older server,
``wait_for_change_seconds=0``), this floor is what stops the poll loop from
becoming a busy loop.
"""

DEFAULT_HTTP_TIMEOUT_SECONDS: Final[float] = 60.0

__all__ = [
    "API_KEY_ENV_VAR",
    "APPROVAL_TOOL_NAME",
    "DEFAULT_HTTP_TIMEOUT_SECONDS",
    "DEFAULT_MAX_POLL_ROUNDS",
    "DEFAULT_WAIT_FOR_CHANGE_SECONDS",
    "LANGCHAIN_UTM_HASH",
    "MIN_POLL_INTERVAL_SECONDS",
    "SERVER_NAME",
    "TENDEM_APP_URL",
    "TENDEM_MCP_BASE_URL",
    "TENDEM_MCP_URL",
    "TENDEM_TOKENS_URL",
    "TOOL_NAMES",
]
