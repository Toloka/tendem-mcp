"""The task state machine behind the agent tools.

``advance_task`` moves one task forward until something needs the calling
agent — a message from Tendem, an over-cap quote, the result, or a time
budget running out — and reports it as a ``TaskEvent``. It is stateless
between calls (the chat is re-read from offset 0), so a crashed caller
resumes with just the ``task_id``. Every wait is a server-side long-poll in
plain Python — no model in the loop.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from langchain_tendem.errors import (
    ApprovalBlockedError,
    PriceCeilingExceededError,
    TaskFailedError,
    TendemToolError,
    TopUpRequiredError,
)
from langchain_tendem.models import (
    Contract,
    TaskOutcome,
    TaskSnapshot,
)

if TYPE_CHECKING:  # pragma: no cover
    from langchain_tendem.client import Tendem

logger = logging.getLogger("langchain_tendem")

T = TypeVar("T")


# -------------------------------------------------------------- chat helpers


def _tendem_messages(chat: dict[str, Any]) -> list[dict[str, Any]]:
    messages = chat.get("messages")
    if not isinstance(messages, list):
        return []
    return [
        entry
        for entry in messages
        if isinstance(entry, dict) and entry.get("from") == "tendem"
    ]


def _trailing_tendem_text(chat: dict[str, Any]) -> str:
    """Text of Tendem's messages after the last host message — the live ask."""
    messages = chat.get("messages")
    if not isinstance(messages, list):
        return ""
    trailing: list[str] = []
    for entry in messages:
        if not isinstance(entry, dict):
            continue
        if entry.get("from") == "tendem":
            trailing.append(str(entry.get("text") or ""))
        else:
            trailing.clear()
    return "\n\n".join(part for part in trailing if part).strip()


def _last_tendem_text(chat: dict[str, Any]) -> str:
    """The most recent non-empty Tendem message."""
    for entry in reversed(_tendem_messages(chat)):
        text = str(entry.get("text") or "").strip()
        if text:
            return text
    return ""


def _chat_offset(chat: dict[str, Any], fallback: int) -> int:
    value = chat.get("last_seen_offset")
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


# ------------------------------------------------------------------- files


def _normalize_files(files: Any) -> dict[str, Path | bytes]:
    """Coerce ``files`` into ``{upload_name: path_or_bytes}``."""
    if not files:
        return {}
    if isinstance(files, dict):
        return {
            str(name): source if isinstance(source, bytes) else Path(source)
            for name, source in files.items()
        }
    return {Path(item).name: Path(item) for item in files}


def blob_upload_url(upload_url: str, name: str) -> str:
    """Per-file PUT URL from the folder-level SAS URL the server mints.

    The filename goes into the path *before* the query string, and the host
    must be swapped from the ``dfs`` to the ``blob`` endpoint — a plain PUT
    to the URL as returned fails with HTTP 400 (verified live).
    """
    base, sep, query = upload_url.partition("?")
    base = base.replace(".dfs.core.windows.net", ".blob.core.windows.net")
    return f"{base.rstrip('/')}/{name}{sep}{query}"


async def _put_blob(url: str, data: bytes) -> None:
    import httpx

    async with httpx.AsyncClient(timeout=120.0) as http:
        response = await http.put(
            url, content=data, headers={"x-ms-blob-type": "BlockBlob"}
        )
        response.raise_for_status()


async def upload_files(
    client: Tendem, task_id: str, files: Any, *, last_seen_offset: int = 0
) -> int:
    """Upload input files to a task, then announce them in the chat.

    The announcement is part of the protocol — Tendem does not auto-detect
    uploads. Returns the new chat offset.
    """
    normalized = _normalize_files(files)
    if not normalized:
        return last_seen_offset

    info = await client.get_file_upload_url(task_id)
    upload_url = info.get("upload_url")
    if not isinstance(upload_url, str) or not upload_url:
        raise TaskFailedError(
            task_id, f"get_file_upload_url returned no upload_url: {info!r}"
        )
    for name, source in normalized.items():
        data = source if isinstance(source, bytes) else Path(source).read_bytes()
        try:
            await _put_blob(blob_upload_url(upload_url, name), data)
        except Exception as exc:
            raise TaskFailedError(task_id, f"uploading {name!r} failed: {exc}") from exc
        logger.info(
            "tendem: uploaded %s (%d bytes) to task %s", name, len(data), task_id
        )

    text = (
        "I've uploaded these input files for this task: "
        f"{', '.join(normalized)}. Please use them as the task inputs."
    )
    payload = await client.send_message(
        task_id, text, last_seen_offset=last_seen_offset
    )
    if payload.get("response_type") == "race":
        # Tendem often asks for the files while the upload is in flight; the
        # confirmation just needs to be re-sent at the new offset.
        payload = await client.send_message(
            task_id, text, last_seen_offset=_chat_offset(payload, last_seen_offset)
        )
    return _chat_offset(payload, last_seen_offset)


