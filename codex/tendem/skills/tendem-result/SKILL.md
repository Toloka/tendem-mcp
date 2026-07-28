---
name: tendem-result
description: Explicitly fetch and present a completed Tendem task's result (markdown + files). Invoke as $tendem-result [task_id] — omit the task_id to pick from recent tasks.
---

Fetch the result of a Tendem task.

- If a `task_id` accompanies this invocation, use it. Otherwise call
  `list_tasks` and pick the relevant completed/closed task (or ask the user
  which one).
- Call `get_task_result(task_id)`.
  - If `content` is `null`, the result isn't ready — fall back to
    `get_task(task_id, wait_for_change_seconds=30)` and only retry
    `get_task_result` once `next_action=fetch_result`.
- Present the `content` markdown to the user.
- Save every entry in `files[]` locally with the `tendem-tasks` skill's
  `scripts/tendem-download.sh`, destination `./tendem/<task_id>/` under the
  current working directory (unless the user names another location), and
  point the user at the saved paths.
- If a download fails because the pre-signed URL expired (~24h), re-run
  `get_task_result` to mint fresh URLs.
