---
name: tendem-task
description: Explicitly submit a new task to Tendem (hybrid AI + human experts) and drive it through scoping. Invoke as $tendem-task <what you want a human expert to do>.
---

Submit a new Tendem task from the user's instruction — the text accompanying
this invocation.

Follow the `tendem-tasks` skill. Fidelity first:

1. Call `create_task` **now** — `description` is the user's instruction,
   passed as faithfully as possible. Add only context the user actually stated
   (input files by name, a deadline if one was given). Do NOT expand it into a
   synthesized brief, and do NOT pre-ask the user clarifying questions —
   scoping is Tendem's job. Derive a short `name` (≤120 chars) and reuse a
   stable `conversation_id` for this conversation.
2. If the instruction references local files: **⚠️ do not upload them
   yourself** — OpenAI's security policies block the sandbox from PUTting
   files to external storage (an OpenAI platform restriction, not a Tendem
   one). Immediately after creating the task, call `get_file_upload_url`,
   build fully-substituted `curl` upload commands per the `tendem-tasks`
   skill's `references/files.md`, and hand them to the user to run on their
   host machine — telling them the manual step is due to OpenAI's sandbox
   policy. After the user confirms, send the naming `send_message`.
3. Scoping loop: poll with `get_task(task_id, wait_for_change_seconds=30)`,
   answer Tendem's questions from conversation context via `send_message`,
   escalate to the user only when the answer isn't in context or
   scope/deliverables/deadline are at stake.
4. Stop when the quote is ready (`next_action=await_user_approval`): call
   `get_contract(task_id)` for the full scope + price, show the user both, and
   wait for their explicit go-ahead before `approve_task`.

Report the `task_id` in your reply so it can be referenced later.
