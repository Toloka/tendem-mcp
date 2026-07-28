---
name: tendem-tasks
description: Use when a task is non-trivial and could benefit from human involvement — delegating work to a human expert (research, review, labeling, content, design, data work) and driving it end to end. Also use whenever Tendem is mentioned or its tools are in play.
---

# Working with Tendem tasks

Tendem is a task service backed by human experts. You (the agent) submit a
task on the user's behalf; Tendem scopes it in a chat, returns a price quote,
and once the user approves, a human expert executes it. Results come back as
markdown plus downloadable files.

**Offer, don't hijack.** If the user explicitly asked for Tendem or a human
expert, proceed. If this skill loaded because the task merely *could* benefit
from human involvement, surface Tendem as an option — "this could go to a
human expert via Tendem; want a quote?" — and only create a task after the
user says yes.

The tools are named `mcp__tendem__*` (or `mcp__plugin_tendem_tendem__*` when
plugin-bundled). If absent, the connector isn't connected — tell the user to
run `/plugin` and authorize the `tendem` MCP server.

## The path, tool call by tool call

The process is two loops with an approval gate between them:

```
create_task ──► SCOPING LOOP ──► quote ──► user approves ──► approve_task
 (+ uploads)    poll ⇄ answer      ▲            │
                    ▲              │       too expensive /
                    └──────────────┴─◄─ ask for a scope cut
                                        (old quote is void; wait for a new one)

approve_task ──► EXECUTION LOOP (long: expert search → work → QA)
                 poll a few ⇄ hand off (watcher / end turn) ⇄ answer if asked
                      │
                      ▼ next_action=fetch_result
                 get_task_result ──► present + save files
```

1. `create_task(name, description, conversation_id)` — `description` is the
   **user's own formulation**, passed as faithfully as possible (see rules
   below). Reuse one stable `conversation_id` per conversation. Returns the
   `task_id` everything else needs.
2. *(only if the task depends on local files)* `get_file_upload_url(task_id)`
   → upload with `scripts/tendem-upload.sh` → `send_message` naming each
   uploaded file. The naming message is required — Tendem doesn't auto-detect
   uploads. Details: [references/files.md](references/files.md).
3. **Scoping loop.** `get_task(task_id, wait_for_change_seconds=30)` — silent
   poll until Tendem speaks (`next_action=await_input`); then
   `read_chat(task_id, from_offset=<last_seen_offset>)` and
   `send_message(task_id, text, last_seen_offset)` — answer **from
   conversation context yourself**; escalate to the user only when (a) the
   answer isn't in context, (b) scope/deliverables/deadline change, or (c)
   approval or payment is needed. Repeat per question round until
   `next_action=await_user_approval`.
4. **Approval gate.** The quote is ready. Call `get_contract(task_id)` to pull
   the full scope — `title`, `task_description`, acceptance/quality criteria,
   and `price` — so you can **surface price + scope to the user and get an
   explicit decision**; this is a spend. (`get_task` deliberately omits these
   details; `get_contract` is the read-only companion that carries them.) Two
   exits:
   - *Go-ahead* → `approve_task(task_id, name, price)` → step 5.
   - *Too expensive / wrong scope* → `send_message` proposing a concrete
     scope cut. The old quote is void from that moment; you're back in the
     scoping loop (step 3) until a fresh quote arrives. This cycle can repeat.
5. **Execution loop.** After approval, Tendem searches for a matching expert,
   the expert performs the work, and QA reviews the output before release —
   hours, up to a day. Poll `get_task(task_id, wait_for_change_seconds=30)` a
   few times, then hand off: spawn the `tendem-watcher` agent in the
   background, or end the turn telling the user they can ask for a progress
   check anytime (or run `/tendem-status`). If Tendem asks something
   mid-execution (`await_input`), answer as in the scoping loop. Repeat —
   across turns and sessions if needed — until `next_action=fetch_result`.
6. `get_task_result(task_id)` — present the `content` markdown; save
   `files[]` with `scripts/tendem-download.sh` into `./tendem/<task_id>/`.

