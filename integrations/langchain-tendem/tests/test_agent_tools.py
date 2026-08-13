"""The split tool set: create / check / reply / wait.

``create_human_task`` is the only non-idempotent call, so it must be thin and
deterministic (no polling). Everything else is stateless against the task_id
and safe to re-call after a crash or a checkpoint replay.
"""

from __future__ import annotations

from typing import Any

from conftest import FakeSession, make_tendem

from langchain_tendem import tendem_tools


def money(amount: float) -> dict[str, Any]:
    return {"amount": amount, "currency": "USD", "formatted": f"${amount:.2f}"}


CREATED = {"task_id": "t1", "status": "ACTING", "next_action": "awaiting_tendem_work"}

QUOTED = {
    "task_id": "t1",
    "ready_for_approval": True,
    "price": money(3.0),
    "next_action": "await_user_approval",
}

CONTRACT = {
    "task_id": "t1",
    "state": "available",
    "contract": {"title": "Verify facts", "input_prompt": "Check the claims..."},
    "price": money(3.0),
}

RESULT_READY = {"task_id": "t1", "next_action": "fetch_result"}
RESULT = {"task_id": "t1", "content": "All claims verified.", "files": []}

QUESTION_CHAT = {
    "messages": [
        {"offset": 0, "text": "brief", "from": "host"},
        {"offset": 1, "text": "Which sources do you trust?", "from": "tendem"},
    ],
    "last_seen_offset": 2,
}


def tools_for(session: FakeSession, **kwargs: Any) -> dict[str, Any]:
    client, _ = make_tendem(session)
    built = tendem_tools(max_price=25.0, client=client, **kwargs)
    return {tool.name: tool for tool in built}


def test_the_set_has_four_well_known_tools() -> None:
    session = FakeSession()
    client, _ = make_tendem(session)

    names = [tool.name for tool in tendem_tools(max_price=25.0, client=client)]

    assert names == [
        "create_human_task",
        "check_human_task",
        "reply_to_human_task",
        "wait_for_human_result",
    ]


# ------------------------------------------------------------------- create


async def test_create_is_thin_and_never_polls() -> None:
    session = FakeSession(
        responses={
            "create_task": CREATED,
            "read_chat": {"messages": [], "last_seen_offset": 1},
        }
    )
    tools = tools_for(session)

    answer = await tools["create_human_task"].ainvoke({"request": "Verify: ..."})

    assert answer.startswith("CREATED — task_id='t1'")
    assert "check_human_task(task_id='t1')" in answer
    # Deterministic: exactly one create and one chat read, zero polling.
    assert session.names() == ["create_task", "read_chat"]


async def test_create_reveals_an_instant_reply_from_the_service() -> None:
    session = FakeSession(
        responses={
            "create_task": CREATED,
            "read_chat": {
                "messages": [
                    {"offset": 0, "text": "brief", "from": "host"},
                    {"offset": 1, "text": "Verdict: **True**.", "from": "tendem"},
                ],
                "last_seen_offset": 2,
            },
        }
    )
    tools = tools_for(session)

    answer = await tools["create_human_task"].ainvoke({"request": "Fact-check X"})

    assert "The service already responded" in answer
    assert "Verdict: **True**." in answer


# -------------------------------------------------------------------- check


async def test_check_approves_under_cap_and_reports_started() -> None:
    session = FakeSession(
        responses={
            "get_task": QUOTED,
            "get_contract": CONTRACT,
            "approve_task": {"task_id": "t1", "approved": True},
        }
    )
    tools = tools_for(session)

    answer = await tools["check_human_task"].ainvoke({"task_id": "t1"})

    assert answer.startswith("STARTED")
    assert "$3.00" in answer
    assert "wait_for_human_result" in answer


async def test_check_forwards_a_message_from_the_service() -> None:
    session = FakeSession(
        responses={
            "get_task": {"task_id": "t1", "next_action": "await_input"},
            "read_chat": QUESTION_CHAT,
        }
    )
    tools = tools_for(session)

    answer = await tools["check_human_task"].ainvoke({"task_id": "t1"})

    assert answer.startswith("MESSAGE")
    assert "Which sources do you trust?" in answer
    assert "reply_to_human_task(task_id='t1'" in answer
    # No reply was fabricated on the agent's behalf.
    assert "send_message" not in session.names()


