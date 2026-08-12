"""Typed views over the Tendem MCP server's JSON payloads.

The server is deliberately envelope-driven: every tool answers with
``next_action`` / ``poll_after_seconds`` / ``poll_timeout_seconds`` /
``guidance`` alongside the raw ``status``, and callers are meant to act on the
envelope rather than pattern-match statuses. These dataclasses surface the
envelope as typed attributes while keeping the untouched payload on ``.raw`` so
nothing is lost when the server adds fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    """Coarse task state. Prefer :class:`NextAction` for deciding what to do."""

    ACTING = "ACTING"
    """Tendem is working: scoping, matching an expert, or executing."""

    LISTENING = "LISTENING"
    """Waiting on our side — a question, an approval, or a ready result."""

    NEEDS_REPAIR = "NEEDS_REPAIR"
    """Something is wrong; the chat explains what."""

    CLOSED = "CLOSED"
    """Terminal. The result is still fetchable via ``get_task_result``."""

    DELETED = "DELETED"
    """Soft-deleted. No further action possible."""

    @classmethod
    def parse(cls, value: Any) -> TaskStatus | None:
        """Return the matching member, or ``None`` for unknown/missing values."""
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            return None
        try:
            return cls(value.upper())
        except ValueError:
            return None


class NextAction(str, Enum):
    """What the server says the caller should do next."""

    AWAITING_TENDEM_WORK = "awaiting_tendem_work"
    """Nothing to do. Long-poll a few rounds, then hand off."""

    AWAIT_INPUT = "await_input"
    """Tendem asked something. ``read_chat`` then ``send_message``."""

    AWAIT_USER_APPROVAL = "await_user_approval"
    """A quote is ready. ``get_contract``, show it to a human, then decide."""

    AWAIT_USER_TOPUP = "await_user_topup"
    """Balance too low. Hand the human the task-bound ``topup_url``."""

    RESOLVE_RACE = "resolve_race"
    """Our message crossed new content. Re-read and re-send with a new offset."""

    FETCH_RESULT = "fetch_result"
    """``get_task_result``."""

    DONE = "done"
    """Stop."""

    @classmethod
    def parse(cls, value: Any) -> NextAction | None:
        """Return the matching member, or ``None`` for unknown/missing values.

        Unknown values are not an error: the server may add actions, and a
        client that crashes on an unrecognised string is worse than one that
        surfaces the raw value and the ``guidance`` string.
        """
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            return None
        try:
            return cls(value.lower())
        except ValueError:
            return None


#: Actions that mean the loop should stop and something outside it must act.
NEEDS_CALLER: frozenset[NextAction] = frozenset(
    {
        NextAction.AWAIT_INPUT,
        NextAction.AWAIT_USER_APPROVAL,
        NextAction.AWAIT_USER_TOPUP,
        NextAction.RESOLVE_RACE,
        NextAction.FETCH_RESULT,
        NextAction.DONE,
    }
)

#: Actions that specifically require a *human*, not the agent, to act.
NEEDS_HUMAN: frozenset[NextAction] = frozenset(
    {NextAction.AWAIT_USER_APPROVAL, NextAction.AWAIT_USER_TOPUP}
)

_TERMINAL_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.CLOSED, TaskStatus.DELETED}
)


def _as_float(value: Any) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class TaskSnapshot:
    """One observation of a task: its status plus the server's action envelope."""

    task_id: str
    status: str | None = None
    next_action: str | None = None
    poll_after_seconds: float | None = None
    poll_timeout_seconds: float | None = None
    guidance: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def action(self) -> NextAction | None:
        """``next_action`` as an enum member, or ``None`` if unrecognised."""
        return NextAction.parse(self.next_action)

    @property
    def task_status(self) -> TaskStatus | None:
        """``status`` as an enum member, or ``None`` if unrecognised."""
        return TaskStatus.parse(self.status)

    @property
    def is_terminal(self) -> bool:
        """``True`` when no further progress is possible on this task."""
        return self.task_status in _TERMINAL_STATUSES or self.action is NextAction.DONE

    @property
    def needs_caller(self) -> bool:
        """``True`` when the poll loop should stop and hand control back."""
        action = self.action
        return self.is_terminal or (action is not None and action in NEEDS_CALLER)

    @property
    def needs_human(self) -> bool:
        """``True`` when a *human* must decide (approval or top-up)."""
        action = self.action
        return action is not None and action in NEEDS_HUMAN

    @property
    def result_ready(self) -> bool:
        """``True`` when ``get_task_result`` will return the deliverable."""
        return self.action is NextAction.FETCH_RESULT

    @classmethod
    def from_payload(
        cls, payload: dict[str, Any], *, task_id: str | None = None
    ) -> TaskSnapshot:
        """Build a snapshot from a ``create_task`` / ``get_task`` payload."""
        resolved = payload.get("task_id") or payload.get("id") or task_id
        if not resolved:
            msg = "Tendem payload carried no task_id"
            raise ValueError(msg)
        return cls(
            task_id=str(resolved),
            status=payload.get("status"),
            next_action=payload.get("next_action"),
            poll_after_seconds=_as_float(payload.get("poll_after_seconds")),
            poll_timeout_seconds=_as_float(payload.get("poll_timeout_seconds")),
            guidance=payload.get("guidance"),
            raw=payload,
        )


