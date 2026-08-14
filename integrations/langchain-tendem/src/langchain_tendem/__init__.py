"""LangChain integration for Tendem — hybrid AI + human expert task execution.

Tendem takes a task written in plain English, scopes it in a chat, quotes a
price, and a vetted human expert executes it and returns verified markdown
plus files. This package wraps that into four LangChain tools driven by your
agent:

```python
from langchain.agents import create_agent
from langchain_tendem import tendem_tools

agent = create_agent("...", tools=tendem_tools(max_price=25.0))
```

* ``create_human_task`` — thin and deterministic: create + upload files +
  announce, no polling; returns the durable ``task_id`` in seconds.
* ``check_human_task`` / ``reply_to_human_task`` — poll in plain Python (no
  LLM, no tokens) and forward only what needs the agent: Tendem's messages
  (the calling LLM answers them from its own context) and over-cap quotes
  (the agent narrows the scope or walks away, nothing charged). Quotes at or
  below ``max_price`` are approved automatically — the cap is the consent,
  the flow never stops for a payment decision.
* ``wait_for_human_result`` — idempotent long wait for the deliverable;
  interrupted or timed out, call it again with the same ``task_id``.

Everything except ``create_human_task`` is stateless against the ``task_id``,
so crashes and checkpoint replays lose nothing. For programmatic (non-tool)
use, the same engine is importable: ``prepare_task`` + ``advance_task`` from
``langchain_tendem.runner``, and the typed ``Tendem`` client underneath.
"""

from __future__ import annotations

from langchain_tendem.client import SpendGuardInterceptor, Tendem
from langchain_tendem.constants import (
    LANGCHAIN_UTM_HASH,
    PACKAGE_VERSION,
    TENDEM_MCP_URL,
)
from langchain_tendem.errors import (
    ApprovalBlockedError,
    PollTimeoutError,
    PriceCeilingExceededError,
    TaskFailedError,
    TendemError,
    TendemProtocolError,
    TendemToolError,
    TopUpRequiredError,
)
from langchain_tendem.models import (
    ApprovalOutcome,
    Contract,
    NextAction,
    TaskOutcome,
    TaskResult,
    TaskSnapshot,
    TaskStatus,
    TendemFile,
    parse_action,
    parse_status,
)
from langchain_tendem.runner import (
    TaskEvent,
    advance_task,
    prepare_task,
    upload_files,
)
from langchain_tendem.tools import tendem_tools

__version__ = PACKAGE_VERSION

__all__ = [
    "LANGCHAIN_UTM_HASH",
    "TENDEM_MCP_URL",
    "ApprovalBlockedError",
    "ApprovalOutcome",
    "Contract",
    "NextAction",
    "PollTimeoutError",
    "PriceCeilingExceededError",
    "SpendGuardInterceptor",
    "TaskEvent",
    "TaskFailedError",
    "TaskOutcome",
    "TaskResult",
    "TaskSnapshot",
    "TaskStatus",
    "Tendem",
    "TendemError",
    "TendemFile",
    "TendemProtocolError",
    "TendemToolError",
    "TopUpRequiredError",
    "__version__",
    "advance_task",
    "parse_action",
    "parse_status",
    "prepare_task",
    "tendem_tools",
    "upload_files",
]
