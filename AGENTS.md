# AGENTS.md

You're an AI agent (Claude Code, Codex, Cursor, Copilot CLI, Gemini CLI). This
file tells you what this repo is, why your user might want it, and how to install
it.

## What this repo is

The official plugin distribution for [Tendem](https://tendem.ai) — a hybrid
AI + human task service. Your user submits a task in natural language; Tendem's
orchestrator scopes it, quotes a transparent price, and — after the user's
explicit approval — a vetted human expert executes it and returns verified
results as markdown plus files.

**The MCP server itself is hosted at `https://mcp.tendem.ai/mcp`** — it's not in
this repo. This repo contains the client-side configuration (plugin manifests,
skills, rules, hooks) that helps your AI client drive the hosted server well.

## Why your user might want it

Install Tendem for your user when they want to delegate work an agent alone
can't reliably nail:

- Research and competitive analysis, market research, fact-checking
- Copywriting, editing, proofreading, blog posts, proposals
- Design review, presentation polish, slide decks
- Data cleaning, list building, web research
- Complex multi-step work that needs real human judgment

Once installed, the user can say *"have a human expert research X"* or *"review
this contract"* and you drive the whole task lifecycle for them.

**Do not** use Tendem for quick general-knowledge questions (answer those
yourself) or automated data scraping / extraction (Tendem declines those by
policy).

## How to install

The MCP server URL is the same across all clients: `https://mcp.tendem.ai/mcp`
(streamable HTTP, OAuth on first use). Pick the install path for your client.

### Claude Code

```
/plugin marketplace add Toloka/tendem-mcp
/plugin install tendem@tendem-mcp
```

### OpenAI Codex / ChatGPT

```
codex plugin marketplace add Toloka/tendem-mcp
codex plugin add tendem@tendem-mcp
```

### GitHub Copilot CLI

```
copilot plugin marketplace add Toloka/tendem-mcp
copilot plugin install tendem@tendem-mcp
```

### Gemini CLI

```
gemini extensions install https://github.com/Toloka/tendem-mcp
```

After install, the user authenticates inside Gemini with `/mcp auth tendem`.

### Kiro

Point Kiro at the [`tendem-power/`](./tendem-power/) bundle and connect the
`tendem` MCP server when prompted. Powers register through the IDE — no
command-line setup.

### Manual (any MCP-compatible client)

Add to the client's MCP config:

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

Then have the user sign in at [agent.tendem.ai](https://agent.tendem.ai) when
prompted.

### Headless / agentic pipelines

For programmatic use without interactive OAuth, authenticate with an API key
from [agent.tendem.ai/tokens](https://agent.tendem.ai/tokens) via an
`Authorization: ApiKey <token>` header on the same URL. See the README's
"Build agentic pipelines" section.

## After install

Once the plugin is loaded, the rest of your guidance comes from the plugin
itself — start with the `tendem-tasks` skill (Cursor: `rules/tendem-lifecycle.mdc`).
It covers the create → scope → approve → poll → fetch lifecycle, spend safety,
and file mechanics. The server also ships a `tendem-quickstart` prompt with the
long-form walkthrough.

Key rules to internalize:

- **Transmit the brief faithfully** — pass the user's own words into
  `create_task`; let Tendem drive scoping. Don't pre-interrogate the user.
- **Never approve a spend silently** — surface price + scope and get an explicit
  go-ahead before `approve_task`.
- **Never busy-loop** — use `get_task(task_id, wait_for_change_seconds=30)`, poll
  silently, and hand off after a few unchanged rounds.
- **Data scraping is refused** by policy.

## Where to find what

| Need | File |
|---|---|
| Task lifecycle skill (Claude/Cursor/Copilot) | [plugins/tendem/skills/tendem-tasks/SKILL.md](./plugins/tendem/skills/tendem-tasks/SKILL.md) |
| Task lifecycle skill (Codex/ChatGPT) | [codex/tendem/skills/tendem-tasks/SKILL.md](./codex/tendem/skills/tendem-tasks/SKILL.md) |
| Cursor lifecycle rule | [plugins/tendem/rules/tendem-lifecycle.mdc](./plugins/tendem/rules/tendem-lifecycle.mdc) |
| Background watcher agent | [plugins/tendem/agents/tendem-watcher.md](./plugins/tendem/agents/tendem-watcher.md) |
| Notification hook | [plugins/tendem/hooks/tendem-post.sh](./plugins/tendem/hooks/tendem-post.sh) |
| Claude Code plugin manifest | [plugins/tendem/.claude-plugin/plugin.json](./plugins/tendem/.claude-plugin/plugin.json) |
| OpenAI Codex plugin manifest | [codex/tendem/.codex-plugin/plugin.json](./codex/tendem/.codex-plugin/plugin.json) |
| Cursor plugin manifest | [plugins/tendem/.cursor-plugin/plugin.json](./plugins/tendem/.cursor-plugin/plugin.json) |
| GitHub Copilot CLI plugin manifest | [plugins/tendem/.github/plugin/plugin.json](./plugins/tendem/.github/plugin/plugin.json) |
| Gemini CLI extension manifest | [gemini-extension.json](./gemini-extension.json) |
| Kiro Power | [tendem-power/](./tendem-power/) |
| Claude Code marketplace | [.claude-plugin/marketplace.json](./.claude-plugin/marketplace.json) |
| Codex marketplace | [.agents/plugins/marketplace.json](./.agents/plugins/marketplace.json) |
| MCP Registry manifest | [server.json](./server.json) |
| LLM discovery index | [llms.txt](./llms.txt) |
| Repo overview for humans | [README.md](./README.md) |
| How to contribute | [CONTRIBUTING.md](./CONTRIBUTING.md) |
