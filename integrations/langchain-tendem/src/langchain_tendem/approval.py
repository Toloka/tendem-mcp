"""The spend guardrail: an approval ledger plus the interceptor that enforces it.

Approving a Tendem task charges real money. This package therefore refuses to
let ``approve_task`` be reachable by accident — not by documenting a convention,
but by putting a gate in the call path:

* Programmatic callers use :meth:`langchain_tendem.Tendem.approve_task`, which
  requires ``confirmed=True`` (literally ``True``, not merely truthy).
* Model-driven callers get the tool via
  :meth:`langchain_tendem.Tendem.get_tools`, where :class:`SpendGuardInterceptor`
  sits between the LangChain tool and the MCP transport. Unless a human's
  decision was recorded on the :class:`HumanApprovalGate` for that exact
  ``task_id``, the call never leaves the process; the model receives an error
  ``ToolMessage`` telling it to ask a human instead.

Grants are single-use and price-bound by default, which also closes the stale
quote hole: asking Tendem to cut scope voids the old price, so a grant recorded
against the old number will not authorise a charge at the new one.
"""

from __future__ import annotations

import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from mcp.types import CallToolResult, TextContent

from langchain_tendem.constants import APPROVAL_TOOL_NAME
from langchain_tendem.errors import (
    ApprovalNotConfirmedError,
    QuoteChangedError,
    TendemError,
)

_PRICE_TOLERANCE = 1e-9


def _as_float(value: Any) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ApprovalGrant:
    """A recorded human decision to spend money on one specific task."""

    task_id: str
    price: float | None = None
    granted_by: str | None = None
    note: str | None = None
    granted_at: float = field(default_factory=time.time)
    consumed: bool = False

    def _consumed(self) -> ApprovalGrant:
        return ApprovalGrant(
            task_id=self.task_id,
            price=self.price,
            granted_by=self.granted_by,
            note=self.note,
            granted_at=self.granted_at,
            consumed=True,
        )


class HumanApprovalGate:
    """Ledger of human spend approvals, keyed by ``task_id``.

    Nothing in this class can be satisfied by a model on its own: the only way
    to add a grant is for application code to call :meth:`grant` with
    ``confirmed=True`` after a human said yes. Keeping that call in the
    application — next to whatever UI or prompt actually asked the person — is
    the whole point.

    Args:
        single_use: When ``True`` (default) a grant authorises exactly one
            ``approve_task`` call and is then spent. Retries need a fresh
            human decision.
        require_price_match: When ``True`` (default) a grant must carry the
            price the human was shown, and an approval whose price differs
            from it is refused with
            :class:`~langchain_tendem.errors.QuoteChangedError`. Set ``False``
            only if the price is enforced elsewhere in your stack — a grant
            with no price authorises *any* amount.
    """

    def __init__(
        self, *, single_use: bool = True, require_price_match: bool = True
    ) -> None:
        self.single_use = single_use
        self.require_price_match = require_price_match
        self._grants: dict[str, ApprovalGrant] = {}
        self._history: list[ApprovalGrant] = []

    def grant(
        self,
        task_id: str,
        *,
        confirmed: bool,
        price: float | None = None,
        granted_by: str | None = None,
        note: str | None = None,
    ) -> ApprovalGrant:
        """Record that a human approved spending ``price`` on ``task_id``.

        Args:
            task_id: The task the human was shown.
            confirmed: Must be exactly ``True``. A truthy value such as
                ``"yes"`` or ``1`` is rejected, so a stringly-typed model
                argument cannot slip through as consent.
            price: The price the human was shown. Required while
                ``require_price_match`` is on — it is what makes a later
                stale-quote charge impossible.
            granted_by: Optional identifier of who approved, for audit.
            note: Optional free-text audit note.

        Raises:
            ApprovalNotConfirmedError: If ``confirmed`` is not exactly ``True``,
                ``task_id`` is empty, or ``price`` is missing while
                ``require_price_match`` is on.
        """
        if not task_id:
            raise ApprovalNotConfirmedError(None, "no task_id supplied")
        if confirmed is not True:
            raise ApprovalNotConfirmedError(
                task_id,
                "confirmed must be exactly True — an explicit human decision, "
                f"not {confirmed!r}",
            )
        resolved_price = _as_float(price)
        if self.require_price_match and resolved_price is None:
            raise ApprovalNotConfirmedError(
                task_id,
                "a grant must record the price the human was shown, otherwise "
                "it authorises any amount. Pass price=, or construct "
                "HumanApprovalGate(require_price_match=False) to opt out "
                "deliberately.",
            )
        entry = ApprovalGrant(
            task_id=task_id,
            price=resolved_price,
            granted_by=granted_by,
            note=note,
        )
        self._grants[task_id] = entry
        self._history.append(entry)
        return entry

    def revoke(self, task_id: str) -> bool:
        """Drop any grant for ``task_id``. Returns ``True`` if one existed."""
        return self._grants.pop(task_id, None) is not None

    def granted_price(self, task_id: str) -> float | None:
        """The price a human approved for ``task_id``, if any."""
        grant = self._grants.get(task_id)
        return grant.price if grant else None

    def pending(self) -> tuple[str, ...]:
        """Task ids with an unspent grant."""
        return tuple(self._grants)

    @property
    def history(self) -> tuple[ApprovalGrant, ...]:
        """Every grant ever recorded, in order — an audit trail."""
        return tuple(self._history)

    def check(self, task_id: str | None, *, price: Any = None) -> ApprovalGrant:
        """Verify a grant covers this approval without spending it.

        Raises:
            ApprovalNotConfirmedError: No grant recorded for ``task_id``.
            QuoteChangedError: The grant's price and ``price`` disagree.
        """
        if not task_id:
            raise ApprovalNotConfirmedError(
                None, "approve_task was called without a task_id"
            )
        grant = self._grants.get(task_id)
        if grant is None:
            raise ApprovalNotConfirmedError(
                task_id,
                "no human approval has been recorded for this task. Show the "
                "contract scope and price to a human, then have application "
                "code call HumanApprovalGate.grant(task_id, confirmed=True, "
                "price=...).",
            )
        if self.require_price_match and grant.price is not None:
            current = _as_float(price)
            if current is None or not math.isclose(
                current, grant.price, rel_tol=_PRICE_TOLERANCE, abs_tol=_PRICE_TOLERANCE
            ):
                raise QuoteChangedError(task_id, grant.price, price)
        return grant

    def consume(self, task_id: str | None, *, price: Any = None) -> ApprovalGrant:
        """Verify a grant covers this approval and spend it if single-use.

        Raises:
            ApprovalNotConfirmedError: No grant recorded for ``task_id``.
            QuoteChangedError: The grant's price and ``price`` disagree.
        """
        grant = self.check(task_id, price=price)
        if self.single_use and task_id:
            self._grants.pop(task_id, None)
            self._history.append(grant._consumed())
        return grant


