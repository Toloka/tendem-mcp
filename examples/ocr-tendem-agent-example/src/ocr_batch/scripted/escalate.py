"""Step two: whatever the model could not read, a human expert reads.

This is the whole human-in-the-loop integration — one `prepare_task` and a
loop over `advance_task`. Scoping, the price quote, auto-approval under the
cap, hours of waiting and the file upload all live inside `langchain_tendem`.

The only state kept here is the `task_id`, written to a `Journal` the moment a
task exists so an interrupted run can re-attach to it instead of paying for the
same transcription twice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from langchain_core.language_models import BaseChatModel
from langchain_tendem import Tendem, TendemError, advance_task, prepare_task

from ocr_batch.common.config import FIELDS, Settings
from ocr_batch.common.documents import document_sha, text_sha
from ocr_batch.common.journal import Journal
from ocr_batch.common.report import Record
from ocr_batch.scripted.extract import Extraction, clean_field, parse_json

log = logging.getLogger("ocr_batch")

#: Trips round the loop before we give up — a scoping question answered or a
#: `timeout` of waiting elapsed each count as one.
MAX_ROUNDS = 6

BRIEF = """Transcribe the attached scanned invoice ({filename}).

Our OCR model could not read it reliably. It reported: {notes}

Read the document and report these fields exactly as printed on it:
{field_list}

Rules: issue_date as YYYY-MM-DD, total_amount as digits only (e.g. 1234.56),
currency as an ISO 4217 code. If a field genuinely is not present in the
document, use null — do not guess.

Return your answer as a single JSON object with exactly these keys:
{keys}
plus a "notes" key describing anything that remained unreadable."""


@dataclass(frozen=True)
class HumanResult:
    """What came back from the expert (or why nothing did)."""

    task_id: str | None = None
    fields: dict[str, str | None] = field(default_factory=dict)
    notes: str = ""
    price: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True when we have a usable transcription."""
        return self.error is None and any(self.fields.values())


def build_tendem(settings: Settings) -> Tendem:
    """The client.

    The endpoint is the package's own business: it resolves `TENDEM_MCP_URL`
    from the environment itself and falls back to production, so pointing the
    whole pipeline at prestable takes no code here.
    """
    return Tendem(api_key=settings.tendem_api_key, max_price=settings.max_price)


def build_brief(item: Extraction) -> str:
    """The self-contained brief — the expert sees only this and the file."""
    return BRIEF.format(
        filename=item.path.name,
        notes=item.notes or "the document is too degraded to read",
        field_list="\n".join(f"- {name}" for name in FIELDS),
        keys=", ".join(FIELDS),
    )


def scope_sha() -> str:
    """A hash of *what we are asking for* — the template and the field list.

    Deliberately not a hash of the rendered brief: that embeds the model's
    free-text notes, which are differently worded on every run, so an existing
    task would never be recognised as answering the same question. Edit the
    template or `FIELDS` and old tasks stop being adopted, which is the point.
    """
    return text_sha(BRIEF + "|".join(FIELDS))


async def escalate(
    tendem: Tendem,
    llm: BaseChatModel,
    item: Extraction,
    *,
    max_price: float,
    timeout: float,
    journal: Journal | None = None,
    conversation_id: str | None = None,
    task_id: str | None = None,
    scope: str | None = None,
) -> HumanResult:
    """Hand one unreadable document to a human expert and wait for the answer.

    Re-attaches to an existing task when one is passed in (the agentic flow's
    fallback does this) or when the journal has a live one for this exact
    document; otherwise creates one and records it at once. `scope` labels the
    journal entry, so the flow that owns the document recognises the task on
    its next run. Everything after creation is idempotent against the
    `task_id`, so this is safe to call again after any interruption.
    """
    brief = build_brief(item)
    sha, scope = document_sha(item.path), scope or scope_sha()
    try:
        task_id = task_id or (
            journal.adopt(sha, url=tendem.url, scope_sha=scope) if journal else None
        )
        if task_id:
            log.info("%s: resuming task %s", item.path.name, task_id)
        else:
            task_id = await prepare_task(
                tendem,
                brief,
                files=[str(item.path)],
                conversation_id=conversation_id,
            )
            # Next statement, no awaits in between: this is the window where a
            # task exists server-side but not on disk.
            if journal:
                journal.created(
                    sha,
                    item.path.name,
                    task_id,
                    url=tendem.url,
                    scope_sha=scope,
                    conversation_id=conversation_id,
                )
            log.info("%s → human expert (task %s)", item.path.name, task_id)

        result, outcome = await _drive(
            tendem, llm, task_id, brief, max_price=max_price, timeout=timeout
        )
        if journal:
            journal.finished(sha, task_id, outcome, result.price)
        return result
    except TendemError as exc:
        return HumanResult(error=str(exc))


