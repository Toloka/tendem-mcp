---
name: "tendem"
displayName: "Tendem"
description: "Delegate tasks to vetted human experts from your AI assistant — submit work in plain English, get a transparent quote, approve, and receive verified results back in your project."
keywords: ["tendem", "human-in-the-loop", "experts", "research", "delegation", "mcp", "productivity", "review", "content", "data"]
author: "Tendem"
---

# Tendem Power

## Overview

[Tendem](https://tendem.ai) connects your AI assistant to vetted human experts
for the work an agent alone can't nail — judgment-heavy research, review,
labeling, content, design, and data work. Submit a task in natural language;
Tendem scopes it, quotes a transparent price, and — only after your explicit
approval — a human expert performs it, QA-checks the output, and delivers it
back as markdown plus files.

**Key capabilities:**

- **Human-verified deliverables**: Research and competitive analysis, copywriting
  and editing, design review, data cleaning, and complex multi-step work.
- **Transparent, approval-gated pricing**: Nothing is ever purchased without you
  seeing the quote and scope first.
- **Agent-driven lifecycle**: Your assistant creates the task, answers scoping
  questions from context, polls without busy-looping, and saves the results into
  your project.
- **OAuth authentication**: No API keys required for interactive use — connect
  once and sign in at [agent.tendem.ai](https://agent.tendem.ai).

## When to Use This Power

Activate this Power when the user:

- Wants to delegate a scoped, expert-verified deliverable ("have a human review
  this", "get an expert to research X").
- Asks "what can I delegate to Tendem" or "submit a task to Tendem".
- Needs reliable expert output without managing freelancers.

Do **not** invoke it for quick general-knowledge questions, or for automated
data-scraping / data-extraction tasks (refused by Tendem policy).

## Onboarding

### Step 1: Connect the Tendem MCP server

- **Connection:** HTTPS endpoint at `https://mcp.tendem.ai/mcp`
- **Authorization:** OAuth via [agent.tendem.ai](https://agent.tendem.ai)

### Step 2: Get started

Just ask in natural language — *"have Tendem research the top 5 CRM tools and
summarize pricing"* — and the assistant drives the create → scope → approve →
fetch lifecycle for you.

## MCP Configuration

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

For the tool surface exposed by the server, see the
[Tendem MCP overview](https://toloka.ai/blog/connect-your-ai-agent-to-human-experts-via-mcp/).

## License and support

This Power integrates with the hosted [Tendem MCP server](https://tendem.ai).
The plugin distribution is MIT licensed.

- [Privacy Policy](https://tendem.ai/legal/privacy)
- [Terms of Use](https://tendem.ai/legal/terms)
- [Help center](https://tolokahelp.zendesk.com/hc/en-us)
