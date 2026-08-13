"""The agent-facing surface: four tools over one task state machine.

``create_human_task`` is deliberately thin and deterministic (create + upload
+ announce, no polling — seconds, minimising the only non-idempotent window).
Everything after it is stateless against the ``task_id`` and survives
restarts and checkpoint replays: ``check_human_task`` polls and forwards
whatever needs the agent (messages, over-cap quotes) while auto-approving
under the cap; ``reply_to_human_task`` answers and resumes the same polling;
``wait_for_human_result`` idempotently waits out execution.

Approval is never interactive: quotes at or below the configured cap are
approved automatically — the cap is the consent, given in advance. Expected
business outcomes (over-budget, empty balance, quiet progress) come back as
informative strings, not exceptions, so any agent loop can recover.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from langchain_tendem.constants import (
    DEFAULT_SCOPING_TIMEOUT_SECONDS,
    DEFAULT_WAIT_TIMEOUT_SECONDS,
)
from langchain_tendem.errors import TendemError, TopUpRequiredError


def _render_error(exc: TendemError) -> str:
    """Business failures as strings the calling agent can act on."""
    if isinstance(exc, TopUpRequiredError):
        return (
            "NOT EXECUTED — the Tendem account balance is too low. "
            f"{exc} A human must add funds; paying the task-bound top-up "
            "URL auto-approves this task."
        )
    return f"NOT COMPLETED — {exc}"


class CreateTaskInput(BaseModel):
    """Arguments for ``create_human_task``."""

    request: str = Field(
        description=(
            "Complete, self-contained brief for the human expert: what to do "
            "or verify, every relevant fact inline, and what a good answer "
            "looks like. The expert sees only this text and the attached "
            "files."
        )
    )
    file_paths: list[str] | None = Field(
        default=None,
        description=(
            "Local paths of input files the expert needs; uploaded under "
            "their basenames. Reference them by name in the request."
        ),
    )


class TaskIdInput(BaseModel):
    """Arguments for ``check_human_task`` / ``wait_for_human_result``."""

    task_id: str = Field(description="The task_id returned by create_human_task.")


class ReplyInput(BaseModel):
    """Arguments for ``reply_to_human_task``."""

    task_id: str = Field(description="The task_id returned by create_human_task.")
    reply: str = Field(description="Your answer to the service's last message.")


def _event_text(event: Any, cap: float) -> str:
    """Render a runner ``TaskEvent`` for the calling agent."""
    task_id = event.task_id
    if event.kind == "result":
        return event.outcome.render()
    if event.kind == "question":
        return (
            f"MESSAGE from the expert service (task_id='{task_id}'):\n"
            f"{event.text}\n\n"
            "If this needs an answer, call "
            f"reply_to_human_task(task_id='{task_id}', reply='...'). If it "
            "already answers your request, you are done."
        )
    if event.kind == "approved":
        return (
            f"STARTED — task '{task_id}' approved at {event.price}. A human "
            "expert is now working; this takes minutes to hours. Call "
            f"wait_for_human_result(task_id='{task_id}') to collect the "
            "result (re-call it if interrupted — nothing is lost)."
        )
    if event.kind == "over_budget":
        return (
            f"QUOTE EXCEEDS CAP — task '{task_id}' was quoted at "
            f"{event.price}, above the ${cap:.2f} budget cap. Nothing has "
            f"been charged.\n\nProposed scope:\n{event.text}\n\n"
            "Decide: (1) narrow the scope — call "
            f"reply_to_human_task(task_id='{task_id}', reply='<a concrete "
            "reduction of the scope above>'); the quote is voided and a new "
            "one will follow. Or (2) conclude it is not worth a human at "
            "this price: stop here and tell your user."
        )
    return (
        f"IN PROGRESS — task '{task_id}' is still being worked on; nothing "
        "is needed from you right now. Call "
        f"check_human_task(task_id='{task_id}') to check again, or "
        f"wait_for_human_result(task_id='{task_id}') to block until done."
    )


def tendem_tools(
    *,
    max_price: float,
    api_key: str | None = None,
    client: Any = None,
    scoping_timeout: float = DEFAULT_SCOPING_TIMEOUT_SECONDS,
    wait_timeout: float = DEFAULT_WAIT_TIMEOUT_SECONDS,
    conversation_id: str | None = None,
) -> list[BaseTool]:
    """The four-tool set for LangChain / LangGraph agents.

    ``create_human_task`` is the only non-idempotent call, so it is thin and
    deterministic — create, upload files, announce them, report anything the
    service already said; no polling, done in seconds. Every other tool is
    stateless against the ``task_id`` (which lives in the conversation), so
    an agent killed or replayed at any point resumes cleanly.

    Quotes at or below ``max_price`` are approved automatically — the flow
    never stops for a payment decision. Over-cap quotes come back with the
    contract scope so the calling agent can negotiate a reduction itself, or
    walk away with nothing charged.

    Args:
        max_price: Spend cap in USD per task. Required.
        api_key: Tendem API key; defaults to ``TENDEM_API_KEY``.
        client: A pre-configured ``Tendem``; overrides ``api_key``.
        scoping_timeout: Per-call poll budget for check/reply (default 15 min).
        wait_timeout: Per-call budget for wait (default 24h); on expiry it
            returns IN PROGRESS and can simply be called again.
        conversation_id: Correlates every task this tool set creates.

    """
    from langchain_tendem.client import Tendem
    from langchain_tendem.runner import (
        _trailing_tendem_text,
        advance_task,
        prepare_task,
        run_blocking,
    )

    tendem = client if client is not None else Tendem(
        api_key=api_key, max_price=max_price
    )

    async def _advance(
        task_id: str, *, reply: str | None, timeout: float, stop: bool
    ) -> str:
        try:
            event = await advance_task(
                tendem,
                task_id,
                max_price=max_price,
                timeout=timeout,
                reply=reply,
                stop_after_approval=stop,
            )
        except TendemError as exc:
            return _render_error(exc)
        return _event_text(event, max_price)

    async def create(request: str, file_paths: list[str] | None = None) -> str:
        try:
            task_id = await prepare_task(
                tendem, request, files=file_paths, conversation_id=conversation_id
            )
            # One quick read — no polling: surface anything the service
            # already said (it sometimes answers trivial briefs instantly).
            chat = await tendem.read_chat(task_id, from_offset=0)
        except TendemError as exc:
            return _render_error(exc)
        said = _trailing_tendem_text(chat)
        already = f"\nThe service already responded:\n{said}\n" if said else ""
        return (
            f"CREATED — task_id='{task_id}'."
            f"{already}\n"
            f"Next: call check_human_task(task_id='{task_id}'). It polls the "
            "task, forwards any message that needs you, and auto-approves "
            f"the price quote up to the ${max_price:.2f} cap."
        )

    async def check(task_id: str) -> str:
        return await _advance(task_id, reply=None, timeout=scoping_timeout, stop=True)

    async def reply_(task_id: str, reply: str) -> str:
        return await _advance(task_id, reply=reply, timeout=scoping_timeout, stop=True)

    async def wait(task_id: str) -> str:
        return await _advance(task_id, reply=None, timeout=wait_timeout, stop=False)

    return [
        StructuredTool.from_function(
            func=lambda request, file_paths=None: run_blocking(
                lambda: create(request, file_paths)
            ),
            coroutine=create,
            name="create_human_task",
            description=(
                "Submit a task to a vetted human expert service. Include ALL "
                "context in `request` (the expert cannot see this "
                "conversation) and attach input files via `file_paths`. Fast "
                "and deterministic: it only creates the task and uploads the "
                "files, then returns the task_id — follow up with "
                "check_human_task. Costs real money later (auto-approved "
                "only up to a pre-configured budget cap). Not for quick "
                "questions you can answer yourself, nor bulk data scraping "
                "(refused by policy)."
            ),
            args_schema=CreateTaskInput,
        ),
        StructuredTool.from_function(
            func=lambda task_id: run_blocking(lambda: check(task_id)),
            coroutine=check,
            name="check_human_task",
            description=(
                "Poll a created task until something needs you: a MESSAGE "
                "from the service (answer via reply_to_human_task), a quote "
                "above the budget cap (decide: narrow the scope or stop), or "
                "the final result. Quotes within the cap are approved "
                "automatically and reported as STARTED. Safe to call any "
                "number of times."
            ),
            args_schema=TaskIdInput,
        ),
        StructuredTool.from_function(
            func=lambda task_id, reply: run_blocking(lambda: reply_(task_id, reply)),
            coroutine=reply_,
            name="reply_to_human_task",
            description=(
                "Send your answer to the expert service's last message (or "
                "your scope reduction after an over-cap quote), then keep "
                "polling like check_human_task and report the next state."
            ),
            args_schema=ReplyInput,
        ),
        StructuredTool.from_function(
            func=lambda task_id: run_blocking(lambda: wait(task_id)),
            coroutine=wait,
            name="wait_for_human_result",
            description=(
                "Wait for a started human task to finish and return the "
                "verified result (markdown plus any file download URLs). "
                "Human work takes minutes to hours; the call blocks without "
                "consuming anything. If it returns IN PROGRESS or is ever "
                "interrupted, simply call it again with the same task_id — "
                "nothing is lost."
            ),
            args_schema=TaskIdInput,
        ),
    ]


__all__ = [
    "CreateTaskInput",
    "ReplyInput",
    "TaskIdInput",
    "tendem_tools",
]
