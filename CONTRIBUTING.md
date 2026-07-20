# Contributing to Tendem MCP

Thanks for considering a contribution. This repo distributes the client-side
plugin manifests, skills, rules, and hooks for [Tendem](https://tendem.ai) — the
MCP server itself is hosted and not part of this repo.

**In scope:** fixes and improvements to plugin manifests, skills, rules, hooks,
brand assets, marketplaces, and docs in this repo.

**Out of scope:** server behavior, the expert network, pricing, billing, or
auth — reach [Tendem support](https://tolokahelp.zendesk.com/hc/en-us) for those.

Open a [GitHub Issue](https://github.com/Toloka/tendem-mcp/issues) for bugs and
proposals, or submit a PR off `main`. AI agents contributing here should start
with [AGENTS.md](./AGENTS.md).

## Layout

- `plugins/tendem/` — the plugin for Claude Code, Cursor, and GitHub Copilot CLI
  (shared skill tree + per-client manifests).
- `codex/tendem/` — the plugin for OpenAI Codex / ChatGPT (its skills differ:
  explicit `$`-skills and Codex-specific file-upload guidance).
- `tendem-power/` — the Kiro Power.
- `gemini-extension.json` — the Gemini CLI extension.
- `.claude-plugin/marketplace.json` / `.agents/plugins/marketplace.json` — the
  two marketplaces this repo hosts.

When you change plugin behavior, keep the two skill trees (Claude vs. Codex) in
sync except where the platform genuinely differs, and validate every JSON
manifest before opening a PR.

## Notes

- Don't put secrets in plugin files. Treat the repo as public.
- All manifests point at the hosted server `https://mcp.tendem.ai/mcp`.
