"""Typed views over the Tendem MCP server's JSON payloads.

Every tool answers with an action envelope (``next_action`` /
``poll_after_seconds`` / ``guidance``) that is authoritative over the raw
``status``; these dataclasses surface it as typed attributes and keep the
untouched payload on ``.raw``. Money arrives as ``{"amount": 3, "currency":
"USD", "formatted": "$3.00"}`` and is parsed via ``parse_money``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

TaskStatus = Literal["ACTING", "LISTENING", "NEEDS_REPAIR", "CLOSED", "DELETED"]
"""Coarse task state. Prefer ``NextAction`` for deciding what to do."""

NextAction = Literal[
    "awaiting_tendem_work",  # nothing to do; long-poll
    "await_input",  # read_chat, then send_message
    "await_user_approval",  # quote ready; decide
    "await_user_topup",  # balance too low; topup_url
    "resolve_race",  # message crossed content; re-read
    "fetch_result",  # get_task_result
    "done",  # stop
]
"""What the server says the caller should do next."""

_STATUSES: frozenset[str] = frozenset(get_args(TaskStatus))
_ACTIONS: frozenset[str] = frozenset(get_args(NextAction))


def parse_status(value: Any) -> TaskStatus | None:
    """Normalise a raw status; unknown values yield ``None``, not an error."""
    if isinstance(value, str) and value.upper() in _STATUSES:
        return value.upper()  # type: ignore[return-value]
    return None


def parse_action(value: Any) -> NextAction | None:
    """Normalise a raw ``next_action``; unknown values yield ``None``, not an
    error — the server may add actions.
    """
    if isinstance(value, str) and value.lower() in _ACTIONS:
        return value.lower()  # type: ignore[return-value]
    return None


#: Actions that mean the loop should stop and something outside it must act.
NEEDS_CALLER: frozenset[NextAction] = frozenset(
    {
        "await_input",
        "await_user_approval",
        "await_user_topup",
        "resolve_race",
        "fetch_result",
        "done",
    }
)

_TERMINAL_STATUSES: frozenset[TaskStatus] = frozenset({"CLOSED", "DELETED"})

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def parse_money(value: Any) -> tuple[float | None, str | None, str | None]:
    """Parse a price field into ``(amount, currency, formatted)``.

    Accepts the live object shape, plain numbers, and formatted strings.
    Unparseable input yields ``(None, None, None)`` — a missing price is a
    normal state (quote not ready yet), not an error.
    """
    if value is None:
        return None, None, None
    if isinstance(value, bool):  # bool is an int subclass; a price it is not
        return None, None, None
    if isinstance(value, (int, float)):
        return float(value), None, None
    if isinstance(value, str):
        text = value.strip()
        match = _NUMBER.search(text.replace(",", ""))
        amount = float(match.group()) if match else None
        return amount, None, text or None
    if isinstance(value, dict):
        amount, _, _ = parse_money(value.get("amount"))
        currency = value.get("currency")
        formatted = value.get("formatted")
        return (
            amount,
            currency if isinstance(currency, str) else None,
            formatted if isinstance(formatted, str) else None,
        )
    return None, None, None


def format_money(
    amount: float | None, currency: str | None, formatted: str | None
) -> str | None:
    """Best human-readable rendering of a parsed price, if any."""
    if formatted:
        return formatted
    if amount is None:
        return None
    if currency in (None, "USD"):
        return f"${amount:.2f}"
    return f"{amount:.2f} {currency}"


def _as_int(value: Any) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class TaskSnapshot:
    """One observation of a task: status, action envelope, quote, chat cursor."""

    task_id: str
    status: str | None = None
    next_action: str | None = None
    poll_after_seconds: float | None = None
    poll_timeout_seconds: float | None = None
    guidance: str | None = None
    name: str | None = None
    ready_for_approval: bool = False
    price: float | None = None
    price_currency: str | None = None
    price_formatted: str | None = None
    latest_chat_offset: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def action(self) -> NextAction | None:
        """``next_action``, normalised; ``None`` if unrecognised."""
        return parse_action(self.next_action)

    @property
    def task_status(self) -> TaskStatus | None:
        """``status``, normalised; ``None`` if unrecognised."""
        return parse_status(self.status)

    @property
    def is_terminal(self) -> bool:
        """``True`` when no further progress is possible on this task."""
        return self.task_status in _TERMINAL_STATUSES or self.action == "done"

    @property
    def needs_caller(self) -> bool:
        """``True`` when a poll loop should stop and hand control back."""
        return self.is_terminal or self.action in NEEDS_CALLER

    @property
    def result_ready(self) -> bool:
        """``True`` when ``get_task_result`` will return the deliverable."""
        return self.action == "fetch_result"

    @classmethod
    def from_payload(
        cls, payload: dict[str, Any], *, task_id: str | None = None
    ) -> TaskSnapshot:
        """Build a snapshot from a ``create_task`` / ``get_task`` payload."""
        resolved = payload.get("task_id") or payload.get("id") or task_id
        if not resolved:
            msg = "Tendem payload carried no task_id"
            raise ValueError(msg)
        amount, currency, formatted = parse_money(payload.get("price"))
        return cls(
            task_id=str(resolved),
            status=payload.get("status"),
            next_action=payload.get("next_action"),
            poll_after_seconds=_as_float(payload.get("poll_after_seconds")),
            poll_timeout_seconds=_as_float(payload.get("poll_timeout_seconds")),
            guidance=payload.get("guidance"),
            name=payload.get("name"),
            ready_for_approval=bool(payload.get("ready_for_approval", False)),
            price=amount,
            price_currency=currency,
            price_formatted=formatted,
            latest_chat_offset=_as_int(payload.get("latest_chat_offset")),
            raw=payload,
        )


@dataclass(frozen=True)
class Contract:
    """Scope plus price — what the money buys, and how much.

    Wire shape: ``{task_id, state, contract: {...}, price}``; the inner object
    is free-form (live server: ``input_prompt``, ``title``,
    ``quality_criteria``). ``state`` goes ``no_contract`` → ``estimating`` →
    ``available``: scope exists *before* the price, so an unquoted contract is
    a normal intermediate state — never present it as a quote.
    """

    task_id: str
    state: str | None = None
    title: str | None = None
    task_description: str | None = None
    price: float | None = None
    currency: str | None = None
    price_formatted: str | None = None
    criteria: Any = None
    fields: dict[str, Any] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_quoted(self) -> bool:
        """``True`` once a price is attached and the contract is approvable."""
        return self.price is not None

    @property
    def display_price(self) -> str | None:
        """The price as the server formats it (``"$3.00"``), if quoted."""
        return format_money(self.price, self.currency, self.price_formatted)

    def summary(self) -> str:
        """A short human-readable block for an approval prompt."""
        lines = [f"Tendem task {self.task_id}"]
        if self.title:
            lines.append(f"Title: {self.title}")
        if self.task_description:
            lines.append(f"Scope: {self.task_description}")
        if self.criteria:
            lines.append(f"Acceptance criteria: {_criteria_names(self.criteria)}")
        if self.price is None:
            lines.append("Price: not quoted yet — do not approve.")
        else:
            lines.append(f"Price: {self.display_price}")
        return "\n".join(lines)

    @classmethod
    def from_payload(
        cls, payload: dict[str, Any], *, task_id: str | None = None
    ) -> Contract:
        """Build a contract from a ``get_contract`` payload (nested or flat)."""
        resolved = payload.get("task_id") or task_id or ""
        inner = payload.get("contract")
        fields_ = inner if isinstance(inner, dict) else {}
        amount, currency, formatted = parse_money(payload.get("price"))

        def pick(*keys: str) -> Any:
            for source in (fields_, payload):
                for key in keys:
                    value = source.get(key)
                    if value is not None:
                        return value
            return None

        return cls(
            task_id=str(resolved),
            state=payload.get("state"),
            title=pick("title"),
            task_description=pick("task_description", "input_prompt", "description"),
            price=amount,
            currency=currency,
            price_formatted=formatted,
            criteria=pick("quality_criteria", "criteria", "acceptance_criteria"),
            fields=fields_,
            raw=payload,
        )


def _criteria_names(criteria: Any) -> str:
    """Render quality criteria compactly — they arrive as rich objects."""
    if isinstance(criteria, list):
        names = [
            entry.get("name") or entry.get("description")
            if isinstance(entry, dict)
            else str(entry)
            for entry in criteria
        ]
        return "; ".join(str(name) for name in names if name)
    return str(criteria)


@dataclass(frozen=True)
class ApprovalOutcome:
    """Result of an approval attempt that reached the server.

    ``approved=False`` with ``needs_topup`` is expected and non-retryable:
    ``topup_url`` is task-bound and paying it auto-approves the task.
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
        """Build a file record from one ``files[]`` entry."""
        return cls(
            name=payload.get("name") or payload.get("filename"),
            url=payload.get("download_url") or payload.get("url"),
            mime_type=payload.get("content_type") or payload.get("mime_type"),
            size_bytes=_as_int(payload.get("size_bytes", payload.get("size"))),
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


@dataclass(frozen=True)
class TaskOutcome:
    """The deliverable of a finished task (a ``"result"`` ``TaskEvent``).

    Two success shapes: an expert deliverable approved at ``price_paid``,
    or — for trivial briefs — the orchestrator's free chat answer
    (``answered_in_chat=True``, nothing charged).
    """

    task_id: str
    content: str | None
    files: tuple[TendemFile, ...] = ()
    price_paid: float | None = None
    price_paid_formatted: str | None = None
    answered_in_chat: bool = False
    contract: Contract | None = None
    result: TaskResult | None = None

    def render(self) -> str:
        """The outcome as one markdown block — what the agent tool returns."""
        parts: list[str] = []
        if self.content:
            parts.append(self.content)
        if self.files:
            listing = "\n".join(f"- {f.name or 'file'}: {f.url}" for f in self.files)
            parts.append(f"Files (pre-signed URLs, short-lived):\n{listing}")
        cost = self.price_paid_formatted or (
            f"${self.price_paid:.2f}" if self.price_paid is not None else None
        )
        if cost:
            parts.append(
                f"(Executed by a human expert via Tendem for {cost}; "
                f"task_id {self.task_id}.)"
            )
        elif self.answered_in_chat:
            parts.append(f"(Answered by Tendem at no charge; task_id {self.task_id}.)")
        if not parts:
            return f"Task {self.task_id} produced no content."
        return "\n\n".join(parts)


__all__ = [
    "NEEDS_CALLER",
    "ApprovalOutcome",
    "Contract",
    "NextAction",
    "TaskOutcome",
    "TaskResult",
    "TaskSnapshot",
    "TaskStatus",
    "TendemFile",
    "format_money",
    "parse_action",
    "parse_money",
    "parse_status",
]
