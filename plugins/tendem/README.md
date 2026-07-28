# tendem

Delegate work to human experts without leaving Claude Code. For tasks an agent
alone can't nail — judgment-heavy research, review, labeling, content, design —
[Tendem](https://tendem.ai) finds a vetted expert who performs the task
precisely, QA-checks the output, and delivers it back into your project. Agent
plus expert raises the quality ceiling of what you can ship well beyond what
either does alone.

Claude drives the whole loop for you: it submits your ask in your own words,
answers most of the scoping chat itself, shows you the price — and only after
your explicit approval does the expert start. Nothing is ever purchased
without you seeing the quote first. Results come back as markdown plus files,
saved into your project.

## What's inside

Installing connects the Tendem MCP server (`https://mcp.tendem.ai/mcp`, OAuth
on first use) and teaches Claude the expected path: correct polling without
busy-loops, safe approval handling, scope negotiation, and live-verified file
upload/download mechanics.

| Component | Purpose |
|---|---|
| `.mcp.json` | Bundles the remote Tendem connector (connects on install) |
| `skills/tendem-tasks` | Auto-triggering skill: the end-to-end path, polling discipline, negotiation rules. `references/files.md` + `scripts/` cover file transfer. |
| `/tendem-task <goal>` | Submit a task — your words are passed to Tendem as-is; Tendem asks the clarifying questions |
| `/tendem-status [task_id]` | Check a task (also works in a fresh session — tasks outlive sessions) |
| `/tendem-result [task_id]` | Fetch a finished task's markdown + files into `./tendem/<task_id>/` |
| `agents/tendem-watcher` | Background watcher: polls a running task at a slow pace (default hourly) and pings you the moment it needs you — "tell me when it's done" |
| `hooks/tendem-post.sh` | OS notification when a task needs you: quote ready, top-up needed, input needed, result ready (macOS / Linux / Windows) |

The skill loads only when work actually involves Tendem — zero overhead
otherwise.

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
expert to research X"* — or use the slash commands. For long-running tasks:
*"tell me when it's done"* starts the background watcher; or just come back
later, in any session, and ask how the task is doing (or run `/tendem-status`).

Note: Tendem refuses data-scraping / automated data-extraction tasks by
policy.

## Install

Available through the Claude Code plugin marketplace:

```
/plugin install tendem
```
