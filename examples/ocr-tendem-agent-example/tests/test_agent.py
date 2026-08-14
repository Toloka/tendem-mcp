"""The default flow: one agent, five tools, faked model — no network.

The agent is stubbed by driving `report_tool` and `agentic_process_single_file`
directly, so what gets tested is the wiring and the guard, not the model's
judgement.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from ocr_batch.agentic import flow as agent_module
from ocr_batch.agentic.flow import agentic_process_single_file, scope_sha
from ocr_batch.agentic.guards import report_tool
from ocr_batch.common.config import FIELDS, Settings
from ocr_batch.common.journal import Journal
from ocr_batch.scripted.escalate import HumanResult

READ = {
    "invoice_number": "BP-11627",
    "issue_date": "2026-07-11",
    "vendor_name": "BluePeak Analytics Ltd",
    "total_amount": "1295.50",
    "currency": "GBP",
    "confidence": 0.97,
    "notes": "",
    "source": "model",
}
FROM_EXPERT = {**READ, "invoice_number": "VP-58812", "source": "human"}
WITH_GAPS = {**READ, "invoice_number": None, "issue_date": None}


@pytest.fixture
def settings() -> Settings:
    return Settings(
        base_url="http://localhost/v1",
        api_key="test",
        ocr_model="test-vision",
        tendem_api_key="test",
        max_price=10.0,
    )


@pytest.fixture
def document(tmp_path: Path) -> Path:
    path = tmp_path / "smudged.png"
    Image.new("RGB", (20, 20), "white").save(path)
    return path


class FakeAgent:
    """Stands in for the compiled graph: calls the tools it was told to."""

    def __init__(self, tools, calls: list, script) -> None:
        self.by_name = {tool.name: tool for tool in tools}
        self.calls = calls
        self.script = script

    async def ainvoke(self, payload, config=None):
        self.calls.append((payload, config))
        messages = []
        for name, args in self.script:
            out = await self.by_name[name].ainvoke(args)
            messages.append(type("M", (), {"content": out})())
        return {"messages": messages}


def drive(monkeypatch, *script) -> list:
    """Make `create_agent` return an agent that performs `script` verbatim."""
    calls: list = []

    def fake_create_agent(model, tools=None, **kwargs):
        return FakeAgent(tools, calls, script)

    monkeypatch.setattr(agent_module, "create_agent", fake_create_agent)
    monkeypatch.setattr(
        agent_module, "tendem_tools", lambda **kwargs: _fake_tendem_tools()
    )
    monkeypatch.setattr(agent_module, "Tendem", lambda **kwargs: object())

    async def unscripted_fallback(*args, **kwargs):
        return HumanResult(error="the fallback is not scripted in this test")

    monkeypatch.setattr(agent_module, "escalate", unscripted_fallback)
    return calls


async def create_human_task(request: str, file_paths: list[str] | None = None) -> str:
    assert file_paths, "the scan must ride along with the brief"
    assert "invoice_number" in request, "the brief must stand alone"
    return "CREATED — task_id='task-1'."


async def check_human_task(task_id: str) -> str:
    if task_id == "over-cap":
        return (
            f"QUOTE EXCEEDS CAP — task '{task_id}' was quoted at $3.00, above "
            "the $0.50 budget cap. Nothing has been charged."
        )
    return f"STARTED — task '{task_id}' approved at $3.00. Working now."


class Killed(Exception):
    """Stands in for the process dying mid-wait."""


async def wait_for_human_result(task_id: str) -> str:
    if task_id == "interrupt-me":
        raise Killed("the process went away while the expert was working")
    return f"{json.dumps(FROM_EXPERT)}\n(Executed by a human expert for $3.00.)"


def _fake_tendem_tools() -> list:
    """The three tools this flow actually exercises, same names and shapes."""
    from langchain_core.tools import StructuredTool

    return [
        StructuredTool.from_function(
            coroutine=func, name=func.__name__, description=func.__name__
        )
        for func in (create_human_task, check_human_task, wait_for_human_result)
    ]


async def process_file(settings, document, **kwargs):
    """Call the per-file function the way the harness does, model faked."""
    return await agentic_process_single_file(settings, document, llm=object(), **kwargs)


# ------------------------------------------------------------------ the guard


def test_a_gap_is_refused_while_an_expert_is_available():
    recorded: dict = {}
    tool = report_tool(recorded)

    answer = tool.invoke(WITH_GAPS)

    assert "NOT ACCEPTED" in answer
    assert "invoice_number" in answer and "issue_date" in answer
    assert recorded == {}, "nothing is recorded on a refusal"


def test_a_report_below_the_threshold_goes_to_a_human():
    """The model's own admitted doubt is a tripwire, not proof of a mistake."""
    recorded: dict = {}
    tool = report_tool(recorded, min_confidence=0.95)

    assert "NOT ACCEPTED" in tool.invoke({**READ, "confidence": 0.86})
    assert recorded == {}


