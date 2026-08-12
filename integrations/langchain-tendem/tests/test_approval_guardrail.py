"""The spend guardrail: approvals must be an explicit human decision."""

from __future__ import annotations

import pytest
from conftest import FakeSession, make_tendem
from langchain_core.messages import ToolMessage

from langchain_tendem import (
    ApprovalNotConfirmedError,
    HumanApprovalGate,
    QuoteChangedError,
)

CONTRACT = {
    "task_id": "task-1",
    "title": "Competitive teardown",
    "task_description": "Teardown of Acme's pricing page",
    "price": 120.0,
    "currency": "USD",
}


def contract_session(**overrides: object) -> FakeSession:
    contract = {**CONTRACT, **overrides}
    return FakeSession(
        responses={
            "get_contract": contract,
            "approve_task": {"task_id": "task-1", "approved": True},
        }
    )


# --------------------------------------------------------------- typed client


async def test_approve_task_refuses_without_confirmation() -> None:
    session = contract_session()
    client, _ = make_tendem(session)

    with pytest.raises(ApprovalNotConfirmedError):
        await client.approve_task("task-1")

    assert "approve_task" not in session.names()


@pytest.mark.parametrize("confirmed", [False, None, 0, "", "yes", 1, [1], object()])
async def test_approve_task_rejects_anything_but_literal_true(confirmed: object) -> None:
    """A truthy value is not consent — only the bool ``True`` is."""
    session = contract_session()
    client, _ = make_tendem(session)

    with pytest.raises(ApprovalNotConfirmedError):
        await client.approve_task("task-1", confirmed=confirmed)  # type: ignore[arg-type]

    assert "approve_task" not in session.names()


async def test_approve_task_proceeds_when_confirmed() -> None:
    session = contract_session()
    client, _ = make_tendem(session)

    outcome = await client.approve_task("task-1", confirmed=True, price=120.0)

    assert outcome.approved is True
    assert session.args_for("approve_task") == [
        {"task_id": "task-1", "name": "Competitive teardown", "price": 120.0}
    ]


async def test_approve_task_refuses_a_stale_quote() -> None:
    """A human approved 90; the server now says 120. Refuse, do not charge."""
    session = contract_session(price=120.0)
    client, _ = make_tendem(session)

    with pytest.raises(QuoteChangedError) as excinfo:
        await client.approve_task("task-1", confirmed=True, price=90.0)

    assert excinfo.value.approved_price == 90.0
    assert excinfo.value.current_price == 120.0
    assert "approve_task" not in session.names()
    # The refused grant must not linger and authorise a later call.
    assert client.approval_gate.pending() == ()


async def test_approve_task_refuses_an_unquoted_contract() -> None:
    """Scope arrives before the price. No price means nobody saw a number."""
    session = FakeSession(
        responses={
            "get_contract": {
                "task_id": "task-1",
                "title": "Teardown",
                "task_description": "Scope text",
            },
            "approve_task": {"task_id": "task-1", "approved": True},
        }
    )
    client, _ = make_tendem(session)

    with pytest.raises(ApprovalNotConfirmedError, match="not been quoted"):
        await client.approve_task("task-1", confirmed=True)

    assert "approve_task" not in session.names()
    assert client.approval_gate.pending() == ()


async def test_approve_task_takes_the_price_from_the_contract() -> None:
    """Omitting price is fine once the contract carries one."""
    session = contract_session()
    client, _ = make_tendem(session)

    outcome = await client.approve_task("task-1", confirmed=True)

    assert outcome.approved is True
    assert session.args_for("approve_task") == [
        {"task_id": "task-1", "name": "Competitive teardown", "price": 120.0}
    ]


async def test_insufficient_balance_is_an_outcome_not_an_exception() -> None:
    session = FakeSession(
        responses={
            "get_contract": CONTRACT,
            "approve_task": {
                "task_id": "task-1",
                "approved": False,
                "reason": "insufficient_balance",
                "topup_url": "https://agent.tendem.ai/topup/task-1",
            },
        }
    )
    client, _ = make_tendem(session)

    outcome = await client.approve_task("task-1", confirmed=True, price=120.0)

    assert outcome.approved is False
    assert outcome.needs_topup is True
    assert outcome.topup_url == "https://agent.tendem.ai/topup/task-1"
    assert session.names().count("approve_task") == 1


async def test_approval_is_recorded_in_the_audit_trail() -> None:
    session = contract_session()
    client, _ = make_tendem(session)

    await client.approve_task(
        "task-1", confirmed=True, price=120.0, approved_by="nbond@example.com"
    )

    history = client.approval_gate.history
    assert history[0].granted_by == "nbond@example.com"
    assert history[0].price == 120.0
    assert history[-1].consumed is True


# ------------------------------------------------------- model-driven toolset


async def test_agent_tool_cannot_approve_without_a_recorded_grant() -> None:
    """The blocked call must not reach the transport at all."""
    session = FakeSession(responses={"approve_task": {"approved": True}})
    client, _ = make_tendem(session)
    tools = {tool.name: tool for tool in await client.get_tools()}

    message = await tools["approve_task"].ainvoke(
        {
            "name": "approve_task",
            "args": {"task_id": "task-1", "price": 120.0},
            "id": "call-1",
            "type": "tool_call",
        }
    )

    assert isinstance(message, ToolMessage)
    assert message.status == "error"
    assert "REFUSED" in str(message.content)
    assert session.names() == []