async def test_check_surfaces_an_over_cap_quote_with_the_scope() -> None:
    """The calling agent negotiates — no automatic re-scope message."""
    session = FakeSession(
        responses={
            "get_task": {**QUOTED, "price": money(80.0)},
            "get_contract": {**CONTRACT, "price": money(80.0)},
        }
    )
    tools = tools_for(session)

    answer = await tools["check_human_task"].ainvoke({"task_id": "t1"})

    assert answer.startswith("QUOTE EXCEEDS CAP")
    assert "$80.00" in answer and "$25.00" in answer
    assert "Check the claims..." in answer  # the contract scope, for re-scoping
    assert "reply_to_human_task" in answer
    # Nothing was charged and nothing was sent on the agent's behalf.
    assert "approve_task" not in session.names()
    assert "send_message" not in session.names()


async def test_check_reports_quiet_progress() -> None:
    session = FakeSession(
        responses={
            "get_task": {
                "task_id": "t1",
                "next_action": "awaiting_tendem_work",
                "poll_after_seconds": 10,
            },
        }
    )
    tools = tools_for(session, scoping_timeout=5)

    answer = await tools["check_human_task"].ainvoke({"task_id": "t1"})

    assert answer.startswith("IN PROGRESS")
    assert "'t1'" in answer


# -------------------------------------------------------------------- reply


async def test_reply_sends_the_answer_and_reports_the_approval() -> None:
    session = FakeSession(
        responses={
            "read_chat": QUESTION_CHAT,
            "send_message": {"response_type": "async", "last_seen_offset": 3},
            "get_task": QUOTED,
            "get_contract": CONTRACT,
            "approve_task": {"task_id": "t1", "approved": True},
        }
    )
    tools = tools_for(session)

    answer = await tools["reply_to_human_task"].ainvoke(
        {"task_id": "t1", "reply": "Official sources only."}
    )

    assert answer.startswith("STARTED")
    sent = session.args_for("send_message")[0]
    assert sent["text"] == "Official sources only."
    assert sent["last_seen_offset"] == 2


# --------------------------------------------------------------------- wait


async def test_wait_returns_the_result() -> None:
    session = FakeSession(
        responses={"get_task": RESULT_READY, "get_task_result": RESULT}
    )
    tools = tools_for(session)

    answer = await tools["wait_for_human_result"].ainvoke({"task_id": "t1"})

    assert "All claims verified." in answer


async def test_wait_is_recallable_while_the_expert_works() -> None:
    session = FakeSession(
        responses={
            "get_task": {
                "task_id": "t1",
                "next_action": "awaiting_tendem_work",
                "poll_after_seconds": 10,
            },
        }
    )
    tools = tools_for(session, wait_timeout=5)

    answer = await tools["wait_for_human_result"].ainvoke({"task_id": "t1"})

    assert answer.startswith("IN PROGRESS")
    assert "wait_for_human_result(task_id='t1')" in answer


async def test_wait_surfaces_a_mid_execution_message() -> None:
    session = FakeSession(
        responses={
            "get_task": {"task_id": "t1", "next_action": "await_input"},
            "read_chat": QUESTION_CHAT,
        }
    )
    tools = tools_for(session)

    answer = await tools["wait_for_human_result"].ainvoke({"task_id": "t1"})

    assert answer.startswith("MESSAGE")
    assert "reply_to_human_task" in answer


async def test_wait_surfaces_an_over_cap_quote_too() -> None:
    """A crash between check and wait must not change the money rules."""
    session = FakeSession(
        responses={
            "get_task": {**QUOTED, "price": money(80.0)},
            "get_contract": {**CONTRACT, "price": money(80.0)},
        }
    )
    tools = tools_for(session)

    answer = await tools["wait_for_human_result"].ainvoke({"task_id": "t1"})

    assert answer.startswith("QUOTE EXCEEDS CAP")
    assert "approve_task" not in session.names()


