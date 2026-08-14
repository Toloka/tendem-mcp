"""Exceptions raised by ``langchain-tendem``. None of the spend-related ones
fire after money has moved: refusal always happens before the charge.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from langchain_tendem.constants import TRANSIENT_ERROR_CODE

if TYPE_CHECKING:  # pragma: no cover
    from langchain_tendem.models import TaskSnapshot


class TendemError(Exception):
    """Base class for every error this package raises."""


_TOOL_FAILURE_CODE = re.compile(r"Tool failed \((?P<code>[A-Z_]+)\)")


class TendemToolError(TendemError):
    """The MCP server reported a tool execution error.

    The server prefixes failures with ``Tool failed (<CODE>):``; the parsed
    code is on ``code``, and ``TEMPORARILY_UNAVAILABLE`` marks it
    ``transient`` (the long-wait paths retry those).
    """

    def __init__(
        self, tool_name: str, message: str, *, code: str | None = None
    ) -> None:
        self.tool_name = tool_name
        self.message = message
        if code is None:
            match = _TOOL_FAILURE_CODE.search(message or "")
            code = match.group("code") if match else None
        self.code = code
        super().__init__(f"Tendem tool {tool_name!r} failed: {message}")

    @property
    def transient(self) -> bool:
        """``True`` when the server itself says retrying is the right move."""
        return self.code == TRANSIENT_ERROR_CODE


class TendemProtocolError(TendemError):
    """A tool returned a payload this client cannot make sense of."""


class ApprovalBlockedError(TendemError):
    """``approve_task`` was blocked before any charge: no spend cap recorded
    for this task, the contract has no quote yet, or the call was malformed.
    """

    def __init__(self, task_id: str | None, detail: str) -> None:
        self.task_id = task_id
        self.detail = detail
        where = f" for task {task_id}" if task_id else ""
        super().__init__(f"Spend approval blocked{where}: {detail}")


class PriceCeilingExceededError(TendemError):
    """The quote landed above the pre-authorised ceiling. Nothing was charged."""

    def __init__(self, task_id: str | None, max_price: Any, current_price: Any) -> None:
        self.task_id = task_id
        self.max_price = max_price
        self.current_price = current_price
        super().__init__(
            f"Quote for task {task_id} is {current_price!r}, above the "
            f"pre-authorised ceiling of {max_price!r}. Nothing was charged. "
            "Raise the ceiling, or show the human the real price and approve "
            "it explicitly."
        )


class TopUpRequiredError(TendemError):
    """The balance cannot cover an approved quote. Not retryable headlessly:
    ``topup_url`` is task-bound — a human paying it auto-approves the task.
    """

    def __init__(
        self, task_id: str | None, topup_url: str | None, price: Any = None
    ) -> None:
        self.task_id = task_id
        self.topup_url = topup_url
        self.price = price
        at = f" at {price!r}" if price is not None else ""
        url = (
            f" Top-up URL (paying it auto-approves this task): {topup_url}"
            if topup_url
            else ""
        )
        super().__init__(
            f"Insufficient Tendem balance to approve task {task_id}{at}.{url}"
        )


class TaskFailedError(TendemError):
    """The task ended (or stalled) without a deliverable. ``snapshot`` carries
    the last state; its ``guidance`` usually explains what went wrong.
    """

    def __init__(
        self, task_id: str, detail: str, snapshot: TaskSnapshot | None = None
    ) -> None:
        self.task_id = task_id
        self.detail = detail
        self.snapshot = snapshot
        super().__init__(f"Tendem task {task_id} failed: {detail}")


class PollTimeoutError(TendemError):
    """A bounded wait ran out of budget. Not a task failure — nothing is lost
    server-side; resume with the same ``task_id``. ``snapshot`` = last state.
    """

    def __init__(
        self, task_id: str, rounds: int, snapshot: TaskSnapshot | None
    ) -> None:
        self.task_id = task_id
        self.rounds = rounds
        self.snapshot = snapshot
        action = snapshot.next_action if snapshot else "unknown"
        super().__init__(
            f"Task {task_id} still at next_action={action!r} after {rounds} "
            "poll round(s). The task keeps running server-side; resume waiting "
            "with the same task_id."
        )


__all__ = [
    "ApprovalBlockedError",
    "PollTimeoutError",
    "PriceCeilingExceededError",
    "TaskFailedError",
    "TendemError",
    "TendemProtocolError",
    "TendemToolError",
    "TopUpRequiredError",
]
