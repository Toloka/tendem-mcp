"""Exceptions raised by ``langchain-tendem``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from langchain_tendem.models import TaskSnapshot


class TendemError(Exception):
    """Base class for every error this package raises."""


class TendemToolError(TendemError):
    """The MCP server reported a tool execution error (``isError=true``)."""

    def __init__(self, tool_name: str, message: str) -> None:
        self.tool_name = tool_name
        self.message = message
        super().__init__(f"Tendem tool {tool_name!r} failed: {message}")


class TendemProtocolError(TendemError):
    """A tool returned a payload this client cannot make sense of."""


class ApprovalNotConfirmedError(TendemError):
    """Something tried to approve a spend without an explicit human decision.

    This is the package's central guardrail. Approving a Tendem task charges the
    user's balance, so neither a model nor a convenience default may do it
    implicitly: calling code must pass ``confirmed=True`` (or record a grant on
    the :class:`~langchain_tendem.approval.HumanApprovalGate`) for the specific
    ``task_id``.
    """

    def __init__(self, task_id: str | None, detail: str) -> None:
        self.task_id = task_id
        self.detail = detail
        where = f" for task {task_id}" if task_id else ""
        super().__init__(f"Spend approval blocked{where}: {detail}")


class QuoteChangedError(TendemError):
    """The price on the server differs from the price a human was shown.

    Asking Tendem to change scope voids the previous quote, so a stale price is
    a real hazard: the human approved one number and the server would charge
    another. Re-read the contract, show the new price, get a fresh decision.
    """

    def __init__(
        self, task_id: str | None, approved_price: Any, current_price: Any
    ) -> None:
        self.task_id = task_id
        self.approved_price = approved_price
        self.current_price = current_price
        super().__init__(
            f"Quote for task {task_id} changed: a human approved "
            f"{approved_price!r} but the current price is {current_price!r}. "
            "Show the new scope and price and get a fresh decision."
        )


class PollTimeoutError(TendemError):
    """A bounded poll loop ran out of rounds without the task needing us.

    Not a failure of the task — Tendem work can take hours. It is the signal to
    stop holding the turn open and hand off (background watcher, or tell the
    human they can check back later). ``snapshot`` carries the last state seen.
    """

    def __init__(self, task_id: str, rounds: int, snapshot: TaskSnapshot | None) -> None:
        self.task_id = task_id
        self.rounds = rounds
        self.snapshot = snapshot
        action = snapshot.next_action if snapshot else "unknown"
        super().__init__(
            f"Task {task_id} still at next_action={action!r} after {rounds} "
            "poll round(s). Hand off rather than continuing to poll."
        )


__all__ = [
    "ApprovalNotConfirmedError",
    "PollTimeoutError",
    "QuoteChangedError",
    "TendemError",
    "TendemProtocolError",
    "TendemToolError",
]