def _matches_tool(request_name: str, tool_name: str) -> bool:
    """Match a tool name allowing for adapter server-name prefixing."""
    return request_name == tool_name or request_name.endswith(f"_{tool_name}")


def _refusal_text(exc: TendemError) -> str:
    return (
        f"REFUSED — this call was blocked before reaching Tendem. {exc}\n\n"
        "approve_task spends the user's money, so it is gated on a recorded "
        "human decision. Do not retry this call. Instead: call get_contract, "
        "present the scope and price to the user, and let them decide."
    )


class SpendGuardInterceptor:
    """MCP tool-call interceptor that blocks unapproved ``approve_task`` calls.

    Implements ``langchain_mcp_adapters.interceptors.ToolCallInterceptor``. It
    short-circuits the handler — the blocked call never touches the network —
    and returns ``CallToolResult(isError=True)``, which the adapter surfaces to
    the model as a ``ToolMessage`` with ``status="error"`` so the agent can
    recover by asking a human instead of crashing the run.

    Args:
        gate: The ledger consulted for each approval attempt.
        approval_tool: Name of the guarded tool. Override only if the server
            renames it.
        on_block: Optional callback invoked with ``(request, exception)``
            whenever a call is blocked — useful for logging or surfacing a
            prompt to the human.
    """

    def __init__(
        self,
        gate: HumanApprovalGate,
        *,
        approval_tool: str = APPROVAL_TOOL_NAME,
        on_block: Callable[[Any, TendemError], None] | None = None,
    ) -> None:
        self.gate = gate
        self.approval_tool = approval_tool
        self.on_block = on_block

    async def __call__(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        """Gate ``approve_task``; pass everything else straight through."""
        if not _matches_tool(request.name, self.approval_tool):
            return await handler(request)

        args = request.args or {}
        try:
            self.gate.consume(args.get("task_id"), price=args.get("price"))
        except (ApprovalNotConfirmedError, QuoteChangedError) as exc:
            if self.on_block is not None:
                self.on_block(request, exc)
            return CallToolResult(
                content=[TextContent(type="text", text=_refusal_text(exc))],
                isError=True,
            )
        return await handler(request)


__all__ = ["ApprovalGrant", "HumanApprovalGate", "SpendGuardInterceptor"]
