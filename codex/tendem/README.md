# tendem (ChatGPT / Codex plugin)

Delegate work to human experts without leaving Codex or ChatGPT. For tasks an
agent alone can't nail — judgment-heavy research, review, labeling, content,
design — [Tendem](https://tendem.ai) finds a vetted expert who performs the
task precisely, QA-checks the output, and delivers it back into your project.
Agent plus expert raises the quality ceiling of what you can ship well beyond
what either does alone.

The agent drives the whole loop for you: it submits your ask in your own
words, answers most of the scoping chat itself, shows you the price — and only
after your explicit approval does the expert start. Nothing is ever purchased
without you seeing the quote first. Results come back as markdown plus files,
saved into your project.

## What's inside

Installing connects the Tendem MCP server (`https://mcp.tendem.ai/mcp`, OAuth
on first use) and teaches the agent the expected path: correct polling without
busy-loops, safe approval handling, scope negotiation, and live-verified file
upload/download mechanics.

| Component | Purpose |
|---|---|
| `.mcp.json` | Bundles the remote Tendem connector (streamable HTTP) |
| `skills/tendem-tasks` | Auto-triggering skill: the end-to-end path, polling discipline, negotiation rules. `references/files.md` + `scripts/` cover file transfer. |
| `$tendem-task <goal>` | Explicit skill: submit a task — your words are passed to Tendem as-is; Tendem asks the clarifying questions |
| `$tendem-status [task_id]` | Explicit skill: check a task (also works in a fresh session — tasks outlive sessions) |
| `$tendem-result [task_id]` | Explicit skill: fetch a finished task's markdown + files into `./tendem/<task_id>/` |
| `hooks/tendem-post.sh` | OS notification when a task needs you: quote ready, top-up needed, input needed, result ready (macOS / Linux / Windows) |

The `tendem-tasks` skill loads only when work actually involves Tendem — zero
overhead otherwise. The three `$`-skills are explicit-only
(`allow_implicit_invocation: false`) — they never trigger unless you invoke
them.

## ⚠️ File uploads: run them yourself

OpenAI's security policies prevent the agent's sandboxed environment from
uploading files to external storage, so the agent **cannot send input files
to a Tendem task the usual way**. To be clear: this is an **OpenAI platform
restriction, not a Tendem limitation** — Tendem's upload mechanism works
normally; it just has to run outside OpenAI's sandbox. When a task needs input
files, the agent will prepare ready-to-run `curl` commands (upload URL
minted, host swap and filenames already applied) and hand them to you —
**run them in a terminal on your host machine**, then tell the agent so it
can confirm the uploads to Tendem. Result-file *downloads* are unaffected.

## Requirements

For full functionality, have these on your machine:

- **`jq`** — required by the notification hook (without it, notifications are
  silently skipped; nothing breaks).
- **`curl`** — used by the file upload/download helpers.

## Notifications

Tendem tasks have long quiet stretches, so the plugin ships a `PostToolUse`
hook that fires a desktop notification the moment a task transitions to
needing you. It is deduped per task (long-polling won't spam), never blocks a
tool call, always exits 0, and degrades to silence if `jq` or the platform
notifier (`osascript` / `notify-send` / `powershell.exe`) is missing. Opt out
with `TENDEM_NO_NOTIFY=1`.

## Usage

Just ask in natural language — *"have a human review this contract"*, *"get an
expert to research X"* — or invoke the skills explicitly with `$tendem-task`,
`$tendem-status`, `$tendem-result`. For long-running tasks, come back later,
in any session, and ask how the task is doing — the notification hook also
pings you when it needs you.

Note: Tendem refuses data-scraping / automated data-extraction tasks by
policy.

## Install

From a marketplace that carries this plugin:

```
codex plugin marketplace add <marketplace-repo>
codex plugin add tendem@<marketplace-name>
```

On first Tendem tool use, complete the MCP server's OAuth login.