async def _drive(
    tendem: Tendem,
    llm: BaseChatModel,
    task_id: str,
    brief: str,
    *,
    max_price: float,
    timeout: float,
) -> tuple[HumanResult, str]:
    """Move one existing task forward until it is done, or we give up.

    Returns the outcome alongside the kind of ending, which decides whether the
    journal closes the task or leaves it open for the next run.
    """
    reply: str | None = None
    for _ in range(MAX_ROUNDS):
        event = await advance_task(
            tendem, task_id, max_price=max_price, timeout=timeout, reply=reply
        )
        if event.kind == "result":
            return _from_markdown(task_id, event.outcome), "result"
        if event.kind == "question":
            log.info("%s: Tendem asks — %s", task_id, event.text)
            reply = await _answer(llm, brief, event.text or "")
            continue
        if event.kind == "over_budget":
            return (
                HumanResult(
                    task_id,
                    error=f"quoted {event.price}, above the ${max_price:.2f} cap",
                ),
                "over_budget",
            )
        reply = None  # "pending": the time budget elapsed, keep waiting
    return HumanResult(task_id, error="too many scoping rounds"), "exhausted"


def _from_markdown(task_id: str, outcome) -> HumanResult:
    """Pull the fields out of the expert's deliverable."""
    payload = parse_json(outcome.content or "")
    inner = payload.get("fields")
    values = inner if isinstance(inner, dict) else payload
    fields = {name: clean_field(values.get(name)) for name in FIELDS}
    if not any(fields.values()):
        return HumanResult(
            task_id,
            notes=(outcome.content or "")[:500],
            price=outcome.price_paid_formatted,
            error="the expert's answer carried none of the requested fields",
        )
    return HumanResult(
        task_id=task_id,
        fields=fields,
        notes=str(payload.get("notes") or ""),
        price=outcome.price_paid_formatted,
    )


def human_record(item: Extraction, result: HumanResult) -> Record:
    """The expert's answer as a CSV row — or a `failed` row saying why not."""
    if not result.ok:
        log.warning("%s: failed — %s", item.path.name, result.error)
        return failed_record(item, result.error or "no result", result.task_id)
    log.info("%s: transcribed by a human expert (%s)", item.path.name, result.price)
    return Record(
        file=item.path.name,
        source="human",
        fields=result.fields,
        confidence=1.0,
        price=result.price,
        task_id=result.task_id,
        notes=result.notes,
    )


def failed_record(item: Extraction, why: str, task_id: str | None = None) -> Record:
    return Record(
        file=item.path.name,
        source="failed",
        fields=item.fields,
        confidence=item.confidence,
        task_id=task_id,
        notes="; ".join(part for part in (why, item.notes) if part),
    )


async def _answer(llm: BaseChatModel, brief: str, question: str) -> str:
    """Answer Tendem's scoping question from the brief — no human needed."""
    system = (
        "You are the agent that submitted the task below to an expert "
        "service. Answer their question in one or two sentences using only "
        "the brief; if the brief does not cover it, say so and tell them to "
        "use their best judgement."
    )
    response = await llm.ainvoke(
        [
            ("system", system),
            ("human", f"Task brief:\n{brief}\n\nTheir question:\n{question}"),
        ]
    )
    return response.text.strip() or "Please use your best judgement."
