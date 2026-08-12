# Publishing `langchain-tendem`

**Nothing here has been published.** No release was uploaded to PyPI or TestPyPI
from this working tree. Everything up to and including `twine check` was run and
passed; the two `twine upload` commands below were deliberately **not** run —
they need credentials, and a name + version pair on PyPI is permanent and
cannot be reclaimed.

- Distribution name: **`langchain-tendem`**
- Import name: `langchain_tendem`
- Version to release first: **`0.1.0`**
- License: MIT
- Requires: Python >= 3.10

Name availability as checked from this machine (both returned HTTP 404, i.e.
unclaimed):

```
https://pypi.org/pypi/langchain-tendem/json        -> 404
https://test.pypi.org/pypi/langchain-tendem/json   -> 404
```

Re-check immediately before uploading — someone else can claim a name at any
time.

## 0. What was verified here

Run from `integrations/langchain-tendem/`. These exact commands were executed and
succeeded:

The dev environment here was created with `uv`, so `.venv` has no `pip` in it —
use `uv pip` against it, or build a plain `python -m venv` if you prefer pip.

```bash
uv venv --python 3.12 .venv
VIRTUAL_ENV=.venv uv pip install -e '.[test]' build twine
.venv/bin/python -m pytest              # 59 passed
rm -rf dist build
.venv/bin/python -m build               # -> dist/*.whl and dist/*.tar.gz
.venv/bin/python -m twine check dist/*  # both PASSED
```

The suite was run on three interpreters — **59 passed** on CPython 3.10.20 (the
declared floor), 3.12.11, and 3.13.13. The rebuilt wheel was then installed into
a clean environment and imported: `__version__ == "0.1.0"`, `TENDEM_MCP_URL ==
"https://mcp.tendem.ai/mcp?utm_hash=9cfb868c94"`, all 23 `__all__` names
resolvable, `py.typed` present in the wheel.

Beyond the suite, the three load-bearing behaviours were exercised directly
against a scripted in-process MCP transport:

- **Endpoint.** Default URL carries `?utm_hash=9cfb868c94`; `Tendem(url=...)`
  overrides it.
- **Spend guardrail.** Nine approval vectors were attempted — no `confirmed`,
  `confirmed="True"`, `confirmed=1`, `confirmed=[True]`, and a model-driven
  `approve_task` tool call with fabricated consent arguments
  (`confirmed=True, user_approved=True, human_confirmed="yes"`). Every one was
  refused *before* the call reached the transport. Only a grant recorded by
  application code let a call through, and replaying it was refused.
- **Polling.** `poll()` issued exactly 6 rounds, each
  `get_task(task_id, wait_for_change_seconds=30)`, paced ≥1s apart, then raised
  `PollTimeoutError`.

The README's end-to-end worked example was extracted from the Markdown and
executed against that same fake transport; it runs to completion unmodified.

## 1. Clean build

```bash
cd integrations/langchain-tendem
rm -rf dist build
python -m pip install --upgrade build twine
python -m build
```

Expected artifacts:

```
dist/langchain_tendem-0.1.0-py3-none-any.whl
dist/langchain_tendem-0.1.0.tar.gz
```

## 2. Validate metadata before any upload

```bash
python -m twine check dist/*
```

Both files must report `PASSED`. Sanity-check the wheel is importable from a
throwaway environment:

```bash
python -m venv /tmp/lt-check
/tmp/lt-check/bin/python -m pip install dist/langchain_tendem-0.1.0-py3-none-any.whl
/tmp/lt-check/bin/python -c "import langchain_tendem; print(langchain_tendem.__version__)"
```

## 3. TestPyPI dry run (do this first)

Get a TestPyPI API token at <https://test.pypi.org/manage/account/token/>. Tokens
are per-index — a PyPI token will not work on TestPyPI.

```bash
python -m twine upload \
  --repository-url https://test.pypi.org/legacy/ \
  --username __token__ \
  --password "$TEST_PYPI_TOKEN" \
  dist/*
```

Then install from TestPyPI, pulling real dependencies from PyPI (TestPyPI does
not mirror them):

```bash
python -m venv /tmp/lt-testpypi
/tmp/lt-testpypi/bin/python -m pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  langchain-tendem==0.1.0
/tmp/lt-testpypi/bin/python -c "import langchain_tendem; print(langchain_tendem.TENDEM_MCP_URL)"
```

Expected output:

```
https://mcp.tendem.ai/mcp?utm_hash=9cfb868c94
```

TestPyPI also burns a version number permanently. If the dry run reveals a
problem, bump to `0.1.0.post1` (or `0.1.1`) for the retry rather than trying to
overwrite.

