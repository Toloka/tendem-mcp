"""The journal's only job: never resume the wrong task, never forget a live one."""

from __future__ import annotations

from pathlib import Path

from ocr_batch.common.journal import Journal

URL = "https://mcp.tendem.ai/mcp"
GUARDS = {"url": URL, "scope_sha": "scope1"}


def a_journal(tmp_path: Path) -> Journal:
    journal = Journal.load(tmp_path / "tasks.jsonl")
    journal.created("sha1", "invoice.png", "task-1", **GUARDS)
    return journal


def test_a_live_task_is_adopted_and_survives_a_restart(tmp_path):
    a_journal(tmp_path)

    reloaded = Journal.load(tmp_path / "tasks.jsonl")  # a whole new process
    assert reloaded.adopt("sha1", **GUARDS) == "task-1"


def test_a_collected_task_is_not_adopted_again(tmp_path):
    journal = a_journal(tmp_path)
    journal.finished("sha1", "task-1", "result", "$3.00")

    assert journal.adopt("sha1", **GUARDS) is None
    assert journal.orphans() == []


def test_an_over_cap_quote_closes_the_task(tmp_path):
    journal = a_journal(tmp_path)
    journal.finished("sha1", "task-1", "over_budget")

    assert journal.adopt("sha1", **GUARDS) is None


def test_a_transport_failure_leaves_the_task_resumable(tmp_path):
    journal = a_journal(tmp_path)
    journal.finished("sha1", "task-1", "exhausted")  # not terminal — ignored

    assert journal.adopt("sha1", **GUARDS) == "task-1"
    assert [o["task_id"] for o in journal.orphans()] == ["task-1"]


def test_each_guard_rejects_on_its_own(tmp_path):
    journal = a_journal(tmp_path)

    assert journal.adopt("sha2", **GUARDS) is None, "a different document"
    assert (
        journal.adopt("sha1", url="https://mcp.tendem-test.ai/mcp", scope_sha="scope1")
        is None
    ), "a task id from another deployment"
    assert journal.adopt("sha1", url=URL, scope_sha="scope2") is None, (
        "a re-scoped brief"
    )


def test_the_newest_entry_for_a_document_wins(tmp_path):
    journal = a_journal(tmp_path)
    journal.created("sha1", "invoice.png", "task-2", **GUARDS)

    assert journal.adopt("sha1", **GUARDS) == "task-2"
    assert len(journal.orphans()) == 2  # both are live and billable


def test_an_endpoint_switch_does_not_shadow_a_live_task(tmp_path):
    """A task created on another deployment is skipped, not the last word.

    Flip `TENDEM_MCP_URL` to prestable and back: the production task is still
    live and still billable, so it must still be adopted — not abandoned for a
    duplicate just because a prestable entry is newer in the file.
    """
    journal = a_journal(tmp_path)  # task-1, on URL
    prestable = "https://mcp.tendem-test.ai/mcp"
    journal.created("sha1", "invoice.png", "task-2", url=prestable, scope_sha="scope1")

    assert journal.adopt("sha1", **GUARDS) == "task-1"
    assert journal.adopt("sha1", url=prestable, scope_sha="scope1") == "task-2"


def test_a_collected_task_uncovers_an_older_live_one(tmp_path):
    """Closing the newest task must not write off an older one still running."""
    journal = a_journal(tmp_path)  # task-1
    journal.created("sha1", "invoice.png", "task-2", **GUARDS)
    journal.finished("sha1", "task-2", "result", "$3.00")

    assert journal.adopt("sha1", **GUARDS) == "task-1"
    assert [o["task_id"] for o in journal.orphans()] == ["task-1"]


def test_a_truncated_line_does_not_break_the_journal(tmp_path):
    path = tmp_path / "tasks.jsonl"
    a_journal(tmp_path)
    with path.open("a") as handle:
        handle.write('{"event": "created", "sha": "sha9"\n')  # killed mid-write

    reloaded = Journal.load(path)
    assert reloaded.adopt("sha1", **GUARDS) == "task-1"
