# langchain-tendem

Human-in-the-loop for agentic pipelines. [Tendem](https://tendem.ai) is a
hybrid AI + human task service; this package wraps it into **four LangChain
tools** your agent drives — delegation to a real human expert with no
interruptions and no paywalls, capped by a budget you set in advance.

## Quickstart

```bash
pip install langchain-tendem
# key: agent.tendem.ai/mcp → "Agent builders" tab
export TENDEM_API_KEY=...
```

```python
from langchain.agents import create_agent
from langchain_tendem import tendem_tools

agent = create_agent(
    "anthropic:claude-sonnet-4-5",
    tools=tendem_tools(max_price=25.0),
)
```

That's the whole configuration: an API key and a spend cap.

## The four tools

- **`create_human_task(request, file_paths)`** — the only non-idempotent
  call, so it is thin and deterministic: create + upload files + announce
  them, **no polling**, done in seconds. Returns the `task_id`, which lands
  in the (checkpointed) conversation — a retried agent never duplicates a
  task it knows about.
- **`check_human_task(task_id)`** — polls in plain Python (no LLM, no
  tokens) and forwards only what needs the agent: a MESSAGE from the service
  (your agent answers from its own context — the agent-to-agent conversation
  the MCP is designed around), a `QUOTE EXCEEDS CAP` report carrying the
  full contract scope (the agent narrows the scope or walks away, nothing
  charged), or `STARTED` after **auto-approving** a quote within the cap.
  The flow never stops for a payment decision — the cap is the consent.
- **`reply_to_human_task(task_id, reply)`** — send the answer or the scope
  reduction, then keep polling like `check_human_task`.
- **`wait_for_human_result(task_id)`** — blocks until the verified result
  (markdown + pre-signed file URLs). Idempotent: interrupted or
  `IN PROGRESS`, just call it again.

Everything except `create_human_task` is stateless against the `task_id` and
survives crashes and checkpoint replays.

## How it behaves

- **The cap is the safety model.** Quotes ≤ `max_price` are approved
  automatically; quotes above it are surfaced with the scope for the agent
  to renegotiate — **nothing is ever charged** on any refusal path. An empty
  balance returns a task-bound top-up URL (paying it auto-approves the task).
- **Waiting is free.** Human work takes minutes to hours; all waiting is
  server-side long-polling in Python. Transient network errors and the
  server's `TEMPORARILY_UNAVAILABLE` are retried with backoff.
- **Trivial briefs are answered free** by Tendem's orchestrator in the
  scoping chat; the tools return those answers directly, marked uncharged.
- **Input files ride along**: `file_paths` on create (paths, uploaded under
  basenames), announced to the service as the protocol requires; results
  come back as pre-signed download URLs.
- **Business outcomes are strings, not exceptions** (`QUOTE EXCEEDS CAP`,
  `NOT EXECUTED`, `IN PROGRESS`), so any agent loop can read and recover.
- **Limits:** briefs must be self-contained (the expert sees only the task
  text and files); data scraping is refused by Tendem policy; one unit of
  work per task.

## Programmatic use

The same engine is importable for non-agent code: `prepare_task(client,
description, files=...)` creates a task, and `advance_task(client, task_id,
max_price=..., timeout=..., reply=...)` drives it, returning typed
`TaskEvent`s (`result` / `question` / `approved` / `over_budget` /
`pending`). Underneath sits the typed `Tendem` client — `create_task`,
`poll`, `get_contract`, cap-gated `approve_task`, `get_task_result`,
`upload_files`, `read_chat`, `send_message`, and raw `call` — plus
`tendem.get_tools()`, which loads the 11 raw MCP tools with model-issued
`approve_task` calls capped by the same `max_price`.

Logging goes to the `langchain_tendem` logger (created, approved, uploaded,
result) — enable INFO to watch a pipeline run.

## Development

```bash
uv venv && uv pip install -e '.[test]'
uv run pytest   # network-free; fixtures mirror live server payloads
```

MIT. Part of [Toloka/tendem-mcp](https://github.com/Toloka/tendem-mcp).
