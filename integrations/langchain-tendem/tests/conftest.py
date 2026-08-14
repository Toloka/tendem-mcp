"""Network-free test doubles for the Tendem MCP transport.

`FakeSession` implements just enough of `mcp.ClientSession` for
`langchain_mcp_adapters` and `langchain_tendem.Tendem`: `initialize`,
`list_tools`, and `call_tool`. Nothing here opens a socket.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent
from mcp.types import Tool as MCPTool

from langchain_tendem.client import Tendem

ToolResponder = Callable[[dict[str, Any], int], Any]

#: The 11 tools the hosted server exposes (discovered via list_tools at
#: runtime; pinned here only so the fake transport can advertise them).
TOOL_NAMES = (
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


def make_tool(name: str) -> MCPTool:
    """A minimal MCP tool descriptor with a permissive object input schema."""
    return MCPTool(
        name=name,
        description=f"Tendem {name}",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": True},
    )


@dataclass
class RecordedCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class FakeSession:
    """A scripted stand-in for an initialised MCP client session.

    `responses` maps a tool name to either a payload dict, a list of payloads
    consumed one per call (the last one repeats), or a callable taking
    `(arguments, call_index)`. A payload may be a `CallToolResult` to model an
    error, otherwise it is returned as `structuredContent`.
    """

    responses: dict[str, Any] = field(default_factory=dict)
    tools: tuple[str, ...] = TOOL_NAMES
    calls: list[RecordedCall] = field(default_factory=list)
    initialized: int = 0
    max_calls: int = 200
    """Hard cap so a regression that reintroduces an unbounded loop fails the
    suite quickly instead of hanging CI."""

    async def initialize(self) -> None:
        self.initialized += 1

    async def list_tools(self, cursor: str | None = None) -> ListToolsResult:
        return ListToolsResult(tools=[make_tool(name) for name in self.tools])

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> CallToolResult:
        if len(self.calls) >= self.max_calls:
            msg = (
                f"FakeSession exceeded {self.max_calls} tool calls — the caller "
                "is looping without a bound."
            )
            raise AssertionError(msg)
        index = sum(1 for call in self.calls if call.name == name)
        self.calls.append(RecordedCall(name=name, arguments=dict(arguments or {})))

        spec = self.responses.get(name, {})
        if callable(spec):
            spec = spec(dict(arguments or {}), index)
        elif isinstance(spec, list):
            spec = spec[min(index, len(spec) - 1)]

        if isinstance(spec, CallToolResult):
            return spec
        payload = spec if isinstance(spec, dict) else {"content": spec}
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload))],
            structuredContent=payload,
        )

    def names(self) -> list[str]:
        """Tool names in call order."""
        return [call.name for call in self.calls]

    def args_for(self, name: str) -> list[dict[str, Any]]:
        """Arguments of every call to `name`, in order."""
        return [call.arguments for call in self.calls if call.name == name]


def error_result(text: str) -> CallToolResult:
    """A `CallToolResult` marked as a tool execution error."""
    return CallToolResult(
        content=[TextContent(type="text", text=text)], isError=True
    )


class FakeClock:
    """Monotonic clock advanced only by the injected sleeper."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)  # yield to the loop, as a real sleep would


def make_tendem(session: FakeSession, **kwargs: Any) -> tuple[Tendem, FakeClock]:
    """Build a `Tendem` bound to `session`, with a controllable clock."""
    clock = FakeClock()

    @asynccontextmanager
    async def factory() -> AsyncIterator[FakeSession]:
        await session.initialize()
        yield session

    client = Tendem(
        session_factory=factory,
        sleeper=clock.sleep,
        clock=clock,
        **kwargs,
    )
    return client, clock


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()
