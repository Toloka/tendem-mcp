"""LangChain integration for Tendem — hybrid AI + human expert task execution.

Tendem takes a task written in plain English, scopes it in a chat, quotes a
price, and — after a human approves the spend — a vetted expert executes it and
returns verified markdown plus files.

```python
from langchain_tendem import Tendem

tendem = Tendem()  # OAuth on first use; Tendem(api_key=...) for headless
tools = await tendem.get_tools()  # LangChain tools, approve_task gated
```

The generic MCP adapter can reach the same server. What this package adds:

* **Lifecycle helpers** for create → scope → approve → execute → fetch, typed,
  instead of 11 raw tools with no ordering.
* **A structural spend guardrail.** ``approve_task`` charges the user, so it is
  unreachable without an explicit human decision — ``confirmed=True`` on
  :meth:`Tendem.approve_task`, or a recorded grant on
  :class:`HumanApprovalGate` for the model-driven path.
* **Bounded polling.** :meth:`Tendem.poll` uses the server's
  ``wait_for_change_seconds`` blocking window, caps the number of rounds, and
  enforces a minimum round duration, so a busy loop is not expressible.
"""

from __future__ import annotations

from langchain_tendem.approval import (
    ApprovalGrant,
    HumanApprovalGate,
    SpendGuardInterceptor,
)
from langchain_tendem.client import Tendem
from langchain_tendem.constants import (
    LANGCHAIN_UTM_HASH,
    TENDEM_MCP_BASE_URL,
    TENDEM_MCP_URL,
    TENDEM_TOKENS_URL,
    TOOL_NAMES,
)
from langchain_tendem.errors import (
    ApprovalNotConfirmedError,
    PollTimeoutError,
    QuoteChangedError,
    TendemError,
    TendemProtocolError,
    TendemToolError,
)
from langchain_tendem.models import (
    ApprovalOutcome,
    Contract,
    NextAction,
    TaskResult,
    TaskSnapshot,
    TaskStatus,
    TendemFile,
)

__version__ = "0.1.0"

__all__ = [
    "LANGCHAIN_UTM_HASH",
    "TENDEM_MCP_BASE_URL",
    "TENDEM_MCP_URL",
    "TENDEM_TOKENS_URL",
    "TOOL_NAMES",
    "ApprovalGrant",
    "ApprovalNotConfirmedError",
    "ApprovalOutcome",
    "Contract",
    "HumanApprovalGate",
    "NextAction",
    "PollTimeoutError",
    "QuoteChangedError",
    "SpendGuardInterceptor",
    "TaskResult",
    "TaskSnapshot",
    "TaskStatus",
    "Tendem",
    "TendemError",
    "TendemFile",
    "TendemProtocolError",
    "TendemToolError",
    "__version__",
]
