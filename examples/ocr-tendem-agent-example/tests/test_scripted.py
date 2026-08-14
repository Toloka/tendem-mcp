"""The scripted flow, with the model and Tendem faked out.

Both halves are covered: a confident read becomes a model row, an unconfident
one is escalated and the expert's answer takes its place. Batch behaviour
(rows landing as they settle, failures becoming rows) is the harness's job
and is tested in `test_cli.py`.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from langchain_tendem import TaskEvent, TaskOutcome
from PIL import Image

from ocr_batch.common.config import Settings
from ocr_batch.common.journal import Journal
from ocr_batch.common.report import summarize, write_csv
from ocr_batch.scripted import escalate as escalate_module
from ocr_batch.scripted import extract as extract_module
from ocr_batch.scripted import scripted_process_single_file

CLEAN = {
    "fields": {
        "invoice_number": "INV-1",
        "issue_date": "2026-07-03",
        "vendor_name": "Acme BV",
        "total_amount": "100.00",
        "currency": "EUR",
    },
    "confidence": 0.96,
    "notes": "",
}
SMUDGED = {
    "fields": {
        "invoice_number": None,
        "issue_date": "2026-07-29",
        "vendor_name": "Velocity Print",
        "total_amount": None,
        "currency": "USD",
    },
    "confidence": 0.2,
    "notes": "a stamp covers the invoice number; the total is illegible",
}
FROM_EXPERT = {
    "invoice_number": "VP-58812",
    "issue_date": "2026-07-29",
    "vendor_name": "Velocity Print & Mail",
    "total_amount": "2140.75",
    "currency": "USD",
    "notes": "read under the stamp at full zoom",
}


class FakeReply:
    """Just enough of an `AIMessage`: `.text`."""

    def __init__(self, text: str) -> None:
        self.text = text


class FakeLLM:
    """Answers by filename: 'smudged' reads badly, everything else reads well."""

    def __init__(self) -> None:
        self.questions: list[str] = []

    async def ainvoke(self, messages):
        blob = json.dumps(messages, default=str)
        if "image_url" not in blob:  # a scoping question, not a document
            self.questions.append(blob)
            return FakeReply("Transcribe exactly what is printed; do not guess.")
        return FakeReply(json.dumps(SMUDGED if "smudged" in blob else CLEAN))


class FakeTendem:
    url = "https://mcp.example.test/mcp"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        base_url="http://localhost/v1",
        api_key="test",
        ocr_model="test-vision",
        tendem_api_key="test",
        max_price=10.0,
        min_confidence=0.75,
    )


@pytest.fixture
def clean(tmp_path: Path) -> Path:
    path = tmp_path / "clean.png"
    Image.new("RGB", (40, 40), "white").save(path)
    return path


@pytest.fixture
def smudged(tmp_path: Path) -> Path:
    path = tmp_path / "smudged.png"
    Image.new("RGB", (40, 40), "white").save(path)
    return path


@pytest.fixture
def fake_llm(monkeypatch) -> FakeLLM:
    # Keep the real message shape, but make the fake able to tell the two
    # documents apart by name rather than by base64 payload.
    monkeypatch.setattr(
        extract_module, "as_data_url", lambda path: f"data:image/png;name={path.name}"
    )
    return FakeLLM()


@pytest.fixture
def process(settings, fake_llm):
    """The per-file function, wired the way `scripted_flow` wires it."""

    def _process(path: Path, journal: Journal | None = None):
        return scripted_process_single_file(
            settings,
            path,
            llm=fake_llm,
            tendem=FakeTendem(),
            journal=journal,
        )

    return _process


def fake_tendem_flow(monkeypatch, *events: TaskEvent) -> list[str]:
    """Stand in for the package: one task id, then the given events in order.

    Returns the list of briefs `prepare_task` was called with — empty means the
    run adopted an existing task instead of creating one.
    """
    briefs: list[str] = []
    queue = list(events)

    async def prepare_task(client, description, **kwargs):
        briefs.append(description)
        assert kwargs["files"], "the document must ride along with the brief"
        return "task-1"

    async def advance_task(client, task_id, **kwargs):
        event = queue.pop(0)
        if isinstance(event, BaseException):
            raise event
        return event

    monkeypatch.setattr(escalate_module, "prepare_task", prepare_task)
    monkeypatch.setattr(escalate_module, "advance_task", advance_task)
    return briefs


def result_event(payload: dict, price: str = "$3.00") -> TaskEvent:
    return TaskEvent(
        "result",
        "task-1",
        outcome=TaskOutcome(
            task_id="task-1",
            content=f"Here is the transcription:\n\n```json\n{json.dumps(payload)}\n```",
            price_paid=3.0,
            price_paid_formatted=price,
        ),
    )


async def test_a_confident_read_never_reaches_a_human(process, clean, monkeypatch):
    fake_tendem_flow(monkeypatch)  # any escalation would pop from an empty queue
    record = await process(clean)

    assert (record.file, record.source) == ("clean.png", "model")
    assert record.fields["invoice_number"] == "INV-1"


async def test_an_unreadable_document_is_transcribed_by_an_expert(
    process, clean, smudged, monkeypatch, tmp_path
):
    briefs = fake_tendem_flow(monkeypatch, result_event(FROM_EXPERT))
    records = [await process(clean), await process(smudged)]

    assert records[0].source == "model"
    escalated = records[1]
    assert escalated.source == "human"
    assert escalated.fields["total_amount"] == "2140.75"
    assert escalated.price == "$3.00"
    assert escalated.task_id == "task-1"

    # The brief must stand on its own — the expert sees only it and the file.
    assert "invoice_number" in briefs[0]
    assert "stamp covers the invoice number" in briefs[0]

    out = tmp_path / "extracted.csv"
    write_csv(records, out)
    rows = list(csv.DictReader(out.open()))
    assert [row["source"] for row in rows] == ["model", "human"]
    assert rows[1]["invoice_number"] == "VP-58812"
    assert "1 by a human expert ($3.00)" in summarize(records)


async def test_scoping_questions_are_answered_without_a_human(
    process, smudged, monkeypatch, fake_llm
):
    fake_tendem_flow(
        monkeypatch,
        TaskEvent("question", "task-1", text="Should we transcribe the stamp too?"),
        result_event(FROM_EXPERT),
    )
    record = await process(smudged)

    assert record.source == "human"
    assert fake_llm.questions, "the pipeline should answer Tendem itself"


async def test_an_over_cap_quote_costs_nothing_and_is_reported(
    process, smudged, monkeypatch
):
    fake_tendem_flow(
        monkeypatch,
        TaskEvent("over_budget", "task-1", text="scope…", price="$85.00"),
    )
    record = await process(smudged)

    assert record.source == "failed"
    assert record.price is None
    assert "$85.00" in record.notes


async def test_a_transport_failure_reaches_the_harness(process, smudged, monkeypatch):
    """Only `TendemError` becomes a row here; anything else must propagate so
    the harness can turn it into a `failed` row (tested in test_cli)."""
    fake_tendem_flow(monkeypatch, RuntimeError("connection reset"))

    with pytest.raises(RuntimeError, match="connection reset"):
        await process(smudged)


async def test_an_interrupted_run_resumes_instead_of_paying_twice(
    process, smudged, monkeypatch, tmp_path
):
    """The whole point: die mid-wait, restart, collect the work already bought."""
    journal_path = tmp_path / "tasks.jsonl"

    # Run one: the task is created, then the connection dies mid-wait.
    briefs = fake_tendem_flow(monkeypatch, RuntimeError("connection reset"))
    with pytest.raises(RuntimeError):
        await process(smudged, journal=Journal.load(journal_path))

    assert len(briefs) == 1, "the first run creates the task"
    orphans = Journal.load(journal_path).orphans()
    assert [o["task_id"] for o in orphans] == ["task-1"], "the id survived the crash"
    assert orphans[0]["file"] == "smudged.png"

    # Run two: a fresh process, a fresh journal read from disk.
    briefs = fake_tendem_flow(monkeypatch, result_event(FROM_EXPERT))
    record = await process(smudged, journal=Journal.load(journal_path))

    assert briefs == [], "no second task was created"
    assert record.source == "human"
    assert record.fields["total_amount"] == "2140.75"
    assert record.task_id == "task-1"
    assert Journal.load(journal_path).orphans() == [], "the task is closed out"


async def test_resume_survives_the_model_rewording_its_notes(
    process, smudged, monkeypatch, tmp_path
):
    """A rerun's notes are never phrased the same; adoption must not depend on them."""
    journal_path = tmp_path / "tasks.jsonl"

    fake_tendem_flow(monkeypatch, RuntimeError("connection reset"))
    with pytest.raises(RuntimeError):
        await process(smudged, journal=Journal.load(journal_path))

    monkeypatch.setitem(SMUDGED, "notes", "the stamp obscures the number, I think")
    briefs = fake_tendem_flow(monkeypatch, result_event(FROM_EXPERT))
    record = await process(smudged, journal=Journal.load(journal_path))

    assert briefs == [], "the same question about the same document"
    assert record.source == "human"