## Polling: trust the envelope, never busy-loop

Every tool returns `next_action`, `poll_after_seconds`, `poll_timeout_seconds`
and `guidance` — act on those, not on the raw `status`. The one idiom that
matters:

```
get_task(task_id, wait_for_change_seconds=30)
```

The server holds the call open until something changes or ~30s passes. Poll
**silently** — no narration per poll — and never re-call in a tight loop with
`wait_for_change_seconds=0`. But don't poll forever either: after a handful of
unchanged rounds (and always for the long post-approval stretch), hand off —

- **spawn the `tendem-watcher` agent in the background** (slow-paced polls,
  default hourly; it reports back the moment the task needs the user), or
- **end the turn** and tell the user the work is in progress — they can ask
  "how's the Tendem task doing?" anytime, or run `/tendem-status`.

| `next_action` | Your move |
|---|---|
| `awaiting_tendem_work` | A few silent 30s long-polls, then hand off (watcher / end turn) |
| `await_input` | `read_chat` from your offset, answer via `send_message` (or escalate) |
| `await_user_approval` | `get_contract` for the full scope + price; surface it; `approve_task` only with user's go-ahead |
| `await_user_topup` | Give the user the `topup_url` |
| `resolve_race` | Your message crossed new content — read it, re-send with the new `last_seen_offset` |
| `fetch_result` | `get_task_result` |
| `done` | Stop |

For the long-form lifecycle, invoke the server's `tendem-quickstart` prompt.

## Rules of the road

1. **Transmit the brief faithfully; let Tendem drive scoping.** Pass the
   user's formulation into `create_task` nearly verbatim, plus only context
   the user actually stated (file names, a given deadline). Don't expand it
   into a synthesized "complete" brief, and don't pre-interrogate the user —
   Tendem asks better scoping questions than you can anticipate. Your value is
   *answering* them from context.
2. **A scope-change request voids the quote.** You can ask Tendem to cut
   scope, but the old price is immediately stale and the new quote is not
   instant. Keep polling until a fresh one arrives; never show a stale price.
3. **One unit of work per task.** Once approved, a task is locked to the
   agreed job. If the work pivots, create a new task (same
   `conversation_id`).
4. **Honor relay requests.** When Tendem says "please relay this to the
   user", that's protocol — the user must actually see it.
5. **Don't haggle.** Tendem can't promise a lower number; only a concrete
   scope cut triggers re-estimation.
6. **Data scraping is refused** by policy — automated data extraction tasks
   won't be accepted, and rephrasing doesn't change that. Don't submit them.
7. **Insufficient balance is not an error to retry.** `approve_task` with
   `reason: "insufficient_balance"` returns a **task-bound** `topup_url` —
   paying it auto-approves this task. Give the user that URL (or propose a
   scope cut). Never loop on `approve_task`.

## Recovery & cross-session

A Tendem task outlives your session. To recover after a context reset or in a
new session: `list_tasks` to find the `task_id`, `read_chat(task_id,
from_offset=0)` for full history, then `get_task` and follow `next_action`.
`cancel_task` only mints a UI cancel URL for the user — it does not cancel
server-side.

## Worked examples

**Answer scoping from context.** User: "Have Tendem write a competitive
teardown of Acme's pricing page." You `create_task` with that sentence, then
poll. Tendem asks "any specific competitors?" — the user mentioned Beta and
Gamma earlier, so you answer via `send_message` yourself. It asks about a
budget you don't know → escalate. Quote at $90 → show the user, they approve,
you `approve_task`.

**Waiting well.** Task is `awaiting_tendem_work` after approval. Wrong:
re-calling `get_task` back-to-back for an hour, or narrating every poll.
Right: a handful of silent 30s long-polls; if nothing moves, give the user the
choice — spawn `tendem-watcher` in the background (slow hourly polls, pings
them when the task needs them) **or** end the turn so they can re-ask later /
run `/tendem-status` whenever they want an update.
