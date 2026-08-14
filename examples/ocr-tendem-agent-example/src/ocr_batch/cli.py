"""One harness, two brains: `ocr-agentic` and `ocr-scripted`.

Everything about a *batch* is here and shared — folder discovery, skipping
rows already paid for, `--watch`, the CSV sink, the task journal and its
orphan report. The two commands differ in a single function of the same
shape, `(path) -> Record`:

* `ocr-agentic` plugs in `agentic_process_single_file` — an agent decides.
* `ocr-scripted` plugs in `scripted_process_single_file` — plain code decides.

Concurrency is not managed here either: the cap on concurrent model calls
travels inside the shared LLM (see `common/llm.py`), so a document waiting hours on
a human expert holds no slot in anything.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import click

from ocr_batch.agentic import agentic_flow
from ocr_batch.common.config import Settings
from ocr_batch.common.documents import discover
from ocr_batch.common.journal import Journal
from ocr_batch.common.llm import build_llm
from ocr_batch.common.report import Record, Sink, failure_reason
from ocr_batch.scripted import scripted_flow

log = logging.getLogger("ocr_batch")


def harness_options(default_out: Path) -> Callable:
    """The flag set both commands share — they differ only in the flow."""
    decorators = [
        click.argument(
            "inbox",
            type=click.Path(exists=True, file_okay=False, path_type=Path),
            default=Path("documents/inbox"),
            required=False,
        ),
        click.option(
            "-o",
            "--out",
            type=click.Path(path_type=Path),
            default=default_out,
            show_default=True,
            help="CSV to write.",
        ),
        click.option(
            "--state",
            type=click.Path(path_type=Path),
            default=None,
            help=(
                "Task journal, so an interrupted run resumes instead of paying "
                "twice (default: alongside --out, as *.tasks.jsonl)."
            ),
        ),
        click.option(
            "--max-price",
            type=float,
            default=None,
            help="Spend cap in USD per escalated document (default: 10).",
        ),
        click.option(
            "--min-confidence",
            type=float,
            default=None,
            help="Below this a human reads it (default: OCR_MIN_CONFIDENCE, 0.95).",
        ),
        click.option(
            "--force",
            is_flag=True,
            help="Reprocess documents already finished in the CSV (costs money again).",
        ),
        click.option(
            "--concurrency",
            type=int,
            default=4,
            show_default=True,
            help="Concurrent model calls — human waits are unbounded.",
        ),
        click.option(
            "--watch",
            type=float,
            is_flag=False,
            flag_value=10.0,
            default=None,
            metavar="SECONDS",
            help=(
                "Keep watching the folder and process new documents as they "
                "land (default poll: every 10s)."
            ),
        ),
        click.option("-v", "--verbose", is_flag=True, help="Log the package's work."),
    ]

    def apply(command: Callable) -> Callable:
        for decorator in reversed(decorators):
            command = decorator(command)
        return command

    return apply


@click.command("ocr-agentic")
@harness_options(Path("documents/extracted.csv"))
def agentic(**options) -> None:
    """Read INBOX with an LLM agent; whatever defeats it, a human expert
    reads via Tendem. Both land in the same CSV."""
    sys.exit(run(prog="ocr-agentic", flow=agentic_flow, **options))


@click.command("ocr-scripted")
@harness_options(Path("documents/scripted.csv"))
def scripted(**options) -> None:
    """The same job with the decisions in plain code: an LLM read, a
    confidence gate, and a human expert via Tendem below the gate."""
    sys.exit(run(prog="ocr-scripted", flow=scripted_flow, **options))


def run(
    *,
    prog: str,
    flow: Callable[..., Callable[[Path], Awaitable[Record]]],
    inbox: Path,
    out: Path,
    state: Path | None = None,
    max_price: float | None = None,
    min_confidence: float | None = None,
    force: bool = False,
    concurrency: int = 4,
    watch: float | None = None,
    verbose: bool = False,
) -> int:
    """Wire the flow to the shared pieces, then run once or forever."""
    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)
    log.setLevel(logging.INFO)
    if verbose:
        # Watch the package drive the task: created, uploaded, approved, result.
        logging.getLogger("langchain_tendem").setLevel(logging.INFO)

    settings = Settings.from_env()
    overrides = {"max_price": max_price, "min_confidence": min_confidence}
    settings = dataclasses.replace(
        settings,
        **{name: value for name, value in overrides.items() if value is not None},
    )

    state = state or out.with_suffix(".tasks.jsonl")
    journal = Journal.load(state)
    llm = build_llm(settings, max_concurrency=concurrency)
    process = flow(
        settings,
        llm,
        journal=journal,
        # One conversation per run: Tendem uses it to correlate the batch.
        conversation_id=f"{prog}:{datetime.now(UTC).isoformat(timespec='seconds')}",
    )

    try:
        asyncio.run(
            _watch(inbox, out, force, watch, process)
            if watch
            else _once(inbox, out, force, process)
        )
    except KeyboardInterrupt:
        log.info("stopped")
    finally:
        _report_orphans(journal, state)
    return 0


async def _once(
    inbox: Path,
    out: Path,
    force: bool,
    process: Callable[[Path], Awaitable[Record]],
) -> None:
    found = discover(inbox)
    if not found:
        log.info("no documents in %s", inbox)
        return

    # A finished document is not re-read and — more to the point — not sent to a
    # human a second time. `--force` if that is really what you want.
    sink = Sink(out, resume=True)
    done = set() if force else sink.settled()
    documents = [path for path in found if path.name not in done]
    if done:
        log.info("%d document(s) already finished in %s", len(done), out)
    if not documents:
        log.info("nothing to do — pass --force to reprocess")
        return

    log.info("reading %d document(s) from %s", len(documents), inbox)
    await _batch(documents, process, sink)


async def _watch(
    inbox: Path,
    out: Path,
    force: bool,
    every: float,
    process: Callable[[Path], Awaitable[Record]],
) -> None:
    """Process what is there, then whatever lands next.

    Batches run in the background, so a document waiting hours on a human never
    blocks the folder from being picked up again.
    """
    log.info("watching %s (every %.0fs) — Ctrl-C to stop", inbox, every)
    sink = Sink(out, resume=True)
    seen = set() if force else sink.settled()
    if seen:
        log.info("%d document(s) already finished in %s", len(seen), out)

    inflight: set[asyncio.Task] = set()
    while True:
        batch = [path for path in _quiet(discover(inbox)) if path.name not in seen]
        if batch:
            seen.update(path.name for path in batch)
            log.info("picked up %d new document(s)", len(batch))
            task = asyncio.create_task(_batch(batch, process, sink))
            inflight.add(task)
            task.add_done_callback(inflight.discard)
            task.add_done_callback(_log_failure)
        await asyncio.sleep(every)


async def _batch(
    documents: list[Path],
    process: Callable[[Path], Awaitable[Record]],
    sink: Sink,
) -> None:
    """Run the per-document function over a batch; every outcome is a row.

    Every document gets a `processing` row up front, replaced the moment it
    settles — the CSV always says what is being worked on. A row still saying
    `processing` after an interrupt is accurate too: the human task keeps
    running server-side, and the next run resumes it.
    """
    sink.update([Record(file=path.name, source="processing") for path in documents])

    async def settle(path: Path) -> None:
        try:
            record = await process(path)
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except BaseException as exc:  # noqa: BLE001 - one document, one row
            record = _failed(path, exc)
        # Straight into the CSV: a document waiting hours on an expert never
        # holds the finished rows hostage.
        sink.update([record])

    await asyncio.gather(*(settle(path) for path in documents))


def _failed(path: Path, exc: BaseException) -> Record:
    reason = failure_reason(exc)
    log.error("%s: failed — %s", path.name, reason)
    return Record(file=path.name, source="failed", notes=reason)


def _log_failure(task: asyncio.Task) -> None:
    """A background batch must never fail silently."""
    if task.cancelled():
        return
    if exc := task.exception():
        log.error("a batch failed: %s", exc)


def _report_orphans(journal: Journal, state: Path) -> None:
    """Tasks we created and never collected — money in flight."""
    orphans = journal.orphans()
    if not orphans:
        return
    listing = ", ".join(f"{o.get('file')} ({o.get('task_id')})" for o in orphans)
    log.warning(
        "%d task(s) created and not collected: %s\n"
        "They are still running and still billable. Re-run to resume them "
        "(the ids are in %s), or cancel them at agent.tendem.ai.",
        len(orphans),
        listing,
        state,
    )


def _quiet(paths: list[Path], *, quiet_seconds: float = 2.0) -> list[Path]:
    """Skip files still being written — a copy in flight is not a document."""
    now = time.time()
    return [path for path in paths if now - path.stat().st_mtime > quiet_seconds]


if __name__ == "__main__":
    agentic()
