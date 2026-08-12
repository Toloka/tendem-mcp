"""``Tendem`` — the single entry point of this package.

Two things come out of one object:

* :meth:`Tendem.get_tools` — the server's 11 tools as LangChain tools, with the
  spend guardrail wired into the call path.
* typed lifecycle helpers (:meth:`Tendem.create_task`, :meth:`Tendem.poll`,
  :meth:`Tendem.get_contract`, :meth:`Tendem.approve_task`,
  :meth:`Tendem.get_task_result`, …) that encode the create → scope → approve →
  execute → fetch path a raw MCP adapter leaves entirely up to the caller.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any

from langchain_mcp_adapters.sessions import StreamableHttpConnection, create_session
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession
from mcp.types import CallToolResult, TextContent

from langchain_tendem.approval import HumanApprovalGate, SpendGuardInterceptor
from langchain_tendem.constants import (
    API_KEY_ENV_VAR,
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    DEFAULT_MAX_POLL_ROUNDS,
    DEFAULT_WAIT_FOR_CHANGE_SECONDS,
    MIN_POLL_INTERVAL_SECONDS,
    SERVER_NAME,
    TENDEM_MCP_URL,
)
from langchain_tendem.errors import (
    ApprovalNotConfirmedError,
    PollTimeoutError,
    TendemProtocolError,
    TendemToolError,
)
from langchain_tendem.models import (
    ApprovalOutcome,
    Contract,
    NextAction,
    TaskResult,
    TaskSnapshot,
)

if TYPE_CHECKING:  # pragma: no cover
    from langchain_core.tools import BaseTool

SessionFactory = Callable[[], "AsyncIterator[ClientSession]"]
"""An async context manager factory yielding an initialised ``ClientSession``."""

PollPredicate = Callable[[TaskSnapshot], bool]

_UNTIL_DEFAULT = object()


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
        # Some servers wrap a single value under "result"; unwrap dict payloads.
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


def _next_offset(chat: dict[str, Any], current: int) -> int:
    """Best-effort read of the chat cursor to send back as ``last_seen_offset``."""
    for key in ("next_offset", "last_offset", "offset"):
        value = chat.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    messages = chat.get("messages")
    if isinstance(messages, list):
        return current + len(messages)
    return current


class Tendem:
    """Client for the hosted Tendem MCP server.

    ```python
    from langchain_tendem import Tendem

    tendem = Tendem()  # OAuth on first use; or Tendem(api_key=...) for headless
    tools = await tendem.get_tools()  # 11 LangChain tools, approvals gated
    ```

    Args:
        api_key: Tendem API key for headless use. Sent as
            ``Authorization: ApiKey <token>``. Defaults to the
            ``TENDEM_API_KEY`` environment variable; when neither is set, no
            auth header is sent and the transport performs the OAuth 2.0 flow
            on first use.
        url: Endpoint override. Defaults to
            ``https://mcp.tendem.ai/mcp?utm_hash=9cfb868c94`` — the query
            parameter is channel attribution, so preserve it if you override
            the host.
        headers: Extra HTTP headers, merged over the derived auth header.
        timeout: HTTP timeout in seconds for ordinary requests.
        sse_read_timeout: How long the transport waits for a server event.
            Must comfortably exceed ``wait_for_change_seconds``, or long-polls
            get cut off; the default is derived for you.
        approval_gate: Ledger of human spend approvals. Pass your own to share
            one gate across several clients, or to relax its defaults.
        wait_for_change_seconds: Default server-side long-poll window.
        max_poll_rounds: Default bound on :meth:`poll` rounds.
        min_poll_interval: Floor on the wall-clock duration of a poll round.
            This is what makes a busy loop structurally impossible even if the
            server returns instantly.
        server_name: Logical name for the MCP server in adapter plumbing.
        session_factory: Supply an alternative session provider. Intended for
            tests and for reusing a session you manage yourself.
        sleeper: Async sleep function, injectable for tests.
        clock: Monotonic clock, injectable for tests.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        url: str = TENDEM_MCP_URL,
        headers: dict[str, str] | None = None,
        timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        sse_read_timeout: float | None = None,
        approval_gate: HumanApprovalGate | None = None,
        wait_for_change_seconds: int = DEFAULT_WAIT_FOR_CHANGE_SECONDS,
        max_poll_rounds: int = DEFAULT_MAX_POLL_ROUNDS,
        min_poll_interval: float = MIN_POLL_INTERVAL_SECONDS,
        server_name: str = SERVER_NAME,
        session_factory: SessionFactory | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.url = url
        self.server_name = server_name
        self.approval_gate = approval_gate or HumanApprovalGate()
        self.wait_for_change_seconds = wait_for_change_seconds
        self.max_poll_rounds = max_poll_rounds
        self.min_poll_interval = min_poll_interval

        resolved_key = api_key if api_key is not None else os.getenv(API_KEY_ENV_VAR)
        merged_headers: dict[str, str] = {}
        if resolved_key:
            merged_headers["Authorization"] = f"ApiKey {resolved_key}"
        if headers:
            merged_headers.update(headers)
        self._headers = merged_headers
        self._uses_api_key = bool(resolved_key)

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
    def uses_api_key(self) -> bool:
        """``True`` when an API key is configured (headless); ``False`` = OAuth."""
        return self._uses_api_key

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
        guard_spend: bool = True,
        tool_name_prefix: bool = False,
        handle_tool_errors: bool = True,
    ) -> list[BaseTool]:
        """Load Tendem's tools as LangChain tools, with approvals gated.

        The returned tools are ordinary ``StructuredTool`` instances, so they
        drop straight into any LangChain or LangGraph agent.

        Args:
            interceptors: Extra ``ToolCallInterceptor`` objects, applied outside
                the spend guard.
            guard_spend: Leave ``True``. Setting it to ``False`` removes the
                structural block on ``approve_task``, letting a model charge the
                user without a recorded human decision — only appropriate if you
                enforce approval somewhere else in your stack.
            tool_name_prefix: Prefix tool names with the server name.
            handle_tool_errors: Return MCP execution errors to the model as
                error ``ToolMessage``s (default) instead of raising.

        Returns:
            The server's tools, ``approve_task`` included but gated.
        """
        chain: list[Any] = list(interceptors or ())
        if guard_spend:
            chain.append(SpendGuardInterceptor(self.approval_gate))

        if self._session is not None:
            return await load_mcp_tools(
                self._session,
                tool_interceptors=chain,
                server_name=self.server_name,
                tool_name_prefix=tool_name_prefix,
                handle_tool_errors=handle_tool_errors,
            )
        if self._session_factory is not None:
            async with self._new_session() as session:
                return await load_mcp_tools(
                    session,
                    tool_interceptors=chain,
                    server_name=self.server_name,
                    tool_name_prefix=tool_name_prefix,
                    handle_tool_errors=handle_tool_errors,
                )
        return await load_mcp_tools(
            None,
            connection=self.connection,
            tool_interceptors=chain,
            server_name=self.server_name,
            tool_name_prefix=tool_name_prefix,
            handle_tool_errors=handle_tool_errors,
        )

    # ------------------------------------------------------------- raw plumbing

    async def call(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call any Tendem tool and return its payload as a dict.

        Escape hatch for tools or arguments this package does not wrap yet.
        Note that it bypasses :class:`SpendGuardInterceptor`; use
        :meth:`approve_task` for anything that spends money.

        Raises:
            TendemToolError: The server reported a tool execution error.
        """
        async with self.session() as session:
            result = await session.call_tool(tool_name, arguments or {})
        if not isinstance(result, CallToolResult):  # pragma: no cover - defensive
            msg = f"Unexpected result type from {tool_name}: {type(result).__name__}"
            raise TendemProtocolError(msg)
        return _payload_from_result(tool_name, result)

    # --------------------------------------------------------------- lifecycle

    async def create_task(
        self,
        name: str,
        description: str,
        *,
        conversation_id: str | None = None,
        **extra: Any,
    ) -> TaskSnapshot:
        """Submit a task and return its first snapshot.

        Pass the user's own words as ``description``. Tendem's orchestrator asks
        better scoping questions than you can anticipate, so transmitting the
        brief faithfully beats pre-interrogating the user or synthesising a
        "complete" brief. Note that Tendem declines data-scraping work by
        policy.

        Args:
            name: Short task title.
            description: The user's own formulation of the work.
            conversation_id: Stable id correlating tasks from one conversation.
            **extra: Additional arguments passed through to the tool.
        """
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
        self, task_id: str, *, wait_for_change_seconds: int = 0
    ) -> TaskSnapshot:
        """Read a task's current state.

        Args:
            task_id: The task to read.
            wait_for_change_seconds: Server-side blocking window. ``0`` returns
                immediately; a positive value holds the request open until
                something changes. Prefer :meth:`poll`, which sets this for you
                and bounds the number of rounds.
        """
        payload = await self.call(
            "get_task",
            _drop_none(
                {
                    "task_id": task_id,
                    "wait_for_change_seconds": wait_for_change_seconds or None,
                }
            ),
        )
        return TaskSnapshot.from_payload(payload, task_id=task_id)

    async def list_tasks(self, **kwargs: Any) -> dict[str, Any]:
        """List the account's tasks — how you recover a ``task_id`` later."""
        return await self.call("list_tasks", _drop_none(dict(kwargs)))

    async def read_chat(self, task_id: str, *, from_offset: int = 0) -> dict[str, Any]:
        """Read the scoping chat from ``from_offset`` onward."""
        return await self.call(
            "read_chat", {"task_id": task_id, "from_offset": from_offset}
        )

    async def send_message(
        self, task_id: str, text: str, *, last_seen_offset: int | None = None
    ) -> dict[str, Any]:
        """Answer Tendem in the task chat.

        Pass ``last_seen_offset`` so the server can detect that your message
        crossed new content (``next_action="resolve_race"``).
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

    async def get_contract(self, task_id: str) -> Contract:
        """Fetch scope + price — what you show a human before charging them.

        ``get_task`` deliberately omits these details; this is the read-only
        companion that carries them. Scope is available before the price, so
        check :attr:`Contract.is_quoted` before presenting a number.
        """
        payload = await self.call("get_contract", {"task_id": task_id})
        return Contract.from_payload(payload, task_id=task_id)

    async def approve_task(
        self,
        task_id: str,
        *,
        confirmed: bool = False,
        price: float | None = None,
        name: str | None = None,
        verify_quote: bool = True,
        approved_by: str | None = None,
    ) -> ApprovalOutcome:
        """Approve a quote — **only** with an explicit human decision.

        This charges the user. The guardrail is structural: ``confirmed`` must
        be exactly ``True``, which no default and no ambiguous truthy value can
        satisfy. Call :meth:`get_contract` first, show the scope and price to a
        human, and pass the price they saw.

        Args:
            task_id: Task to approve.
            confirmed: Must be exactly ``True``, meaning a human said yes to
                this task at this price.
            price: The price the human approved. Recommended; with
                ``verify_quote`` it is checked against the server's current
                price so a voided quote cannot be charged silently. When
                omitted it is taken from the contract, and approval is refused
                if the contract carries no price yet.
            name: Task name to send. Defaults to the contract title.
            verify_quote: Re-read the contract and refuse on a price mismatch.
            approved_by: Optional audit identifier recorded on the grant.

        Returns:
            The outcome. ``approved=False`` with :attr:`ApprovalOutcome.needs_topup`
            means the balance was too low; hand the human ``topup_url`` — paying
            it auto-approves this task. Do not retry.

        Raises:
            ApprovalNotConfirmedError: ``confirmed`` was not exactly ``True``,
                or no price is known — an approval nobody could have seen a
                number for is not an informed decision.
            QuoteChangedError: The server's price differs from ``price``.
        """
        if confirmed is not True:
            raise ApprovalNotConfirmedError(
                task_id,
                "approve_task requires confirmed=True, recorded after a human "
                "was shown the contract scope and price",
            )

        contract: Contract | None = None
        if verify_quote or price is None or name is None:
            contract = await self.get_contract(task_id)

        if price is None and contract is not None:
            price = contract.price
        if name is None and contract is not None:
            name = contract.title

        # Scope lands before the price does. Approving an unquoted contract
        # would commit the user to a number no human has been shown, so refuse
        # rather than let "no price" mean "any price".
        if price is None:
            raise ApprovalNotConfirmedError(
                task_id,
                "no price is known for this task — the contract has not been "
                "quoted yet. Wait for next_action='await_user_approval', call "
                "get_contract, show the price to a human, and pass it as "
                "price=.",
            )

        # Route through the same ledger the agent-facing tools use, so the price
        # check and the audit trail are identical on both paths.
        self.approval_gate.grant(
            task_id,
            confirmed=True,
            price=price,
            granted_by=approved_by,
            note="Tendem.approve_task(confirmed=True)",
        )
        server_price = contract.price if contract is not None else price
        try:
            self.approval_gate.consume(task_id, price=server_price)
        except Exception:
            self.approval_gate.revoke(task_id)
            raise

        payload = await self.call(
            "approve_task",
            _drop_none({"task_id": task_id, "name": name, "price": price}),
        )
        return ApprovalOutcome.from_payload(payload, task_id=task_id)

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        """Mint a Tendem-UI cancel URL. The server does **not** cancel directly."""
        return await self.call("cancel_task", {"task_id": task_id})

    async def get_task_result(self, task_id: str) -> TaskResult:
        """Fetch the deliverable: markdown plus pre-signed file URLs."""
        payload = await self.call("get_task_result", {"task_id": task_id})
        return TaskResult.from_payload(payload, task_id=task_id)

    async def get_account(self) -> dict[str, Any]:
        """Read balance and top-up URL."""
        return await self.call("get_account", {})

    async def get_file_upload_url(self, task_id: str, **kwargs: Any) -> dict[str, Any]:
        """Get an upload URL for input files.

        Uploads are not auto-detected: after uploading, name each file in a
        :meth:`send_message` so Tendem knows it is there.
        """
        return await self.call(
            "get_file_upload_url", _drop_none({"task_id": task_id, **kwargs})
        )

    # ------------------------------------------------------------------ polling

    async def poll(
        self,
        task_id: str,
        *,
        until: Iterable[NextAction] | PollPredicate | Any = _UNTIL_DEFAULT,
        max_rounds: int | None = None,
        wait_for_change_seconds: int | None = None,
        on_round: Callable[[TaskSnapshot, int], None] | None = None,
    ) -> TaskSnapshot:
        """Long-poll a task a bounded number of rounds.

        Each round issues ``get_task(task_id, wait_for_change_seconds=N)``, which
        the server holds open until the task changes or the window elapses — so
        waiting costs one request per ~N seconds, not a spin. Two independent
        properties keep this from ever becoming a busy loop:

        1. The loop runs at most ``max_rounds`` times and then raises.
        2. A round that returns faster than ``min_poll_interval`` is padded with
           a sleep, so even a server that ignores the blocking hint cannot
           produce a tight loop.

        Args:
            task_id: Task to watch.
            until: Stop condition — an iterable of :class:`NextAction` values, or
                a predicate over the snapshot. Defaults to "anything that needs
                the caller", i.e. a question, an approval, a top-up, a race, a
                ready result, or a terminal state.
            max_rounds: Round budget. Defaults to the client's.
            wait_for_change_seconds: Blocking window per round. Defaults to the
                client's.
            on_round: Called with ``(snapshot, round_index)`` after each round —
                a hook for quiet progress logging.

        Returns:
            The first snapshot satisfying ``until``.

        Raises:
            PollTimeoutError: The budget ran out first. This is the hand-off
                signal, not a failure: attach a background watcher or tell the
                human they can check back. ``exc.snapshot`` holds the last state.
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

        predicate = self._build_predicate(until)

        snapshot: TaskSnapshot | None = None
        for index in range(rounds):
            started = self._clock()
            snapshot = await self.get_task(task_id, wait_for_change_seconds=wait)
            if on_round is not None:
                on_round(snapshot, index)
            if predicate(snapshot):
                return snapshot
            if index == rounds - 1:
                break
            await self._pace(started, snapshot)

        raise PollTimeoutError(task_id, rounds, snapshot)

    @staticmethod
    def _build_predicate(until: Any) -> PollPredicate:
        if until is _UNTIL_DEFAULT:

            def default_predicate(snapshot: TaskSnapshot) -> bool:
                return snapshot.needs_caller

            return default_predicate

        if callable(until):
            return until

        wanted = {NextAction.parse(item) or item for item in until}

        def wanted_predicate(snapshot: TaskSnapshot) -> bool:
            return (
                snapshot.is_terminal
                or snapshot.action in wanted
                or snapshot.next_action in wanted
            )

        return wanted_predicate

    async def _pace(self, round_started: float, snapshot: TaskSnapshot) -> None:
        """Ensure a poll round costs at least ``min_poll_interval`` wall-clock."""
        floor = max(self.min_poll_interval, snapshot.poll_after_seconds or 0.0)
        elapsed = self._clock() - round_started
        remaining = floor - elapsed
        if remaining > 0:
            await self._sleep(remaining)

    async def drive_scoping(
        self,
        task_id: str,
        *,
        answer: Callable[[dict[str, Any], TaskSnapshot], Awaitable[str | None]],
        max_exchanges: int = 6,
        max_rounds: int | None = None,
        wait_for_change_seconds: int | None = None,
    ) -> TaskSnapshot:
        """Run the scoping loop: poll, answer Tendem's questions, repeat.

        Bounded on both axes — at most ``max_exchanges`` question rounds, each
        containing at most ``max_rounds`` long-polls. Returns as soon as the task
        stops asking questions, which normally means a quote is ready
        (``await_user_approval``).

        Args:
            task_id: Task being scoped.
            answer: Async callback receiving ``(chat_payload, snapshot)`` and
                returning the reply text — or ``None`` to stop and let a human
                take over. Answer from conversation context when you can;
                return ``None`` when the answer is not in context, or when
                scope, deliverables, deadline, or money are in play.
            max_exchanges: Cap on question/answer rounds.
            max_rounds: Long-poll rounds per exchange.
            wait_for_change_seconds: Blocking window per long-poll.

        Returns:
            The snapshot that ended the loop. Check
            :attr:`TaskSnapshot.action`: ``await_user_approval`` means fetch the
            contract and ask a human; ``await_input`` means ``answer`` declined
            and a human must reply.

        Raises:
            PollTimeoutError: ``max_exchanges`` was exhausted while Tendem was
                still asking questions.
        """
        offset = 0
        snapshot: TaskSnapshot | None = None
        for _ in range(max_exchanges):
            snapshot = await self.poll(
                task_id,
                max_rounds=max_rounds,
                wait_for_change_seconds=wait_for_change_seconds,
            )
            action = snapshot.action
            if action not in (NextAction.AWAIT_INPUT, NextAction.RESOLVE_RACE):
                return snapshot
            chat = await self.read_chat(task_id, from_offset=offset)
            offset = _next_offset(chat, offset)
            reply = await answer(chat, snapshot)
            if reply is None:
                return snapshot
            await self.send_message(task_id, reply, last_seen_offset=offset)

        raise PollTimeoutError(task_id, max_exchanges, snapshot)


__all__ = ["Tendem"]
