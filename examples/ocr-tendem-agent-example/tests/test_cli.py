"""The shared harness: what it remembers, what it emits, what it refuses to block on.

Both commands run through this code, so it is tested once, with the flow (the
per-document function) faked out.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from click.testing import CliRunner

from ocr_batch import cli
from ocr_batch.common.journal import Journal
from ocr_batch.common.report import Record, read_csv, write_csv


def a_csv(path: Path) -> None:
    write_csv(
        [
            Record(file="done.png", source="model", confidence=0.96),
            Record(file="expert.png", source="human", confidence=1.0, price="$3.00"),
            Record(file="stuck.png", source="processing", task_id="task-1"),
        ],
        path,
    )


def an_inbox(tmp_path: Path, *names: str) -> Path:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for name in names:
        (inbox / name).write_bytes(b"x")
    return inbox


def recording_flow(seen: dict, processed: list[str] | None = None):
    """A stand-in flow: records what the harness wires in and what it asks for."""

    def flow(settings, llm, *, journal, conversation_id):
        seen.update(
            settings=settings, llm=llm, journal=journal, conversation_id=conversation_id
        )

        async def process(path: Path) -> Record:
            if processed is not None:
                processed.append(path.name)
            return Record(file=path.name, source="model", confidence=0.99)

        return process

    return flow


class Recorder:
    """Duck-typed Sink: keeps rows in memory, in arrival order."""

    def __init__(self) -> None:
        self.rows: list[Record] = []

    def update(self, records: list[Record]) -> None:
        self.rows.extend(records)


# ------------------------------------------------------------------- the sink


def test_a_restarted_watch_keeps_the_rows_it_already_had(tmp_path):
    out = tmp_path / "extracted.csv"
    a_csv(out)

    sink = cli.Sink(out, resume=True)
    assert set(sink.rows) == {"done.png", "expert.png", "stuck.png"}

    # A new batch must extend the file, not truncate it to its own rows.
    sink.update([Record(file="fresh.png", source="model", confidence=0.9)])
    assert set(cli.Sink(out, resume=True).rows) == {
        "done.png",
        "expert.png",
        "stuck.png",
        "fresh.png",
    }


def test_only_finished_rows_are_skipped_on_restart(tmp_path):
    out = tmp_path / "extracted.csv"
    a_csv(out)

    # 'stuck.png' is deliberately absent: it has a live task to resume.
    assert cli.Sink(out, resume=True).settled() == {"done.png", "expert.png"}


def test_a_placeholder_row_is_replaced_by_the_real_one(tmp_path):
    out = tmp_path / "extracted.csv"
    sink = cli.Sink(out)
    sink.update([Record(file="a.png", source="processing")])
    sink.update([Record(file="a.png", source="human", price="$3.00")])

    rows = cli.Sink(out, resume=True).rows
    assert len(rows) == 1
    assert rows["a.png"].source == "human"


# ------------------------------------------------------------------ the batch


async def test_each_row_is_written_as_it_lands(tmp_path):
    """A document waiting on a human must not hold the finished rows in memory.

    The slow document only finishes once the fast one's row has already been
    handed over — if rows were collected until the end of the batch, this
    would deadlock (and time out) instead.
    """
    fast_row_landed = asyncio.Event()

    async def process(path: Path) -> Record:
        if path.name == "slow.png":  # stand-in for hours of human work
            await asyncio.wait_for(fast_row_landed.wait(), timeout=5)
        return Record(file=path.name, source="model", confidence=0.99)

    class Trigger(Recorder):
        def update(self, records):
            super().update(records)
            if any(r.file == "fast.png" and r.source == "model" for r in records):
                fast_row_landed.set()

    sink = Trigger()
    await cli._batch([tmp_path / "fast.png", tmp_path / "slow.png"], process, sink)

    placeholders = [r.file for r in sink.rows if r.source == "processing"]
    assert placeholders == ["fast.png", "slow.png"], "announced up front"
    settled = [r for r in sink.rows if r.source != "processing"]
    assert [record.file for record in settled] == ["fast.png", "slow.png"]
    assert all(record.source == "model" for record in settled), (
        "the slow document finished normally — nothing timed out waiting"
    )


async def test_one_document_blowing_up_loses_only_its_own_row(tmp_path):
    async def process(path: Path) -> Record:
        if path.name == "bad.png":
            raise RuntimeError("boom")
        return Record(file=path.name, source="model", confidence=0.99)

    sink = Recorder()
    await cli._batch([tmp_path / "good.png", tmp_path / "bad.png"], process, sink)

    by_file = {record.file: record for record in sink.rows}
    assert by_file["good.png"].source == "model"
    assert by_file["bad.png"].source == "failed"
    assert "boom" in by_file["bad.png"].notes


# ---------------------------------------------------------------- the harness


def test_rows_are_on_disk_while_the_batch_is_still_running(tmp_path, monkeypatch):
    """The CSV must hold every settled row mid-batch, not only at the end."""
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("TENDEM_API_KEY", "test")
    inbox = an_inbox(tmp_path, "a.png", "b.png")
    out = tmp_path / "extracted.csv"

    def flow(settings, llm, *, journal, conversation_id):
        async def process(path: Path) -> Record:
            if path.name == "b.png":
                # Wait until a.png's row is on disk — mid-batch, by definition.
                for _ in range(200):
                    if any(
                        r.file == "a.png" and r.source == "model" for r in read_csv(out)
                    ):
                        break
                    await asyncio.sleep(0.01)
                else:
                    raise AssertionError("a.png never landed while b.png ran")
            return Record(file=path.name, source="model", confidence=0.99)

        return process

    code = cli.run(prog="ocr-agentic", flow=flow, inbox=inbox, out=out)

    assert code == 0
    assert {r.file: r.source for r in read_csv(out)} == {
        "a.png": "model",
        "b.png": "model",
    }


def test_a_finished_document_is_not_paid_for_twice(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("TENDEM_API_KEY", "test")
    inbox = an_inbox(tmp_path, "done.png", "new.png")
    out = tmp_path / "extracted.csv"
    write_csv([Record(file="done.png", source="human", price="$3.00")], out)

    processed: list[str] = []
    flow = recording_flow({}, processed)

    assert cli.run(prog="p", flow=flow, inbox=inbox, out=out) == 0
    assert processed == ["new.png"], "done.png was skipped"

    processed.clear()
    assert cli.run(prog="p", flow=flow, inbox=inbox, out=out, force=True) == 0
    assert sorted(processed) == ["done.png", "new.png"]


def test_the_journal_lands_next_to_the_csv(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("TENDEM_API_KEY", "test")
    inbox = an_inbox(tmp_path, "a.png")
    seen: dict = {}

    code = cli.run(
        prog="ocr-agentic",
        flow=recording_flow(seen),
        inbox=inbox,
        out=tmp_path / "run.csv",
    )

    assert code == 0
    assert seen["journal"].path == tmp_path / "run.tasks.jsonl"
    assert seen["conversation_id"].startswith("ocr-agentic:")


def test_the_journal_location_can_be_overridden(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("TENDEM_API_KEY", "test")
    inbox = an_inbox(tmp_path, "a.png")
    seen: dict = {}
    elsewhere = tmp_path / "state" / "mine.jsonl"

    code = cli.run(
        prog="p",
        flow=recording_flow(seen),
        inbox=inbox,
        out=tmp_path / "out.csv",
        state=elsewhere,
    )

    assert code == 0
    assert seen["journal"].path == elsewhere


def test_both_commands_share_the_harness(tmp_path, monkeypatch):
    """`ocr-agentic` and `ocr-scripted` differ only in the flow they plug in."""
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("TENDEM_API_KEY", "test")
    inbox = an_inbox(tmp_path, "a.png")

    for entry, name, flow_attr in (
        (cli.agentic, "ocr-agentic", "agentic_flow"),
        (cli.scripted, "ocr-scripted", "scripted_flow"),
    ):
        seen: dict = {}
        monkeypatch.setattr(cli, flow_attr, recording_flow(seen))
        out = tmp_path / f"{name}.csv"

        result = CliRunner().invoke(entry, [str(inbox), "-o", str(out)])
        assert result.exit_code == 0, result.output
        assert seen["conversation_id"].startswith(f"{name}:")
        assert [r.file for r in read_csv(out)] == ["a.png"]


def test_orphans_are_reported_with_what_it_takes_to_recover(tmp_path, caplog):
    journal = Journal.load(tmp_path / "tasks.jsonl")
    journal.created("sha1", "velocity-print.png", "task-1", url="u", scope_sha="s")

    with caplog.at_level(logging.WARNING, logger="ocr_batch"):
        cli._report_orphans(journal, tmp_path / "tasks.jsonl")

    message = caplog.text
    assert "velocity-print.png" in message
    assert "task-1" in message
    assert "still billable" in message


def test_watch_does_not_block_while_a_batch_waits_on_a_human(
    tmp_path, monkeypatch, caplog
):
    """A three-hour expert must not stop the folder from being watched."""
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("TENDEM_API_KEY", "test")
    inbox = an_inbox(tmp_path, "a.png")
    os.utime(inbox / "a.png", (0, 0))  # old enough to pass the quiet-file check

    running = asyncio.Event()

    async def never_finishes(*args, **kwargs):
        running.set()
        await asyncio.Event().wait()  # a batch stuck on human work

    polls = 0
    real_sleep = asyncio.sleep

    async def fake_sleep(_seconds):
        nonlocal polls
        polls += 1
        if polls >= 3:
            raise KeyboardInterrupt  # what Ctrl-C does to the loop
        await real_sleep(0)  # hand control to the background batch

    monkeypatch.setattr(cli, "_batch", never_finishes)
    monkeypatch.setattr(cli.asyncio, "sleep", fake_sleep)

    with caplog.at_level(logging.INFO, logger="ocr_batch"):
        code = cli.run(
            prog="p",
            flow=recording_flow({}),
            inbox=inbox,
            out=tmp_path / "out.csv",
            watch=0.01,
        )

    assert code == 0
    assert running.is_set(), "the batch started"
    assert polls >= 2, "the loop kept polling while the batch was still waiting"
