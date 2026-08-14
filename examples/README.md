# Examples

Runnable projects that use Tendem as part of a real pipeline, rather than as a
tool call in a chat. Each one is a self-contained repo-within-a-repo: clone the
folder, add your keys, run it.

| example | what it shows |
| --- | --- |
| [ocr-tendem-agent-example](./ocr-tendem-agent-example) | Batch OCR where the human expert is the fallback: a folder of scans in, a CSV out, and whatever the vision model cannot read is handed to a person via [`langchain-tendem`](../integrations/langchain-tendem). The same job is built twice — once as an agent with five tools, once as plain deterministic code — so you can see what each style costs and guarantees. |

More will land here over time, covering other clients, other languages and
other shapes of hand-off. If you build something with Tendem that would help
the next person, a PR adding it is welcome — see
[CONTRIBUTING.md](../CONTRIBUTING.md).
