#!/usr/bin/env bash
#
# Upload input files to a Tendem task.
#
# Usage:
#   tendem-upload.sh '<upload_url>' <file> [<file>...]
#
#   <upload_url>  The folder-level SAS URL returned by get_file_upload_url.
#                 QUOTE IT — it contains '&'. Valid ~1h.
#   <file>        Local file path. Uploaded under its basename.
#
# On success prints the confirmation line to pass to send_message — Tendem
# does NOT auto-detect uploads; sending that message is a required step.
#
# Verified mechanics (2026-07-10, live against Tendem):
#   - The URL points at the Azure Data Lake (dfs) endpoint, but a plain PUT
#     there returns 400. Swap the host to the blob endpoint and PUT with
#     x-ms-blob-type: BlockBlob -> 201.
#   - The filename is inserted into the path BEFORE the query string.

set -euo pipefail

if [ $# -lt 2 ]; then
  echo "usage: $0 '<upload_url>' <file> [<file>...]" >&2
  exit 2
fi

UPLOAD_URL="$1"; shift
BASE="${UPLOAD_URL%%\?*}"
QUERY="${UPLOAD_URL#*\?}"
if [ "$BASE" = "$UPLOAD_URL" ]; then
  echo "error: upload_url has no query string — did you quote it?" >&2
  exit 2
fi
# The SAS is minted for the dfs endpoint; simple PUT only works on blob.
BASE="${BASE/.dfs.core.windows.net/.blob.core.windows.net}"

uploaded=()
for f in "$@"; do
  if [ ! -f "$f" ]; then
    echo "error: no such file: $f" >&2
    exit 1
  fi
  name="$(basename "$f")"
  code="$(curl -sS -o /dev/null -w '%{http_code}' -X PUT \
    -H 'x-ms-blob-type: BlockBlob' \
    --data-binary @"$f" \
    "$BASE/$name?$QUERY")"
  case "$code" in
    2*) uploaded+=("$name"); echo "uploaded: $name ($code)" >&2 ;;
    *)  echo "error: upload of $name failed (HTTP $code)" >&2; exit 1 ;;
  esac
done

names="$(printf '`%s`, ' "${uploaded[@]}")"
names="${names%, }"
echo
echo "Now send this via send_message (required — Tendem doesn't auto-detect uploads):"
echo "I've uploaded ${names} for this task."
