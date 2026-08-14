"""The spend guardrail: approval is capped, and nothing charges above the cap.

Approval always charges the server's current quote; the only human decision
anywhere is ``max_price``.
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import FakeSession, make_tendem

from langchain_tendem import ApprovalBlockedError, PriceCeilingExceededError


def money(amount: float) -> dict[str, Any]:
    return {"amount": amount, "currency": "USD", "formatted": f"${amount:.2f}"}


def contract_session(price: float | None = 120.0) -> FakeSession:
    return FakeSession(
        responses={
            "get_contract": {
                "task_id": "task-1",
                "state": "available" if price is not None else "estimating",
                "contract": {"title": "Competitive teardown", "input_prompt": "..."},
                "price": money(price) if price is not None else None,
            },
            "approve_task": {"task_id": "task-1", "approved": True},
        }
    )


# --------------------------------------------------------------- typed client


async def test_approve_requires_a_cap() -> None:
    session = contract_session()
    client, _ = make_tendem(session)

    with pytest.raises(ApprovalBlockedError, match="spend cap is required"):
        await client.approve_task("task-1")

    assert "approve_task" not in session.names()


async def test_approve_sends_the_servers_current_quote() -> None:
    """The wire price is always the live quote, formatted by the server."""
    session = contract_session()
    client, _ = make_tendem(session)

    outcome = await client.approve_task("task-1", max_price=150.0)

    assert outcome.approved is True
    assert session.args_for("approve_task") == [
        {"task_id": "task-1", "name": "Competitive teardown", "price": "$120.00"}
    ]


async def test_approve_falls_back_to_the_client_cap() -> None:
    session = contract_session()
    client, _ = make_tendem(session, max_price=150.0)

    outcome = await client.approve_task("task-1")

    assert outcome.approved is True


async def test_over_cap_quote_is_refused_uncharged() -> None:
    session = contract_session(price=120.0)
    client, _ = make_tendem(session)

    with pytest.raises(PriceCeilingExceededError) as excinfo:
        await client.approve_task("task-1", max_price=100.0)

    assert excinfo.value.max_price == 100.0
    assert "approve_task" not in session.names()


async def test_unquoted_contract_is_refused() -> None:
    """Scope arrives before the price; no quote means nothing to approve."""
    session = contract_session(price=None)
    client, _ = make_tendem(session)

    with pytest.raises(ApprovalBlockedError, match="not been quoted"):
        await client.approve_task("task-1", max_price=100.0)

    assert "approve_task" not in session.names()


async def test_insufficient_balance_is_an_outcome_not_an_exception() -> None:
    session = contract_session()
    session.responses["approve_task"] = {
        "task_id": "task-1",
        "approved": False,
        "reason": "insufficient_balance",
        "topup_url": "https://agent.tendem.ai/topup/task-1",
    }
    client, _ = make_tendem(session)

    outcome = await client.approve_task("task-1", max_price=150.0)

    assert outcome.approved is False
    assert outcome.needs_topup is True
    assert outcome.topup_url == "https://agent.tendem.ai/topup/task-1"


# ------------------------------------------------------- model-driven toolset


def approve_call(task_id: str = "task-1", price: Any = "$120.00") -> dict[str, Any]:
    args: dict[str, Any] = {"task_id": task_id}
    if price is not None:
        args["price"] = price
    return {"name": "approve_task", "args": args, "id": "call-1", "type": "tool_call"}


async def test_get_tools_refuses_to_expose_approvals_without_a_cap() -> None:
    """Without Tendem(max_price=...), the guarded toolset cannot be built."""
    client, _ = make_tendem(FakeSession())  # no cap

    with pytest.raises(ValueError, match="spend cap"):
        await client.get_tools()


async def test_agent_tool_approves_under_the_cap() -> None:
    session = FakeSession(
        responses={"approve_task": {"task_id": "task-1", "approved": True}}
    )
    client, _ = make_tendem(session, max_price=150.0)
    approve = {tool.name: tool for tool in await client.get_tools()}["approve_task"]

    message = await approve.ainvoke(approve_call(price="$120.00"))

    assert message.status == "success"
    assert session.names() == ["approve_task"]


async def test_agent_tool_blocked_when_the_quote_exceeds_the_cap() -> None:
    session = FakeSession(responses={"approve_task": {"approved": True}})
    client, _ = make_tendem(session, max_price=100.0)
    approve = {tool.name: tool for tool in await client.get_tools()}["approve_task"]

    message = await approve.ainvoke(approve_call(price="$120.00"))

    assert message.status == "error"
    assert session.names() == []


async def test_agent_tool_blocked_without_a_price_argument() -> None:
    """No price = nothing to check against the cap = no charge."""
    session = FakeSession(responses={"approve_task": {"approved": True}})
    client, _ = make_tendem(session, max_price=150.0)
    approve = {tool.name: tool for tool in await client.get_tools()}["approve_task"]

    message = await approve.ainvoke(approve_call(price=None))

    assert message.status == "error"
    assert session.names() == []


async def test_non_spending_tools_pass_through_the_guard() -> None:
    session = FakeSession(
        responses={"get_task": {"task_id": "task-1", "status": "ACTING"}}
    )
    client, _ = make_tendem(session, max_price=1.0)
    get_task = {tool.name: tool for tool in await client.get_tools()}["get_task"]

    await get_task.ainvoke({"task_id": "task-1"})

    assert session.names() == ["get_task"]