@dataclass(frozen=True)
class Contract:
    """Scope plus price for a task — the thing you show a human before charging.

    Scope becomes available before the price does, so ``price is None`` while
    ``task_description`` is populated is a normal intermediate state, not an
    error. Never present a contract without a price as if it were a quote.
    """

    task_id: str
    title: str | None = None
    task_description: str | None = None
    price: float | None = None
    currency: str | None = None
    criteria: Any = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_quoted(self) -> bool:
        """``True`` once a price is attached and the contract is approvable."""
        return self.price is not None

    def summary(self) -> str:
        """A short human-readable block: what the money buys, and how much."""
        lines = [f"Tendem task {self.task_id}"]
        if self.title:
            lines.append(f"Title: {self.title}")
        if self.task_description:
            lines.append(f"Scope: {self.task_description}")
        if self.criteria:
            lines.append(f"Acceptance criteria: {self.criteria}")
        if self.price is None:
            lines.append("Price: not quoted yet — do not approve.")
        else:
            currency = f" {self.currency}" if self.currency else ""
            lines.append(f"Price: {self.price}{currency}")
        return "\n".join(lines)

    @classmethod
    def from_payload(
        cls, payload: dict[str, Any], *, task_id: str | None = None
    ) -> Contract:
        """Build a contract from a ``get_contract`` payload."""
        resolved = payload.get("task_id") or task_id or ""
        return cls(
            task_id=str(resolved),
            title=payload.get("title"),
            task_description=payload.get("task_description")
            or payload.get("description"),
            price=_as_float(payload.get("price")),
            currency=payload.get("currency"),
            criteria=payload.get("criteria") or payload.get("acceptance_criteria"),
            raw=payload,
        )


@dataclass(frozen=True)
class ApprovalOutcome:
    """Result of an approval attempt that actually reached the server.

    ``approved=False`` with ``reason="insufficient_balance"`` is an expected,
    non-retryable outcome: ``topup_url`` is bound to this task and paying it
    auto-approves the task. Never loop on ``approve_task``.
    """

    task_id: str
    approved: bool
    reason: str | None = None
    topup_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def needs_topup(self) -> bool:
        """``True`` when the only thing missing is money in the account."""
        return not self.approved and (
            self.reason == "insufficient_balance" or self.topup_url is not None
        )

    @classmethod
    def from_payload(
        cls, payload: dict[str, Any], *, task_id: str | None = None
    ) -> ApprovalOutcome:
        """Build an outcome from an ``approve_task`` payload."""
        resolved = payload.get("task_id") or task_id or ""
        return cls(
            task_id=str(resolved),
            approved=bool(payload.get("approved", False)),
            reason=payload.get("reason"),
            topup_url=payload.get("topup_url"),
            raw=payload,
        )


@dataclass(frozen=True)
class TendemFile:
    """One deliverable file. ``url`` is a short-lived pre-signed link."""

    name: str | None = None
    url: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> TendemFile:
        """Build a file record from one entry of ``get_task_result``'s ``files``."""
        size = payload.get("size_bytes", payload.get("size"))
        try:
            size_int = int(size) if size is not None else None
        except (TypeError, ValueError):
            size_int = None
        return cls(
            name=payload.get("name") or payload.get("filename"),
            url=payload.get("url") or payload.get("download_url"),
            mime_type=payload.get("mime_type") or payload.get("content_type"),
            size_bytes=size_int,
            raw=payload,
        )


@dataclass(frozen=True)
class TaskResult:
    """The delivered work: markdown ``content`` plus zero or more files."""

    task_id: str
    content: str | None = None
    files: tuple[TendemFile, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_payload(
        cls, payload: dict[str, Any], *, task_id: str | None = None
    ) -> TaskResult:
        """Build a result from a ``get_task_result`` payload."""
        resolved = payload.get("task_id") or task_id or ""
        raw_files = payload.get("files") or ()
        files = tuple(
            TendemFile.from_payload(entry)
            for entry in raw_files
            if isinstance(entry, dict)
        )
        return cls(
            task_id=str(resolved),
            content=payload.get("content") or payload.get("markdown"),
            files=files,
            raw=payload,
        )


__all__ = [
    "NEEDS_CALLER",
    "NEEDS_HUMAN",
    "ApprovalOutcome",
    "Contract",
    "NextAction",
    "TaskResult",
    "TaskSnapshot",
    "TaskStatus",
    "TendemFile",
]
