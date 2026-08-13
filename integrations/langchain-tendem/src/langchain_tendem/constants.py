"""Endpoint, attribution, polling and flow constants."""

from __future__ import annotations

from importlib import metadata
from typing import Final

try:
    PACKAGE_VERSION: Final[str] = metadata.version("langchain-tendem")
except metadata.PackageNotFoundError:  # pragma: no cover - not installed
    PACKAGE_VERSION = "0.0.0.dev0"
"""The installed package version — pyproject.toml is the single source."""

LANGCHAIN_UTM_HASH: Final[str] = "9cfb868c94"
"""Attribution hash assigned to the LangChain distribution channel."""

TENDEM_MCP_URL: Final[str] = (
    "https://mcp.tendem.ai/mcp"
    f"?utm_hash={LANGCHAIN_UTM_HASH}&client_version={PACKAGE_VERSION}"
)
"""Default endpoint used by ``Tendem``, carrying channel + version attribution."""

API_KEY_ENV_VAR: Final[str] = "TENDEM_API_KEY"

SERVER_NAME: Final[str] = "tendem"

APPROVAL_TOOL_NAME: Final[str] = "approve_task"
"""The only tool that spends the user's money."""

DEFAULT_WAIT_FOR_CHANGE_SECONDS: Final[int] = 30
"""Server-side long-poll window per ``get_task`` round."""

DEFAULT_MAX_POLL_ROUNDS: Final[int] = 6
"""Round budget for ``Tendem.poll`` (~3 min at a 30s window)."""

MIN_POLL_INTERVAL_SECONDS: Final[float] = 1.0
"""Wall-clock floor per poll round — the busy-loop guard."""

DEFAULT_HTTP_TIMEOUT_SECONDS: Final[float] = 60.0

# ------------------------------------------------------------- agent tools

DEFAULT_WAIT_TIMEOUT_SECONDS: Final[float] = 24 * 3600.0
"""Per-call budget for ``wait_for_human_result`` — human work takes up to a
day; generous but bounded, and the call is idempotently re-callable."""

DEFAULT_SCOPING_TIMEOUT_SECONDS: Final[float] = 900.0
"""Per-call poll budget for check/reply (~15 min covers scoping and
estimation, observed live at 3-5 min)."""

TRANSIENT_ERROR_CODE: Final[str] = "TEMPORARILY_UNAVAILABLE"
"""The server's explicit "retry me" failure code."""

DEFAULT_TRANSIENT_RETRIES: Final[int] = 5
"""Consecutive transient failures tolerated during a long wait (a multi-hour
wait *will* see blips); the counter resets on every success."""

TRANSIENT_BACKOFF_BASE_SECONDS: Final[float] = 2.0
TRANSIENT_BACKOFF_MAX_SECONDS: Final[float] = 60.0

__all__ = [
    "API_KEY_ENV_VAR",
    "APPROVAL_TOOL_NAME",
    "DEFAULT_HTTP_TIMEOUT_SECONDS",
    "DEFAULT_MAX_POLL_ROUNDS",
    "DEFAULT_SCOPING_TIMEOUT_SECONDS",
    "DEFAULT_TRANSIENT_RETRIES",
    "DEFAULT_WAIT_FOR_CHANGE_SECONDS",
    "DEFAULT_WAIT_TIMEOUT_SECONDS",
    "LANGCHAIN_UTM_HASH",
    "MIN_POLL_INTERVAL_SECONDS",
    "PACKAGE_VERSION",
    "SERVER_NAME",
    "TENDEM_MCP_URL",
    "TRANSIENT_BACKOFF_BASE_SECONDS",
    "TRANSIENT_BACKOFF_MAX_SECONDS",
    "TRANSIENT_ERROR_CODE",
]
