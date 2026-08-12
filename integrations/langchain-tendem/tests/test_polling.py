"""Polling must terminate, block server-side, and never spin."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import FakeSession, make_tendem

from langchain_tendem import NextAction, PollTimeoutError

WORKING = {"task_id": "task-1", "status": "ACTING", "next_action": "awaiting_tendem_work"}
QUOTED = {
    "task_id": "task-1",
    "status": "LISTENING",
    "next_action": "await_user_approval",
}
RESULT_READY = {
    "task_id": "task-1",
    "status": "LISTENING",
    "next_action": "fetch_result",
}


async def test_poll_gives_up_after_max_rounds() -> None:
    """A task that never changes must not hold the loop forever."""
    session = FakeSession(responses={"get_task": WORKING})
    client, _ = make_tendem(session)

    with pytest.raises(PollTimeoutError) as excinfo:
        await client.poll("task-1", max_rounds=4)

    assert excinfo.value.rounds == 4
    assert session.names().count("get_task") == 4
    assert excinfo.value.snapshot is not None
    assert excinfo.value.snapshot.action is NextAction.AWAITING_TENDEM_WORK


async def test_poll_uses_server_side_blocking() -> None:
    session = FakeSession(responses={"get_task": WORKING})
    client, _ = make_tendem(session)

    with pytest.raises(PollTimeoutError):
        await client.poll("task-1", max_rounds=2)

    assert session.args_for("get_task") == [
        {"task_id": "task-1", "wait_for_change_seconds": 30},
        {"task_id": "task-1", "wait_for_change_seconds": 30},
    ]


async def test_poll_never_busy_loops_even_if_the_server_returns_instantly() -> None:
    """The fake server answers with zero delay; the loop must still pace itself."""
    session = FakeSession(responses={"get_task": WORKING})
    client, clock = make_tendem(session)

    with pytest.raises(PollTimeoutError):
        await client.poll("task-1", max_rounds=5)

    # One pacing sleep between each pair of rounds, each at least the floor.
    assert len(clock.sleeps) == 4
    assert all(delay >= client.min_poll_interval for delay in clock.sleeps)
    assert clock.now >= client.min_poll_interval * 4


async def test_poll_honours_server_poll_after_seconds() -> None:
    session = FakeSession(
        responses={"get_task": {**WORKING, "poll_after_seconds": 12}}
    )
    client, clock = make_tendem(session)

    with pytest.raises(PollTimeoutError):
        await client.poll("task-1", max_rounds=3)

    assert clock.sleeps == [12.0, 12.0]


async def test_poll_returns_as_soon_as_the_task_needs_us() -> None:
    session = FakeSession(responses={"get_task": [WORKING, WORKING, QUOTED]})
    client, _ = make_tendem(session)

    snapshot = await client.poll("task-1", max_rounds=8)

    assert snapshot.action is NextAction.AWAIT_USER_APPROVAL
    assert snapshot.needs_human is True
    assert session.names().count("get_task") == 3


async def test_poll_stops_on_a_terminal_status() -> None:
    session = FakeSession(
        responses={"get_task": {"task_id": "task-1", "status": "CLOSED"}}
    )
    client, _ = make_tendem(session)

    snapshot = await client.poll("task-1", max_rounds=5)

    assert snapshot.is_terminal is True
    assert session.names().count("get_task") == 1


async def test_poll_accepts_an_explicit_until_set() -> None:
    session = FakeSession(responses={"get_task": [QUOTED, QUOTED, RESULT_READY]})
    client, _ = make_tendem(session)

    snapshot = await client.poll(
        "task-1", until=(NextAction.FETCH_RESULT,), max_rounds=6
    )

    assert snapshot.result_ready is True
    assert session.names().count("get_task") == 3


async def test_poll_accepts_a_predicate() -> None:
    session = FakeSession(responses={"get_task": [WORKING, RESULT_READY]})
    client, _ = make_tendem(session)

    snapshot = await client.poll(
        "task-1", until=lambda snap: snap.result_ready, max_rounds=6
    )

    assert snapshot.result_ready is True


async def test_poll_rejects_a_zero_round_budget() -> None:
    client, _ = make_tendem(FakeSession(responses={"get_task": WORKING}))

    with pytest.raises(ValueError, match="at least 1"):
        await client.poll("task-1", max_rounds=0)


async def test_poll_tolerates_an_unknown_next_action() -> None:
    """A next_action the client has never heard of must not crash the loop."""
    session = FakeSession(
        responses={"get_task": {"task_id": "task-1", "next_action": "warp_drive"}}
    )
    client, _ = make_tendem(session)

    with pytest.raises(PollTimeoutError) as excinfo:
        await client.poll("task-1", max_rounds=2)

    assert excinfo.value.snapshot is not None
    assert excinfo.value.snapshot.action is None
    assert excinfo.value.snapshot.next_action == "warp_drive"


async def test_on_round_callback_sees_every_round() -> None:
    session = FakeSession(responses={"get_task": [WORKING, QUOTED]})
    client, _ = make_tendem(session)
    seen: list[int] = []

    await client.poll("task-1", max_rounds=5, on_round=lambda _s, i: seen.append(i))

    assert seen == [0, 1]


# ------------------------------------------------------------ scoping loop


async def test_drive_scoping_answers_then_stops_at_the_quote() -> None:
    session = FakeSession(
        responses={
            "get_task": [
                {"task_id": "task-1", "next_action": "await_input"},
                QUOTED,
            ],
            "read_chat": {
                "messages": [{"role": "tendem", "text": "Which competitors?"}],
                "next_offset": 4,
            },
        }
    )
    client, _ = make_tendem(session)

    async def answer(chat: dict[str, Any], _snap: Any) -> str:
        assert chat["messages"][0]["text"] == "Which competitors?"
        return "Beta and Gamma."

    snapshot = await client.drive_scoping("task-1", answer=answer)

    assert snapshot.action is NextAction.AWAIT_USER_APPROVAL
    assert session.args_for("send_message") == [
        {"task_id": "task-1", "text": "Beta and Gamma.", "last_seen_offset": 4}
    ]


async def test_drive_scoping_stops_when_the_answerer_declines() -> None:
    """Returning None means 'a human must answer this' — stop, do not guess."""
    session = FakeSession(
        responses={
            "get_task": {"task_id": "task-1", "next_action": "await_input"},
            "read_chat": {"messages": [], "next_offset": 1},
        }
    )
    client, _ = make_tendem(session)

    snapshot = await client.drive_scoping("task-1", answer=lambda *_: _none())

    assert snapshot.action is NextAction.AWAIT_INPUT
    assert "send_message" not in session.names()


async def test_drive_scoping_is_bounded() -> None:
    """An endlessly questioning server must exhaust the exchange budget."""
    session = FakeSession(
        responses={
            "get_task": {"task_id": "task-1", "next_action": "await_input"},
            "read_chat": {"messages": [], "next_offset": 1},
        }
    )
    client, _ = make_tendem(session)

    async def answer(*_: Any) -> str:
        return "sure"

    with pytest.raises(PollTimeoutError):
        await client.drive_scoping("task-1", answer=answer, max_exchanges=3)

    assert session.names().count("send_message") == 3


async def _none() -> None:
    return None
