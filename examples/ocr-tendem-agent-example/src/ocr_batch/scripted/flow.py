"""The scripted flow: the same job as the agent, decided by plain code.

`ocr-agentic` hands five tools to one model and lets it decide. This flow is
deterministic: a vision call extracts the fields, a confidence gate in plain
Python decides what a human should see, and `escalate` — one `prepare_task`
and a loop over `advance_task` — drives the hand-off. No tool calling, no
agent loop: the same package used as an ordinary async API.

Worth having both: this flow is the one to trust with money at scale (nothing
depends on a model choosing correctly), while the agentic one is shorter and
adapts to whatever the document turns out to need.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langchain_tendem import Tendem

from ocr_batch.common.config import Settings
from ocr_batch.common.journal import Journal
from ocr_batch.common.report import Record
from ocr_batch.scripted.escalate import build_tendem, escalate, human_record
from ocr_batch.scripted.extract import Extraction, extract

log = logging.getLogger("ocr_batch")


def scripted_flow(
    settings: Settings,
    llm: BaseChatModel,
    *,
    journal: Journal | None,
    conversation_id: str | None,
) -> Callable[[Path], Awaitable[Record]]:
    """The harness plug: wire the shared pieces to the per-document function."""
    tendem = build_tendem(settings)
    log.info(
        "tendem endpoint: %s (cap $%.2f per document)", tendem.url, settings.max_price
    )

    async def process(path: Path) -> Record:
        return await scripted_process_single_file(
            settings,
            path,
            llm=llm,
            tendem=tendem,
            journal=journal,
            conversation_id=conversation_id,
        )

    return process


async def scripted_process_single_file(
    settings: Settings,
    path: Path,
    *,
    llm: BaseChatModel,
    tendem: Tendem,
    journal: Journal | None = None,
    conversation_id: str | None = None,
    timeout: float = 6 * 3600.0,
) -> Record:
    """Read one document with the model; below the gate, a human reads it.

    This is the whole deterministic flow, in reading order. The escalation is
    one function call that behaves like any other async API — scoping, the
    quote, approval under the cap, the file upload and the hours of waiting
    all happen inside it, and the `timeout` bounds one wait on the expert.
    """
    item = await extract(llm, path)
    ok = item.is_confident(settings.min_confidence)
    log.info(
        "%s: confidence %.2f — %s",
        path.name,
        item.confidence,
        "ok" if ok else "needs a human",
    )
    if ok:
        return _from_model(item)
    result = await escalate(
        tendem,
        llm,
        item,
        max_price=settings.max_price,
        timeout=timeout,
        journal=journal,
        conversation_id=conversation_id,
    )
    return human_record(item, result)


def _from_model(item: Extraction) -> Record:
    return Record(
        file=item.path.name,
        source="model",
        fields=item.fields,
        confidence=item.confidence,
        notes=item.notes,
    )
