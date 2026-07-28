# Tendem files: upload inputs, download results

Mechanics below were verified live against Tendem (2026-07-10). For
downloads, prefer the bundled script in `scripts/`. For uploads on
Codex/ChatGPT, the agent cannot upload directly — see the warning below.

## Sending input files to a task

> **⚠️ Codex / ChatGPT: the agent must NOT run the upload itself.** OpenAI's
> security policies prevent the sandboxed environment from PUTting files to
> external storage — the script or raw `curl` will fail or be blocked. This
> is an **OpenAI platform restriction, not a Tendem limitation**: Tendem's
> upload mechanism works normally, it just has to run outside the sandbox.
> The agent's job is to mint the URL (step 1), then **build the step-2
> commands with everything already substituted** (blob host swap done,
> filename in place, URL quoted) **and hand them to the user to run in a
> terminal on their host machine** — saying clearly that the manual step is
> due to OpenAI's sandbox policy. Only after the user confirms the uploads
> succeeded (HTTP 201) does the agent send the naming message (step 3).

Files are **scoped to a single task** — a new task starts with none. If the
task description references files, upload them immediately after
`create_task`; Tendem expects that order and waits for your upload.

1. **Mint a write URL.** `get_file_upload_url(task_id)` returns `upload_url`
   (a folder-level Azure SAS URL) and `expires_in_seconds` (~3600). Re-call to
   mint a fresh one if it expires.

2. **Upload each file** — on Codex/ChatGPT this step is performed **by the
   user on their host machine**, with commands the agent prepares. Give the
   user one fully-substituted `curl` per file (preferred — no dependency on
   plugin files being on their machine), built from this recipe. The bundled
   script does the same if they have the plugin checked out:

   ```bash
   scripts/tendem-upload.sh '<upload_url>' brief.pdf data.csv
   ```

   Raw recipe (what the script does — apply it FOR the user when building
   their commands):
   - Split the URL at the first `?` into `<base>` and `<query>`.
   - **Swap the endpoint**: replace `.dfs.core.windows.net` with
     `.blob.core.windows.net` in `<base>`. A plain PUT to the `dfs` URL as
     returned fails with HTTP 400 — this swap is required.
   - Insert the filename before the query string and PUT the raw bytes:

     ```bash
     curl -X PUT -H "x-ms-blob-type: BlockBlob" \
       --data-binary @brief.pdf \
       "<blob_base>/brief.pdf?<query>"
     ```
   - Expect HTTP 201. Subpaths work (`<blob_base>/data/input.csv?<query>`).
     Keep names simple; avoid spaces.

3. **Tell Tendem what you uploaded** via `send_message`, naming each file:

   > "I've uploaded `brief.pdf` and `data/input.csv` for this task."

   This step is **required** — Tendem does not auto-detect uploads. It reads
   the message, locates the files, and confirms it can see them. If Tendem
   asked for the files while your upload was in flight (a `race` response),
   just complete the upload and send the confirmation with the new
   `last_seen_offset`.

## Receiving result files

When `next_action=fetch_result`, call `get_task_result(task_id)`. Each entry
in `files[]` has:

- `name` — may be a **full path** (e.g. `/mnt/data/report.pdf`); use only its
  basename when saving.
- `download_url` — pre-signed HTTPS URL, valid ~24h, no auth needed.
- `content_type?` / `size_bytes?` — best-effort.

Save them with the bundled script, defaulting to `./tendem/<task_id>/` under
the session's working directory:

```bash
scripts/tendem-download.sh ./tendem/<task_id> '<name>' '<download_url>' [...]
```

Raw fallback: `curl -fSL -o report.pdf '<download_url>'` (quote the URL — it
contains `&`).

URLs expire (~24h); the task stays fetchable even after it's `CLOSED`, so
re-call `get_task_result` to mint fresh ones.
