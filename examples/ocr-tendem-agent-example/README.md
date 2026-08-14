# ocr-agentic — OCR with a human expert as the fallback

A folder of scanned documents goes in, a CSV comes out. One agent reads each
scan; whatever defeats it goes to a **real human expert** and lands in the same
CSV a while later.

```
documents/inbox/ ──► agent ──► report_fields ─────────► documents/extracted.csv
                       │                                          ▲
                       └──► create_human_task ──► … ──► wait ─────┘
                            (a human reads it, minutes to hours)
```

The hand-off is [`langchain-tendem`](https://pypi.org/project/langchain-tendem/).
The agent gets five tools — `report_fields` to finish, and the package's four to
buy a human's attention — and that is the entire application:
[agentic/flow.py](src/ocr_batch/agentic/flow.py) is the prompt and the loop, and
[agentic/guards.py](src/ocr_batch/agentic/guards.py) is the deterministic rails around the
tools (no duplicate tasks, no lost ids, no dead-end loops). Scoping, the price
quote, approving it under your cap, uploading the file and waiting out hours of
human work all happen inside the package.

**Two flows, one harness.** `ocr-agentic` and `ocr-scripted` are the same
program ([cli.py](src/ocr_batch/cli.py) — discovery, resume, `--watch`, the
CSV, the journal) with one function swapped, `(path) -> Record`:
`agentic_process_single_file` lets the agent above decide, while
`scripted_process_single_file` decides deterministically — a vision call, a
confidence gate in plain Python, and one `escalate()` call driving the
hand-off with no tool calling at all
([scripted/flow.py](src/ocr_batch/scripted/flow.py)). Same package, used as an ordinary
async API. Pick whichever suits: the agent adapts, the script is auditable
and never depends on a model choosing correctly.

The source tree is the argument, one directory per choice:
[agentic/](src/ocr_batch/agentic) is the smaller half — a prompt, a loop and
the rails that keep it honest — bought at the price of trusting a model to
choose. [scripted/](src/ocr_batch/scripted) spells every step out and gives
the stronger guarantee. [common/](src/ocr_batch/common) is what both stand
on; neither flow knows it is in a batch.

## Quickstart

Needs [uv](https://docs.astral.sh/uv/), an OpenAI-compatible endpoint with a
vision model, and a Tendem API key from
[agent.tendem.ai/mcp](https://agent.tendem.ai/mcp) ("Agent builders" tab).

```bash
uv sync
cp .env.example .env    # then fill in the two keys
```

```bash
uv run make-samples          # five invoice scans, one deliberately unreadable
uv run ocr-agentic           # the agentic flow → documents/extracted.csv
uv run ocr-scripted          # the same job, Tendem as a plain API → documents/scripted.csv
```

```
reading 5 document(s) from documents/inbox
tendem endpoint: https://mcp.tendem.ai/mcp (cap $10.00 per document)
bluepeak-analytics.png: model
cedarworks-supply.png: model
meridian-labs.png: model
north-harbor-logistics.png: model
velocity-print.png: human ($3.00)
5 documents: 4 read by the model, 1 by a human expert ($3.00)
→ documents/extracted.csv
```

Rows land in the CSV as each document settles: a batch stuck for hours behind
one expert still shows every other row on disk the moment it finishes. Every
row carries a `source` — `model` and `human` are answers and are never
re-processed; `processing` means a run is working on it right now (or was
interrupted — the task is still live); `failed` means this run gave up, with
the reason first in `notes`. `processing` and `failed` rows are retried on
the next run.

Try it with a tight cap first — a quote above the cap is refused with the
scope attached, and nothing is charged:

```bash
uv run ocr-agentic --max-price 0.5
```

## Configuration

`.env` (see [.env.example](.env.example)) — everything has a default except
the two keys:

| variable | what it does |
| --- | --- |
| `OPENAI_BASE_URL` | any OpenAI-compatible endpoint (OpenAI, Nebius, vLLM, …) |
| `OPENAI_API_KEY` | key for that endpoint |
| `OCR_MODEL` | the one model — needs vision **and** tool calling |
| `TENDEM_API_KEY` | key for the human expert service |
| `TENDEM_MCP_URL` | Tendem endpoint; unset = production, set it for prestable (read by the package itself) |
| `TENDEM_MAX_PRICE` | spend cap per escalated document, USD (default `10`) |
| `OCR_MIN_CONFIDENCE` | below this a human reads it, and a model-only row is never trusted (default `0.95`) |

Both commands take the same flags:

```bash
uv run ocr-agentic path/to/folder -o out.csv   # where to read from / write to
uv run ocr-agentic --max-price 3               # tighter cap per document
uv run ocr-agentic --watch                     # keep processing whatever lands
uv run ocr-agentic --force                     # redo documents already finished
uv run ocr-agentic --concurrency 8             # concurrent model calls
uv run ocr-agentic -v                          # log what the package is doing
```

`--concurrency` caps concurrent *model calls* — the semaphore travels inside
the shared LLM ([common/llm.py](src/ocr_batch/common/llm.py)), so it is the only throttle:
a document waiting hours on a human expert holds no slot in anything.

PNG, JPEG, WebP, GIF and PDF (first page) all work. The inbox is read as one
flat folder — a document's identity is its name plus its bytes, and the flat
folder is what keeps the name half unambiguous.

## Interrupt it whenever you like

Human work takes hours, so a run gets interrupted sooner or later. Nothing you
have paid for is lost, and nothing is paid for twice:

```
$ uv run ocr-agentic
velocity-print.png: created task f312b656…
^C
stopped
1 task(s) created and not collected: velocity-print.png (f312b656…)
They are still running and still billable. Re-run to resume them.

$ uv run ocr-agentic
velocity-print.png: resuming task f312b656…
velocity-print.png: transcribed by a human expert ($3.00)
```

The mechanism is one append-only file next to the CSV
(`documents/extracted.tasks.jsonl`, moveable with `--state`) holding a line per
task. Everything after `prepare_task` is idempotent against the `task_id`, so
knowing the id is all a restarted run needs.

A stored id is reused only when all three hold — see
[common/journal.py](src/ocr_batch/common/journal.py):

| guard | why |
| --- | --- |
| same **name and bytes** | an edited re-scan is a new document; so is a second copy of the same one, so each file gets its own task |
| same **endpoint** | a prestable `task_id` is a 404 against production |
| same **question** | the prompt and `FIELDS`, *not* the model's notes — those are worded differently every run, and hashing them would break resume on the first retry |

Documents already finished in the CSV are skipped entirely, so re-running a
completed batch costs nothing. `--force` overrides that.

Three honest limits:

- **One window stays open.** If the process dies between `prepare_task`
  returning and the journal line being written, the id is lost and a re-run
  creates a second task. It is microseconds wide, and only an idempotency key
  on `create_task` could close it — until then, that is what the orphan report
  is for.
- **One run per journal at a time.** The journal is a plain file with no lock;
  two processes pointed at the same folder can each create a task for the same
  document. Run one batch per journal.
- **Resume is told to the agent, not remembered by it.** A restarted run reads
  the id out of the journal and puts it in the prompt (*"you already created
  task_id=…, do NOT create another"*), so re-attachment is decided by a file on
  disk rather than by the model recalling its own history. That is deliberate: a
  LangGraph checkpointer would also work, but it would make a money guarantee
  depend on the model reading its notes correctly.

## The two flows

**Agentic** — [agentic/flow.py](src/ocr_batch/agentic/flow.py). Five tools and one prompt:

```python
tools = [report_tool(reported, min_confidence=...)]
tools += guarded_tendem_tools(tendem_tools(client=Tendem(...), ...), journal, ...)
agent = create_agent(llm, tools=tools, system_prompt=PROMPT)
state = await agent.ainvoke({"messages": [_ask(path, resume)]})
```

`report_fields` is how the agent finishes, and it is also the one piece of
deterministic control left in the loop: while an expert is reachable it refuses
an incomplete or low-confidence report and sends the agent to escalate. So "the
model decides" has a floor under it. That floor — and the wrapper that refuses
duplicate tasks and journals every id the instant it exists — lives in
[agentic/guards.py](src/ocr_batch/agentic/guards.py), enforced by the tools rather than asked
of the prompt.

And if the agent finishes without an accepted report at all — models
sometimes answer in prose instead of calling the tool — the flow does not
argue with it: the scripted flow's `escalate()` takes over, buys a human, and
resumes the agent's task if it had already created one. Either way the
document ends in an answer, not an apology.

**Scripted** — [scripted/flow.py](src/ocr_batch/scripted/flow.py). No agent, no tool
calling; the human is one function call in `scripted_process_single_file`:

```python
item = await extract(llm, path)                    # the model reads it
if item.is_confident(settings.min_confidence):
    return _from_model(item)                       # good enough — done
result = await escalate(tendem, llm, item, ...)    # a human reads it
return _from_human(item, result)
```

Inside `escalate` ([scripted/escalate.py](src/ocr_batch/scripted/escalate.py)) the hand-off is
one `prepare_task` and a loop over `advance_task` — the package as an async
API:

```python
task_id = await prepare_task(tendem, brief, files=[str(item.path)])

while True:
    event = await advance_task(tendem, task_id, max_price=..., timeout=...)
    if event.kind == "result":       # the expert's answer, verified
        return event.outcome
    if event.kind == "question":     # Tendem is scoping — the LLM replies
        reply = await answer_from_brief(llm, event.text)
    if event.kind == "over_budget":  # too expensive; nothing was charged
        return None
```

Notable properties:

- **The cap is the consent.** A quote at or under `TENDEM_MAX_PRICE` is
  approved automatically, so the batch never blocks on a payment decision. A
  quote above it comes back with the scope, and nothing is charged.
- **Waiting is free.** All of it is server-side long-polling in plain Python
  — no model in the loop, no tokens burned while a human works. Every
  escalation in a batch waits concurrently.
- **The brief is self-contained.** The expert sees only that text and the
  attached file, so the brief spells out the fields and the exact JSON to
  return. The document rides along via `files=`.
- **Tendem's questions are answered by the pipeline**, not by you — the LLM
  replies from the same brief. You are only involved if a quote breaks the
  cap.
- **A dead endpoint is a row, not a crash.** An unreachable service, or one
  document blowing up, still leaves every other row in the CSV with the reason
  in `notes`.

### On trusting the model

Measured on the sample set with `gpt-5.4-mini`: the four clean invoices come
back at confidence 0.99, correct. On the stamped, blurred one the agent
**invented** an invoice number and a date and reported 0.78–0.86 — while its own
`notes` said the fields were obscured. Telling it in the prompt to return null
did not stop it.

That is why `OCR_MIN_CONFIDENCE` defaults to `0.95` and why the gate is code, not
prompt: a model-only row that misses a field or falls under the threshold is
written as `failed` with the reason first in `notes`, values included but not
to be trusted. It is a tripwire
on a self-reported number, not a hallucination detector — the honest fix for that
is a second read compared against the first, which this example does not do.

## Layout

| file | |
| --- | --- |
| [agentic/flow.py](src/ocr_batch/agentic/flow.py) | **the agentic flow — the prompt and the loop** |
| [agentic/guards.py](src/ocr_batch/agentic/guards.py) | the rails the prompt cannot guarantee: no duplicates, no lost ids |
| [scripted/flow.py](src/ocr_batch/scripted/flow.py) | **the scripted flow — read, gate, escalate** |
| [scripted/extract.py](src/ocr_batch/scripted/extract.py) | the vision prompt |
| [scripted/escalate.py](src/ocr_batch/scripted/escalate.py) | the hand-off loop — also the agent's fallback |
| [cli.py](src/ocr_batch/cli.py) | the shared harness: batching, resume, `--watch`, the orphan report |
| [common/llm.py](src/ocr_batch/common/llm.py) | the one model both flows share, carrying the concurrency cap |
| [common/journal.py](src/ocr_batch/common/journal.py) | the task ids, so an interrupt costs nothing |
| [common/documents.py](src/ocr_batch/common/documents.py) | folder discovery, hashing, PDF rasterising |
| [common/report.py](src/ocr_batch/common/report.py) | the CSV, rewritten as each row settles |
| [common/samples.py](src/ocr_batch/common/samples.py) | the synthetic invoices |

Change the `FIELDS` tuple in [common/config.py](src/ocr_batch/common/config.py) and the
prompt, the expert's brief and the CSV columns all follow.

```bash
uv run pytest    # both halves of the batch, with the model and Tendem faked
```

MIT.
