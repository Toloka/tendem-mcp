"""One agent, one document: read it, or get a human expert to read it.

This is the whole application: a prompt, the scan, and five tools —
`report_fields` to finish, and the four `langchain_tendem` tools to delegate
to a vetted human when the scan defeats it. Nothing here polls, waits,
approves a quote, uploads a file or parses a deliverable: the package does
all of that. The deterministic rails around the tools — no duplicate tasks,
no lost ids, no dead-end loops — live in `guards.py`, so what is left here is
the loop itself.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_tendem import Tendem, tendem_tools

from ocr_batch.agentic.guards import (
    close_out,
    find_price,
    find_task_id,
    guarded_tendem_tools,
    report_tool,
)
from ocr_batch.common.config import FIELDS, Settings
from ocr_batch.common.documents import as_data_url, document_sha, text_sha
from ocr_batch.common.journal import Journal
from ocr_batch.common.report import Record
from ocr_batch.scripted.escalate import escalate, human_record
from ocr_batch.scripted.extract import Extraction

log = logging.getLogger("ocr_batch")

PROMPT = """You transcribe scanned invoices. You are shown one scan; report
what is printed on it — never guess, never complete a value you can only
partly see. A wrong value costs far more than an admitted gap.

If every field is legible, call `report_fields` with source="model" and stop.

If anything is illegible, a human expert can read it for you:
1. `create_human_task` — the brief must stand alone (the expert sees only that
   text and the file, never this conversation): name the {n} fields, the exact
   JSON to return, the formatting rules below, and what you could not read.
   Pass the document's local path in `file_paths`.
2. `check_human_task`, answering anything it asks with `reply_to_human_task`.
   A quote within the budget is approved for you automatically.
3. `wait_for_human_result` — this takes minutes to hours and costs you nothing
   to wait. Re-call it if it returns IN PROGRESS.
4. `report_fields` with the expert's values and source="human".

If a quote exceeds the budget cap, or the expert cannot read it either, call
`report_fields` with whatever you do have, source="model", and say so in notes.

Formatting: issue_date as YYYY-MM-DD, total_amount digits only (1234.56),
currency an ISO 4217 code, null for anything genuinely unreadable."""


def scope_sha() -> str:
    """Hash of the question we are asking — the prompt and the field list."""
    return text_sha(PROMPT + "|".join(FIELDS))


def agentic_flow(
    settings: Settings,
    llm: BaseChatModel,
    *,
    journal: Journal | None,
    conversation_id: str | None,
) -> Callable[[Path], Awaitable[Record]]:
    """The harness plug: wire the shared pieces to the per-document function."""
    endpoint = Tendem(api_key=settings.tendem_api_key, max_price=settings.max_price).url
    log.info(
        "tendem endpoint: %s (cap $%.2f per document)", endpoint, settings.max_price
    )

    async def process(path: Path) -> Record:
        return await agentic_process_single_file(
            settings,
            path,
            llm=llm,
            journal=journal,
            conversation_id=conversation_id,
            endpoint=endpoint,
        )

    return process


async def agentic_process_single_file(
    settings: Settings,
    path: Path,
    *,
    llm: BaseChatModel,
    journal: Journal | None = None,
    conversation_id: str | None = None,
    endpoint: str = "",
) -> Record:
    """Run the loop over one document and return its CSV row."""
    reported: dict[str, Any] = {}
    exhausted = {"exhausted": False}
    tools = [
        report_tool(
            reported,
            min_confidence=settings.min_confidence,
            exhausted=exhausted,
        )
    ]
    sha = document_sha(path)
    client = Tendem(api_key=settings.tendem_api_key, max_price=settings.max_price)
    tools += guarded_tendem_tools(
        tendem_tools(
            client=client,
            max_price=settings.max_price,
            conversation_id=conversation_id,
        ),
        journal,
        sha=sha,
        file=path.name,
        endpoint=endpoint,
        scope=scope_sha(),
        conversation_id=conversation_id,
        exhausted=exhausted,
    )

    resume = (
        journal.adopt(sha, url=endpoint, scope_sha=scope_sha()) if journal else None
    )
    if resume:
        log.info("%s: resuming task %s", path.name, resume)

    prompt = PROMPT.format(n=len(FIELDS))
    agent = create_agent(llm, tools=tools, system_prompt=prompt)
    # Read → escalate → scope → wait → report, with room to spare.
    state = await agent.ainvoke(
        {"messages": [_ask(path, resume)]}, config={"recursion_limit": 40}
    )

    seen = "\n".join(str(getattr(m, "content", "")) for m in state["messages"])

    if not reported and not exhausted["exhausted"]:
        # The agent finished without an accepted report — it answered in prose,
        # or wandered off. The rails do not argue with it: the deterministic
        # hand-off buys a human, resuming the agent's task if it had created
        # one, and journaling under the agent's scope so the next run — and the
        # agent itself — still recognises the task as this document's.
        log.warning(
            "%s: the agent never reported — a human finishes the job", path.name
        )
        unread = Extraction(
            path=path,
            fields=dict.fromkeys(FIELDS),
            confidence=0.0,
            notes="the agent could not produce a report",
        )
        result = await escalate(
            client,
            llm,
            unread,
            max_price=settings.max_price,
            timeout=6 * 3600.0,  # one bounded wait on the expert
            journal=journal,
            conversation_id=conversation_id,
            task_id=resume or find_task_id(seen),
            scope=scope_sha(),
        )
        return human_record(unread, result)

    task_id, price = resume or find_task_id(seen), find_price(seen)
    record = Record.from_report(
        path.name,
        reported,
        task_id=task_id,
        price=price,
        # Observed: told to null out an illegible field, this model fills it in
        # anyway and lowers `confidence` instead. So the number decides whether
        # the row may claim to be a reading — the prompt cannot be trusted to.
        min_confidence=settings.min_confidence,
    )
    close_out(journal, sha, record, seen)
    extra = f" ({price})" if price else ""
    if record.source == "failed" and record.notes:
        extra = f" — {record.notes[:90]}"
    log.info("%s: %s%s", path.name, record.source, extra)
    return record


def _ask(path: Path, resume: str | None) -> HumanMessage:
    """The scan itself, plus the one fact a restarted run needs to know."""
    already = (
        f"\n\nYou already created task_id='{resume}' for this document. Do NOT "
        "create another — continue with check_human_task / wait_for_human_result."
        if resume
        else ""
    )
    return HumanMessage(
        content=[
            {
                "type": "text",
                "text": f"Transcribe this scan: {path.name}\nLocal path, for "
                f"attaching to a human task: {path.resolve()}{already}",
            },
            {"type": "image_url", "image_url": {"url": as_data_url(path)}},
        ]
    )
