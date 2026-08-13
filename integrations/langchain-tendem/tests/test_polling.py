"""Polling must terminate, block server-side, and never spin."""

from __future__ import annotations

import pytest
from conftest import FakeSession, make_tendem

from langchain_tendem import PollTimeoutError, TaskSnapshot

WORKING = {
    "task_id": "task-1",
    "status": "ACTING",
    "next_action": "awaiting_tendem_work",
}
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


def needs_me(snapshot: TaskSnapshot) -> bool:
    return snapshot.needs_caller


async def test_poll_gives_up_after_max_rounds() -> None:
    """A task that never changes must not hold the loop forever."""
    session = FakeSession(responses={"get_task": WORKING})
    client, _ = make_tendem(session)

    with pytest.raises(PollTimeoutError) as excinfo:
        await client.poll("task-1", needs_me, max_rounds=4)

    assert excinfo.value.rounds == 4
    assert session.names().count("get_task") == 4
    assert excinfo.value.snapshot is not None
    assert excinfo.value.snapshot.next_action == "awaiting_tendem_work"


async def test_poll_uses_server_side_blocking() -> None:
    session = FakeSession(responses={"get_task": WORKING})
    client, _ = make_tendem(session)

    with pytest.raises(PollTimeoutError):
        await client.poll("task-1", needs_me, max_rounds=2)

    assert session.args_for("get_task") == [
        {"task_id": "task-1", "wait_for_change_seconds": 30},
        {"task_id": "task-1", "wait_for_change_seconds": 30},
    ]


async def test_poll_never_busy_loops_even_if_the_server_returns_instantly() -> None:
    """The fake server answers with zero delay; the loop must still pace itself."""
    session = FakeSession(responses={"get_task": WORKING})
    client, clock = make_tendem(session)

    with pytest.raises(PollTimeoutError):
        await client.poll("task-1", needs_me, max_rounds=5)

    # One pacing sleep between each pair of rounds, each at least the floor.
    assert len(clock.sleeps) == 4
    assert all(delay >= client.min_poll_interval for delay in clock.sleeps)
    assert clock.now >= client.min_poll_interval * 4


async def test_poll_honours_server_poll_after_seconds() -> None:
    session = FakeSession(responses={"get_task": {**WORKING, "poll_after_seconds": 12}})
    client, clock = make_tendem(session)

    with pytest.raises(PollTimeoutError):
        await client.poll("task-1", needs_me, max_rounds=3)

    assert clock.sleeps == [12.0, 12.0]


async def test_poll_returns_as_soon_as_the_predicate_is_satisfied() -> None:
    session = FakeSession(responses={"get_task": [WORKING, WORKING, QUOTED]})
    client, _ = make_tendem(session)

    snapshot = await client.poll("task-1", needs_me, max_rounds=8)

    assert snapshot.next_action == "await_user_approval"
    assert session.names().count("get_task") == 3


async def test_poll_predicates_compose_from_snapshot_helpers() -> None:
    session = FakeSession(responses={"get_task": [QUOTED, QUOTED, RESULT_READY]})
    client, _ = make_tendem(session)

    snapshot = await client.poll(
        "task-1", lambda snap: snap.result_ready, max_rounds=6
    )

    assert snapshot.result_ready is True
    assert session.names().count("get_task") == 3


async def test_poll_stops_on_a_terminal_status() -> None:
    session = FakeSession(
        responses={"get_task": {"task_id": "task-1", "status": "CLOSED"}}
    )
    client, _ = make_tendem(session)

    snapshot = await client.poll("task-1", needs_me, max_rounds=5)

    assert snapshot.is_terminal is True
    assert session.names().count("get_task") == 1


async def test_poll_rejects_a_zero_round_budget() -> None:
    client, _ = make_tendem(FakeSession(responses={"get_task": WORKING}))

    with pytest.raises(ValueError, match="at least 1"):
        await client.poll("task-1", needs_me, max_rounds=0)


async def test_poll_tolerates_an_unknown_next_action() -> None:
    """A next_action the client has never heard of must not crash the loop."""
    session = FakeSession(
        responses={"get_task": {"task_id": "task-1", "next_action": "warp_drive"}}
    )
    client, _ = make_tendem(session)

    with pytest.raises(PollTimeoutError) as excinfo:
        await client.poll("task-1", needs_me, max_rounds=2)

    assert excinfo.value.snapshot is not None
    assert excinfo.value.snapshot.action is None
    assert excinfo.value.snapshot.next_action == "warp_drive"


async def test_on_round_callback_sees_every_round() -> None:
    session = FakeSession(responses={"get_task": [WORKING, QUOTED]})
    client, _ = make_tendem(session)
    seen: list[int] = []

    await client.poll(
        "task-1", needs_me, max_rounds=5, on_round=lambda _s, i: seen.append(i)
    )

    assert seen == [0, 1]
