"""``Tendem`` — the typed client the agent tools are built on.

* Typed lifecycle helpers (``create_task`` … ``get_task_result``) with
  transient-error retry and the cap-gated ``approve_task``. The agent-facing
  surface is ``tendem_tools()`` in ``langchain_tendem.tools``.
* ``Tendem.get_tools`` — the raw MCP tools as LangChain tools, with
  ``approve_task`` capped by ``SpendGuardInterceptor``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any

from langchain_mcp_adapters.interceptors import MCPToolCallRequest, MCPToolCallResult
from langchain_mcp_adapters.sessions import StreamableHttpConnection, create_session
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession
from mcp.types import CallToolResult, TextContent

from langchain_tendem.constants import (
    API_KEY_ENV_VAR,
    APPROVAL_TOOL_NAME,
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    DEFAULT_MAX_POLL_ROUNDS,
    DEFAULT_TRANSIENT_RETRIES,
    DEFAULT_WAIT_FOR_CHANGE_SECONDS,
    MIN_POLL_INTERVAL_SECONDS,
    SERVER_NAME,
    TENDEM_MCP_URL,
    TRANSIENT_BACKOFF_BASE_SECONDS,
    TRANSIENT_BACKOFF_MAX_SECONDS,
    URL_ENV_VAR,
)
from langchain_tendem.errors import (
    ApprovalBlockedError,
    PollTimeoutError,
    PriceCeilingExceededError,
    TendemProtocolError,
    TendemToolError,
)
from langchain_tendem.models import (
    ApprovalOutcome,
    Contract,
    TaskResult,
    TaskSnapshot,
    parse_money,
)

if TYPE_CHECKING:  # pragma: no cover
    # Used in annotations only; with `from __future__ import annotations`
    # they are never evaluated at runtime, so the import can stay out of the
    # module's import cost.
    from langchain_core.tools import BaseTool

logger = logging.getLogger("langchain_tendem")

SessionFactory = Callable[[], "AsyncIterator[ClientSession]"]
"""An async context manager factory yielding an initialised ``ClientSession``."""

PollPredicate = Callable[[TaskSnapshot], bool]
"""Stop condition for ``Tendem.poll`` — return ``True`` to stop polling."""

ToolCallHandler = Callable[[MCPToolCallRequest], Awaitable[MCPToolCallResult]]


def _transport_exceptions() -> tuple[type[BaseException], ...]:
    """Exception types worth retrying; imported defensively (transitive deps)."""
    types: list[type[BaseException]] = [ConnectionError, TimeoutError, OSError]
    try:
        import httpx

        types.append(httpx.HTTPError)
    except ImportError:  # pragma: no cover
        pass
    try:
        import anyio

        types.extend([anyio.BrokenResourceError, anyio.ClosedResourceError])
    except ImportError:  # pragma: no cover
        pass
    try:
        from mcp.shared.exceptions import McpError

        types.append(McpError)
    except ImportError:  # pragma: no cover
        pass
    return tuple(types)


_TRANSIENT_EXCEPTIONS = _transport_exceptions()


def _is_transient(exc: BaseException) -> bool:
    """Is this a temporary, self-healing failure worth retrying?

    Transient = the call may well succeed if simply repeated: a dropped
    connection, a timeout, or the server's own ``TEMPORARILY_UNAVAILABLE``
    code. Anything else (bad request, unknown task, auth) is permanent and
    must surface immediately.
    """
    if isinstance(exc, TendemToolError):
        return exc.transient
    return isinstance(exc, _TRANSIENT_EXCEPTIONS)


def _payload_from_result(tool_name: str, result: CallToolResult) -> dict[str, Any]:
    """Turn a ``CallToolResult`` into a dict, raising on tool errors."""
    text_parts = [
        block.text for block in result.content if isinstance(block, TextContent)
    ]
    joined = "\n".join(text_parts).strip()

    if result.isError:
        raise TendemToolError(tool_name, joined or "unknown error")

    if isinstance(result.structuredContent, dict):
        structured = result.structuredContent
        inner = structured.get("result")
        if set(structured) == {"result"} and isinstance(inner, dict):
            return inner
        return structured

    if not joined:
        return {}
    try:
        decoded = json.loads(joined)
    except json.JSONDecodeError:
        return {"content": joined}
    if isinstance(decoded, dict):
        return decoded
    return {"content": decoded}


def _drop_none(mapping: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in mapping.items() if value is not None}


def _matches_tool(request_name: str, tool_name: str) -> bool:
    """Match a tool name allowing for adapter server-name prefixing."""
    return request_name == tool_name or request_name.endswith(f"_{tool_name}")


class SpendGuardInterceptor:
    """MCP tool-call interceptor capping model-issued ``approve_task`` calls.

    ``approve_task`` charges the user, so a model call goes through only when
    the quoted price it carries fits under ``max_price``. Blocked calls never
    touch the network; the model gets an error ``ToolMessage`` explaining the
    refusal — the way forward is a cheaper quote (narrow the task scope), not
    a retry.
    """

    def __init__(
        self,
        max_price: float,
        *,
        approval_tool: str = APPROVAL_TOOL_NAME,
    ) -> None:
        if max_price <= 0:
            msg = "max_price must be positive — it is the spend cap"
            raise ValueError(msg)
        self.max_price = max_price
        self.approval_tool = approval_tool

    async def __call__(
        self,
        request: MCPToolCallRequest,
        handler: ToolCallHandler,
    ) -> MCPToolCallResult:
        """Gate ``approve_task``; pass everything else straight through."""
        if not _matches_tool(request.name, self.approval_tool):
            return await handler(request)

        price = (request.args or {}).get("price")
        amount, _, _ = parse_money(price)
        if amount is None:
            reason = (
                "approve_task must carry the quoted price so it can be "
                f"checked against the cap; got {price!r}."
            )
        elif amount > self.max_price:
            reason = (
                f"the quoted price {price!r} exceeds the "
                f"${self.max_price:.2f} cap."
            )
        else:
            logger.info(
                "tendem: model approval at %r within cap $%.2f", price, self.max_price
            )
            return await handler(request)

        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=(
                        f"REFUSED — this call was blocked before reaching "
                        f"Tendem. {reason}\n\napprove_task spends real money "
                        "and goes through only when the quoted price fits "
                        "under the configured cap. Do not retry as-is: either "
                        "negotiate a narrower scope in the task chat so a "
                        "cheaper quote arrives, or report to your user that "
                        "the task exceeds the budget."
                    ),
                )
            ],
            isError=True,
        )


class Tendem:
    """Client for the hosted Tendem MCP server.

    ```python
    tendem = Tendem(max_price=25.0)  # reads TENDEM_API_KEY
    tools = tendem_tools(client=tendem, max_price=25.0)  # the agent surface
    ```

    Args:
        api_key: Sent as ``Authorization: ApiKey <token>``; defaults to the
            ``TENDEM_API_KEY`` env var. Required — the hosted server rejects
            unauthenticated calls, so a missing key fails here, at
            construction, rather than at the first call.
        url: Endpoint override; falls back to the ``TENDEM_MCP_URL``
            environment variable, then the hosted default. The default
            carries the LangChain attribution hash and package version —
            preserve the query params if you change hosts.
        headers: Extra HTTP headers, merged over the auth header.
        timeout: HTTP timeout in seconds.
        sse_read_timeout: Transport read timeout; derived to safely exceed
            the long-poll window when omitted.
        max_price: Default spend cap in USD — the one human decision this
            package needs. ``approve_task`` approves quotes at or below it;
            above it (and for model calls via ``get_tools``) approval is
            refused with nothing charged.
        wait_for_change_seconds: Server-side long-poll window per round.
        max_poll_rounds: Default round budget for ``poll``.
        min_poll_interval: Wall-clock floor per poll round — what makes a
            busy loop impossible even against a misbehaving server.
        transient_retries: Consecutive transport failures tolerated by
            retrying reads; resets on success.
        server_name: Logical MCP server name in adapter plumbing.
        session_factory: Alternative session provider (tests). When given,
            no API key is required — the factory owns the transport.
        sleeper / clock: Injectable timing (tests).

    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        sse_read_timeout: float | None = None,
        max_price: float | None = None,
        wait_for_change_seconds: int = DEFAULT_WAIT_FOR_CHANGE_SECONDS,
        max_poll_rounds: int = DEFAULT_MAX_POLL_ROUNDS,
        min_poll_interval: float = MIN_POLL_INTERVAL_SECONDS,
        transient_retries: int = DEFAULT_TRANSIENT_RETRIES,
        server_name: str = SERVER_NAME,
        session_factory: SessionFactory | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.url = url or os.getenv(URL_ENV_VAR) or TENDEM_MCP_URL
        self.server_name = server_name
        self.max_price = max_price
        self.wait_for_change_seconds = wait_for_change_seconds
        self.max_poll_rounds = max_poll_rounds
        self.min_poll_interval = min_poll_interval
        self.transient_retries = transient_retries

        resolved_key = api_key if api_key is not None else os.getenv(API_KEY_ENV_VAR)
        if not resolved_key and session_factory is None:
            msg = (
                "a Tendem API key is required: pass api_key= or set the "
                f"{API_KEY_ENV_VAR} environment variable. Create one at "
                "agent.tendem.ai/mcp, 'Agent builders' tab."
            )
            raise ValueError(msg)
        merged_headers: dict[str, str] = {}
        if resolved_key:
            merged_headers["Authorization"] = f"ApiKey {resolved_key}"
        if headers:
            merged_headers.update(headers)
        self._headers = merged_headers

        # A long-poll of N seconds needs a read timeout above N, with headroom.
        self._sse_read_timeout = (
            sse_read_timeout
            if sse_read_timeout is not None
            else max(300.0, float(wait_for_change_seconds) * 3 + 30.0)
        )
        self._timeout = timeout

        self._session_factory = session_factory
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None
        self._sleep = sleeper or asyncio.sleep
        self._clock = clock or time.monotonic

    # ------------------------------------------------------------------ config

    @property
    def connection(self) -> StreamableHttpConnection:
        """The ``langchain-mcp-adapters`` connection config this client uses."""
        config: StreamableHttpConnection = {
            "transport": "streamable_http",
            "url": self.url,
            "timeout": self._timeout,
            "sse_read_timeout": self._sse_read_timeout,
        }
        if self._headers:
            config["headers"] = dict(self._headers)
        return config

    # ----------------------------------------------------------------- session

    async def __aenter__(self) -> Tendem:
        """Open one MCP session reused by every call inside the block."""
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            self._session = await stack.enter_async_context(self._new_session())
        except BaseException:
            await stack.aclose()
            raise
        self._stack = stack
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        """Close the shared session."""
        stack, self._stack, self._session = self._stack, None, None
        if stack is not None:
            await stack.aclose()

    @asynccontextmanager
    async def _new_session(self) -> AsyncIterator[ClientSession]:
        if self._session_factory is not None:
            async with self._session_factory() as session:  # type: ignore[attr-defined]
                yield session
            return
        async with create_session(self.connection) as session:
            await session.initialize()
            yield session

    @asynccontextmanager
    async def session(self) -> AsyncIterator[ClientSession]:
        """Yield an initialised session — the shared one if inside ``async with``."""
        if self._session is not None:
            yield self._session
            return
        async with self._new_session() as session:
            yield session

    # ------------------------------------------------------------------- tools

    async def get_tools(
        self,
        *,
        interceptors: Sequence[Any] | None = None,
        tool_name_prefix: bool = False,
        handle_tool_errors: bool = True,
    ) -> list[BaseTool]:
        """Load the raw MCP tools as LangChain tools, ``approve_task`` capped.

        The spend guard is always on: model-issued approvals go through only
        up to the client's ``max_price``, which is therefore required here.
        """
        if self.max_price is None:
            msg = (
                "a spend cap is required to expose approve_task to a model: "
                "construct Tendem(max_price=...)"
            )
            raise ValueError(msg)
        chain: list[Any] = [
            *(interceptors or ()),
            SpendGuardInterceptor(self.max_price),
        ]

        if self._session is not None or self._session_factory is not None:
            async with self.session() as session:
                return await load_mcp_tools(
                    session,
                    tool_interceptors=chain,
                    server_name=self.server_name,
                    tool_name_prefix=tool_name_prefix,
                    handle_tool_errors=handle_tool_errors,
                )
        # No live session: hand the adapter the connection config instead, so
        # each tool invocation opens (and closes) its own session — the
        # returned tools then outlive any single connection.
        return await load_mcp_tools(
            None,
            connection=self.connection,
            tool_interceptors=chain,
            server_name=self.server_name,
            tool_name_prefix=tool_name_prefix,
            handle_tool_errors=handle_tool_errors,
        )

    # ------------------------------------------------------------- raw plumbing

    async def call(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        retry_transient: bool = False,
    ) -> dict[str, Any]:
        """Call any Tendem tool and return its payload as a dict.

        Escape hatch; bypasses the spend guard — use ``approve_task`` for
        anything that spends money. ``retry_transient`` retries transport
        failures and ``TEMPORARILY_UNAVAILABLE`` with capped exponential
        backoff; pass ``True`` only for **reads**, since retrying a write can
        duplicate a message or a charge.
        """
        attempts_left = self.transient_retries if retry_transient else 0
        delay = TRANSIENT_BACKOFF_BASE_SECONDS
        while True:
            try:
                return await self._call_once(tool_name, arguments)
            except Exception as exc:
                if attempts_left <= 0 or not _is_transient(exc):
                    raise
                attempts_left -= 1
                await self._sleep(delay)
                delay = min(delay * 2, TRANSIENT_BACKOFF_MAX_SECONDS)

    async def _call_once(
        self, tool_name: str, arguments: dict[str, Any] | None
    ) -> dict[str, Any]:
        async with self.session() as session:
            result = await session.call_tool(tool_name, arguments or {})
        if not isinstance(result, CallToolResult):  # pragma: no cover - defensive
            msg = f"Unexpected result type from {tool_name}: {type(result).__name__}"
            raise TendemProtocolError(msg)
        return _payload_from_result(tool_name, result)

    # --------------------------------------------------------------- lifecycle
    # Typed wrappers over the MCP tools the task flow actually uses: they
    # parse payloads into models and thread the transient-retry plumbing.

    async def create_task(
        self,
        name: str,
        description: str,
        *,
        conversation_id: str | None = None,
        **extra: Any,
    ) -> TaskSnapshot:
        """Submit a task. Pass the brief faithfully; Tendem drives scoping."""
        payload = await self.call(
            "create_task",
            _drop_none(
                {
                    "name": name,
                    "description": description,
                    "conversation_id": conversation_id,
                    **extra,
                }
            ),
        )
        return TaskSnapshot.from_payload(payload)

    async def get_task(
        self,
        task_id: str,
        *,
        wait_for_change_seconds: int = 0,
        retry_transient: bool = False,
    ) -> TaskSnapshot:
        """Read a task's state. A positive window makes the server hold the
        request open until something changes (the long-poll primitive).
        """
        payload = await self.call(
            "get_task",
            _drop_none(
                {
                    "task_id": task_id,
                    "wait_for_change_seconds": wait_for_change_seconds or None,
                }
            ),
            retry_transient=retry_transient,
        )
        return TaskSnapshot.from_payload(payload, task_id=task_id)

    async def read_chat(
        self,
        task_id: str,
        *,
        from_offset: int = 0,
        retry_transient: bool = False,
    ) -> dict[str, Any]:
        """Read the scoping chat from ``from_offset`` onward."""
        return await self.call(
            "read_chat",
            {"task_id": task_id, "from_offset": from_offset},
            retry_transient=retry_transient,
        )

    async def send_message(
        self, task_id: str, text: str, *, last_seen_offset: int | None = None
    ) -> dict[str, Any]:
        """Answer Tendem in the task chat; ``last_seen_offset`` enables race
        detection (``response_type="race"``).
        """
        return await self.call(
            "send_message",
            _drop_none(
                {
                    "task_id": task_id,
                    "text": text,
                    "last_seen_offset": last_seen_offset,
                }
            ),
        )

    async def get_contract(
        self, task_id: str, *, retry_transient: bool = False
    ) -> Contract:
        """Fetch scope + price. Scope lands before the price — check
        ``Contract.is_quoted`` before presenting a number.
        """
        payload = await self.call(
            "get_contract", {"task_id": task_id}, retry_transient=retry_transient
        )
        return Contract.from_payload(payload, task_id=task_id)

    async def approve_task(
        self,
        task_id: str,
        *,
        max_price: float | None = None,
        name: str | None = None,
    ) -> ApprovalOutcome:
        """Approve the current quote — this charges the user, so it is cap-gated.

        Approval always charges the server's *current* quote; the decision you
        supply is only the cap. The live contract is read, and the quote is
        approved iff it is at or below ``max_price`` (falls back to the
        client's) — above it, ``PriceCeilingExceededError`` is raised with
        nothing charged; an unquoted contract raises ``ApprovalBlockedError``.
        ``approved=False`` with ``needs_topup`` means the balance was short —
        hand over ``topup_url`` (paying it auto-approves this task); do not
        retry.
        """
        cap = max_price if max_price is not None else self.max_price
        if cap is None:
            raise ApprovalBlockedError(
                task_id,
                "a spend cap is required: pass max_price= or construct "
                "Tendem(max_price=...).",
            )
        contract = await self.get_contract(task_id)
        if not contract.is_quoted:
            raise ApprovalBlockedError(
                task_id,
                "the contract has not been quoted yet. Wait for "
                "next_action='await_user_approval', then retry.",
            )
        if contract.price is not None and contract.price > cap:
            raise PriceCeilingExceededError(task_id, cap, contract.display_price)

        logger.info(
            "tendem: approving task %s at %s (cap $%.2f)",
            task_id,
            contract.display_price,
            cap,
        )
        payload = await self.call(
            "approve_task",
            _drop_none(
                {
                    "task_id": task_id,
                    "name": name or contract.title,
                    # The server takes the price as its own formatted string.
                    "price": contract.display_price,
                }
            ),
        )
        return ApprovalOutcome.from_payload(payload, task_id=task_id)

    async def get_task_result(
        self, task_id: str, *, retry_transient: bool = False
    ) -> TaskResult:
        """Fetch the deliverable: markdown plus pre-signed file URLs."""
        payload = await self.call(
            "get_task_result", {"task_id": task_id}, retry_transient=retry_transient
        )
        return TaskResult.from_payload(payload, task_id=task_id)

    async def get_file_upload_url(self, task_id: str) -> dict[str, Any]:
        """Mint a folder-level pre-signed upload URL for task input files.

        Prefer ``runner.upload_files``, which handles the Azure mechanics and
        the required naming message for you.
        """
        return await self.call("get_file_upload_url", {"task_id": task_id})

    # ------------------------------------------------------------------ polling

    async def poll(
        self,
        task_id: str,
        until: PollPredicate,
        *,
        max_rounds: int | None = None,
        wait_for_change_seconds: int | None = None,
        on_round: Callable[[TaskSnapshot, int], None] | None = None,
    ) -> TaskSnapshot:
        """Long-poll a task until ``until(snapshot)`` is true, bounded.

        Each round is one server-held ``get_task`` request, retried through
        transient errors and padded to ``min_poll_interval``, so a busy loop
        is not expressible. Raises ``PollTimeoutError`` when ``max_rounds``
        (default 6, ~3 min) runs out — not a task failure; ``exc.snapshot``
        holds the last state, and polling can simply resume with the same
        ``task_id``.
        """
        rounds = self.max_poll_rounds if max_rounds is None else max_rounds
        if rounds < 1:
            msg = "max_rounds must be at least 1"
            raise ValueError(msg)
        wait = (
            self.wait_for_change_seconds
            if wait_for_change_seconds is None
            else wait_for_change_seconds
        )

        snapshot: TaskSnapshot | None = None
        for index in range(rounds):
            started = self._clock()
            snapshot = await self.get_task(
                task_id, wait_for_change_seconds=wait, retry_transient=True
            )
            if on_round is not None:
                on_round(snapshot, index)
            if until(snapshot):
                return snapshot
            if index < rounds - 1:
                await self._pace(started, snapshot)

        raise PollTimeoutError(task_id, rounds, snapshot)

    async def _pace(self, round_started: float, snapshot: TaskSnapshot) -> None:
        """Pad a round to at least ``min_poll_interval`` (or the server's
        ``poll_after_seconds``) of wall-clock.
        """
        floor = max(self.min_poll_interval, snapshot.poll_after_seconds or 0.0)
        remaining = floor - (self._clock() - round_started)
        if remaining > 0:
            await self._sleep(remaining)


__all__ = ["PollPredicate", "SpendGuardInterceptor", "Tendem"]
