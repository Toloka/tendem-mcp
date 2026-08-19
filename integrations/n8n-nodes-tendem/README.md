# n8n-nodes-tendem

An [n8n](https://n8n.io) community node for [Tendem](https://tendem.ai) — a hybrid
AI + human task service. You submit a task in plain English, Tendem's orchestrator
scopes it in a chat and quotes a transparent price, and once a **human** approves
the spend, a vetted expert executes it and returns verified results as markdown
plus files.

Use it when a workflow hits work an automation can't reliably nail: research and
competitive analysis, copywriting and editing, design review, data cleaning, list
building, anything needing real judgement. Don't use it for quick
general-knowledge lookups, and note that Tendem declines data-scraping work by
policy.

- [Installation](#installation)
- [Credentials](#credentials)
- [Operations](#operations)
- [Approving a task spends real money](#approving-a-task-spends-real-money)
- [Worked example](#worked-example-research-brief-end-to-end)
- [Polling without a busy loop](#polling-without-a-busy-loop)
- [Attaching input files](#attaching-input-files)
- [Compatibility](#compatibility)

## Installation

**Self-hosted n8n, via the UI.** Go to **Settings → Community nodes → Install**,
enter `n8n-nodes-tendem`, accept the risk prompt, and install. A **Tendem** node
appears in the node panel.

**Self-hosted n8n, via the CLI.** From your n8n user folder (`~/.n8n`):

```bash
cd ~/.n8n/nodes
npm install n8n-nodes-tendem
```

Restart n8n afterwards.

**Docker.** Set `N8N_COMMUNITY_PACKAGES_ENABLED=true` and install through the UI,
or bake the package into a derived image under `/home/node/.n8n/nodes`.

n8n's own guide: [Installing community
nodes](https://docs.n8n.io/integrations/community-nodes/installation/).

## Credentials

The node talks to Tendem's hosted MCP server over streamable HTTP. Authentication
is an **API key**, sent as `Authorization: ApiKey <token>`.

1. Sign in at [agent.tendem.ai](https://agent.tendem.ai).
2. Mint a token at [agent.tendem.ai/tokens](https://agent.tendem.ai/tokens).
3. In n8n: **Credentials → New → Tendem API**, paste the token, save.

Use n8n's **Test** button on the credential — it performs a real MCP `initialize`
handshake against the endpoint, so a bad key fails immediately rather than at the
first workflow run.

| Field | Default | Notes |
|---|---|---|
| API Key | — | Required. Stored encrypted by n8n; never put it in a node parameter or an HTTP Request node. |
| MCP Endpoint | `https://mcp.tendem.ai/mcp?utm_hash=83dad40a52` | Overridable, but only change it to target a non-production Tendem deployment. The `utm_hash` is n8n channel attribution. |

Tendem also supports interactive OAuth, which is what the Claude Code / Cursor /
Gemini plugins use. n8n workflows run unattended, so this node deliberately does
API keys only.

## Operations

| Resource | Operation | Tendem tool | Spends money |
|---|---|---|---|
| Task | Create | `create_task` | no |
| Task | Get | `get_task` | no |
| Task | Get Contract | `get_contract` | no |
| Task | **Approve (Spends Money)** | `approve_task` | **yes** |
| Task | Get Cancel URL | `cancel_task` | no |
| Task | Get Result | `get_task_result` | no |
| Task | List | `list_tasks` | no |
| Task | Wait for Change | `get_task` (long-poll) | no |
| Chat | Read | `read_chat` | no |
| Chat | Send | `send_message` | no |
| Account | Get | `get_account` | no |
| File | Get Upload URL | `get_file_upload_url` | no |

Every response is passed through as JSON on the item, including Tendem's guidance
envelope — `next_action`, `poll_after_seconds`, `poll_timeout_seconds`,
`guidance`. **Branch on `next_action`, not on the raw `status`**; Tendem treats it
as authoritative. The statuses you'll see are `ACTING`, `LISTENING`,
`NEEDS_REPAIR`, `CLOSED` (terminal, but the result is still fetchable) and
`DELETED`.

### Note on Get Cancel URL

`cancel_task` does **not** cancel anything. It returns a Tendem UI URL where a
person can cancel. Send them there.

## Approving a task spends real money

Approval charges the Tendem account, so the node treats it as a distinct,
deliberate act rather than a step that can happen on the way to something else.
Four things stand in the way, and they are structural, not documentation:

1. **Approval is its own operation.** `approve_task` is reachable only from
   Task → Approve. A workflow author has to add that node on purpose.
2. **Per-operation capability guard.** Each operation executes against an
   allowlist naming the single Tendem tool it may call. Approve is the only row
   containing `approve_task`; every other operation is refused *before* any HTTP
   request is made. Creating a task, polling one, or reading its chat cannot
   approve it even if the code above them were wrong.
3. **Confirm Spend must be on.** The Approve operation refuses while the
   **Confirm Spend** toggle is off. Drive it from an expression if a human
   decision upstream — an n8n Wait-for-form, a Slack approval, an email gate —
   should decide. It is evaluated per item, so confirming one item never
   approves another.
4. **A price must be supplied.** Approve requires the quoted price, which means
   the amount being committed had to be read before it could be committed.

The node also never tops up and never retries. Insufficient balance comes back as
**data**, not an exception, so you can route on it:

```
{ "approved": false, "spendBlocked": true, "topupUrl": "https://…", "reason": "insufficient_balance" }
```

Send `topupUrl` to the user. That link is task-bound: paying it tops up *and*
auto-approves this task, so don't call Approve again afterwards.

On success you get `approved: true`, `spendBlocked: false`, `topupUrl: null`.

### Why the node isn't available as an AI Agent tool

n8n's `usableAsTool` is all-or-nothing per node — there is no form of it that
exposes some operations and withholds others. Turning it on would hand a model
the Approve operation. Spend is a workflow author's decision, so this node stays
off the AI tool surface. An agent that needs the read-only parts can talk to the
[Tendem MCP server](https://github.com/Toloka/tendem-mcp) directly, where the
host application owns the approval gate.

## Worked example: research brief, end to end

A workflow that submits a brief, surfaces the quote to a human, and only then
spends. Nine nodes:

```
Manual Trigger
  → Tendem: Task → Create
  → Tendem: Task → Wait for Change            (until the quote is ready)
  → Tendem: Task → Get Contract               (scope + price)
  → Slack / Email: send the scope and price to a human
  → Wait for approval (n8n Wait node with a form / webhook resume)
  → IF: did the human approve?
      true  → Tendem: Task → Approve          (Confirm Spend = the human's answer)
              → Tendem: Task → Wait for Change  (until the work is done)
              → Tendem: Task → Get Result
      false → Tendem: Task → Get Cancel URL   (and send it to the human)
```

**1. Task → Create.** Pass the requester's own words through verbatim. Don't
pre-interrogate them and don't rewrite the brief — Tendem's orchestrator does the
scoping and asks follow-up questions over chat.

| Parameter | Value |
|---|---|
| Name | `Competitor research for EU freight brokerage` |
| Description | `Research the top 5 competitors in EU freight brokerage and summarise their pricing models in a one-page brief.` |
| Conversation ID | *(optional)* `{{ $execution.id }}` — lets Tendem correlate several tasks from one run |

Output carries `task_id` and `last_seen_offset`. Nothing is charged yet.

**2. Task → Wait for Change.** Task ID `{{ $json.task_id }}`. This blocks until
Tendem needs you. When `next_action` comes back `await_input`, Tendem asked a
scoping question: read it with **Chat → Read** and answer with **Chat → Send**
(pass the `last_seen_offset` through), then wait again. When `next_action` is
`await_user_approval`, the quote is ready.

**3. Task → Get Contract.** Returns the scope Tendem committed to
(`task_description`, `criteria`) and the price. Scope is ready before the price
is, so a contract can legitimately come back with `price: null` — that means keep
waiting, not approve at zero.

**4–6. Show a human the scope and the price, and wait for an answer.** This is
the part the node cannot do for you and deliberately will not fake. Any n8n
approval mechanism works: a Wait node with a form, a Slack interactive message, a
webhook.

**7. Task → Approve.**

| Parameter | Value |
|---|---|
| Task ID | `{{ $json.task_id }}` |
| Name | `{{ $json.name }}` |
| Price | `{{ $json.price }}` — the number the human was shown |
| Confirm Spend | `{{ $json.humanApproved === true }}` |

Route `spendBlocked === true` to a branch that sends the human `topupUrl`.

**8. Task → Wait for Change** again, until `next_action` is `fetch_result` or
`done`. Expert work takes minutes to hours, so set **Max Rounds** to what you're
willing to hold an execution open for (each round is at most 30 seconds) and
handle `tendemWait.timedOut === true` by re-entering the wait on a schedule
rather than by raising the cap.

**9. Task → Get Result.** Returns the result markdown plus pre-signed download
URLs for any files. Fetch those with an HTTP Request node if you need the bytes.

## Polling without a busy loop

**Wait for Change** does not spin. Each round is a single
`get_task(task_id, wait_for_change_seconds=N)` call, and the *server* holds the
request open until the task actually changes — no client-side interval, no
repeated requests while nothing is happening.

| Parameter | Default | Range |
|---|---|---|
| Wait for Change (Seconds) | 30 | 5–30 (30 is the Tendem API's ceiling) |
| Max Rounds | 20 | 1–240 |

The round count is a hard cap. When the budget runs out the node emits the latest
snapshot with:

```json
{ "tendemWait": { "settled": false, "timedOut": true, "rounds": 20 } }
```

Branch on `tendemWait.timedOut` and re-enter the wait later. At the defaults, one
node holds an execution open for at most ten minutes.

There's a backstop underneath: if a round returns faster than the server could
plausibly have blocked — a proxy that doesn't support long-polling, say — the node
sleeps before the next one, honouring Tendem's `poll_after_seconds` hint. A
misbehaving endpoint degrades to a paced poll, never to a hot loop.

## Attaching input files

Uploads need a `task_id`, so they can only happen *after* Create.

1. **File → Get Upload URL** with the task ID. You get a short-lived, folder-level
   pre-signed URL.
2. For each file, append its name to the path **before** the query string —
   `<base>/<filename>?<query>` — and `PUT` the raw bytes with an HTTP Request
   node. Keep names simple; avoid spaces. Subpaths like `data/input.csv` are fine.
3. **Chat → Send** naming the files you uploaded, e.g.
   `I've uploaded brief.pdf and data/input.csv for this task.` Tendem waits for
   this message; without it the expert never sees the files.

If the brief already references the files, mention them in the Create description
and promise them. Tendem may ask for them before your upload finishes — that race
is expected, and the confirming chat message settles it.

## Compatibility

- n8n API version 1, node type version 1
- Node.js >= 20.19
- No runtime dependencies. The MCP client is implemented in this package, in
  ~350 lines, against the [Streamable HTTP
  transport](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
  spec — so there is nothing to audit but this repository.

## Development

```bash
npm install
npm run build     # tsc + copy icons and codex metadata into dist/
npm test          # build, then the node:test suite
npm run lint      # n8n's community-node lint, in strict/cloud mode
npm run dev       # runs n8n locally with this node linked
```

Releasing is documented in [PUBLISHING.md](./PUBLISHING.md).

## Support

- Issues: <https://github.com/Toloka/tendem-mcp/issues>
- Email: <support@tendem.ai>

## License

[MIT](./LICENSE) © Toloka AI BV