async def prepare_task(
    client: Tendem,
    description: str | None,
    *,
    name: str | None = None,
    files: Any = None,
    conversation_id: str | None = None,
) -> str:
    """Create a task and upload its input files. Returns the ``task_id``.

    Deterministic and fast — no polling. This is the only non-idempotent
    step of the flow; everything after it is keyed by the returned id.
    """
    if not description or not description.strip():
        msg = "a non-empty task description is required"
        raise ValueError(msg)
    file_inputs = _normalize_files(files)
    if file_inputs:
        # The server expects the brief to promise files it should wait for.
        description = (
            f"{description}\n\nInput files (uploading them now): "
            f"{', '.join(file_inputs)}."
        )
    snapshot = await client.create_task(
        name or _derive_name(description),
        description,
        conversation_id=conversation_id,
    )
    logger.info("tendem: created task %s", snapshot.task_id)
    if file_inputs:
        await upload_files(client, snapshot.task_id, file_inputs)
    return snapshot.task_id


# ----------------------------------------------------------- state machine


@dataclass(frozen=True)
class TaskEvent:
    """What ``advance_task`` ran into. ``kind`` is one of:

    * ``"result"`` — the task is done; ``outcome`` carries the deliverable
      (or the orchestrator's free chat answer).
    * ``"question"`` — Tendem said something and waits; ``text`` carries it.
      Advance again with ``reply=...``.
    * ``"approved"`` — the quote was just approved under the cap (only with
      ``stop_after_approval=True``); ``price`` is the display price.
    * ``"over_budget"`` — the quote exceeds the cap; ``price`` is the quote
      and ``text`` the contract scope. Nothing was charged. Advance with a
      scope-cutting ``reply`` to get a fresh quote, or stop.
    * ``"pending"`` — the time budget for this call elapsed; the task keeps
      running server-side. Advance again whenever.
    """

    kind: str
    task_id: str
    text: str | None = None
    price: str | None = None
    outcome: TaskOutcome | None = None
    snapshot: TaskSnapshot | None = None


async def advance_task(
    client: Tendem,
    task_id: str,
    *,
    max_price: float,
    timeout: float,
    reply: str | None = None,
    stop_after_approval: bool = False,
) -> TaskEvent:
    """Send an optional ``reply``, then drive the task until an event.

    Stateless between calls: the chat is re-read from offset 0, so a crashed
    caller loses nothing — call again with the same ``task_id``. Quotes at or
    below ``max_price`` are approved automatically — the flow never stops for
    a payment decision; over-cap quotes come back as an ``"over_budget"``
    event for the caller to negotiate or abandon. A topup need raises
    ``TopUpRequiredError``. In every refusal path nothing was charged.
    """
    if timeout <= 0:
        msg = "timeout must be positive"
        raise ValueError(msg)
    if max_price <= 0:
        msg = "max_price must be positive — it is the spend cap"
        raise ValueError(msg)

    deadline = client._clock() + timeout
    approved = False
    price_paid: float | None = None
    price_paid_formatted: str | None = None
    contract: Contract | None = None
    snapshot: TaskSnapshot | None = None

    if reply is not None:
        chat = await client.read_chat(task_id, from_offset=0, retry_transient=True)
        await client.send_message(
            task_id, reply, last_seen_offset=_chat_offset(chat, 0)
        )
        # If the send races with new content, the reply was not delivered;
        # the loop below re-reads the state and the caller will see whatever
        # superseded it.

    while True:
        remaining = deadline - client._clock()
        if remaining <= 0:
            return TaskEvent("pending", task_id, snapshot=snapshot)
        wait = max(1, min(client.wait_for_change_seconds, int(remaining)))
        started = client._clock()
        snapshot = await client.get_task(
            task_id, wait_for_change_seconds=wait, retry_transient=True
        )
        action = snapshot.action

        if snapshot.result_ready or snapshot.is_terminal:
            outcome = await _collect_outcome(
                client,
                task_id,
                snapshot,
                approved=approved,
                price_paid=price_paid,
                price_paid_formatted=price_paid_formatted,
                contract=contract,
            )
            return TaskEvent(
                "result", task_id, outcome=outcome, snapshot=snapshot
            )

        if action == "await_input":
            chat = await client.read_chat(
                task_id, from_offset=0, retry_transient=True
            )
            question = _trailing_tendem_text(chat)
            if question:
                return TaskEvent(
                    "question",
                    task_id,
                    text=question,
                    snapshot=snapshot,
                )
            # Our own message is the newest content; Tendem is composing.
            await client._pace(started, snapshot)
            continue

        if action == "await_user_topup":
            raise TopUpRequiredError(task_id, _topup_url(snapshot.raw))

        if not approved and (
            action == "await_user_approval" or snapshot.ready_for_approval
        ):
            contract = await client.get_contract(task_id, retry_transient=True)
            if not contract.is_quoted:  # still estimating
                await client._pace(started, snapshot)
                continue
            quoted = contract.display_price or str(contract.price)
            if contract.price is None or contract.price > max_price:
                # The caller (an LLM agent) negotiates: hand it the scope and
                # the numbers; nothing is charged.
                return TaskEvent(
                    "over_budget",
                    task_id,
                    text=contract.summary(),
                    price=quoted,
                    snapshot=snapshot,
                )
            try:
                outcome_ = await client.approve_task(task_id, max_price=max_price)
            except (PriceCeilingExceededError, ApprovalBlockedError):
                # The quote moved or vanished between our read and the
                # approval read; loop so the next round re-decides.
                contract = None
                await client._pace(started, snapshot)
                continue
            if outcome_.needs_topup:
                raise TopUpRequiredError(task_id, outcome_.topup_url, quoted)
            if not outcome_.approved:
                raise TaskFailedError(
                    task_id,
                    f"approval was refused: {outcome_.reason or 'unknown reason'}",
                    snapshot,
                )
            approved = True
            price_paid = contract.price
            price_paid_formatted = contract.display_price
            logger.info("tendem: task %s approved at %s", task_id, quoted)
            if stop_after_approval:
                return TaskEvent(
                    "approved",
                    task_id,
                    price=quoted,
                    snapshot=snapshot,
                )
            continue

        # awaiting_tendem_work (or an unknown action): wait, at the paced floor.
        await client._pace(started, snapshot)


