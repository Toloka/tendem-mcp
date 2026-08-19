# Publishing `n8n-nodes-tendem`

**Nothing here has been published.** No release was uploaded to npm from this
working tree. Everything up to and including `npm pack --dry-run` was run and
passed; `npm publish` was deliberately **not** run — it needs credentials, and a
name + version pair on npm is permanent and cannot be reclaimed.

- Package name: **`n8n-nodes-tendem`**
- Version to release first: **`0.1.0`**
- License in `package.json` and `LICENSE`: MIT (relicensed in `7954d17`)
- Requires: Node.js >= 20.19

Name availability as checked from this machine on 2026-08-06:

```
npm view n8n-nodes-tendem version   ->  npm error 404   (unclaimed)
```

Re-check immediately before publishing — anyone can claim a name at any time.

## 0. What was verified here

Run from `integrations/n8n-nodes-tendem/`. These exact commands were executed and
succeeded.

The repo's default Node is 20.18.0, which **cannot run `@n8n/node-cli`** — it
`require()`s an ES module, which needs Node >= 20.19. Build and test work on
20.18; lint does not. `engines.node` is set to `>=20.19` for that reason. The
commands below were run on Node 26.2.0 unless noted.

```bash
npm install
npm run build     # tsc + copy-assets: "copied 3 asset(s) into dist/"
npm test          # 71 passed, 0 failed
npm run lint      # n8n-node lint v0.42.1 — 1 error, see below
npm pack --dry-run
```

The suite was run on **both Node 20.18.0 and Node 26.2.0** — 71 passed, 0 failed
on each.

`npm pack --dry-run` produces 24 files, 28.2 kB packed / 100.9 kB unpacked:
`dist/` (compiled JS, `.d.ts`, source maps, both icon SVGs, the codex
`Tendem.node.json`), plus `README.md`, `LICENSE` and `package.json`. Both paths
named in the `n8n` block resolve inside the tarball:

- `dist/credentials/TendemApi.credentials.js` ✓
- `dist/nodes/Tendem/Tendem.node.js` ✓

The compiled artifacts were loaded out of `dist/` and inspected: node type
`tendem`, credential type `tendemApi`, both icon variants resolving to real files
from the directory each class is compiled into, and all eight task operations
present.