def test_the_same_gap_is_accepted_once_a_human_has_looked():
    recorded: dict = {}
    tool = report_tool(recorded)

    tool.invoke({**WITH_GAPS, "source": "human"})

    assert recorded["source"] == "human"


def test_a_clean_read_is_recorded_immediately():
    recorded: dict = {}

    report_tool(recorded).invoke(READ)

    assert all(recorded[name] for name in FIELDS)


# ------------------------------------------------------------------- the loop


async def test_a_readable_scan_never_reaches_a_human(settings, document, monkeypatch):
    drive(monkeypatch, ("report_fields", READ))

    record = await process_file(settings, document)

    assert record.source == "model"
    assert record.fields["invoice_number"] == "BP-11627"
    assert record.task_id is None and record.price is None


async def test_an_unreadable_scan_is_delegated_and_journaled(
    settings, document, monkeypatch, tmp_path
):
    drive(
        monkeypatch,
        ("report_fields", WITH_GAPS),  # refused by the guard
        ("create_human_task", {"request": f"{FIELDS}", "file_paths": [str(document)]}),
        ("check_human_task", {"task_id": "task-1"}),
        ("wait_for_human_result", {"task_id": "task-1"}),
        ("report_fields", FROM_EXPERT),
    )
    journal = Journal.load(tmp_path / "tasks.jsonl")

    record = await process_file(
        settings, document, journal=journal, endpoint="https://mcp.example/mcp"
    )

    assert record.source == "human"
    assert record.fields["invoice_number"] == "VP-58812"
    assert record.task_id == "task-1"
    assert record.price == "$3.00"
    # Created and collected, so nothing is left in flight.
    assert Journal.load(tmp_path / "tasks.jsonl").orphans() == []


async def test_an_interrupted_run_resumes_the_same_task(
    settings, document, monkeypatch, tmp_path
):
    journal_path = tmp_path / "tasks.jsonl"
    first = Journal.load(journal_path)

    # Run one dies mid-wait, after creating the task.
    drive(
        monkeypatch,
        ("create_human_task", {"request": f"{FIELDS}", "file_paths": [str(document)]}),
        ("wait_for_human_result", {"task_id": "interrupt-me"}),
    )
    with pytest.raises(Killed):
        await process_file(
            settings, document, journal=first, endpoint="https://mcp.example/mcp"
        )
    assert [o["task_id"] for o in Journal.load(journal_path).orphans()] == ["task-1"]

    # Run two must be told the id, and must not create a second task.
    calls = drive(monkeypatch, ("report_fields", FROM_EXPERT))
    record = await process_file(
        settings,
        document,
        journal=Journal.load(journal_path),
        endpoint="https://mcp.example/mcp",
    )

    prompt = json.dumps(calls[0][0]["messages"][0].content, default=str)
    assert "task_id='task-1'" in prompt, "the agent is told what already exists"
    assert "Do NOT" in prompt
    assert record.task_id == "task-1"
    assert Journal.load(journal_path).orphans() == []


async def test_a_kill_during_the_wait_still_records_the_task(
    settings, document, monkeypatch, tmp_path
):
    """The id must be on disk before the hours of waiting, not after."""

    journal_path = tmp_path / "tasks.jsonl"
    drive(
        monkeypatch,
        ("create_human_task", {"request": f"{FIELDS}", "file_paths": [str(document)]}),
        ("wait_for_human_result", {"task_id": "interrupt-me"}),
    )

    with pytest.raises(Killed):
        await process_file(
            settings,
            document,
            journal=Journal.load(journal_path),
            endpoint="https://mcp.example/mcp",
        )

    orphans = Journal.load(journal_path).orphans()
    assert [o["task_id"] for o in orphans] == ["task-1"], "the id survived the kill"


async def test_a_second_create_is_refused_by_the_tool(
    settings, document, monkeypatch, tmp_path
):
    """Not merely discouraged in the prompt: the tool itself says no."""
    journal_path = tmp_path / "tasks.jsonl"
    args = {"request": f"{FIELDS}", "file_paths": [str(document)]}
    drive(
        monkeypatch,
        ("create_human_task", args),
        ("create_human_task", args),  # a disobedient agent tries again
        ("report_fields", FROM_EXPERT),
    )

    record = await process_file(
        settings,
        document,
        journal=Journal.load(journal_path),
        endpoint="https://mcp.example/mcp",
    )

    created = [
        entry
        for entry in Journal.load(journal_path)._entries
        if entry["event"] == "created"
    ]
    assert len(created) == 1, "one task, however many times it asked"
    assert record.task_id == "task-1"