async def test_agent_tool_approves_after_a_human_grant() -> None:
    session = FakeSession(
        responses={"approve_task": {"task_id": "task-1", "approved": True}}
    )
    client, _ = make_tendem(session)
    client.approval_gate.grant("task-1", confirmed=True, price=120.0)
    tools = {tool.name: tool for tool in await client.get_tools()}

    message = await tools["approve_task"].ainvoke(
        {
            "name": "approve_task",
            "args": {"task_id": "task-1", "price": 120.0},
            "id": "call-1",
            "type": "tool_call",
        }
    )

    assert message.status == "success"
    assert session.names() == ["approve_task"]


async def test_grant_is_single_use() -> None:
    session = FakeSession(
        responses={"approve_task": {"task_id": "task-1", "approved": True}}
    )
    client, _ = make_tendem(session)
    client.approval_gate.grant("task-1", confirmed=True, price=120.0)
    approve = {tool.name: tool for tool in await client.get_tools()}["approve_task"]
    call = {
        "name": "approve_task",
        "args": {"task_id": "task-1", "price": 120.0},
        "id": "call-1",
        "type": "tool_call",
    }

    first = await approve.ainvoke(call)
    second = await approve.ainvoke(call)

    assert first.status == "success"
    assert second.status == "error"
    assert session.names() == ["approve_task"]


async def test_grant_does_not_cover_a_different_task() -> None:
    session = FakeSession(responses={"approve_task": {"approved": True}})
    client, _ = make_tendem(session)
    client.approval_gate.grant("task-1", confirmed=True, price=120.0)
    approve = {tool.name: tool for tool in await client.get_tools()}["approve_task"]

    message = await approve.ainvoke(
        {
            "name": "approve_task",
            "args": {"task_id": "task-OTHER", "price": 120.0},
            "id": "call-1",
            "type": "tool_call",
        }
    )

    assert message.status == "error"
    assert session.names() == []


async def test_agent_tool_blocked_when_the_price_drifts_from_the_grant() -> None:
    session = FakeSession(responses={"approve_task": {"approved": True}})
    client, _ = make_tendem(session)
    client.approval_gate.grant("task-1", confirmed=True, price=120.0)
    approve = {tool.name: tool for tool in await client.get_tools()}["approve_task"]

    message = await approve.ainvoke(
        {
            "name": "approve_task",
            "args": {"task_id": "task-1", "price": 480.0},
            "id": "call-1",
            "type": "tool_call",
        }
    )

    assert message.status == "error"
    assert session.names() == []


async def test_non_spending_tools_pass_through_the_guard() -> None:
    session = FakeSession(
        responses={"get_task": {"task_id": "task-1", "status": "ACTING"}}
    )
    client, _ = make_tendem(session)
    get_task = {tool.name: tool for tool in await client.get_tools()}["get_task"]

    await get_task.ainvoke({"task_id": "task-1"})

    assert session.names() == ["get_task"]


# ------------------------------------------------------------ the gate itself


def test_gate_grant_requires_literal_true() -> None:
    gate = HumanApprovalGate()

    with pytest.raises(ApprovalNotConfirmedError):
        gate.grant("task-1", confirmed="yes")  # type: ignore[arg-type]

    assert gate.pending() == ()


def test_gate_grant_requires_a_price_by_default() -> None:
    """A priceless grant would authorise any amount — refuse it."""
    gate = HumanApprovalGate()

    with pytest.raises(ApprovalNotConfirmedError, match="price the human was shown"):
        gate.grant("task-1", confirmed=True)

    assert gate.pending() == ()


def test_gate_priceless_grants_are_an_explicit_opt_out() -> None:
    gate = HumanApprovalGate(require_price_match=False)
    gate.grant("task-1", confirmed=True)

    gate.consume("task-1", price=999.0)

    assert gate.history[0].price is None


def test_gate_check_does_not_spend_the_grant() -> None:
    gate = HumanApprovalGate()
    gate.grant("task-1", confirmed=True, price=10.0)

    gate.check("task-1", price=10.0)
    gate.check("task-1", price=10.0)

    assert gate.pending() == ("task-1",)


def test_gate_revoke_removes_the_grant() -> None:
    gate = HumanApprovalGate()
    gate.grant("task-1", confirmed=True, price=10.0)

    assert gate.revoke("task-1") is True
    with pytest.raises(ApprovalNotConfirmedError):
        gate.consume("task-1", price=10.0)


def test_gate_requires_a_price_when_one_was_granted() -> None:
    gate = HumanApprovalGate()
    gate.grant("task-1", confirmed=True, price=10.0)

    with pytest.raises(QuoteChangedError):
        gate.consume("task-1", price=None)


def test_gate_multi_use_mode_is_opt_in() -> None:
    gate = HumanApprovalGate(single_use=False)
    gate.grant("task-1", confirmed=True, price=10.0)

    gate.consume("task-1", price=10.0)
    gate.consume("task-1", price=10.0)

    assert gate.pending() == ("task-1",)
