"""Input-file upload: URL mechanics, the naming message, and the tool wiring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import FakeSession, make_tendem

from langchain_tendem import TaskFailedError, prepare_task, tendem_tools
from langchain_tendem import runner as runner_module
from langchain_tendem.runner import blob_upload_url

SAS = (
    "https://acct.dfs.core.windows.net/container/task-1"
    "?sv=2024&sig=abc%3D&se=2026"
)


def test_blob_upload_url_swaps_host_and_inserts_the_name() -> None:
    url = blob_upload_url(SAS, "data/input.csv")

    assert url == (
        "https://acct.blob.core.windows.net/container/task-1/data/input.csv"
        "?sv=2024&sig=abc%3D&se=2026"
    )


@pytest.fixture
def uploads(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int]]:
    """Capture blob PUTs instead of hitting the network."""
    seen: list[tuple[str, int]] = []

    async def fake_put(url: str, data: bytes) -> None:
        seen.append((url, len(data)))

    monkeypatch.setattr(runner_module, "_put_blob", fake_put)
    return seen


def upload_session(**extra: Any) -> FakeSession:
    return FakeSession(
        responses={
            "create_task": {"task_id": "t1", "next_action": "awaiting_tendem_work"},
            "get_file_upload_url": {"upload_url": SAS, "expires_in_seconds": 3600},
            "send_message": {"response_type": "async", "last_seen_offset": 3},
            "read_chat": {"messages": [], "last_seen_offset": 3},
            **extra,
        }
    )


async def test_create_tool_uploads_files_and_announces_them(
    tmp_path: Path, uploads: list[tuple[str, int]]
) -> None:
    (tmp_path / "brief.pdf").write_bytes(b"12345")
    session = upload_session()
    client, _ = make_tendem(session)
    tools = {t.name: t for t in tendem_tools(max_price=25.0, client=client)}

    answer = await tools["create_human_task"].ainvoke(
        {
            "request": "Review the brief.",
            "file_paths": [str(tmp_path / "brief.pdf")],
        }
    )

    assert answer.startswith("CREATED — task_id='t1'")
    # Uploaded under its basename, to the blob endpoint, before the query.
    assert uploads == [
        (
            "https://acct.blob.core.windows.net/container/task-1/brief.pdf"
            "?sv=2024&sig=abc%3D&se=2026",
            5,
        )
    ]
    # The brief promises the files; the follow-up message names them.
    assert "Input files (uploading them now): brief.pdf" in (
        session.args_for("create_task")[0]["description"]
    )
    assert "brief.pdf" in session.args_for("send_message")[0]["text"]


async def test_prepare_task_accepts_a_name_to_bytes_mapping(
    uploads: list[tuple[str, int]],
) -> None:
    session = upload_session()
    client, _ = make_tendem(session)

    task_id = await prepare_task(
        client, "Analyse the data.", files={"data/input.csv": b"a,b\n1,2\n"}
    )

    assert task_id == "t1"
    assert uploads[0][0].endswith("/task-1/data/input.csv?sv=2024&sig=abc%3D&se=2026")


async def test_upload_announcement_survives_a_chat_race(
    tmp_path: Path, uploads: list[tuple[str, int]]
) -> None:
    """Tendem asking for the files mid-upload races the confirmation."""
    (tmp_path / "a.txt").write_bytes(b"x")
    session = upload_session(
        send_message=[
            {"response_type": "race", "last_seen_offset": 2},
            {"response_type": "async", "last_seen_offset": 3},
        ]
    )
    client, _ = make_tendem(session)

    await prepare_task(client, "Go.", files=[tmp_path / "a.txt"])

    sends = session.args_for("send_message")
    assert len(sends) == 2
    assert sends[1]["last_seen_offset"] == 2  # re-sent at the new offset


async def test_upload_failure_names_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.txt").write_bytes(b"x")

    async def broken_put(url: str, data: bytes) -> None:
        raise ConnectionError("boom")

    monkeypatch.setattr(runner_module, "_put_blob", broken_put)
    client, _ = make_tendem(upload_session())

    with pytest.raises(TaskFailedError, match="a.txt"):
        await prepare_task(client, "Go.", files=[tmp_path / "a.txt"])


async def test_create_tool_reports_the_upload_failure_as_a_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.txt").write_bytes(b"x")

    async def broken_put(url: str, data: bytes) -> None:
        raise ConnectionError("boom")

    monkeypatch.setattr(runner_module, "_put_blob", broken_put)
    client, _ = make_tendem(upload_session())
    tools = {t.name: t for t in tendem_tools(max_price=25.0, client=client)}

    answer = await tools["create_human_task"].ainvoke(
        {"request": "Go.", "file_paths": [str(tmp_path / "a.txt")]}
    )

    assert answer.startswith("NOT COMPLETED")
    assert "a.txt" in answer


async def test_prepare_task_requires_a_description() -> None:
    client, _ = make_tendem(FakeSession())

    with pytest.raises(ValueError, match="description"):
        await prepare_task(client, "   \n  ")