async def test_a_changed_question_does_not_adopt_an_old_task(
    process, smudged, monkeypatch, tmp_path
):
    """Re-scope the brief and the old task is answering something else."""
    journal_path = tmp_path / "tasks.jsonl"

    fake_tendem_flow(monkeypatch, RuntimeError("connection reset"))
    with pytest.raises(RuntimeError):
        await process(smudged, journal=Journal.load(journal_path))

    monkeypatch.setattr(escalate_module, "BRIEF", "A different brief entirely: {keys}")
    briefs = fake_tendem_flow(monkeypatch, result_event(FROM_EXPERT))
    await process(smudged, journal=Journal.load(journal_path))

    assert len(briefs) == 1, "a re-scoped brief needs its own task"


async def test_a_re_scanned_document_gets_its_own_task(
    process, smudged, monkeypatch, tmp_path
):
    """A file edited under the same name must not adopt the old task."""
    journal_path = tmp_path / "tasks.jsonl"

    fake_tendem_flow(monkeypatch, RuntimeError("connection reset"))
    with pytest.raises(RuntimeError):
        await process(smudged, journal=Journal.load(journal_path))

    Image.new("RGB", (40, 40), "black").save(smudged)  # a better scan arrives
    briefs = fake_tendem_flow(monkeypatch, result_event(FROM_EXPERT))
    await process(smudged, journal=Journal.load(journal_path))

    assert len(briefs) == 1, "different bytes, different task"


async def test_an_over_cap_quote_is_not_retried_forever(
    process, smudged, monkeypatch, tmp_path
):
    journal_path = tmp_path / "tasks.jsonl"
    fake_tendem_flow(
        monkeypatch, TaskEvent("over_budget", "task-1", text="scope…", price="$85.00")
    )
    await process(smudged, journal=Journal.load(journal_path))

    reloaded = Journal.load(journal_path)
    assert reloaded.orphans() == [], "walking away closes the task"

    briefs = fake_tendem_flow(monkeypatch, result_event(FROM_EXPERT))
    await process(smudged, journal=reloaded)
    assert len(briefs) == 1, "a re-run starts a fresh negotiation, not the old one"