async def test_an_over_cap_quote_lets_the_agent_stop(
    settings, document, monkeypatch, tmp_path
):
    """Refused for price: the guard must stop demanding an escalation, and the
    journal must not report money in flight when nothing was charged."""
    journal_path = tmp_path / "tasks.jsonl"
    drive(
        monkeypatch,
        ("create_human_task", {"request": f"{FIELDS}", "file_paths": [str(document)]}),
        ("check_human_task", {"task_id": "over-cap"}),
        ("report_fields", WITH_GAPS),  # would be refused, were the cap not hit
    )

    record = await process_file(
        settings,
        document,
        journal=Journal.load(journal_path),
        endpoint="https://mcp.example/mcp",
    )

    assert record.source == "failed"
    assert record.fields["currency"] == "GBP", "what it could read is kept"
    assert Journal.load(journal_path).orphans() == [], "nothing was charged"


async def test_a_silent_agent_still_ends_with_a_human_answer(
    settings, document, monkeypatch, tmp_path
):
    """The agent answers in prose instead of calling report_fields: the
    deterministic hand-off takes over and buys the answer."""
    drive(monkeypatch)  # the agent calls no tools at all and stops
    handed: dict = {}

    async def fake_escalate(tendem, llm, item, **kwargs):
        handed.update(kwargs, file=item.path.name)
        return HumanResult(
            task_id="task-9",
            fields=dict.fromkeys(FIELDS, "x"),
            price="$2.00",
        )

    monkeypatch.setattr(agent_module, "escalate", fake_escalate)
    record = await process_file(
        settings,
        document,
        journal=Journal.load(tmp_path / "tasks.jsonl"),
        endpoint="https://mcp.example/mcp",
    )

    assert record.source == "human"
    assert record.task_id == "task-9"
    assert handed["file"] == "smudged.png"
    assert handed["task_id"] is None, "no task existed yet — the fallback creates one"
    assert handed["scope"] == scope_sha(), "journaled as the agent's own question"


async def test_the_fallback_resumes_the_task_the_agent_abandoned(
    settings, document, monkeypatch, tmp_path
):
    """Created a task, then went silent: the fallback drives that same task —
    never a second one for the same scan."""
    drive(
        monkeypatch,
        ("create_human_task", {"request": f"{FIELDS}", "file_paths": [str(document)]}),
    )
    handed: dict = {}

    async def fake_escalate(tendem, llm, item, **kwargs):
        handed.update(kwargs)
        return HumanResult(
            task_id=kwargs["task_id"],
            fields=dict.fromkeys(FIELDS, "x"),
            price="$3.00",
        )

    monkeypatch.setattr(agent_module, "escalate", fake_escalate)
    record = await process_file(
        settings,
        document,
        journal=Journal.load(tmp_path / "tasks.jsonl"),
        endpoint="https://mcp.example/mcp",
    )

    assert handed["task_id"] == "task-1", "the agent's own task, not a new one"
    assert record.source == "human"
    assert record.task_id == "task-1"


async def test_a_byte_identical_copy_gets_its_own_task(
    settings, document, monkeypatch, tmp_path
):
    """Two files in the folder are two documents, however alike their contents."""
    journal_path = tmp_path / "tasks.jsonl"
    args = {"request": f"{FIELDS}", "file_paths": [str(document)]}
    drive(monkeypatch, ("create_human_task", args))
    await process_file(
        settings,
        document,
        journal=Journal.load(journal_path),
        endpoint="https://mcp.example/mcp",
    )

    copy = tmp_path / "smudged copy.png"
    copy.write_bytes(document.read_bytes())
    calls = drive(monkeypatch, ("create_human_task", args))
    await process_file(
        settings,
        copy,
        journal=Journal.load(journal_path),
        endpoint="https://mcp.example/mcp",
    )

    prompt = json.dumps(calls[0][0]["messages"][0].content, default=str)
    assert "already created" not in prompt, "the copy is not told to resume"
    created = [
        entry
        for entry in Journal.load(journal_path)._entries
        if entry["event"] == "created"
    ]
    assert len(created) == 2, "one task per file, not per set of bytes"
    assert len({entry["sha"] for entry in created}) == 2


async def test_a_re_scoped_question_does_not_adopt_an_old_task(
    settings, document, monkeypatch, tmp_path
):
    journal = Journal.load(tmp_path / "tasks.jsonl")
    journal.created(
        agent_module.document_sha(document),
        document.name,
        "task-old",
        url="https://mcp.example/mcp",
        scope_sha="a-different-question",
    )
    calls = drive(monkeypatch, ("report_fields", READ))

    await process_file(
        settings, document, journal=journal, endpoint="https://mcp.example/mcp"
    )

    prompt = json.dumps(calls[0][0]["messages"][0].content, default=str)
    assert "task-old" not in prompt


def test_the_scope_hash_ignores_run_to_run_wording(monkeypatch):
    before = scope_sha()
    monkeypatch.setattr(agent_module, "PROMPT", agent_module.PROMPT + " (reworded)")

    assert scope_sha() != before, "changing the prompt re-scopes the question"
