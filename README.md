<!-- mcp-name: io.github.Toloka/tendem-mcp -->
# Tendem MCP

**Delegate tasks to vetted human experts — straight from your AI assistant.** 🧑‍🔬🤝🤖

[Tendem](https://tendem.ai) gives your AI direct access to vetted human experts
for the work an agent alone can't nail: judgment-heavy research, competitive
analysis, copywriting and editing, design review, data cleaning, and complex
multi-step tasks. You (or your agent) submit a task in plain English — Tendem
**scopes the work, quotes a transparent price, and delivers verified results**
back into the chat as files, reports, or structured outputs. Agent *plus* expert
raises the quality ceiling of what you can ship well beyond what either does
alone.

Ideal for founders, creators, operators, and consultants who need reliable
expert output without managing freelancers.

**Get started → [agent.tendem.ai](https://agent.tendem.ai).**

This repo is the **official distribution home** for the Tendem MCP plugin — the
part that lives in your AI client. Install it and your assistant arrives already
knowing how to drive the full Tendem lifecycle: create → scope → approve →
fetch, with clean polling and spend safety built in.

> The MCP server itself is **hosted** at `https://mcp.tendem.ai/mcp` — it is not
> in this repo. This repo contains the client-side plugins, skills, and rules
> that teach your AI client to use the hosted server well.

## Start using Tendem

### 1. The web interface

Sign in at **[agent.tendem.ai](https://agent.tendem.ai)** to create your
account, top up your balance, and watch tasks run end-to-end. The full output of
every task — markdown plus downloadable files — is always available there, no
matter which client you launched it from.

### 2. Install the plugin in your AI client

The hosted server URL is the same everywhere: `https://mcp.tendem.ai/mcp`
(streamable HTTP, OAuth on first use). Pick your client:

#### Claude Code

```
/plugin marketplace add Toloka/tendem-mcp
/plugin install tendem@tendem-mcp
```

#### OpenAI Codex / ChatGPT

```
codex plugin marketplace add Toloka/tendem-mcp
codex plugin add tendem@tendem-mcp
```

Complete the MCP server's OAuth login on first tool use.

#### GitHub Copilot CLI

```
copilot plugin marketplace add Toloka/tendem-mcp
copilot plugin install tendem@tendem-mcp
```

#### Gemini CLI

```
gemini extensions install https://github.com/Toloka/tendem-mcp
```

Then authenticate inside Gemini with `/mcp auth tendem`. The catalog entry is
[`gemini-extension.json`](./gemini-extension.json) at the repo root.

#### Kiro

Kiro Powers register through the IDE. Point Kiro at the
[`tendem-power/`](./tendem-power/) steering bundle in this repo, then connect the
`tendem` MCP server when prompted.

#### Manual (any MCP-compatible client)

Add to your client's MCP config and sign in when prompted:

```json
{
  "mcpServers": {
    "tendem": {
      "type": "http",
      "url": "https://mcp.tendem.ai/mcp"
    }
  }
}
```

## Build agentic pipelines (API-key auth)

For headless, CI, or programmatic use — where the interactive OAuth flow isn't
viable — authenticate with a Tendem **API key** instead: sign in at
[agent.tendem.ai/mcp](https://agent.tendem.ai/mcp) and create one under the
**Agent builders** tab.

**Python / LangChain / LangGraph:** skip the raw MCP wiring and use
[`langchain-tendem`](./integrations/langchain-tendem/) — four price-capped
tools that let an agent create a task, answer the service's questions, and
collect the verified result, with all polling in plain Python (no LLM, no
tokens).

For every other stack, a native `"type": "http"` remote server triggers the
interactive OAuth flow, so
to pass a static API key instead, bridge the connection through
[`mcp-remote`](https://www.npmjs.com/package/mcp-remote) — a local stdio process
that connects to the hosted URL and injects your `Authorization: ApiKey` header:

```json
{
  "mcpServers": {
    "tendem": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://mcp.tendem.ai/mcp",
        "--transport",
        "http-only",
        "--header",
        "Authorization: ApiKey <your-tendem-api-key>"
      ]
    }
  }
}
```

Keep the token out of source control (inject it from an environment variable or
secret store). Your custom agent loop drives the same eleven tools the plugin
uses — `create_task`, `get_task`, `get_contract`, `get_task_result`,
`list_tasks`, `approve_task`, `cancel_task`, `send_message`, `read_chat`,
`get_account`, `get_file_upload_url` — to implement the create → scope →
approve → poll → fetch lifecycle yourself. The server ships a
`tendem-quickstart` prompt with a full walkthrough.

> **Spend safety:** `approve_task` is what triggers a charge. In an automated
> pipeline, gate it behind your own approval logic and check `get_account`
> first — an insufficient balance returns a task-bound top-up URL, not an error
> to retry.

## What's in this repo

| Path | What it is |
|---|---|
| [`plugins/tendem/`](./plugins/tendem/) | The plugin for **Claude Code, Cursor, and GitHub Copilot CLI** — skill, slash commands, background watcher agent, notification hook, and the MCP connector |
| [`codex/tendem/`](./codex/tendem/) | The plugin for **OpenAI Codex / ChatGPT** — explicit `$`-skills and Codex-specific file-upload guidance |
| [`tendem-power/`](./tendem-power/) | The **Kiro** Power (steering + MCP config) |
| [`gemini-extension.json`](./gemini-extension.json) | The **Gemini CLI** extension manifest (root install) |
| [`.claude-plugin/marketplace.json`](./.claude-plugin/marketplace.json) | This repo's **Claude Code** marketplace |
| [`.agents/plugins/marketplace.json`](./.agents/plugins/marketplace.json) | This repo's **Codex** marketplace |
| [`server.json`](./server.json) | MCP Registry manifest (remote server) |
| [`llms.txt`](./llms.txt) | LLM discovery index |

### What the plugin teaches your assistant

Installing does more than wire up the connector — it ships steering so your AI
handles Tendem correctly out of the box:

- **`tendem-tasks` skill** — the end-to-end path (create → scope → approve →
  fetch), silent long-polling without busy-loops, scope negotiation, and
  live-verified file upload/download mechanics. Loads only when work actually
  involves Tendem.
- **`/tendem-task`, `/tendem-status`, `/tendem-result`** — explicit entry points
  to submit, check, and fetch a task (in Codex these are `$tendem-task` etc.).
- **`tendem-watcher` agent** (Claude / Copilot) — a background watcher that polls
  a running task at a slow pace and pings you the moment it needs you.
- **Notification hook** — a desktop notification when a task transitions to
  needing you (quote ready, top-up needed, input needed, result ready).

## For contributors

- [AGENTS.md](./AGENTS.md) — guide for AI agents working in this repo
- [CONTRIBUTING.md](./CONTRIBUTING.md) — how to contribute
- **Per-client manifests**: [Claude Code](./plugins/tendem/.claude-plugin/plugin.json),
  [OpenAI Codex](./codex/tendem/.codex-plugin/plugin.json),
  [Cursor](./plugins/tendem/.cursor-plugin/plugin.json),
  [GitHub Copilot CLI](./plugins/tendem/.github/plugin/plugin.json), and
  [Gemini CLI](./gemini-extension.json)

## Links

- **Product:** [tendem.ai](https://tendem.ai) · **App:** [agent.tendem.ai](https://agent.tendem.ai)
- **Overview:** [Connect your AI agent to human experts via MCP](https://toloka.ai/blog/connect-your-ai-agent-to-human-experts-via-mcp/)
- **Legal:** [Privacy Policy](https://tendem.ai/legal/privacy) · [Terms of Use](https://tendem.ai/legal/terms)
- **Support:** [Help center](https://tolokahelp.zendesk.com/hc/en-us)

---

*Tendem MCP is part of the [Model Context Protocol](https://modelcontextprotocol.io/) ecosystem.*
