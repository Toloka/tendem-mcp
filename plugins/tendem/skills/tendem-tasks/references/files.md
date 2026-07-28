# Tendem files: upload inputs, download results

Mechanics below were verified live against Tendem (2026-07-10). Prefer the
bundled scripts in `scripts/` — the raw recipes are the fallback for
environments where the plugin's files aren't on disk.

## Sending input files to a task

Files are **scoped to a single task** — a new task starts with none. If the
task description references files, upload them immediately after
`create_task`; Tendem expects that order and waits for your upload.

1. **Mint a write URL.** `get_file_upload_url(task_id)` returns `upload_url`
   (a folder-level Azure SAS URL) and `expires_in_seconds` (~3600). Re-call to
   mint a fresh one if it expires.

2. **Upload each file** with the bundled script:

   ```bash
   scripts/tendem-upload.sh '<upload_url>' brief.pdf data.csv
   ```

   Raw recipe (what the script does):
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