`npx n8n-node build` (the CLI's own build) was run and its output diffed against
`npm run build`. They are identical apart from indentation inside the copied
`Tendem.node.json`. The custom build is kept because it is dependency-free and
runs on Node 20.18, where the CLI cannot.

### The three load-bearing behaviours were exercised directly

- **Endpoint.** `TENDEM_DEFAULT_ENDPOINT` is
  `https://mcp.tendem.ai/mcp?utm_hash=83dad40a52`. A node-level test asserts every
  HTTP request the node makes goes to that exact URL; two more assert that a
  credential override is honoured and that a blank override falls back to the
  default.
- **Spend guardrail.** `approve_task` is reachable only from Task → Approve. All
  eleven other operations were driven through the real `execute()` against a mock
  MCP server and none put `approve_task` on the wire — including Get and Wait
  against a task Tendem had already marked `ready_for_approval: true` with a
  price. Approve itself refuses with Confirm Spend off or with an empty price,
  before any HTTP request. A two-item run confirmed confirming item 0 does not
  approve item 1.
- **Polling.** Wait for Change issues `get_task(task_id,
  wait_for_change_seconds=30)` and stops on the first settled snapshot; the round
  count is hard-capped (default 20, max 240) and a budget overrun emits
  `tendemWait.timedOut: true` rather than looping.

Those tests were **mutation-checked**: neutering the Confirm Spend condition in
the compiled output made exactly the three approval tests fail, so they are not
vacuous.

## 1. The license question is resolved

Earlier revisions of this document tracked one open lint error: the package
originally declared Apache-2.0, which n8n's [verification
guidelines](https://docs.n8n.io/connect/create-nodes/build-your-node/reference/verification-guidelines)
reject (*"Make sure your package license is MIT"*). Commit `7954d17` relicensed
the integration packages to MIT — both `package.json` and `LICENSE` in this
directory — so the blocker no longer exists.

Re-verified on 2026-08-19 (Node 26.2.0, after an `npm install` to sync the
lockfile — see below):

```
npm run lint   ->  0 errors, 2 warnings
npm test       ->  85 passed, 0 failed
```

The two warnings are both `icon-prefer-themed-variants`, on the node and the
credential. They are deliberate: the brand-consolidation commit (`f01f2a7`)
dropped the dark icon variant because the mark is a dark container with a white
glyph that reads on either theme. Warnings do not fail lint.

One trap fixed along the way: `package-lock.json` had drifted from
`package.json` (a missing `ignore@7.0.6` entry), so `npm ci` — which the publish
workflow uses — failed until `npm install` re-synced it. Separately, if lint
dies with `ERR_REQUIRE_ESM` (via `change-case`), that is the Node < 20.19 trap
from §0 — check `node --version` actually resolves to >= 20.19 before debugging
anything else; on this machine fnm prints a 26.x banner even when the `node` on
PATH is still 20.18.

## 2. Publish to npm

**Do not run these casually.** Publishing is irreversible.

Check the working tree is clean and the version is right, then:

```bash
npm run build
npm test
npm pack --dry-run          # eyeball the file list one more time
npm publish --access public
```

`prepublishOnly` re-runs build and test, so a broken tree cannot be published by
accident.

To smoke-test the exact tarball before committing to a name, publish a prerelease
version instead — `0.1.0-rc.1` — install it into a scratch n8n, then publish
`0.1.0` proper. A prerelease burns a version, not the name.

### Verification requires provenance

If the goal is a **verified** community node, a hand-run `npm publish` is not
enough. Per n8n's submission docs: *"From May 1st 2026, nodes submitted for
verification must be published using GitHub Actions with a provenance
statement."* Publishing from a laptop disqualifies the release.

That workflow now exists:
[`.github/workflows/publish-n8n-tendem.yml`](../../.github/workflows/publish-n8n-tendem.yml).
It triggers on a `n8n-nodes-tendem-v*` tag, runs `npm ci` + lint + test, refuses
to publish when the tag and `package.json` version disagree, and publishes with
`--provenance --access public`. It needs the `NPM_TOKEN` repository secret set to
an npm automation token that can publish this package.

So the release procedure is: bump `version` in `package.json` (the
`MCP_CLIENT_VERSION` constant in `transport.ts` must match — a test fails
otherwise), merge to main, then:

```bash
git tag n8n-nodes-tendem-v0.1.0 && git push origin n8n-nodes-tendem-v0.1.0
```

`@n8n/node-cli` also ships `n8n-node release`, which wraps the same flow with
release-it. Either is fine; provenance is what matters.

## 3. Install checks after publishing

```bash
npm view n8n-nodes-tendem
npx -y @n8n/scan-community-package n8n-nodes-tendem
```

Then install it into a throwaway self-hosted n8n and confirm, by hand:

- The **Tendem** node appears in the node panel with its icon, in both light and
  dark themes.
- **Credentials → New → Tendem API** shows the API Key and MCP Endpoint fields,
  and the credential **Test** button succeeds against a real token.
- Task → Create returns a `task_id` against the live server.
- Task → Approve with Confirm Spend **off** fails with the refusal message —
  verify this on a real instance, since it's the guardrail that protects money.

## 4. Submit for verification (optional)

Only after the license question is settled and a provenance-signed release is on
npm. Sign in to the n8n Creator Portal and submit the package — the current link
and steps are in n8n's [submit community
nodes](https://docs.n8n.io/connect/create-nodes/deploy-your-node/submit-community-nodes)
guide. n8n reviews against its technical and UX standards and reserves the right
to reject nodes that compete with its paid features.

One known deviation to expect a question about: n8n *strongly suggests* starting
from `n8n-node new` scaffolding. This package is hand-written but tracks the
scaffold's shape — same `n8n` block, same `files`, same lint config
(`@n8n/node-cli/eslint`, unmodified, so strict mode's config check passes). It
adds an `engines` field, an explicit `test` script, and a dependency-free build
script; the scaffold has none of those.

## Checklist against n8n's package requirements

| Requirement | Status |
|---|---|
| Name starts with `n8n-nodes-` or `@scope/n8n-nodes-` | ✓ `n8n-nodes-tendem` |
| `n8n-community-node-package` in `keywords` | ✓ |
| `n8n` block pointing at compiled `dist/` files | ✓ both paths resolve in the tarball |
| `n8nNodesApiVersion: 1` | ✓ |
| No runtime dependencies | ✓ `dependencies` is absent; only `devDependencies` and a `peerDependencies` on `n8n-workflow` |
| Author with a non-empty email | ✓ `Toloka AI BV <support@tendem.ai>` |
| Node and credential both carry icons | ✓ one theme-proof SVG, shared (dark variant deliberately dropped in `f01f2a7`) |
| Documentation (README with auth + usage) | ✓ [README.md](./README.md) |
| `n8n-node lint` clean in strict/cloud mode | ✓ 0 errors (2 deliberate icon warnings) |
| MIT license | ✓ `package.json` + `LICENSE`, relicensed in `7954d17` |
| Published via GitHub Actions with provenance | ✗ workflow ready (`publish-n8n-tendem.yml`), not yet published |
