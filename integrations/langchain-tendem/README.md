# langchain-tendem

LangChain integration for [Tendem](https://tendem.ai) — a hybrid AI + human task
service. You submit a task in plain English, Tendem's orchestrator scopes it in a
chat, quotes a transparent price, and once a **human** approves the spend, a
vetted expert executes it and returns verified results as markdown plus files.

```bash
pip install langchain-tendem
```

```python
from langchain_tendem import Tendem

tendem = Tendem()                 # OAuth on first use
tools = await tendem.get_tools()  # LangChain tools; approve_task is gated
```

Drop `tools` into any LangChain or LangGraph agent — they are plain
`StructuredTool`s.

## Why not just use `langchain-mcp-adapters`?

You can: Tendem is a standard streamable-HTTP MCP server, and the generic adapter
will happily hand your model all 11 tools. This package exists because raw access
is the easy part.

| | generic adapter | `langchain-tendem` |
|---|---|---|
| Lifecycle | 11 tools, no ordering, no typing | typed helpers for create → scope → approve → execute → fetch |
| Spend approval | model can call `approve_task` freely | structurally gated on an explicit human decision |
| Stale quotes | a voided quote can still be charged | price-bound grants refuse the mismatch |
| Polling | whatever the model improvises | server-side long-poll, bounded rounds, paced floor |
| Attribution | no channel hash | `?utm_hash=9cfb868c94` by default |

## Authentication

**OAuth 2.0 (interactive, default).** Construct `Tendem()` with no key. The MCP
transport runs the OAuth flow on first use; the user signs in at
[agent.tendem.ai](https://agent.tendem.ai).

**API key (headless).** For pipelines with no browser, mint a token at
[agent.tendem.ai/tokens](https://agent.tendem.ai/tokens). It is sent as
`Authorization: ApiKey <token>`.

```python
tendem = Tendem(api_key="tk_live_...")     # explicit
tendem = Tendem()                          # or set TENDEM_API_KEY in the env
```

## Approval is a human decision — and the code enforces it

Approving a Tendem task **charges the user's account**. This package therefore
makes `approve_task` unreachable without an explicit, unambiguous opt-in. Not a
convention in the docs — a gate in the call path.

**Programmatic path.** `confirmed` must be exactly `True`. `"yes"`, `1`, and any
other truthy value are rejected, so a stringly-typed model argument cannot pass
as consent:

```python
await tendem.approve_task(task_id)                     # ApprovalNotConfirmedError
await tendem.approve_task(task_id, confirmed="yes")    # ApprovalNotConfirmedError
await tendem.approve_task(task_id, confirmed=True, price=120.0)   # charges
```

A price must also be *known*. Scope is available before the quote is, so
`approve_task` on a contract with no price yet is refused too — an approval
nobody could have seen a number for is not an informed one. Omitting `price`
is fine once the contract carries one; it is then read from the contract.

**Model-driven path.** The `approve_task` tool returned by `get_tools()` is
wrapped by `SpendGuardInterceptor`. Unless your application recorded a human's
decision on the gate for that exact `task_id`, the call is short-circuited before
it reaches the network and the model gets an error `ToolMessage` telling it to ask
a human:

```python
# ... your UI showed the contract and the person clicked Approve:
tendem.approval_gate.grant(task_id, confirmed=True, price=120.0,
                           granted_by="alex@example.com")
```

Grants are **single-use** and **price-bound** by default — `price` is required,
because a grant without one would authorise any amount. Price binding closes the
stale-quote hole: asking Tendem to cut scope voids the old quote, so a grant
recorded at $90 will not authorise a charge at $120 — you get `QuoteChangedError`
and have to show the new number. `HumanApprovalGate(require_price_match=False)`
opts out, deliberately.

`tendem.approval_gate.history` is an audit trail of every grant and its use.

## Polling never busy-loops

`get_task` supports a server-side blocking window: the server holds the request
open until the task changes or the window elapses. `Tendem.poll` uses it, and adds
two independent bounds:

1. at most `max_rounds` rounds (default 6), then `PollTimeoutError`;
2. a minimum wall-clock duration per round, so even a server that answers
   instantly cannot produce a tight loop.

`PollTimeoutError` is not a failure. Tendem execution takes hours; the exception
is the cue to **hand off** — attach a background watcher, or tell the user they
can check back — not to raise the budget.

```python
from langchain_tendem import PollTimeoutError

try:
    snapshot = await tendem.poll(task_id)
except PollTimeoutError as exc:
    print("still working:", exc.snapshot.guidance)  # hand off here
```

## Worked example: a task end to end

```python
import asyncio
from langchain_tendem import NextAction, PollTimeoutError, Tendem


async def main() -> None:
    async with Tendem() as tendem:  # one MCP session for the whole flow
        # 1. Submit the user's own words. Don't pre-interrogate them and don't
        #    synthesise a "complete" brief — Tendem asks better questions.
        task = await tendem.create_task(
            "Pricing page teardown",
            "Write a competitive teardown of Acme's pricing page.",
            conversation_id="conv-42",
        )

        # 2. Scoping loop. Answer from context; return None to escalate.
        async def answer(chat, snapshot):
            messages = chat.get("messages") or []
            question = messages[-1].get("text", "") if messages else ""
            if "competitor" in question.lower():
                return "Compare against Beta and Gamma."
            return None  # not in context — a human must reply

        try:
            snapshot = await tendem.drive_scoping(task.task_id, answer=answer)
        except PollTimeoutError as exc:
            print("still scoping. Check back later:", exc.snapshot.guidance)
            return

        if snapshot.action is not NextAction.AWAIT_USER_APPROVAL:
            print("needs a human:", snapshot.guidance)
            return

        # 3. Approval gate. Show scope AND price, then get a real decision.
        contract = await tendem.get_contract(task.task_id)
        print(contract.summary())
        if input("Approve this spend? [y/N] ").strip().lower() != "y":
            return  # or send_message() proposing a concrete scope cut

        outcome = await tendem.approve_task(
            task.task_id, confirmed=True, price=contract.price
        )
        if not outcome.approved:
            if outcome.needs_topup:
                print("Top up to auto-approve:", outcome.topup_url)
            return

        # 4. Execution takes hours. Poll a few rounds, then hand off.
        try:
            snapshot = await tendem.poll(task.task_id, max_rounds=4)
        except PollTimeoutError as exc:
            print("In progress. Check back later:", exc.snapshot.guidance)
            return

        # 5. Fetch the deliverable.
        if snapshot.result_ready:
            result = await tendem.get_task_result(task.task_id)
            print(result.content)
            for f in result.files:
                print(f.name, f.url)  # pre-signed, short-lived


asyncio.run(main())
```

## Public API

| Object | Purpose |
|---|---|
| `Tendem` | client: `get_tools`, `create_task`, `get_task`, `poll`, `drive_scoping`, `get_contract`, `approve_task`, `get_task_result`, `read_chat`, `send_message`, `list_tasks`, `cancel_task`, `get_account`, `get_file_upload_url`, `call` |
| `HumanApprovalGate` | ledger of human spend approvals (`grant`, `check`, `consume`, `revoke`, `history`) |
| `SpendGuardInterceptor` | MCP interceptor enforcing the gate on `approve_task` |
| `TaskSnapshot` | status + `next_action` envelope, with `needs_caller` / `needs_human` / `result_ready` / `is_terminal` |
| `Contract` | scope + price, with `is_quoted` and `summary()` |
| `ApprovalOutcome` | `approved`, `reason`, `topup_url`, `needs_topup` |
| `TaskResult`, `TendemFile` | delivered markdown and files |
| `NextAction`, `TaskStatus` | server vocabulary as enums, tolerant of unknown values |
| errors | `TendemError`, `ApprovalNotConfirmedError`, `QuoteChangedError`, `PollTimeoutError`, `TendemToolError`, `TendemProtocolError` |

Read `next_action` (via `TaskSnapshot.action`) rather than `status`:

| `next_action` | What to do |
|---|---|
| `awaiting_tendem_work` | a few long-polls, then hand off |
| `await_input` | `read_chat`, answer with `send_message` |
| `await_user_approval` | `get_contract`, show a human, then decide |
| `await_user_topup` | give the human `topup_url` |
| `resolve_race` | re-read the chat, re-send with the new offset |
| `fetch_result` | `get_task_result` |
| `done` | stop |

## Notes and limits

- **Data scraping is refused** by Tendem policy. Rephrasing does not help.
- **One unit of work per task.** After approval a task is locked to the agreed
  job; a pivot means a new task (same `conversation_id`).
- **Uploads are not auto-detected.** After `get_file_upload_url` and the upload,
  name each file in a `send_message`.
- **`cancel_task` does not cancel** server-side; it mints a Tendem-UI URL for the
  user.
- **Insufficient balance is not retryable.** `topup_url` is bound to the task and
  paying it auto-approves that task.
- `Tendem.get_tools()` is async, so this package does not subclass LangChain's
  `BaseToolkit` (whose `get_tools` is synchronous). Use `Tendem` directly.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[test]'
pytest
```

Tests mock the MCP transport end to end and never touch the network.

## License

MIT. Part of [Toloka/tendem-mcp](https://github.com/Toloka/tendem-mcp).