## 4. Real upload (requires credentials — not run here)

```bash
python -m twine upload \
  --username __token__ \
  --password "$PYPI_TOKEN" \
  dist/*
```

Or, with a project-scoped token in `~/.pypirc`, simply `python -m twine upload dist/*`.

Recommended instead: **PyPI Trusted Publishing** from GitHub Actions, so no
long-lived token exists. Configure the publisher at
<https://pypi.org/manage/account/publishing/> against `Toloka/tendem-mcp`, then a
workflow with `permissions: id-token: write` and `pypa/gh-action-pypi-publish`
uploads without secrets. This requires the project to exist on PyPI or to use
"pending publisher" setup for the first release.

## 5. Post-release verification

```bash
python -m venv /tmp/lt-release
/tmp/lt-release/bin/python -m pip install langchain-tendem==0.1.0
/tmp/lt-release/bin/python -c "import langchain_tendem; print(langchain_tendem.__version__)"
```

Then tag the repo (the orchestrator handles git; do not run git from an agent
session):

```
git tag langchain-tendem-v0.1.0 && git push origin langchain-tendem-v0.1.0
```

## 6. Getting listed in LangChain's integrations docs

A docs listing requires the package to be **published** first — the provider page
points at a real, installable distribution. Order of operations:

1. Publish `langchain-tendem` to PyPI (steps 3–4).
2. Read the contribution guide: <https://docs.langchain.com/oss/python/contributing>
   (verified reachable, HTTP 200).
3. Add the provider page under the integrations index:
   <https://docs.langchain.com/oss/python/integrations/providers>
   (verified reachable, HTTP 200). LangChain keeps these under a
   `docs`-style repo and expects a PR adding a provider page plus an index entry;
   confirm the current file layout and required front-matter from the
   contribution guide at the time you open the PR.
4. Expect the reviewers to ask for: an installable PyPI release, a public source
   repo, a runnable quickstart, and tests. All four exist here.

### Uncertainty worth flagging

LangChain's documentation is mid-migration. From this machine:

- `https://docs.langchain.com/oss/python/contributing` — 200
- `https://docs.langchain.com/oss/python/integrations/providers` — 200
- `python.langchain.com` redirects (308) to `docs.langchain.com`
- `reference.langchain.com/python/` — 200 (it was intermittently 503 earlier)

Because prose docs were unreliable, **every API assumption in this package was
pinned against the installed packages, not documentation**:

- `langchain-mcp-adapters==0.3.2` — `StreamableHttpConnection`, `create_session`,
  `load_mcp_tools`, and the `ToolCallInterceptor` protocol (`MCPToolCallRequest`
  with `.name` / `.args` / `.headers`, handler-callback signature) were read from
  the installed source. The interceptor API is the load-bearing dependency for
  the spend guardrail and is **not** covered by a stability guarantee at `0.3.x`;
  pin is `>=0.3.2,<0.4`. If `0.4` changes the interceptor protocol, the guard in
  `approval.py` is the code to revisit.
- `langchain-core==1.5.3` — the guarded tools are the adapter's own
  `StructuredTool` with `response_format="content_and_artifact"`; blocked calls
  surface as a `ToolMessage` with `status="error"`, which is asserted in
  `tests/test_approval_guardrail.py` rather than assumed.
- Deliberately **not** subclassing `langchain_core.tools.BaseToolkit`: its
  `get_tools` is synchronous while MCP tool loading is async. `Tendem.get_tools`
  is async instead. If LangChain later ships an async toolkit base, adding it is
  a compatible change.
- Tendem payload field names (`next_action`, `poll_after_seconds`, `guidance`,
  `topup_url`, `task_description`, `price`) come from the server's own MCP
  instructions and the `tendem-tasks` skill in this repo, not from live traffic —
  no live call was made. The model classes keep the untouched payload on `.raw`
  and tolerate unknown `next_action` / `status` values, so a field rename
  degrades rather than crashes. **A single live smoke test against a real
  account is the one thing this package has not had.**

## 7. Release checklist

- [ ] `dist/` rebuilt clean; `twine check` PASSED
- [ ] `pytest` green on the lowest supported Python (3.10)
- [ ] name still unclaimed on PyPI
- [ ] version bumped in **both** `pyproject.toml` and
      `src/langchain_tendem/__init__.py` (`__version__`)
- [ ] `LANGCHAIN_UTM_HASH` is still `9cfb868c94`
- [ ] TestPyPI dry run installed and imported cleanly
- [ ] one live smoke test against a real Tendem account
- [ ] real upload
- [ ] git tag pushed
- [ ] docs PR opened per step 6
