"""Endpoint, auth, tool loading, and payload parsing."""

from __future__ import annotations

import pytest
from conftest import TOOL_NAMES, FakeSession, error_result, make_tendem

from langchain_tendem import (
    LANGCHAIN_UTM_HASH,
    TENDEM_MCP_URL,
    Contract,
    Tendem,
    TendemToolError,
)
from langchain_tendem.constants import API_KEY_ENV_VAR, PACKAGE_VERSION

# ------------------------------------------------------------ attribution


def test_default_endpoint_carries_attribution_and_version() -> None:
    assert (
        "https://mcp.tendem.ai/mcp"
        f"?utm_hash={LANGCHAIN_UTM_HASH}&client_version={PACKAGE_VERSION}"
    ) == TENDEM_MCP_URL
    assert LANGCHAIN_UTM_HASH == "9cfb868c94"
    assert Tendem(api_key="k").connection["url"] == TENDEM_MCP_URL


def test_endpoint_is_overridable() -> None:
    client = Tendem(api_key="k", url="http://localhost:8931/mcp")
    assert client.connection["url"] == "http://localhost:8931/mcp"


def test_connection_is_streamable_http() -> None:
    assert Tendem(api_key="k").connection["transport"] == "streamable_http"


# -------------------------------------------------------------------- auth


def test_api_key_becomes_an_apikey_authorization_header() -> None:
    client = Tendem(api_key="tk_live_123")

    assert client.connection["headers"]["Authorization"] == "ApiKey tk_live_123"


def test_api_key_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV_VAR, "tk_env_456")

    assert Tendem().connection["headers"]["Authorization"] == "ApiKey tk_env_456"


def test_a_missing_api_key_fails_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The server rejects unauthenticated calls, so fail fast and clearly."""
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)

    with pytest.raises(ValueError, match=API_KEY_ENV_VAR):
        Tendem()


def test_extra_headers_are_merged() -> None:
    client = Tendem(api_key="k", headers={"X-Trace": "abc"})

    assert client.connection["headers"] == {
        "Authorization": "ApiKey k",
        "X-Trace": "abc",
    }


def test_read_timeout_outlives_the_long_poll_window() -> None:
    """A 30s server-side block needs a read timeout well above 30s."""
    client = Tendem(api_key="k", wait_for_change_seconds=30)

    assert client.connection["sse_read_timeout"] > 30


# ------------------------------------------------------------------- tools


async def test_get_tools_exposes_the_servers_tools(session: FakeSession) -> None:
    client, _ = make_tendem(session, max_price=25.0)

    tools = await client.get_tools()

    assert [tool.name for tool in tools] == list(TOOL_NAMES)
    assert len(tools) == 11


async def test_tool_names_can_be_prefixed(session: FakeSession) -> None:
    client, _ = make_tendem(session, max_price=25.0)

    tools = await client.get_tools(tool_name_prefix=True)

    assert tools[0].name == "tendem_create_task"


async def test_get_tools_requires_a_spend_cap(session: FakeSession) -> None:
    """The guard is always on, so exposing approve_task needs the cap."""
    client, _ = make_tendem(session)

    with pytest.raises(ValueError, match="spend cap"):
        await client.get_tools()


# -------------------------------------------------------------- lifecycle


async def test_create_task_passes_the_brief_through() -> None:
    session = FakeSession(
        responses={
            "create_task": {
                "task_id": "task-9",
                "status": "ACTING",
                "next_action": "awaiting_tendem_work",
                "guidance": "Poll with wait_for_change_seconds=30.",
            }
        }
    )
    client, _ = make_tendem(session)

    snapshot = await client.create_task(
        "Pricing teardown",
        "Write a competitive teardown of Acme's pricing page.",
        conversation_id="conv-1",
    )

    assert snapshot.task_id == "task-9"
    assert snapshot.action == "awaiting_tendem_work"
    assert snapshot.guidance is not None
    assert session.args_for("create_task") == [
        {
            "name": "Pricing teardown",
            "description": "Write a competitive teardown of Acme's pricing page.",
            "conversation_id": "conv-1",
        }
    ]


async def test_get_contract_summary_flags_an_unquoted_scope() -> None:
    session = FakeSession(
        responses={
            "get_contract": {
                "task_id": "task-1",
                "state": "estimating",
                "contract": {"title": "Teardown", "input_prompt": "Scope text"},
                "price": None,
            }
        }
    )
    client, _ = make_tendem(session)

    contract = await client.get_contract("task-1")

    assert isinstance(contract, Contract)
    assert contract.is_quoted is False
    assert "do not approve" in contract.summary()


async def test_get_task_result_parses_content_and_files() -> None:
    session = FakeSession(
        responses={
            "get_task_result": {
                "task_id": "task-1",
                "content": "# Teardown\n\nFindings...",
                "files": [
                    {
                        "name": "teardown.pdf",
                        "download_url": "https://files.example/teardown.pdf",
                        "content_type": "application/pdf",
                        "size_bytes": "2048",
                    }
                ],
            }
        }
    )
    client, _ = make_tendem(session)

    result = await client.get_task_result("task-1")

    assert result.content.startswith("# Teardown")
    assert len(result.files) == 1
    assert result.files[0].name == "teardown.pdf"
    assert result.files[0].size_bytes == 2048


async def test_tool_errors_raise_tendem_tool_error() -> None:
    session = FakeSession(responses={"get_task": error_result("task not found")})
    client, _ = make_tendem(session)

    with pytest.raises(TendemToolError, match="task not found"):
        await client.get_task("nope")


async def test_payload_falls_back_to_json_in_text_content() -> None:
    """Servers without structuredContent still parse: JSON in a text block."""
    from mcp.types import CallToolResult, TextContent

    session = FakeSession(
        responses={
            "get_task": CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text='{"task_id": "task-1", "next_action": "fetch_result"}',
                    )
                ]
            )
        }
    )
    client, _ = make_tendem(session)

    snapshot = await client.get_task("task-1")

    assert snapshot.result_ready is True


async def test_shared_session_is_reused_inside_a_context_block() -> None:
    session = FakeSession(
        responses={"get_task": {"task_id": "t1", "status": "ACTING"}}
    )

    client, _ = make_tendem(session)
    async with client as tendem:
        await tendem.get_task("t1")
        await tendem.get_task("t1")

    assert session.initialized == 1
    assert session.names() == ["get_task", "get_task"]