def test_split_tools_work_synchronously_too() -> None:
    session = FakeSession(
        responses={"get_task": RESULT_READY, "get_task_result": RESULT}
    )
    tools = tools_for(session)

    answer = tools["wait_for_human_result"].invoke({"task_id": "t1"})

    assert "All claims verified." in answer


# ------------------------------------------------- resilience and edge paths


async def test_wait_returns_a_free_chat_answer_from_a_closed_task() -> None:
    """Trivial briefs are answered by the orchestrator, free of charge."""
    session = FakeSession(
        responses={
            "get_task": {"task_id": "t1", "status": "CLOSED", "next_action": "done"},
            "get_task_result": {"task_id": "t1", "content": None, "files": []},
            "read_chat": {
                "messages": [
                    {"offset": 0, "text": "Fact-check X", "from": "host"},
                    {"offset": 1, "text": "Verdict: **True**.", "from": "tendem"},
                ],
                "last_seen_offset": 2,
            },
        }
    )
    tools = tools_for(session)

    answer = await tools["wait_for_human_result"].ainvoke({"task_id": "t1"})

    assert "Verdict: **True**." in answer
    assert "no charge" in answer


async def test_wait_reports_a_task_that_died_without_a_deliverable() -> None:
    session = FakeSession(
        responses={
            "get_task": {"task_id": "t1", "status": "CLOSED", "next_action": "done"},
            "get_task_result": {"task_id": "t1", "content": None, "files": []},
            "read_chat": {"messages": [], "last_seen_offset": 0},
        }
    )
    tools = tools_for(session)

    answer = await tools["wait_for_human_result"].ainvoke({"task_id": "t1"})

    assert answer.startswith("NOT COMPLETED")
    assert "without a deliverable" in answer


async def test_wait_attributes_the_price_after_a_crash_resume() -> None:
    """Approval happened in an earlier (killed) call: the price still shows."""
    session = FakeSession(
        responses={
            "get_task": RESULT_READY,
            "get_task_result": RESULT,
            "get_contract": CONTRACT,
        }
    )
    tools = tools_for(session)

    answer = await tools["wait_for_human_result"].ainvoke({"task_id": "t1"})

    assert "All claims verified." in answer
    assert "$3.00" in answer


async def test_polling_rides_out_a_transient_blip() -> None:
    from conftest import error_result

    session = FakeSession(
        responses={
            "get_task": [
                error_result("Tool failed (TEMPORARILY_UNAVAILABLE): hiccup"),
                RESULT_READY,
            ],
            "get_task_result": RESULT,
        }
    )
    client, clock = make_tendem(session)
    tools = {t.name: t for t in tendem_tools(max_price=25.0, client=client)}

    answer = await tools["wait_for_human_result"].ainvoke({"task_id": "t1"})

    assert "All claims verified." in answer
    assert 2.0 in clock.sleeps  # backoff before the retry


async def test_full_lifecycle_across_the_four_tools() -> None:
    """create → check (auto-approve) → wait, as an agent would drive it."""
    session = FakeSession(
        responses={
            "create_task": CREATED,
            "read_chat": {"messages": [], "last_seen_offset": 1},
            "get_task": [QUOTED, RESULT_READY],
            "get_contract": CONTRACT,
            "approve_task": {"task_id": "t1", "approved": True},
            "get_task_result": RESULT,
        }
    )
    tools = tools_for(session)

    created = await tools["create_human_task"].ainvoke({"request": "Verify: ..."})
    assert "task_id='t1'" in created

    started = await tools["check_human_task"].ainvoke({"task_id": "t1"})
    assert started.startswith("STARTED")
    assert session.args_for("approve_task") == [
        {"task_id": "t1", "name": "Verify facts", "price": "$3.00"}
    ]

    result = await tools["wait_for_human_result"].ainvoke({"task_id": "t1"})
    assert "All claims verified." in result