def _topup_url(payload: dict[str, Any]) -> str | None:
    url = payload.get("topup_url")
    return url if isinstance(url, str) else None


async def _collect_outcome(
    client: Tendem,
    task_id: str,
    snapshot: TaskSnapshot,
    *,
    approved: bool,
    price_paid: float | None,
    price_paid_formatted: str | None,
    contract: Contract | None,
) -> TaskOutcome:
    """Assemble the deliverable: ``get_task_result`` first, else — for
    unapproved tasks the orchestrator closed after answering in chat — the
    trailing chat messages.
    """
    result = None
    try:
        result = await client.get_task_result(task_id, retry_transient=True)
    except TendemToolError as exc:
        logger.debug("tendem: get_task_result failed for %s: %s", task_id, exc)

    if result is not None and (result.content or result.files):
        if not approved:
            # The approval may have happened in an earlier (possibly killed)
            # call; attribute the price from the contract, best-effort.
            try:
                contract = await client.get_contract(task_id, retry_transient=True)
            except TendemToolError:  # pragma: no cover - best-effort
                contract = None
            if contract is not None and contract.is_quoted:
                price_paid = contract.price
                price_paid_formatted = contract.display_price
        return TaskOutcome(
            task_id=task_id,
            content=result.content,
            files=result.files,
            price_paid=price_paid,
            price_paid_formatted=price_paid_formatted,
            contract=contract,
            result=result,
        )

    chat = await client.read_chat(task_id, from_offset=0, retry_transient=True)
    answer = _trailing_tendem_text(chat) or _last_tendem_text(chat)
    if answer and not approved:
        return TaskOutcome(
            task_id=task_id,
            content=answer,
            answered_in_chat=True,
            contract=contract,
            result=result,
        )

    raise TaskFailedError(
        task_id,
        "the task ended without a deliverable"
        + (f" (guidance: {snapshot.guidance})" if snapshot.guidance else ""),
        snapshot,
    )


def _derive_name(description: str) -> str:
    """A short task name from the first line of the brief."""
    lines = description.strip().splitlines()
    if not lines:
        return "Tendem task"
    first_line = lines[0]
    return first_line[:77] + "..." if len(first_line) > 80 else first_line


def run_blocking(coro_factory: Callable[[], Awaitable[T]]) -> T:
    """Run a coroutine to completion from sync code. ``asyncio.run`` when no
    loop is running; otherwise a fresh loop on a dedicated thread, so a sync
    tool inside an async framework still works.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro_factory())).result()


__all__ = [
    "TaskEvent",
    "advance_task",
    "blob_upload_url",
    "prepare_task",
    "run_blocking",
    "upload_files",
]
