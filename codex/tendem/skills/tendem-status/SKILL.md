---
name: tendem-status
description: Explicitly check on a Tendem task and advance it (poll cleanly, don't busy-loop). Invoke as $tendem-status [task_id] — omit the task_id to pick from recent tasks.
---

Check the status of a Tendem task and take the next correct step.

- If a `task_id` accompanies this invocation, use it. Otherwise call
  `list_tasks` and either use the obvious in-flight task or ask the user which
  one.
- Call `get_task(task_id, wait_for_change_seconds=30)` and act on the returned
  `next_action` (per the `tendem-tasks` skill):
  - `awaiting_tendem_work` → it's still working. Report where it stands and do
    NOT sit re-polling. The task lives on Tendem's side, nothing is lost
    between sessions — the user can simply ask again later ("how's the Tendem
    task doing?" or `$tendem-status`), and the plugin's notification hook
    pings them the moment the task needs them.
  - `await_input` → read the new chat and answer via `send_message` (or escalate).
  - `await_user_approval` → call `get_contract(task_id)` for the full scope +
    price, surface both; approve only with the user's go-ahead.
  - `await_user_topup` → give the user the `topup_url`.
  - `fetch_result` → follow the `tendem-result` skill (or call `get_task_result`).
  - `done` → nothing left; summarize the outcome.

Summarize where the task stands and what happens next in plain language.
