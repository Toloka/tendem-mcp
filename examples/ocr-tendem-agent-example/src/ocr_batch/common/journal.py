"""The one thing worth remembering across runs: which task is doing what.

Human work costs money and takes hours, so losing a `task_id` to a Ctrl-C is
expensive — the task keeps running server-side and the next run would buy the
same transcription again. This is the smallest possible memory that prevents
that: an append-only JSONL file, one `created` line per task and one `finished`
line when it is collected.

It only exists because `create_task` has no idempotency key yet. Give it one
(or make `conversation_id` queryable) and this file can be deleted: the server
would be the authority instead.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("ocr_batch")

#: Outcomes that close a task for good. Anything else (a dropped connection, a
#: killed process) deliberately leaves the `created` line open, so the next run
#: resumes the task instead of writing off work already paid for.
TERMINAL = ("result", "over_budget")


def now() -> str:
    """Timestamp for a journal line."""
    return datetime.now(UTC).isoformat(timespec="seconds")


class Journal:
    """Append-only record of the tasks this pipeline has created."""

    def __init__(self, path: Path, entries: list[dict[str, Any]] | None = None) -> None:
        self.path = path
        self._entries = entries or []

    @classmethod
    def load(cls, path: Path) -> Journal:
        """Read the journal, tolerating a truncated or hand-edited last line."""
        entries: list[dict[str, Any]] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("skipping unreadable line in %s", path)
                    continue
                if isinstance(entry, dict):
                    entries.append(entry)
        return cls(path, entries)

    # ------------------------------------------------------------- reading

    def adopt(self, sha: str, *, url: str, scope_sha: str) -> str | None:
        """The id of a live task for this exact document, if we have one.

        All three guards must hold, so a resumed task can never answer a
        different question than the one being asked now: it is the same document
        (`sha` covers the filename as well as the bytes, so a copy is its own
        document), the endpoint is the one that issued the id, and the question
        has not been re-scoped.

        The newest matching task that has not been closed out wins. Entries for
        another endpoint or scope are skipped, not treated as the last word:
        flipping `TENDEM_MCP_URL` back and forth must never lose track of a
        task that is still live — and still billable — where it was created.
        """
        closed = {
            str(entry["task_id"])
            for entry in self._entries
            if entry.get("event") == "finished" and entry.get("task_id")
        }
        for entry in reversed(self._entries):
            if entry.get("event") != "created" or entry.get("sha") != sha:
                continue
            if entry.get("url") != url or entry.get("scope_sha") != scope_sha:
                continue
            task_id = entry.get("task_id")
            if task_id and str(task_id) not in closed:
                return str(task_id)
        return None

    def orphans(self) -> list[dict[str, Any]]:
        """Tasks created and never collected — still running, still billable."""
        live: dict[str, dict[str, Any]] = {}
        for entry in self._entries:
            task_id = entry.get("task_id")
            if not task_id:
                continue
            if entry.get("event") == "created":
                live[str(task_id)] = entry
            elif entry.get("event") == "finished":
                live.pop(str(task_id), None)
        return list(live.values())

    # ------------------------------------------------------------- writing

    def created(
        self,
        sha: str,
        file: str,
        task_id: str,
        *,
        url: str,
        scope_sha: str,
        conversation_id: str | None = None,
    ) -> None:
        """Record a new task. Called immediately after `prepare_task` returns."""
        self._append(
            {
                "event": "created",
                "sha": sha,
                "file": file,
                "task_id": task_id,
                "url": url,
                "scope_sha": scope_sha,
                "conversation_id": conversation_id,
            }
        )

    def finished(
        self, sha: str, task_id: str, outcome: str, price: str | None = None
    ) -> None:
        """Close a task out. Only `TERMINAL` outcomes are recorded."""
        if outcome not in TERMINAL:
            return
        self._append(
            {
                "event": "finished",
                "sha": sha,
                "task_id": task_id,
                "outcome": outcome,
                "price": price,
            }
        )

    def _append(self, entry: dict[str, Any]) -> None:
        entry["at"] = now()
        self._entries.append(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Open, write, close: a line that made it to disk survives a kill -9.
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")


__all__ = ["TERMINAL", "Journal"]
